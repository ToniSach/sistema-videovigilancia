"""
================================================================================
MÓDULO: infrastructure.metrics.collector — Métricas de salud + auto-restart
================================================================================

PROPÓSITO
    Recolector central de métricas del NVR/VMS, thread-safe y DESACOPLADO:
    los PRODUCTORES (workers de cámara, FrameDistributor) reportan frames sin
    conocer a los CONSUMIDORES (endpoint /health, telemetría, UI). Elimina la
    dependencia circular que existía entre FrameDistributor y las rutas de la API.

RESPONSABILIDAD PRINCIPAL
    1. Mantener métricas por cámara (último frame, FPS real, bytes, errores).
    2. Muestrear métricas globales del sistema (CPU/RAM/disco) en un hilo de fondo.
    3. Servir el estado de salud agregado a `GET /api/v1/health`.
    4. Detectar cámaras CONGELADAS (sin frames > umbral) y reiniciarlas
       automáticamente vía el `stalled monitor` (auto-restart / self-healing).

DEPENDENCIAS
    psutil ........................ CPU/RAM/disco del proceso y el host.
    config.settings ............... RECORDINGS_PATH (disco a medir).
    cameras.camera_manager ........ se le pasa a `check_stalled_cameras` para
                                    consultar/reiniciar workers (no se importa
                                    directamente: inversión de control).
    events.event_manager .......... emite `camera_offline` al reiniciar una cámara.

COMPONENTES RELACIONADOS
    main.py ....................... lo arranca (paso 11/13): el singleton se
                                    autoinicia al importarse y main lanza el
                                    monitor con `start_stalled_monitor()`.
    telemetry.py .................. consumidor: muestrea estas métricas a disco.
    infrastructure/metrics/__init__ exporta el singleton.

PUNTO DE ENTRADA
    `from ...infrastructure.metrics.collector import metrics_collector`
    (instancia singleton global creada al final del módulo).

PIPELINE(S) + ETAPA
    - Pipeline #1 (Inicio): el singleton se autoinicia y main arranca el monitor.
    - Pipeline #3 (Live) / #11 (Grabación): recibe `update_camera_frame()` de los
      consumidores del FrameDistributor → mide FPS y frescura por cámara.
    - Pipeline #10 (Eventos): al detectar congelación emite `camera_offline`.
    - Transversal: alimenta /health y la telemetría (observabilidad).

NOTA DE AUTO-CURACIÓN (defensa en capas contra cámaras colgadas)
    1ª línea: el watchdog INTERNO de cada worker (WATCHDOG_TIMEOUT=30s).
    2ª línea: este `stalled monitor` (umbral 25s en main) como red de seguridad.
    El monitor evita interferir con reconexiones en curso (ver
    `check_stalled_cameras`): respeta workers reconnecting/starting/error,
    auto-desactivados y recién arrancados (grace period).
================================================================================
"""
import threading
import time
import logging
import psutil
from typing import Dict, Optional
from dataclasses import dataclass, field
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class CameraMetrics:
    """
    Métricas individuales acumuladas por cámara (estado mutable thread-safe).

    ROL: contenedor de datos vivos de UNA cámara. Lo crea/posee `MetricsCollector`
    (un dict camera_id → CameraMetrics). `update_frame` se llama desde el hilo
    consumidor del FrameDistributor por cada frame; la lectura ocurre desde el
    hilo de /health y desde el stalled monitor. Tiene su propio `_lock` para que
    la escritura por-frame no bloquee el RLock global del collector.

    FPS REAL: en vez de promediar, cuenta cuántos timestamps caen en la última
    ventana de 1s (deslizante) → FPS instantáneo robusto a ráfagas/GOP.
    """
    last_frame_time: float = 0.0
    frame_count: int = 0
    fps: float = 0.0
    bytes_processed: int = 0
    errors: int = 0
    # Historial para cálculo real de FPS
    frame_timestamps: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    def update_frame(self, timestamp: float, frame_size: int = 0):
        with self._lock:
            self.last_frame_time = timestamp
            self.frame_count += 1
            self.bytes_processed += frame_size
            
            # Mantener últimos 30 timestamps para calcular FPS real
            self.frame_timestamps.append(timestamp)
            # Limpiar timestamps mayores a 1 segundo
            cutoff = timestamp - 1.0
            self.frame_timestamps = [t for t in self.frame_timestamps if t > cutoff]
            self.fps = len(self.frame_timestamps)


