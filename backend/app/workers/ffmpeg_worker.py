import subprocess
import threading
import numpy as np
import logging
import time
import os
from enum import Enum

from ..database.models import Camera
from ..streaming.frame_buffer import CircularFrameBuffer


class WorkerStatus(Enum):
    """Estados posibles del worker FFmpeg."""
    STARTING = "starting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"
    ERROR = "error"


class FFmpegWorker:
    """
    Worker que ejecuta FFmpeg para capturar stream RTSP y alimentar el frame buffer.
    Implementa reconexión automática y watchdog de frames.
    """

    def __init__(self, camera: Camera, frame_buffer: CircularFrameBuffer):
        """
        Inicializa el worker FFmpeg.

        Args:
            camera: Instancia del modelo Camera con configuración
            frame_buffer: Buffer donde depositar los frames capturados
        """
        self.camera = camera
        self.frame_buffer = frame_buffer
        self.status = WorkerStatus.STARTING
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._reconnect_attempts = 0
        self.MAX_RECONNECT = 10
        self.WATCHDOG_TIMEOUT = 30  # Segundos sin frame antes de reiniciar
        self._last_frame_time = time.time()
        self._logger = logging.getLogger(f"{__name__}.Cam{camera.id}")

        # Calcular tamaño de frame esperado
        self._frame_size = camera.resolution_width * camera.resolution_height * 3  # BGR24 = 3 bytes/pixel

    def _build_ffmpeg_command(self) -> list[str]:
        """
        Construye el comando FFmpeg para capturar el stream.

        Returns:
            Lista de argumentos para subprocess
        """
        return [
            "ffmpeg",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",  # TCP para mayor estabilidad que UDP
            "-i", self.camera.rtsp_url,
            "-vf", f"fps={self.camera.fps}",  # Forzar FPS objetivo
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",  # Formato compatible con OpenCV/numpy
            "pipe:1"  # Salida a stdout
        ]

    def start(self) -> None:
        """Inicia el worker en thread separado."""
        if self._running:
            return

        self._running = True
        self.status = WorkerStatus.STARTING
        self._thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"FFmpeg-Cam{self.camera.id}"
        )
        self._thread.start()
        self._logger.info(f"Worker iniciado para cámara {self.camera.id}")

    def _worker_loop(self) -> None:
        """
        Loop principal del worker: mantiene FFmpeg corriendo con reconexión.
        """
        while self._running and self._reconnect_attempts < self.MAX_RECONNECT:
            try:
                self._start_ffmpeg()

                # Si FFmpeg termina limpiamente y no estamos deteniendo, reconectar
                if self._running:
                    self.status = WorkerStatus.RECONNECTING
                    self._reconnect_attempts += 1
                    self._logger.warning(
                        f"FFmpeg terminó, reintento {self._reconnect_attempts}/{self.MAX_RECONNECT}"
                    )
                    time.sleep(5)
                else:
                    break

            except Exception as e:
                self._logger.error(f"Error en worker loop: {e}")
                self._reconnect_attempts += 1
                if self._reconnect_attempts < self.MAX_RECONNECT:
                    time.sleep(5)

        # Si agotamos intentos
        if self._reconnect_attempts >= self.MAX_RECONNECT:
            self.status = WorkerStatus.ERROR
            self._logger.error("Máximos reintentos alcanzados, cámara offline")
            # Aquí se podría emitir evento camera_offline
        else:
            self.status = WorkerStatus.STOPPED

    def _start_ffmpeg(self) -> None:
        """
        Inicia el proceso FFmpeg y lee frames desde stdout.
        """
        cmd = self._build_ffmpeg_command()
        height = self.camera.resolution_height
        width = self.camera.resolution_width

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=self._frame_size * 2  # Buffer para 2 frames
            )

            self.status = WorkerStatus.RUNNING
            self._reconnect_attempts = 0
            self._last_frame_time = time.time()

            self._logger.info(f"FFmpeg iniciado para {self.camera.rtsp_url}")

            # Loop de lectura de frames
            while self._running:
                # Leer exactamente un frame completo
                raw = self._process.stdout.read(self._frame_size)

                if len(raw) < self._frame_size:
                    # FFmpeg cerró o error
                    if self._process.poll() is not None:
                        self._logger.warning("FFmpeg terminó inesperadamente")
                        break
                    continue

                # Convertir bytes a numpy array
                frame = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))

                # Entregar al buffer
                self.frame_buffer.put(frame)
                self._last_frame_time = time.time()

                # Watchdog: si no hay frames por mucho tiempo, reiniciar
                if time.time() - self._last_frame_time > self.WATCHDOG_TIMEOUT:
                    self._logger.error("Watchdog timeout - no hay frames nuevos")
                    break

        except Exception as e:
            self._logger.error(f"Error en FFmpeg: {e}")
            raise
        finally:
            if self._process:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except:
                    self._process.kill()
                self._process = None

    def stop(self) -> None:
        """Detiene el worker FFmpeg de manera ordenada."""
        self._running = False
        self.status = WorkerStatus.STOPPED

        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except:
                self._process.kill()
            finally:
                self._process = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

        self._logger.info(f"Worker detenido para cámara {self.camera.id}")

    def get_status(self) -> dict:
        """
        Retorna estado actual del worker.

        Returns:
            Diccionario con información de estado
        """
        return {
            "camera_id": self.camera.id,
            "status": self.status.value,
            "reconnect_attempts": self._reconnect_attempts,
            "last_frame_time": self._last_frame_time,
            "seconds_since_last_frame": time.time() - self._last_frame_time if self._last_frame_time else None
        }