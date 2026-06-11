"""
TelemetryRecorder — telemetría integrada del backend.

Arranca con el backend, muestrea TODOS los módulos en uso cada N segundos y
escribe a disco FILA A FILA con flush → los datos sobreviven a un crash o a
Ctrl-C (cada muestra ya está en disco; no se pierde nada salvo lo posterior a
la última muestra).

Mide:
  - Sistema: CPU%, RAM.
  - Proceso backend (python + hijos) vs go2rtc (proceso + transcoders), CPU/RAM.
  - go2rtc: nº de streams con productor y nº total de consumidores (clientes).
  - IA: schedulers activos, frames recibidos/procesados, latencia de inferencia,
    si la fuente está viva.
  - Notificaciones: clientes WebSocket conectados.
  - GlobalExecutor: tareas enviadas/completadas/activas.
  - Grabación: nº de grabaciones continuas activas.
  - Hilos totales del proceso.

Salida (carpeta `telemetry/` por defecto, o TELEMETRY_PATH):
  - telemetry_<fecha>.csv   → una fila por muestra (para que me lo pases y lo revise).
  - telemetry_<fecha>.jsonl → snapshot rico cada ~15s (go2rtc streams + IA detalle).

Activado por defecto (TELEMETRY_ENABLED=true). No interfiere con el Ctrl-C de
werkzeug (no registra handlers de señal): confía en el flush por fila + atexit +
el cierre desde main.py.

--------------------------------------------------------------------------------
FICHA DE MÓDULO (referencia rápida)
    PROPÓSITO: caja negra de observabilidad del backend en producción; deja un
        rastro a disco de carga (CPU/RAM), actividad de go2rtc, IA, grabación y
        executor para diagnosticar problemas a posteriori.
    RESPONSABILIDAD: muestrear sin bloquear y persistir a prueba de crashes; NO
        toma decisiones ni altera el sistema (solo observa).
    DEPENDENCIAS: psutil (CPU/RAM por proceso); urllib (API HTTP de go2rtc);
        DependencyContainer (ai_service, recording_manager — SOLO si ya construido);
        core.executor, notifications.ws_broker, config.settings.
    COMPONENTES RELACIONADOS: main.py (lo arranca en paso 12 y lo detiene primero
        en el finally); MetricsCollector (métrica de salud "en vivo", complementaria);
        go2rtc_manager (fuente de streams/consumidores).
    PUNTO DE ENTRADA: singleton global `telemetry_recorder` (al final del módulo).
    PIPELINE(S): transversal de observabilidad; arranca en Pipeline #1 (Inicio).
--------------------------------------------------------------------------------
"""
from __future__ import annotations

import atexit
import csv
import json
import logging
import os
import threading
import time
from typing import Optional

import psutil

logger = logging.getLogger(__name__)

# Columnas del CSV (orden fijo): una fila por muestra. Agrupadas por subsistema:
# tiempo, sistema, procesos backend vs go2rtc, conteo de ffmpeg, streams/consumidores
# de go2rtc, IA (activos/frames/latencia/fuente viva), WebSocket, executor,
# grabaciones activas y nº de hilos del proceso. Si se añade un campo, añadirlo
# aquí (los faltantes se rellenan vacíos en `_loop` por seguridad).
_CSV_FIELDS = [
    "t", "clock",
    "sys_cpu", "sys_ram_pct", "sys_ram_used_mb",
    "backend_cpu", "backend_ram_mb",
    "go2rtc_cpu", "go2rtc_ram_mb", "go2rtc_procs",
    "ffmpeg_procs",
    "go2rtc_streams_live", "go2rtc_consumers",
    "ai_active", "ai_frames_recv", "ai_frames_proc",
    "ai_infer_ms_avg", "ai_src_alive",
    "ws_clients",
    "exec_active", "exec_submitted", "exec_completed",
    "recordings_active",
    "threads",
]


