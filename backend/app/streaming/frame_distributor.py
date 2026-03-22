import threading
import logging
import time
from typing import Callable, Dict
from concurrent.futures import ThreadPoolExecutor

from .frame_buffer import CircularFrameBuffer, FrameData


class FrameDistributor:
    """
    Distribuye frames desde el buffer a múltiples consumidores (subscribers).
    Usa ThreadPoolExecutor para limitar creación de threads y reutilizar workers.
    """

    def __init__(self, camera_id: int, max_workers: int = 4):
        """
        Inicializa el distribuidor de frames.

        Args:
            camera_id: ID de la cámara asociada
            max_workers: Número máximo de threads workers para callbacks (default 4)
        """
        self.camera_id = camera_id
        self._consumers: Dict[str, Callable[[FrameData], None]] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._logger = logging.getLogger(__name__)
        # CORRECCIÓN: Pool de threads reutilizable en lugar de crear uno nuevo por frame
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"Distributor-{camera_id}")

    def register_consumer(self, name: str, callback: Callable[[FrameData], None]) -> None:
        """
        Registra un nuevo consumidor de frames.

        Args:
            name: Identificador único del consumidor
            callback: Función que recibirá FrameData
        """
        with self._lock:
            self._consumers[name] = callback
            self._logger.info(f"Consumidor '{name}' registrado para cámara {self.camera_id}")

    def unregister_consumer(self, name: str) -> None:
        """
        Elimina un consumidor registrado.

        Args:
            name: Identificador del consumidor a eliminar
        """
        with self._lock:
            if name in self._consumers:
                del self._consumers[name]
                self._logger.info(f"Consumidor '{name}' desregistrado de cámara {self.camera_id}")

    def start(self, frame_buffer: CircularFrameBuffer) -> None:
        """
        Inicia el loop de distribución de frames.

        Args:
            frame_buffer: Buffer circular de donde leer los frames
        """
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
        Loop principal que lee del buffer y distribuye a consumidores.
        Corre en thread separado.
        """
        last_timestamp = 0.0

        while self._running:
            try:
                frame_data = frame_buffer.get_latest()

                # Solo distribuir si hay frame nuevo
                if frame_data and frame_data.timestamp > last_timestamp:
                    last_timestamp = frame_data.timestamp

                    # Copia de consumidores bajo lock para thread-safety
                    with self._lock:
                        consumers_snapshot = dict(self._consumers)

                    # CORRECCIÓN: Usar executor en lugar de crear threads nuevos
                    for name, callback in consumers_snapshot.items():
                        try:
                            # Submit al pool de threads existente
                            self._executor.submit(self._safe_callback, name, callback, frame_data)
                        except Exception as e:
                            self._logger.error(f"Error al encolar callback para consumer {name}: {e}")

                # Limitar a ~30fps máximo (33ms entre frames)
                time.sleep(0.033)

            except Exception as e:
                self._logger.error(f"Error en distribution loop: {e}")
                time.sleep(0.1)

    def _safe_callback(self, name: str, callback: Callable[[FrameData], None], frame_data: FrameData) -> None:
        """
        Wrapper seguro para ejecutar callbacks sin propagar excepciones al executor.
        """
        try:
            callback(frame_data)
        except Exception as e:
            self._logger.error(f"Error en consumer '{name}' de cámara {self.camera_id}: {e}")

    def stop(self) -> None:
        """Detiene el distribuidor de frames y libera recursos del pool."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        
        # CORRECCIÓN: Cerrar el executor limpiamente
        self._executor.shutdown(wait=False)
        self._logger.info(f"FrameDistributor detenido para cámara {self.camera_id}")