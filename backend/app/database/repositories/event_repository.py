"""
Repositorio especializado para operaciones con eventos de seguridad.
Soporta consultas por tiempo, cámara y estado de reconocimiento.
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from ..models import Event
from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class EventRepository(BaseRepository[Event]):
    """
    Repositorio para gestión de eventos de videovigilancia.
    Incluye métodos para alertas y reconocimiento de eventos.
    """
    
    def __init__(self) -> None:
        """Inicializa el repositorio con el modelo Event."""
        super().__init__(Event)
        self.logger = logging.getLogger(__name__)
    
    def get_by_camera(self, camera_id: int, limit: int = 50) -> List[Event]:
        """
        Obtiene los últimos eventos de una cámara específica.
        
        Args:
            camera_id: ID de la cámara
            limit: Número máximo de resultados (default 50)
            
        Returns:
            Lista de eventos ordenados por fecha descendente
        """
        try:
            from ..connection import db_manager
            with db_manager.get_session() as session:
                results = session.query(Event).filter_by(
                    camera_id=camera_id
                ).order_by(
                    Event.created_at.desc()
                ).limit(limit).all()
                
                for result in results:
                    session.expunge(result)
                return results
        except Exception as error:
            self.logger.error(f"Error al obtener eventos de cámara {camera_id}: {error}")
            raise
    
    def get_unacknowledged(self) -> List[Event]:
        """
        Obtiene todos los eventos no reconocidos (pendientes de revisión).
        
        Returns:
            Lista de eventos sin reconocer
        """
        try:
            from ..connection import db_manager
            with db_manager.get_session() as session:
                results = session.query(Event).filter_by(
                    acknowledged=False
                ).order_by(
                    Event.created_at.desc()
                ).all()
                
                for result in results:
                    session.expunge(result)
                return results
        except Exception as error:
            self.logger.error(f"Error al obtener eventos no reconocidos: {error}")
            raise
    
    def get_by_type(self, event_type: str, limit: int = 50) -> List[Event]:
        """
        Filtra eventos por tipo (motion, person, vehicle, etc.).
        
        Args:
            event_type: Tipo de evento según EventType
            limit: Número máximo de resultados
            
        Returns:
            Lista de eventos del tipo especificado
        """
        try:
            from ..connection import db_manager
            with db_manager.get_session() as session:
                results = session.query(Event).filter_by(
                    event_type=event_type
                ).order_by(
                    Event.created_at.desc()
                ).limit(limit).all()
                
                for result in results:
                    session.expunge(result)
                return results
        except Exception as error:
            self.logger.error(f"Error al obtener eventos tipo {event_type}: {error}")
            raise
    
    def get_recent(self, hours: int = 24, limit: int = 1000) -> List[Event]:
        """
        Obtiene eventos ocurridos en las últimas N horas.
        
        Args:
            hours: Ventana de tiempo hacia atrás (default 24)
            limit: Máximo número de resultados (default 1000)
            
        Returns:
            Lista de eventos recientes
        """
        try:
            from ..connection import db_manager
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            
            with db_manager.get_session() as session:
                results = session.query(Event).filter(
                    Event.created_at >= cutoff_time
                ).order_by(
                    Event.created_at.desc()
                ).limit(limit).all()  # ← AGREGADO: .limit(limit)
                
                for result in results:
                    session.expunge(result)
                return results
        except Exception as error:
            self.logger.error(f"Error al obtener eventos recientes: {error}")
            raise
    
    def acknowledge(self, event_id: int) -> bool:
        """
        Marca un evento como reconocido/revisado.
        
        Args:
            event_id: ID del evento a reconocer
            
        Returns:
            True si se actualizó, False si no existía
        """
        try:
            from ..connection import db_manager
            with db_manager.get_session() as session:
                event = session.get(Event, event_id)
                if event:
                    event.acknowledged = True
                    return True
                return False
        except Exception as error:
            self.logger.error(f"Error al reconocer evento {event_id}: {error}")
            raise