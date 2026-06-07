"""
AIFrameSource — fuente de frames MÍNIMA y dedicada para la IA.

Sustituye a la cadena pesada FFmpegWorker → CircularFrameBuffer →
FrameDistributor → (DualLensSplitter) → InferenceQueue cuando el único
consumidor es la IA.

Diseño:
  - Un solo ffmpeg que lee el SUBSTREAM BAJO del lente desde go2rtc
    (cam_X[_lY]_low, ~360p y ya transcodificado por hardware si GO2RTC_HWACCEL),
    NO la cámara directa → respeta el límite de 1 conexión RTSP y decodifica
    una imagen pequeña (CPU mínima).
  - Salida raw BGR24 a tamaño FIJO (letterbox, sin distorsión) → un único
    "slot" latest-frame (tamaño 1, newest-wins). No hay colas ni fan-out.
  - El crop del lente lo hace go2rtc (cam_X_lY), aquí NO se recorta nada.
  - Reconexión con backoff si ffmpeg/go2rtc cae.

Esto desacopla la IA del pipeline de captura: funciona aunque la cámara no
esté "activa" en CameraManager (la IA ya no necesita su FrameDistributor).
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class AIFrameSource:
    def __init__(self, camera_id: int, lens: Optional[str] = None,
                 width: int = 640, height: int = 384, fps: int = 6,
                 quality: str = "low"):
        self.camera_id = camera_id
        # lens: None/"main" → stream combinado; "l1"/"l2" → lente dual.
        self.lens = None if (lens in (None, "main")) else lens
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.quality = quality

        self._frame_bytes = self.width * self.height * 3
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self._slot: Optional[np.ndarray] = None
        self._seq = 0
        self._lock = threading.Lock()
        self._frames_read = 0
        self._last_frame_t = 0.0

    # ------------------------------------------------------------------
    def _build_url(self) -> Optional[str]:
        try:
            from ...streaming.go2rtc_manager import Go2RtcManager
            return Go2RtcManager().rtsp_restream_url(
                self.camera_id, self.lens, self.quality
            )
        except Exception as e:
            logger.error(f"[AISource cam={self.camera_id}] no pude construir URL go2rtc: {e}")
            return None

    def _ffmpeg_cmd(self, url: str) -> list:
        import shutil
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        # Letterbox a tamaño fijo (sin distorsión) + LÍMITE de tasa con el filtro
        # `fps` (más fiable que `-r` para limitar la salida de un stream en vivo;
        # con `-r` la IA recibía ráfagas de >100fps = trabajo de más).
        vf = (
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
            f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={self.fps}"
        )
        return [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-fflags", "nobuffer", "-flags", "low_delay",
            "-i", url,
            "-an",                      # sin audio
            "-vf", vf,                  # incluye fps={self.fps} → baja tasa real
            "-pix_fmt", "bgr24",
            "-f", "rawvideo", "-",
        ]

    # ------------------------------------------------------------------
    def start(self) -> bool:
        if self._running:
            return True
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name=f"AISource-cam{self.camera_id}", daemon=True
        )
        self._thread.start()
        logger.info(
            f"[AISource cam={self.camera_id} lens={self.lens or 'main'}] iniciado "
            f"({self.width}x{self.height}@{self.fps} desde go2rtc {self.quality})"
        )
        return True

    def _loop(self) -> None:
        backoff = 1.0
        while self._running:
            url = self._build_url()
            if not url:
                time.sleep(2.0)
                continue
            try:
                self._proc = subprocess.Popen(
                    self._ffmpeg_cmd(url),
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    bufsize=self._frame_bytes,
                )
            except Exception as e:
                logger.error(f"[AISource cam={self.camera_id}] no pude lanzar ffmpeg: {e}")
                time.sleep(backoff)
                backoff = min(backoff * 2, 15.0)
                continue

            backoff = 1.0  # conexión OK → reset backoff
            try:
                while self._running:
                    buf = self._read_exact(self._frame_bytes)
                    if buf is None:
                        break  # EOF / proceso muerto → reconectar
                    frame = np.frombuffer(buf, dtype=np.uint8).reshape(
                        (self.height, self.width, 3)
                    )
                    with self._lock:
                        self._slot = frame
                        self._seq += 1
                        self._frames_read += 1
                        self._last_frame_t = time.time()
            finally:
                self._kill_proc()

            if self._running:
                logger.warning(
                    f"[AISource cam={self.camera_id}] stream cortado; reconectando…"
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 15.0)

    def _read_exact(self, n: int) -> Optional[bytes]:
        if not self._proc or not self._proc.stdout:
            return None
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = self._proc.stdout.read(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _kill_proc(self) -> None:
        try:
            if self._proc:
                self._proc.kill()
                self._proc.wait(timeout=3)
        except Exception:
            pass
        self._proc = None

    # ------------------------------------------------------------------
    def get_latest(self) -> Tuple[int, Optional[np.ndarray]]:
        """Devuelve (seq, frame). frame es None si aún no hay. seq permite al
        consumidor saber si el frame es nuevo (evitar reprocesar el mismo)."""
        with self._lock:
            return self._seq, self._slot

    def is_alive(self) -> bool:
        return self._running and (time.time() - self._last_frame_t) < 10.0

    def stop(self) -> None:
        self._running = False
        self._kill_proc()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        with self._lock:
            self._slot = None
        logger.info(f"[AISource cam={self.camera_id}] detenido "
                    f"(frames leídos={self._frames_read})")
