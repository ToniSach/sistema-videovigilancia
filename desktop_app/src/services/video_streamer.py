"""
Servicio de streaming MJPEG para cámaras en vivo.
"""
import logging
import time
from typing import Optional
from dataclasses import dataclass

import requests
from PySide6.QtCore import QObject, Signal, QThread, QMutex, QMutexLocker, QWaitCondition, Qt

logger = logging.getLogger(__name__)


@dataclass
class Frame:
    """Frame de video con metadata."""
    pixmap: 'QPixmap'
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
        # Crear mutexes en el constructor del thread (todavía en thread del padre)
        self._mutex = QMutex()
        self._wait_condition = QWaitCondition()
        self._session: Optional[requests.Session] = None
        self._response: Optional[requests.Response] = None
    
    def run(self):
        """Loop principal de captura."""
        self._running = True
        logger.info(f"[Cam {self.camera_id}] Stream thread iniciado")
        
        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            max_retries=0,
            pool_connections=1,
            pool_maxsize=1
        )
        self._session.mount('http://', adapter)
        self._session.mount('https://', adapter)
        
        retry_count = 0
        max_retries = 3
        
        while self._running and retry_count < max_retries:
            try:
                self._response = self._session.get(
                    self.stream_url, 
                    stream=True, 
                    timeout=(5, 30),  # Aumentado timeout de lectura
                    headers={'Connection': 'close'}
                )
                
                if self._response.status_code != 200:
                    self.error_occurred.emit(f"HTTP {self._response.status_code}")
                    retry_count += 1
                    time.sleep(2)
                    continue
                
                retry_count = 0
                buffer = b''
                consecutive_empty = 0
                
                for chunk in self._response.iter_content(chunk_size=8192):
                    if not self._running:
                        break
                    
                    if not chunk:
                        consecutive_empty += 1
                        if consecutive_empty > 10:  # Demasiados chunks vacíos
                            break
                        continue
                    else:
                        consecutive_empty = 0
                        
                    buffer += chunk
                    
                    # Procesar frames JPEG
                    while True:
                        start = buffer.find(b'\xff\xd8')
                        end = buffer.find(b'\xff\xd9')
                        
                        if start != -1 and end != -1 and end > start:
                            jpg = buffer[start:end+2]
                            buffer = buffer[end+2:]
                            
                            try:
                                from PySide6.QtGui import QImage, QPixmap
                                image = QImage.fromData(jpg)
                                
                                if not image.isNull():
                                    pixmap = QPixmap.fromImage(image)
                                    if not pixmap.isNull():
                                        frame = Frame(
                                            pixmap=pixmap,
                                            timestamp=time.time(),
                                            camera_id=self.camera_id
                                        )
                                        self.frame_ready.emit(frame)
                            except Exception as e:
                                logger.debug(f"Error decodificando JPEG: {e}")
                        else:
                            break
                            
                    # Limitar buffer
                    if len(buffer) > 2 * 1024 * 1024:  # 2MB max
                        buffer = buffer[-65536:]
                        
            except requests.exceptions.Timeout:
                logger.warning(f"[Cam {self.camera_id}] Timeout")
                retry_count += 1
                self.error_occurred.emit("Timeout de conexión")
            except requests.exceptions.RequestException as e:
                if self._running:
                    logger.warning(f"[Cam {self.camera_id}] Error conexión: {e}")
                    self.connection_lost.emit()
                    retry_count += 1
            except Exception as e:
                if self._running:
                    logger.error(f"[Cam {self.camera_id}] Error: {e}")
                    retry_count += 1
            
            if self._running and retry_count < max_retries:
                time.sleep(3)
        
        logger.info(f"[Cam {self.camera_id}] Stream thread finalizado")
    
    def stop(self):
        """Detiene el thread de forma segura."""
        with QMutexLocker(self._mutex):
            self._running = False
        
        # Cerrar response para desbloquear iter_content
        if self._response:
            try:
                self._response.close()
            except:
                pass
        
        if self._session:
            try:
                self._session.close()
            except:
                pass
        
        self._wait_condition.wakeAll()
        # Esperar a que termine (máximo 3 segundos)
        if not self.wait(3000):
            logger.warning(f"[Cam {self.camera_id}] Forzando terminación del thread")
            self.terminate()
            self.wait(1000)


class VideoStreamerService(QObject):
    """Servicio que gestiona múltiples streams de video."""
    
    frame_updated = Signal(Frame)
    camera_error = Signal(int, str)
    
    def __init__(self):
        super().__init__()
        self._streams: dict[int, MJPEGThread] = {}
        self._base_url = None
        self._mutex = QMutex()  # Mutex para proteger el diccionario
    
    def set_base_url(self, base_url: str):
        """Establece URL base del backend."""
        self._base_url = base_url
    
    def start_stream(self, camera_id: int, token: str):
        """Inicia streaming de una cámara."""
        with QMutexLocker(self._mutex):
            # Si ya existe, verificar si está corriendo
            if camera_id in self._streams:
                old_thread = self._streams[camera_id]
                if old_thread.isRunning():
                    logger.info(f"[Cam {self.camera_id}] Stream ya activo, ignorando solicitud")
                    return
                else:
                    # Limpiar thread muerto
                    old_thread.deleteLater()
                    del self._streams[camera_id]
            
            if not self._base_url:
                self.camera_error.emit(camera_id, "API no configurada")
                return
            
            stream_url = f"{self._base_url}/cameras/{camera_id}/stream?token={token}"
            
            thread = MJPEGThread(camera_id, stream_url, self)  # Parent es self
            thread.frame_ready.connect(self._on_frame, type=Qt.QueuedConnection)
            thread.error_occurred.connect(lambda e: self.camera_error.emit(camera_id, e))
            thread.connection_lost.connect(lambda: self.camera_error.emit(camera_id, "Conexión perdida"))
            
            self._streams[camera_id] = thread
            thread.start()
            logger.info(f"[Cam {camera_id}] Stream iniciado")
    
    def stop_stream(self, camera_id: int):
        """Detiene streaming específico."""
        with QMutexLocker(self._mutex):
            if camera_id in self._streams:
                thread = self._streams.pop(camera_id)
                thread.stop()
                thread.deleteLater()  # Importante para Qt
                logger.info(f"[Cam {camera_id}] Stream detenido")
    
    def stop_all(self):
        """Detiene todos los streams."""
        with QMutexLocker(self._mutex):
            camera_ids = list(self._streams.keys())
        
        for cam_id in camera_ids:
            self.stop_stream(cam_id)
    
    def _on_frame(self, frame: Frame):
        """Reenvía frame."""
        self.frame_updated.emit(frame)
    
    def is_streaming(self, camera_id: int) -> bool:
        """Verifica si está stremeando."""
        with QMutexLocker(self._mutex):
            return (camera_id in self._streams and 
                    self._streams[camera_id].isRunning())


# Instancia global
video_streamer = VideoStreamerService()