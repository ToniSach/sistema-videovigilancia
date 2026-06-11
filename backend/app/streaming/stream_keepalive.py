"""
================================================================================
MÓDULO: stream_keepalive — Mantiene CALIENTES los streams de directo de go2rtc
================================================================================

PROPÓSITO
    Evitar el "arranque en frío" del directo: mantener vivo el transcoder de
    go2rtc de UN stream por cámara con un consumidor ligero (ffmpeg -c copy),
    para que al abrir el directo el primer frame llegue casi instantáneo.

RESPONSABILIDAD PRINCIPAL
    Derivar de la BD el conjunto de streams a mantener calientes (1 por cámara
    activa), arrancar/parar un worker ffmpeg por stream y reconciliar ese
    conjunto cada 15s ante altas/bajas de cámaras — sin que nadie lo invoque.

PIPELINES EN LOS QUE PARTICIPA
    #3  Live ..... reduce la latencia de apertura del directo (su único fin).
    #5  go2rtc ... mantiene ocupado el transcoder de cada stream de go2rtc.

DEPENDENCIAS
    go2rtc_manager .... stream_name() / lens_stream_name() (nombres de stream).
    database.connection / models.Camera — deriva los streams de las cámaras.
    ffmpeg (PATH) ..... consumidor ligero por stream (-c copy → ≈0% CPU).

COMPONENTES RELACIONADOS
    Go2RtcManager — produce los streams que este componente mantiene calientes.
    main.py ....... arranca (start) y detiene (stop) el keep-alive.

PUNTO DE ENTRADA
    Singleton `stream_keepalive`. Métodos: start(rtsp_base, env_extra) / stop().

--------------------------------------------------------------------------------
StreamKeepAlive — mantiene CALIENTES los streams de directo de go2rtc.

Problema: go2rtc arranca el transcoder de un stream de forma PEREZOSA (solo
cuando alguien lo consume) y lo para cuando no hay consumidores. Por eso, al
abrir/cambiar a un lente o calidad, se paga el "arranque en frío" (~3.5-5s:
init QSV + esperar keyframe).

Solución: este componente mantiene un consumidor LIGERO (`ffmpeg -c copy -f null`,
≈0% CPU porque NO decodifica) por cada stream → su transcoder de go2rtc queda
corriendo → al abrirlo, el primer frame es casi instantáneo.

MODO DINÁMICO (desde BD): en vez de una lista fija, el conjunto de streams a
mantener calientes se DERIVA de las cámaras registradas en la base de datos. Un
bucle de reconciliación (cada 15s) lo recalcula, de modo que:
  - Al REGISTRAR/activar una cámara → se mantiene caliente automáticamente.
  - Al desactivar/borrar una cámara → se libera su keep-alive.
Se mantienen los streams que la app abre en el mosaico, según el tipo de cámara:
  - mono      → cam_<id>_medium                      (un solo stream)
  - dual-lens → cam_<id>_l1_medium Y cam_<id>_l2_medium  (AMBOS lentes)
Calidad "medium" porque es la única calidad de visualización de la app. Se
calientan los DOS lentes porque el directo dual-lens muestra ambos a la vez; si
solo se calentaba l1, el L2 arrancaba en frío y se quedaba en "Conectando…".

COSTE: cada stream caliente = su transcoder QSV corriendo 24/7. Para N cámaras
mono son N transcodes; para N dual-lens son 2N (un encode 480p por lente). En la
iGPU (QSV) un encode 480p es barato; sin QSV (libx264) pesa más. NO usa disco
(consumidor `-c copy -f null`, no graba nada). `STREAM_KEEPALIVE` (env) se
conserva como lista EXTRA opcional que se fusiona con la derivada de la BD.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from typing import List, Optional

from .go2rtc_manager import stream_name, lens_stream_name

logger = logging.getLogger(__name__)

# Calidad y lente por defecto del keep-alive (lo que la app abre por defecto).
_KEEPALIVE_QUALITY = "medium"
_DEFAULT_LENS = "l1"
# Cada cuánto se reconcilia el conjunto de streams contra la BD.
_RECONCILE_SECONDS = 15


class StreamKeepAlive:
    """Singleton (proceso único) que mantiene calientes los streams de go2rtc.

    ROL: por cada cámara activa de la BD, corre un worker ffmpeg ligero que
    consume su stream "medium" (-c copy, sin decodificar) para que el transcoder
    de go2rtc no se apague. Un hilo reconciliador recalcula el conjunto cada 15s.
    QUIÉN LO USA: main.py lo arranca/detiene; nadie más lo invoca (autónomo).
    SINGLETON: retiene en memoria los subprocesos ffmpeg y sus hilos; por eso el
    backend debe ser un único proceso (ver CLAUDE.md).
    ESTADO INTERNO: _procs (name→Popen), _threads (name→hilo), _active (deseados),
    protegidos por _lock; _env_extra son streams EXTRA opcionales (STREAM_KEEPALIVE).
    """

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
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}   # name -> proceso ffmpeg
        self._threads: dict[str, threading.Thread] = {}  # name -> hilo keep-alive
        self._active: set[str] = set()                   # streams deseados/vivos
        self._rtsp_base = "rtsp://127.0.0.1:8554"
        self._env_extra: List[str] = []
        self._reconciler: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    def start(self, rtsp_base: str = "", env_extra: Optional[List[str]] = None) -> bool:
        """Arranca el keep-alive en modo dinámico (deriva los streams de la BD).

        rtsp_base: base RTSP de go2rtc (p. ej. rtsp://127.0.0.1:8554).
        env_extra: streams EXTRA a mantener calientes además de los de la BD
                   (de la variable STREAM_KEEPALIVE; opcional).
        """
        if self._running:
            return True
        if not shutil.which("ffmpeg"):
            logger.warning("StreamKeepAlive: ffmpeg no encontrado; desactivado.")
            return False
        if rtsp_base:
            self._rtsp_base = rtsp_base.rstrip("/")
        self._env_extra = [s.strip() for s in (env_extra or []) if s.strip()]
        self._running = True
        self._reconciler = threading.Thread(
            target=self._reconcile_loop, name="KeepAliveReconciler", daemon=True
        )
        self._reconciler.start()
        logger.info(
            "StreamKeepAlive iniciado (modo dinámico desde BD; extra=%s)",
            self._env_extra or "—",
        )
        return True

    # ------------------------------------------------------------------
    # Derivación del conjunto deseado desde la BD
    # ------------------------------------------------------------------
    def _streams_for_camera(self, cam_id: int, is_dual: bool) -> List[str]:
        """Nombres de stream de directo a mantener calientes para una cámara.

        Dual-lens: AMBOS lentes (l1 Y l2). Si solo se calentaba l1, el L2
        arrancaba en frío cada vez que se abría el directo y se quedaba en
        "Conectando…" (su transcoder de go2rtc no estaba corriendo). Mono: un
        único stream.
        """
        if is_dual:
            return [
                f"{lens_stream_name(cam_id, 'l1')}_{_KEEPALIVE_QUALITY}",
                f"{lens_stream_name(cam_id, 'l2')}_{_KEEPALIVE_QUALITY}",
            ]
        return [f"{stream_name(cam_id)}_{_KEEPALIVE_QUALITY}"]

    def _streams_from_db(self) -> List[str]:
        """Streams de keep-alive de las cámaras ACTIVAS de la BD.

        1 stream por cámara mono; 2 (l1 y l2) por cámara dual-lens.
        """
        out: List[str] = []
        try:
            from ..database.connection import db_manager
            from ..database.models import Camera
            with db_manager.get_session() as session:
                rows = session.query(
                    Camera.id, Camera.is_active, Camera.is_dual_lens
                ).all()
            for cid, active, dual in rows:
                if not active:
                    continue
                out.extend(self._streams_for_camera(cid, bool(dual)))
        except Exception as e:
            logger.debug("StreamKeepAlive: no pude leer cámaras de BD: %s", e)
        return out

    def _desired_streams(self) -> set[str]:
        return set(self._env_extra) | set(self._streams_from_db())

    def _reconcile_loop(self) -> None:
        while self._running:
            try:
                desired = self._desired_streams()
                with self._lock:
                    current = set(self._threads.keys())
                for name in desired - current:
                    self._start_worker(name)
                for name in current - desired:
                    self._stop_worker(name)
            except Exception as e:
                logger.debug("StreamKeepAlive reconcile: %s", e)
            for _ in range(_RECONCILE_SECONDS):
                if not self._running:
                    break
                time.sleep(1)

    # ------------------------------------------------------------------
    # Gestión de workers por stream
    # ------------------------------------------------------------------
    def _start_worker(self, name: str) -> None:
        with self._lock:
            if name in self._threads:
                return
            self._active.add(name)
            t = threading.Thread(
                target=self._keepalive_loop, args=(name,),
                name=f"KeepAlive-{name}", daemon=True,
            )
            self._threads[name] = t
        t.start()
        logger.info("StreamKeepAlive: manteniendo caliente %s", name)

    def _stop_worker(self, name: str) -> None:
        with self._lock:
            self._active.discard(name)
            proc = self._procs.pop(name, None)
            self._threads.pop(name, None)
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        logger.info("StreamKeepAlive: liberado %s (cámara ya no activa)", name)

    def _keepalive_loop(self, name: str) -> None:
        """Worker por stream: lanza un ffmpeg `-c copy -f null` y lo re-lanza.

        Mantiene un único consumidor RTSP vivo contra go2rtc (8554) para que su
        transcoder no se apague. `-c copy` → no decodifica (≈0% CPU). Si el
        proceso cae (go2rtc reinició, red), espera `backoff` y reconecta, hasta
        que stop() o el reconciliador marquen el stream como no deseado.
        Corre en su propio hilo (uno por stream); llamado por _start_worker.
        """
        url = f"{self._rtsp_base}/{name}"
        backoff = 2.0
        while self._running and name in self._active:
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
                logger.debug("KeepAlive %s: %s", name, e)
            if self._running and name in self._active:
                time.sleep(backoff)  # reconectar (go2rtc reinició, etc.)

    # ------------------------------------------------------------------
    def stop(self) -> None:
        """Apagado: detiene el reconciliador y mata todos los ffmpeg de keep-alive.

        Llamado por el `finally` de main.py. Idempotente (no-op si no corría).
        """
        if not self._running:
            return
        self._running = False
        with self._lock:
            procs = list(self._procs.values())
            self._procs.clear()
            self._threads.clear()
            self._active.clear()
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
        logger.info("StreamKeepAlive detenido.")


stream_keepalive = StreamKeepAlive()
