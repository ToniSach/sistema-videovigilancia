import time
import logging
import threading
from typing import Callable, Optional, Dict

from ..cameras.camera_manager import CameraManager
from ..processing.ai.ai_scheduler import AIScheduler
from ..events.event_manager import event_manager, EventData
from ..config import settings

logger = logging.getLogger(__name__)


class AIService:
    """
    Servicio singleton que gestiona el procesamiento de IA
    para todas las cámaras del sistema.
    """

    def __init__(self, camera_manager: CameraManager):
        self._camera_manager = camera_manager
        self._schedulers: Dict[int, AIScheduler] = {}
        self._lock = threading.Lock()
        self._event_callback: Optional[
            Callable[[int, str, str, float, dict], None]
        ] = None

        # Cache GPU availability (no lo recalcules cada vez)
        self._gpu_available = self._detect_gpu()

        logger.info(
            f"AIService inicializado | GPU: {'disponible' if self._gpu_available else 'no disponible'}"
        )

    # =========================
    # 🔧 UTILIDADES INTERNAS
    # =========================

    def _detect_gpu(self) -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except Exception:
            return False

    def _detection_callback(self, cam_id, class_name, conf, frame, metadata):
        self._handle_detection(cam_id, class_name, conf, frame, metadata)

    def _can_activate_more_ai(self) -> bool:
        max_ai_cameras = getattr(settings, "MAX_AI_CAMERAS", 4)

        current_ai_count = len(self._schedulers)

        if current_ai_count < max_ai_cameras:
            return True

        if self._gpu_available:
            logger.warning(
                f"Superando límite CPU ({max_ai_cameras}), pero GPU disponible → permitido"
            )
            return True

        logger.error(
            f"Límite de IA alcanzado ({max_ai_cameras} cámaras) y no hay GPU disponible"
        )
        return False

    # =========================
    # 🎯 EVENTOS
    # =========================

    def set_event_callback(
        self,
        callback: Callable[[int, str, str, float, dict], None]
    ) -> None:
        self._event_callback = callback

        with self._lock:
            schedulers = list(self._schedulers.values())

        for scheduler in schedulers:
            scheduler.set_detection_callback(self._detection_callback)

        logger.debug("Callback de eventos asignado a AIService")

    def _handle_detection(
        self,
        camera_id: int,
        class_name: str,
        confidence: float,
        frame,
        metadata: Optional[dict] = None
    ) -> None:
        metadata = metadata or {}

        # Callback externo (logging, analytics, etc.)
        if self._event_callback:
            try:
                self._event_callback(
                    camera_id, class_name, class_name, confidence, metadata
                )
            except Exception as e:
                logger.error(f"Error en event_callback: {e}")

        # Metadata estructurada
        event_metadata = {
            "class": class_name,
            "count": metadata.get("count", 1),
            "snapshot_path": metadata.get("snapshot_path"),
        }

        # Nombre de cámara
        camera_name = f"Camara_{camera_id}"
        try:
            camera = self._camera_manager.get_camera_by_id(camera_id)
            if camera:
                camera_name = camera.name
        except Exception:
            pass

        # Crear evento
        event_data = EventData(
            camera_id=camera_id,
            camera_name=camera_name,
            event_type=class_name,
            confidence=confidence,
            timestamp=time.time(),
            frame=frame,
            metadata=event_metadata,
        )

        # Emitir evento
        try:
            event_manager.emit(event_data)
            logger.debug(f"Evento publicado: {class_name} (cam {camera_id})")
        except Exception as e:
            logger.error(f"Error publicando evento: {e}")

    # =========================
    # 🚀 CONTROL DE IA
    # =========================

    def activate_ai(self, camera_id: int, mode: str = "low_cpu") -> bool:
        # Validar límite sin bloquear demasiado tiempo
        with self._lock:
            if not self._can_activate_more_ai():
                return False

        distributor = self._camera_manager.get_distributor(camera_id)
        if distributor is None:
            logger.error(f"No se encontró distributor para cámara {camera_id}")
            return False

        with self._lock:
            # Reemplazo si ya existe
            if camera_id in self._schedulers:
                logger.warning(f"Reactivando IA para cámara {camera_id}")
                old_scheduler = self._schedulers[camera_id]
                old_scheduler.stop(distributor)
                del self._schedulers[camera_id]

            scheduler = AIScheduler(camera_id, mode, cooldown_seconds=30)

            if self._event_callback:
                scheduler.set_detection_callback(self._detection_callback)

            scheduler.start(distributor)
            self._schedulers[camera_id] = scheduler

        logger.info(f"IA activada | cámara={camera_id} | modo={mode}")
        return True

    def deactivate_ai(self, camera_id: int) -> bool:
        distributor = self._camera_manager.get_distributor(camera_id)

        with self._lock:
            scheduler = self._schedulers.pop(camera_id, None)

        if not scheduler:
            logger.warning(f"No hay IA activa en cámara {camera_id}")
            return False

        if distributor:
            scheduler.stop(distributor)

        logger.info(f"IA desactivada | cámara={camera_id}")
        return True

    def change_mode(self, camera_id: int, mode: str) -> bool:
        with self._lock:
            scheduler = self._schedulers.get(camera_id)

        if not scheduler:
            logger.error(f"No hay IA activa en cámara {camera_id}")
            return False

        scheduler.set_mode(mode)
        logger.info(f"Modo IA cambiado | cámara={camera_id} | modo={mode}")
        return True

    # =========================
    # 📊 ESTADO
    # =========================

    def get_ai_status(self) -> dict:
        with self._lock:
            schedulers_copy = dict(self._schedulers)

        return {
            "active_camera_ids": list(schedulers_copy.keys()),
            "active_count": len(schedulers_copy),
            "gpu_available": self._gpu_available,
            "schedulers": {
                cam_id: scheduler.get_stats()
                for cam_id, scheduler in schedulers_copy.items()
            },
        }

    # =========================
    # 🛑 SHUTDOWN
    # =========================

    def stop_all(self) -> None:
        with self._lock:
            schedulers_copy = dict(self._schedulers)
            self._schedulers.clear()

        for camera_id, scheduler in schedulers_copy.items():
            distributor = self._camera_manager.get_distributor(camera_id)
            if distributor:
                scheduler.stop(distributor)

        logger.info("Todos los schedulers de IA detenidos")