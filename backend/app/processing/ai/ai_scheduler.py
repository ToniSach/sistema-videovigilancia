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
            self._proc_fps = float(getattr(settings, "AI_SOURCE_FPS", 6))
        except Exception:
            motion_sensitivity = 0.015
            self._proc_fps = 6.0
        self._motion_detector = MotionDetector(camera_id, sensitivity=motion_sensitivity)
        self._yolo = YLOModelPool()
        self._frame_source = None  # AIFrameSource (slot-1), asignado en start()

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

    def start(self, frame_source) -> None:
        """Arranca el worker. `frame_source` es un AIFrameSource (slot-1)
        ya iniciado; el worker lee su último frame (sin colas ni distributor)."""
        if self._running:
            logger.warning(f"AIScheduler {self.camera_id} ya está corriendo")
            return

        self._frame_source = frame_source
        self._running = True

        self._worker_thread = threading.Thread(
            target=self._inference_worker,
            daemon=True,
            name=f"AI-Worker-Cam{self.camera_id}"
        )
        self._worker_thread.start()

        logger.info(f"AIScheduler {self.camera_id} iniciado (fuente dedicada slot-1)")

    def stop(self) -> None:
        if not self._running:
            return

        self._running = False

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)

        self._motion_detector.reset()
        self._frame_source = None

        logger.info(f"AIScheduler {self.camera_id} detenido")

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
        logger.info(f"[AI cam={self.camera_id}] Worker iniciado (lee slot del AIFrameSource)…")

        frames_total = 0
        frames_with_motion = 0
        detections_total = 0
        last_summary_t = time.time()
        last_seq = -1
        poll = 0.05  # espera corta entre comprobaciones del slot (sin busy-wait)
        # Throttle DEFENSIVO: procesar como máximo a _proc_fps, sea cual sea la
        # tasa de la fuente. Evita que un stream en ráfaga haga motion+YOLO a
        # cientos de fps (= "trabajo de más"). Siempre se toma el frame MÁS
        # reciente (latest-wins), descartando los intermedios.
        min_interval = 1.0 / max(1.0, self._proc_fps)
        last_proc_t = 0.0

        while self._running:
            try:
                now = time.time()
                wait = min_interval - (now - last_proc_t)
                if wait > 0:
                    time.sleep(min(wait, 0.2))
                    continue

                src = self._frame_source
                if src is None:
                    time.sleep(0.1)
                    continue

                seq, frame = src.get_latest()
                # Sin frame nuevo → esperar. Si la fuente lleva rato muerta,
                # reset del motion detector para no comparar contra frames stale.
                if frame is None or seq == last_seq:
                    if not src.is_alive() and self._motion_detector._prev_gray is not None:
                        self._motion_detector.reset()
                    time.sleep(poll)
                    continue
                last_seq = seq
                last_proc_t = time.time()
                timestamp = last_proc_t

                frames_total += 1
                with self._lock:
                    self._frame_count += 1
                motion_result = self._motion_detector.detect(frame)

                if not motion_result.has_motion:
                    if time.time() - last_summary_t > 30:
                        logger.info(
                            f"[AI cam={self.camera_id}] últimos 30s: "
                            f"{frames_total} frames, {frames_with_motion} con movimiento, "
                            f"{detections_total} detecciones"
                        )
                        frames_total = frames_with_motion = detections_total = 0
                        last_summary_t = time.time()
                    continue

                frames_with_motion += 1
                logger.debug(
                    f"[AI cam={self.camera_id}] Movimiento (score={motion_result.motion_score:.3f}) → YOLO"
                )

                _t_infer = time.perf_counter()
                detections = self._yolo.detect(frame)
                infer_ms = (time.perf_counter() - _t_infer) * 1000.0

                with self._lock:
                    self._frames_processed += 1
                    self._last_infer_ms = infer_ms
                    self._infer_ms.append(infer_ms)

                if detections:
                    detections_total += len(detections)
                    classes = [d.class_name for d in detections]
                    logger.info(
                        f"[AI cam={self.camera_id}] 🎯 YOLO detectó {len(detections)} objeto(s): {classes}"
                    )
                    if self._on_detection_callback:
                        self._process_detections_with_cooldown(detections, frame, timestamp)

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

    def _process_detections_with_cooldown(self, detections: list[Detection], frame, timestamp: float) -> None:
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
                "timestamp": timestamp,
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
                    frame,
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
                "queue_size": 0,  # ya no hay InferenceQueue (slot-1 latest-wins)
                "frame_source_alive": bool(self._frame_source and self._frame_source.is_alive()),
                "cooldown_seconds": self._cooldown_seconds,
                "active_cooldowns": len(self._last_alert_time),
                # Latencia de inferencia YOLO (ms): última + media/p95 de la ventana.
                "inference_ms_last": round(self._last_infer_ms, 1),
                "inference_ms_avg": round(avg_ms, 1),
                "inference_ms_p95": round(p95_ms, 1),
                "inference_fps": round(1000.0 / avg_ms, 1) if avg_ms > 0 else 0.0,
            }
            return stats