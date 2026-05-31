"""
Pool compartido de modelos YOLO - Unificación de YOLODetector + YLOModelPool.
Thread-safe, singleton, y con conversión automática a formato Detection.
"""
import logging
import threading
from typing import NamedTuple, List, Optional
import numpy as np

logger = logging.getLogger(__name__)


class Detection(NamedTuple):
    """Resultado estandarizado de detección de objeto."""
    class_name: str
    confidence: float
    x1: int
    y1: int
    x2: int
    y2: int


class YLOModelPool:
    """
    Singleton que mantiene una única instancia del modelo YOLO.
    Thread-safe, lazy loading, y conversión automática a Detections.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    # Mapeo COCO a nombres legibles
    CLASSES_OF_INTEREST = {
        0: "person",
        2: "car",
        3: "motorcycle",
        5: "bus",
        7: "truck"
    }
    
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
        # Tomar confianza de settings (configurable vía AI_CONFIDENCE en .env)
        try:
            from backend.app.config import settings
            self._confidence = float(getattr(settings, "AI_CONFIDENCE", 0.35))
        except Exception:
            self._confidence = 0.35
        self._device = self._select_device()
        logger.info(
            f"YLOModelPool inicializado: device={self._device}, "
            f"confidence={self._confidence}"
        )

    def _select_device(self) -> str:
        """Selecciona automáticamente GPU si está disponible."""
        from backend.app.config import settings
        
        # 1. Override manual desde settings
        if settings.AI_BACKEND != "auto":
            if settings.AI_BACKEND == "cuda":
                try:
                    import torch
                    if torch.cuda.is_available():
                        return "cuda"
                except ImportError:
                    pass
            return "cpu"
        
        if settings.USE_GPU_AI == "false":
            return "cpu"
        
        # 2. Auto-detección
        try:
            import torch
            if torch.cuda.is_available():
                logger.info("YOLO usará CUDA (GPU detectada)")
                return "cuda"
        except ImportError:
            pass
        
        logger.info("YOLO usará CPU")
        return "cpu"

    @staticmethod
    def check_dependencies() -> tuple[bool, str]:
        """
        Verifica que torch, torchvision y ultralytics estén instalados Y
        que su import no falle por incompatibilidades (p.ej. torchvision
        ausente hace que ultralytics levante AttributeError al importar,
        no ImportError).
        Devuelve (ok, mensaje). Si falta algo, mensaje incluye el comando pip.
        """
        # Pre-test individual: torch y torchvision tienen que importarse limpiamente.
        missing: list[str] = []
        for mod in ("torch", "torchvision"):
            try:
                __import__(mod)
            except Exception:
                missing.append(mod)

        # Ultralytics: si torchvision falta, su import revienta con
        # AttributeError dentro de utils/checks.py — atrapamos cualquier
        # excepción para diagnosticar.
        ultralytics_err: str | None = None
        try:
            __import__("ultralytics")
        except ImportError:
            missing.append("ultralytics")
        except Exception as e:
            ultralytics_err = f"{type(e).__name__}: {e}"

        if not missing and ultralytics_err is None:
            return True, "ok"

        cmd = (
            "pip install torch==2.6.0+cpu torchvision==0.21.0+cpu "
            "--index-url https://download.pytorch.org/whl/cpu && "
            "pip install ultralytics"
        )
        parts = []
        if missing:
            parts.append(f"Faltan dependencias de IA: {', '.join(missing)}.")
        if ultralytics_err:
            parts.append(
                f"ultralytics no se pudo importar ({ultralytics_err}); "
                "típicamente significa que torchvision no está instalado o "
                "es incompatible con la versión de torch."
            )
        parts.append(f"Instala con: {cmd}")
        return False, " ".join(parts)

    def _load_model(self) -> None:
        """Carga lazy del modelo."""
        with self._model_lock:
            if self._model is not None:
                return

            ok, msg = self.check_dependencies()
            if not ok:
                logger.error(f"[YOLO] {msg}")
                raise RuntimeError(msg)

            try:
                # MODO OFFLINE: desactivar telemetría y check de versión de
                # Ultralytics → no hace peticiones a internet. El modelo ya
                # se descargó la primera vez y queda en local.
                import os as _os
                _os.environ.setdefault("YOLO_OFFLINE", "True")
                _os.environ.setdefault("YOLO_VERBOSE", "False")
                # Desactivar wandb/clearml/comet/etc por si están instalados
                _os.environ.setdefault("WANDB_DISABLED", "true")

                try:
                    # API moderna de Ultralytics: desactivar telemetría/checks
                    from ultralytics import settings as _ul_settings
                    _ul_settings.update({"sync": False})  # no envía analytics
                except Exception:
                    pass

                from ultralytics import YOLO
                self._model = YOLO(self._model_path)

                if self._device == "cuda":
                    self._model.to("cuda")

                logger.info(f"Modelo YOLO cargado: {self._model_path} en {self._device}")

            except Exception as e:
                logger.error(f"Error cargando YOLO: {e}")
                raise

    def detect(self, frame: np.ndarray, conf: Optional[float] = None) -> List[Detection]:
        """
        Realiza inferencia y devuelve lista de Detection (formato estándar).
        
        Args:
            frame: Frame BGR de OpenCV
            conf: Umbral de confianza (usa default si None)
            
        Returns:
            Lista de Detection (class_name, confidence, x1, y1, x2, y2)
        """
        if frame is None or frame.size == 0:
            return []
        
        # Cargar modelo si es primera vez
        if self._model is None:
            self._load_model()
        
        confidence = conf or self._confidence

        # Inferencia (thread-safe con lock)
        h, w = frame.shape[:2]
        with self._model_lock:
            try:
                results = self._model(frame, conf=confidence, verbose=False)
            except Exception as e:
                logger.error(
                    f"Error en inferencia YOLO (frame {w}x{h}, conf={confidence}): {e}"
                )
                return []
        
        # Convertir a formato Detection (filtrando solo clases de interés)
        detections: List[Detection] = []
        
        for result in results:
            if result.boxes is None:
                continue
                
            for box in result.boxes:
                class_id = int(box.cls[0])
                
                # Solo clases de videovigilancia
                if class_id not in self.CLASSES_OF_INTEREST:
                    continue
                
                confidence_val = float(box.conf[0])
                x1, y1, x2, y2 = [int(c) for c in box.xyxy[0].tolist()]
                class_name = self.CLASSES_OF_INTEREST[class_id]
                
                detections.append(Detection(
                    class_name=class_name,
                    confidence=confidence_val,
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2
                ))
        
        if detections:
            logger.debug(f"YOLO detectó {len(detections)} objetos: "
                        f"{[d.class_name for d in detections]}")
        
        return detections

    def draw_detections(self, frame: np.ndarray, detections: List[Detection]) -> np.ndarray:
        """
        Helper para dibujar detecciones en el frame (debug/visualización).
        """
        import cv2
        
        annotated = frame.copy()
        colors = {
            "person": (0, 255, 0),
            "car": (0, 0, 255),
            "motorcycle": (0, 255, 255),
            "bus": (255, 0, 0),
            "truck": (255, 255, 0)
        }

        for det in detections:
            color = colors.get(det.class_name, (255, 255, 255))

            # Bounding box
            cv2.rectangle(annotated, (det.x1, det.y1), (det.x2, det.y2), color, 2)

            # Label
            label = f"{det.class_name}: {det.confidence:.2f}"
            (text_w, text_h), _ = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2
            )
            
            cv2.rectangle(
                annotated,
                (det.x1, det.y1 - text_h - 10),
                (det.x1 + text_w, det.y1),
                color,
                -1
            )
            cv2.putText(
                annotated,
                label,
                (det.x1, det.y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 0, 0),
                2
            )

        return annotated

    def get_stats(self) -> dict:
        """Estadísticas del pool."""
        return {
            "model_loaded": self._model is not None,
            "device": self._device,
            "model_path": self._model_path,
            "reference_count": self._ref_count,
            "classes_supported": list(self.CLASSES_OF_INTEREST.values())
        }


# Instancia global singleton (para quien quiera importar directamente)
yolo_pool = YLOModelPool()