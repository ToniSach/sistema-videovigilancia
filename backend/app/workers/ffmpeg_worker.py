import subprocess
import threading
import numpy as np
import logging
import time
import os
import shutil
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
    FFMPEG_NOT_FOUND = "ffmpeg_not_found"


class FFmpegWorker:
    """
    Worker que ejecuta FFmpeg para capturar stream RTSP.
    """

    def __init__(self, camera: Camera, frame_buffer: CircularFrameBuffer):
        self.camera = camera
        self.frame_buffer = frame_buffer
        self.status = WorkerStatus.STARTING
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._reconnect_attempts = 0
        self.MAX_RECONNECT = 10
        self.WATCHDOG_TIMEOUT = 30
        self._last_frame_time = time.time()
        self._logger = logging.getLogger(f"{__name__}.Cam{camera.id}")
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_running = False
        
        # Calcular tamaño de frame
        self._frame_size = camera.resolution_width * camera.resolution_height * 3
        
        # Verificar FFmpeg al inicio
        self._ffmpeg_path = self._find_ffmpeg_executable()
        if not self._ffmpeg_path:
            self.status = WorkerStatus.FFMPEG_NOT_FOUND
            self._logger.error("FFmpeg no encontrado. Instale FFmpeg y añádalo al PATH del sistema.")

    def _find_ffmpeg_executable(self) -> str | None:
        """Busca el ejecutable de FFmpeg."""
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            return ffmpeg_path
            
        common_windows_paths = [
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Users\%USERNAME%\ffmpeg\bin\ffmpeg.exe",
            r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
            r"C:\ProgramData\scoop\shims\ffmpeg.exe",
        ]
        
        for path in common_windows_paths:
            expanded_path = os.path.expandvars(path)
            if os.path.exists(expanded_path):
                self._logger.info(f"FFmpeg encontrado en: {expanded_path}")
                return expanded_path
                
        return None

    def _build_ffmpeg_command(self) -> list[str]:
        """Construye el comando FFmpeg."""
        if not self._ffmpeg_path:
            raise RuntimeError("FFmpeg no disponible")
            
        return [
            self._ffmpeg_path,
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", self.camera.rtsp_url,
            "-vf", f"fps={self.camera.fps}",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "pipe:1"
        ]

    def start(self) -> None:
        """Inicia el worker."""
        if self._running:
            return
            
        if not self._ffmpeg_path:
            self._logger.error("No se puede iniciar cámara: FFmpeg no está instalado")
            self.status = WorkerStatus.FFMPEG_NOT_FOUND
            return

        self._running = True
        self.status = WorkerStatus.STARTING
        self._thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"FFmpeg-Cam{self.camera.id}"
        )
        self._thread.start()
        
        # CORRECCIÓN: Iniciar watchdog en thread separado
        self._watchdog_running = True
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name=f"Watchdog-Cam{self.camera.id}"
        )
        self._watchdog_thread.start()
        
        self._logger.info(f"Worker iniciado para cámara {self.camera.id}")

    def _watchdog_loop(self) -> None:
        """
        CORRECCIÓN: Watchdog en thread separado que monitorea la actividad.
        Detecta bloqueos donde FFmpeg no cierra pero deja de producir frames.
        """
        while self._watchdog_running:
            try:
                time.sleep(5)  # Verificar cada 5 segundos
                
                if not self._running:
                    break
                
                # Si han pasado más de WATCHDOG_TIMEOUT segundos sin frames
                time_since_last = time.time() - self._last_frame_time
                if time_since_last > self.WATCHDOG_TIMEOUT:
                    self._logger.error(f"Watchdog timeout: {time_since_last:.1f}s sin frames. Matando FFmpeg...")
                    
                    # Forzar terminación del proceso FFmpeg bloqueado
                    if self._process and self._process.poll() is None:
                        try:
                            self._process.kill()
                            self._process.wait(timeout=2)
                        except:
                            pass
                    
                    # Actualizar estado para forzar reconnect
                    self.status = WorkerStatus.RECONNECTING
                    break
                    
            except Exception as e:
                self._logger.error(f"Error en watchdog: {e}")
                time.sleep(1)

    def _worker_loop(self) -> None:
        """Loop principal del worker."""
        while self._running and self._reconnect_attempts < self.MAX_RECONNECT:
            try:
                self._start_ffmpeg()

                if self._running:
                    self.status = WorkerStatus.RECONNECTING
                    self._reconnect_attempts += 1
                    self._logger.warning(f"Reintento {self._reconnect_attempts}/{self.MAX_RECONNECT}")
                    time.sleep(5)
                else:
                    break

            except FileNotFoundError as e:
                self._logger.error(f"FFmpeg no encontrado: {e}")
                self.status = WorkerStatus.FFMPEG_NOT_FOUND
                break
            except Exception as e:
                self._logger.error(f"Error en worker loop: {e}")
                self._reconnect_attempts += 1
                if self._reconnect_attempts < self.MAX_RECONNECT:
                    time.sleep(5)

        if self._reconnect_attempts >= self.MAX_RECONNECT:
            self.status = WorkerStatus.ERROR
            self._logger.error("Máximos reintentos alcanzados")

    def _start_ffmpeg(self) -> None:
        """Inicia el proceso FFmpeg."""
        cmd = self._build_ffmpeg_command()
        height = self.camera.resolution_height
        width = self.camera.resolution_width

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=self._frame_size * 2
            )

            self.status = WorkerStatus.RUNNING
            self._reconnect_attempts = 0
            self._last_frame_time = time.time()

            self._logger.info(f"FFmpeg iniciado para {self.camera.rtsp_url}")

            while self._running:
                raw = self._process.stdout.read(self._frame_size)

                if len(raw) < self._frame_size:
                    if self._process.poll() is not None:
                        stderr = self._process.stderr.read().decode('utf-8', errors='ignore')
                        if stderr:
                            self._logger.error(f"FFmpeg error: {stderr}")
                        break
                    continue

                frame = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
                self.frame_buffer.put(frame)
                self._last_frame_time = time.time()

        except FileNotFoundError:
            self._logger.error("FFmpeg executable no encontrado")
            raise
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
        """Detiene el worker y el watchdog."""
        self._running = False
        self._watchdog_running = False
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
            
        # CORRECCIÓN: Esperar al watchdog también
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=2)

        self._logger.info(f"Worker detenido para cámara {self.camera.id}")

    def get_status(self) -> dict:
        """Retorna estado actual."""
        return {
            "camera_id": self.camera.id,
            "status": self.status.value,
            "reconnect_attempts": self._reconnect_attempts,
            "last_frame_time": self._last_frame_time,
            "seconds_since_last_frame": time.time() - self._last_frame_time if self._last_frame_time else None,
            "ffmpeg_found": self._ffmpeg_path is not None,
            "ffmpeg_path": self._ffmpeg_path
        }