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
    """Frame de video con metadata. stream_type identifica el lente para dual-lens."""
    pixmap: 'QPixmap'
    timestamp: float
    camera_id: int
    stream_type: str = "main"  # "main" | "l1" | "l2"


class MJPEGThread(QThread):
    """Thread dedicado para consumir stream MJPEG."""

    frame_ready = Signal(Frame)
    error_occurred = Signal(str)
    connection_lost = Signal()

    def __init__(self, camera_id: int, stream_url: str,
                 stream_type: str = "main", parent=None):
        super().__init__(parent)
        self.camera_id = camera_id
        self.stream_type = stream_type
        self.stream_url = stream_url
        self._running = False
        # Crear mutexes en el constructor del thread (todavía en thread del padre)
        self._mutex = QMutex()
        self._wait_condition = QWaitCondition()
        self._session: Optional[requests.Session] = None
        self._response: Optional[requests.Response] = None

        # PULL-BASED: el thread guarda SOLO el último IMAGE decodificado.
        # El receiver (CameraWidget) lo lee con un QTimer cada 67ms y ahí
        # lo convierte a QPixmap (en el GUI thread).
        #
        # CRÍTICO: usar QImage, NO QPixmap. QPixmap NO es thread-safe en
        # Qt: solo se puede crear/usar desde el GUI thread. Si se crea
        # en MJPEGThread (como antes), PySide6 marshalliza internamente
        # al GUI thread con una cola interna que crece con el tiempo →
        # lag acumulativo que se siente como "delay creciente". QImage
        # SÍ es thread-safe y la conversión QImage→QPixmap en el GUI
        # thread es trivial (<1 ms).
        self._latest_pixmap_mutex = QMutex()
        self._latest_image: Optional["QImage"] = None
        self._latest_pixmap_ts: float = 0.0
        self._latest_pixmap_seq: int = 0  # contador para detectar si llegó uno nuevo
    
    def run(self):
        """Loop principal de captura."""
        self._running = True
        logger.debug(f"[Cam {self.camera_id}] Stream thread iniciado")

        self._session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            max_retries=0,
            pool_connections=1,
            pool_maxsize=1
        )
        self._session.mount('http://', adapter)
        self._session.mount('https://', adapter)

        # SO_RCVBUF pequeño: el SO mantendrá poca cola de recepción, así que
        # si el GUI va lento, los frames excedentes se descartan a nivel TCP
        # (la ventana se cierra y el server espera). Esto evita ver frames
        # viejos acumulados cuando volvemos a procesar tras un freeze.
        # 128KB ≈ 1-2 frames JPEG; suficiente para no perder packets cuando
        # el GUI corre bien, pero limita la acumulación.
        try:
            import socket
            adapter.poolmanager.connection_pool_kw["socket_options"] = [
                (socket.SOL_SOCKET, socket.SO_RCVBUF, 128 * 1024),
                (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
            ]
        except Exception as e:
            logger.debug(f"No se pudieron configurar socket_options: {e}")

        retry_count = 0
        max_retries = 5  # antes era 3; muere muy rápido en LAN intermitente

        while self._running and retry_count < max_retries:
            # Reset por iteración para que cleanup pueda cerrar lo último
            self._response = None
            try:
                self._response = self._session.get(
                    self.stream_url,
                    stream=True,
                    timeout=(5, 30),
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

                    # Procesar frames JPEG con drop-newest: si en el buffer
                    # hay varios frames acumulados (porque hubo backlog en
                    # la red o el GUI iba detrás), parseamos todos pero solo
                    # decodificamos y emitimos el ÚLTIMO. Esto mantiene la
                    # latencia baja después de un freeze de FFmpeg.
                    last_jpg = None
                    while True:
                        start = buffer.find(b'\xff\xd8')
                        end = buffer.find(b'\xff\xd9')

                        if start != -1 and end != -1 and end > start:
                            last_jpg = buffer[start:end+2]
                            buffer = buffer[end+2:]
                        else:
                            break

                    if last_jpg is not None:
                        try:
                            from PySide6.QtGui import QImage
                            # SOLO QImage en el worker thread. QPixmap se
                            # crea en GUI thread (ver pop_latest_pixmap).
                            image = QImage.fromData(last_jpg)

                            if not image.isNull():
                                # PULL-BASED: guardar bajo mutex en lugar
                                # de emitir signal. Sobreescribe el anterior
                                # si aún no fue leído (drop-newest natural).
                                with QMutexLocker(self._latest_pixmap_mutex):
                                    self._latest_image = image
                                    self._latest_pixmap_ts = time.time()
                                    self._latest_pixmap_seq += 1
                        except Exception as e:
                            logger.debug(f"Error decodificando JPEG: {e}")
                            
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
                time.sleep(2)  # antes 3s; baja la sensación de "trabón"

        # Si salimos por agotar reintentos, avisar al UI explícitamente.
        # Antes el thread terminaba en silencio y el QLabel se quedaba con
        # "Esperando video..." indefinidamente.
        if self._running and retry_count >= max_retries:
            logger.error(
                f"[Cam {self.camera_id}/{self.stream_type}] máximo de "
                f"reintentos ({max_retries}) alcanzado"
            )
            self.error_occurred.emit("Sin conexión tras varios intentos")
            self.connection_lost.emit()

        # Cleanup garantizado al salir del thread (importante si salimos
        # por excepción sin pasar por stop()).
        try:
            if self._response is not None:
                self._response.close()
        except Exception:
            pass
        try:
            if self._session is not None:
                self._session.close()
        except Exception:
            pass

        logger.debug(f"[Cam {self.camera_id}] Stream thread finalizado")

    def pop_latest_pixmap(self, last_seq: int = -1):
        """
        Devuelve (pixmap, seq, age_ms) si hay un frame más nuevo que last_seq,
        o (None, last_seq, 0) si no hay nada nuevo.

        IMPORTANTE: ESTE MÉTODO DEBE LLAMARSE DESDE EL GUI THREAD.
        Lo llama el QTimer de live_view (que vive en el GUI thread), así que
        está OK. Aquí convertimos QImage→QPixmap porque QPixmap requiere
        GUI thread; el MJPEGThread solo guarda QImage (thread-safe).
        """
        from PySide6.QtGui import QPixmap

        # Sacar referencia a la QImage bajo lock (rápido), liberar lock,
        # y convertir a QPixmap fuera del lock (no bloquea al worker).
        with QMutexLocker(self._latest_pixmap_mutex):
            if self._latest_image is None or self._latest_pixmap_seq <= last_seq:
                return None, last_seq, 0
            img = self._latest_image
            seq = self._latest_pixmap_seq
            age_ms = int((time.time() - self._latest_pixmap_ts) * 1000)

        # Conversión en GUI thread (~1 ms). Esto es la línea CRÍTICA: hacerlo
        # aquí en vez de en MJPEGThread elimina el lag acumulativo causado
        # por el marshalling implícito de QPixmap entre hilos.
        pm = QPixmap.fromImage(img)
        if pm.isNull():
            return None, last_seq, 0
        return pm, seq, age_ms

    def stop(self):
        """
        Detiene el thread de forma segura.

        Cerrar sólo response.close() no siempre desbloquea iter_content
        si está dormido leyendo del socket — hay que cerrar el socket raw
        también para que la lectura tire OSError de inmediato.
        """
        with QMutexLocker(self._mutex):
            self._running = False

        if self._response is not None:
            try:
                # Cerrar el socket subyacente: esto sí garantiza que
                # iter_content rompa con error en milisegundos.
                raw = getattr(self._response, "raw", None)
                if raw is not None:
                    try:
                        raw.close()
                    except Exception:
                        pass
                self._response.close()
            except Exception:
                pass

        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass

        self._wait_condition.wakeAll()

        # Esperar a que termine (máximo 2 segundos — antes 3s)
        if not self.wait(2000):
            logger.warning(f"[Cam {self.camera_id}] Forzando terminación del thread")
            self.terminate()
            self.wait(500)


class VideoStreamerService(QObject):
    """
    Servicio que gestiona múltiples streams de video.

    Las claves del diccionario interno son tuplas (camera_id, stream_type)
    para soportar cámaras dual-lens (l1/l2 como streams independientes).
    """

    frame_updated = Signal(Frame)
    camera_error = Signal(int, str)

    def __init__(self):
        super().__init__()
        self._streams: dict[tuple[int, str], MJPEGThread] = {}
        self._base_url = None
        self._mutex = QMutex()

    def set_base_url(self, base_url: str):
        """Establece URL base del backend."""
        self._base_url = base_url

    def start_stream(self, camera_id: int, token: str,
                     stream_type: str = "main"):
        """
        Inicia streaming de una cámara.

        Args:
            camera_id: ID de la cámara
            token: JWT access_token
            stream_type: "main" para cámara mono, "l1"/"l2" para dual-lens
        """
        key = (camera_id, stream_type)
        with QMutexLocker(self._mutex):
            if key in self._streams:
                old_thread = self._streams[key]
                if old_thread.isRunning():
                    logger.debug(f"[Cam {camera_id}/{stream_type}] Stream ya activo")
                    return
                old_thread.deleteLater()
                del self._streams[key]

            if not self._base_url:
                self.camera_error.emit(camera_id, "API no configurada")
                return

            # URL con type y token vía query string (compatible con el endpoint
            # /api/v1/cameras/<id>/stream?type=main|l1|l2&token=...)
            stream_url = (
                f"{self._base_url}/cameras/{camera_id}/stream"
                f"?type={stream_type}&token={token}"
            )

            thread = MJPEGThread(camera_id, stream_url,
                                 stream_type=stream_type, parent=self)
            thread.frame_ready.connect(self._on_frame, type=Qt.QueuedConnection)
            thread.error_occurred.connect(
                lambda e, cid=camera_id: self.camera_error.emit(cid, e)
            )
            thread.connection_lost.connect(
                lambda cid=camera_id: self.camera_error.emit(cid, "Conexión perdida")
            )

            self._streams[key] = thread
            thread.start()
            logger.debug(f"[Cam {camera_id}/{stream_type}] Stream iniciado")

    def stop_stream(self, camera_id: int, stream_type: str = "main"):
        """Detiene streaming específico (por lente si dual)."""
        key = (camera_id, stream_type)
        with QMutexLocker(self._mutex):
            if key in self._streams:
                thread = self._streams.pop(key)
                thread.stop()
                thread.deleteLater()
                logger.debug(f"[Cam {camera_id}/{stream_type}] Stream detenido")

    def stop_camera(self, camera_id: int):
        """Detiene TODOS los streams de una cámara (main + l1 + l2 si aplica)."""
        with QMutexLocker(self._mutex):
            keys = [k for k in self._streams.keys() if k[0] == camera_id]
        for k in keys:
            self.stop_stream(k[0], k[1])

    def stop_all(self):
        """Detiene todos los streams."""
        with QMutexLocker(self._mutex):
            keys = list(self._streams.keys())
        for cam_id, stream_type in keys:
            self.stop_stream(cam_id, stream_type)

    def _on_frame(self, frame: Frame):
        """Reenvía frame."""
        self.frame_updated.emit(frame)

    def is_streaming(self, camera_id: int, stream_type: str = "main") -> bool:
        """Verifica si está streameando."""
        key = (camera_id, stream_type)
        with QMutexLocker(self._mutex):
            return (key in self._streams and self._streams[key].isRunning())

    def pop_latest_pixmap(self, camera_id: int, stream_type: str = "main",
                           last_seq: int = -1):
        """
        Pull-based API para CameraWidget. Devuelve (pixmap, seq, age_ms)
        del thread (camera_id, stream_type) o (None, last_seq, 0) si no hay
        thread activo o no hay frame nuevo.
        """
        key = (camera_id, stream_type)
        with QMutexLocker(self._mutex):
            thread = self._streams.get(key)
        if thread is None or not thread.isRunning():
            return None, last_seq, 0
        return thread.pop_latest_pixmap(last_seq)


# Instancia global
video_streamer = VideoStreamerService()