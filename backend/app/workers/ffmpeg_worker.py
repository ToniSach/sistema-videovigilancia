import subprocess
import threading
import select
import numpy as np
import logging
import time
import os
import shutil
import re
from enum import Enum
from typing import Optional, Tuple
import cv2

from ..database.models import Camera
from ..streaming.frame_buffer import CircularFrameBuffer


class WorkerStatus(Enum):
    STARTING = "starting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"
    ERROR = "error"
    FFMPEG_NOT_FOUND = "ffmpeg_not_found"


class FFmpegWorker:
    ERROR_PATTERNS = {
        r"authentication failed": ("AUTH_FAILED", True),
        r"connection refused": ("CONN_REFUSED", True),
        r"invalid data found": ("INVALID_DATA", True),
        r"timeout": ("TIMEOUT", True),
        r"404 not found": ("NOT_FOUND", False),
        r"401 unauthorized": ("UNAUTHORIZED", False),
        r"could not find codec": ("CODEC_ERROR", False),
        r"protocol not found": ("PROTOCOL_ERROR", False),
        r"invalid argument": ("INVALID_ARG", False),
        r"memory allocation error": ("MEMORY_ERROR", False),
    }

    # Detectado una sola vez a nivel de clase para no repetirlo en cada método
    IS_WINDOWS = os.name == "nt"

    def __init__(self, camera: Camera, frame_buffer: CircularFrameBuffer,
                 rtsp_transport: str = "tcp"):
        self.camera_id = camera.id
        self.rtsp_url = camera.rtsp_url
        #self.resolution_width = camera.resolution_width
        #self.resolution_height = camera.resolution_height
        self.resolution_width = 1280
        self.resolution_height = 1440
        self._frame_size = 1280 * 1440 * 3
        self.fps = camera.fps
        self.camera_name = camera.name
        self.rtsp_transport = rtsp_transport

        self.frame_buffer = frame_buffer
        self.status = WorkerStatus.STARTING
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._reconnect_attempts = 0
        self.MAX_RECONNECT = 10
        self.WATCHDOG_TIMEOUT = 30
        self._last_frame_time = time.time()
        self._state_lock = threading.Lock()
        self._logger = logging.getLogger(f"{__name__}.Cam{self.camera_id}")
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_running = False

        from backend.app.config import settings
        #self._frame_size = settings.FFMPEG_RESOLUTION_WIDTH * settings.FFMPEG_RESOLUTION_HEIGHT * 3
        self.resolution_width = settings.FFMPEG_RESOLUTION_WIDTH
        self.resolution_height = settings.FFMPEG_RESOLUTION_HEIGHT

        self._stderr_thread: Optional[threading.Thread] = None

        self._frames_processed = 0
        self._frames_sent = 0
        self._last_error_code: Optional[str] = None
        self._permanent_failure_callback = None

        self._ffmpeg_path = self._find_ffmpeg_executable()
        if not self._ffmpeg_path:
            self.status = WorkerStatus.FFMPEG_NOT_FOUND
            self._logger.error("FFmpeg no encontrado")

    # -------------------------------------------------------------------------
    # Utilidades internas
    # -------------------------------------------------------------------------

    def _find_ffmpeg_executable(self) -> Optional[str]:
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            return ffmpeg_path

        common_paths = [
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
        ]
        for path in common_paths:
            if os.path.exists(path):
                self._logger.info(f"FFmpeg encontrado en: {path}")
                return path

        return None

    def set_permanent_failure_callback(self, callback):
        """Registra callback cuando ocurre error irrecuperable (auth, red, etc.)"""
        self._permanent_failure_callback = callback

    def _build_ffmpeg_command(self) -> list[str]:
        if not self._ffmpeg_path:
            raise RuntimeError("FFmpeg no disponible")

        from backend.app.config import settings

        target_width = settings.FFMPEG_RESOLUTION_WIDTH
        target_height = settings.FFMPEG_RESOLUTION_HEIGHT
        target_fps = settings.FFMPEG_FPS

        self._frame_size = target_width * target_height * 3
        self.resolution_width = target_width
        self.resolution_height = target_height

        #vf_filter = f"fps={target_fps},scale={target_width}:{target_height}"
        vf_filter = f"scale={target_width}:{target_height}"

        cmd = [
            self._ffmpeg_path,
            "-hide_banner",
            "-loglevel", "error",
            "-rtsp_transport", self.rtsp_transport,
            "-timeout", "5000000",
            "-fflags", "nobuffer+discardcorrupt",
            "-flags", "low_delay",
            "-avioflags", "direct",
            "-i", self.rtsp_url,
            "-vf", vf_filter,
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-frame_drop_threshold", "5",
            "pipe:1",
        ]

        self._logger.info(
            f"FFmpeg Config: {target_width}x{target_height}@{target_fps}fps, "
            f"frame_bytes={self._frame_size}"
        )
        return cmd

    def _parse_error(self, line: str) -> Tuple[Optional[str], bool]:
        for pattern, (code, retryable) in self.ERROR_PATTERNS.items():
            if re.search(pattern, line, re.IGNORECASE):
                return code, retryable
        return None, True

    def _read_exact(self, pipe, size: int) -> Optional[bytes]:
        """Lee exactamente `size` bytes del pipe. Retorna None si el pipe se cierra."""
        data = bytearray()
        while len(data) < size:
            chunk = pipe.read(size - len(data))
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    def _flush_pipe_buffer(self) -> None:
        """
        Vacía el backlog acumulado en el pipe del OS durante el arranque de FFmpeg.
        Compatible con Windows y Linux/Mac. No usa imports locales — todo está
        importado a nivel de módulo.
        """
        flushed = 0

        if self.IS_WINDOWS:
            # En Windows, select() no funciona con pipes anónimos.
            # Usamos PeekNamedPipe de pywin32 si está disponible.
            try:
                import msvcrt
                import win32pipe  # parte de pywin32

                fd = self._process.stdout.fileno()
                handle = msvcrt.get_osfhandle(fd)

                while True:
                    available, _, _ = win32pipe.PeekNamedPipe(handle, 0)
                    if available == 0:
                        break
                    to_read = min(available, 65536)
                    chunk = os.read(fd, to_read)
                    if not chunk:
                        break
                    flushed += len(chunk)

                if flushed:
                    self._logger.info(
                        f"Pipe flush (Windows/pywin32): {flushed} bytes descartados"
                    )

            except ImportError:
                # pywin32 no instalado: hacemos un flush aproximado leyendo
                # durante una ventana de tiempo fija sin bloquear demasiado.
                self._logger.debug(
                    "pywin32 no disponible — flush aproximado (instala pywin32 "
                    "para mejor rendimiento en Windows)"
                )
                deadline = time.time() + 0.3
                fd = self._process.stdout.fileno()
                while time.time() < deadline:
                    try:
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            break
                        flushed += len(chunk)
                    except BlockingIOError:
                        break
                    except OSError:
                        break

                if flushed:
                    self._logger.info(
                        f"Pipe flush (Windows/approx): {flushed} bytes descartados"
                    )

            except Exception as exc:
                self._logger.debug(f"Error en flush de Windows: {exc}")

        else:
            # Linux / macOS: select() funciona perfectamente con pipes.
            fd = self._process.stdout.fileno()
            while True:
                ready, _, _ = select.select([self._process.stdout], [], [], 0.05)
                if not ready:
                    break
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                flushed += len(chunk)

            if flushed:
                self._logger.info(
                    f"Pipe flush (Linux): {flushed} bytes descartados (backlog de arranque)"
                )

    # -------------------------------------------------------------------------
    # Ciclo de vida
    # -------------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return

        if not self._ffmpeg_path:
            self._logger.error("No se puede iniciar: FFmpeg no está instalado")
            self.status = WorkerStatus.FFMPEG_NOT_FOUND
            return

        with self._state_lock:
            self._running = True
            self.status = WorkerStatus.STARTING
            self._last_frame_time = time.time()
            self._reconnect_attempts = 0
            self._last_error_code = None

        self._thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"FFmpeg-Cam{self.camera_id}",
        )
        self._thread.start()

        self._watchdog_running = True
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name=f"Watchdog-Cam{self.camera_id}",
        )
        self._watchdog_thread.start()

        self._logger.info(f"Worker iniciado para cámara {self.camera_id}")

    def stop(self) -> None:
        with self._state_lock:
            self._running = False
            self._watchdog_running = False
            self.status = WorkerStatus.STOPPED

        self._cleanup_process(kill=True)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=2)

        self._logger.info(f"Worker detenido. Frames enviados: {self._frames_sent}")

    def switch_transport(self, transport: str) -> bool:
        if transport not in ("tcp", "udp"):
            return False
        if self.rtsp_transport == transport:
            return True
        self._logger.info(f"Cambiando transporte: {self.rtsp_transport} -> {transport}")
        self.rtsp_transport = transport
        self._trigger_restart()
        return True

    def get_status(self) -> dict:
        with self._state_lock:
            return {
                "camera_id": self.camera_id,
                "status": self.status.value,
                "reconnect_attempts": self._reconnect_attempts,
                "last_frame_time": self._last_frame_time,
                "seconds_since_last_frame": time.time() - self._last_frame_time,
                "frames_processed": self._frames_processed,
                "frames_sent": self._frames_sent,
                "ffmpeg_found": self._ffmpeg_path is not None,
                "expected_resolution": f"{self.resolution_width}x{self.resolution_height}",
                "rtsp_transport": self.rtsp_transport,
                "last_error_code": self._last_error_code,
            }

    # -------------------------------------------------------------------------
    # Loops internos
    # -------------------------------------------------------------------------

    def _watchdog_loop(self) -> None:
        while self._watchdog_running:
            try:
                time.sleep(2)

                with self._state_lock:
                    if not self._running:
                        break
                    last_time = self._last_frame_time
                    current_status = self.status

                if (
                    current_status == WorkerStatus.RUNNING
                    and time.time() - last_time > self.WATCHDOG_TIMEOUT
                ):
                    self._logger.error(
                        f"Watchdog timeout: {time.time() - last_time:.1f}s sin frames"
                    )
                    self._trigger_restart()
                    break

            except Exception as exc:
                self._logger.error(f"Error en watchdog: {exc}")
                time.sleep(1)

    def _trigger_restart(self) -> None:
        with self._state_lock:
            self.status = WorkerStatus.RECONNECTING
        self._cleanup_process(kill=True)

    def _worker_loop(self) -> None:
        while self._running and self._reconnect_attempts < self.MAX_RECONNECT:
            try:
                if self._frame_size <= 0:
                    self._logger.error("Frame size no inicializado correctamente")
                    time.sleep(1)
                    continue

                self._start_ffmpeg_session()

                if self._running:
                    with self._state_lock:
                        self._reconnect_attempts += 1
                        if self.status != WorkerStatus.RECONNECTING:
                            self.status = WorkerStatus.RECONNECTING

                    if self._reconnect_attempts < self.MAX_RECONNECT:
                        wait_time = min(5 * (1.5 ** self._reconnect_attempts), 30)
                        self._logger.warning(
                            f"Reintento {self._reconnect_attempts}/{self.MAX_RECONNECT} "
                            f"en {wait_time:.1f}s..."
                        )
                        time.sleep(wait_time)
                else:
                    break

            except Exception as exc:
                self._logger.error(f"Error en worker loop: {exc}", exc_info=True)
                with self._state_lock:
                    self._reconnect_attempts += 1
                    self.status = WorkerStatus.ERROR
                if self._reconnect_attempts < self.MAX_RECONNECT:
                    time.sleep(5)

        if self._reconnect_attempts >= self.MAX_RECONNECT:
            with self._state_lock:
                self.status = WorkerStatus.ERROR
            self._logger.error("Máximos reintentos alcanzados")

    def _start_ffmpeg_session(self) -> None:
        cmd = self._build_ffmpeg_command()
        self._logger.info("Iniciando FFmpeg...")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                stdin=subprocess.DEVNULL,
            )

            # Esperar a que FFmpeg produzca datos (o falle) sin sleep fijo
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if self._process.poll() is not None:
                    stderr_data = self._process.stderr.read(512).decode("utf-8", errors="ignore")
                    self._logger.error(f"FFmpeg terminó prematuramente: {stderr_data}")
                    return

                if self.IS_WINDOWS:
                    time.sleep(0.1)
                else:
                    ready, _, _ = select.select([self._process.stdout], [], [], 0.1)
                    if ready:
                        break

            if self._process.poll() is not None:
                return

            # Vaciar el backlog acumulado durante el arranque
            self._flush_pipe_buffer()
            self._read_exact(self._process.stdout, self._frame_size)

            # Hilo de lectura de stderr para logs de error de FFmpeg
            self._stderr_thread = threading.Thread(
                target=self._stderr_reader,
                args=(self._process.stderr,),
                daemon=True,
            )
            self._stderr_thread.start()

            with self._state_lock:
                self.status = WorkerStatus.RUNNING
                self._reconnect_attempts = 0
                self._last_frame_time = time.time()

            self._logger.info(
                f"FFmpeg corriendo — leyendo {self._frame_size} bytes/frame"
            )

            # Loop principal de lectura de frames
            while self._running and self.status == WorkerStatus.RUNNING:
                frame_bytes = self._read_exact(self._process.stdout, self._frame_size)

                if frame_bytes is None:
                    self._logger.warning("Pipe cerrado o FFmpeg terminó")
                    break
                
                frame = np.frombuffer(frame_bytes, dtype=np.uint8)\
                .reshape((720, 1280, 3))\
                .copy()

                self._frames_processed += 1
                self.frame_buffer.put(frame)
                self._frames_sent += 1

                with self._state_lock:
                    self._last_frame_time = time.time()

        except Exception as exc:
            self._logger.error(f"Error en sesión FFmpeg: {exc}", exc_info=True)
            raise
        finally:
            self._cleanup_process()

    def _stderr_reader(self, pipe) -> None:
        try:
            while self._running:
                line = pipe.readline()
                if not line:
                    if not self._running:
                        break
                    time.sleep(0.01)
                    continue

                line_str = line.decode("utf-8", errors="ignore").strip()
                if not line_str:
                    continue

                error_code, retryable = self._parse_error(line_str)
                if error_code:
                    self._last_error_code = error_code
                    level = logging.WARNING if retryable else logging.ERROR
                    self._logger.log(level, f"FFmpeg [{error_code}]: {line_str}")
                    if not retryable:
                        self._logger.critical(
                            f"Error fatal permanente: {error_code}. Deteniendo worker."
                        )
                        if self._permanent_failure_callback:
                            self._permanent_failure_callback(self.camera_id, error_code)
                        self.stop()
                        break
                else:
                    self._logger.debug(f"FFmpeg: {line_str}")

        except Exception as exc:
            self._logger.debug(f"Stderr reader terminado: {exc}")

    def _cleanup_process(self, kill: bool = False) -> None:
        process = None
        with self._state_lock:
            process = self._process
            self._process = None

        if process:
            try:
                if kill:
                    process.kill()
                else:
                    process.terminate()
                process.wait(timeout=3)
            except Exception:
                pass

        if self._stderr_thread and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=2)