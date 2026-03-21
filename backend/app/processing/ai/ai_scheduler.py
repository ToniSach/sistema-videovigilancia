"""
AI Scheduler - Orquestador de procesamiento de IA.
Coordina detección de movimiento, cola de inferencia y ejecución de YOLO.
Implementa cooldown de alertas para evitar spam de notificaciones.
"""

import threading
import logging
import time
from typing import Callable, Dict
from collections import defaultdict

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
    
    COOLDOWN: Evita enviar alertas del mismo tipo de objeto durante N segundos.
    """


    def __init__(self, camera_id: int, mode: str = "low_cpu", cooldown_seconds: int = 30):
        """
        Inicializa el scheduler de IA.
        
        Args:
            camera_id: ID de la cámara a monitorear
            mode: Modo de operación (low_cpu o high_quality)
            cooldown_seconds: Segundos de espera entre alertas del mismo tipo 
                             (default: 30s para evitar spam)
        """
        self.camera_id = camera_id
        self.mode = mode
        self._cooldown_seconds = cooldown_seconds

        # Componentes
        self._motion_detector = MotionDetector(camera_id)
        self._yolo = YOLODetector()
        self._inference_queue = InferenceQueue(maxsize=5)

        # Estado
        self._running = False
        self._frame_count = 0
        self._frames_processed = 0
        self._detections_count = 0
        
        # NUEVO: Cooldown tracking - última alerta por clase de objeto
        # Ejemplo: {"person": 1234567890.0, "car": 1234567880.0}
        self._last_alert_time: Dict[str, float] = {}
        self._cooldown_lock = threading.Lock()

        # Threading
        self._worker_thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Callback para eventos de detección
        # Firma actualizada: callback(cam_id, class_name, confidence, frame, metadata)
        self._on_detection_callback: Callable[[int, str, float, object, dict], None] | None = None

        logger.info(f"AIScheduler inicializado para cámara {camera_id} "
                   f"(modo: {mode}, cooldown: {cooldown_seconds}s)")

    @property
    def _inference_interval(self) -> int:
        """Intervalo de frames para inferencia según el modo."""
        if self.mode == "high_quality":
            return 6
        return 12

    def set_detection_callback(self, callback: Callable[[int, str, float, object, dict], None]) -> None:
        """
        Establece callback para nuevas detecciones.
        
        Args:
            callback: Función(camera_id, class_name, confidence, frame, metadata)
                     donde metadata es dict con count, objects, etc.
        """
        self._on_detection_callback = callback
        logger.debug(f"Callback de detección asignado para cámara {self.camera_id}")

    def start(self, frame_distributor) -> None:
        """Inicia el scheduler y se registra como consumidor de frames."""
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
        """Detiene el scheduler y libera recursos."""
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
        """Callback llamado por FrameDistributor cuando hay nuevo frame."""
        if not self._running:
            return

        with self._lock:
            self._frame_count += 1

        # 1. Detección de movimiento
        motion_result = self._motion_detector.detect(frame_data.frame)

        if not motion_result.has_motion:
            return  # No hay movimiento, no procesar

        # 2. Throttling por intervalo (cada N frames)
        if self._frame_count % self._inference_interval != 0:
            return

        # 3. Encolar para inferencia asíncrona
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
        Implementa cooldown: agrupa detecciones y filtra por tiempo.
        """
        logger.info(f"Worker de inferencia iniciado para cámara {self.camera_id}")

        while self._running:
            try:
                task = self._inference_queue.dequeue(timeout=1.0)
                if task is None:
                    continue

                # Procesar con YOLO
                detections = self._yolo.detect(task.frame)

                with self._lock:
                    self._frames_processed += 1

                # NUEVO: Procesar detecciones con agrupación y cooldown
                if detections and self._on_detection_callback:
                    self._process_detections_with_cooldown(detections, task)

            except Exception as e:
                logger.error(f"Error en worker de inferencia: {e}")
                time.sleep(0.1)

        logger.info(f"Worker de inferencia finalizado para cámara {self.camera_id}")

    def _process_detections_with_cooldown(self, detections: list[Detection], task) -> None:
        """
        Procesa detecciones aplicando:
        1. Agrupación por clase (una alerta por tipo de objeto)
        2. Cooldown por clase (no alertar si ya alertamos hace poco)
        """
        current_time = time.time()
        
        # Agrupar detecciones por clase (person: [det1, det2], car: [det3])
        grouped = defaultdict(list)
        for detection in detections:
            grouped[detection.class_name].append(detection)
            with self._lock:
                self._detections_count += 1
        
        # Procesar UNA alerta por clase
        for class_name, class_detections in grouped.items():
            
            # VERIFICAR COOLDOWN
            with self._cooldown_lock:
                last_time = self._last_alert_time.get(class_name, 0)
                time_since_last = current_time - last_time
                
                if time_since_last < self._cooldown_seconds:
                    # Estamos en período de cooldown - silenciar esta alerta
                    logger.debug(
                        f"🚫 Cooldown activo para '{class_name}' en cámara {self.camera_id} "
                        f"(hace {time_since_last:.1f}s, mínimo {self._cooldown_seconds}s)"
                    )
                    continue  # Saltar esta alerta
                
                # Actualizar timestamp de última alerta
                self._last_alert_time[class_name] = current_time
            
            # Preparar metadata enriquecida
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
            
            # Enviar alerta (ahora sí, pasó el filtro de cooldown)
            try:
                self._on_detection_callback(
                    self.camera_id,
                    class_name,
                    best_confidence,
                    task.frame,
                    metadata
                )
                
                log_msg = f"✅ Alerta enviada: {class_name} (x{count}, conf: {best_confidence:.0%})"
                if count > 1:
                    log_msg += f" - {count} objetos agrupados"
                logger.info(log_msg)
                
            except Exception as e:
                logger.error(f"Error en callback de detección: {e}")

    def reset_cooldown(self, class_name: str | None = None) -> None:
        """
        Resetea el cooldown para una clase específica o todas.
        Útil para testing o para forzar una alerta inmediata.
        """
        with self._cooldown_lock:
            if class_name is None:
                self._last_alert_time.clear()
                logger.info(f"⏱️ Cooldowns reseteados para todas las clases en cámara {self.camera_id}")
            elif class_name in self._last_alert_time:
                del self._last_alert_time[class_name]
                logger.info(f"⏱️ Cooldown reseteado para '{class_name}' en cámara {self.camera_id}")

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