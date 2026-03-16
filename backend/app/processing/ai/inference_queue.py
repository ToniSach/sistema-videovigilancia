"""
Inference Queue - Cola de tareas para procesamiento asíncrono de IA.
"""


import queue
import threading
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class InferenceTask:
    """Tarea de inferencia pendiente."""

    camera_id: int
    frame: object  # np.ndarray
    timestamp: float


class InferenceQueue:
    """
    Cola thread-safe para tareas de inferencia.
    Implementa política de descarte cuando está llena (drop oldest).
    """


    def __init__(self, maxsize: int = 5):
        """
        Inicializa la cola de inferencia.
        
        Args:
            maxsize: Tamaño máximo de la cola (default: 5 para baja latencia)
        """

        self._queue: queue.Queue[InferenceTask] = queue.Queue(maxsize=maxsize)
        self._dropped = 0
        self._lock = threading.Lock()
        logger.debug(f"InferenceQueue inicializada (maxsize={maxsize})")

    def enqueue(self, camera_id: int, frame, timestamp: float) -> None:
        """
        Agrega una tarea a la cola.
        Si está llena, descarta la tarea más antigua.
        
        Args:
            camera_id: ID de la cámara
            frame: Frame numpy array
            timestamp: Timestamp del frame
        """

        task = InferenceTask(camera_id=camera_id, frame=frame, timestamp=timestamp)

        try:
            # Intentar agregar sin bloquear
            self._queue.put_nowait(task)
        except queue.Full:
            # Cola llena: descartar el más viejo
            with self._lock:
                try:
                    self._queue.get_nowait()
                    self._dropped += 1
                    if self._dropped % 10 == 0:
                        logger.warning(f"Cola de inferencia llena. Descartados: {self._dropped}")
                except queue.Empty:
                    pass

                # Intentar nuevamente
                try:
                    self._queue.put_nowait(task)
                except queue.Full:
                    logger.error("No se pudo agregar tarea incluso después de limpiar")

    def dequeue(self, timeout: float = 1.0) -> InferenceTask | None:
        """
        Extrae una tarea de la cola.
        
        Args:
            timeout: Tiempo máximo de espera en segundos
        
        Returns:
            InferenceTask o None si timeout
        """

        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def size(self) -> int:
        """Retorna el tamaño actual de la cola."""

        return self._queue.qsize()

    def dropped_count(self) -> int:
        """Retorna el número de tareas descartadas."""

        with self._lock:
            return self._dropped
