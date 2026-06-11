"""
================================================================================
MÓDULO: desktop_app.services.api_client — Cliente HTTP (COLUMNA VERTEBRAL)
================================================================================

PROPÓSITO
    Único punto de contacto del cliente desktop con el backend Flask. Expone un
    singleton `api_client` (instancia de `APIClient`) que TODAS las vistas usan
    para hablar REST con la API: autenticación, cámaras, usuarios, permisos,
    eventos, IA, grabaciones, ajustes… Centraliza la gestión del JWT (set/clear
    de tokens, refresh automático en 401, refresh proactivo para streaming) y
    ejecuta cada petición de forma asíncrona sin bloquear el hilo de UI de Qt.

RESPONSABILIDAD
    - Mantener la sesión `requests` con cabeceras y pool de conexiones.
    - Guardar los `AuthTokens` vivos y poner/quitar el header `Authorization`.
    - Refrescar el access_token cuando el backend responde 401 (transparente)
      y proactivamente para los tokens que viajan en streams (`?token=...`).
    - Ejecutar requests en un QThreadPool y marshallear el resultado al hilo
      main vía Qt.QueuedConnection (los callbacks tocan widgets Qt y DEBEN
      correr en el hilo main; ver `_CallbackDispatcher`).
    - Normalizar toda respuesta a `APIResponse(success/data/error/status_code)`.

DEPENDENCIAS
    - requests (HTTP + Session + HTTPAdapter para el pool).
    - PySide6.QtCore (QObject/Signal/QThreadPool/QRunnable/Qt) para async + señales.
    - desktop_app.src.config.config → API_BASE_URL y TIMEOUT.
    - desktop_app.src.models.user → AuthTokens (DTO de tokens) y User.

COMPONENTES RELACIONADOS
    - Lo consumen TODAS las ui/views/* (login, dashboard, live, playback,
      cámaras, usuarios, permisos, eventos, ajustes) y varios diálogos.
    - El directo en vivo NO pasa por aquí: rtsp_video.py reproduce el restream
      RTSP de go2rtc con VLC. Pero los tokens de stream (HLS/snapshots con
      `?token=...`) SÍ se obtienen aquí con `get_stream_token()`.
    - playback_service.py descarga grabaciones con su propio request, pero usa
      el token que este cliente mantiene.

PUNTO DE ENTRADA
    Se crea el singleton `api_client = APIClient()` al final del módulo, al
    importarlo (antes de que exista QApplication). Por eso el dispatcher Qt se
    crea de forma PEREZOSA (lazy) en el primer uso; ver `_get_dispatcher`.

COMUNICACIÓN CON EL BACKEND (pipelines del sistema)
    - #2 Auth: login()/setup_admin()/register() (sin token previo),
      set_tokens()/clear_tokens(), _refresh_token_if_needed() (en 401).
    - #3 Live: get_stream_token() entrega un access_token con ≥2 min de vida
      para los streams que van por HTTP directo.
    - #14 Reproducción histórica: los GET a /recordings y el token los usa
      playback_service; este cliente provee la sesión/token.
    - #13 Notificaciones: las vistas consultan preferencias/Telegram/FCM por
      REST a través de este cliente.

PATRÓN ASÍNCRONO (clave)
    Vista → api_client.get/post/... → APIWorker (QRunnable en QThreadPool)
          → _make_request (síncrono, en hilo de fondo)
          → _CallbackDispatcher.dispatch (Signal Qt.QueuedConnection)
          → callback(APIResponse) ejecutado en el HILO MAIN.
================================================================================
"""
import json
import logging
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass

import requests
from PySide6.QtCore import QObject, Signal, QThreadPool, QRunnable, Qt

from desktop_app.src.config import config
from desktop_app.src.models.user import AuthTokens, User

logger = logging.getLogger(__name__)


