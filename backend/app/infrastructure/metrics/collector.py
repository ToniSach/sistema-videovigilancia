"""
Metrics Collector - Sistema de métricas desacoplado.
Elimina la dependencia circular entre FrameDistributor y API routes.
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
    """Métricas individuales por cámara."""
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
    """Métricas globales del sistema."""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    disk_percent: float = 0.0
    timestamp: float = 0.0


class MetricsCollector:
    """
    Singleton de recolección de métricas thread-safe.
    Desacopla productores (FrameDistributor) de consumidores (API Health).
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
        
        # Thread de actualización de métricas de sistema (no bloqueante)
        self._running = True
        self._system_thread = threading.Thread(target=self._update_system_metrics, daemon=True)
        self._system_thread.start()
        
        logger.info("MetricsCollector inicializado")
    
    def update_camera_frame(self, camera_id: int, timestamp: Optional[float] = None, 
                           frame_size: int = 0) -> None:
        """Registra un nuevo frame recibido de una cámara."""
        if timestamp is None:
            timestamp = time.time()
            
        with self._lock:
            metrics = self._camera_metrics[camera_id]
        
        metrics.update_frame(timestamp, frame_size)
    
    def register_camera(self, camera_id: int) -> None:
        """Registra una nueva cámara para monitoreo."""
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
        """Elimina una cámara del monitoreo."""
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
    
    def _update_system_metrics(self):
        """Loop background que actualiza métricas de sistema cada 2 segundos."""
        while self._running:
            try:
                cpu = psutil.cpu_percent(interval=None)
                memory = psutil.virtual_memory().percent
                disk = psutil.disk_usage('/').percent
                
                with self._lock:
                    self._system_metrics.cpu_percent = cpu
                    self._system_metrics.memory_percent = memory
                    self._system_metrics.disk_percent = disk
                    self._system_metrics.timestamp = time.time()
                    
            except Exception as e:
                logger.error(f"Error actualizando métricas de sistema: {e}")
            
            time.sleep(2.0)
    
    def get_health_status(self) -> dict:
        """Genera el estado de salud completo para la API."""
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
                'total_frames': metrics.frame_count
            })
        
        sys_metrics = self.get_system_metrics()
        
        return {
            "cameras": cameras,
            "system": {
                "cpu_percent": sys_metrics.cpu_percent,
                "memory_percent": sys_metrics.memory_percent,
                "disk_percent": sys_metrics.disk_percent,
                "last_updated": sys_metrics.timestamp
            }
        }
    
    # ================== NUEVOS MÉTODOS PARA AUTO-RESTART ==================
    def check_stalled_cameras(self, camera_manager, stalled_threshold: int = 30):
        """
        Verifica cámaras que no han recibido frames por más de `stalled_threshold` segundos.
        Si se detecta una cámara congelada, intenta reiniciar su worker.
        Retorna lista de cámaras reiniciadas.
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
        Inicia un thread que monitorea cámaras congeladas y las reinicia.
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
        """Detiene el thread de métricas de sistema y el monitor de cámaras congeladas."""
        self._running = False
        if self._system_thread.is_alive():
            self._system_thread.join(timeout=2.0)
        logger.info("MetricsCollector detenido")


# Instancia global singleton
metrics_collector = MetricsCollector()