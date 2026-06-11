"""
================================================================================
MÓDULO: motion_detector — Detección de movimiento (compuerta de la IA)
================================================================================

PROPÓSITO
    Decidir, frame a frame, si en la escena HAY movimiento suficiente como para
    valer la pena ejecutar la inferencia YOLOv8 (cara en CPU). Es la "compuerta"
    (gate) barata que evita que el modelo corra sobre escenas estáticas.

RESPONSABILIDAD PRINCIPAL
    Comparar el frame actual contra el anterior por DIFERENCIA ABSOLUTA en
    escala de grises (background subtraction simple), binarizar el resultado y
    devolver un score (fracción de píxeles que cambiaron) + un booleano
    `has_motion` cuando ese score supera el umbral de sensibilidad.

POR QUÉ DIFERENCIA DE FRAMES (y no MOG2/KNN)
    Es O(píxeles) sin estado pesado ni aprendizaje de fondo: rapidísimo y
    suficiente para gatear YOLO en un appliance LAN. El blur gaussiano + umbral
    + dilatación reducen falsos positivos por ruido del sensor/compresión.

DEPENDENCIAS
    cv2 (OpenCV) ........ cvtColor / GaussianBlur / absdiff / threshold / dilate
    numpy ............... cálculo del score y máscara

COMPONENTES RELACIONADOS
    AIScheduler ......... instancia UN MotionDetector por cámara y lo invoca en
                          su worker thread ANTES de cada posible llamada a YOLO.
    YLOModelPool ........ solo se invoca si este detector devuelve has_motion.

PUNTO DE ENTRADA
    MotionDetector(camera_id, sensitivity).detect(frame) → MotionResult
    El estado (frame previo) vive en la instancia; reset() lo limpia cuando la
    fuente muere (evita comparar contra un frame "stale").

PIPELINE(S)
    Pipeline de IA (#9) — ETAPA 3 (compuerta de movimiento):
        go2rtc substream low → AIFrameSource (decode→BGR letterbox)
          → [MotionDetector.detect] ──has_motion?──> YLOModelPool.infer (YOLOv8)
          → AIScheduler publica EventData → EventManager → grabación/notif.
    Si has_motion == False, el frame se descarta y NO se gasta CPU en YOLO.
================================================================================
"""


import cv2
import numpy as np
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class MotionResult:
    """
    DTO inmutable con el veredicto de un análisis de movimiento.

    ROL: contrato de salida de MotionDetector.detect(); el AIScheduler solo lee
    `has_motion` (para gatear YOLO) y loguea `motion_score`. `motion_mask` se
    expone para depuración/visualización pero el pipeline no la consume.

    Campos:
        has_motion ... True si motion_score > sensitivity (dispara YOLO).
        motion_score . fracción [0..1] de píxeles que cambiaron respecto al
                       frame previo (p.ej. 0.015 = 1.5% de la imagen).
        motion_mask .. máscara binaria uint8 (255 = píxel cambiado) tras
                       umbral + dilatación; mismo tamaño que el frame en gris.
    """

    has_motion: bool
    motion_score: float
    motion_mask: np.ndarray


