import cv2
import numpy as np
import threading
import time
import logging
from typing import Dict, Optional

from .frame_buffer import FrameData


class MJPEGStreamer:
    """
    Convierte frames numpy a JPEG y genera streams MJPEG para HTTP.
    Implementa limpieza automática de cámaras inactivas.
    """

    def __init__(self, camera_timeout: float = 30.0):
        self._frames: Dict[int, bytes] = {}
        self._timestamps: Dict[int, float] = {}
        self._lock = threading.Lock()
        self._jpeg_quality = 60  # Optimizado para velocidad
        self._logger = logging.getLogger(__name__)
        self._camera_timeout = camera_timeout
        
        # Iniciar thread de limpieza
        self._cleanup_running = True
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def update_frame(self, camera_id: int, frame_data: FrameData) -> None:
        """Convierte frame numpy a JPEG con calidad reducida para velocidad."""
        try:
            # Reducir calidad para menor ancho de banda (60 en lugar de 75)
            _, buffer = cv2.imencode(
                '.jpg', 
                frame_data.frame, 
                [cv2.IMWRITE_JPEG_QUALITY, 60]  # Menor calidad = menor tamaño = menos lag
            )

            with self._lock:
                self._frames[camera_id] = buffer.tobytes()
                self._timestamps[camera_id] = time.time()

        except Exception as e:
            self._logger.error(f"Error codificando frame: {e}")

    def get_latest_frame(self, camera_id: int) -> Optional[bytes]:
        """
        Obtiene el último frame JPEG de una cámara si está activa.
        """
        with self._lock:
            last_update = self._timestamps.get(camera_id, 0)
            if time.time() - last_update > self._camera_timeout:
                return None  # Cámara inactiva
            return self._frames.get(camera_id)

    def unregister_camera(self, camera_id: int) -> None:
        """Limpia explícitamente los recursos de una cámara."""
        with self._lock:
            self._frames.pop(camera_id, None)
            self._timestamps.pop(camera_id, None)
            self._logger.info(f"Cámara {camera_id} eliminada de MJPEG streamer")

    def _cleanup_loop(self) -> None:
        """Elimina cámaras inactivas periódicamente."""
        while self._cleanup_running:
            try:
                time.sleep(60)
                current_time = time.time()
                
                with self._lock:
                    inactive = [
                        cam_id for cam_id, ts in self._timestamps.items()
                        if current_time - ts > self._camera_timeout
                    ]
                    for cam_id in inactive:
                        del self._frames[cam_id]
                        del self._timestamps[cam_id]
                        self._logger.warning(f"Cámara {cam_id} eliminada por inactividad (timeout)")
            except Exception as e:
                self._logger.error(f"Error en cleanup loop: {e}")

    def generate_stream(self, camera_id: int):
        """
        Generador infinito de frames MJPEG para HTTP streaming.
        """
        boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
        last_frame_time = time.time()

        while True:
            try:
                frame = self.get_latest_frame(camera_id)
                current_time = time.time()

                if frame:
                    yield boundary + frame + b"\r\n"
                    last_frame_time = current_time
                else:
                    if current_time - last_frame_time > 5:
                        time.sleep(0.5)
                    else:
                        time.sleep(0.033)

            except GeneratorExit:
                break
            except Exception as e:
                self._logger.error(f"Error en stream de cámara {camera_id}: {e}")
                time.sleep(0.5)

    def shutdown(self):
        """Limpia recursos al cerrar la aplicación."""
        self._cleanup_running = False
        with self._lock:
            self._frames.clear()
            self._timestamps.clear()


# Instancia global
mjpeg_streamer = MJPEGStreamer()