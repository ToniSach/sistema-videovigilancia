"""
Servicio de streaming MJPEG para cámaras en vivo.
"""
import logging
import time
from typing import Optional, Callable
from dataclasses import dataclass

import requests
from PySide6.QtCore import QObject, Signal, QThread, QMutex, QMutexLocker
from PySide6.QtGui import QImage, QPixmap

logger = logging.getLogger(__name__)


@dataclass
class Frame:
    """Frame de video con metadata."""
    pixmap: QPixmap
    timestamp: float
    camera_id: int


class MJPEGThread(QThread):
    """Thread dedicado para consumir stream MJPEG."""
    
    frame_ready = Signal(Frame)
    error_occurred = Signal(str)
    connection_lost = Signal()
    
    def __init__(self, camera_id: int, stream_url: str, parent=None):
        super().__init__(parent)
        self.camera_id = camera_id
        self.stream_url = stream_url
        self._running = False
        self._mutex = QMutex()
        self.session = requests.Session()
        self.session.timeout = 10
    
    def run(self):
        """Loop principal de captura."""
        self._running = True
        logger.info(f"Stream iniciado para cámara {self.camera_id}")
        
        while self._running:
            try:
                response = self.session.get(self.stream_url, stream=True, timeout=5)
                if response.status_code != 200:
                    self.error_occurred.emit(f"HTTP {response.status_code}")
                    time.sleep(2)
                    continue
                
                buffer = b''
                for chunk in response.iter_content(chunk_size=1024):
                    if not self._running:
                        break
                    
                    buffer += chunk
                    
                    # Buscar JPEG en buffer (formato MJPEG)
                    start = buffer.find(b'\xff\xd8')  # JPEG SOI
                    end = buffer.find(b'\xff\xd9')    # JPEG EOI
                    
                    if start != -1 and end != -1 and end > start:
                        jpg = buffer[start:end+2]
                        buffer = buffer[end+2:]
                        
                        # Convertir a QPixmap
                        image = QImage.fromData(jpg)
                        if not image.isNull():
                            pixmap = QPixmap.fromImage(image)
                            frame = Frame(
                                pixmap=pixmap,
                                timestamp=time.time(),
                                camera_id=self.camera_id
                            )
                            self.frame_ready.emit(frame)
                
            except requests.RequestException as e:
                if self._running:
                    logger.warning(f"Error de conexión cámara {self.camera_id}: {e}")
                    self.connection_lost.emit()
                    time.sleep(3)
            except Exception as e:
                if self._running:
                    logger.error(f"Error en stream cámara {self.camera_id}: {e}")
                    time.sleep(1)
        
        logger.info(f"Stream detenido para cámara {self.camera_id}")
    
    def stop(self):
        """Detiene el thread de forma segura."""
        with QMutexLocker(self._mutex):
            self._running = False
        self.wait(1000)  # Esperar max 1 segundo
        self.session.close()


class VideoStreamerService(QObject):
    """Servicio que gestiona múltiples streams de video."""
    
    frame_updated = Signal(Frame)
    camera_error = Signal(int, str)  # camera_id, error
    
    def __init__(self):
        super().__init__()
        self._streams: dict[int, MJPEGThread] = {}
        self._base_url = None  # Se establece después del login
    
    def set_base_url(self, base_url: str):
        """Establece URL base del backend (incluyendo token)."""
        self._base_url = base_url
    
    def start_stream(self, camera_id: int, token: str):
        """Inicia streaming de una cámara."""
        if camera_id in self._streams:
            return  # Ya está corriendo
        
        if not self._base_url:
            self.camera_error.emit(camera_id, "API no configurada")
            return
        
        stream_url = f"{self._base_url}/cameras/{camera_id}/stream?token={token}"
        
        thread = MJPEGThread(camera_id, stream_url)
        thread.frame_ready.connect(self._on_frame)
        thread.error_occurred.connect(lambda e: self.camera_error.emit(camera_id, e))
        thread.connection_lost.connect(lambda: self.camera_error.emit(camera_id, "Conexión perdida"))
        
        self._streams[camera_id] = thread
        thread.start()
        logger.info(f"Stream iniciado para cámara {camera_id}")
    
    def stop_stream(self, camera_id: int):
        """Detiene streaming de una cámara."""
        if camera_id in self._streams:
            self._streams[camera_id].stop()
            del self._streams[camera_id]
            logger.info(f"Stream detenido para cámara {camera_id}")
    
    def stop_all(self):
        """Detiene todos los streams."""
        for camera_id in list(self._streams.keys()):
            self.stop_stream(camera_id)
    
    def _on_frame(self, frame: Frame):
        """Reenvía frame a quienes escuchan."""
        self.frame_updated.emit(frame)
    
    def is_streaming(self, camera_id: int) -> bool:
        """Verifica si una cámara está stremeando."""
        return camera_id in self._streams and self._streams[camera_id].isRunning()


# Instancia global
video_streamer = VideoStreamerService()