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
    """Estados posibles del worker FFmpeg."""
    STARTING = "starting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"
    ERROR = "error"
    FFMPEG_NOT_FOUND = "ffmpeg_not_found"
    RESOLUTION_MISMATCH = "resolution_mismatch"


class FFmpegWorker:
    """
    Worker que ejecuta FFmpeg para capturar stream RTSP.
    CORREGIDO: Parámetros RTSP compatibles, orden correcto, transporte configurable.
    FIX: -stimeout cambiado a -timeout (compatible FFmpeg 4.x/5.x/6.x)
    """

    def __init__(self, camera: Camera, frame_buffer: CircularFrameBuffer, 
                 rtsp_transport: str = "tcp"):
        # Extraer valores inmediatamente
        self.camera_id = camera.id
        self.rtsp_url = camera.rtsp_url
        self.resolution_width = camera.resolution_width
        self.resolution_height = camera.resolution_height
        self.fps = camera.fps
        self.camera_name = camera.name
        self.rtsp_transport = rtsp_transport  # "tcp" o "udp"

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

        # Queue para datos de stdout (lectura no bloqueante)
        self._stdout_queue = queue.Queue(maxsize=200)  # Aumentado para evitar bloqueos
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None

        # Tamaño esperado del frame
        self._frame_size = self.resolution_width * self.resolution_height * 3

        # Verificar FFmpeg
        self._ffmpeg_path = self._find_ffmpeg_executable()
        if not self._ffmpeg_path:
            self.status = WorkerStatus.FFMPEG_NOT_FOUND
            self._logger.error("FFmpeg no encontrado. Instale FFmpeg y anadalo al PATH.")

    def _find_ffmpeg_executable(self) -> Optional[str]:
        """Busca el ejecutable de FFmpeg."""
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            return ffmpeg_path

        common_windows_paths = [
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
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
        """
        Comando FFmpeg optimizado para RTSP.
        CORRECCION CRITICA: -stimeout cambiado a -timeout (compatible con FFmpeg moderno)
        Orden correcto de parámetros y flags compatibles con RTSP.
        """
        if not self._ffmpeg_path:
            raise RuntimeError("FFmpeg no disponible")

        # Filtro de video
        vf_filter = (f"fps={self.fps},"
                    f"scale={self.resolution_width}:{self.resolution_height}:force_original_aspect_ratio=decrease,"
                    f"pad={self.resolution_width}:{self.resolution_height}:(ow-iw)/2:(oh-ih)/2,"
                    f"setsar=1:1,"
                    f"format=pix_fmts=bgr24")

        # CORRECCION: Estructura correcta de comandos FFmpeg para RTSP
        # Orden: [Global Options] [Input Options] -i input [Output Options] output
        # FIX: -stimeout -> -timeout (parametro actual de FFmpeg 4.x/5.x/6.x)

        cmd = [
            self._ffmpeg_path,
            # === GLOBAL OPTIONS ===
            "-hide_banner",          # Ocultar info de versión
            "-loglevel", "error",    # Solo errores (reducir overhead)
            "-fflags", "nobuffer",   # Sin buffer de entrada
            "-flags", "low_delay",   # Baja latencia

            # === INPUT OPTIONS (antes de -i) ===
            "-rtsp_transport", self.rtsp_transport,  # tcp o udp
            "-timeout", "10000000",  # FIX: Cambiado de -stimeout (obsoleto) a -timeout (microsegundos)

            # === INPUT ===
            "-i", self.rtsp_url,

            # === OUTPUT OPTIONS ===
            "-vf", vf_filter,
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-threads", "1",
            # Quitar flags de reconnect - no funcionan bien con RTSP nativo
            "pipe:1"
        ]

        return cmd

    def start(self) -> None:
        """Inicia el worker de forma segura."""
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

        self._logger.info(f"Worker iniciado para camara {self.camera_id} ({self.resolution_width}x{self.resolution_height})")

    def _watchdog_loop(self) -> None:
        """Monitorea la actividad del worker y fuerza reconexión si es necesario."""
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
        """Fuerza la limpieza del proceso para permitir reconexión."""
        with self._state_lock:
            self.status = WorkerStatus.RECONNECTING

        self._cleanup_process(kill=True)

        # Limpiar queue
        cleared = 0
        while not self._stdout_queue.empty():
            try:
                self._stdout_queue.get_nowait()
                cleared += 1
            except queue.Empty:
                break
        if cleared > 0:
            self._logger.debug(f"Limpiados {cleared} chunks de queue stale")

    def _stdout_reader(self, pipe):
        """Hilo separado para leer stdout sin bloquear."""
        try:
            while self._running:
                # CORRECCION: Leer en chunks más grandes para eficiencia, 
                # pero verificar frecuentemente si debemos salir
                try:
                    chunk = pipe.read(8192)  # Aumentado a 8KB
                    if chunk:
                        # CORRECCION CRITICA: Nunca bloquear en put
                        try:
                            self._stdout_queue.put_nowait(chunk)
                        except queue.Full:
                            # Descartar el más antiguo y poner el nuevo
                            try:
                                self._stdout_queue.get_nowait()
                                self._stdout_queue.put_nowait(chunk)
                            except queue.Empty:
                                pass
                    else:
                        # EOF detectado
                        if not self._running:
                            break
                        time.sleep(0.001)
                except (ValueError, OSError) as e:
                    self._logger.debug(f"Stdout pipe cerrado: {e}")
                    break
        except Exception as e:
            self._logger.debug(f"Stdout reader terminado: {e}")

    def _stderr_reader(self, pipe):
        """Hilo separado para leer stderr y evitar bloqueo por buffer lleno."""
        try:
            while self._running:
                line = pipe.readline()
                if not line:
                    if not self._running:
                        break
                    time.sleep(0.01)
                    continue

                line_str = line.decode('utf-8', errors='ignore').strip()

                # CORRECCION: Detectar errores específicos de RTSP para mejor diagnóstico
                if line_str:
                    if "401 Unauthorized" in line_str:
                        self._logger.error(f"Error de autenticacion RTSP: {line_str}")
                    elif "404 Not Found" in line_str or "No such file" in line_str:
                        self._logger.error(f"Stream RTSP no encontrado: {line_str}")
                    elif "Connection refused" in line_str or "Connection timed out" in line_str:
                        self._logger.warning(f"Conexion RTSP fallida: {line_str}")
                    elif not line_str.startswith('frame=') and not line_str.startswith('size='):
                        self._logger.debug(f"FFmpeg: {line_str}")

        except Exception as e:
            self._logger.debug(f"Stderr reader terminado: {e}")

    def _worker_loop(self) -> None:
        """Loop principal con manejo robusto de errores."""
        while self._running and self._reconnect_attempts < self.MAX_RECONNECT:
            try:
                self._start_ffmpeg_session()

                # Si llegamos aquí, FFmpeg terminó
                if self._running:
                    with self._state_lock:
                        self._reconnect_attempts += 1
                        if self.status != WorkerStatus.RECONNECTING:
                            self.status = WorkerStatus.RECONNECTING

                    if self._reconnect_attempts < self.MAX_RECONNECT:
                        # CORRECCION: Backoff exponencial para no saturar
                        wait_time = min(5 * (1.5 ** self._reconnect_attempts), 30)
                        self._logger.warning(f"Reintento {self._reconnect_attempts}/{self.MAX_RECONNECT} en {wait_time:.1f}s...")
                        time.sleep(wait_time)
                else:
                    break

            except FileNotFoundError as e:
                self._logger.error(f"FFmpeg no encontrado: {e}")
                with self._state_lock:
                    self.status = WorkerStatus.FFMPEG_NOT_FOUND
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
            self._logger.error("Maximos reintentos alcanzados - deteniendo worker")

    def _start_ffmpeg_session(self) -> None:
        """
        Inicia FFmpeg y procesa frames.
        CORRECCION: Mejor manejo de errores iniciales y configuración de pipes.
        """
        cmd = self._build_ffmpeg_command()
        self._logger.debug(f"Ejecutando: {' '.join(cmd)}")

        try:
            # CORRECCION: Configuración de subprocess optimizada
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,  # Sin buffer para baja latencia
                stdin=subprocess.DEVNULL,
                # CORRECCION: Cerrar todos los file descriptors heredados en Unix
                close_fds=True if os.name != 'nt' else False
            )

            # Esperar un momento para verificar que FFmpeg no muera inmediatamente
            time.sleep(0.5)
            if self._process.poll() is not None:
                # FFmpeg terminó inmediatamente
                stderr_data = self._process.stderr.read(1024).decode('utf-8', errors='ignore')
                if "Unrecognized option" in stderr_data and "timeout" in stderr_data:
                    self._logger.error("FFmpeg no soporta el parametro -timeout. Intenta actualizar FFmpeg.")
                else:
                    self._logger.error(f"FFmpeg termino inmediatamente: {stderr_data[:200]}")
                return  # Salir para trigger reconnect

            # Iniciar threads de lectura
            self._stdout_thread = threading.Thread(
                target=self._stdout_reader,
                args=(self._process.stdout,),
                daemon=True,
                name=f"FFmpeg-Stdout-{self.camera_id}"
            )
            self._stdout_thread.start()

            self._stderr_thread = threading.Thread(
                target=self._stderr_reader,
                args=(self._process.stderr,),
                daemon=True,
                name=f"FFmpeg-Stderr-{self.camera_id}"
            )
            self._stderr_thread.start()

            with self._state_lock:
                self.status = WorkerStatus.RUNNING
                self._reconnect_attempts = 0
                self._last_frame_time = time.time()

            self._logger.info(f"FFmpeg iniciado: {self.resolution_width}x{self.resolution_height} @ {self.fps}fps ({self.rtsp_transport})")

            # Loop de procesamiento de frames
            pending_data = b''
            empty_count = 0
            consecutive_errors = 0
            MAX_CONSECUTIVE_ERRORS = 10

            while self._running and self.status == WorkerStatus.RUNNING:
                try:
                    # Timeout en la queue para verificar estado frecuentemente
                    chunk = self._stdout_queue.get(timeout=1.0)
                    pending_data += chunk
                    empty_count = 0
                    consecutive_errors = 0
                except queue.Empty:
                    empty_count += 1

                    # Verificar si el proceso terminó
                    if self._process and self._process.poll() is not None:
                        exit_code = self._process.returncode
                        if exit_code != 0:
                            self._logger.warning(f"FFmpeg termino con codigo {exit_code}")
                        else:
                            self._logger.info("FFmpeg termino normalmente (EOF)")
                        break

                    # Si está vacío por mucho tiempo, podría ser stall
                    if empty_count > 20:  # 20 segundos sin datos
                        self._logger.warning("No hay datos por 20s, posible problema de red")
                        # No romper el loop, dejar que el watchdog actúe
                    continue

                # Procesar frames completos
                while len(pending_data) >= self._frame_size:
                    raw = pending_data[:self._frame_size]
                    pending_data = pending_data[self._frame_size:]

                    try:
                        frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                            (self.resolution_height, self.resolution_width, 3)
                        )

                        if frame.size == 0:
                            continue

                        self.frame_buffer.put(frame)

                        with self._state_lock:
                            self._last_frame_time = time.time()

                    except ValueError as e:
                        consecutive_errors += 1
                        if consecutive_errors <= 3:  # Log solo los primeros errores
                            self._logger.error(f"Error reshape ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {e}. "
                                             f"Esperado: {self._frame_size}, Datos: {len(raw)}")
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            self._logger.error("Demasiados errores consecutivos, reiniciando...")
                            break
                        continue
                    except Exception as e:
                        self._logger.error(f"Error procesando frame: {e}")
                        continue

                # Limitar tamaño de buffer pendiente (evitar memory leak)
                max_buffer = self._frame_size * 5
                if len(pending_data) > max_buffer:
                    self._logger.warning(f"Buffer overflow ({len(pending_data)} > {max_buffer}), descartando datos")
                    # Mantener solo el último frame completo posible
                    excess = len(pending_data) - (self._frame_size * 2)
                    if excess > 0:
                        pending_data = pending_data[excess:]

        except Exception as e:
            self._logger.error(f"Error iniciando FFmpeg: {e}", exc_info=True)
            raise
        finally:
            self._cleanup_process()

    def _cleanup_process(self, kill: bool = False) -> None:
        """Limpia el proceso FFmpeg y threads de I/O de forma segura."""
        process = None
        with self._state_lock:
            process = self._process
            self._process = None

        if process:
            try:
                if kill:
                    process.kill()
                    self._logger.debug("Proceso FFmpeg killado")
                else:
                    process.terminate()

                # Esperar con timeout
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    if not kill:
                        process.kill()
                        process.wait(timeout=1)
            except Exception as e:
                self._logger.debug(f"Error limpiando proceso: {e}")

        # Esperar threads de I/O
        for thread_attr in ['_stdout_thread', '_stderr_thread']:
            thread = getattr(self, thread_attr, None)
            if thread and thread.is_alive():
                thread.join(timeout=2)
                if thread.is_alive():
                    self._logger.warning(f"Thread {thread_attr} no termino a tiempo")

    def stop(self) -> None:
        """Detiene el worker y libera recursos."""
        with self._state_lock:
            self._running = False
            self._watchdog_running = False
            self.status = WorkerStatus.STOPPED

        self._cleanup_process(kill=True)

        # Esperar threads principales
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=2)

        # Limpiar queue residual
        cleared = 0
        while not self._stdout_queue.empty():
            try:
                self._stdout_queue.get_nowait()
                cleared += 1
            except queue.Empty:
                break

        if cleared > 0:
            self._logger.debug(f"Limpiados {cleared} items residuales de queue")

        self._logger.info(f"Worker detenido para camara {self.camera_id}")

    def get_status(self) -> dict:
        """Retorna estado actual thread-safe."""
        with self._state_lock:
            return {
                "camera_id": self.camera_id,
                "status": self.status.value,
                "reconnect_attempts": self._reconnect_attempts,
                "last_frame_time": self._last_frame_time,
                "seconds_since_last_frame": time.time() - self._last_frame_time,
                "ffmpeg_found": self._ffmpeg_path is not None,
                "ffmpeg_path": self._ffmpeg_path,
                "expected_resolution": f"{self.resolution_width}x{self.resolution_height}",
                "frame_size_bytes": self._frame_size,
                "queue_size": self._stdout_queue.qsize(),
                "rtsp_transport": self.rtsp_transport
            }

    def switch_transport(self, transport: str) -> bool:
        """
        Cambia entre TCP y UDP sin reiniciar todo el worker.
        Útil para cámaras problemáticas.
        """
        if transport not in ["tcp", "udp"]:
            return False

        if self.rtsp_transport == transport:
            return True

        self._logger.info(f"Cambiando transporte RTSP: {self.rtsp_transport} -> {transport}")
        self.rtsp_transport = transport

        # Forzar reconexión con nuevo transporte
        self._trigger_restart()
        return True