"""
Motion Detector - Detección de movimiento optimizada con OpenCV.
"""


import cv2
import numpy as np
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class MotionResult:
    """Resultado del análisis de movimiento."""

    has_motion: bool
    motion_score: float
    motion_mask: np.ndarray


class MotionDetector:
    """
    Detector de movimiento basado en diferencia de frames.
    Optimizado para reducir falsos positivos por ruido.
    """


    def __init__(self, camera_id: int, sensitivity: float = 0.02):
        """
        Inicializa el detector de movimiento.
        
        Args:
            camera_id: ID de la cámara para logging
            sensitivity: Umbral de sensibilidad (0.0 - 1.0, default 0.02 = 2%)
        """

        self.camera_id = camera_id
        self.sensitivity = sensitivity
        self._prev_gray: np.ndarray | None = None
        self._kernel = np.ones((3, 3), np.uint8)
        logger.info(f"MotionDetector inicializado para cámara {camera_id} (sensibilidad: {sensitivity})")

    def detect(self, frame: np.ndarray) -> MotionResult:
        """
        Detecta movimiento en un frame.
        
        Args:
            frame: Frame BGR de OpenCV
        
        Returns:
            MotionResult con información del movimiento detectado
        """

        # Convertir a escala de grises
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Aplicar blur gaussiano para reducir ruido
        gray_blurred = cv2.GaussianBlur(gray, (21, 21), 0)

        # Si no hay frame previo, inicializar y retornar sin movimiento
        if self._prev_gray is None:
            self._prev_gray = gray_blurred
            return MotionResult(
                has_motion=False,
                motion_score=0.0,
                motion_mask=np.zeros_like(gray)
            )

        # Calcular diferencia absoluta entre frames
        diff = cv2.absdiff(self._prev_gray, gray_blurred)

        # Aplicar umbral para binarizar
        _, mask = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

        # Dilatar para conectar regiones cercanas
        mask = cv2.dilate(mask, self._kernel, iterations=2)

        # Calcular score de movimiento (porcentaje de pixeles activos)
        motion_score = np.sum(mask > 0) / mask.size

        # Actualizar frame previo. NO se necesita .copy(): cv2.GaussianBlur
        # devuelve un array NUEVO en cada llamada (no es vista del frame), y
        # solo se lee de forma no destructiva en cv2.absdiff. Copiar aquí eran
        # ~345KB/frame (×fps) de memcpy puro sin beneficio.
        self._prev_gray = gray_blurred

        has_motion = motion_score > self.sensitivity

        if has_motion:
            logger.debug(f"Cámara {self.camera_id}: Movimiento detectado (score: {motion_score:.4f})")

        return MotionResult(
            has_motion=has_motion,
            motion_score=motion_score,
            motion_mask=mask
        )

    def reset(self) -> None:
        """Resetea el estado del detector (util cuando se pausa el stream)."""

        self._prev_gray = None
        logger.debug(f"MotionDetector reseteado para cámara {self.camera_id}")
