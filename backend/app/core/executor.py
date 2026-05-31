# backend/app/core/executor.py
"""
Global Executor v2.0 - Pool de threads unificado con límite estricto.
FIX F1.3: Previene explosión de threads saturando recursos.
"""
from concurrent.futures import ThreadPoolExecutor
import logging
import threading

logger = logging.getLogger(__name__)


class GlobalExecutor:
    """
    Singleton ThreadPoolExecutor con política de rechazo.
    Límite máximo de 20 threads concurrentes para todo el sistema.
    """
    _instance = None
    _lock = threading.Lock()
    MAX_WORKERS = 50  # FIX F1.3: Límite global estricto

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
        self._executor = ThreadPoolExecutor(
            max_workers=self.MAX_WORKERS,
            thread_name_prefix="GlobalWorker"
        )
        self._submitted_count = 0
        self._completed_count = 0
        self._rejected_count = 0
        self._lock = threading.Lock()
        self._shutdown = False
        
        logger.info(f"GlobalExecutor inicializado con max_workers={self.MAX_WORKERS}")

    def submit(self, fn, *args, **kwargs):
        """
        Submit con manejo de rechazo si el sistema está saturado o shutdown.
        """
        # _shutdown se lee aquí y se escribe en shutdown(): sin lock había
        # TOCTOU que podía intentar submit() sobre un executor ya cerrado y
        # dejar contadores inconsistentes.
        with self._lock:
            if self._shutdown:
                logger.warning("Task rechazado: executor está detenido")
                return None

        try:
            future = self._executor.submit(self._wrap_task, fn, *args, **kwargs)
            with self._lock:
                self._submitted_count += 1
            return future
            
        except RuntimeError as e:
            # FIX: Python no tiene RejectedExecutionError, usa RuntimeError
            # Ocurre cuando el executor está saturado o shutdown
            with self._lock:
                self._rejected_count += 1
            logger.warning(f"Task rechazado: {e}")
            return None
        except Exception as e:
            logger.error(f"Error submiting task: {e}")
            return None

    def _wrap_task(self, fn, *args, **kwargs):
        """Wrapper para trackear completitud y manejar excepciones."""
        try:
            result = fn(*args, **kwargs)
            with self._lock:
                self._completed_count += 1
            return result
        except Exception as e:
            logger.error(f"Error en task ejecutada: {e}", exc_info=True)
            raise

    def get_stats(self) -> dict:
        """Estadísticas del executor."""
        with self._lock:
            return {
                "max_workers": self.MAX_WORKERS,
                "submitted_tasks": self._submitted_count,
                "completed_tasks": self._completed_count,
                "rejected_tasks": self._rejected_count,
                "active_threads": threading.active_count()
            }

    def shutdown(self):
        """Detiene el executor gracefulmente."""
        with self._lock:
            self._shutdown = True
        self._executor.shutdown(wait=False)
        logger.info("GlobalExecutor detenido")


# Instancia global
global_executor = GlobalExecutor()