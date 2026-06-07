"""
StreamKeepAlive — mantiene CALIENTES ciertos streams de go2rtc.

Problema: go2rtc arranca el transcoder de un stream de forma PEREZOSA (solo
cuando alguien lo consume) y lo para cuando no hay consumidores. Por eso, al
abrir/cambiar a un lente o calidad, se paga el "arranque en frío" (~3.5-5s:
init QSV + esperar keyframe).

Solución: este componente mantiene un consumidor LIGERO (`ffmpeg -c copy -f null`,
≈0% CPU porque NO decodifica) por cada stream listado → el transcoder de ese
stream queda corriendo → al cambiar a él, el primer frame es casi instantáneo.

COSTE: cada stream que se mantiene caliente = su transcoder QSV corriendo todo el
tiempo. NO los actives todos (saturarías la iGPU): mantén solo los que usas
(p. ej. la calidad media de cada lente). Configurable por STREAM_KEEPALIVE.
Vacío = desactivado (comportamiento actual).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from typing import List, Optional

logger = logging.getLogger(__name__)


class StreamKeepAlive:
    _instance: Optional["StreamKeepAlive"] = None
    _new_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._new_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self):
        self._running = False
        self._threads: List[threading.Thread] = []
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()
        self._streams: List[str] = []
        self._rtsp_base = "rtsp://127.0.0.1:8554"

    # ------------------------------------------------------------------
    def start(self, streams: List[str], rtsp_base: str = "") -> bool:
        """streams: nombres de stream go2rtc a mantener calientes (ej.
        ['cam_9_l1_medium','cam_9_l2_medium']). rtsp_base opcional."""
        streams = [s.strip() for s in (streams or []) if s.strip()]
        if not streams:
            logger.info("StreamKeepAlive: sin streams configurados (desactivado).")
            return False
        if self._running:
            return True
        if rtsp_base:
            self._rtsp_base = rtsp_base.rstrip("/")
        if not shutil.which("ffmpeg"):
            logger.warning("StreamKeepAlive: ffmpeg no encontrado; desactivado.")
            return False

        self._streams = streams
        self._running = True
        for name in streams:
            t = threading.Thread(
                target=self._keepalive_loop, args=(name,),
                name=f"KeepAlive-{name}", daemon=True,
            )
            t.start()
            self._threads.append(t)
        logger.info(f"StreamKeepAlive iniciado para {len(streams)} stream(s): {streams}")
        return True

    def _keepalive_loop(self, name: str) -> None:
        url = f"{self._rtsp_base}/{name}"
        backoff = 2.0
        while self._running:
            try:
                # -c copy → NO decodifica (consumidor ≈0% CPU); solo mantiene
                # vivo el transcoder de go2rtc que produce 'name'.
                proc = subprocess.Popen(
                    ["ffmpeg", "-hide_banner", "-loglevel", "error",
                     "-rtsp_transport", "tcp", "-i", url,
                     "-c", "copy", "-f", "null", "-"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                with self._lock:
                    self._procs[name] = proc
                proc.wait()  # corre hasta que el stream caiga o lo matemos
            except Exception as e:
                logger.debug(f"KeepAlive {name}: {e}")
            if self._running:
                time.sleep(backoff)  # reconectar (go2rtc reinició, etc.)

    # ------------------------------------------------------------------
    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        with self._lock:
            procs = list(self._procs.values())
            self._procs.clear()
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
        logger.info("StreamKeepAlive detenido.")


stream_keepalive = StreamKeepAlive()
