import threading
import logging
import time
from typing import Callable, Dict

from .frame_buffer import CircularFrameBuffer, FrameData


class FrameDistributor:
    """
    Distribuye frames desde el buffer a múltiples consumidores (subscribers).
    Cada consumidor recibe los frames en un thread separado para no bloquear.
    """

    def __init__(self, camera_id: int):
        """
        Inicializa el distribuidor de frames.

        Args:
            camera_id: ID de la cámara asociada
        """
        self.camera_id = camera_id
        self._consumers: Dict[str, Callable[[FrameData], None]] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._logger = logging.getLogger(__name__)

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

                    # Entregar a cada consumidor en thread separado para no bloquear
                    for name, callback in consumers_snapshot.items():
                        try:
                            consumer_thread = threading.Thread(
                                target=callback,
                                args=(frame_data,),
                                daemon=True,
                                name=f"Consumer-{name}-Cam{self.camera_id}"
                            )
                            consumer_thread.start()
                        except Exception as e:
                            self._logger.error(f"Error al iniciar thread para consumer {name}: {e}")

                # Limitar a ~30fps máximo (33ms entre frames)
                time.sleep(0.033)

            except Exception as e:
                self._logger.error(f"Error en distribution loop: {e}")
                time.sleep(0.1)

    def stop(self) -> None:
        """Detiene el distribuidor de frames."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
            self._logger.info(f"FrameDistributor detenido para cámara {self.camera_id}")