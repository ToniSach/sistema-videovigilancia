"""
================================================================================
MÓDULO: ai_scheduler — Orquestador de inferencia IA en un HILO worker dedicado
================================================================================

PROPÓSITO
    Orquestar el tramo de cómputo del Pipeline de IA (#9) para UNA cámara:
    sondear la fuente de frames, gatear el caro YOLO con detección de
    movimiento, ejecutar la inferencia, aplicar cooldown por clase y publicar
    el resultado hacia arriba mediante un callback (que termina en EventData →
    EventManager → grabación/notificaciones).

RESPONSABILIDAD PRINCIPAL
    Ser el ÚNICO consumidor del AIFrameSource (slot-1, latest-wins) y, en su
    propio hilo, decidir qué frames merecen YOLO. El movimiento actúa de
    "compuerta" (gate): si no hay movimiento NO se llama a YOLO (que es lo caro),
    ahorrando CPU/GPU. Las alertas resultantes se de-duplican con un cooldown
    por clase para no inundar de notificaciones.

    Arquitectura DESACOPLADA (fix F1.2): históricamente el motion+inferencia
    corría en el callback del FrameDistributor (bloqueaba el hilo de captura).
    Ahora todo el cómputo vive en un worker thread propio que SONDEA el slot del
    AIFrameSource (sin colas, sin distributor) → la captura nunca se bloquea.

RESTRICCIÓN — UNA SOLA CÁMARA CON YOLO
    Aunque AIScheduler es por-cámara, solo UNA instancia está activa a la vez
    (la indicada por AI_CAMERA_ID): comparten el YLOModelPool singleton, que
    mantiene un único modelo en memoria. No asumir IA simultánea multi-cámara.

DEPENDENCIAS
    AIFrameSource ......... fuente de frames slot-1 (se inyecta en start()).      [etapa 2]
    MotionDetector ........ gate: detect(frame) → has_motion / motion_score.      [etapa 3]
    YLOModelPool .......... singleton YOLOv8; detect(frame) → List[Detection].     [etapa 4]
    config.settings ....... sensibilidad, FPS de proceso, intervalos, cooldown.
    threading ............. worker daemon + locks de estado y de cooldown.

COMPONENTES RELACIONADOS
    AIService.activate_ai .. crea AIFrameSource, lo arranca, instancia el
                             AIScheduler, registra el callback de detección y
                             llama a start(frame_source). check_dependencies()
                             del YLOModelPool corre AHÍ (upfront) para que un
                             fallo salga como 503 AI_DEPENDENCIES_MISSING.
    EventManager ........... destino final del callback (vía EventService):
                             publica EventData(person/vehicle).

PUNTO DE ENTRADA
    AIScheduler(camera_id, mode, cooldown_seconds) → set_detection_callback(cb)
    → start(frame_source). El worker (_inference_worker) hace el trabajo;
    stop() lo detiene y resetea el MotionDetector.

PIPELINE(S)
    Pipeline de IA (#9) — ETAPAS 3-5 (gate de movimiento → inferencia → alerta).
    Diagrama del pipeline completo (este módulo = recuadro central):

        go2rtc substream low ──RTSP──> AIFrameSource (decode→BGR letterbox 640x384)
                                              │ get_latest() (seq, frame)   [etapa 2]
        ┌─────────────────────────────────────▼──────────────────────────────────┐
        │ AIScheduler._inference_worker (HILO daemon, throttle a AI_SOURCE_FPS)    │
        │   1. ¿frame nuevo? (seq cambió)                                          │
        │   2. MotionDetector.detect → has_motion?  ── NO ──▶ descartar  [etapa 3] │
        │           │ SÍ                                                           │
        │   3. YLOModelPool.detect(frame) → List[Detection]              [etapa 4] │
        │   4. _process_detections_with_cooldown (1 alerta/clase)        [etapa 5] │
        └─────────────────────────────────────┬──────────────────────────────────┘
                                              │ callback(cam, clase, conf, frame, meta)
                                              ▼
                          AIService → EventData → EventManager → grabación/notificaciones
================================================================================
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
    Scheduler que gestiona el tramo de cómputo del Pipeline de IA (#9, etapas
    3-5) para UNA cámara específica.

    ROL: en su propio hilo worker, sondea el AIFrameSource, gatea YOLO con
    movimiento, ejecuta la inferencia y emite alertas de-duplicadas por cooldown
    de clase a través de un callback.

    QUIÉN LO INSTANCIA / CONSUME
        Instanciado por AIService.activate_ai (uno por cámara activa, pero solo
        AI_CAMERA_ID corre a la vez). No es singleton; sí comparte el
        YLOModelPool singleton. Su "consumidor" es el callback de detección que
        registra AIService → termina en EventManager.

    DEPENDENCIAS
        AIFrameSource (inyectado en start), MotionDetector (gate), YLOModelPool
        (inferencia), settings (sensibilidad/FPS/intervalos/cooldown).

    THREADING
        start() lanza UN hilo daemon (_inference_worker), único que procesa
        frames. _lock protege contadores/stats; _cooldown_lock protege el
        diccionario de últimas alertas por clase. Diseño desacoplado (fix F1.2):
        el cómputo NO corre en el callback del distributor → la captura no se
        bloquea jamás.
    """

    def __init__(self, camera_id: int, mode: str = "low_cpu", cooldown_seconds: int = 30):
        self.camera_id = camera_id
        self.mode = mode
        self._cooldown_seconds = cooldown_seconds

        # Cooldown ESPECÍFICO para "movimiento general". El movimiento se dispara
        # muchísimo más a menudo que persona/vehículo (cualquier cambio en la
        # escena), así que con el cooldown corto de clase (p.ej. 5s) inundaría de
        # notificaciones, fotos, clips y eventos. Por eso lleva su propio cooldown
        # más largo (configurable AI_MOTION_NOTIFY_COOLDOWN, por defecto 120s).
        # Además exige un score mínimo para ignorar micro-movimientos triviales.
        try:
            from backend.app.config import settings
            motion_sensitivity = float(getattr(settings, "AI_MOTION_SENSITIVITY", 0.015))
            self._proc_fps = float(getattr(settings, "AI_SOURCE_FPS", 6))
            self._motion_cooldown_seconds = float(
                getattr(settings, "AI_MOTION_NOTIFY_COOLDOWN", 120)
            )
            self._motion_notify_min_score = float(
                getattr(settings, "AI_MOTION_NOTIFY_MIN_SCORE", 0.06)
            )
        except Exception:
            motion_sensitivity = 0.015
            self._proc_fps = 6.0
            self._motion_cooldown_seconds = 120.0
            self._motion_notify_min_score = 0.06
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
        """
        Registra el callback que recibe cada alerta tras pasar el cooldown.

        PROPÓSITO (Pipeline #9, ETAPA 5→salida): conectar el scheduler con el
        consumidor de detecciones. Firma del callback:
        (camera_id, class_name, confidence, frame, metadata).

        Inputs: callback — invocado por _process_detections_with_cooldown.
        Llamado por: AIService.activate_ai (apunta a su handler, que construye
            EventData y lo publica en EventManager).
        """
        self._on_detection_callback = callback
        logger.debug(f"Callback de detección asignado para cámara {self.camera_id}")

    def start(self, frame_source) -> None:
        """
        Arranca el worker de inferencia (Pipeline #9, entrada a etapas 3-5).

        PROPÓSITO: inyectar la fuente de frames ya iniciada y lanzar el hilo
        daemon que la sondea. Idempotente: si ya corre, avisa y no relanza.

        Inputs: frame_source — un AIFrameSource (slot-1, latest-wins) YA
            iniciado por AIService; el worker lee su último frame con
            get_latest() (sin colas ni FrameDistributor).
        Outputs: ninguno (efecto: hilo _inference_worker corriendo).
        Llamado por: AIService.activate_ai, tras frame_source.start() y
            set_detection_callback().
        Llama a: threading.Thread(_inference_worker).start().
        """
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
        """
        Detiene el worker, hace join del hilo, resetea el MotionDetector y
        suelta la referencia a la fuente. Idempotente. Llamado por AIService al
        desactivar la IA o al reciclar la cámara. NOTA: NO detiene el
        AIFrameSource (eso lo gestiona AIService) ni el YLOModelPool singleton.
        """
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
        Bucle del hilo worker: gate de movimiento + inferencia YOLO + alertas.

        PROPÓSITO (Pipeline #9, ETAPAS 3-5): por cada frame NUEVO del slot
        (seq cambió), correr el gate de movimiento; si hay movimiento, llamar a
        YOLO; si hay detecciones, emitir alertas de-duplicadas por cooldown. El
        movimiento es la "compuerta" que evita ejecutar YOLO (lo caro) en
        escenas estáticas.

        THROTTLE: aunque la fuente entregue ráfagas, se procesa a lo sumo a
        _proc_fps (AI_SOURCE_FPS), tomando siempre el frame MÁS reciente
        (latest-wins) y descartando intermedios → no malgasta CPU/GPU.

        Inputs: lee self._frame_source.get_latest() (seq, frame) en polling.
        Outputs: ninguno directo; efecto lateral = llamadas al callback de
            detección y actualización de contadores/latencias para get_stats().
        Excepciones: captura cualquier Exception por iteración, la loguea y
            sigue (un frame corrupto no tumba el worker).
        Llamado por: el hilo daemon creado en start() (NO invocar directamente).
        Llama a: MotionDetector.detect, YLOModelPool.detect,
            _process_detections_with_cooldown.
        Siguiente etapa: el callback → AIService → EventManager.

        Contadores de diagnóstico (resumen logueado cada ~30s):
          - frames recibidos (procesados tras el throttle)
          - frames con movimiento → ejecutan YOLO
          - detecciones YOLO totales (antes de cooldown)
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
                elif self._on_detection_callback:
                    # Hubo MOVIMIENTO pero YOLO no reconoció persona/vehículo →
                    # emitir alerta de "movimiento general" (el usuario puede
                    # pedir recibir cualquier movimiento). Cooldown propio por la
                    # clase "motion" para no inundar.
                    self._emit_motion_alert(frame, motion_result, timestamp)

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
        Agrupa detecciones por clase, aplica cooldown y emite UNA alerta/clase.

        PROPÓSITO (Pipeline #9, ETAPA 5): de-duplicar el ruido de YOLO. Varias
        cajas de la misma clase en un frame → UNA sola alerta (con count y la
        mejor confianza). Además, una clase que ya alertó hace menos de
        _cooldown_seconds se SILENCIA → no se reenvía la notificación.

        Inputs:
            detections — List[Detection] de YOLO para este frame.
            frame — el frame BGR (se pasa tal cual al callback para snapshot).
            timestamp — instante de captura del frame procesado.
        Outputs: ninguno; efecto = 0..N llamadas al callback de detección (una
            por clase que supera el cooldown). metadata incluye count, objects,
            confidences, timestamp y bbox (solo si count==1).
        Excepciones: si el callback lanza, se loguea y se continúa con la
            siguiente clase (no aborta el resto de alertas).
        Llamado por: _inference_worker, solo cuando hay detecciones y callback.
        Llama a: self._on_detection_callback(...).
        Siguiente etapa: AIService construye EventData y lo publica en
            EventManager (→ grabación de evento + notificaciones).
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

    def _emit_motion_alert(self, frame, motion_result: MotionResult, timestamp: float) -> None:
        """
        Emite UNA alerta de "movimiento general" (clase "motion") cuando hubo
        movimiento pero YOLO no reconoció ningún objeto (persona/vehículo).

        PROPÓSITO (Pipeline #9, ETAPA 5 alterna): permitir que el usuario reciba
        avisos de cualquier movimiento, no solo de objetos reconocidos. Aplica el
        MISMO cooldown por clase que las detecciones (clave "motion") para no
        inundar de notificaciones. La confianza se deriva del score de movimiento
        (acotada a [0.30, 1.0], solo informativa).

        Inputs: frame (BGR, para snapshot aguas abajo), motion_result (score),
            timestamp del frame.
        Outputs: ninguno; efecto = 0..1 llamada al callback de detección.
        Llamado por: _inference_worker cuando hay movimiento y YOLO no detecta nada.
        """
        score = float(getattr(motion_result, "motion_score", 0.0) or 0.0)

        # Ignorar micro-movimientos triviales (ruido del sensor, cambios de luz):
        # solo notificar si el movimiento supera un mínimo. Esto evita avisos por
        # un score de 0.03 cuando "no pasa nada".
        if score < self._motion_notify_min_score:
            return

        # Cooldown LARGO y propio del movimiento (no el de clase, que es corto):
        # como mucho una alerta de movimiento cada _motion_cooldown_seconds.
        current_time = time.time()
        with self._cooldown_lock:
            last_time = self._last_alert_time.get("motion", 0)
            if current_time - last_time < self._motion_cooldown_seconds:
                return
            self._last_alert_time["motion"] = current_time

        confidence = min(1.0, max(0.30, score))
        metadata = {
            "count": 1,
            "objects": ["motion"],
            "motion_score": score,
            "timestamp": timestamp,
            "cooldown_applied": True,
        }

        try:
            self._on_detection_callback(
                self.camera_id, "motion", confidence, frame, metadata
            )
            logger.info(
                f"[AI cam={self.camera_id}] Alerta de movimiento general "
                f"(score={score:.3f})"
            )
        except Exception as e:
            logger.error(f"Error en callback de movimiento: {e}")

    def reset_cooldown(self, class_name: str | None = None) -> None:
        """
        Borra el cooldown de una clase (o de todas si class_name es None) para
        que la PRÓXIMA detección alerte de inmediato. Útil para tests o para
        forzar una nueva notificación. Llamado por la API/AIService. Thread-safe
        (toma _cooldown_lock).
        """
        with self._cooldown_lock:
            if class_name is None:
                self._last_alert_time.clear()
                logger.info(f"Cooldowns reseteados para todas las clases en cámara {self.camera_id}")
            elif class_name in self._last_alert_time:
                del self._last_alert_time[class_name]
                logger.info(f"Cooldown reseteado para '{class_name}' en cámara {self.camera_id}")

    def set_mode(self, mode: str) -> None:
        """
        Cambia el modo de operación ("low_cpu" | "high_quality"), que afecta a
        _inference_interval (cada cuántos frames se considera la inferencia).
        Lanza ValueError si el modo no es válido. Llamado por AIService/API.
        """
        if mode not in ("low_cpu", "high_quality"):
            raise ValueError(f"Modo inválido: {mode}")
        self.mode = mode
        logger.info(f"Modo cambiado a {mode} para cámara {self.camera_id}")

    def get_stats(self) -> dict:
        """
        Devuelve un snapshot de métricas del scheduler para la API de estado y
        la prueba de carga de IA: frames recibidos/procesados, detecciones,
        salud de la fuente, cooldowns activos y latencia de inferencia YOLO
        (última, media y p95 sobre la ventana _infer_ms, más fps derivado).
        Thread-safe (toma _lock). Llamado por AIService/endpoints de stats.
        """
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