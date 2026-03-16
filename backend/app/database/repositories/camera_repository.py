"""
Repositorio especializado para operaciones con cámaras.
Extiende BaseRepository con métodos específicos de búsqueda.
"""
import logging
from typing import List, Optional

from ..models import Camera
from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class CameraRepository(BaseRepository[Camera]):
    """
    Repositorio para gestión de cámaras de videovigilancia.
    Proporciona búsquedas específicas por estado y capacidades.
    """
    
    def __init__(self) -> None:
        """Inicializa el repositorio con el modelo Camera."""
        super().__init__(Camera)
        self.logger = logging.getLogger(__name__)
    
    def get_active_cameras(self) -> List[Camera]:
        """
        Obtiene todas las cámaras marcadas como activas.
        
        Returns:
            Lista de cámaras activas
        """
        try:
            from ..connection import db_manager
            with db_manager.get_session() as session:
                results = session.query(Camera).filter_by(is_active=True).all()
                for result in results:
                    session.expunge(result)
                return results
        except Exception as error:
            self.logger.error(f"Error al obtener cámaras activas: {error}")
            raise
    
    def get_by_ip(self, ip_address: str) -> Optional[Camera]:
        """
        Busca una cámara por su dirección IP.
        
        Args:
            ip_address: Dirección IPv4 o IPv6
            
        Returns:
            Cámara encontrada o None
        """
        try:
            from ..connection import db_manager
            with db_manager.get_session() as session:
                result = session.query(Camera).filter_by(ip_address=ip_address).first()
                if result:
                    session.expunge(result)
                return result
        except Exception as error:
            self.logger.error(f"Error al buscar cámara por IP {ip_address}: {error}")
            raise
    
    def get_ai_camera(self) -> Optional[Camera]:
        """
        Obtiene la primera cámara activa con capacidad de IA habilitada.
        Útil para determinar qué cámara procesar con YOLO.
        
        Returns:
            Cámara con IA activa o None si no hay ninguna
        """
        try:
            from ..connection import db_manager
            with db_manager.get_session() as session:
                result = session.query(Camera).filter_by(
                    is_active=True, 
                    has_ai=True
                ).first()
                if result:
                    session.expunge(result)
                return result
        except Exception as error:
            self.logger.error(f"Error al obtener cámara con IA: {error}")
            raise
