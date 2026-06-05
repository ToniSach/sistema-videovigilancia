"""
Notification Router - Orquesta el envío de notificaciones a usuarios.
NO bloqueante - usa ThreadPoolExecutor.
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Optional
from concurrent.futures import ThreadPoolExecutor

from backend.app.database.models import (
    Event, User, NotificationPreference, NotificationChannel,
    NotificationDay, UserTelegramChat, NotificationLog,
    UserCameraPermission, Camera
)
from backend.app.database.connection import db_manager
from backend.app.services.permission_service import PermissionService

logger = logging.getLogger(__name__)


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
        self._executor.submit(self._process_event, event, event_data)
    
    def _process_event(self, event: Event, event_data=None):
        """
        Procesa un evento abriendo UNA SOLA sesión BD para todo el ciclo
        (lookup de permisos, prefs, canales, persistencia de log).

        Antes se abría una sesión nueva en _notify_user_if_applies,
        _check_day y _send_notification → ~30 sesiones por evento con 10
        usuarios. Eso saturaba el pool de conexiones bajo carga.
        """
        try:
            camera_id = event.camera_id

            # GATE IA: solo se notifica de la cámara con IA activa. Si no hay
            # ninguna IA activa → no se notifica nada (los usuarios tampoco
            # pueden personalizar notificaciones hasta que se active).
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
        """Evalúa preferencias del usuario y dispara notificaciones aplicables.
        Reutiliza la session pasada por _process_event."""
        try:
            prefs = session.query(NotificationPreference).filter_by(
                user_id=user_id,
                event_type=event.event_type,
                enabled=True
            ).all()

            camera_specific = [p for p in prefs if p.camera_id == event.camera_id]
            general_prefs = [p for p in prefs if p.camera_id is None]
            applicable_prefs = camera_specific if camera_specific else general_prefs

            if not applicable_prefs:
                return

            for pref in applicable_prefs:
                if not self._check_schedule(pref):
                    continue
                if not self._check_day(pref, session):
                    continue

                cooldown_key = f"{user_id}:{event.camera_id}:{event.event_type}"
                if self._is_in_cooldown(cooldown_key):
                    continue

                channels = session.query(NotificationChannel).filter_by(
                    preference_id=pref.id
                ).all()

                for channel in channels:
                    self._send_notification(
                        user_id=user_id,
                        event=event,
                        channel=channel.channel,
                        cooldown_key=cooldown_key,
                        event_data=event_data,
                        session=session,
                    )

        except Exception as e:
            logger.error(f"Error notificando a usuario {user_id}: {e}", exc_info=True)

    def _check_schedule(self, pref: NotificationPreference) -> bool:
        if pref.schedule_start is None or pref.schedule_end is None:
            return True

        now = datetime.now().time()

        if pref.schedule_start < pref.schedule_end:
            return pref.schedule_start <= now <= pref.schedule_end
        else:
            return now >= pref.schedule_start or now <= pref.schedule_end

    def _check_day(self, pref: NotificationPreference, session) -> bool:
        """Recibe session para reutilizar la conexión ya abierta."""
        days = session.query(NotificationDay).filter_by(preference_id=pref.id).all()
        if not days:
            return True
        today = (datetime.now().weekday() + 1) % 7
        return any(d.day_of_week == today for d in days)
    
    def _is_in_cooldown(self, cooldown_key: str, seconds: int = 300) -> bool:
        with self._cooldown_lock:
            last_time = self._cooldown_cache.get(cooldown_key)
            if last_time is None:
                return False
            
            if datetime.now() - last_time < timedelta(seconds=seconds):
                return True
            
            del self._cooldown_cache[cooldown_key]
            return False
    
    def _update_cooldown(self, cooldown_key: str):
        with self._cooldown_lock:
            self._cooldown_cache[cooldown_key] = datetime.now()
    
    def _send_notification(self, user_id, event, channel, cooldown_key,
                            event_data=None, session=None):
        """
        Inserta NotificationLog y envía notificación por canal indicado.
        Reutiliza la session de _process_event si se pasa; sino abre una nueva
        (fallback para llamadas externas, p.ej. tests).
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
                    success = self._send_telegram(user_id, event, sess)
                elif channel in ('app', 'push', 'web'):
                    # Canal "en la app": la entrega en tiempo real la hace el
                    # WSNotificationBroker a los clientes (escritorio/móvil)
                    # conectados por WebSocket — funciona en LAN sin internet.
                    # ('push'/'web' se aceptan por compatibilidad con datos
                    # antiguos; FCM fue eliminado.) Aquí solo se registra.
                    success = True

                log.status = 'sent' if success else 'failed'

                if success:
                    self._update_cooldown(cooldown_key)

        except Exception as e:
            logger.error(f"Error enviando notificación: {e}", exc_info=True)
    
    def _send_telegram(self, user_id, event, session):
        try:
            # Un usuario puede tener VARIOS chats de Telegram vinculados
            # (distintas cuentas: su móvil, el de un familiar, un grupo…).
            # Hay que enviar a TODOS, no solo al primero.
            chats = session.query(UserTelegramChat).filter_by(
                user_id=user_id, is_active=True
            ).all()

            if not chats:
                return False

            notifier = self._get_telegram_notifier()

            message = (
                f"🚨 Alerta de Videovigilancia\n\n"
                f"📹 Cámara: {event.camera.name if event.camera else event.camera_id}\n"
                f"🎯 Evento: {event.event_type}\n"
                f"📊 Confianza: {event.confidence:.0%}\n"
                f"🕐 Hora: {event.created_at.strftime('%d/%m/%Y %H:%M:%S')}"
            )

            any_ok = False
            for chat in chats:
                try:
                    result = self._telegram_cb.call(
                        notifier.send_message, chat.telegram_chat_id, message
                    )
                    any_ok = any_ok or bool(result)
                except Exception as e:
                    logger.warning(f"Telegram a chat {chat.telegram_chat_id} falló: {e}")
            return any_ok

        except Exception as e:
            logger.error(f"Error enviando Telegram: {e}")
            return False
    
    def shutdown(self):
        self._executor.shutdown(wait=False)
        logger.info("NotificationRouter detenido")


# Instancia global
notification_router = NotificationRouter()