"""
================================================================================
MÓDULO: notification_router — Enrutamiento de eventos a notificaciones por usuario
================================================================================

PROPÓSITO
    Núcleo del Pipeline #13 (Notificaciones). Recibe un Event ya persistido y
    decide A QUIÉN, POR QUÉ CANAL y CUÁNDO se le notifica, aplicando las reglas
    de negocio: permiso por cámara, preferencias del usuario (tipo de evento,
    horario, días), gate de IA y anti-spam (cooldown).

RESPONSABILIDAD PRINCIPAL
    - Resolver la audiencia del evento: owner de la cámara + usuarios con permiso
      explícito `can_view` (no basta el owner: hay cámaras compartidas).
    - Filtrar por `NotificationPreference` (evento habilitado, horario, día).
    - Evitar tormentas de notificaciones repetidas con un cooldown en memoria
      (clave user:cámara:tipo, 300s por defecto).
    - Persistir un `NotificationLog` por intento y entregar por canal
      (Telegram vía TelegramNotifier; canal "app" lo entrega el WSNotificationBroker).
    - Hacerlo NO bloqueante: el ruteo se delega a un ThreadPoolExecutor propio
      para no frenar al hilo del EventManager que publica el evento.

GATE DE IA (regla de producto)
    Solo se notifica de la cámara con IA ACTIVA. Si ninguna IA está activa, no se
    notifica nada (ver `_ai_active_camera_ids`). Esto acota el ruido a la única
    cámara que el operador eligió analizar (ver AI_CAMERA_ID en config).

DEPENDENCIAS
    database.models ........... Event, User, NotificationPreference/Channel/Day,
                                UserTelegramChat, NotificationLog,
                                UserCameraPermission, Camera
    database.connection ....... db_manager (UNA sesión por evento, ver abajo)
    services.permission_service PermissionService (instanciado, reservado)
    notifications.telegram_notifier  TelegramNotifier (lazy; entrega real)
    container.get_container ... para consultar AIService (gate de IA)

COMPONENTES RELACIONADOS
    - EventManager: publica los eventos que aquí se rutean (productor upstream).
    - TelegramNotifier: efectúa el envío a la API de Telegram.
    - WSNotificationBroker: entrega el canal "app" por WebSocket en la LAN.
    - NotificationPreferenceService: CRUD de las preferencias que aquí se leen.

PUNTO DE ENTRADA
    `route_event(event, event_data)` — encolado por suscriptores del EventManager.
    El singleton global `notification_router` se crea al importar el módulo.

PIPELINE(S)
    #13 Notificaciones — etapa central:
        EventManager → NotificationRouter (permiso/preferencias/gate IA/cooldown)
                     → TelegramNotifier / WSNotificationBroker

NOTA DE RENDIMIENTO (sesiones BD)
    `_process_event` abre UNA sola sesión y la propaga a todos los submétodos
    (`_notify_user_if_applies`, `_check_day`, `_send_notification`). Antes cada
    submétodo abría su propia sesión → ~30 sesiones por evento con 10 usuarios,
    saturando el pool. El `_NullCM` permite reusar esa sesión sin cerrarla.
================================================================================
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Optional
from concurrent.futures import ThreadPoolExecutor

from backend.app.database.models import (
    Event, User, UserTelegramChat, NotificationLog,
    UserCameraPermission, Camera
)
from backend.app.database.connection import db_manager
from backend.app.services.permission_service import PermissionService
from backend.app.notifications.preference_eval import wanted_channels, TELEGRAM_CHANNEL

logger = logging.getLogger(__name__)

# Eventos de conectividad/sistema: NO dependen de la IA, así que se saltan el
# gate de IA en `_process_event` (una cámara que se cae debe avisar siempre).
_CONNECTIVITY_EVENT_TYPES = {"camera_offline"}

# Cooldown anti-spam por defecto (segundos) entre dos notificaciones de la misma
# combinación usuario:cámara:tipo. Producto: 60s (suficiente para no inundar
# sin perder avisos relevantes).
NOTIFICATION_COOLDOWN_SECONDS = 60


class _NullCM:
    """Context manager pasante: devuelve la sesión sin cerrarla.
    Permite que _send_notification reutilice la sesión de _process_event
    pero también pueda crear una propia si lo llaman aisladamente."""
    def __init__(self, sess):
        self._sess = sess
    def __enter__(self):
        return self._sess
    def __exit__(self, *args):
        return False


# ==============================
# CIRCUIT BREAKER
# ==============================
class CircuitBreaker:
    """
    Cortacircuitos para envíos externos (aquí: Telegram).

    Tras `failure_threshold` fallos consecutivos pasa a OPEN y rechaza llamadas
    al instante (fast-fail) durante `recovery_timeout` segundos, para no bloquear
    los hilos del router esperando timeouts de una API caída. Pasado ese tiempo
    entra en HALF_OPEN: deja pasar UNA llamada de prueba; si va bien vuelve a
    CLOSED, si falla reabre. Lo usa NotificationRouter._send_telegram.
    """
    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self.failures = 0
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = 'CLOSED'  # CLOSED, OPEN, HALF_OPEN
        self.last_failure_time = None

    def call(self, func, *args, **kwargs):
        if self.state == 'OPEN':
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = 'HALF_OPEN'
            else:
                return None  # Fast fail

        try:
            result = func(*args, **kwargs)
            if self.state == 'HALF_OPEN':
                self.state = 'CLOSED'
                self.failures = 0
            return result
        except Exception:
            self.failures += 1
            self.last_failure_time = time.time()
            if self.failures >= self.failure_threshold:
                self.state = 'OPEN'
            raise


# ==============================
# NOTIFICATION ROUTER
# ==============================
class NotificationRouter:
    """
    Enrutador de notificaciones (singleton, Pipeline #13).

    ROL: convertir un Event en cero o más notificaciones entregadas a los
    usuarios correctos por sus canales preferidos, respetando permisos, horario,
    gate de IA y cooldown anti-spam.

    QUIÉN LO INSTANCIA / CONSUME:
        - Singleton global `notification_router` (al final del módulo); se
          suscribe al EventManager (lo cablea quien arranca las notificaciones).
        - Lo consume cualquier productor de eventos vía `route_event()`.

    DEPENDENCIAS VIVAS:
        - ThreadPoolExecutor propio (4 hilos "NotifyRouter") → ruteo no bloqueante.
        - `_cooldown_cache` (dict en memoria) protegido por `_cooldown_lock`.
        - CircuitBreaker de Telegram (`_telegram_cb`).
        - TelegramNotifier (lazy) y AIService (vía contenedor) para el gate de IA.

    PIPELINE: #13, etapa de enrutamiento (entre EventManager y los notificadores).
    """

    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        
        self._initialized = True
        self.permission_service = PermissionService()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="NotifyRouter")
        self._cooldown_cache: Dict[str, datetime] = {}
        self._cooldown_lock = threading.Lock()

        # Circuit breaker (solo Telegram; FCM/push fue eliminado del proyecto)
        self._telegram_cb = CircuitBreaker()

        # Lazy notifier
        self._telegram_notifier = None

        logger.info("NotificationRouter inicializado")

    def _get_telegram_notifier(self):
        """Carga perezosa del TelegramNotifier (evita import circular en arranque)."""
        if self._telegram_notifier is None:
            from backend.app.notifications.telegram_notifier import telegram_notifier
            self._telegram_notifier = telegram_notifier
        return self._telegram_notifier

    def _ai_active_camera_ids(self) -> Optional[set]:
        """
        Conjunto de camera_id con IA ACTIVA en este momento (vía AIService).

        Regla de producto: las notificaciones SOLO existen para la cámara con
        IA activa. Devuelve:
          - set de ids con IA activa (puede ser vacío → no notificar nada),
          - None si no se pudo consultar el estado (en ese caso NO filtramos,
            para no perder notificaciones por un fallo transitorio).
        """
        try:
            from backend.app.container import get_container
            ai_service = get_container().get("ai_service")
            status = ai_service.get_ai_status() or {}
            return {a.get("camera_id") for a in status.get("active", [])}
        except Exception as e:
            logger.debug(f"No pude consultar IA activa para filtrar notif: {e}")
            return None

    def route_event(self, event: Event, event_data=None):
        """
        Propósito: PUNTO DE ENTRADA del Pipeline #13. Encola el procesamiento del
            evento en el pool propio y retorna de inmediato (no bloquea al
            EventManager).
        Inputs: event (Event persistido); event_data opcional (payload extra,
            p.ej. ruta de snapshot) que se arrastra hasta la entrega.
        Outputs: None (efecto: tarea encolada).
        Llamado por: suscriptores del EventManager (productores de eventos).
        Llama a: self._executor.submit(self._process_event, ...).
        """
        self._executor.submit(self._process_event, event, event_data)
    
    def _process_event(self, event: Event, event_data=None):
        """
        Propósito (etapa central #13): resuelve la audiencia del evento y
            delega la evaluación de preferencias por usuario. Abre UNA SOLA
            sesión BD para todo el ciclo (permisos, prefs, canales, log).

        Flujo:
            1. Gate de IA: si hay IA activa y el evento NO es de esa cámara → sale.
            2. Audiencia = usuarios con `can_view` sobre la cámara + owner.
            3. Por cada usuario → _notify_user_if_applies(...).

        Inputs: event, event_data (reenviados desde route_event).
        Outputs: None (efecto: notificaciones encoladas/enviadas + logs en BD).
        Excepciones: capturadas y logueadas; nunca propagan (corre en el pool).
        Llamado por: route_event (en un hilo del executor).
        Llama a: _ai_active_camera_ids, _notify_user_if_applies.

        NOTA: el reuso de una única sesión evita el patrón antiguo de ~30
        sesiones por evento (con 10 usuarios) que saturaba el pool bajo carga.
        """
        try:
            camera_id = event.camera_id

            # GATE IA: las detecciones (movimiento/persona/vehículo) SOLO se
            # notifican de la cámara con IA activa. Pero los eventos de
            # CONECTIVIDAD (cámara desconectada) NO dependen de la IA: si una
            # cámara se cae, el dueño debe enterarse aunque no tuviera IA activa
            # (de hecho, al caer la cámara su IA se detiene, así que el gate los
            # bloqueaba siempre → las alertas de desconexión nunca llegaban).
            # Por eso estos eventos se saltan el gate.
            if event.event_type not in _CONNECTIVITY_EVENT_TYPES:
                ai_cams = self._ai_active_camera_ids()
                if ai_cams is not None:  # None = no se pudo consultar → no filtrar
                    if not ai_cams:
                        logger.debug(
                            "Evento sin notificación: no hay IA activa en ninguna cámara"
                        )
                        return
                    if camera_id not in ai_cams:
                        logger.debug(
                            f"Evento de cam {camera_id} sin notificación: la IA está "
                            f"activa en {ai_cams}, no en esta cámara"
                        )
                        return

            with db_manager.get_session() as session:
                # Usuarios con permiso explícito + owner de la cámara
                perms = session.query(UserCameraPermission).filter_by(
                    camera_id=camera_id, can_view=True
                ).all()
                user_ids = {p.user_id for p in perms}

                camera = session.get(Camera, camera_id)
                if camera and camera.owner_id:
                    user_ids.add(camera.owner_id)

                for user_id in user_ids:
                    self._notify_user_if_applies(user_id, event, event_data, session)

        except Exception as e:
            logger.error(f"Error ruteando evento {event.id}: {e}", exc_info=True)

    def _notify_user_if_applies(self, user_id: int, event: Event, event_data, session):
        """
        Propósito (etapa #13): resuelve y envía TELEGRAM para UN usuario, POR
            DISPOSITIVO. El canal in-app (WebSocket) lo entrega ws_broker por su
            cuenta, por dispositivo conectado, así que aquí SOLO se hace Telegram.

        TELEGRAM POR DISPOSITIVO: cada chat de Telegram del usuario pertenece a un
            alcance (device_id; NULL = cuenta/escritorio). Un chat recibe solo si
            las preferencias de SU alcance quieren 'telegram' para este evento
            ahora (enabled + cámara específica>general + horario + día). Se
            deduplica por chat_id y se aplica un cooldown por (usuario, cámara, tipo).

        Inputs: user_id; event; event_data (trae el snapshot); session (reutilizada).
        Outputs: None. Excepciones: capturadas y logueadas (no abortan al resto).
        Llamado por: _process_event. Llama a: wanted_channels, _send_notification.
        """
        try:
            chats = session.query(UserTelegramChat).filter_by(
                user_id=user_id, is_active=True
            ).all()
            if not chats:
                return

            # Evaluar cada alcance (device_id) UNA vez; reunir los chats destino
            # de los alcances que quieren telegram, deduplicados por chat_id.
            scope_wants: dict = {}
            target_chat_ids: list = []
            seen: set = set()
            for c in chats:
                scope = c.device_id
                if scope not in scope_wants:
                    chans = wanted_channels(
                        session, user_id, event.event_type, event.camera_id,
                        device_id=scope
                    )
                    scope_wants[scope] = TELEGRAM_CHANNEL in chans
                if not scope_wants[scope]:
                    continue
                if c.telegram_chat_id not in seen:
                    seen.add(c.telegram_chat_id)
                    target_chat_ids.append(c.telegram_chat_id)

            if not target_chat_ids:
                return

            cooldown_key = f"{user_id}:{event.camera_id}:{event.event_type}"
            if self._is_in_cooldown(cooldown_key):
                return

            self._send_notification(
                user_id=user_id,
                event=event,
                channel='telegram',
                cooldown_key=cooldown_key,
                event_data=event_data,
                session=session,
                chat_ids=target_chat_ids,
            )

        except Exception as e:
            logger.error(f"Error notificando a usuario {user_id}: {e}", exc_info=True)

    def _is_in_cooldown(self, cooldown_key: str,
                        seconds: int = NOTIFICATION_COOLDOWN_SECONDS) -> bool:
        """
        Propósito (anti-spam): True si ya se notificó la misma combinación
            (clave user:cámara:tipo) dentro de los últimos `seconds` (20s por
            defecto). Limpia la entrada cuando ya caducó. La marca de tiempo la
            fija _update_cooldown tras un envío exitoso. Inputs: cooldown_key,
            seconds. Outputs: bool. Llamado por: _notify_user_if_applies.
        """
        with self._cooldown_lock:
            last_time = self._cooldown_cache.get(cooldown_key)
            if last_time is None:
                return False
            
            if datetime.now() - last_time < timedelta(seconds=seconds):
                return True
            
            del self._cooldown_cache[cooldown_key]
            return False
    
    def _update_cooldown(self, cooldown_key: str):
        """Marca 'ahora' como último envío de la clave (arma el cooldown).
        Llamado por _send_notification tras una entrega exitosa."""
        with self._cooldown_lock:
            self._cooldown_cache[cooldown_key] = datetime.now()
    
    def _send_notification(self, user_id, event, channel, cooldown_key,
                            event_data=None, session=None, chat_ids=None):
        """
        Propósito (entrega #13): registra un NotificationLog ('pending'→'sent'/
            'failed') y entrega Telegram a los chat_ids YA RESUELTOS (por
            dispositivo) por _notify_user_if_applies. Al tener éxito, arma el
            cooldown. El canal in-app lo entrega ws_broker aparte.
        Inputs: user_id, event, channel ('telegram'), cooldown_key, event_data,
            session, chat_ids (lista de destinos ya filtrados por dispositivo).
        Outputs: None. Excepciones: capturadas y logueadas (no propagan).
        Llamado por: _notify_user_if_applies. Llama a: _send_telegram, _update_cooldown.
        """
        try:
            own_session = session is None
            ctx = db_manager.get_session() if own_session else _NullCM(session)
            with ctx as sess:
                log = NotificationLog(
                    event_id=event.id,
                    user_id=user_id,
                    channel=channel,
                    status='pending',
                    cooldown_key=cooldown_key
                )
                sess.add(log)
                sess.flush()

                success = False
                if channel == 'telegram':
                    success = self._send_telegram(
                        event, sess, event_data, chat_ids or []
                    )

                log.status = 'sent' if success else 'failed'

                if success:
                    self._update_cooldown(cooldown_key)

        except Exception as e:
            logger.error(f"Error enviando notificación: {e}", exc_info=True)
    
    def telegram_chat_ids_for_event(self, event_type: str,
                                    camera_id: Optional[int]) -> list:
        """
        Lista de telegram_chat_id que DEBEN recibir este evento, resuelta con el
        MISMO criterio per-usuario que usa el envío de la foto: audiencia de la
        cámara (dueño + permisos can_view) ∩ usuarios cuyo `wanted_channels`
        incluye 'telegram' ahora. Devuelve los chats activos de esos usuarios,
        deduplicados.

        Sirve para que otros productores de Telegram (p.ej. el VIDEO del clip en
        recording_manager) usen la única fuente de destinos (UserTelegramChat),
        en vez de la lista global legacy `SystemConfig.telegram_chat_ids`.

        Inputs: event_type, camera_id. Outputs: list[str] (puede ser vacía).
        Excepciones: capturadas → devuelve [] (best-effort, no rompe la grabación).
        """
        try:
            with db_manager.get_session() as session:
                # Audiencia: permisos can_view + dueño de la cámara.
                user_ids = set()
                if camera_id is not None and camera_id > 0:
                    perms = session.query(UserCameraPermission).filter_by(
                        camera_id=camera_id, can_view=True
                    ).all()
                    user_ids = {p.user_id for p in perms}
                    camera = session.get(Camera, camera_id)
                    if camera and camera.owner_id:
                        user_ids.add(camera.owner_id)

                chat_ids = []
                seen = set()
                for uid in user_ids:
                    # Telegram POR DISPOSITIVO: cada chat del usuario recibe solo
                    # si las preferencias de SU alcance (device_id) quieren telegram.
                    chats = session.query(UserTelegramChat).filter_by(
                        user_id=uid, is_active=True
                    ).all()
                    scope_wants: dict = {}
                    for c in chats:
                        scope = c.device_id
                        if scope not in scope_wants:
                            chans = wanted_channels(
                                session, uid, event_type, camera_id, device_id=scope
                            )
                            scope_wants[scope] = TELEGRAM_CHANNEL in chans
                        if not scope_wants[scope]:
                            continue
                        if c.telegram_chat_id not in seen:
                            seen.add(c.telegram_chat_id)
                            chat_ids.append(c.telegram_chat_id)
                return chat_ids
        except Exception as e:
            logger.error(f"Error resolviendo destinos Telegram: {e}")
            return []

    def _send_telegram(self, event, session, event_data, chat_ids):
        """
        Propósito (canal Telegram, #13): envía la alerta a los chat_ids YA
            RESUELTOS por dispositivo (los calculó _notify_user_if_applies). Si el
            evento trae un SNAPSHOT válido, envía FOTO + caption (la "foto
            inmediata"); si no, solo texto. True si al menos un chat recibió algo.

        Inputs: event, session (para resolver nombre de cámara), event_data (trae
            el snapshot), chat_ids (lista de destinos ya filtrados por dispositivo).
        Outputs: bool (any_ok). False si la lista viene vacía.
        Excepciones: por chat se capturan y loguean (un chat caído no aborta los demás).
        Llamado por: _send_notification (canal 'telegram').
        Llama a: _get_telegram_notifier, _telegram_cb.call → _send_photo/send_message.
        """
        try:
            if not chat_ids:
                return False

            notifier = self._get_telegram_notifier()

            # Nombre de cámara SIN tocar la relación lazy `event.camera`: el Event
            # llega DESLIGADO de su sesión (creado en otra sesión ya cerrada), así
            # que acceder a la relación lanza DetachedInstanceError y reventaba TODO
            # el envío (ni foto ni texto). Resolvemos por event_data.camera_name o,
            # como respaldo, consultando Camera en la sesión activa. (Las columnas
            # simples —event_type/confidence/created_at/snapshot_path— sí están
            # cargadas; solo la relación necesitaba sesión.)
            cam_name = getattr(event_data, "camera_name", None) if event_data else None
            if not cam_name:
                try:
                    cam = session.get(Camera, event.camera_id)
                    cam_name = cam.name if cam else None
                except Exception:
                    cam_name = None
            cam_name = cam_name or event.camera_id

            message = (
                f"🚨 Alerta de Videovigilancia\n\n"
                f"📹 Cámara: {cam_name}\n"
                f"🎯 Evento: {event.event_type}\n"
                f"📊 Confianza: {event.confidence:.0%}\n"
                f"🕐 Hora: {event.created_at.strftime('%d/%m/%Y %H:%M:%S')}"
            )

            # Resolver y validar el snapshot (anti-traversal en el notifier). Si
            # hay foto válida se envía como FOTO + caption; si no, solo texto.
            # Fuente preferente: Event.snapshot_path (lo fija SIEMPRE event_service,
            # venga de la IA o del fallback). event_data.metadata es respaldo.
            snapshot = getattr(event, "snapshot_path", None)
            if not snapshot and event_data is not None and getattr(event_data, "metadata", None):
                snapshot = event_data.metadata.get("snapshot_path")
            validated = notifier._validate_snapshot_path(snapshot) if snapshot else None

            any_ok = False
            for chat_id in chat_ids:
                try:
                    if validated:
                        result = self._telegram_cb.call(
                            notifier._send_photo, chat_id, validated, message
                        )
                    else:
                        result = self._telegram_cb.call(
                            notifier.send_message, chat_id, message
                        )
                    any_ok = any_ok or bool(result)
                except Exception as e:
                    logger.warning(f"Telegram a chat {chat_id} falló: {e}")
            return any_ok

        except Exception as e:
            logger.error(f"Error enviando Telegram: {e}")
            return False
    
    def shutdown(self):
        """Apaga el pool de hilos del router (sin esperar a las tareas en curso).
        Llamado en el apagado ordenado del sistema."""
        self._executor.shutdown(wait=False)
        logger.info("NotificationRouter detenido")


# Singleton global del Pipeline #13: se crea al importar y se suscribe al
# EventManager para recibir cada evento publicado.
notification_router = NotificationRouter()