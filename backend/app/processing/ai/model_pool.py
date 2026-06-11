"""
================================================================================
MÓDULO: model_pool — Pool singleton del modelo YOLOv8 (inferencia compartida)
================================================================================

PROPÓSITO
    Mantener UNA única instancia del modelo YOLOv8 en memoria de proceso y
    exponer una API simple (detect → List[Detection]) para la etapa de
    inferencia del Pipeline de IA (#9). Encapsula la elección de runtime
    (PyTorch / ONNX / OpenVINO), la selección de dispositivo (CPU/CUDA), la
    carga perezosa, el warmup y la verificación de dependencias.

RESPONSABILIDAD PRINCIPAL
    - check_dependencies(): comprobar torch/torchvision/ultralytics ANTES de
      cargar nada, para que un entorno incompleto produzca un 503 HTTP
      (AI_DEPENDENCIES_MISSING) con el comando pip exacto, en lugar de morir
      silenciosamente en el hilo worker.
    - _resolve_model(): exportar (una vez, cacheado en disco) a OpenVINO/ONNX
      según AI_FORMAT y dispositivo; caer a PyTorch si el export falla.
    - detect(): inferir y convertir las cajas de YOLO al formato estándar
      Detection, filtrando solo las clases de videovigilancia (CLASSES_OF_INTEREST).
    - warmup(): cargar + inferencia dummy para que la primera detección real no
      pague el coste de carga/export.

RESTRICCIÓN — SINGLETON DE PROCESO
    YLOModelPool es singleton (__new__): un único modelo, compartido por el
    AIScheduler activo. Concuerda con AI_CAMERA_ID (solo una cámara corre YOLO).
    El acceso al modelo está serializado con _model_lock (inferencia thread-safe).

DEPENDENCIAS
    ultralytics.YOLO ...... carga e inferencia del modelo YOLOv8.
    torch / torchvision ... backend de cómputo; su ausencia = 503 upfront.
    numpy ................. frames BGR de entrada; frame dummy del warmup.
    config.settings ....... AI_MODEL, AI_CONFIDENCE, AI_FORMAT, AI_IMGSZ,
                            AI_BACKEND, USE_GPU_AI, AI_TORCH_THREADS.
    cv2 (lazy) ............ solo en draw_detections (debug/visualización).

COMPONENTES RELACIONADOS
    AIScheduler ........... ÚNICO consumidor de detect() (etapa 4 del pipeline).
    AIService.activate_ai .. llama a check_dependencies() upfront (→ 503) y a
                             warmup() en un hilo aparte al activar la IA.

PUNTO DE ENTRADA
    YLOModelPool() (devuelve el singleton). En arranque, main.py programa
    warmup() en un hilo. La instancia global `yolo_pool` está al final del módulo
    para quien quiera importar directamente.

PIPELINE(S)
    Pipeline de IA (#9) — ETAPA 4 (inferencia). Diagrama del tramo:

        AIScheduler (gate de movimiento OK) ── frame BGR ──▶ YLOModelPool.detect
                                                                  │ YOLO(frame)
                                                                  ▼
                              filtra CLASSES_OF_INTEREST → List[Detection]
                                                                  │
                                                                  ▼
                              AIScheduler etapa 5 (cooldown + callback)
================================================================================
"""
import logging
import threading
from typing import NamedTuple, List, Optional
import numpy as np

logger = logging.getLogger(__name__)


class Detection(NamedTuple):
    """
    Resultado estandarizado de una detección de objeto (DTO inmutable).

    Es el contrato entre YLOModelPool.detect (productor) y el AIScheduler
    (consumidor): class_name ya es legible (mapeado desde el id COCO),
    confidence en [0,1] y (x1,y1,x2,y2) es la bounding box en píxeles del frame.
    """
    class_name: str
    confidence: float
    x1: int
    y1: int
    x2: int
    y2: int


