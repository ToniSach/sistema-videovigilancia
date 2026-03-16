import collections
import threading
import numpy as np
from dataclasses import dataclass
import time
from typing import Optional


@dataclass
class FrameData:
    """Estructura de datos para un frame capturado."""
    frame: np.ndarray
    timestamp: float
    camera_id: int


class CircularFrameBuffer:
    """
    Buffer circular thread-safe para almacenar frames de video.
    Mantiene los últimos N frames (por defecto 30, ~1 segundo a 30fps).
    """

    def __init__(self, camera_id: int, maxsize: int = 30):
        """
        Inicializa el buffer circular.

        Args:
            camera_id: ID de la cámara asociada
            maxsize: Número máximo de frames a mantener en memoria
        """
        self._buffer: collections.deque[FrameData] = collections.deque(maxlen=maxsize)
        self._lock = threading.Lock()
        self.camera_id = camera_id
        self.dropped_frames = 0
        self._maxsize = maxsize

    def put(self, frame: np.ndarray) -> None:
        """
        Agrega un frame al buffer. Si el buffer está lleno,
        descarta el frame más antiguo automáticamente.

        Args:
            frame: Array numpy con el frame (BGR24)
        """
        frame_data = FrameData(
            frame=frame,
            timestamp=time.time(),
            camera_id=self.camera_id
        )

        with self._lock:
            was_full = len(self._buffer) >= self._maxsize
            self._buffer.append(frame_data)
            if was_full:
                self.dropped_frames += 1

    def get_latest(self) -> Optional[FrameData]:
        """
        Obtiene el frame más reciente.

        Returns:
            FrameData más reciente o None si el buffer está vacío
        """
        with self._lock:
            if len(self._buffer) > 0:
                return self._buffer[-1]
            return None

    def get_all(self) -> list[FrameData]:
        """
        Obtiene copia de todos los frames en el buffer.

        Returns:
            Lista de FrameData ordenados cronológicamente
        """
        with self._lock:
            return list(self._buffer)

    def size(self) -> int:
        """Retorna el número actual de frames en el buffer."""
        with self._lock:
            return len(self._buffer)

    def clear(self) -> None:
        """Limpia todos los frames del buffer."""
        with self._lock:
            self._buffer.clear()
            self.dropped_frames = 0