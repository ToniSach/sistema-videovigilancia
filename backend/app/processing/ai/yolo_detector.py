"""
YOLO Detector - Inferencia con Ultralytics YOLOv8.
"""


import cv2
import numpy as np
import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)


class Detection(NamedTuple):
    """Resultado de detección de objeto."""

    class_name: str
    confidence: float
    x1: int
    y1: int
    x2: int
    y2: int


class YOLODetector:
    """
    Detector de objetos usando YOLOv8.
    Carga lazy del modelo para optimizar startup time.
    """


    # Clases COCO de interés para videovigilancia
    CLASSES_OF_INTEREST = {
        0: "person",
        2: "car", 
        3: "motorcycle",
        5: "bus",
        7: "truck"
    }

    def __init__(self, model_path: str = "yolov8n.pt", confidence: float = 0.45):
        """
        Inicializa el detector YOLO.
        
        Args:
            model_path: Ruta al modelo (default: yolov8n.pt - nano, más rápido)
            confidence: Umbral de confianza para detecciones
        """

        self._model_path = model_path
        self._confidence = confidence
        self._model = None
        self._loaded = False

        # Importar aquí para lazy loading
        try:
            from ultralytics import YOLO
            self._model = YOLO(model_path)
            self._loaded = True
            logger.info(f"Modelo YOLO cargado correctamente: {model_path}")
        except Exception as e:
            logger.error(f"Error cargando modelo YOLO: {e}")
            raise

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """
        Realiza detección de objetos en un frame.
        
        Args:
            frame: Frame BGR de OpenCV
        
        Returns:
            Lista de detecciones filtradas por clases de interés
        """

        if not self._loaded or self._model is None:
            logger.error("Modelo YOLO no está cargado")
            return []

        try:
            # Realizar inferencia
            results = self._model(frame, conf=self._confidence, verbose=False)

            detections: list[Detection] = []

            for result in results:
                if result.boxes is None:
                    continue

                for box in result.boxes:
                    class_id = int(box.cls[0])

                    # Filtrar solo clases de interés
                    if class_id not in self.CLASSES_OF_INTEREST:
                        continue

                    confidence = float(box.conf[0])
                    x1, y1, x2, y2 = [int(c) for c in box.xyxy[0].tolist()]
                    class_name = self.CLASSES_OF_INTEREST[class_id]

                    detections.append(Detection(
                        class_name=class_name,
                        confidence=confidence,
                        x1=x1, y1=y1, x2=x2, y2=y2
                    ))

            if detections:
                logger.debug(f"YOLO detectó {len(detections)} objetos de interés")

            return detections

        except Exception as e:
            logger.error(f"Error en inferencia YOLO: {e}")
            return []

    def draw_detections(self, frame: np.ndarray, detections: list[Detection]) -> np.ndarray:
        """
        Dibuja las detecciones en el frame.
        
        Args:
            frame: Frame original
            detections: Lista de detecciones a dibujar
        
        Returns:
            Frame anotado con bounding boxes
        """

        annotated = frame.copy()

        # Colores por clase (BGR)
        colors = {
            "person": (0, 255, 0),
            "car": (0, 0, 255),
            "motorcycle": (0, 255, 255),
            "bus": (255, 0, 0),
            "truck": (255, 255, 0)
        }

        for det in detections:
            color = colors.get(det.class_name, (255, 255, 255))

            # Dibujar rectángulo
            cv2.rectangle(annotated, (det.x1, det.y1), (det.x2, det.y2), color, 2)

            # Dibujar label con fondo
            label = f"{det.class_name}: {det.confidence:.2f}"
            (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)

            # Fondo del texto
            cv2.rectangle(
                annotated, 
                (det.x1, det.y1 - text_h - 10), 
                (det.x1 + text_w, det.y1), 
                color, 
                -1
            )

            # Texto
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
