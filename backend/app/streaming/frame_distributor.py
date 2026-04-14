"""
Frame Distributor v2.0 - Distribución eficiente con opción de copia.
Usa GlobalExecutor y permite consumidores sin copia (zero-copy) para MJPEG.
"""
import threading
import time
import logging
from typing import Callable, Dict, Optional, Tuple

from .frame_buffer import CircularFrameBuffer, FrameData
from ..core.executor import global_executor

# FIX F1.1: Eliminar import circular de system.py
# from backend.app.api.routes.system import health_monitor  # ELIMINADO

# FIX F1.1: Importar MetricsCollector independiente
from ..infrastructure.metrics.collector import metrics_collector
logger = logging.getLogger(__name__)


class FrameDistributor:
    def __init__(self, camera_id: int, max_workers: int = 4):
        self.camera_id = camera_id
        self._consumers: Dict[str, Tuple[Callable[[FrameData], None], bool]] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._logger = logging.getLogger(__name__)
        
        self._last_frame_id = -1
        self._frames_distributed = 0
        self._duplicates_skipped = 0
        
        # FIX F1.1: Registrar cámara en métricas al crear distributor
        metrics_collector.register_camera(camera_id)

    def register_consumer(self, name: str, callback: Callable[[FrameData], None], needs_copy: bool = True) -> None:
        with self._lock:
            self._consumers[name] = (callback, needs_copy)
            self._logger.info(f"Consumidor '{name}' registrado (copy={needs_copy})")

    def unregister_consumer(self, name: str) -> None:
        with self._lock:
            if name in self._consumers:
                del self._consumers[name]
                self._logger.info(f"Consumidor '{name}' desregistrado")

    def start(self, frame_buffer: CircularFrameBuffer) -> None:
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(
            target=self._distribution_loop,
            args=(frame_buffer,),
            daemon=True,
            name=f"Distributor-Cam{self.camera_id}"
        )
        self._thread.start()
        self._logger.info(f"FrameDistributor iniciado para cámara {self.camera_id}")

    def _distribution_loop(self, frame_buffer: CircularFrameBuffer) -> None:
        last_frame_id = -1
        
        while self._running:
            try:
                frame_data = frame_buffer.get_latest()
                
                if frame_data is None:
                    time.sleep(0.005)
                    continue
                
                # --- NUEVO: Asegurar que stream_id esté presente ---
                if not hasattr(frame_data, 'stream_id') or frame_data.stream_id is None:
                    frame_data.stream_id = "main"  # valor por defecto para cámaras normales
                
                if frame_data.frame_id == last_frame_id:
                    self._duplicates_skipped += 1
                    time.sleep(0.005)
                    continue
                
                last_frame_id = frame_data.frame_id
                self._frames_distributed += 1
                
                # FIX F1.1: Usar MetricsCollector en lugar de health_monitor directo
                metrics_collector.update_camera_frame(
                    self.camera_id, 
                    timestamp=frame_data.timestamp,
                    frame_size=frame_data.frame.size if hasattr(frame_data.frame, 'size') else 0
                )
                
                with self._lock:
                    consumers = dict(self._consumers)
                
                for name, (callback, needs_copy) in consumers.items():
                    try:
                        if needs_copy:
                            # --- NUEVO: Copiar también el stream_id ---
                            frame_copy = FrameData(
                                frame=frame_data.frame.copy(),
                                timestamp=frame_data.timestamp,
                                camera_id=frame_data.camera_id,
                                frame_id=frame_data.frame_id,
                                stream_id=frame_data.stream_id  # ← propagar stream_id
                            )
                            global_executor.submit(self._safe_callback, name, callback, frame_copy)
                        else:
                            # Sin copia: se pasa la misma referencia (stream_id ya está presente)
                            global_executor.submit(self._safe_callback, name, callback, frame_data)
                    except Exception as e:
                        self._logger.error(f"Error encolando {name}: {e}")
                
                time.sleep(0.001)
                
            except Exception as e:
                self._logger.error(f"Error en loop de distribución: {e}")
                time.sleep(0.01)

    def _safe_callback(self, name: str, callback: Callable, frame_data: FrameData) -> None:
        try:
            callback(frame_data)
        except Exception as e:
            self._logger.error(f"Error en consumer '{name}': {e}")

    def get_stats(self) -> dict:
        return {
            "frames_distributed": self._frames_distributed,
            "last_frame_id": self._last_frame_id,
            "duplicates_skipped": self._duplicates_skipped,
            "consumers_count": len(self._consumers)
        }

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        
        # FIX F1.1: Desregistrar de métricas al detener
        metrics_collector.unregister_camera(self.camera_id)
        self._logger.info(f"Distributor detenido. Total distribuidos: {self._frames_distributed}")