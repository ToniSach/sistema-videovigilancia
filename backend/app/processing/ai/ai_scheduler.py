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
        try:
            from backend.app.config import settings
            motion_sensitivity = float(getattr(settings, "AI_MOTION_SENSITIVITY", 0.015))
        except Exception:
            motion_sensitivity = 0.015
        self._motion_detector = MotionDetector(camera_id, sensitivity=motion_sensitivity)
        self._yolo = YLOModelPool()
        self._inference_queue = InferenceQueue(maxsize=5)

        # Estado
        self._running = False
        self._frame_count = 0
        self._frames_processed = 0
        self._detections_count = 0
        # Latencia de inferencia: ventana de las últimas N duraciones (ms) de la
        # llamada a YOLO, para reportar media/p95 en stats (prueba de carga IA).
        from collections import deque
        self._infer_ms = deque(maxlen=50)
        self._last_infer_ms = 0.0
        
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
        """
        Intervalo de frames para encolar (1 cada N) según el modo.
        Configurable vía .env: AI_INFERENCE_INTERVAL_LOW / _HIGH.
        Default: low=6 (~2.5 inf/s a 15fps), high=3 (~5 inf/s).
        """
        try:
            from backend.app.config import settings
            if self.mode == "high_quality":
                return int(getattr(settings, "AI_INFERENCE_INTERVAL_HIGH", 3))
            return int(getattr(settings, "AI_INFERENCE_INTERVAL_LOW", 6))
        except Exception:
            return 3 if self.mode == "high_quality" else 6

    def set_detection_callback(self, callback: Callable[[int, str, float, object, dict], None]) -> None:
        self._on_detection_callback = callback
        logger.debug(f"Callback de detección asignado para cámara {self.camera_id}")

    def start(self, frame_distributor) -> None:
        if self._running:
            logger.warning(f"AIScheduler {self.camera_id} ya está corriendo")
            return

        self._running = True

        # Registrar como consumidor de frames (lightweight, solo encola)
        # needs_copy=False: _on_frame hace su PROPIA copia al enqueue
        # (`frame_data.frame.copy()`). Tener needs_copy=True aquí causaba
        # DOBLE memcpy por frame: una en el distribuidor + otra al enqueue.
        # Para dual-lens con frames de 1.5 MB a 15 fps eso eran 22 MB/s
        # de memcpy desperdiciado compitiendo con el encoder MJPEG por
        # ancho de banda de memoria → contribuía al delay de la live.
        consumer_name = f"ai_scheduler_{self.camera_id}"
        frame_distributor.register_consumer(
            consumer_name,
            self._on_frame,  # FIX F1.2: Solo encola, no procesa aquí
            needs_copy=False  # _on_frame copia internamente al enqueue
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
        Worker thread dedicado: motion detection + YOLO inference.

        Contadores para diagnóstico:
          - frames recibidos (decididos por motion)
          - frames con movimiento → ejecutan YOLO
          - detecciones YOLO totales (antes de cooldown)
          - alertas enviadas (después de cooldown)
        Se loguea un resumen cada N frames procesados.
        """
        logger.info(f"[AI cam={self.camera_id}] Worker iniciado, esperando frames…")

        frames_total = 0
        frames_with_motion = 0
        detections_total = 0
        last_summary_t = time.time()
        last_frame_t = time.time()

        while self._running:
            try:
                task = self._inference_queue.dequeue(timeout=1.0)
                if task is None:
                    # Sin frame en 1s. Si llevamos 5s sin frames, asumir que
                    # el stream upstream se reinició y reset al motion detector
                    # para no comparar contra frames stale.
                    if time.time() - last_frame_t > 5:
                        if self._motion_detector._prev_gray is not None:
                            logger.info(
                                f"[AI cam={self.camera_id}] Sin frames >5s, "
                                f"reseteando motion detector"
                            )
                            self._motion_detector.reset()
                            last_frame_t = time.time()  # evitar reset repetitivo
                    continue
                last_frame_t = time.time()

                frames_total += 1
                motion_result = self._motion_detector.detect(task.frame)

                if not motion_result.has_motion:
                    # Resumen periódico cada 30s aunque no haya movimiento
                    if time.time() - last_summary_t > 30:
                        logger.info(
                            f"[AI cam={self.camera_id}] últimos 30s: "
                            f"{frames_total} frames analizados, "
                            f"{frames_with_motion} con movimiento, "
                            f"{detections_total} detecciones"
                        )
                        frames_total = frames_with_motion = detections_total = 0
                        last_summary_t = time.time()
                    continue

                frames_with_motion += 1
                logger.debug(
                    f"[AI cam={self.camera_id}] Movimiento detectado "
                    f"(score={motion_result.motion_score:.3f}) → ejecutando YOLO"
                )

                _t_infer = time.perf_counter()
                detections = self._yolo.detect(task.frame)
                infer_ms = (time.perf_counter() - _t_infer) * 1000.0

                with self._lock:
                    self._frames_processed += 1
                    self._last_infer_ms = infer_ms
                    self._infer_ms.append(infer_ms)

                if detections:
                    detections_total += len(detections)
                    classes = [d.class_name for d in detections]
                    logger.info(
                        f"[AI cam={self.camera_id}] 🎯 YOLO detectó {len(detections)} objeto(s): "
                        f"{classes}"
                    )
                    if self._on_detection_callback:
                        self._process_detections_with_cooldown(detections, task)

                # Resumen periódico
                if time.time() - last_summary_t > 30:
                    logger.info(
                        f"[AI cam={self.camera_id}] últimos 30s: "
                        f"{frames_total} frames, {frames_with_motion} con movimiento, "
                        f"{detections_total} detecciones"
                    )
                    frames_total = frames_with_motion = detections_total = 0
                    last_summary_t = time.time()

            except Exception as e:
                logger.error(f"[AI cam={self.camera_id}] Error en worker: {e}")
                time.sleep(0.1)

        logger.info(f"[AI cam={self.camera_id}] Worker finalizado")

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
            samples = list(self._infer_ms)
            if samples:
                avg_ms = sum(samples) / len(samples)
                p95_ms = sorted(samples)[max(0, int(len(samples) * 0.95) - 1)]
            else:
                avg_ms = p95_ms = 0.0
            stats = {
                "camera_id": self.camera_id,
                "mode": self.mode,
                "frames_received": self._frame_count,
                "frames_processed": self._frames_processed,
                "detections": self._detections_count,
                "queue_size": self._inference_queue.size(),
                "cooldown_seconds": self._cooldown_seconds,
                "active_cooldowns": len(self._last_alert_time),
                # Latencia de inferencia YOLO (ms): última + media/p95 de la ventana.
                "inference_ms_last": round(self._last_infer_ms, 1),
                "inference_ms_avg": round(avg_ms, 1),
                "inference_ms_p95": round(p95_ms, 1),
                "inference_fps": round(1000.0 / avg_ms, 1) if avg_ms > 0 else 0.0,
            }
            return stats