@dataclass
class SystemMetrics:
    """
    Snapshot de métricas globales del host (no por cámara). Lo rellena el hilo
    `_update_system_metrics` del collector cada 2s y lo leen /health y telemetría.
    Cacheado: la lectura nunca bloquea esperando a psutil.
    """
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    disk_percent: float = 0.0
    timestamp: float = 0.0


class MetricsCollector:
    """
    SINGLETON de recolección de métricas thread-safe (clave en main, paso 11/13).

    SINGLETON (patrón `__new__` con doble-check lock): UNA sola instancia por
    proceso, porque las métricas viven en memoria de proceso (coherente con la
    restricción de proceso único del backend). Se crea al importar el módulo
    (`metrics_collector` al final) y se AUTOINICIA: arranca de inmediato el hilo
    de fondo que muestrea CPU/RAM/disco. El monitor de cámaras congeladas NO
    arranca solo: lo lanza main vía `start_stalled_monitor()`.

    QUIÉN LO INSTANCIA/CONSUME
        - Instancia: nadie con `new`; se usa el singleton global `metrics_collector`.
        - Productores: consumidores del FrameDistributor llaman `update_camera_frame`;
          CameraManager llama `register_camera` / `unregister_camera`.
        - Consumidores: `health_check` en main (`get_health_status`), telemetry.py,
          y el propio stalled monitor.

    DEPENDENCIAS: psutil (sistema), config.settings (ruta de disco), y por
    inversión de control un `camera_manager` que recibe como parámetro para
    consultar/reiniciar workers (no lo importa para evitar acoplamiento).

    PIPELINE: transversal de observabilidad + auto-curación de cámaras
    (Pipelines #1, #3/#11 y #10).
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
            
        self._initialized = True
        self._camera_metrics: Dict[int, CameraMetrics] = defaultdict(CameraMetrics)
        self._system_metrics = SystemMetrics()
        self._lock = threading.RLock()

        # Disco a medir: el de la carpeta de GRABACIONES (no la raíz "/", que en
        # Windows ni siquiera es válida y mide una unidad distinta a la del disco
        # donde se guardan los vídeos → disk_percent engañoso). Se resuelve a una
        # ruta absoluta existente; si falla, cae a la unidad del proceso.
        self._disk_path = self._resolve_disk_path()
        # Momento de arranque para calcular uptime del servicio.
        self._start_time = time.time()
        
        # Thread de actualización de métricas de sistema (no bloqueante)
        self._running = True
        self._system_thread = threading.Thread(target=self._update_system_metrics, daemon=True)
        self._system_thread.start()
        
        logger.info("MetricsCollector inicializado")
    
    def update_camera_frame(self, camera_id: int, timestamp: Optional[float] = None,
                           frame_size: int = 0) -> None:
        """
        Propósito: registrar que llegó un frame de una cámara (alimenta FPS real,
            frescura y bytes). Etapa: Pipeline #3/#11, lado consumidor.
        Inputs: camera_id; timestamp (default now); frame_size en bytes (opcional).
        Outputs: None (muta CameraMetrics in-place bajo su propio lock).
        Excepciones: ninguna esperada.
        Llamado por: consumidores del FrameDistributor y `_start_ffmpeg_session`
            del worker legacy; también al reconectar para resetear la frescura.
        Llama a: CameraMetrics.update_frame.
        """
        if timestamp is None:
            timestamp = time.time()
            
        with self._lock:
            metrics = self._camera_metrics[camera_id]
        
        metrics.update_frame(timestamp, frame_size)
    
    def register_camera(self, camera_id: int) -> None:
        """
        Propósito: dar de alta una cámara en el monitoreo, inicializando
            `last_frame_time = now` para que el stalled monitor no la dé por
            congelada antes de que llegue su primer frame. Etapa: Pipeline #1/#3.
        Inputs: camera_id. Outputs: None. Excepciones: ninguna.
        Llamado por: CameraManager al arrancar una cámara.
        Llama a: nada (solo muta el dict bajo el RLock global).
        """
        with self._lock:
            if camera_id not in self._camera_metrics:
                # IMPORTANTE: inicializar last_frame_time = ahora.
                # Si se queda en 0.0, el stalled monitor ve "1779565812s sin
                # frames" y reinicia la cámara antes de que llegue el primer frame.
                m = CameraMetrics()
                m.last_frame_time = time.time()
                self._camera_metrics[camera_id] = m
                logger.info(f"Cámara {camera_id} registrada en métricas "
                            f"(last_frame_time inicializado a now)")
    
    def unregister_camera(self, camera_id: int) -> None:
        """
        Propósito: baja de una cámara del monitoreo (no más alertas/FPS).
        Inputs: camera_id. Outputs: None. Excepciones: ninguna (no-op si no existe).
        Llamado por: CameraManager al parar/borrar una cámara, y por
            `check_stalled_cameras` cuando detecta métricas huérfanas (sin worker).
        """
        with self._lock:
            if camera_id in self._camera_metrics:
                del self._camera_metrics[camera_id]
                logger.info(f"Cámara {camera_id} desregistrada de métricas")
    
    def get_camera_metrics(self, camera_id: int) -> Optional[CameraMetrics]:
        """Obtiene métricas de una cámara específica."""
        with self._lock:
            return self._camera_metrics.get(camera_id)
    
    def get_all_camera_metrics(self) -> Dict[int, CameraMetrics]:
        """Obtiene métricas de todas las cámaras."""
        with self._lock:
            return dict(self._camera_metrics)
    
    def get_system_metrics(self) -> SystemMetrics:
        """Obtiene métricas globales del sistema (cacheadas, no bloqueante)."""
        with self._lock:
            return SystemMetrics(
                cpu_percent=self._system_metrics.cpu_percent,
                memory_percent=self._system_metrics.memory_percent,
                disk_percent=self._system_metrics.disk_percent,
                timestamp=self._system_metrics.timestamp
            )
    
    def _resolve_disk_path(self) -> str:
        """Ruta cuyo disco se mide para disk_percent: el de las grabaciones."""
        import os
        try:
            from backend.app.config import settings
            p = os.path.abspath(getattr(settings, "RECORDINGS_PATH", ".") or ".")
            # Subir hasta un directorio existente (la carpeta puede no existir aún).
            while p and not os.path.exists(p):
                parent = os.path.dirname(p)
                if parent == p:
                    break
                p = parent
            return p if p and os.path.exists(p) else os.path.abspath(os.sep)
        except Exception:
            return os.path.abspath(os.sep)

    def _update_system_metrics(self):
        """Loop background que actualiza métricas de sistema cada 2 segundos."""
        while self._running:
            try:
                cpu = psutil.cpu_percent(interval=None)
                memory = psutil.virtual_memory().percent
                disk = psutil.disk_usage(self._disk_path).percent
                
                with self._lock:
                    self._system_metrics.cpu_percent = cpu
                    self._system_metrics.memory_percent = memory
                    self._system_metrics.disk_percent = disk
                    self._system_metrics.timestamp = time.time()
                    
            except Exception as e:
                logger.error(f"Error actualizando métricas de sistema: {e}")
            
            time.sleep(2.0)
    
    def get_health_status(self) -> dict:
        """
        Propósito: construir el payload de salud agregado del sistema. Etapa:
            transversal (sirve a `GET /api/v1/health`).
        Inputs: ninguno (lee estado interno).
        Outputs: dict {cameras: [{id, status healthy/stalled, last_frame_seconds_ago,
            fps, total_frames, data_mb}], system: {cpu/mem/disk %, uptime_seconds,
            last_updated}}. Marca 'stalled' si pasaron >5s desde el último frame.
        Excepciones: ninguna esperada.
        Llamado por: el endpoint `health_check` de main.py.
        Llama a: get_system_metrics.
        """
        now = time.time()
        cameras = []
        
        with self._lock:
            metrics_copy = dict(self._camera_metrics)
        
        for cid, metrics in metrics_copy.items():
            time_since = now - metrics.last_frame_time
            cameras.append({
                'id': cid,
                'status': 'healthy' if time_since < 5 else 'stalled',
                'last_frame_seconds_ago': round(time_since, 1),
                'fps': round(metrics.fps, 1),
                'total_frames': metrics.frame_count,
                # Datos procesados (MB) — más legible que "total frames" en la UI.
                'data_mb': round(metrics.bytes_processed / (1024 * 1024), 1),
            })
        
        sys_metrics = self.get_system_metrics()

        return {
            "cameras": cameras,
            "system": {
                "cpu_percent": sys_metrics.cpu_percent,
                "memory_percent": sys_metrics.memory_percent,
                "disk_percent": sys_metrics.disk_percent,
                "uptime_seconds": round(now - self._start_time),
                "last_updated": sys_metrics.timestamp
            }
        }
    
    # ================== NUEVOS MÉTODOS PARA AUTO-RESTART ==================
    def check_stalled_cameras(self, camera_manager, stalled_threshold: int = 30):
        """
        Propósito: detectar cámaras CONGELADAS (sin frames > umbral) y reiniciar
            su worker — núcleo de la auto-curación. Etapa: Pipeline #3/#10 (al
            reiniciar emite `camera_offline`).
        Inputs: camera_manager (inversión de control: se le consultan/reinician
            workers); stalled_threshold en segundos.
        Outputs: list[int] con los IDs de cámaras efectivamente reiniciadas.
        Excepciones: capturadas internamente por cámara (un fallo no aborta el barrido).

        GUARDAS que evitan reinicios duplicados o en mal momento (en este orden):
            - last_frame_time inválido (<1990) → la cámara aún no entregó frames.
            - cámara auto-desactivada por fallo permanente → NO reanimar (evita el
              bucle infinito reintentos→reanimación cada 15s).
            - worker inexistente → desregistra métricas huérfanas y sigue.
            - worker en reconnecting/starting → ya se está recuperando solo.
            - worker en error → en plena auto-desactivación; esperar.
            - worker RUNNING recién arrancado (<30s) → grace period para el 1er
              keyframe (GOP largo).
        Llamado por: el hilo `monitor` de `start_stalled_monitor`.
        Llama a: camera_manager.{is_auto_disabled,get_worker,restart_camera},
            unregister_camera, event_manager.emit.
        """
        now = time.time()
        restarted = []
        
        with self._lock:
            metrics_copy = dict(self._camera_metrics)
        
        for cam_id, metrics in metrics_copy.items():
            time_since_last = now - metrics.last_frame_time
            # Sanity check: si last_frame_time es 0 o un timestamp absurdo
            # (pre-1990), no reiniciar — la cámara aún no ha entregado frames.
            if metrics.last_frame_time < 631152000:  # 1990-01-01
                logger.debug(f"Cámara {cam_id}: last_frame_time inválido, skip check")
                continue
            if time_since_last > stalled_threshold:
                # Si el CameraManager la auto-desactivó por fallo permanente,
                # NO reiniciar. Antes esto producía un bucle infinito: la cámara
                # agotaba 10 reintentos (~3 min), el stalled monitor la reanimaba
                # 15 s después y volvíamos a empezar.
                try:
                    if hasattr(camera_manager, "is_auto_disabled") and camera_manager.is_auto_disabled(cam_id):
                        logger.debug(
                            f"Cámara {cam_id} auto-desactivada por fallo permanente; "
                            f"no se reinicia (edita la cámara y reactívala manualmente)"
                        )
                        continue
                except Exception:
                    pass

                # No interferir si el worker ya está reconectándose por su cuenta.
                # Si el FFmpeg cayó hace 5s, el worker está en RECONNECTING y
                # un restart externo aquí duplica el trabajo (doble kill,
                # doble re-spawn, más tiempo sin frames).
                try:
                    worker = camera_manager.get_worker(cam_id)
                    if worker is None:
                        # No hay worker: la cámara fue parada/borrada. Limpiar
                        # métricas huérfanas para no seguir alertando.
                        logger.debug(
                            f"Cámara {cam_id} sin worker activo; desregistrando métricas"
                        )
                        try:
                            self.unregister_camera(cam_id)
                        except Exception:
                            pass
                        continue
                    status = getattr(worker, "status", None)
                    status_val = getattr(status, "value", str(status))
                    if status_val in ("reconnecting", "starting"):
                        logger.debug(
                            f"Cámara {cam_id} sin frames {time_since_last:.1f}s "
                            f"pero worker ya está {status_val}; no reinicio"
                        )
                        continue
                    if status_val == "error":
                        # Worker en ERROR: probablemente está en proceso de auto-
                        # desactivación pero aún no se ha aplicado el flag.
                        # No reiniciar; deja que el callback termine.
                        logger.debug(
                            f"Cámara {cam_id} en ERROR; esperando auto-desactivación"
                        )
                        continue
                    # Grace period: si el worker está RUNNING pero acaba de
                    # arrancar (<30s), no reiniciar. Le damos margen para que
                    # llegue el primer keyframe de la cámara (GOP largo).
                    running_since = getattr(worker, "_running_since", 0) or 0
                    if running_since > 0 and (now - running_since) < 30:
                        logger.debug(
                            f"Cámara {cam_id} sin frames {time_since_last:.1f}s "
                            f"pero worker recién arrancado ({now - running_since:.0f}s); "
                            f"esperando primer keyframe"
                        )
                        continue
                except Exception:
                    pass

                logger.warning(f"Cámara {cam_id} congelada ({time_since_last:.1f}s sin frames). Reiniciando...")
                try:
                    camera_manager.restart_camera(cam_id)
                    restarted.append(cam_id)
                    # Emitir evento de cámara offline
                    from backend.app.events.event_manager import event_manager, EventData
                    event_data = EventData(
                        event_type="camera_offline",
                        camera_id=cam_id,
                        camera_name=f"Camara_{cam_id}",
                        timestamp=now,
                        confidence=1.0,
                        frame=None,
                        metadata={"reason": f"stalled_{stalled_threshold}s"}
                    )
                    event_manager.emit(event_data)
                except Exception as e:
                    logger.error(f"Error reiniciando cámara {cam_id}: {e}")
        
        return restarted

    def start_stalled_monitor(self, camera_manager, interval: int = 15, stalled_threshold: int = 30):
        """
        Propósito: lanzar el hilo daemon que ejecuta `check_stalled_cameras` en
            bucle — la 2ª línea de defensa del self-healing. Etapa: Pipeline #1
            (lo arranca main, paso 13).
        Inputs: camera_manager; interval (cada cuántos s barre, main usa 10);
            stalled_threshold (main usa 25s, margen sobre el watchdog interno de 30s).
        Outputs: None (deja un hilo daemon corriendo mientras `_running`).
        Excepciones: cada iteración captura errores para no matar el hilo.
        Llamado por: main.py en el arranque.
        Llama a: check_stalled_cameras (en loop).
        """
        def monitor():
            while self._running:
                time.sleep(interval)
                try:
                    self.check_stalled_cameras(camera_manager, stalled_threshold)
                except Exception as e:
                    logger.error(f"Error en monitor de cámaras congeladas: {e}")
        
        threading.Thread(target=monitor, daemon=True).start()
        logger.info("Stalled camera monitor iniciado")
    # ====================================================================
    
    def shutdown(self):
        """
        Propósito: apagado ordenado — baja `_running` (lo que detiene tanto el hilo
            de métricas de sistema como el del stalled monitor) y hace join del de
            sistema. Etapa: Pipeline #1 (bloque `finally` de main).
        Inputs/Outputs: ninguno. Excepciones: ninguna. Llamado por: main.py al cerrar.
        """
        self._running = False
        if self._system_thread.is_alive():
            self._system_thread.join(timeout=2.0)
        logger.info("MetricsCollector detenido")


# Instancia global singleton
metrics_collector = MetricsCollector()