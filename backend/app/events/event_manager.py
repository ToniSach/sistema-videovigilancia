import threading
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor
import time
import numpy as np

from ..database.models import EventType


@dataclass
class EventData:
    event_type: str           # Valor de EventType enum (ej: "person", "motion")
    camera_id: int
    camera_name: str
    timestamp: float          # time.time()
    confidence: float         # 0.0 si no aplica
    frame: Optional[np.ndarray] = None  # np.ndarray | None
    metadata: Dict[str, Any] = field(default_factory=dict)  # info extra


class EventManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

        self._subscribers: Dict[str, List[Callable]] = {}  # event_type → list of callables
        self._global_subscribers: List[Callable] = []       # se llaman para todos los eventos
        self._lock = threading.Lock()
        # CORRECCIÓN: Pool de threads compartido para todos los eventos (evita explosión de threads)
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="EventManager")

    def subscribe(self, event_type: str, callback: Callable[[EventData], None]) -> None:
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)
            logging.debug(f"Subscriber añadido para event_type={event_type}")

    def subscribe_all(self, callback: Callable[[EventData], None]) -> None:
        with self._lock:
            self._global_subscribers.append(callback)
            logging.debug("Subscriber global añadido")

    def unsubscribe(self, event_type: str, callback: Callable[[EventData], None]) -> None:
        with self._lock:
            if event_type in self._subscribers:
                try:
                    self._subscribers[event_type].remove(callback)
                except ValueError:
                    pass

    def emit(self, event_data: EventData) -> None:
        with self._lock:
            specific_callbacks = self._subscribers.get(event_data.event_type, []).copy()
            global_callbacks = self._global_subscribers.copy()

        # CORRECCIÓN: Usar executor en lugar de crear threads nuevos por cada callback
        # Llamar callbacks específicos
        for callback in specific_callbacks:
            self._executor.submit(self._safe_call, callback, event_data)

        # Llamar callbacks globales
        for callback in global_callbacks:
            self._executor.submit(self._safe_call, callback, event_data)

    def _safe_call(self, callback: Callable[[EventData], None], event_data: EventData) -> None:
        """Wrapper seguro para ejecutar callbacks sin propagar excepciones."""
        try:
            callback(event_data)
        except Exception as e:
            logging.error(f"Error en subscriber: {e}", exc_info=True)

    def shutdown(self) -> None:
        """Limpia recursos del executor (llamar al cerrar la aplicación)."""
        self._executor.shutdown(wait=False)
        logging.info("EventManager executor detenido")


# Instancia global
event_manager = EventManager()