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
    # ========== CONFIGURACIÓN DE ESCALADO ==========
    # Cambia estos valores si quieres otra resolución de salida
    TARGET_WIDTH = 640
    TARGET_HEIGHT = 360

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

    IS_WINDOWS = os.name == "nt"

    def __init__(self, camera: Camera, frame_buffer: CircularFrameBuffer,
                 rtsp_transport: str = "tcp",
                 target_resolution: Optional[Tuple[int, int]] = None):
        """
        Args:
            camera: Modelo Camera con datos de conexión
            frame_buffer: Buffer circular destino
            rtsp_transport: tcp o udp
            target_resolution: (width, height) opcional para sobreescribir la resolución de salida.
                               Si es None, se usa TARGET_WIDTH, TARGET_HEIGHT de la clase.
        """
        self.camera_id = camera.id
        self.rtsp_url = camera.rtsp_url
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

        # ========== 1. DETECTAR RESOLUCIÓN REAL (UNA SOLA VEZ) ==========
        self.original_width = None
        self.original_height = None
        self._detect_real_resolution()  # Llena self.original_width/height

        # ========== 2. RESOLUCIÓN DE SALIDA (escalado) ==========
        if target_resolution:
            self.target_width, self.target_height = target_resolution
        else:
            self.target_width = self.TARGET_WIDTH
            self.target_height = self.TARGET_HEIGHT

        # Asegurar que sean múltiplos de 2 (requerido por codec)
        if self.target_width % 2 != 0:
            self.target_width -= 1
        if self.target_height % 2 != 0:
            self.target_height -= 1

        self._frame_size = self.target_width * self.target_height * 3  # BGR24

        self._logger.info(f"🎥 Cámara {camera.id}: resolución original={self.original_width}x{self.original_height}, "
                         f"escalado a {self.target_width}x{self.target_height}")

        self._stderr_thread: Optional[threading.Thread] = None
        self._frames_processed = 0
        self._frames_sent = 0
        self._last_error_code: Optional[str] = None
        self._permanent_failure_callback = None

        self._ffmpeg_path = self._find_ffmpeg_executable()
        if not self._ffmpeg_path:
            self.status = WorkerStatus.FFMPEG_NOT_FOUND
            self._logger.error("FFmpeg no encontrado")

    # ========== DETECCIÓN DE RESOLUCIÓN REAL (CON OPENCV) ==========
    def _detect_real_resolution(self) -> None:
        """Abre un VideoCapture, lee un frame y almacena resolución original."""
        cap = None
        try:
            self._logger.info(f"Detectando resolución real de {self.rtsp_url}")
            cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                self._logger.error("No se pudo abrir el stream para detectar resolución")
                self.original_width, self.original_height = 640, 360  # fallback
                return

            # Configurar para leer rápido
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            for _ in range(5):  # leer hasta 5 frames para estabilizar
                ret, frame = cap.read()
                if ret and frame is not None:
                    h, w = frame.shape[:2]
                    self.original_width, self.original_height = w, h
                    self._logger.info(f"✅ Resolución real detectada: {w}x{h}")
                    # Guardar snapshot opcional para debug
                    self._save_debug_snapshot(frame)
                    return
                time.sleep(0.1)
            # Fallback
            self.original_width, self.original_height = 640, 360
            self._logger.warning("No se pudo leer frame, usando fallback 640x360")
        except Exception as e:
            self._logger.error(f"Error detectando resolución: {e}")
            self.original_width, self.original_height = 640, 360
        finally:
            if cap:
                cap.release()

    def _save_debug_snapshot(self, frame: np.ndarray) -> None:
        """Guarda un snapshot de la cámara (opcional)."""
        try:
            debug_dir = "debug_snapshots"
            os.makedirs(debug_dir, exist_ok=True)
            path = os.path.join(debug_dir, f"cam_{self.camera_id}_original.jpg")
            cv2.imwrite(path, frame)
            self._logger.info(f"Snapshot original guardado en {path}")
        except Exception as e:
            self._logger.debug(f"No se pudo guardar snapshot: {e}")

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
        self._permanent_failure_callback = callback

    # ========== COMANDO FFMPEG CON ESCALADO ==========
    def _build_ffmpeg_command(self) -> list[str]:
        if not self._ffmpeg_path:
            raise RuntimeError("FFmpeg no disponible")

        # Filtro de escala: fuerza la resolución a target_width x target_height
        # Si quieres mantener aspect ratio con padding, cambia a:
        # f"scale={self.target_width}:{self.target_height}:force_original_aspect_ratio=decrease,pad={self.target_width}:{self.target_height}:(ow-iw)/2:(oh-ih)/2"
        vf_filter = f"scale={self.target_width}:{self.target_height}:force_original_aspect_ratio=disable"

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
            "-r", str(self.fps),
            "pipe:1",
        ]

        self._logger.info(f"FFmpeg: escala {self.original_width}x{self.original_height} → {self.target_width}x{self.target_height}")
        return cmd

    # ========== LECTURA EXACTA DE FRAMES (SIN STRIDE) ==========
    def _read_exact(self, pipe, size: int) -> Optional[bytes]:
        """Lee exactamente `size` bytes del pipe."""
        data = bytearray()
        while len(data) < size:
            chunk = pipe.read(size - len(data))
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    def _read_frame(self, pipe) -> Optional[np.ndarray]:
        """Lee un frame escalado del pipe de FFmpeg (rawvideo bgr24)."""
        frame_bytes = self._read_exact(pipe, self._frame_size)
        if frame_bytes is None:
            return None
        frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(
            (self.target_height, self.target_width, 3)
        )
        return frame

    def _parse_error(self, line: str) -> Tuple[Optional[str], bool]:
        for pattern, (code, retryable) in self.ERROR_PATTERNS.items():
            if re.search(pattern, line, re.IGNORECASE):
                return code, retryable
        return None, True

    def _flush_pipe_buffer(self) -> None:
        flushed = 0
        if self.IS_WINDOWS:
            try:
                import msvcrt
                import win32pipe
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
                    self._logger.info(f"Pipe flush (Windows): {flushed} bytes")
            except Exception:
                pass
        else:
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
                self._logger.info(f"Pipe flush (Linux): {flushed} bytes")

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
                        self._logger.critical(f"Error fatal permanente: {error_code}")
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

    def _start_ffmpeg_session(self) -> None:
        """Inicia FFmpeg con escalado y lee frames exactos."""
        cmd = self._build_ffmpeg_command()
        self._logger.info("Iniciando FFmpeg con escalado")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                stdin=subprocess.DEVNULL,
            )

            # Esperar datos iniciales
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

            self._flush_pipe_buffer()

            # Leer un frame de prueba
            test_frame = self._read_frame(self._process.stdout)
            if test_frame is None:
                self._logger.error("No se pudo leer el primer frame")
                return

            # Iniciar stderr reader
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

            self._logger.info(f"FFmpeg corriendo — frames escalados a {self.target_width}x{self.target_height}")

            # Loop principal
            while self._running and self.status == WorkerStatus.RUNNING:
                frame = self._read_frame(self._process.stdout)
                if frame is None:
                    self._logger.warning("Pipe cerrado o FFmpeg terminó")
                    break

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
                        self._logger.warning(f"Reintento {self._reconnect_attempts}/{self.MAX_RECONNECT} en {wait_time:.1f}s...")
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

    def _watchdog_loop(self) -> None:
        while self._watchdog_running:
            try:
                time.sleep(2)
                with self._state_lock:
                    if not self._running:
                        break
                    last_time = self._last_frame_time
                    current_status = self.status
                if current_status == WorkerStatus.RUNNING and time.time() - last_time > self.WATCHDOG_TIMEOUT:
                    self._logger.error(f"Watchdog timeout: {time.time() - last_time:.1f}s sin frames")
                    self._trigger_restart()
                    break
            except Exception as exc:
                self._logger.error(f"Error en watchdog: {exc}")
                time.sleep(1)

    def _trigger_restart(self) -> None:
        with self._state_lock:
            self.status = WorkerStatus.RECONNECTING
        self._cleanup_process(kill=True)

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
        self._thread = threading.Thread(target=self._worker_loop, daemon=True, name=f"FFmpeg-Cam{self.camera_id}")
        self._thread.start()
        self._watchdog_running = True
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True, name=f"Watchdog-Cam{self.camera_id}")
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
                "original_resolution": f"{self.original_width}x{self.original_height}",
                "target_resolution": f"{self.target_width}x{self.target_height}",
                "frame_bytes": self._frame_size,
                "rtsp_transport": self.rtsp_transport,
                "last_error_code": self._last_error_code,
            }