class YLOModelPool:
    """
    Pool singleton del modelo YOLOv8 (Pipeline #9, ETAPA 4 — inferencia).

    ROL: mantener UN único modelo en memoria y servir detect() thread-safe a
    quien lo necesite, abstrayendo runtime (PyTorch/ONNX/OpenVINO), dispositivo
    (CPU/CUDA), carga perezosa y warmup.

    QUIÉN LO INSTANCIA / CONSUME
        Singleton: cualquier YLOModelPool() devuelve la MISMA instancia. Lo
        consume el AIScheduler activo (uno a la vez, AI_CAMERA_ID). AIService
        usa check_dependencies()/warmup() en la activación. La instancia global
        `yolo_pool` (final del módulo) es para imports directos.

    SINGLETON
        Implementado con __new__ + _lock (doble verificación). __init__ se
        protege con la bandera _initialized para no reconfigurar en cada
        YLOModelPool(). El modelo en sí se carga perezosamente (lazy) la primera
        vez que se necesita, o explícitamente vía warmup().

    THREADING
        _lock — serializa la creación del singleton (clase).
        _model_lock — serializa la carga Y cada inferencia (la instancia de
        YOLO no es necesariamente re-entrante) → detect() es thread-safe.
    """

    _instance = None
    _lock = threading.Lock()

    # Mapeo de id de clase COCO → nombre legible. Filtro de interés: solo estas
    # clases (personas y vehículos) generan Detection; el resto se descarta.
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
        # Config (modelo, runtime, imgsz, hilos) desde settings.
        try:
            from backend.app.config import settings
            self._model_path = getattr(settings, "AI_MODEL", "yolov8n.pt")
            self._confidence = float(getattr(settings, "AI_CONFIDENCE", 0.35))
            self._format = getattr(settings, "AI_FORMAT", "auto").lower()
            self._imgsz = int(getattr(settings, "AI_IMGSZ", 640))
            self._torch_threads = int(getattr(settings, "AI_TORCH_THREADS", 0))
        except Exception:
            self._model_path = "yolov8n.pt"
            self._confidence = 0.35
            self._format = "auto"
            self._imgsz = 640
            self._torch_threads = 0
        self._runtime = "pytorch"  # se ajusta al cargar
        self._device = self._select_device()
        logger.info(
            f"YLOModelPool inicializado: model={self._model_path}, "
            f"device={self._device}, format={self._format}, imgsz={self._imgsz}, "
            f"confidence={self._confidence}"
        )

    def _resolve_model(self) -> tuple[str, str]:
        """
        Resuelve qué modelo cargar según AI_FORMAT, exportando a OpenVINO/ONNX si
        hace falta (una sola vez; se cachea en disco). Devuelve (ruta, runtime).
        Si el export o la dependencia falla, cae a PyTorch (.pt) — sin romper.
        """
        import os as _os
        fmt = self._format
        pt = self._model_path
        if fmt == "auto":
            # En CPU, OpenVINO suele ser 2-4× más rápido; en GPU, PyTorch/CUDA.
            fmt = "openvino" if self._device == "cpu" else "pytorch"
        if fmt == "pytorch" or not pt.endswith(".pt"):
            return pt, "pytorch"

        base = pt[:-3]  # sin ".pt"
        try:
            from ultralytics import YOLO
            if fmt == "openvino":
                export_dir = f"{base}_openvino_model"
                if not _os.path.isdir(export_dir):
                    logger.info(f"[YOLO] Exportando {pt} → OpenVINO (imgsz={self._imgsz}); puede tardar")
                    YOLO(pt).export(format="openvino", imgsz=self._imgsz, half=False)
                if _os.path.isdir(export_dir):
                    return export_dir, "openvino"
            elif fmt == "onnx":
                onnx_path = f"{base}.onnx"
                if not _os.path.exists(onnx_path):
                    logger.info(f"[YOLO] Exportando {pt} → ONNX (imgsz={self._imgsz}); puede tardar")
                    YOLO(pt).export(format="onnx", imgsz=self._imgsz)
                if _os.path.exists(onnx_path):
                    return onnx_path, "onnx"
        except Exception as e:
            logger.warning(
                f"[YOLO] Export a {fmt} falló ({e}); usando PyTorch (.pt). "
                f"Para acelerar: pip install "
                f"{'openvino' if fmt == 'openvino' else 'onnxruntime'}"
            )
        return pt, "pytorch"

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
        Verifica que las dependencias de IA estén instaladas e importables.

        PROPÓSITO (Pipeline #9, guardia de la ETAPA 4): comprobar torch,
        torchvision y ultralytics ANTES de cargar el modelo. AIService.activate_ai
        la llama UPFRONT para que un entorno incompleto se traduzca en un 503
        HTTP (AI_DEPENDENCIES_MISSING) con instrucciones, en vez de morir dentro
        del hilo worker. Detecta también el caso sutil en que torchvision falta o
        es incompatible: entonces `import ultralytics` lanza AttributeError (no
        ImportError) dentro de utils/checks.py — por eso se atrapa Exception.

        Inputs: ninguno (estática).
        Outputs: tupla (ok: bool, mensaje: str). Si ok=False, el mensaje incluye
            qué falta y el comando pip exacto para arreglarlo.
        Excepciones: ninguna (las captura todas y las convierte en el mensaje).
        Llamado por: AIService.activate_ai (upfront, → 503) y _load_model (antes
            de instanciar YOLO).
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
        """
        Carga perezosa y thread-safe del modelo YOLO (una sola vez).

        Bajo _model_lock: verifica dependencias (→ RuntimeError si faltan),
        fuerza MODO OFFLINE de Ultralytics (sin telemetría ni peticiones a
        internet), limita los hilos de torch (AI_TORCH_THREADS, para no competir
        con FFmpeg), resuelve runtime/ruta vía _resolve_model() y mueve a CUDA si
        procede. Llamado por warmup() y por detect() (primer frame).
        """
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

                # Limitar hilos de torch para que YOLO no acapare la CPU y
                # compita con FFmpeg (si AI_TORCH_THREADS > 0).
                if self._torch_threads > 0:
                    try:
                        import torch
                        torch.set_num_threads(self._torch_threads)
                        logger.info(f"[YOLO] torch.set_num_threads({self._torch_threads})")
                    except Exception:
                        pass

                from ultralytics import YOLO
                # Resolver runtime (OpenVINO/ONNX/PyTorch) y exportar si hace falta.
                model_path, self._runtime = self._resolve_model()
                self._model = YOLO(model_path)

                if self._device == "cuda" and self._runtime == "pytorch":
                    self._model.to("cuda")

                logger.info(
                    f"Modelo YOLO cargado: {model_path} "
                    f"(runtime={self._runtime}, device={self._device})"
                )

            except Exception as e:
                logger.error(f"Error cargando YOLO: {e}")
                raise

    def warmup(self) -> None:
        """
        Precarga el modelo y ejecuta una inferencia dummy (Pipeline #9, ETAPA 4).

        PROPÓSITO: hacer que la PRIMERA detección real sea inmediata. Sin warmup,
        el modelo se cargaba perezosamente en el primer frame con movimiento
        (incluyendo la posible exportación a OpenVINO/ONNX, que tarda segundos)
        → la primera alerta llegaba con mucho retraso.

        Inputs: ninguno (usa self._imgsz para el frame dummy de ceros).
        Outputs: ninguno (efecto: self._model cargado y "caliente").
        Excepciones: ninguna propagada — si falla, lo loguea como warning y el
            modelo se cargará en el primer frame (degradación elegante).
        Llamado por: AIService al activar la IA y main.py en arranque, ambos en
            un HILO aparte (la carga/export no debe bloquear el arranque/HTTP).
        Llama a: _load_model() (si hace falta) y al modelo con un frame dummy.
        Idempotente: si ya está cargado, solo repite la inferencia dummy.
        """
        try:
            if self._model is None:
                self._load_model()
            dummy = np.zeros((self._imgsz, self._imgsz, 3), dtype=np.uint8)
            with self._model_lock:
                self._model(dummy, imgsz=self._imgsz, verbose=False)
            logger.info(
                f"[YOLO] warmup completado — modelo listo "
                f"(runtime={self._runtime}, imgsz={self._imgsz})"
            )
        except Exception as e:
            logger.warning(f"[YOLO] warmup falló (se cargará en el primer frame): {e}")

    def detect(self, frame: np.ndarray, conf: Optional[float] = None) -> List[Detection]:
        """
        Ejecuta YOLOv8 sobre un frame y devuelve las detecciones de interés.

        PROPÓSITO (Pipeline #9, ETAPA 4 — núcleo de la inferencia): inferir y
        convertir las cajas crudas de YOLO al formato estándar Detection,
        FILTRANDO solo CLASSES_OF_INTEREST (personas/vehículos). Carga el modelo
        de forma perezosa si aún no está. Thread-safe (serializa con _model_lock).

        Inputs:
            frame — np.ndarray BGR de OpenCV (el letterbox 640x384 del AIFrameSource).
            conf — umbral de confianza; si None usa self._confidence (AI_CONFIDENCE).
        Outputs: List[Detection] (vacía si no hay objetos de interés, frame
            inválido, o si la inferencia lanzó — el error se loguea, no propaga).
        Excepciones: ninguna propagada por la inferencia (se atrapa y devuelve []);
            _load_model SÍ puede lanzar RuntimeError si faltan dependencias.
        Llamado por: AIScheduler._inference_worker (solo tras pasar el gate de
            movimiento).
        Llama a: _load_model (lazy), el modelo YOLO, CLASSES_OF_INTEREST (filtro).
        Siguiente etapa: AIScheduler._process_detections_with_cooldown (etapa 5).
        """
        if frame is None or frame.size == 0:
            return []
        
        # Cargar modelo si es primera vez
        if self._model is None:
            self._load_model()
        
        # Umbral de confianza: si el llamador no fuerza uno, leer el valor VIVO
        # de settings.AI_CONFIDENCE en cada inferencia. Así, cuando el usuario
        # cambia "Confianza mínima" en Ajustes (PUT /system/config →
        # reload_runtime_config_from_db actualiza settings.AI_CONFIDENCE), el
        # cambio surte efecto de inmediato. Antes se cacheaba self._confidence en
        # el __init__ del singleton y NUNCA se releía → el control no hacía nada.
        if conf is not None:
            confidence = conf
        else:
            try:
                from backend.app.config import settings as _settings
                confidence = float(getattr(_settings, "AI_CONFIDENCE", self._confidence))
            except Exception:
                confidence = self._confidence

        # Normalizar unidades: YOLO espera una fracción 0–1, pero la UI de Ajustes
        # guarda "Confianza mínima" como PORCENTAJE (30–90). Si llega >1 lo tratamos
        # como porcentaje (45 → 0.45); si ya es fracción (0.35) se deja igual. Sin
        # esto, guardar desde la UI dejaría conf=45 y YOLO no detectaría NADA.
        if confidence > 1.0:
            confidence = confidence / 100.0
        # Acotar a un rango sensato por si llega un valor extremo.
        confidence = min(0.95, max(0.05, confidence))

        # Inferencia (thread-safe con lock)
        h, w = frame.shape[:2]
        with self._model_lock:
            try:
                results = self._model(
                    frame, conf=confidence, imgsz=self._imgsz, verbose=False
                )
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
        Dibuja las cajas y etiquetas de las detecciones sobre una COPIA del
        frame (no muta el original). Solo para debug/visualización (no forma
        parte del pipeline de alertas). cv2 se importa de forma perezosa porque
        este helper es opcional. Devuelve el frame anotado.
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
        """
        Devuelve un snapshot del estado del pool para la API/diagnóstico: si el
        modelo está cargado, dispositivo (cpu/cuda), ruta del modelo, runtime
        efectivo (pytorch/onnx/openvino), imgsz, ref_count y las clases
        soportadas. Llamado por AIService/endpoints de estado de IA.
        """
        return {
            "model_loaded": self._model is not None,
            "device": self._device,
            "model_path": self._model_path,
            "runtime": self._runtime,
            "imgsz": self._imgsz,
            "reference_count": self._ref_count,
            "classes_supported": list(self.CLASSES_OF_INTEREST.values())
        }


# Instancia global singleton (para quien quiera importar directamente)
yolo_pool = YLOModelPool()