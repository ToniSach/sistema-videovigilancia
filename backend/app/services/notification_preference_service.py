"""
Servicio de gestión de preferencias de notificación.
"""
import logging
from typing import List, Optional
from datetime import time

from backend.app.database.models import (
    NotificationPreference, NotificationChannel, NotificationDay
)
from backend.app.database.connection import db_manager

logger = logging.getLogger(__name__)


class NotificationPreferenceService:
    """Gestiona preferencias de notificación por usuario."""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def create_preference(self, user_id: int, event_type: str, 
                         camera_id: Optional[int] = None,
                         enabled: bool = True,
                         channels: List[str] = None,
                         schedule_start: Optional[time] = None,
                         schedule_end: Optional[time] = None,
                         days_of_week: List[int] = None) -> NotificationPreference:
        """
        Crea preferencia de notificación.
        
        Args:
            channels: Lista de canales ['telegram', 'push']
            days_of_week: Lista de enteros 0-6 (0=domingo)
        """
        try:
            with db_manager.get_session() as session:
                # Crear preferencia base
                pref = NotificationPreference(
                    user_id=user_id,
                    event_type=event_type,
                    camera_id=camera_id,
                    enabled=enabled,
                    schedule_start=schedule_start,
                    schedule_end=schedule_end
                )
                session.add(pref)
                session.flush()
                
                # Agregar canales
                if channels:
                    for ch in channels:
                        nc = NotificationChannel(preference_id=pref.id, channel=ch)
                        session.add(nc)
                
                # Agregar días
                if days_of_week:
                    for day in days_of_week:
                        nd = NotificationDay(preference_id=pref.id, day_of_week=day)
                        session.add(nd)
                
                session.flush()
                session.expunge(pref)
                self.logger.info(f"Preferencia creada para user {user_id}, event {event_type}")
                return pref
        except Exception as e:
            self.logger.error(f"Error creando preferencia: {e}")
            raise
    
    def get_user_preferences(self, user_id: int) -> List[NotificationPreference]:
        """Obtiene todas las preferencias de un usuario."""
        try:
            with db_manager.get_session() as session:
                prefs = session.query(NotificationPreference).filter_by(user_id=user_id).all()
                for p in prefs:
                    # Cargar relaciones
                    p.channels = session.query(NotificationChannel).filter_by(preference_id=p.id).all()
                    p.days = session.query(NotificationDay).filter_by(preference_id=p.id).all()
                    session.expunge(p)
                return prefs
        except Exception as e:
            self.logger.error(f"Error obteniendo preferencias: {e}")
            raise
    
    def update_preference(self, pref_id: int, **kwargs) -> Optional[NotificationPreference]:
        """Actualiza preferencia."""
        try:
            with db_manager.get_session() as session:
                pref = session.get(NotificationPreference, pref_id)
                if not pref:
                    return None
                
                # Actualizar campos simples
                for key in ['enabled', 'schedule_start', 'schedule_end']:
                    if key in kwargs:
                        setattr(pref, key, kwargs[key])
                
                # Actualizar canales (reemplazar todos)
                if 'channels' in kwargs:
                    session.query(NotificationChannel).filter_by(preference_id=pref.id).delete()
                    for ch in kwargs['channels']:
                        session.add(NotificationChannel(preference_id=pref.id, channel=ch))
                
                # Actualizar días (reemplazar todos)
                if 'days_of_week' in kwargs:
                    session.query(NotificationDay).filter_by(preference_id=pref.id).delete()
                    for day in kwargs['days_of_week']:
                        session.add(NotificationDay(preference_id=pref.id, day_of_week=day))
                
                session.flush()
                session.expunge(pref)
                return pref
        except Exception as e:
            self.logger.error(f"Error actualizando preferencia: {e}")
            raise
    
    def delete_preference(self, pref_id: int) -> bool:
        """Elimina preferencia."""
        try:
            with db_manager.get_session() as session:
                pref = session.get(NotificationPreference, pref_id)
                if pref:
                    session.delete(pref)
                    return True
                return False
        except Exception as e:
            self.logger.error(f"Error eliminando preferencia: {e}")
            raise