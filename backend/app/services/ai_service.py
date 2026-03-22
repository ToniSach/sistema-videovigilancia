"""
AI Service - Servicio de alto nivel para gestión de IA.
Coordina schedulers para todas las cámaras activas.
"""

from typing import Callable
import logging
import threading

from ..cameras.camera_manager import CameraManager
from ..processing.ai.ai_scheduler import AIScheduler

logger = logging.getLogger(__name__)


class AIService:
    """
    Servicio singleton que gestiona el procesamiento de IA
    para todas las cámaras del sistema.
    """

    def __init__(self, camera_manager: CameraManager):
        """
        Inicializa el servicio de IA.
        
        Args:
            camera_manager: Instancia de CameraManager para acceder a distributors
        """

        self._camera_manager = camera_manager
        self._schedulers: dict[int, AIScheduler] = {}
        self._lock = threading.Lock()
        self._event_callback: Callable[[int, str, str, float, dict], None] | None = None

        logger.info("AIService inicializado")

    def set_event_callback(self, callback: Callable[[int, str, str, float, dict], None]) -> None:
        """
        Establece callback para eventos de detección.
        Será llamado cuando cualquier scheduler detecte un objeto.
        
        Args:
            callback: Función(camera_id, event_type, class_name, confidence, metadata)
        """

        self._event_callback = callback

        # Actualizar callbacks en schedulers existentes
        with self._lock:
            for scheduler in self._schedulers.values():
                scheduler.set_detection_callback(
                    lambda cam_id, class_name, conf, frame, metadata: 
                    self._handle_detection(cam_id, class_name, conf, frame, metadata)
                )

        logger.debug("Callback de eventos asignado a AIService")

    def _handle_detection(self, camera_id: int, class_name: str, confidence: float, frame, metadata: dict = None) -> None:
        """
        Handler interno para detecciones de schedulers.
        Recibe metadata con count (cantidad de objetos detectados).
        """
        if self._event_callback:
            event_metadata = metadata or {}
            event_metadata["detected_class"] = class_name
            
            # Si hay múltiples objetos, incluir el conteo en el mensaje
            if metadata and metadata.get("count", 1) > 1:
                event_metadata["object_count"] = metadata["count"]
                logger.info(f"Detección múltiple: {metadata['count']} {class_name}s")
            
            self._event_callback(
                camera_id,
                "ai_detection",
                class_name,
                confidence,
                event_metadata
            )

    def activate_ai(self, camera_id: int, mode: str = "low_cpu") -> bool:
        """
        Activa el procesamiento de IA para una cámara.
        Esto significa: si detecta una persona, espera 30s antes de alertar "persona" otra vez
        
        Args:
            camera_id: ID de la cámara
            mode: Modo de operación (low_cpu o high_quality)
        
        Returns:
            True si se activó correctamente
        """
        # CORRECCIÓN: La instanciación ahora está DENTRO del método
        # Obtener distributor
        distributor = self._camera_manager.get_distributor(camera_id)
        if distributor is None:
            logger.error(f"No se encontró distributor para cámara {camera_id}")
            return False

        with self._lock:
            # Si ya existe, desactivar primero
            if camera_id in self._schedulers:
                logger.warning(f"Reactivando IA para cámara {camera_id}")
                old_scheduler = self._schedulers[camera_id]
                old_scheduler.stop(distributor)
                del self._schedulers[camera_id]

            # CORRECCIÓN: Crear scheduler DENTRO del método, no en la firma
            scheduler = AIScheduler(camera_id, mode, cooldown_seconds=30)

            # Asignar callback si existe
            if self._event_callback:
                scheduler.set_detection_callback(
                    lambda cam_id, class_name, conf, frame, metadata: 
                    self._handle_detection(cam_id, class_name, conf, frame, metadata)
                )

            # Iniciar
            scheduler.start(distributor)
            self._schedulers[camera_id] = scheduler

        logger.info(f"IA activada para cámara {camera_id} (modo: {mode})")
        return True

    def deactivate_ai(self, camera_id: int) -> bool:
        """
        Desactiva el procesamiento de IA para una cámara.
        
        Args:
            camera_id: ID de la cámara
        
        Returns:
            True si se desactivó correctamente
        """

        distributor = self._camera_manager.get_distributor(camera_id)

        with self._lock:
            if camera_id not in self._schedulers:
                logger.warning(f"No hay IA activa para desactivar en cámara {camera_id}")
                return False

            scheduler = self._schedulers.pop(camera_id)

        # Detener fuera del lock para evitar bloqueos
        if distributor:
            scheduler.stop(distributor)

        logger.info(f"IA desactivada para cámara {camera_id}")
        return True

    def change_mode(self, camera_id: int, mode: str) -> bool:
        """
        Cambia el modo de IA para una cámara activa.
        
        Args:
            camera_id: ID de la cámara
            mode: Nuevo modo (low_cpu o high_quality)
        
        Returns:
            True si se cambió correctamente
        """

        with self._lock:
            if camera_id not in self._schedulers:
                logger.error(f"No hay IA activa en cámara {camera_id} para cambiar modo")
                return False

            self._schedulers[camera_id].set_mode(mode)

        logger.info(f"Modo cambiado a {mode} para cámara {camera_id}")
        return True

    def get_ai_status(self) -> dict:
        """
        Retorna estado de IA para todas las cámaras.
        
        Returns:
            Dict con IDs activas y estadísticas
        """

        with self._lock:
            active_ids = list(self._schedulers.keys())
            schedulers_stats = {
                cam_id: scheduler.get_stats() 
                for cam_id, scheduler in self._schedulers.items()
            }

        return {
            "active_camera_ids": active_ids,
            "active_count": len(active_ids),
            "schedulers": schedulers_stats
        }

    def stop_all(self) -> None:
        """
        Detiene todos los schedulers de IA (util para shutdown)."""

        with self._lock:
            schedulers_copy = dict(self._schedulers)
            self._schedulers.clear()

        for camera_id, scheduler in schedulers_copy.items():
            distributor = self._camera_manager.get_distributor(camera_id)
            if distributor:
                scheduler.stop(distributor)

        logger.info("Todos los schedulers de IA detenidos")