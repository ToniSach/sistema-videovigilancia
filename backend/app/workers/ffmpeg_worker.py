import subprocess
import threading
import numpy as np
import logging
import time
import os
import shutil
import queue
from enum import Enum
from typing import Optional

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
    """
    Worker que ejecuta FFmpeg para capturar stream RTSP.
    VERSIÓN CORREGIDA - Parámetros de salida simplificados.
    """

    def __init__(self, camera: Camera, frame_buffer: CircularFrameBuffer, 
                 rtsp_transport: str = "tcp"):
        self.camera_id = camera.id
        self.rtsp_url = camera.rtsp_url
        self.resolution_width = camera.resolution_width
        self.resolution_height = camera.resolution_height
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

        self._stdout_queue = queue.Queue(maxsize=1000)
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None

        self._frame_size = self.resolution_width * self.resolution_height * 3
        self._frames_processed = 0
        self._frames_sent = 0

        self._ffmpeg_path = self._find_ffmpeg_executable()
        if not self._ffmpeg_path:
            self.status = WorkerStatus.FFMPEG_NOT_FOUND
            self._logger.error("FFmpeg no encontrado")

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

    def _build_ffmpeg_command(self) -> list[str]:
        """
        Comando FFmpeg CORREGIDO - Sin parámetros problemáticos.
        """
        if not self._ffmpeg_path:
            raise RuntimeError("FFmpeg no disponible")

        # Filtro simple
        vf_filter = f"fps={self.fps},scale={self.resolution_width}:{self.resolution_height},format=bgr24"

        cmd = [
            self._ffmpeg_path,
            "-hide_banner",
            "-loglevel", "error",
            "-rtsp_transport", self.rtsp_transport,
            "-timeout", "5000000",
            "-i", self.rtsp_url,
            "-vf", vf_filter,
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "pipe:1"
        ]

        return cmd

    def start(self) -> None:
        if self._running:
            return

        if not self._ffmpeg_path:
            self._logger.error("No se puede iniciar: FFmpeg no esta instalado")
            self.status = WorkerStatus.FFMPEG_NOT_FOUND
            return

        with self._state_lock:
            self._running = True
            self.status = WorkerStatus.STARTING
            self._last_frame_time = time.time()
            self._reconnect_attempts = 0

        self._thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"FFmpeg-Cam{self.camera_id}"
        )
        self._thread.start()

        self._watchdog_running = True
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name=f"Watchdog-Cam{self.camera_id}"
        )
        self._watchdog_thread.start()

        self._logger.info(f"Worker iniciado para camara {self.camera_id}")

    def _watchdog_loop(self) -> None:
        check_interval = 2

        while self._watchdog_running:
            try:
                time.sleep(check_interval)

                with self._state_lock:
                    if not self._running:
                        break
                    last_time = self._last_frame_time
                    current_status = self.status

                time_since_last = time.time() - last_time

                if current_status == WorkerStatus.RUNNING and time_since_last > self.WATCHDOG_TIMEOUT:
                    self._logger.error(f"Watchdog timeout: {time_since_last:.1f}s sin frames")
                    self._trigger_restart()
                    break

            except Exception as e:
                self._logger.error(f"Error en watchdog: {e}")
                time.sleep(1)

    def _trigger_restart(self) -> None:
        with self._state_lock:
            self.status = WorkerStatus.RECONNECTING

        self._cleanup_process(kill=True)
        
        while not self._stdout_queue.empty():
            try:
                self._stdout_queue.get_nowait()
            except queue.Empty:
                break

    def _stdout_reader(self, pipe):
        try:
            while self._running:
                try:
                    chunk = pipe.read(65536)
                    if chunk:
                        try:
                            self._stdout_queue.put_nowait(chunk)
                        except queue.Full:
                            try:
                                self._stdout_queue.get_nowait()
                                self._stdout_queue.put_nowait(chunk)
                            except queue.Empty:
                                pass
                    else:
                        if not self._running:
                            break
                        time.sleep(0.001)
                except (ValueError, OSError) as e:
                    self._logger.debug(f"Stdout pipe cerrado: {e}")
                    break
        except Exception as e:
            self._logger.debug(f"Stdout reader terminado: {e}")

    def _stderr_reader(self, pipe):
        try:
            while self._running:
                line = pipe.readline()
                if not line:
                    if not self._running:
                        break
                    time.sleep(0.01)
                    continue

                line_str = line.decode('utf-8', errors='ignore').strip()
                if line_str:
                    # Filtrar errores comunes de HEVC (no críticos)
                    if "Error constructing the frame RPS" in line_str:
                        self._logger.debug(f"HEVC warning: {line_str}")
                    elif "Invalid data found" in line_str:
                        self._logger.debug(f"FFmpeg: {line_str}")
                    else:
                        self._logger.debug(f"FFmpeg: {line_str}")

        except Exception as e:
            self._logger.debug(f"Stderr reader terminado: {e}")

    def _worker_loop(self) -> None:
        while self._running and self._reconnect_attempts < self.MAX_RECONNECT:
            try:
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

            except Exception as e:
                self._logger.error(f"Error en worker loop: {e}", exc_info=True)
                with self._state_lock:
                    self._reconnect_attempts += 1
                    self.status = WorkerStatus.ERROR
                if self._reconnect_attempts < self.MAX_RECONNECT:
                    time.sleep(5)

        if self._reconnect_attempts >= self.MAX_RECONNECT:
            with self._state_lock:
                self.status = WorkerStatus.ERROR
            self._logger.error("Maximos reintentos alcanzados")

    def _start_ffmpeg_session(self) -> None:
        cmd = self._build_ffmpeg_command()
        self._logger.info(f"Iniciando FFmpeg...")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                stdin=subprocess.DEVNULL
            )

            time.sleep(1.5)
            if self._process.poll() is not None:
                stderr_data = self._process.stderr.read(1024).decode('utf-8', errors='ignore')
                self._logger.error(f"FFmpeg terminó: {stderr_data[:200]}")
                return

            self._stdout_thread = threading.Thread(
                target=self._stdout_reader,
                args=(self._process.stdout,),
                daemon=True
            )
            self._stdout_thread.start()

            self._stderr_thread = threading.Thread(
                target=self._stderr_reader,
                args=(self._process.stderr,),
                daemon=True
            )
            self._stderr_thread.start()

            with self._state_lock:
                self.status = WorkerStatus.RUNNING
                self._reconnect_attempts = 0
                self._last_frame_time = time.time()

            self._logger.info(f"FFmpeg corriendo")

            pending_data = bytearray()
            empty_count = 0
            frame_count = 0

            while self._running and self.status == WorkerStatus.RUNNING:
                try:
                    chunk = self._stdout_queue.get(timeout=0.5)
                    pending_data.extend(chunk)
                    empty_count = 0
                except queue.Empty:
                    empty_count += 1
                    
                    if self._process and self._process.poll() is not None:
                        self._logger.warning(f"FFmpeg terminó")
                        break
                    
                    if empty_count > 60:
                        self._logger.warning("30 segundos sin datos, reiniciando...")
                        self._trigger_restart()
                        return
                    continue

                while len(pending_data) >= self._frame_size:
                    frame_bytes = bytes(pending_data[:self._frame_size])
                    pending_data = pending_data[self._frame_size:]
                    
                    try:
                        frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(
                            self.resolution_height, self.resolution_width, 3
                        )
                        
                        frame_count += 1
                        self._frames_processed += 1
                        
                        # Enviar al buffer
                        self.frame_buffer.put(frame.copy())
                        self._frames_sent += 1
                        
                        with self._state_lock:
                            self._last_frame_time = time.time()
                            
                    except ValueError as e:
                        self._logger.error(f"Error reshape: {e}")
                        continue
                    except Exception as e:
                        self._logger.error(f"Error: {e}")
                        continue
                
                max_buffer = self._frame_size * 10
                if len(pending_data) > max_buffer:
                    pending_data = pending_data[-self._frame_size:]

        except Exception as e:
            self._logger.error(f"Error: {e}", exc_info=True)
            raise
        finally:
            self._cleanup_process()

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
            except:
                pass

        for thread_attr in ['_stdout_thread', '_stderr_thread']:
            thread = getattr(self, thread_attr, None)
            if thread and thread.is_alive():
                thread.join(timeout=2)

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

        self._logger.info(f"Worker detenido. Frames: {self._frames_sent}")

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
                "queue_size": self._stdout_queue.qsize(),
                "rtsp_transport": self.rtsp_transport
            }

    def switch_transport(self, transport: str) -> bool:
        if transport not in ["tcp", "udp"]:
            return False

        if self.rtsp_transport == transport:
            return True

        self._logger.info(f"Cambiando transporte: {self.rtsp_transport} -> {transport}")
        self.rtsp_transport = transport
        self._trigger_restart()
        return True