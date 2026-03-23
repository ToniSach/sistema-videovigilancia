import collections
import threading
import numpy as np
from dataclasses import dataclass
import time
from typing import Optional
import hashlib


@dataclass
class FrameData:
    """Estructura de datos para un frame capturado."""
    frame: np.ndarray
    timestamp: float
    camera_id: int
    frame_id: int = 0  # FIX: ID secuencial para tracking


class CircularFrameBuffer:
    """
    Buffer circular thread-safe para almacenar frames de video.
    CORREGIDO: Ahora incluye detección de frames congelados/duplicados
    y asigna ID único a cada frame.
    """

    def __init__(self, camera_id: int, maxsize: int = 30):
        self._buffer: collections.deque[FrameData] = collections.deque(maxlen=maxsize)
        self._lock = threading.Lock()
        self.camera_id = camera_id
        self.dropped_frames = 0
        self._maxsize = maxsize
        self._frame_counter = 0
        self._last_frame_hash: Optional[str] = None
        self._frozen_frame_count = 0  # Contador de frames congelados detectados

    def _compute_quick_hash(self, frame: np.ndarray) -> str:
        """Hash rápido para detección de duplicados."""
        if frame.size == 0:
            return "empty"
        # Samplear pixels esquinas y centro para detectar cambios
        h, w = frame.shape[:2]
        samples = [
            frame[0:10, 0:10].tobytes(),  # Esquina sup-izq
            frame[h-10:h, w-10:w].tobytes(),  # Esquina inf-der
            frame[h//2-5:h//2+5, w//2-5:w//2+5].tobytes(),  # Centro
        ]
        return hashlib.md5(b''.join(samples)).hexdigest()[:12]

    def put(self, frame: np.ndarray) -> bool:
        """
        Agrega un frame al buffer.
        Retorna False si es un frame duplicado (congelado).
        """
        current_hash = self._compute_quick_hash(frame)
        
        with self._lock:
            # Detección de frame congelado/duplicado
            if current_hash == self._last_frame_hash:
                self._frozen_frame_count += 1
                # Si son más de 30 frames idénticos seguidos, loggear warning
                if self._frozen_frame_count == 30:
                    import logging
                    logging.getLogger(__name__).warning(
                        f"Cámara {self.camera_id}: Posible stream congelado - "
                        f"30 frames idénticos consecutivos detectados"
                    )
                # Aún así guardar (para mantener timestamp actualizado), pero marcar como duplicado
                is_duplicate = True
            else:
                self._last_frame_hash = current_hash
                self._frozen_frame_count = 0
                is_duplicate = False
            
            self._frame_counter += 1
            
            frame_data = FrameData(
                frame=frame,
                timestamp=time.time(),
                camera_id=self.camera_id,
                frame_id=self._frame_counter
            )
            
            was_full = len(self._buffer) >= self._maxsize
            self._buffer.append(frame_data)
            
            if was_full:
                self.dropped_frames += 1
        
        return not is_duplicate  # Retorna True si es frame nuevo

    def get_latest(self) -> Optional[FrameData]:
        """Obtiene el frame más reciente."""
        with self._lock:
            if len(self._buffer) > 0:
                # FIX: Retornar copia para evitar modificaciones externas
                latest = self._buffer[-1]
                return FrameData(
                    frame=latest.frame.copy(),
                    timestamp=latest.timestamp,
                    camera_id=latest.camera_id,
                    frame_id=latest.frame_id
                )
            return None

    def is_frozen(self, threshold: int = 30) -> bool:
        """Detecta si el stream está congelado (muchos frames idénticos)."""
        with self._lock:
            return self._frozen_frame_count >= threshold

    def size(self) -> int:
        with self._lock:
            return len(self._buffer)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
            self.dropped_frames = 0
            self._frozen_frame_count = 0
            self._last_frame_hash = None