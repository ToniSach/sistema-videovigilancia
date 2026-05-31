"""
Cliente HTTP para API backend con manejo de JWT y refresh automático.
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
    """Cliente API singleton con manejo de tokens."""
    
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
        """Establece tokens de autenticación."""
        self.tokens = tokens
        self.session.headers["Authorization"] = f"Bearer {tokens.access_token}"
    
    def clear_tokens(self):
        """Limpia tokens (logout)."""
        self.tokens = None
        self.session.headers.pop("Authorization", None)
    
    def _refresh_token_if_needed(self) -> bool:
        """Intenta refrescar token si es necesario."""
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
    # Token fresco para streaming (MJPEG, HLS, snapshots con ?token=...)
    # ──────────────────────────────────────────────────────────────────────
    # El access_token del JWT vive ~15 min. Los streams MJPEG van por HTTP
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
        """Ejecuta request síncrono."""
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
        """Ejecuta request asíncrono. El callback corre en el main thread."""
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


# Instancia global
api_client = APIClient()