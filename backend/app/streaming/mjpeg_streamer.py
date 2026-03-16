import cv2
import numpy as np
import threading
import time
import logging
from .frame_buffer import FrameData


class MJPEGStreamer:
    """
    Convierte frames numpy a JPEG y genera streams MJPEG para HTTP.
    Mantiene caché del último frame por cámara.
    """

    def __init__(self):
        self._frames: dict[int, bytes] = {}  # camera_id → último JPEG bytes
        self._lock = threading.Lock()
        self._jpeg_quality = 75
        self._logger = logging.getLogger(__name__)

    def update_frame(self, camera_id: int, frame_data: FrameData) -> None:
        """
        Convierte frame numpy a JPEG y almacena en caché.

        Args:
            camera_id: ID de la cámara
            frame_data: FrameData con frame numpy (BGR)
        """
        try:
            # Codificar frame a JPEG
            _, buffer = cv2.imencode(
                '.jpg', 
                frame_data.frame, 
                [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]
            )

            with self._lock:
                self._frames[camera_id] = buffer.tobytes()

        except Exception as e:
            self._logger.error(f"Error al codificar frame de cámara {camera_id}: {e}")

    def get_latest_frame(self, camera_id: int) -> bytes | None:
        """
        Obtiene el último frame JPEG de una cámara.

        Args:
            camera_id: ID de la cámara

        Returns:
            Bytes JPEG o None si no hay frame disponible
        """
        with self._lock:
            return self._frames.get(camera_id)

    def generate_stream(self, camera_id: int):
        """
        Generador infinito de frames MJPEG para HTTP streaming.

        Args:
            camera_id: ID de la cámara

        Yields:
            Chunks de bytes para multipart/x-mixed-replace
        """
        boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"

        while True:
            try:
                frame = self.get_latest_frame(camera_id)

                if frame:
                    yield boundary + frame + b"\r\n"
                else:
                    # Si no hay frame, esperar un poco antes de reintentar
                    time.sleep(0.1)

                # Limitar a ~30fps máximo
                time.sleep(0.033)

            except Exception as e:
                self._logger.error(f"Error en stream de cámara {camera_id}: {e}")
                time.sleep(0.5)


# Instancia global para uso en toda la aplicación
mjpeg_streamer = MJPEGStreamer()