class MotionDetector:
    """
    Detector de movimiento por diferencia de frames (compuerta de la IA).

    RESPONSABILIDAD / ROL
        Mantener el último frame en gris+blur como "fondo" y compararlo con cada
        frame nuevo; devolver un MotionResult que el AIScheduler usa para decidir
        si ejecutar YOLO. Optimizado para reducir falsos positivos por ruido
        (blur gaussiano 21×21, umbral 25, dilatación ×2).

    QUIÉN LA INSTANCIA / CONSUME
        Instanciado por AIScheduler.__init__ (uno por cámara, con la sensibilidad
        de settings.AI_MOTION_SENSITIVITY). Consumido SOLO desde el worker thread
        del scheduler → NO es thread-safe por sí mismo, pero no lo necesita
        porque un único hilo llama detect()/reset() en serie.

    ESTADO (vive en memoria de la instancia, no es singleton)
        _prev_gray ... frame anterior (gris+blur) o None tras reset/al arrancar.
        _kernel ...... kernel 3×3 reutilizado para la dilatación morfológica.

    PIPELINE
        Pipeline de IA (#9), ETAPA 3 (compuerta). Ver docstring del módulo.
    """


    def __init__(self, camera_id: int, sensitivity: float = 0.02):
        """
        Inicializa el detector de movimiento.
        
        Args:
            camera_id: ID de la cámara para logging
            sensitivity: Umbral de sensibilidad (0.0 - 1.0, default 0.02 = 2%)
        """

        self.camera_id = camera_id
        self.sensitivity = sensitivity
        self._prev_gray: np.ndarray | None = None
        self._kernel = np.ones((3, 3), np.uint8)
        logger.info(f"MotionDetector inicializado para cámara {camera_id} (sensibilidad: {sensitivity})")

    def detect(self, frame: np.ndarray) -> MotionResult:
        """
        Compara el frame con el anterior y decide si hay movimiento.

        PROPÓSITO (Pipeline de IA #9, ETAPA 3 — compuerta)
            Producir el veredicto que gatea la inferencia YOLO. El PRIMER frame
            tras arrancar/reset solo inicializa el fondo y devuelve has_motion=False
            (no hay con qué comparar todavía).

        ALGORITMO
            gris → blur 21×21 → absdiff(prev, actual) → threshold(25) →
            dilate(×2) → score = píxeles_activos / total → has_motion = score > sensitivity.

        Inputs:
            frame: ndarray BGR (OpenCV) — típicamente el letterbox 640×384 que
                entrega AIFrameSource.

        Outputs:
            MotionResult(has_motion, motion_score, motion_mask).

        Efecto colateral:
            Actualiza self._prev_gray con el frame actual (sin .copy(): ver nota
            interna — GaussianBlur ya devuelve un array nuevo).

        Llamado por:
            AIScheduler._inference_worker (una vez por frame tomado del slot).

        Llama a:
            cv2.cvtColor/GaussianBlur/absdiff/threshold/dilate, numpy.

        Siguiente etapa:
            Si has_motion → AIScheduler invoca YLOModelPool.detect(frame).
            Si no → el frame se descarta sin gastar CPU en YOLO.
        """

        # Convertir a escala de grises
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Aplicar blur gaussiano para reducir ruido
        gray_blurred = cv2.GaussianBlur(gray, (21, 21), 0)

        # Si no hay frame previo, inicializar y retornar sin movimiento
        if self._prev_gray is None:
            self._prev_gray = gray_blurred
            return MotionResult(
                has_motion=False,
                motion_score=0.0,
                motion_mask=np.zeros_like(gray)
            )

        # Calcular diferencia absoluta entre frames
        diff = cv2.absdiff(self._prev_gray, gray_blurred)

        # Aplicar umbral para binarizar
        _, mask = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

        # Dilatar para conectar regiones cercanas
        mask = cv2.dilate(mask, self._kernel, iterations=2)

        # Calcular score de movimiento (porcentaje de pixeles activos)
        motion_score = np.sum(mask > 0) / mask.size

        # Actualizar frame previo. NO se necesita .copy(): cv2.GaussianBlur
        # devuelve un array NUEVO en cada llamada (no es vista del frame), y
        # solo se lee de forma no destructiva en cv2.absdiff. Copiar aquí eran
        # ~345KB/frame (×fps) de memcpy puro sin beneficio.
        self._prev_gray = gray_blurred

        has_motion = motion_score > self.sensitivity

        if has_motion:
            logger.debug(f"Cámara {self.camera_id}: Movimiento detectado (score: {motion_score:.4f})")

        return MotionResult(
            has_motion=has_motion,
            motion_score=motion_score,
            motion_mask=mask
        )

    def reset(self) -> None:
        """
        Olvida el frame de fondo (_prev_gray = None).

        PROPÓSITO: evitar comparar contra un frame "stale" cuando el stream se
        pausa o muere. Tras reset, el siguiente detect() reinicializa el fondo y
        devuelve has_motion=False (sin falso positivo por el salto temporal).

        Llamado por:
            AIScheduler.stop() y el worker cuando detecta que la fuente murió
            (AIFrameSource.is_alive() == False).
        """

        self._prev_gray = None
        logger.debug(f"MotionDetector reseteado para cámara {self.camera_id}")
