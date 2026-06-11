"""
================================================================================
MÓDULO: camera_repository — Acceso a datos de cámaras
================================================================================

PROPÓSITO
    Repositorio concreto del modelo `Camera`: hereda el CRUD genérico de
    `BaseRepository[Camera]` y añade búsquedas propias del dominio (cámaras
    activas, por IP, cámara con IA).

RESPONSABILIDAD PRINCIPAL
    Resolver las consultas de cámaras que necesitan el arranque y los servicios,
    devolviendo entidades desvinculadas de la sesión.

DEPENDENCIAS IMPORTANTES
    database.models.Camera, database.repositories.base_repository.BaseRepository,
    database.connection.db_manager (sesiones).

COMPONENTES RELACIONADOS (quién lo consume)
    CameraManager (pipeline #1 Inicio: `get_active_cameras`), AIService/scheduler
    (pipeline #9: `get_ai_camera`), y rutas/servicios de gestión de cámaras.

PIPELINES
    #1 Inicio (cámaras activas), #4 RTSP/#5 go2rtc/#7 ONVIF/#8 PTZ (config de
    cámara), #9 IA (cámara con detección habilitada).
================================================================================
"""
import logging
from typing import List, Optional

from ..models import Camera
from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class CameraRepository(BaseRepository[Camera]):
    """
    Repositorio del modelo `Camera`.

    ROL
        CRUD heredado de BaseRepository + búsquedas por estado/capacidad
        (activas, por IP, con IA). Lo consumen CameraManager y los servicios de
        cámara/IA.
    """
    
    def __init__(self) -> None:
        """Inicializa el repositorio con el modelo Camera."""
        super().__init__(Camera)
        self.logger = logging.getLogger(__name__)
    
    def get_active_cameras(self) -> List[Camera]:
        """
        Obtiene todas las cámaras con `is_active=True` (etapa del pipeline #1).

        Returns:
            Lista de cámaras activas (desvinculadas de la sesión).
        Llamado por:
            CameraManager.start_all_active() en el arranque, para levantar el
            pipeline de captura de cada cámara activa.
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
        Primera cámara activa con IA habilitada (is_active y has_ai) — pipeline #9.

        Recuérdese la restricción del sistema: solo UNA cámara corre YOLO a la
        vez (selección por AI_CAMERA_ID). Este método sirve para resolver la
        cámara candidata a detección.

        Returns:
            Cámara con IA activa o None si no hay ninguna.
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