class TelemetryRecorder:
    """
    SINGLETON grabador de telemetría a disco (arranca en main, paso 12).

    ROL: muestrea periódicamente el estado de todos los subsistemas y lo escribe
    fila-a-fila (CSV + snapshots JSONL) con flush/fsync → caja negra resistente a
    crashes. Es OBSERVADOR puro: no modifica nada del sistema.

    SINGLETON (patrón `__new__` con doble-check lock): una instancia por proceso;
    `_init()` corre una sola vez. Se expone como `telemetry_recorder` al final del
    módulo. Lo arranca/detiene main.py; nadie más debería instanciarlo.

    HILOS: usa DOS hilos daemon —
        `_loop`        : muestreo principal cada `interval` s (escribe CSV/JSONL).
        `_go2rtc_loop` : refresca en caché la consulta HTTP a go2rtc cada ~3s, AISLADA
                         para que un go2rtc lento/caído no frene el muestreo principal.

    DEPENDENCIAS: psutil; container (ai_service/recording_manager solo si ya
    construido — no lo fuerza para no cargar torch desde aquí); executor; ws_broker.

    PIPELINE: transversal de observabilidad (arranca en Pipeline #1).
    """
    _instance: Optional["TelemetryRecorder"] = None
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
        self._thread: Optional[threading.Thread] = None
        self._g2r_thread: Optional[threading.Thread] = None
        self._csv_file = None
        self._csv_writer = None
        self._jsonl_file = None
        self._t0 = time.time()
        self._proc_cache: dict[int, psutil.Process] = {}
        self._rows = 0
        self.csv_path = ""
        self.jsonl_path = ""
        # Cache de la consulta a go2rtc (HTTP) para no frenar la cadencia si
        # go2rtc tarda/cae: se refresca como mucho cada 4s.
        self._g2r_ts = 0.0
        self._g2r_cache: tuple = (0, 0, {})

    # ------------------------------------------------------------------
    def start(self, interval: float = 2.0, out_dir: str = "") -> bool:
        """
        Propósito: abrir los ficheros de salida y lanzar los dos hilos de muestreo.
            Etapa: Pipeline #1 (lo llama main, paso 12). Idempotente (no-op si ya corre).
        Inputs: interval (s entre muestras, clamp a >=0.5); out_dir (default
            `<repo>/telemetry/`, o TELEMETRY_PATH del .env).
        Outputs: bool — True si arrancó (o ya estaba), False si falló al abrir ficheros.
        Efectos: crea telemetry_<fecha>.csv + .jsonl, registra `stop` en atexit.
        Excepciones: capturadas → devuelve False y loguea.
        Llamado por: main.py. Llama a: threading (hilos _loop y _go2rtc_loop).
        """
        if self._running:
            return True
        try:
            if not out_dir:
                repo = os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__)))))
                out_dir = os.path.join(repo, "telemetry")
            os.makedirs(out_dir, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            self.csv_path = os.path.join(out_dir, f"telemetry_{stamp}.csv")
            self.jsonl_path = os.path.join(out_dir, f"telemetry_{stamp}.jsonl")
            self._csv_file = open(self.csv_path, "w", newline="", encoding="utf-8")
            self._csv_writer = csv.DictWriter(self._csv_file, fieldnames=_CSV_FIELDS)
            self._csv_writer.writeheader()
            self._csv_file.flush()
            self._jsonl_file = open(self.jsonl_path, "w", encoding="utf-8")
            self._interval = max(0.5, float(interval))
            self._t0 = time.time()
            self._running = True
            self._thread = threading.Thread(target=self._loop, name="Telemetry", daemon=True)
            self._thread.start()
            self._g2r_thread = threading.Thread(
                target=self._go2rtc_loop, name="Telemetry-go2rtc", daemon=True)
            self._g2r_thread.start()
            atexit.register(self.stop)
            logger.info(f"Telemetría iniciada (cada {self._interval}s) → {self.csv_path}")
            return True
        except Exception as e:
            logger.error(f"No se pudo iniciar telemetría: {e}")
            return False

    # ------------------------------------------------------------------
    def _proc(self, pid: int) -> Optional[psutil.Process]:
        p = self._proc_cache.get(pid)
        if p is None:
            try:
                p = psutil.Process(pid)
                p.cpu_percent(None)  # ceba (1ª lectura = 0)
                self._proc_cache[pid] = p
            except psutil.Error:
                return None
        return p

    def _sample_processes(self) -> dict:
        """CPU/RAM separando go2rtc (proceso + transcoders) del resto del árbol
        del backend (python + ffmpeg de IA/grabación)."""
        out = {"backend_cpu": 0.0, "backend_ram_mb": 0.0, "go2rtc_cpu": 0.0,
               "go2rtc_ram_mb": 0.0, "go2rtc_procs": 0, "ffmpeg_procs": 0}
        try:
            me = psutil.Process(os.getpid())
            kids = me.children(recursive=True)
        except psutil.Error:
            return out

        # PIDs del subárbol de go2rtc (go2rtc.exe + sus transcoders ffmpeg)
        go2rtc_pids: set[int] = set()
        for k in kids:
            try:
                if "go2rtc" in (k.name() or "").lower():
                    go2rtc_pids.add(k.pid)
                    for gk in k.children(recursive=True):
                        go2rtc_pids.add(gk.pid)
            except psutil.Error:
                pass

        all_procs = [me] + kids
        for p in all_procs:
            pp = self._proc(p.pid)
            if pp is None:
                continue
            try:
                cpu = pp.cpu_percent(None)
                ram = pp.memory_info().rss / 1e6
                name = (p.name() or "").lower()
            except psutil.Error:
                continue
            if "ffmpeg" in name:
                out["ffmpeg_procs"] += 1
            if p.pid in go2rtc_pids:
                out["go2rtc_cpu"] += cpu
                out["go2rtc_ram_mb"] += ram
                out["go2rtc_procs"] += 1
            else:
                out["backend_cpu"] += cpu
                out["backend_ram_mb"] += ram

        out["backend_cpu"] = round(out["backend_cpu"], 1)
        out["backend_ram_mb"] = round(out["backend_ram_mb"])
        out["go2rtc_cpu"] = round(out["go2rtc_cpu"], 1)
        out["go2rtc_ram_mb"] = round(out["go2rtc_ram_mb"])
        return out

    def _go2rtc_loop(self) -> None:
        """Hilo SEPARADO que refresca la caché de go2rtc cada ~3s. Aislar la
        consulta HTTP aquí evita que, si go2rtc cae (connect lento en Windows),
        se frene el loop de muestreo principal."""
        while self._running:
            try:
                import urllib.request
                from ..config import settings
                port = getattr(settings, "GO2RTC_API_PORT", 1984)
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/streams", timeout=2.0
                ) as r:
                    data = json.loads(r.read().decode())
                live = consumers = 0
                for info in (data or {}).values():
                    if info.get("producers"):
                        live += 1
                    consumers += len(info.get("consumers") or [])
                self._g2r_cache = (live, consumers, (data or {}))
            except Exception:
                self._g2r_cache = (0, 0, {})
            time.sleep(3.0)

    def _container(self):
        """Devuelve el contenedor SOLO si ya está construido (no lo fuerza —
        construirlo desde el hilo de telemetría cargaría torch y bloquearía)."""
        try:
            from ..container import DependencyContainer
            if DependencyContainer._instance is not None and DependencyContainer._initialized:
                return DependencyContainer._instance
        except Exception:
            pass
        return None

    def _sample_ai(self) -> tuple[dict, dict]:
        try:
            c = self._container()
            ai = c.get("ai_service") if c is not None else None
            if ai is None:
                return {"ai_active": 0, "ai_frames_recv": 0, "ai_frames_proc": 0,
                        "ai_infer_ms_avg": 0.0, "ai_src_alive": 0}, {}
            status = ai.get_ai_status()
            scheds = status.get("schedulers", {})
            recv = proc = 0
            infer = []
            alive = 0
            for st in scheds.values():
                recv += st.get("frames_received", 0)
                proc += st.get("frames_processed", 0)
                if st.get("inference_ms_avg"):
                    infer.append(st["inference_ms_avg"])
                if st.get("frame_source_alive"):
                    alive += 1
            return {
                "ai_active": status.get("active_count", 0),
                "ai_frames_recv": recv,
                "ai_frames_proc": proc,
                "ai_infer_ms_avg": round(sum(infer) / len(infer), 1) if infer else 0.0,
                "ai_src_alive": alive,
            }, status
        except Exception:
            return {"ai_active": 0, "ai_frames_recv": 0, "ai_frames_proc": 0,
                    "ai_infer_ms_avg": 0.0, "ai_src_alive": 0}, {}

    def _sample_misc(self) -> dict:
        out = {"ws_clients": 0, "exec_active": 0, "exec_submitted": 0,
               "exec_completed": 0, "recordings_active": 0, "threads": 0}
        out["threads"] = threading.active_count()
        try:
            from ..notifications.ws_broker import ws_broker
            out["ws_clients"] = ws_broker.client_count()
        except Exception:
            pass
        try:
            from ..core.executor import global_executor
            st = global_executor.get_stats()
            out["exec_active"] = st.get("active_threads", 0)
            out["exec_submitted"] = st.get("submitted_tasks", 0)
            out["exec_completed"] = st.get("completed_tasks", 0)
        except Exception:
            pass
        try:
            c = self._container()
            rec = c.get("recording_manager") if c is not None else None
            if rec is not None:
                ct = getattr(rec, "_continuous_threads", None)
                if isinstance(ct, dict):
                    out["recordings_active"] = len(ct)
        except Exception:
            pass
        return out

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        next_jsonl = 0.0
        while self._running:
            try:
                t = round(time.time() - self._t0, 1)
                row = {"t": t, "clock": time.strftime("%H:%M:%S")}
                row["sys_cpu"] = psutil.cpu_percent(None)
                vm = psutil.virtual_memory()
                row["sys_ram_pct"] = vm.percent
                row["sys_ram_used_mb"] = round(vm.used / 1e6)
                row.update(self._sample_processes())
                live, consumers, raw_streams = self._g2r_cache  # actualizado por _go2rtc_loop
                row["go2rtc_streams_live"] = live
                row["go2rtc_consumers"] = consumers
                ai_row, ai_full = self._sample_ai()
                row.update(ai_row)
                row.update(self._sample_misc())

                # Completar campos faltantes por seguridad
                for f in _CSV_FIELDS:
                    row.setdefault(f, "")
                self._csv_writer.writerow(row)
                self._csv_file.flush()
                self._rows += 1
                if self._rows % 10 == 0:
                    try:
                        os.fsync(self._csv_file.fileno())
                    except Exception:
                        pass

                # Snapshot rico cada ~15s
                if t >= next_jsonl:
                    next_jsonl = t + 15
                    snap = {"t": t, "clock": row["clock"],
                            "ai": ai_full, "go2rtc_streams": raw_streams}
                    self._jsonl_file.write(json.dumps(snap, default=str) + "\n")
                    self._jsonl_file.flush()
            except Exception as e:
                logger.debug(f"telemetría muestra falló: {e}")
            time.sleep(self._interval)

    # ------------------------------------------------------------------
    def stop(self) -> None:
        """
        Propósito: apagado ordenado — baja `_running`, hace join de ambos hilos
            (salvo si se llama desde uno de ellos, p.ej. atexit), y cierra los
            ficheros con flush+fsync para no perder la última muestra. Etapa:
            Pipeline #1 (main lo invoca PRIMERO en el finally; también vía atexit).
        Inputs/Outputs: ninguno. Idempotente. Excepciones: capturadas al cerrar.
        Llamado por: main.py (finally) y atexit.
        """
        if not self._running:
            return
        self._running = False
        for th in (self._thread, self._g2r_thread):
            if th and th.is_alive() and threading.current_thread() is not th:
                th.join(timeout=self._interval + 1)
        for fh in (self._csv_file, self._jsonl_file):
            try:
                if fh:
                    fh.flush()
                    os.fsync(fh.fileno())
                    fh.close()
            except Exception:
                pass
        logger.info(f"Telemetría detenida ({self._rows} muestras) → {self.csv_path}")


telemetry_recorder = TelemetryRecorder()
