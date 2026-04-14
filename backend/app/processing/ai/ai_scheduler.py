"""
AI Scheduler v2.0 - Orquestador de procesamiento de IA desacoplado.
El motion detection ahora ocurre en el worker thread, no en el callback del distributor.
"""
import threading
import logging
import time
from typing import Callable, Dict
from collections import defaultdict

from ..motion.motion_detector import MotionDetector, MotionResult
from .model_pool import YLOModelPool, Detection
from .inference_queue import InferenceQueue, InferenceTask
from ...streaming.frame_buffer import FrameData

logger = logging.getLogger(__name__)


class AIScheduler:
    """
    Scheduler que gestiona el pipeline de IA para una cámara específica.
    
    FIX F1.2: Arquitectura desacoplada:
    - El distributor solo encola frames (sin procesamiento)
    - El worker hace motion detection + inferencia YOLO
    - No bloquea el hilo del distributor
    """

    def __init__(self, camera_id: int, mode: str = "low_cpu", cooldown_seconds: int = 30):
        self.camera_id = camera_id
        self.mode = mode
        self._cooldown_seconds = cooldown_seconds

        # Componentes (inicializados pero no activos hasta start)
        self._motion_detector = MotionDetector(camera_id)
        self._yolo = YLOModelPool()
        self._inference_queue = InferenceQueue(maxsize=5)

        # Estado
        self._running = False
        self._frame_count = 0
        self._frames_processed = 0
        self._detections_count = 0
        
        # Cooldown tracking
        self._last_alert_time: Dict[str, float] = {}
        self._cooldown_lock = threading.Lock()

        # Threading
        self._worker_thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Callback para eventos de detección
        self._on_detection_callback: Callable[[int, str, float, object, dict], None] | None = None

        logger.info(f"AIScheduler inicializado para cámara {camera_id} "
                   f"(modo: {mode}, cooldown: {cooldown_seconds}s)")

    @property
    def _inference_interval(self) -> int:
        """Intervalo de frames para encolar según el modo."""
        if self.mode == "high_quality":
            return 6
        return 12

    def set_detection_callback(self, callback: Callable[[int, str, float, object, dict], None]) -> None:
        self._on_detection_callback = callback
        logger.debug(f"Callback de detección asignado para cámara {self.camera_id}")

    def start(self, frame_distributor) -> None:
        if self._running:
            logger.warning(f"AIScheduler {self.camera_id} ya está corriendo")
            return

        self._running = True

        # Registrar como consumidor de frames (lightweight, solo encola)
        consumer_name = f"ai_scheduler_{self.camera_id}"
        frame_distributor.register_consumer(
            consumer_name, 
            self._on_frame,  # FIX F1.2: Solo encola, no procesa aquí
            needs_copy=True  # Necesitamos copia porque el frame se procesa async
        )

        # FIX F1.2: Iniciar worker thread único
        self._worker_thread = threading.Thread(
            target=self._inference_worker, 
            daemon=True,
            name=f"AI-Worker-Cam{self.camera_id}"
        )
        self._worker_thread.start()

        logger.info(f"AIScheduler {self.camera_id} iniciado con worker dedicado")

    def stop(self, frame_distributor) -> None:
        if not self._running:
            return

        self._running = False

        # Desregistrar consumidor
        consumer_name = f"ai_scheduler_{self.camera_id}"
        frame_distributor.unregister_consumer(consumer_name)

        # Esperar worker thread
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)

        # Limpiar motion detector
        self._motion_detector.reset()

        logger.info(f"AIScheduler {self.camera_id} detenido")

    def _on_frame(self, frame_data: FrameData) -> None:
        """
        FIX F1.2: Callback ligero del distributor.
        Solo cuenta frames y encola para procesamiento async.
        No hace motion detection aquí (no bloquea distributor).
        """
        if not self._running:
            return

        with self._lock:
            self._frame_count += 1

        # Throttling por intervalo (evitar sobrecargar cola)
        if self._frame_count % self._inference_interval != 0:
            return

        # Encolar para procesamiento async (no bloquea si cola llena, descarta)
        try:
            self._inference_queue.enqueue(
                self.camera_id,
                frame_data.frame.copy(),  # Copia aquí porque la procesaremos async
                frame_data.timestamp
            )
        except Exception as e:
            logger.error(f"Error encolando frame: {e}")

    def _inference_worker(self) -> None:
        """
        FIX F1.2: Worker thread dedicado.
        Realiza motion detection + YOLO inference sin bloquear el distributor.
        """
        logger.info(f"Worker de IA iniciado para cámara {self.camera_id}")

        while self._running:
            try:
                # Obtener frame de la cola (bloquea hasta 1 segundo)
                task = self._inference_queue.dequeue(timeout=1.0)
                if task is None:
                    continue

                # FIX F1.2: Motion detection ocurre aquí, no en el distributor
                motion_result = self._motion_detector.detect(task.frame)
                
                if not motion_result.has_motion:
                    continue  # No hay movimiento, descartar frame

                # Procesar con YOLO (bloqueante, pero en thread dedicado)
                detections = self._yolo.detect(task.frame)

                with self._lock:
                    self._frames_processed += 1

                # Procesar detecciones con cooldown
                if detections and self._on_detection_callback:
                    self._process_detections_with_cooldown(detections, task)

            except Exception as e:
                logger.error(f"Error en worker de inferencia: {e}")
                time.sleep(0.1)

        logger.info(f"Worker de inferencia finalizado para cámara {self.camera_id}")

    def _process_detections_with_cooldown(self, detections: list[Detection], task) -> None:
        """
        Agrupa detecciones por clase y aplica cooldown.
        """
        current_time = time.time()
        
        # Agrupar detecciones por clase
        grouped = defaultdict(list)
        for detection in detections:
            grouped[detection.class_name].append(detection)
            with self._lock:
                self._detections_count += 1
        
        # Procesar UNA alerta por clase
        for class_name, class_detections in grouped.items():
            
            # Verificar cooldown
            with self._cooldown_lock:
                last_time = self._last_alert_time.get(class_name, 0)
                time_since_last = current_time - last_time
                
                if time_since_last < self._cooldown_seconds:
                    logger.debug(
                        f"Cooldown activo para '{class_name}' en cámara {self.camera_id} "
                        f"(hace {time_since_last:.1f}s)"
                    )
                    continue
                
                # Actualizar timestamp de última alerta
                self._last_alert_time[class_name] = current_time
            
            # Preparar metadata
            count = len(class_detections)
            best_confidence = max(d.confidence for d in class_detections)
            
            metadata = {
                "count": count,
                "objects": [d.class_name for d in class_detections],
                "confidences": [d.confidence for d in class_detections],
                "timestamp": task.timestamp,
                "cooldown_applied": True
            }
            
            # Si es un solo objeto, incluir bounding box
            if count == 1:
                d = class_detections[0]
                metadata["bbox"] = {"x1": d.x1, "y1": d.y1, "x2": d.x2, "y2": d.y2}
            
            # Enviar alerta
            try:
                self._on_detection_callback(
                    self.camera_id,
                    class_name,
                    best_confidence,
                    task.frame,
                    metadata
                )
                
                log_msg = f"Alerta enviada: {class_name} (x{count}, conf: {best_confidence:.0%})"
                if count > 1:
                    log_msg += f" - {count} objetos agrupados"
                logger.info(log_msg)
                
            except Exception as e:
                logger.error(f"Error en callback de detección: {e}")

    def reset_cooldown(self, class_name: str | None = None) -> None:
        """Resetea el cooldown para una clase específica o todas."""
        with self._cooldown_lock:
            if class_name is None:
                self._last_alert_time.clear()
                logger.info(f"Cooldowns reseteados para todas las clases en cámara {self.camera_id}")
            elif class_name in self._last_alert_time:
                del self._last_alert_time[class_name]
                logger.info(f"Cooldown reseteado para '{class_name}' en cámara {self.camera_id}")

    def set_mode(self, mode: str) -> None:
        """Cambia el modo de operación."""
        if mode not in ("low_cpu", "high_quality"):
            raise ValueError(f"Modo inválido: {mode}")
        self.mode = mode
        logger.info(f"Modo cambiado a {mode} para cámara {self.camera_id}")

    def get_stats(self) -> dict:
        """Retorna estadísticas del scheduler."""
        with self._lock:
            stats = {
                "camera_id": self.camera_id,
                "mode": self.mode,
                "frames_received": self._frame_count,
                "frames_processed": self._frames_processed,
                "detections": self._detections_count,
                "queue_size": self._inference_queue.size(),
                "cooldown_seconds": self._cooldown_seconds,
                "active_cooldowns": len(self._last_alert_time)
            }
            return stats