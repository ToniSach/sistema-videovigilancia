"""
AI Scheduler - Orquestador de procesamiento de IA.
Coordina detección de movimiento, cola de inferencia y ejecución de YOLO.
"""


import threading
import logging
import time
from typing import Callable

from ..motion.motion_detector import MotionDetector, MotionResult
from .yolo_detector import YOLODetector, Detection
from .inference_queue import InferenceQueue, InferenceTask
from ...streaming.frame_buffer import FrameData

logger = logging.getLogger(__name__)


class AIScheduler:
    """
    Scheduler que gestiona el pipeline de IA para una cámara específica.
    
    Modos:
    - low_cpu: Procesa 1 de cada 12 frames (aprox 2.5 fps a 30fps)
    - high_quality: Procesa 1 de cada 6 frames (aprox 5 fps a 30fps)
    """


    def __init__(self, camera_id: int, mode: str = "low_cpu"):
        """
        Inicializa el scheduler de IA.
        
        Args:
            camera_id: ID de la cámara a monitorear
            mode: Modo de operación (low_cpu o high_quality)
        """

        self.camera_id = camera_id
        self.mode = mode

        # Componentes
        self._motion_detector = MotionDetector(camera_id)
        self._yolo = YOLODetector()
        self._inference_queue = InferenceQueue(maxsize=5)

        # Estado
        self._running = False
        self._frame_count = 0
        self._frames_processed = 0
        self._detections_count = 0

        # Threading
        self._worker_thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Callback para eventos de detección
        self._on_detection_callback: Callable[[int, str, float, object], None] | None = None

        logger.info(f"AIScheduler inicializado para cámara {camera_id} (modo: {mode})")

    @property
    def _inference_interval(self) -> int:
        """Intervalo de frames para inferencia según el modo."""

        if self.mode == "high_quality":
            return 6
        return 12

    def set_detection_callback(self, callback: Callable[[int, str, float, object], None]) -> None:
        """
        Establece callback para nuevas detecciones.
        
        Args:
            callback: Función(camera_id, class_name, confidence, frame)
        """

        self._on_detection_callback = callback
        logger.debug(f"Callback de detección asignado para cámara {self.camera_id}")

    def start(self, frame_distributor) -> None:
        """
        Inicia el scheduler y se registra como consumidor de frames.
        
        Args:
            frame_distributor: Instancia de FrameDistributor
        """

        if self._running:
            logger.warning(f"AIScheduler {self.camera_id} ya está corriendo")
            return

        self._running = True

        # Registrar como consumidor de frames
        consumer_name = f"ai_scheduler_{self.camera_id}"
        frame_distributor.register_consumer(consumer_name, self._on_frame)

        # Iniciar worker thread
        self._worker_thread = threading.Thread(target=self._inference_worker, daemon=True)
        self._worker_thread.start()

        logger.info(f"AIScheduler {self.camera_id} iniciado")

    def stop(self, frame_distributor) -> None:
        """
        Detiene el scheduler y libera recursos.
        
        Args:
            frame_distributor: Instancia de FrameDistributor
        """

        if not self._running:
            return

        self._running = False

        # Desregistrar consumidor
        consumer_name = f"ai_scheduler_{self.camera_id}"
        frame_distributor.unregister_consumer(consumer_name)

        # Esperar worker thread
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)
            if self._worker_thread.is_alive():
                logger.warning(f"Worker thread de cámara {self.camera_id} no terminó a tiempo")

        # Limpiar motion detector
        self._motion_detector.reset()

        logger.info(f"AIScheduler {self.camera_id} detenido")

    def _on_frame(self, frame_data: FrameData) -> None:
        """
        Callback llamado por FrameDistributor cuando hay nuevo frame.
        Ejecutado en thread separado del distributor.
        """

        if not self._running:
            return

        with self._lock:
            self._frame_count += 1

        # 1. Detección de movimiento (rápido, en thread actual)
        motion_result = self._motion_detector.detect(frame_data.frame)

        if not motion_result.has_motion:
            return  # No hay movimiento, no procesar

        # 2. Throttling por intervalo
        if self._frame_count % self._inference_interval != 0:
            return

        # 3. Encolar para inferencia asíncrona (no bloquear thread de streaming)
        try:
            self._inference_queue.enqueue(
                self.camera_id,
                frame_data.frame.copy(),
                frame_data.timestamp
            )
        except Exception as e:
            logger.error(f"Error encolando frame: {e}")

    def _inference_worker(self) -> None:
        """
        Worker thread que procesa la cola de inferencia.
        Ejecuta YOLO en frames con movimiento detectado.
        """

        logger.info(f"Worker de inferencia iniciado para cámara {self.camera_id}")

        while self._running:
            try:
                # Obtener tarea con timeout para permitir verificar _running periódicamente
                task = self._inference_queue.dequeue(timeout=1.0)

                if task is None:
                    continue

                # Procesar con YOLO
                detections = self._yolo.detect(task.frame)

                with self._lock:
                    self._frames_processed += 1

                # Notificar detecciones
                if detections and self._on_detection_callback:
                    for detection in detections:
                        with self._lock:
                            self._detections_count += 1

                        try:
                            self._on_detection_callback(
                                self.camera_id,
                                detection.class_name,
                                detection.confidence,
                                task.frame
                            )
                        except Exception as e:
                            logger.error(f"Error en callback de detección: {e}")

            except Exception as e:
                logger.error(f"Error en worker de inferencia: {e}")
                time.sleep(0.1)

        logger.info(f"Worker de inferencia finalizado para cámara {self.camera_id}")

    def set_mode(self, mode: str) -> None:
        """
        Cambia el modo de operación.
        
        Args:
            mode: low_cpu o high_quality
        """

        if mode not in ("low_cpu", "high_quality"):
            raise ValueError(f"Modo inválido: {mode}")

        self.mode = mode
        logger.info(f"Modo cambiado a {mode} para cámara {self.camera_id}")

    def get_stats(self) -> dict:
        """
        Retorna estadísticas del scheduler.
        
        Returns:
            Dict con métricas de rendimiento
        """

        with self._lock:
            return {
                "camera_id": self.camera_id,
                "mode": self.mode,
                "frames_received": self._frame_count,
                "frames_processed": self._frames_processed,
                "detections": self._detections_count,
                "queue_size": self._inference_queue.size(),
                "queue_dropped": self._inference_queue.dropped_count(),
                "inference_interval": self._inference_interval,
                "running": self._running
            }
