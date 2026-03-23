# Nuevo archivo: backend/app/processing/ai/model_pool.py
"""
Pool compartido de modelos YOLO para evitar carga múltiple.
"""
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class YLOModelPool:
    """
    Singleton que mantiene una única instancia del modelo YOLO.
    Thread-safe y con lazy loading.
    """
    
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
        self._model = None
        self._model_lock = threading.Lock()
        self._ref_count = 0
        self._model_path = "yolov8n.pt"
        self._confidence = 0.45
    
    def get_model(self):
        """Obtiene el modelo compartido (carga lazy si es necesario)."""
        with self._model_lock:
            if self._model is None:
                try:
                    from ultralytics import YOLO
                    self._model = YOLO(self._model_path)
                    logger.info(f"Modelo YOLO cargado: {self._model_path}")
                except Exception as e:
                    logger.error(f"Error cargando YOLO: {e}")
                    raise
            self._ref_count += 1
            return self._model
    
    def release_model(self):
        """Libera referencia (opcional: descargar si ref_count = 0)."""
        with self._model_lock:
            self._ref_count = max(0, self._ref_count - 1)
            if self._ref_count == 0 and self._model is not None:
                # Opcional: descargar para liberar RAM
                # self._model = None
                pass
    
    def detect(self, frame, conf=None):
        """Método helper thread-safe para inferencia."""
        model = self.get_model()
        confidence = conf or self._confidence
        
        with self._model_lock:  # YOLO no es thread-safe por defecto
            try:
                results = model(frame, conf=confidence, verbose=False)
                return results
            except Exception as e:
                logger.error(f"Error en inferencia: {e}")
                return []


# Modificar yolo_detector.py para usar el pool:

class YOLODetector:
    def __init__(self, model_path: str = "yolov8n.pt", confidence: float = 0.45):
        self._pool = YLOModelPool()
        self._confidence = confidence
    
    def detect(self, frame):
        if frame is None or frame.size == 0:
            return []
            
        try:
            results = self._pool.detect(frame, self._confidence)
            # Procesar results igual que antes...
            detections = []
            for result in results:
                if result.boxes is None:
                    continue
                for box in result.boxes:
                    # ... lógica existente ...
                    pass
            return detections
        except Exception as e:
            logger.error(f"Error en detección: {e}")
            return []