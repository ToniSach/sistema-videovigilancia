"""
================================================================================
MÓDULO: event_repository — Acceso a datos de eventos de seguridad
================================================================================

PROPÓSITO
    Repositorio concreto del modelo `Event`: hereda el CRUD de
    `BaseRepository[Event]` y añade consultas por cámara, por tipo, por tiempo y
    por estado de reconocimiento, más la acción de "reconocer" (acknowledge).

RESPONSABILIDAD PRINCIPAL
    Resolver listados/timeline de eventos y la marcación de eventos como
    revisados, devolviendo entidades desvinculadas de la sesión.

DEPENDENCIAS IMPORTANTES
    database.models.Event, base_repository.BaseRepository, connection.db_manager.

COMPONENTES RELACIONADOS (quién lo consume)
    EventService (persiste/consulta eventos del pipeline #10), las rutas de
    eventos del API y la UI (listados, pendientes, timeline).

PIPELINES
    #10 Eventos (escritura vía create heredado + lectura aquí) y, de forma
    indirecta, #9 IA (origen de los eventos) y #13 Notificaciones (consumidor).
================================================================================
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from ..models import Event
from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class EventRepository(BaseRepository[Event]):
    """
    Repositorio del modelo `Event`.

    ROL
        CRUD heredado + consultas por cámara/tipo/tiempo y `acknowledge()`.
        Lo consume EventService y las rutas de eventos del API (pipeline #10).
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
    
    def get_history_page(self, camera_ids, before=None, limit: int = 50,
                         hours: int = 168) -> List[Event]:
        """
        Página de historial para el móvil con paginación por cursor a NIVEL BD.

        Filtra por cámaras accesibles y created_at < `before` (cursor, datetime),
        ordenado descendente, LIMIT en la consulta. Así TODOS los eventos de la
        ventana son alcanzables al hacer scroll — antes el endpoint cacheaba solo
        los 200 más recientes globales y los antiguos nunca aparecían.

        Args:
            camera_ids: ids de cámaras accesibles por el usuario.
            before: datetime tope (exclusivo); None = desde ahora.
            limit: máximo de resultados de esta página.
            hours: ventana máxima de historial (default 7 días).
        """
        try:
            from ..connection import db_manager
            if not camera_ids:
                return []
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            with db_manager.get_session() as session:
                query = session.query(Event).filter(
                    Event.camera_id.in_(list(camera_ids)),
                    Event.created_at >= cutoff_time,
                )
                if before is not None:
                    query = query.filter(Event.created_at < before)
                results = query.order_by(
                    Event.created_at.desc()
                ).limit(limit).all()
                for result in results:
                    session.expunge(result)
                return results
        except Exception as error:
            self.logger.error(f"Error al obtener página de historial: {error}")
            raise

    def acknowledge(self, event_id: int) -> bool:
        """
        Marca un evento como reconocido/revisado (acknowledged=True).

        Modifica el objeto dentro de la sesión; el COMMIT lo hace el context
        manager al cerrar sin error (no se necesita save explícito).

        Args:
            event_id: ID del evento a reconocer
        Returns:
            True si se actualizó, False si el evento no existía.
        Llamado por:
            La acción de "reconocer alerta" desde el cliente (vía EventService/ruta).
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