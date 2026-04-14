"""
Frame Distributor v2.1 - Distribución eficiente con opción de copia.
Usa GlobalExecutor y permite consumidores sin copia (zero-copy) para MJPEG.
FIX: Reducido overhead de métricas y mejor manejo de excepciones.
"""
import threading
import time
import logging
from typing import Callable, Dict, Optional, Tuple

from .frame_buffer import CircularFrameBuffer, FrameData
from ..core.executor import global_executor

# FIX: Importación segura de métricas (graceful degradation si no está disponible)
try:
    from ..infrastructure.metrics.collector import metrics_collector
    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False
    metrics_collector = None

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
        self._metrics_counter = 0  # FIX I2: Contador para throttling de métricas
        
        # Registrar cámara en métricas si está disponible
        if METRICS_AVAILABLE and metrics_collector:
            try:
                metrics_collector.register_camera(camera_id)
            except Exception as e:
                self._logger.warning(f"No se pudo registrar cámara {camera_id} en métricas: {e}")

    def register_consumer(self, name: str, callback: Callable[[FrameData], None], needs_copy: bool = True) -> None:
        """Registra un consumidor de frames."""
        with self._lock:
            self._consumers[name] = (callback, needs_copy)
            self._logger.info(f"Consumidor '{name}' registrado (copy={needs_copy}) para cámara {self.camera_id}")

    def unregister_consumer(self, name: str) -> None:
        """Desregistra un consumidor."""
        with self._lock:
            if name in self._consumers:
                del self._consumers[name]
                self._logger.info(f"Consumidor '{name}' desregistrado de cámara {self.camera_id}")

    def start(self, frame_buffer: CircularFrameBuffer) -> None:
        """Inicia el loop de distribución."""
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
        """Loop principal de distribución de frames."""
        last_frame_id = -1
        
        while self._running:
            try:
                frame_data = frame_buffer.get_latest()
                
                if frame_data is None:
                    time.sleep(0.005)
                    continue
                
                # Asegurar que stream_id esté presente (para cámaras normales y dual-lens)
                if not hasattr(frame_data, 'stream_id') or frame_data.stream_id is None:
                    frame_data.stream_id = "main"
                
                # Skip duplicates
                if frame_data.frame_id == last_frame_id:
                    self._duplicates_skipped += 1
                    time.sleep(0.005)
                    continue
                
                last_frame_id = frame_data.frame_id
                self._frames_distributed += 1
                self._metrics_counter += 1
                
                # FIX I2: Actualizar métricas solo cada 30 frames (~2 segundos a 15 FPS)
                # para reducir overhead de sincronización y cálculos
                if METRICS_AVAILABLE and metrics_collector and self._metrics_counter >= 30:
                    try:
                        metrics_collector.update_camera_frame(
                            self.camera_id, 
                            timestamp=frame_data.timestamp,
                            frame_size=frame_data.frame.size if hasattr(frame_data.frame, 'size') else 0
                        )
                        self._metrics_counter = 0
                    except Exception as e:
                        # Silenciar errores de métricas para no afectar el streaming
                        if self._frames_distributed % 300 == 0:  # Log cada ~20 segundos
                            self._logger.debug(f"Error actualizando métricas: {e}")
                
                # Obtener copia de consumidores para minimizar tiempo de lock
                with self._lock:
                    consumers = dict(self._consumers)
                
                if not consumers:
                    time.sleep(0.001)
                    continue
                
                # Distribuir a consumidores
                for name, (callback, needs_copy) in consumers.items():
                    try:
                        if needs_copy:
                            # Crear copia profunda del frame para consumidores que lo requieren
                            frame_copy = FrameData(
                                frame=frame_data.frame.copy(),
                                timestamp=frame_data.timestamp,
                                camera_id=frame_data.camera_id,
                                frame_id=frame_data.frame_id,
                                stream_id=frame_data.stream_id  # Propagar stream_id
                            )
                            # Enviar al executor (no bloquea si hay espacio)
                            future = global_executor.submit(self._safe_callback, name, callback, frame_copy)
                            if future is None:
                                # Executor saturado, loggear pero continuar
                                if self._frames_distributed % 100 == 0:
                                    self._logger.warning(f"Executor saturado, frame descartado para {name}")
                        else:
                            # Zero-copy: pasar referencia directa (más rápido, pero inseguro si el consumidor modifica)
                            # El stream_id ya está presente en frame_data
                            future = global_executor.submit(self._safe_callback, name, callback, frame_data)
                            if future is None and self._frames_distributed % 100 == 0:
                                self._logger.warning(f"Executor saturado (zero-copy), frame descartado para {name}")
                                
                    except Exception as e:
                        self._logger.error(f"Error encolando {name}: {e}")
                
                # Pausa mínima para ceder CPU (1ms)
                time.sleep(0.001)
                
            except Exception as e:
                self._logger.error(f"Error en loop de distribución cámara {self.camera_id}: {e}")
                time.sleep(0.01)  # Backoff en error

    def _safe_callback(self, name: str, callback: Callable, frame_data: FrameData) -> None:
        """Wrapper seguro para ejecutar callbacks sin propagar excepciones."""
        try:
            callback(frame_data)
        except Exception as e:
            self._logger.error(f"Error en consumer '{name}' cámara {self.camera_id}: {e}")

    def get_stats(self) -> dict:
        """Retorna estadísticas del distributor."""
        with self._lock:
            return {
                "camera_id": self.camera_id,
                "frames_distributed": self._frames_distributed,
                "last_frame_id": self._last_frame_id,
                "duplicates_skipped": self._duplicates_skipped,
                "consumers_count": len(self._consumers),
                "metrics_throttle": self._metrics_counter,
                "running": self._running
            }

    def stop(self) -> None:
        """Detiene el distributor de forma segura."""
        self._running = False
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        
        # Desregistrar de métricas
        if METRICS_AVAILABLE and metrics_collector:
            try:
                metrics_collector.unregister_camera(self.camera_id)
            except Exception as e:
                self._logger.debug(f"Error desregistrando cámara de métricas: {e}")
                
        self._logger.info(f"Distributor detenido. Total distribuidos: {self._frames_distributed}")