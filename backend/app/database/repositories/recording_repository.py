"""
================================================================================
MÓDULO: recording_repository — Acceso a datos de grabaciones de video
================================================================================

PROPÓSITO
    Repositorio concreto del modelo `Recording`: hereda el CRUD de
    `BaseRepository[Recording]` y añade consultas por cámara, por rango de fechas,
    las más antiguas (para rotación) y el tamaño total ocupado.

RESPONSABILIDAD PRINCIPAL
    Servir el índice en BD de los segmentos grabados para reproducción histórica
    y para la gestión de cuota/rotación de almacenamiento.

DEPENDENCIAS IMPORTANTES
    database.models.Recording, base_repository.BaseRepository,
    connection.db_manager, sqlalchemy.func (suma de tamaños).

COMPONENTES RELACIONADOS (quién lo consume)
    recording_manager (pipeline #11 Grabación: alta/cierre de segmentos),
    StorageManager (rotación: get_oldest + get_total_size_bytes), y las rutas de
    reproducción/timeline (pipeline #14).

PIPELINES
    #11 Grabación (escritura del índice), #14 Reproducción histórica (lectura),
    rotación/limpieza de almacenamiento (get_oldest / get_total_size_bytes).
================================================================================
"""
import logging
from datetime import datetime
from typing import List

from ..models import Recording
from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class RecordingRepository(BaseRepository[Recording]):
    """
    Repositorio del modelo `Recording`.

    ROL
        CRUD heredado + consultas por cámara/fecha y soporte de rotación
        (get_oldest, get_total_size_bytes). Lo consumen recording_manager
        (#11), StorageManager (rotación) y las rutas de reproducción (#14).
    """
    
    def __init__(self) -> None:
        """Inicializa el repositorio con el modelo Recording."""
        super().__init__(Recording)
        self.logger = logging.getLogger(__name__)
    
    def get_by_camera(self, camera_id: int, limit: int = 100) -> List[Recording]:
        """
        Obtiene las últimas grabaciones de una cámara específica.
        
        Args:
            camera_id: ID de la cámara
            limit: Número máximo de resultados (default 100)
            
        Returns:
            Lista de grabaciones ordenadas por fecha descendente
        """
        try:
            from ..connection import db_manager
            with db_manager.get_session() as session:
                results = session.query(Recording).filter_by(
                    camera_id=camera_id
                ).order_by(
                    Recording.start_time.desc()
                ).limit(limit).all()
                
                for result in results:
                    session.expunge(result)
                return results
        except Exception as error:
            self.logger.error(f"Error al obtener grabaciones de cámara {camera_id}: {error}")
            raise
    
    def get_oldest(self, count: int = 10) -> List[Recording]:
        """
        Obtiene las grabaciones más antiguas para rotación de almacenamiento.
        
        Args:
            count: Número de grabaciones a recuperar (default 10)
            
        Returns:
            Lista de grabaciones antiguas (primero a eliminar)
        """
        try:
            from ..connection import db_manager
            with db_manager.get_session() as session:
                results = session.query(Recording).order_by(
                    Recording.start_time.asc()
                ).limit(count).all()
                
                for result in results:
                    session.expunge(result)
                return results
        except Exception as error:
            self.logger.error(f"Error al obtener grabaciones antiguas: {error}")
            raise
    
    def get_total_size_bytes(self) -> int:
        """
        Calcula el tamaño total ocupado por todas las grabaciones.
        Útil para verificar límites de almacenamiento.
        
        Returns:
            Suma total en bytes del campo file_size_bytes
        """
        try:
            from ..connection import db_manager
            from sqlalchemy import func
            
            with db_manager.get_session() as session:
                total = session.query(
                    func.sum(Recording.file_size_bytes)
                ).scalar()
                
                return total or 0
        except Exception as error:
            self.logger.error(f"Error al calcular tamaño total: {error}")
            raise
    
    def get_by_date_range(
        self, 
        camera_id: int, 
        start: datetime, 
        end: datetime
    ) -> List[Recording]:
        """
        Obtiene grabaciones de un rango de fechas específico.
        
        Args:
            camera_id: ID de la cámara
            start: Fecha/hora inicial
            end: Fecha/hora final
            
        Returns:
            Lista de grabaciones en el rango especificado
        """
        try:
            from ..connection import db_manager
            with db_manager.get_session() as session:
                results = session.query(Recording).filter_by(
                    camera_id=camera_id
                ).filter(
                    Recording.start_time >= start,
                    Recording.start_time <= end
                ).order_by(
                    Recording.start_time.desc()
                ).all()
                
                for result in results:
                    session.expunge(result)
                return results
        except Exception as error:
            self.logger.error(f"Error al obtener grabaciones por rango de fechas: {error}")
            raise
