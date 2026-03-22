"""
Notification Router - Orquesta el envío de notificaciones a usuarios.
NO bloqueante - usa ThreadPoolExecutor.
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor

from backend.app.database.models import (
    Event, User, NotificationPreference, NotificationChannel, 
    NotificationDay, UserTelegramChat, MobileDevice, NotificationLog
)
from backend.app.database.connection import db_manager
from backend.app.services.permission_service import PermissionService

logger = logging.getLogger(__name__)


class NotificationRouter:
    """
    Router de notificaciones que distribuye eventos a usuarios según:
    - Permisos de cámara
    - Preferencias de notificación
    - Horarios y días configurados
    - Cooldown anti-spam
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
        
        # Importar notificadores (lazy para evitar circular imports)
        self._telegram_notifier = None
        self._fcm_notifier = None
        
        logger.info("NotificationRouter inicializado")
    
    def _get_telegram_notifier(self):
        if self._telegram_notifier is None:
            from backend.app.services.telegram_notifier import telegram_notifier
            self._telegram_notifier = telegram_notifier
        return self._telegram_notifier
    
    def _get_fcm_notifier(self):
        if self._fcm_notifier is None:
            from backend.app.services.fcm_notifier import fcm_notifier
            self._fcm_notifier = fcm_notifier
        return self._fcm_notifier
    
    def route_event(self, event: Event, event_data=None):
        """
        Rutea un evento a los usuarios correspondientes.
        Ejecutado de forma asíncrona vía executor.
        
        Args:
            event: Evento de la base de datos
            event_data: Datos adicionales del evento (FrameData, etc.)
        """
        self._executor.submit(self._process_event, event, event_data)
    
    def _process_event(self, event: Event, event_data=None):
        """Procesa el evento y envía notificaciones."""
        try:
            camera_id = event.camera_id
            event_type = event.event_type
            
            # Obtener usuarios con acceso a esta cámara
            with db_manager.get_session() as session:
                # Obtener todos los usuarios que tienen permisos sobre esta cámara
                perms = session.query(UserCameraPermission).filter_by(
                    camera_id=camera_id, can_view=True
                ).all()
                
                user_ids = {p.user_id for p in perms}
                
                # Agregar owner de la cámara
                camera = session.get(Camera, camera_id)
                if camera and camera.owner_id:
                    user_ids.add(camera.owner_id)
                
                # Para cada usuario, evaluar si debe notificar
                for user_id in user_ids:
                    self._notify_user_if_applies(user_id, event, event_data)
                    
        except Exception as e:
            logger.error(f"Error ruteando evento {event.id}: {e}", exc_info=True)
    
    def _notify_user_if_applies(self, user_id: int, event: Event, event_data):
        """Evalúa si se debe notificar a un usuario específico."""
        try:
            with db_manager.get_session() as session:
                # Buscar preferencias para este tipo de evento
                prefs = session.query(NotificationPreference).filter_by(
                    user_id=user_id,
                    event_type=event.event_type,
                    enabled=True
                ).all()
                
                # Si hay preferencia específica por cámara, verificar
                camera_specific = [p for p in prefs if p.camera_id == event.camera_id]
                general_prefs = [p for p in prefs if p.camera_id is None]
                
                # Priorizar específicas sobre generales
                applicable_prefs = camera_specific if camera_specific else general_prefs
                
                if not applicable_prefs:
                    return
                
                for pref in applicable_prefs:
                    # Verificar horario
                    if not self._check_schedule(pref):
                        continue
                    
                    # Verificar día
                    if not self._check_day(pref):
                        continue
                    
                    # Verificar cooldown
                    cooldown_key = f"{user_id}:{event.camera_id}:{event.event_type}"
                    if self._is_in_cooldown(cooldown_key):
                        continue
                    
                    # Obtener canales
                    channels = session.query(NotificationChannel).filter_by(
                        preference_id=pref.id
                    ).all()
                    
                    for channel in channels:
                        self._send_notification(
                            user_id=user_id,
                            event=event,
                            channel=channel.channel,
                            cooldown_key=cooldown_key,
                            event_data=event_data
                        )
                        
        except Exception as e:
            logger.error(f"Error notificando a usuario {user_id}: {e}")
    
    def _check_schedule(self, pref: NotificationPreference) -> bool:
        """Verifica si está dentro del horario configurado."""
        if pref.schedule_start is None or pref.schedule_end is None:
            return True
        
        now = datetime.now().time()
        
        if pref.schedule_start < pref.schedule_end:
            # Horario normal (ej: 09:00 a 18:00)
            return pref.schedule_start <= now <= pref.schedule_end
        else:
            # Horario nocturno (ej: 22:00 a 06:00)
            return now >= pref.schedule_start or now <= pref.schedule_end
    
    def _check_day(self, pref: NotificationPreference) -> bool:
        """Verifica si hoy es un día habilitado."""
        with db_manager.get_session() as session:
            days = session.query(NotificationDay).filter_by(preference_id=pref.id).all()
            if not days:
                return True  # Todos los días si no hay restricción
            
            today = datetime.now().weekday()  # 0=lunes, 6=domingo
            # Convertir a formato 0=domingo para consistencia con el modelo
            today = (today + 1) % 7
            return any(d.day_of_week == today for d in days)
    
    def _is_in_cooldown(self, cooldown_key: str, seconds: int = 300) -> bool:
        """
        Verifica cooldown anti-spam.
        Default: 5 minutos entre notificaciones del mismo tipo para mismo user/cámara.
        """
        with self._cooldown_lock:
            last_time = self._cooldown_cache.get(cooldown_key)
            if last_time is None:
                return False
            
            if datetime.now() - last_time < timedelta(seconds=seconds):
                return True
            
            # Expiró, limpiar
            del self._cooldown_cache[cooldown_key]
            return False
    
    def _update_cooldown(self, cooldown_key: str):
        """Actualiza timestamp de última notificación."""
        with self._cooldown_lock:
            self._cooldown_cache[cooldown_key] = datetime.now()
    
    def _send_notification(self, user_id: int, event: Event, channel: str, 
                          cooldown_key: str, event_data=None):
        """Envía notificación por canal específico."""
        try:
            with db_manager.get_session() as session:
                log = NotificationLog(
                    event_id=event.id,
                    user_id=user_id,
                    channel=channel,
                    status='pending',
                    cooldown_key=cooldown_key
                )
                session.add(log)
                session.flush()
                
                success = False
                error_msg = None
                
                if channel == 'telegram':
                    success = self._send_telegram(user_id, event, session)
                elif channel == 'push':
                    success = self._send_push(user_id, event, session)
                
                # Actualizar log
                log.status = 'sent' if success else 'failed'
                if not success and error_msg:
                    log.error_message = error_msg
                
                if success:
                    self._update_cooldown(cooldown_key)
                
        except Exception as e:
            logger.error(f"Error enviando notificación: {e}")
    
    def _send_telegram(self, user_id: int, event: Event, session) -> bool:
        """Envía notificación por Telegram."""
        try:
            # Obtener chat
            chat = session.query(UserTelegramChat).filter_by(
                user_id=user_id, is_active=True
            ).first()
            
            if not chat:
                return False
            
            notifier = self._get_telegram_notifier()
            
            # Construir mensaje
            message = (
                f"🚨 Alerta de Videovigilancia\n\n"
                f"📹 Cámara: {event.camera.name if event.camera else event.camera_id}\n"
                f"🎯 Evento: {event.event_type}\n"
                f"📊 Confianza: {event.confidence:.0%}\n"
                f"🕐 Hora: {event.created_at.strftime('%d/%m/%Y %H:%M:%S')}"
            )
            
            # Enviar (asumiendo que el notifier tiene método send_message)
            return notifier.send_message(chat.telegram_chat_id, message)
            
        except Exception as e:
            logger.error(f"Error enviando Telegram: {e}")
            return False
    
    def _send_push(self, user_id: int, event: Event, session) -> bool:
        """Envía notificación push por FCM."""
        try:
            devices = session.query(MobileDevice).filter_by(
                user_id=user_id, is_active=True
            ).all()
            
            if not devices:
                return False
            
            notifier = self._get_fcm_notifier()
            
            success = True
            for device in devices:
                try:
                    notifier.send_to_device(
                        fcm_token=device.fcm_token,
                        title=f"Alerta: {event.event_type}",
                        body=f"Cámara {event.camera.name if event.camera else event.camera_id}",
                        data={
                            "event_id": str(event.id),
                            "camera_id": str(event.camera_id),
                            "event_type": event.event_type
                        }
                    )
                except Exception as e:
                    logger.error(f"Error enviando push a dispositivo {device.id}: {e}")
                    success = False
            
            return success
            
        except Exception as e:
            logger.error(f"Error enviando push: {e}")
            return False
    
    def shutdown(self):
        """Limpia recursos."""
        self._executor.shutdown(wait=False)
        logger.info("NotificationRouter detenido")


# Instancia global
notification_router = NotificationRouter()