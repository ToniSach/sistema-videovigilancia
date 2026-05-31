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
    # ──────────────────────────────────────────────────────────────────────
    # Resolución de salida (escalado)
    # ──────────────────────────────────────────────────────────────────────
    # Estos defaults solo se usan si no hay settings.FFMPEG_RESOLUTION_*
    # disponible y el caller no pasa `target_resolution`. En condiciones
    # normales la fuente de verdad es el .env (settings.FFMPEG_RESOLUTION_WIDTH
    # / FFMPEG_RESOLUTION_HEIGHT, default 1280×720).
    #
    # Diferencia resolución vs. escalado:
    #   - RESOLUCIÓN ORIGINAL: lo que la cámara emite por RTSP (la configuras
    #     en su panel web). No la cambiamos.
    #   - ESCALADO: filtro `-vf scale=W:H` que reduce cada frame ANTES de
    #     entregarlo a los consumidores (MJPEG encoder, YOLO, grabación,
    #     buffer circular). Ahorra CPU/RAM proporcionalmente a los píxeles.
    #
    # Si la cámara ya emite <= target, NO escalamos (saltarse el filtro
    # ahorra ~5-15% de CPU y evita degradación innecesaria por reescalado).
    DEFAULT_TARGET_WIDTH = 640
    DEFAULT_TARGET_HEIGHT = 360

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
        self._running_since: float = 0.0  # se setea al pasar a RUNNING
        self._state_lock = threading.Lock()
        self._logger = logging.getLogger(f"{__name__}.Cam{self.camera_id}")
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_running = False

        # 1) Resolución original de la cámara (cache BD → ffprobe fallback)
        self._init_original_resolution(camera)
        # 2) Resolución de salida + decisión de escalar o no
        self._init_target_resolution(target_resolution)
        # Tamaño en bytes del frame raw (BGR24 = 3 bytes/píxel)
        self._frame_size = self.target_width * self.target_height * 3

        self._logger.info(
            f"🎥 Cam {camera.id}: original={self.original_width}x{self.original_height} "
            f"→ salida={self.target_width}x{self.target_height} "
            f"(escalado={'sí' if self._needs_scaling else 'NO, passthrough'})"
        )

        self._stderr_thread: Optional[threading.Thread] = None
        self._frames_processed = 0
        self._frames_sent = 0
        self._last_error_code: Optional[str] = None
        self._permanent_failure_callback = None

        self._ffmpeg_path = self._find_ffmpeg_executable()
        if not self._ffmpeg_path:
            self.status = WorkerStatus.FFMPEG_NOT_FOUND
            self._logger.error("FFmpeg no encontrado")

    # ──────────────────────────────────────────────────────────────────────
    # Inicialización de resolución (extraídas de __init__)
    # ──────────────────────────────────────────────────────────────────────
    def _init_original_resolution(self, camera: Camera) -> None:
        """
        Decide la resolución ORIGINAL del stream. Si la BD ya cachea valores
        válidos (no el default 1920x1080 sospechoso de no-detectado), los usa
        y nos ahorra ~1-3s de ffprobe por arranque. Si no, lanza detección
        y persiste el resultado.
        """
        self.original_width = None
        self.original_height = None
        cached_w = getattr(camera, "resolution_width", None)
        cached_h = getattr(camera, "resolution_height", None)
        # El default del modelo Camera es 1920x1080; no podemos distinguir
        # "se detectó 1920x1080 de verdad" de "nadie detectó nada" → forzamos
        # re-detección solo en ese caso concreto.
        if cached_w and cached_h and not (cached_w == 1920 and cached_h == 1080):
            self.original_width = int(cached_w)
            self.original_height = int(cached_h)
            self._logger.info(
                f"Resolución desde cache BD: {self.original_width}x{self.original_height}"
            )
        else:
            self._detect_real_resolution()
            self._persist_resolution_to_db()

    def _init_target_resolution(self,
                                target_resolution: Optional[Tuple[int, int]]) -> None:
        """
        Decide la resolución de SALIDA (escalado) PRESERVANDO el aspect ratio
        original. Esto evita la deformación visible cuando original y target
        tienen ratios distintos (típico: cámara dual 3072x2048 = 3:2 escalada
        a 1280x720 = 16:9 se ve aplastada horizontalmente).

        Orden de precedencia para el ANCHO:
          1. `target_resolution[0]` del caller (dual-lens splitter pasa width).
          2. settings.FFMPEG_RESOLUTION_WIDTH del .env.
          3. DEFAULT_TARGET_WIDTH (640) como último recurso.

        El ALTO se DERIVA del ancho × aspect_original. Ignoramos el height del
        caller / .env porque ese era el origen de la deformación. Si en algún
        caso futuro hace falta forzar un height específico (con letterbox),
        habría que añadir un flag explícito.
        """
        # 1) Width: del caller o del .env
        if target_resolution and target_resolution[0] > 0:
            tw = int(target_resolution[0])
        else:
            try:
                from ..config import settings
                tw = int(getattr(settings, "FFMPEG_RESOLUTION_WIDTH",
                                 self.DEFAULT_TARGET_WIDTH))
            except Exception:
                tw = self.DEFAULT_TARGET_WIDTH

        # 2) Height: derivado del aspect original (preserva proporción)
        if self.original_width and self.original_height and self.original_width > 0:
            th = max(2, int(round(tw * self.original_height / self.original_width)))
        else:
            # Sin original conocido, caer al height configurado (mejor que nada)
            try:
                from ..config import settings
                th = int(getattr(settings, "FFMPEG_RESOLUTION_HEIGHT",
                                 self.DEFAULT_TARGET_HEIGHT))
            except Exception:
                th = self.DEFAULT_TARGET_HEIGHT

        # Múltiplos de 2 (requerido por codec H.264/JPEG planar)
        tw -= tw % 2
        th -= th % 2

        # Skip-scale-if-already-small: si la cámara ya emite menor o igual
        # tamaño que el target, no escalar — ahorra CPU del filtro scale.
        if (self.original_width and self.original_height
                and self.original_width <= tw and self.original_height <= th):
            self.target_width = self.original_width
            self.target_height = self.original_height
            self._needs_scaling = False
        else:
            self.target_width = tw
            self.target_height = th
            self._needs_scaling = True

    def _persist_resolution_to_db(self) -> None:
        """Guarda la resolución detectada en la tabla cameras para cachearla."""
        if not self.original_width or not self.original_height:
            return
        try:
            from backend.app.database.connection import db_manager
            from backend.app.database.models import Camera as CameraModel
            with db_manager.get_session() as session:
                cam = session.query(CameraModel).filter_by(id=self.camera_id).first()
                if cam is None:
                    return
                cam.resolution_width = int(self.original_width)
                cam.resolution_height = int(self.original_height)
                session.commit()
            self._logger.debug(
                f"Resolución cacheada en BD: "
                f"{self.original_width}x{self.original_height}"
            )
        except Exception as e:
            self._logger.warning(f"No se pudo cachear resolución en BD: {e}")

    # ========== DETECCIÓN DE RESOLUCIÓN REAL (CON OPENCV) ==========
    def _detect_real_resolution(self) -> None:
        """
        Detecta la resolución real del stream RTSP usando `ffprobe` con flags
        de baja latencia. Antes usaba cv2.VideoCapture que internamente abre
        un FFmpeg con `analyzeduration=5s` por defecto → ese era el 80% del
        delay inicial al arrancar una cámara.

        Con `-probesize 32 -analyzeduration 0` ffprobe devuelve la resolución
        en <500ms típico vs 3-5s del cv2.VideoCapture.
        """
        try:
            self._logger.info(f"Detectando resolución real de {self.rtsp_url}")

            ffprobe_path = shutil.which("ffprobe")
            if ffprobe_path is None:
                # Si ffprobe no está, hacemos un fallback robusto:
                # arrancamos un ffmpeg rápido que solo lea 1 frame.
                self._logger.warning(
                    "ffprobe no disponible, usando fallback 1280x720"
                )
                self.original_width, self.original_height = 1280, 720
                return

            cmd = [
                ffprobe_path,
                "-v", "error",
                "-probesize", "32",
                "-analyzeduration", "0",
                "-rtsp_transport", self.rtsp_transport,
                "-timeout", "5000000",  # 5s, microsegundos
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0:s=x",
                self.rtsp_url,
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                self._logger.error(
                    f"ffprobe falló (rc={result.returncode}): "
                    f"{result.stderr[:200]}"
                )
                self.original_width, self.original_height = 1280, 720
                return

            # Salida esperada: "1920x1080\n"
            output = result.stdout.strip()
            if "x" not in output:
                self._logger.warning(
                    f"ffprobe sin resolución parseable: {output!r}, fallback 1280x720"
                )
                self.original_width, self.original_height = 1280, 720
                return

            w_str, h_str = output.split("x", 1)
            self.original_width = int(w_str)
            self.original_height = int(h_str)
            self._logger.info(
                f"✅ Resolución real detectada: "
                f"{self.original_width}x{self.original_height}"
            )

        except subprocess.TimeoutExpired:
            self._logger.error("ffprobe timeout (>10s), fallback 1280x720")
            self.original_width, self.original_height = 1280, 720
        except Exception as e:
            self._logger.error(f"Error detectando resolución: {e}")
            self.original_width, self.original_height = 1280, 720

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

    # Cache compartido entre todos los workers
    _FFMPEG_VERSION_CACHE: Optional[Tuple[int, int]] = None
    _FFMPEG_VERSION_LOCK = threading.Lock()

    @classmethod
    def _detect_ffmpeg_version(cls, ffmpeg_path: str) -> Tuple[int, int]:
        """
        Detecta la versión MAJOR.MINOR de ffmpeg.
        Se cachea entre instancias (todas usan el mismo binario).
        """
        with cls._FFMPEG_VERSION_LOCK:
            if cls._FFMPEG_VERSION_CACHE is not None:
                return cls._FFMPEG_VERSION_CACHE
            try:
                result = subprocess.run(
                    [ffmpeg_path, "-version"],
                    capture_output=True, text=True, timeout=5,
                )
                # "ffmpeg version 8.0.1-essentials_build-www.gyan.dev ..."
                # "ffmpeg version 4.4.2-0ubuntu0.22.04.1 ..."
                # "ffmpeg version n7.1 ..."
                m = re.search(r"ffmpeg version n?(\d+)\.(\d+)",
                              (result.stdout or "") + (result.stderr or ""))
                if m:
                    cls._FFMPEG_VERSION_CACHE = (int(m.group(1)), int(m.group(2)))
                    logging.getLogger(__name__).info(
                        f"FFmpeg versión detectada: {cls._FFMPEG_VERSION_CACHE[0]}."
                        f"{cls._FFMPEG_VERSION_CACHE[1]}"
                    )
                    return cls._FFMPEG_VERSION_CACHE
            except Exception as e:
                logging.getLogger(__name__).warning(
                    f"No se pudo detectar versión FFmpeg: {e}"
                )
            cls._FFMPEG_VERSION_CACHE = (4, 0)  # default conservador
            return cls._FFMPEG_VERSION_CACHE

    def set_permanent_failure_callback(self, callback):
        self._permanent_failure_callback = callback

    # ========== COMANDO FFMPEG CON ESCALADO ==========
    def _build_ffmpeg_command(self) -> list[str]:
        if not self._ffmpeg_path:
            raise RuntimeError("FFmpeg no disponible")

        # Filtro scale. Solo lo aplicamos si realmente reducimos resolución
        # (ver _init_target_resolution). Cuando la cámara ya emite ≤ target,
        # _needs_scaling=False y omitimos el filtro → ahorro de CPU.
        #
        # NOTA: NO usar `fps=N` aquí. Combina mal con vsync passthrough +
        # nobuffer + probesize 32 (PTS RTSP inestables → cap a 1 fps).
        # El throttle real está en MJPEGStreamer.update_frame (wall clock).
        vf_filter = (
            f"scale={self.target_width}:{self.target_height}"
            f":force_original_aspect_ratio=disable"
        ) if self._needs_scaling else None

        # Detección de versión: FFmpeg 6+ eliminó `-stimeout` (forzó `-timeout`).
        version = self._detect_ffmpeg_version(self._ffmpeg_path)
        if version >= (6, 0):
            rtsp_timeout = ["-timeout", "10000000"]   # microsegundos = 10s
        else:
            rtsp_timeout = ["-stimeout", "10000000"]

        # Config validada para cámaras XiongMai / RTSP genéricas:
        #
        #   -rtsp_transport tcp : TCP fiable contra packet loss.
        #   -timeout            : timeout I/O RTSP (10s).
        #   -fflags nobuffer    : no acumular en demuxer (baja latencia).
        #   -flags low_delay    : decodificador en modo baja-latencia.
        #   -vsync passthrough  : entrega frames TAL CUAL llegan, no duplica
        #                          ni descarta artificialmente.
        #
        # QUITADOS (causaban la sensación de "video trabado"):
        #   -r {fps}                       → forzaba duplicación si la cámara
        #                                     emitía pocos frames únicos
        #   -fflags +discardcorrupt        → demasiado agresivo, tira P-frames
        #                                     válidos en cámaras con bitrate alto
        #   -fflags +genpts                → reconstruir PTS confunde el decoder
        #   -avioflags direct              → I/O sin buffer del SO, lecturas parciales
        #   -use_wallclock_as_timestamps   → choca con vsync passthrough
        #
        # `-rw_timeout` y `-reconnect*` no se incluyen (HTTP-only, ignorados
        # por RTSP en algunas builds o rechazados).
        # CRÍTICO PARA BAJA LATENCIA:
        #   -probesize 32 + -analyzeduration 0  → no esperar 5s analizando stream.
        #   -fflags nobuffer                    → demuxer sin colchón.
        #   -flags low_delay                    → decoder en modo low-latency.
        #   -flags2 +fast                       → decoder usa fast paths (puede
        #                                          saltar B-frame reordering;
        #                                          en cámaras IP no se usan
        #                                          B-frames así que es seguro).
        #   -strict experimental                → habilita -flags2 +fast.
        #   -fflags +flush_packets              → fuerza envío al pipe inmediato.
        #   -vsync passthrough                  → frames tal cual llegan.
        #
        # Antes: sin probesize/analyzeduration explícitos, FFmpeg usaba 5MB
        # y 5 SEGUNDOS de análisis de stream → eso solo añadía 5s al delay
        # en cada arranque/reconexión. Con probesize=32, FFmpeg empieza a
        # entregar frames con los primeros bytes que llegan del RTSP.
        # CLAVE PARA EL LAG ACUMULATIVO:
        # `-rtbufsize` controla cuántos bytes acumula el demuxer RTSP antes
        # de procesar. Default = 3 MB. Algunas cámaras XiongMai (la tuya
        # incluida) envían en RÁFAGAS por GOP: 50 frames de golpe seguidos
        # de silencio. FFmpeg buffea esos 3 MB (~30 s a tu bitrate) y los
        # entrega al pipe a tasa constante → CADA FRAME que sacamos al
        # encoder es de hasta 30 s antes. La métrica frame_age sólo mide
        # desde el buffer hacia abajo, así que dice "16 ms" mientras el
        # delay real es 30 s antes de entrar al buffer.
        #
        # Bajar a 256 KB = max ~2-3 s de acumulación = el lag tendrá ese
        # cap absoluto. Si la cámara envía más, FFmpeg descarta paquetes
        # viejos (mejor un glitch ocasional que 30 s de delay constante).
        #
        # `-max_delay 0`: tiempo máximo que el demuxer espera para
        # reordenar paquetes. 0 = entrega inmediata.
        #
        # `-buffer_size 32k` (sólo UDP): buffer del socket UDP del kernel.
        # Default es enorme en Windows; lo limitamos a un par de frames.
        extra_low_latency = ["-rtbufsize", "256k", "-max_delay", "0"]
        if self.rtsp_transport == "udp":
            extra_low_latency += ["-buffer_size", "32768"]

        cmd = [
            self._ffmpeg_path,
            "-hide_banner",
            "-loglevel", "error",
            # --- Entrada RTSP ---
            "-rtsp_transport", self.rtsp_transport,
            *rtsp_timeout,
            *extra_low_latency,
            "-probesize", "32",
            "-analyzeduration", "0",
            "-fflags", "nobuffer+flush_packets+discardcorrupt",
            "-flags", "low_delay",
            "-flags2", "+fast",
            "-strict", "experimental",
            "-i", self.rtsp_url,
            # --- Salida raw a rate nativo del decoder ---
            "-vsync", "passthrough",
            *(["-vf", vf_filter] if vf_filter else []),
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "pipe:1",
        ]

        if vf_filter:
            self._logger.info(
                f"FFmpeg: escala {self.original_width}x{self.original_height} "
                f"→ {self.target_width}x{self.target_height}"
            )
        else:
            self._logger.info(
                f"FFmpeg: passthrough {self.original_width}x{self.original_height} "
                f"(target ≥ original, sin escalar)"
            )

        # Aplicar lista negra runtime: si alguna opción se descartó en intentos
        # anteriores por "Option not found", la quitamos.
        cmd = self._strip_unsupported_options(cmd)
        return cmd

    # Cache global de opciones que el ffmpeg local NO soporta (descubiertas
    # en runtime al fallar inmediatamente con "Option X not found").
    _UNSUPPORTED_OPTIONS: set = set()
    _UNSUPPORTED_LOCK = threading.Lock()

    @classmethod
    def _strip_unsupported_options(cls, cmd: list) -> list:
        """
        Elimina del comando cualquier `-flag value` que esté en la lista negra
        de opciones no soportadas (descubiertas en runtime).
        """
        with cls._UNSUPPORTED_LOCK:
            blacklist = set(cls._UNSUPPORTED_OPTIONS)
        if not blacklist:
            return cmd
        out = []
        skip_next = False
        for i, tok in enumerate(cmd):
            if skip_next:
                skip_next = False
                continue
            # Compatibilidad: opciones aparecen como "-flag" seguido de su valor
            if tok.startswith("-") and tok[1:] in blacklist:
                # Quitar también el valor si lo lleva
                if i + 1 < len(cmd) and not cmd[i + 1].startswith("-"):
                    skip_next = True
                continue
            out.append(tok)
        return out

    @classmethod
    def _mark_option_unsupported(cls, option_name: str) -> None:
        with cls._UNSUPPORTED_LOCK:
            cls._UNSUPPORTED_OPTIONS.add(option_name)
        logging.getLogger(__name__).warning(
            f"FFmpeg: '{option_name}' no soportado por este build, "
            f"se quitará en próximos intentos"
        )

    @staticmethod
    def _detect_unsupported_option_from_stderr(stderr_text: str) -> Optional[str]:
        """
        Parsea stderr de ffmpeg buscando 'Option XXX not found' o
        'Unrecognized option YYY'. Devuelve el nombre del flag (sin '-') o None.
        """
        if not stderr_text:
            return None
        # Patrones que hemos visto:
        #   "Option rw_timeout not found."
        #   "Unrecognized option 'stimeout'."
        m = re.search(r"Option\s+(\w+)\s+not\s+found", stderr_text)
        if m:
            return m.group(1)
        m = re.search(r"Unrecognized\s+option\s+['\"]?(\w+)['\"]?", stderr_text)
        if m:
            return m.group(1)
        return None

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
                    stderr_data = self._process.stderr.read(2048).decode("utf-8", errors="ignore")
                    self._logger.error(f"FFmpeg terminó prematuramente: {stderr_data}")
                    # Si fue por una opción no soportada por este build de FFmpeg,
                    # apuntarla en la lista negra para que los próximos intentos
                    # la omitan automáticamente.
                    bad_opt = self._detect_unsupported_option_from_stderr(stderr_data)
                    if bad_opt:
                        self._mark_option_unsupported(bad_opt)
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
                self._running_since = time.time()  # uptime tracker

            self._logger.info(f"FFmpeg corriendo — frames escalados a {self.target_width}x{self.target_height}")

            # Resetear el last_frame_time del metrics_collector también:
            # evita que el stalled monitor cuente desde antes del reconnect
            # y dispare un restart innecesario en los primeros segundos.
            try:
                from backend.app.infrastructure.metrics.collector import metrics_collector
                metrics_collector.update_camera_frame(self.camera_id, timestamp=time.time())
            except Exception:
                pass

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
                        # Backoff: 1s, 2s, 4s, 8s, ... hasta 30s. En LAN la
                        # cámara suele volver al instante; antes era 5*1.5^n
                        # (7.5s en el 1er intento) y eso causaba ~15s sin
                        # frames visibles en el cliente.
                        wait_time = min(2 ** (self._reconnect_attempts - 1), 30)
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
                self._last_error_code = self._last_error_code or "MAX_RETRIES_EXCEEDED"
                code_to_emit = self._last_error_code
            self._logger.error(
                f"Máximos reintentos alcanzados ({self.MAX_RECONNECT}); "
                f"último error: {code_to_emit}. Solicitando auto-desactivación."
            )
            # Notificar al CameraManager para que marque is_active=False y limpie
            # recursos. Sin esto, el stalled monitor seguiría reiniciándonos en
            # bucle infinito cada 15s.
            if self._permanent_failure_callback:
                try:
                    self._permanent_failure_callback(self.camera_id, code_to_emit)
                except Exception as e:
                    self._logger.error(f"Callback de fallo permanente lanzó excepción: {e}")

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