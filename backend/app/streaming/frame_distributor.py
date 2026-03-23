import threading
import logging
import time
from typing import Callable, Dict, Optional
from concurrent.futures import ThreadPoolExecutor
import hashlib
import numpy as np

from .frame_buffer import CircularFrameBuffer, FrameData


class FrameDistributor:
    """
    Distribuye frames desde el buffer a múltiples consumidores.
    CORREGIDO: Implementa deduplicación por hash de contenido y timestamp
    para evitar distribuir el mismo frame múltiples veces.
    """

    def __init__(self, camera_id: int, max_workers: int = 4):
        self.camera_id = camera_id
        self._consumers: Dict[str, Callable[[FrameData], None]] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._logger = logging.getLogger(__name__)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"Distributor-{camera_id}")
        
        # FIX: Tracking de último frame distribuido para deduplicación
        self._last_frame_hash: Optional[str] = None
        self._last_timestamp: float = 0.0
        self._last_frame_id: int = 0  # Contador interno de frames únicos distribuidos
        self._stats_lock = threading.Lock()
        self._duplicates_avoided = 0

    def register_consumer(self, name: str, callback: Callable[[FrameData], None]) -> None:
        with self._lock:
            self._consumers[name] = callback
            self._logger.info(f"Consumidor '{name}' registrado para cámara {self.camera_id}")

    def unregister_consumer(self, name: str) -> None:
        with self._lock:
            if name in self._consumers:
                del self._consumers[name]
                self._logger.info(f"Consumidor '{name}' desregistrado de cámara {self.camera_id}")

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

    def _compute_frame_hash(self, frame: Optional[np.ndarray]) -> str:
        """Calcula hash parcial del frame para detección de duplicados."""
        if frame is None or frame.size == 0:
            return "empty"
        # Usar primeros y últimos 1000 bytes + shape para detectar duplicados rápidamente
        sample = frame.flat[:1000].tobytes() + frame.flat[-1000:].tobytes() + str(frame.shape).encode()
        return hashlib.md5(sample).hexdigest()[:16]

    def _distribution_loop(self, frame_buffer: CircularFrameBuffer) -> None:
        """
        Loop principal con deduplicación estricta.
        Solo distribuye cuando detecta un frame NUEVO (diferente contenido o timestamp).
        """
        while self._running:
            try:
                frame_data = frame_buffer.get_latest()
                
                if frame_data is None or frame_data.frame is None:
                    time.sleep(0.05)
                    continue

                # FIX CRÍTICO: Deduplicación por hash de contenido + timestamp
                current_hash = self._compute_frame_hash(frame_data.frame)
                current_timestamp = frame_data.timestamp
                
                should_distribute = False
                
                with self._stats_lock:
                    # Es nuevo si: hash diferente O timestamp estrictamente mayor
                    if current_hash != self._last_frame_hash:
                        should_distribute = True
                        self._last_frame_hash = current_hash
                        self._last_timestamp = current_timestamp
                        self._last_frame_id += 1
                    elif current_timestamp > self._last_timestamp + 0.1:  # 100ms tolerancia
                        # Mismo contenido pero timestamp muy diferente (posible cámara congelada enviando duplicados)
                        should_distribute = True
                        self._last_timestamp = current_timestamp
                        self._duplicates_avoided += 1
                    else:
                        self._duplicates_avoided += 1
                        # Log cada 100 duplicados para no saturar
                        if self._duplicates_avoided % 100 == 0:
                            self._logger.debug(f"Deduplicación: {self._duplicates_avoided} frames repetidos evitados")

                if should_distribute:
                    # Copiar consumidores bajo lock
                    with self._lock:
                        consumers_snapshot = dict(self._consumers)
                    
                    # Distribuir a cada consumidor en el pool
                    for name, callback in consumers_snapshot.items():
                        try:
                            # FIX: Pasar copia del frame_data para evitar modificaciones
                            frame_data_copy = FrameData(
                                frame=frame_data.frame.copy(),
                                timestamp=frame_data.timestamp,
                                camera_id=frame_data.camera_id
                            )
                            self._executor.submit(self._safe_callback, name, callback, frame_data_copy)
                        except Exception as e:
                            self._logger.error(f"Error al encolar callback para {name}: {e}")
                    
                    # Sleep corto después de distribuir (esperar nuevo frame)
                    time.sleep(0.01)
                else:
                    # Frame duplicado, esperar más tiempo antes de revisar de nuevo
                    time.sleep(0.066)  # ~15fps, sincronizarse con cámara típica

            except Exception as e:
                self._logger.error(f"Error en distribution loop: {e}")
                time.sleep(0.1)

    def _safe_callback(self, name: str, callback: Callable[[FrameData], None], frame_data: FrameData) -> None:
        """Wrapper seguro para ejecutar callbacks."""
        try:
            callback(frame_data)
        except Exception as e:
            self._logger.error(f"Error en consumer '{name}' de cámara {self.camera_id}: {e}")

    def get_stats(self) -> dict:
        """Retorna estadísticas de deduplicación."""
        with self._stats_lock:
            return {
                "frames_distributed": self._last_frame_id,
                "duplicates_avoided": self._duplicates_avoided,
                "last_hash": self._last_frame_hash
            }

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        
        self._executor.shutdown(wait=False)
        stats = self.get_stats()
        self._logger.info(f"FrameDistributor detenido. Stats: {stats['frames_distributed']} frames distribuidos, "
                         f"{stats['duplicates_avoided']} duplicados evitados")