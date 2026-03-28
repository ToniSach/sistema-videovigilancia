import threading
import logging
import time
from typing import Callable, Dict, Optional
from concurrent.futures import ThreadPoolExecutor

from .frame_buffer import CircularFrameBuffer, FrameData


class FrameDistributor:
    """
    Distribuye frames desde el buffer a múltiples consumidores.
    """

    def __init__(self, camera_id: int, max_workers: int = 4):
        self.camera_id = camera_id
        self._consumers: Dict[str, Callable[[FrameData], None]] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._logger = logging.getLogger(__name__)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, 
            thread_name_prefix=f"Distributor-{camera_id}"
        )
        
        self._last_frame_id: int = -1
        self._frames_distributed = 0
        self._duplicates_skipped = 0

    def register_consumer(self, name: str, callback: Callable[[FrameData], None]) -> None:
        with self._lock:
            self._consumers[name] = callback
            self._logger.info(f"Consumidor '{name}' registrado para cámara {self.camera_id}")

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
        """
        Loop principal de distribución.
        """
        last_frame_id = -1
        
        while self._running:
            try:
                frame_data = frame_buffer.get_latest()
                
                if frame_data is None:
                    time.sleep(0.01)
                    continue
                
                # Si es el mismo frame, saltar
                if frame_data.frame_id == last_frame_id:
                    self._duplicates_skipped += 1
                    time.sleep(0.01)
                    continue
                
                last_frame_id = frame_data.frame_id
                self._frames_distributed += 1
                
                # Copiar consumidores
                with self._lock:
                    consumers = dict(self._consumers)
                
                if not consumers:
                    time.sleep(0.01)
                    continue
                
                # Distribuir a todos los consumidores
                for name, callback in consumers.items():
                    try:
                        # Copia independiente para cada consumidor
                        frame_copy = FrameData(
                            frame=frame_data.frame.copy(),
                            timestamp=frame_data.timestamp,
                            camera_id=frame_data.camera_id,
                            frame_id=frame_data.frame_id
                        )
                        self._executor.submit(self._safe_callback, name, callback, frame_copy)
                    except Exception as e:
                        self._logger.error(f"Error encolando {name}: {e}")
                
                # Pequeña pausa
                time.sleep(0.001)
                
            except Exception as e:
                self._logger.error(f"Error en loop: {e}")
                time.sleep(0.01)

    def _safe_callback(self, name: str, callback: Callable[[FrameData], None], frame_data: FrameData) -> None:
        """Wrapper seguro para callbacks."""
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
        
        self._executor.shutdown(wait=False)
        self._logger.info(f"Distributor detenido. Total distribuidos: {self._frames_distributed}")