@dataclass
class APIResponse:
    """Respuesta estandarizada de API."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    status_code: int = 200


class _CallbackDispatcher(QObject):
    """
    Helper que vive en el main thread y dispara callbacks ahí.

    Por qué: APIWorker (QRunnable) corre en el thread pool. Si su callback
    toca widgets Qt, Qt avisa "Cannot set parent, new parent is in different
    thread" y puede crashear. Marshalleamos vía signal Qt.QueuedConnection
    para garantizar ejecución en main thread.
    """
    _dispatch = Signal(object, object)  # (callback, response)

    def __init__(self):
        super().__init__()
        # Qt.QueuedConnection garantiza que el slot corra en el thread
        # del receiver (este QObject), que es el main thread.
        self._dispatch.connect(self._on_dispatch, type=Qt.QueuedConnection)

    def _on_dispatch(self, callback, response):
        try:
            callback(response)
        except Exception as e:
            logger.error(f"Error ejecutando callback en main thread: {e}",
                         exc_info=True)

    def dispatch(self, callback, response):
        """Encola callback(response) para correr en main thread."""
        self._dispatch.emit(callback, response)


class APIWorker(QRunnable):
    """Worker para requests HTTP en background."""

    def __init__(self, func: Callable, callback: Optional[Callable],
                 dispatcher: _CallbackDispatcher):
        super().__init__()
        self.func = func
        self.callback = callback
        self.dispatcher = dispatcher
        self.setAutoDelete(True)

    def run(self):
        try:
            result = self.func()
        except Exception as e:
            logger.error(f"Error en APIWorker: {e}")
            result = APIResponse(success=False, error=str(e), status_code=0)
        if self.callback:
            # Marshalling al main thread (NO ejecutar callback aquí)
            self.dispatcher.dispatch(self.callback, result)


class APIClient(QObject):
    """
    NIVEL 2 — Cliente API singleton (COLUMNA VERTEBRAL del cliente desktop).

    Rol: única puerta de salida hacia el backend Flask. Mantiene la sesión
    `requests`, los `AuthTokens` y orquesta el patrón asíncrono (QThreadPool +
    dispatcher al hilo main). Toda vista/diálogo del cliente lo usa.

    Singleton (`__new__`): hay UNA sola instancia por proceso —el módulo crea
    `api_client = APIClient()` al importarse y todas las vistas la comparten,
    de modo que el token vive en un único lugar y un refresh beneficia a todos.
    El guard `_initialized` en `__init__` evita re-inicializar la sesión si se
    vuelve a llamar `APIClient()`.

    Lo instancia: el propio módulo (instancia global al final del archivo).
    Lo consume: ui/views/* y ui/dialogs/*.

    Señales (notifican a la UI de forma global):
      - auth_error: emitida cuando el refresh falla → la MainWindow debe forzar
        re-login.
      - request_error: error general (reservada para avisos no atados a un
        callback concreto).
    """

    # Señales
    auth_error = Signal()  # Token inválido, requerir re-login
    request_error = Signal(str)  # Error general
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        super().__init__()
        self._initialized = True
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })
        # Al iniciar sesión la UI dispara ~12 peticiones en paralelo (cámaras,
        # usuarios, ai/status, salud, telegram, eventos…). El pool urllib3 por
        # defecto es 10 → "Connection pool is full, discarding connection" y
        # reconexiones. Subimos el pool para absorber la ráfaga sin descartes.
        try:
            from requests.adapters import HTTPAdapter
            _adapter = HTTPAdapter(pool_connections=20, pool_maxsize=30)
            self.session.mount("http://", _adapter)
            self.session.mount("https://", _adapter)
        except Exception:
            pass
        self.tokens: Optional[AuthTokens] = None
        self._thread_pool = QThreadPool.globalInstance()
        self._base_url = config.API_BASE_URL

        # Dispatcher LAZY: se crea en el primer uso, no aquí.
        # Motivo: `api_client = APIClient()` se ejecuta al importar este
        # módulo, lo cual sucede ANTES de que main.py construya QApplication.
        # Crear un QObject sin QApplication viva provoca:
        #   "QObject::startTimer: Timers can only be used with threads
        #    started with QThread"
        # Posponer la creación al primer dispatch garantiza que ya hay
        # QApplication y que el QObject vive en el hilo main correcto.
        self._dispatcher: Optional[_CallbackDispatcher] = None

        logger.info("APIClient inicializado")

    def _get_dispatcher(self) -> "_CallbackDispatcher":
        """Devuelve el dispatcher, creándolo en el primer uso (lazy)."""
        if self._dispatcher is None:
            self._dispatcher = _CallbackDispatcher()
        return self._dispatcher
    
    def set_tokens(self, tokens: AuthTokens):
        """
        Propósito: registrar la sesión autenticada tras un login exitoso
            (pipeline #2 Auth), fijando el header Authorization para todas las
            peticiones posteriores que salgan por `session`.
        Inputs: tokens (AuthTokens con access_token + refresh_token).
        Outputs: ninguno (muta estado interno).
        Llamado por: login_view tras recibir el JSON de /auth/login.
        Llama a: ninguno relevante.
        """
        self.tokens = tokens
        self.session.headers["Authorization"] = f"Bearer {tokens.access_token}"

    def clear_tokens(self):
        """
        Propósito: cerrar sesión (logout) — descartar tokens y quitar el header
            Authorization para que las peticiones siguientes vayan sin credenciales.
        Llamado por: la lógica de logout de la MainWindow y el manejador de
            auth_error (re-login).
        """
        self.tokens = None
        self.session.headers.pop("Authorization", None)

    def _refresh_token_if_needed(self) -> bool:
        """
        Propósito: renovar el access_token usando el refresh_token contra
            /auth/refresh (pipeline #2 Auth). Es el corazón del refresh
            transparente que dispara `_make_request` al recibir un 401.
        Inputs: ninguno (usa self.tokens.refresh_token).
        Outputs: True si renovó y actualizó el header; False si no hay sesión
            o el refresh falló.
        Excepciones: capturadas internamente (se logean y devuelven False).
        Llamado por: _make_request (en 401) y get_stream_token (proactivo).
        Llama a: session.post(/auth/refresh).
        """
        if not self.tokens:
            return False

        try:
            response = self.session.post(
                f"{self._base_url}/auth/refresh",
                headers={"Authorization": f"Bearer {self.tokens.refresh_token}"},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                self.tokens.access_token = data.get("access_token")
                self.session.headers["Authorization"] = f"Bearer {self.tokens.access_token}"
                return True
        except Exception as e:
            logger.error(f"Error refrescando token: {e}")

        return False

    # ──────────────────────────────────────────────────────────────────────
    # Token fresco para streaming (HLS, snapshots con ?token=...)
    # ──────────────────────────────────────────────────────────────────────
    # El access_token del JWT vive ~15 min. Los streams van por HTTP
    # directo con ?token=... y NO pasan por _make_request → cuando un
    # cliente Qt reabre un stream a los 20 min sigue mandando el token viejo
    # y el backend devuelve 401 "Signature has expired".
    # Esta helper decodifica el campo `exp` del JWT (no requiere lib, es
    # base64 plano) y si queda menos del umbral, refresca antes de
    # devolverlo. Si el refresh falla, devuelve el actual (mejor un 401
    # que un None que rompe el call-site).
    _STREAM_TOKEN_REFRESH_THRESHOLD_S = 120  # < 2 min → refresca

    def get_stream_token(self) -> Optional[str]:
        """
        Devuelve un access_token con vida útil garantizada de al menos
        ~2 minutos. Si el actual está más cerca de expirar, dispara
        refresh proactivo. Devuelve None si no hay sesión.
        """
        if not self.tokens or not self.tokens.access_token:
            return None
        try:
            seconds_left = self._access_token_seconds_remaining()
            if seconds_left is not None and \
               seconds_left < self._STREAM_TOKEN_REFRESH_THRESHOLD_S:
                logger.info(
                    f"Token a {seconds_left}s de expirar; refrescando proactivamente"
                )
                self._refresh_token_if_needed()
        except Exception as e:
            logger.debug(f"get_stream_token: error decodificando exp: {e}")
        return self.tokens.access_token if self.tokens else None

    def _access_token_seconds_remaining(self) -> Optional[int]:
        """
        Decodifica el campo `exp` del JWT actual (sin verificar firma — solo
        lectura local) y devuelve segundos restantes hasta expiración.
        Devuelve None si no se puede parsear.
        """
        import base64
        import json
        import time as _t
        if not self.tokens or not self.tokens.access_token:
            return None
        try:
            parts = self.tokens.access_token.split(".")
            if len(parts) != 3:
                return None
            payload_b64 = parts[1]
            # Padding base64
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            exp = payload.get("exp")
            if exp is None:
                return None
            return int(exp - _t.time())
        except Exception:
            return None
    
    def _make_request(self, method: str, endpoint: str, 
                     data: Dict = None, 
                     params: Dict = None,
                     stream: bool = False) -> APIResponse:
        """
        Propósito: ejecutar UNA petición HTTP síncrona (corre en el hilo de
            fondo del APIWorker, nunca en el main) y normalizar la respuesta a
            APIResponse. Maneja el 401 con refresh transparente y un único
            reintento; si el refresh falla emite auth_error.
        Inputs: method ("GET"/"POST"/"PUT"/"PATCH"/"DELETE"), endpoint relativo
            (se le antepone API_BASE_URL), data (cuerpo JSON), params (query
            string), stream (si True devuelve el objeto Response crudo para leer
            por chunks, p.ej. descargas).
        Outputs: APIResponse. Con stream=True el campo .data es el Response sin
            parsear; si no, .data/.error/.success se extraen del JSON de la API.
        Excepciones: requests.RequestException se captura y devuelve como
            APIResponse(success=False, status_code=0).
        Llamado por: request_async (vía el lambda del APIWorker) y, en el 401,
            por sí mismo (reintento único tras refrescar).
        Llama a: session.<verbo>(), _refresh_token_if_needed(), auth_error.emit().
        """
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        
        try:
            if method == "GET":
                response = self.session.get(url, params=params, stream=stream, timeout=config.TIMEOUT)
            elif method == "POST":
                response = self.session.post(url, json=data, timeout=config.TIMEOUT)
            elif method == "PUT":
                response = self.session.put(url, json=data, timeout=config.TIMEOUT)
            elif method == "PATCH":
                response = self.session.patch(url, json=data, timeout=config.TIMEOUT)
            elif method == "DELETE":
                response = self.session.delete(url, json=data, timeout=config.TIMEOUT)
            else:
                return APIResponse(success=False, error=f"Método no soportado: {method}")
            
            # Manejar 401 Unauthorized
            if response.status_code == 401:
                if self._refresh_token_if_needed():
                    return self._make_request(method, endpoint, data, params, stream)
                else:
                    self.auth_error.emit()
                    return APIResponse(success=False, error="Sesión expirada", status_code=401)
            
            if stream:
                return APIResponse(success=True, data=response, status_code=response.status_code)
            
            try:
                json_data = response.json()
                return APIResponse(
                    success=json_data.get("success", response.status_code < 400),
                    data=json_data.get("data"),
                    error=json_data.get("error"),
                    status_code=response.status_code
                )
            except:
                return APIResponse(
                    success=response.status_code < 400,
                    data=response.text,
                    status_code=response.status_code
                )
                
        except requests.RequestException as e:
            logger.error(f"Request error: {e}")
            return APIResponse(success=False, error=str(e), status_code=0)
    
    def request_async(self, method: str, endpoint: str,
                     callback: Callable[[APIResponse], None],
                     data: Dict = None,
                     params: Dict = None,
                     stream: bool = False):
        """
        Propósito: lanzar `_make_request` en el QThreadPool (no bloquea la UI) y
            garantizar que `callback(APIResponse)` se ejecute en el HILO MAIN.
            Es la primitiva que envuelven get/post/put/patch/delete.
        Inputs: method, endpoint, callback (recibe el APIResponse), data, params,
            stream.
        Outputs: ninguno; el resultado llega por callback.
        Llamado por: get/post/put/patch/delete y, por extensión, todas las vistas.
        Llama a: QThreadPool.start(APIWorker(...)), _get_dispatcher().
        """
        worker = APIWorker(
            lambda: self._make_request(method, endpoint, data, params, stream),
            callback,
            self._get_dispatcher(),
        )
        self._thread_pool.start(worker)
    
    # ============ MÉTODOS CONVENIENTES (REST completos) ============
    
    def get(self, endpoint: str, callback: Callable, params: Dict = None):
        """GET asíncrono."""
        self.request_async("GET", endpoint, callback, params=params)
    
    def post(self, endpoint: str, callback: Callable, data: Dict = None):
        """POST asíncrono."""
        self.request_async("POST", endpoint, callback, data=data)
    
    def put(self, endpoint: str, callback: Callable, data: Dict = None):
        """PUT asíncrono."""
        self.request_async("PUT", endpoint, callback, data=data)
    
    def patch(self, endpoint: str, callback: Callable, data: Dict = None):
        """PATCH asíncrono."""
        self.request_async("PATCH", endpoint, callback, data=data)
    
    def delete(self, endpoint: str, callback: Callable, data: Dict = None):
        """DELETE asíncrono."""
        self.request_async("DELETE", endpoint, callback, data=data)
    
    def login(self, username: str, password: str, callback: Callable[[APIResponse], None]):
        """Login especial que no requiere token previo. Callback en main thread."""
        def do_login():
            try:
                response = requests.post(
                    f"{self._base_url}/auth/login",
                    json={"username": username, "password": password},
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    return APIResponse(success=True, data=data)
                else:
                    try:
                        err = response.json()
                        return APIResponse(success=False, error=err.get("error", "Login failed"),
                                        status_code=response.status_code)
                    except:
                        return APIResponse(success=False, error="Login failed",
                                        status_code=response.status_code)
            except Exception as e:
                return APIResponse(success=False, error=str(e))

        worker = APIWorker(do_login, callback, self._get_dispatcher())
        self._thread_pool.start(worker)

    def check_setup_status(self, callback: Callable[[APIResponse], None]):
        """Consulta si el sistema necesita crear el primer admin (sin token)."""
        def do():
            try:
                r = requests.get(f"{self._base_url}/auth/setup-status", timeout=8)
                if r.status_code == 200:
                    return APIResponse(success=True, data=r.json().get("data", {}))
                return APIResponse(success=False, error="status", status_code=r.status_code)
            except Exception as e:
                return APIResponse(success=False, error=str(e))
        self._thread_pool.start(APIWorker(do, callback, self._get_dispatcher()))

    def setup_admin(self, username: str, password: str,
                    callback: Callable[[APIResponse], None]):
        """Crea el primer usuario administrador (sin token). Callback en main thread."""
        def do():
            try:
                r = requests.post(
                    f"{self._base_url}/auth/setup",
                    json={"username": username, "password": password},
                    timeout=10,
                )
                data = r.json() if r.content else {}
                if r.status_code in (200, 201) and data.get("success"):
                    return APIResponse(success=True, data=data.get("data"))
                return APIResponse(
                    success=False,
                    error=data.get("error", "No se pudo crear el administrador"),
                    status_code=r.status_code,
                )
            except Exception as e:
                return APIResponse(success=False, error=str(e))
        self._thread_pool.start(APIWorker(do, callback, self._get_dispatcher()))

    def register(self, username: str, password: str,
                 callback: Callable[[APIResponse], None]):
        """Registra un usuario normal (sin token). Callback en main thread."""
        def do():
            try:
                r = requests.post(
                    f"{self._base_url}/auth/register",
                    json={"username": username, "password": password},
                    timeout=10,
                )
                data = r.json() if r.content else {}
                if r.status_code in (200, 201) and data.get("success"):
                    return APIResponse(success=True, data=data.get("data"))
                return APIResponse(
                    success=False,
                    error=data.get("error", "No se pudo crear la cuenta"),
                    status_code=r.status_code,
                )
            except Exception as e:
                return APIResponse(success=False, error=str(e))
        self._thread_pool.start(APIWorker(do, callback, self._get_dispatcher()))


# Instancia global
api_client = APIClient()