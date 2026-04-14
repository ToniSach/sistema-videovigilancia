import collections
import threading
import numpy as np
from dataclasses import dataclass
import time
from typing import Optional


@dataclass
class FrameData:
    frame: np.ndarray
    timestamp: float
    camera_id: int
    frame_id: int = 0
    stream_id: str = "main" 


class CircularFrameBuffer:
    """
    Buffer circular thread-safe para almacenar frames de video.
    """

    def __init__(self, camera_id: int, maxsize: int = 30):
        self._buffer: collections.deque[FrameData] = collections.deque(maxlen=maxsize)
        self._lock = threading.Lock()
        self.camera_id = camera_id
        self.dropped_frames = 0
        self._maxsize = maxsize
        self._frame_counter = 0
        self._logger = None

    def _get_logger(self):
        if self._logger is None:
            import logging
            self._logger = logging.getLogger(__name__)
        return self._logger

    def put(self, frame: np.ndarray) -> bool:
        """
        Agrega un frame al buffer.
        """
        with self._lock:
            self._frame_counter += 1
            
            frame_data = FrameData(
                frame=frame,  # Ya viene copiado desde el worker
                timestamp=time.time(),
                camera_id=self.camera_id,
                frame_id=self._frame_counter
            )
            
            was_full = len(self._buffer) >= self._maxsize
            self._buffer.append(frame_data)
            
            if was_full:
                self.dropped_frames += 1
        
        return True

    def get_latest(self) -> Optional[FrameData]:
        """
        Obtiene el frame más reciente del buffer.
        ✅ CORREGIDO: No hace copia aquí, deja que el FrameDistributor 
        maneje la copia según el parámetro needs_copy de cada consumidor.
        """
        with self._lock:
            if len(self._buffer) > 0:
                latest = self._buffer[-1]
                # ✅ Sin .copy() aquí - el distributor decide si copiar o no
                return FrameData(
                    frame=latest.frame,              # Referencia directa
                    timestamp=latest.timestamp,
                    camera_id=latest.camera_id,
                    frame_id=latest.frame_id,
                    stream_id=latest.stream_id if hasattr(latest, 'stream_id') else "main"
                )
            return None

    def get_stats(self) -> dict:
        """Obtiene estadísticas del buffer."""
        with self._lock:
            return {
                "size": len(self._buffer),
                "maxsize": self._maxsize,
                "dropped_frames": self.dropped_frames,
                "total_frames": self._frame_counter,
                "last_frame_id": self._frame_counter,
                "buffer_utilization": len(self._buffer) / self._maxsize if self._maxsize > 0 else 0
            }

    def size(self) -> int:
        with self._lock:
            return len(self._buffer)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
            self.dropped_frames = 0