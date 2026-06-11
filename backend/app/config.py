"""
================================================================================
MÓDULO: config — Configuración global del backend NVR/VMS (Settings singleton)
================================================================================

PROPÓSITO
    Centralizar TODA la configuración del sistema en un único objeto `settings`
    (instancia global de `Settings`). Lee el `.env` de la raíz del repositorio
    una sola vez al importar el módulo y expone cada variable de entorno ya
    parseada/validada como atributo tipado (POSTGRES_*, MAX_CAMERAS, GO2RTC_*,
    FFMPEG_*, AI_*, JWT_*, etc.).

RESPONSABILIDAD PRINCIPAL
    - Cargar `.env` (o caer a `.env-example` si no existe, para que un clon
      recién bajado arranque con defaults razonables).
    - Convertir strings de entorno a tipos correctos (int/float/bool/path) con
      defaults seguros para desarrollo y validación dura de secretos en
      producción (APP_ENV=production exige SECRET_KEY/JWT_SECRET_KEY propios).
    - Construir la URL de conexión a PostgreSQL (`get_database_url()`).
    - Permitir overrides OPERACIONALES persistidos en BD (SystemConfig) que se
      aplican EN CALIENTE sobre el singleton (`reload_runtime_config_from_db()`).
    - Asegurar que existen los directorios de datos (grabaciones, snapshots).

RESPONSABILIDAD (lo que NO hace)
    No abre conexiones ni arranca servicios: es solo estado de configuración.
    Los secretos y la infraestructura (puertos, pools, binarios) viven SOLO en
    .env (12-factor); a la BD solo bajan ajustes operativos tuneables en runtime.

DEPENDENCIAS
    python-dotenv (load_dotenv) ........ carga el .env
    backend.app.runtime ................ bootstrap del modo empaquetado (.exe):
                                         data dir escribible, PATH de binarios,
                                         defaults del PostgreSQL embebido.
    backend.app.database (diferido) .... SystemConfig + db_manager para leer los
                                         overrides operacionales desde la BD.

COMPONENTES RELACIONADOS
    main.create_app() .................. primer consumidor; lee SECRET_KEY,
                                         JWT_*, SERVER_*, GO2RTC_*, etc.
    Prácticamente todos los módulos importan `settings` para leer sus knobs.
    container.DependencyContainer ...... los servicios que construye leen aquí.

PUNTO DE ENTRADA EN LA ARQUITECTURA
    `from backend.app.config import settings`. El singleton se crea al final del
    módulo (`settings = Settings()`), por lo que el .env se lee exactamente una
    vez por proceso (encaja con la restricción de PROCESO ÚNICO del backend).

PIPELINE(S)
    Pipeline #1 (Inicio) — etapa BASE: es lo primero que importa main.py; todo
    el arranque depende de estos valores. También alimenta indirectamente a los
    14 pipelines (cada uno lee sus knobs: RTSP_TRANSPORT→#4, GO2RTC_*→#5/#6,
    AI_*→#9, RECORDING_*→#11, MEDIA_URL_*→#12/#14, TELEGRAM_*→#13...).

GRUPOS DE VARIABLES DE ENTORNO (qué controla cada bloque)
    SEGURIDAD ......... SECRET_KEY, JWT_SECRET_KEY (+APP_ENV/ALLOW_DEFAULT_SECRETS
                        que deciden si se aceptan defaults o se aborta).
    BASE DE DATOS ..... POSTGRES_HOST/PORT/DB/USER/PASSWORD + DB_POOL_* (tamaño
                        del pool SQLAlchemy). Es la BD activa del sistema.
    ALMACENAMIENTO .... RECORDINGS_PATH, MAX_STORAGE_GB, AUTO_START_RECORDING,
                        RECORDING_MAX_FILE_SIZE (split de archivos).
    LÍMITES SISTEMA ... MAX_CAMERAS, MAX_CONCURRENT_FFMPEG, MAX_AI_INFERENCE_QUEUE,
                        AI_CAMERA_ID (selector de la única cámara con IA).
    IA / HARDWARE ..... USE_GPU_AI, AI_BACKEND, AI_CONFIDENCE, AI_MODEL/FORMAT/
                        IMGSZ, AI_TORCH_THREADS, AI_INFERENCE_INTERVAL_*,
                        AI_MOTION_SENSITIVITY, AI_SOURCE_* (fuente dedicada),
                        AI_EVENT_COOLDOWN_SECONDS.
    RTSP .............. RTSP_TRANSPORT (tcp/udp).
    FFMPEG ............ FFMPEG_RESOLUTION_*/FPS (mono) y FFMPEG_DUAL_LENS_*
                        (tamaño RAW antes del split de cámaras dual-lens).
    TELEGRAM .......... TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (bootstrap del bot).
    JWT ............... JWT_ACCESS_TOKEN_MINUTES, JWT_REFRESH_TOKEN_DAYS.
    SERVIDOR .......... SERVER_HOST, SERVER_PORT.
    STREAMING/go2rtc .. GO2RTC_ENABLED, GO2RTC_BINARY, GO2RTC_API_HOST/PORT,
                        GO2RTC_RTSP_PORT, GO2RTC_WEBRTC_PORT, GO2RTC_CONFIG_PATH,
                        GO2RTC_PUBLIC_HOST, GO2RTC_WEBRTC_CANDIDATES,
                        GO2RTC_HWACCEL, WEBRTC_ENABLED, GO2RTC_AS_SOURCE,
                        RECORDING_COPY_MODE, CAMERA_SYNC_TIME_ON_START,
                        LIVE_HIDE_INACTIVE_CAMERAS, STREAM_KEEPALIVE.
    URLS FIRMADAS ..... MEDIA_URL_SECRET, MEDIA_URL_TTL_SECONDS (HMAC para que
                        reproductores nativos accedan a medios sin Authorization).
    TELEMETRÍA ........ TELEMETRY_ENABLED, TELEMETRY_INTERVAL, TELEMETRY_PATH.
================================================================================
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Modo EMPAQUETADO (.exe): prepara directorio de datos escribible, secretos
# autogenerados, PATH con los binarios incluidos y los valores por defecto del
# PostgreSQL embebido ANTES de leer ninguna variable de entorno. En desarrollo
# es un no-op (respeta el .env del repositorio).
from backend.app.runtime import bootstrap as _bootstrap_runtime, app_data_dir, is_frozen
_bootstrap_runtime()

# Cargar variables de entorno: se usa «.env» si existe; si no, se cae a la
# plantilla «.env-example» para que un clon recién bajado arranque igualmente
# (con valores por defecto). En despliegues reales crea tu propio «.env».
# En modo empaquetado el .env (opcional) vive junto a los datos del usuario.
_root = Path(__file__).parent.parent.parent
env_path = (app_data_dir() / '.env') if is_frozen() else (_root / '.env')
if not env_path.exists():
    example_path = _root / '.env-example'
    if example_path.exists():
        env_path = example_path
load_dotenv(dotenv_path=env_path, verbose=True)

logger = logging.getLogger(__name__)


class Settings:
    """
    Estado de configuración global del sistema (un objeto, todas las knobs).

    ROL / RESPONSABILIDAD
        Leer las variables de entorno (ya cargadas del .env por load_dotenv al
        importar el módulo), parsearlas a su tipo y exponerlas como atributos.
        Validar secretos en producción y crear los directorios de datos.

    QUIÉN LA INSTANCIA / CONSUME
        Se instancia UNA vez al final del módulo (`settings = Settings()`).
        No usa el patrón __new__, pero es de facto un singleton de proceso: todo
        el código importa la MISMA instancia `settings`. No instanciar otra
        copia (volvería a leer el entorno y perdería los overrides de BD).

    DEPENDENCIAS
        os.environ (vía os.getenv) y, de forma diferida, la BD (SystemConfig)
        para los overrides operacionales.

    PIPELINE
        #1 (Inicio), etapa BASE. Ver docstring del módulo para el detalle de
        qué grupo de variables alimenta a cada uno de los 14 pipelines.
    """

    def __init__(self) -> None:
        """
        Construye el singleton leyendo y validando TODAS las variables de
        entorno (etapa BASE del pipeline #1).

        Inputs: ninguno explícito; lee de os.environ (poblado por load_dotenv).
        Outputs: self con todos los atributos de configuración ya tipados; crea
            los directorios de datos vía _ensure_directories().
        Excepciones:
            RuntimeError — en producción (APP_ENV=production) si SECRET_KEY o
                JWT_SECRET_KEY siguen en su valor por defecto (sin
                ALLOW_DEFAULT_SECRETS=true).
            ValueError — si una variable numérica del .env no parsea (int/float).
        Llamado por: la línea final `settings = Settings()` (una vez por proceso).
        """
        try:
            # ==============================
            # SEGURIDAD
            # ==============================
            # En dev se aceptan defaults para no romper el primer arranque;
            # en producción exigimos secrets explícitos. El switch lo controla
            # APP_ENV=production. ALLOW_DEFAULT_SECRETS=true es un override
            # explícito para desarrolladores que entienden el riesgo.
            _default_secret = "default-secret-key-change-in-production"
            _default_jwt = "default-jwt-secret-change-immediately"
            self.SECRET_KEY: str = os.getenv("SECRET_KEY", _default_secret)
            self.JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", _default_jwt)

            _is_prod = os.getenv("APP_ENV", "development").lower() == "production"
            _allow_default = os.getenv("ALLOW_DEFAULT_SECRETS", "").lower() == "true"
            if _is_prod and not _allow_default:
                if self.SECRET_KEY == _default_secret:
                    raise RuntimeError(
                        "SECRET_KEY no configurada en producción. "
                        "Define SECRET_KEY en .env con un valor único (>=32 chars). "
                        "Para forzar default en dev: ALLOW_DEFAULT_SECRETS=true"
                    )
                if self.JWT_SECRET_KEY == _default_jwt:
                    raise RuntimeError(
                        "JWT_SECRET_KEY no configurada en producción. "
                        "Define JWT_SECRET_KEY en .env con un valor único (>=32 chars). "
                        "Para forzar default en dev: ALLOW_DEFAULT_SECRETS=true"
                    )
            elif self.SECRET_KEY == _default_secret or self.JWT_SECRET_KEY == _default_jwt:
                # Warning visible en dev para que no se olvide al desplegar
                logger.warning(
                    "[SEGURIDAD] Usando secrets por defecto. Genera valores "
                    "únicos antes de producción: "
                    "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
                )
            
            # ==============================
            # BASE DE DATOS (PostgreSQL)
            # ==============================
            self.POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
            self.POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
            self.POSTGRES_DB: str = os.getenv("POSTGRES_DB", "nvr_db")
            self.POSTGRES_USER: str = os.getenv("POSTGRES_USER", "nvr_user")
            self.POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "nvr_pass")
            
            # Pooling
            self.DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "20"))
            self.DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "30"))
            self.DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))
            
            # ==============================
            # ALMACENAMIENTO
            # ==============================
            self.RECORDINGS_PATH: str = os.getenv("RECORDINGS_PATH", "recordings")
            # Default 1GB para evitar llenar el disco en desarrollo/demo.
            # En producción típica: 50-500GB según el cliente.
            self.MAX_STORAGE_GB: float = float(os.getenv("MAX_STORAGE_GB", "1.0"))

            # Auto-iniciar grabación continua en cuanto arranca cada cámara.
            # Estándar en NVRs comerciales (Hikvision, Dahua, etc.).
            # Ponlo en "false" si prefieres iniciar la grabación manualmente.
            self.AUTO_START_RECORDING: bool = (
                os.getenv("AUTO_START_RECORDING", "true").lower() == "true"
            )
            
            # NUEVO: límite de tamaño de archivos de grabación (split automático)
            self.RECORDING_MAX_FILE_SIZE: int = int(
                os.getenv("RECORDING_MAX_FILE_SIZE", str(2 * 1024**3))  # 2GB
            )
            
            # ==============================
            # LOGGING
            # ==============================
            self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
            
            # ==============================
            # LÍMITES DEL SISTEMA
            # ==============================
            self.MAX_CAMERAS: int = int(os.getenv("MAX_CAMERAS", "4"))
            self.AI_CAMERA_ID: str | None = os.getenv("AI_CAMERA_ID") or None
            
            # NUEVOS LÍMITES CRÍTICOS (NVR)
            self.MAX_CONCURRENT_FFMPEG: int = int(os.getenv("MAX_CONCURRENT_FFMPEG", "4"))
            self.MAX_AI_INFERENCE_QUEUE: int = int(os.getenv("MAX_AI_INFERENCE_QUEUE", "10"))
            # NOTA: el streaming en vivo ahora lo sirve go2rtc (WebRTC/RTSP/HLS)
            # directamente al cliente; MJPEG fue eliminado por completo, por eso
            # ya no hay flags MJPEG aquí.

            # ==============================
            # CONFIGURACIÓN AI/HARDWARE
            # ==============================
            # use_gpu_ai: "auto", "true", "false"
            self.USE_GPU_AI: str = os.getenv("USE_GPU_AI", "auto")
            # ai_backend: "auto", "cuda", "cpu"
            self.AI_BACKEND: str = os.getenv("AI_BACKEND", "auto")

            # Umbral de confianza YOLO (0.0-1.0). Default 0.35 — bajo a propósito
            # porque con dual-lens los frames son 960x540, las personas a
            # distancia salen pequeñas y 0.45 era demasiado restrictivo.
            # Sube a 0.50+ si tienes muchos falsos positivos.
            self.AI_CONFIDENCE: float = float(os.getenv("AI_CONFIDENCE", "0.35"))

            # ── Modelo y runtime de IA (optimización de CPU) ──
            # AI_MODEL: archivo del modelo (yolov8n.pt por defecto; prueba
            #   yolo11n.pt para mejor precisión a igual coste).
            # AI_FORMAT: runtime de inferencia:
            #   "auto"     → OpenVINO en CPU (2-4× más rápido), PyTorch en GPU.
            #   "openvino" → fuerza OpenVINO (requiere: pip install openvino).
            #   "onnx"     → ONNX Runtime (requiere: pip install onnxruntime).
            #   "pytorch"  → comportamiento clásico (sin export).
            #   Si el export/instalación falla, cae a PyTorch automáticamente.
            # AI_IMGSZ: resolución de inferencia. 640 = precisión actual;
            #   320 = ~4× más rápido pero detecta peor objetos pequeños/lejanos.
            # AI_TORCH_THREADS: hilos de torch (0 = no tocar). Útil para que
            #   YOLO no compita con FFmpeg por los núcleos.
            self.AI_MODEL: str = os.getenv("AI_MODEL", "yolov8n.pt")
            self.AI_FORMAT: str = os.getenv("AI_FORMAT", "auto").lower()
            self.AI_IMGSZ: int = int(os.getenv("AI_IMGSZ", "640"))
            self.AI_TORCH_THREADS: int = int(os.getenv("AI_TORCH_THREADS", "0"))

            # Intervalo de inferencia: 1 cada N frames (a 15fps).
            # low_cpu=5 → 3 inferencias/s; suficiente para detectar a una
            # persona caminando (cruzar el FOV típico tarda 2-4s).
            # high_quality=3 → 5/s para escenas con movimiento rápido.
            # Bajar a 3 fps libera CPU y reduce la contención de hilos en
            # GlobalExecutor (grabación + IA comparten pool).
            self.AI_INFERENCE_INTERVAL_LOW: int = int(
                os.getenv("AI_INFERENCE_INTERVAL_LOW", "5")
            )
            self.AI_INFERENCE_INTERVAL_HIGH: int = int(
                os.getenv("AI_INFERENCE_INTERVAL_HIGH", "3")
            )

            # Sensibilidad motion detector (0.0-1.0). Default 0.015 = 1.5%
            # de pixeles cambiados → más sensible que el 0.02 anterior.
            self.AI_MOTION_SENSITIVITY: float = float(
                os.getenv("AI_MOTION_SENSITIVITY", "0.015")
            )

            # Fuente DEDICADA de la IA (AIFrameSource): un ffmpeg ligero que lee
            # el substream BAJO del lente desde go2rtc (cam_X[_lY]_low) y entrega
            # frames pequeños a la IA en un slot-1, SIN el pipeline pesado
            # (FFmpegWorker/CircularFrameBuffer/FrameDistributor/InferenceQueue/
            # DualLensSplitter). Decode mínimo → "la IA no hace trabajo de más".
            self.AI_SOURCE_WIDTH: int = int(os.getenv("AI_SOURCE_WIDTH", "640"))
            self.AI_SOURCE_HEIGHT: int = int(os.getenv("AI_SOURCE_HEIGHT", "384"))
            self.AI_SOURCE_FPS: int = int(os.getenv("AI_SOURCE_FPS", "6"))
            # Calidad del substream de go2rtc que consume la IA: "low" (360p,
            # recomendado, mínimo coste), "medium" (480p) o "high" (nativo).
            self.AI_SOURCE_QUALITY: str = os.getenv("AI_SOURCE_QUALITY", "low").strip()

            # Telemetría integrada: muestrea todos los módulos en uso y escribe a
            # disco fila-a-fila (sobrevive crash/Ctrl-C). Default ON.
            self.TELEMETRY_ENABLED: bool = (
                os.getenv("TELEMETRY_ENABLED", "true").lower() == "true"
            )
            self.TELEMETRY_INTERVAL: float = float(os.getenv("TELEMETRY_INTERVAL", "2.0"))
            # Carpeta de salida. Vacío = <repo>/telemetry. (Evita OneDrive si puedes.)
            self.TELEMETRY_PATH: str = os.getenv("TELEMETRY_PATH", "").strip()

            # Streams de go2rtc a mantener CALIENTES (consumidor -c copy ligero)
            # para que cambiar a ellos sea instantáneo (sin arranque en frío).
            # Coma-separado, ej: "cam_9_l1_medium,cam_9_l2_medium,cam_10_medium".
            # OJO: cada uno mantiene su transcoder QSV corriendo → NO los pongas
            # todos (satura la iGPU); solo los que de verdad usas. Vacío = off.
            self.STREAM_KEEPALIVE: str = os.getenv("STREAM_KEEPALIVE", "").strip()

            # Cooldown entre alertas de la misma clase (segundos).
            # 30s es el default razonable para producción: una persona que
            # cruza el FOV genera 1 alerta, no decenas. Con cooldown=0
            # (modo test) el flood de eventos satura GlobalExecutor con
            # tareas de cv2.imwrite + HTTP a Telegram (~2s c/u) + splice
            # ffmpeg, retrasando la grabación y la IA.
            self.AI_EVENT_COOLDOWN_SECONDS: int = int(
                os.getenv("AI_EVENT_COOLDOWN_SECONDS", "30")
            )
            
            # ==============================
            # RTSP TRANSPORT
            # ==============================
            # tcp  → fiable contra packet loss, MÁS LATENCIA (~200-500ms más)
            # udp  → menor latencia (-100 a -300ms), pero glitches visibles
            #         si la red WiFi es inestable. Recomendado solo en LAN
            #         cableada o WiFi 5GHz sin saturación.
            # Cambio el default: tcp (seguro). Para probar UDP poner en .env:
            #   RTSP_TRANSPORT=udp
            self.RTSP_TRANSPORT: str = os.getenv("RTSP_TRANSPORT", "tcp").lower()
            if self.RTSP_TRANSPORT not in ("tcp", "udp"):
                logger.warning(
                    f"RTSP_TRANSPORT inválido '{self.RTSP_TRANSPORT}', usando 'tcp'"
                )
                self.RTSP_TRANSPORT = "tcp"

            # ==============================
            # FFMPEG OPTIMIZACIÓN (720p15 por defecto)
            # ==============================
            self.FFMPEG_RESOLUTION_WIDTH: int = int(os.getenv("FFMPEG_RESOLUTION_WIDTH", "1280"))
            self.FFMPEG_RESOLUTION_HEIGHT: int = int(os.getenv("FFMPEG_RESOLUTION_HEIGHT", "720"))
            self.FFMPEG_FPS: int = int(os.getenv("FFMPEG_FPS", "15"))

            # DUAL LENS: resolución del stream completo ANTES del split.
            # Por defecto bajamos a 960x1080 (per lens 960x540) para reducir
            # la carga del pipeline (memcpy en Python). Cada 5.5MB por frame se
            # convierten en ~1.5MB → 4x menos work.
            # Para mayor calidad (con CPU disponible) puedes subirlo en .env:
            #   FFMPEG_DUAL_LENS_WIDTH=1280
            #   FFMPEG_DUAL_LENS_HEIGHT=1440
            self.FFMPEG_DUAL_LENS_WIDTH: int = int(os.getenv("FFMPEG_DUAL_LENS_WIDTH", "960"))
            self.FFMPEG_DUAL_LENS_HEIGHT: int = int(os.getenv("FFMPEG_DUAL_LENS_HEIGHT", "1080"))
            
            # ==============================
            # TELEGRAM
            # ==============================
            self.TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
            self.TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
            
            # ==============================
            # JWT
            # ==============================
            self.JWT_ACCESS_TOKEN_MINUTES: int = int(os.getenv("JWT_ACCESS_TOKEN_MINUTES", "15"))
            self.JWT_REFRESH_TOKEN_DAYS: int = int(os.getenv("JWT_REFRESH_TOKEN_DAYS", "7"))
            
            # ==============================
            # SERVIDOR
            # ==============================
            self.SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
            self.SERVER_PORT: int = int(os.getenv("SERVER_PORT", "5000"))

            # ==============================
            # STREAMING (go2rtc / WebRTC)
            # ==============================
            # go2rtc es la ÚNICA capa de directo (MJPEG fue eliminado): un
            # proceso sidecar que habla RTSP con las cámaras UNA sola vez y
            # reexpone WebRTC/RTSP/HLS sin transcode. Viene ACTIVADO por
            # defecto; ponlo en false solo si no quieres directo en absoluto.
            self.GO2RTC_ENABLED: bool = (
                os.getenv("GO2RTC_ENABLED", "true").lower() == "true"
            )
            # Binario: ruta absoluta o nombre en PATH. Se valida con shutil.which.
            self.GO2RTC_BINARY: str = os.getenv("GO2RTC_BINARY", "go2rtc")
            # API de go2rtc. Sirve el dashboard Y los flujos HLS/MSE/WebRTC.
            # Por defecto 0.0.0.0 para que la app MÓVIL pueda consumir HLS
            # (http://<lan-ip>:1984/api/stream.m3u8?src=cam_X) desde la LAN —
            # ExoPlayer-RTSP es poco fiable, HLS es el transporte robusto. Es un
            # appliance de LAN; si se quiere restringir, fijar 127.0.0.1.
            self.GO2RTC_API_HOST: str = os.getenv("GO2RTC_API_HOST", "0.0.0.0")
            self.GO2RTC_API_PORT: int = int(os.getenv("GO2RTC_API_PORT", "1984"))
            # Puerto RTSP de restream (lo consumen desktop, móvil, grabación, IA).
            self.GO2RTC_RTSP_PORT: int = int(os.getenv("GO2RTC_RTSP_PORT", "8554"))
            # Puerto WebRTC (ICE). Debe ser alcanzable desde los clientes en LAN.
            self.GO2RTC_WEBRTC_PORT: int = int(os.getenv("GO2RTC_WEBRTC_PORT", "8555"))
            # Ruta donde Go2RtcManager escribe el go2rtc.yaml generado desde la BD.
            self.GO2RTC_CONFIG_PATH: str = os.getenv(
                "GO2RTC_CONFIG_PATH",
                os.path.join(self.RECORDINGS_PATH, "..", "go2rtc.generated.yaml"),
            )
            # Host/IP del servidor anunciado a los clientes para el restream y los
            # candidatos ICE de WebRTC. Si está vacío, Go2RtcManager intenta
            # autodetectar la IP LAN. Útil fijarlo en despliegues con varias NICs.
            self.GO2RTC_PUBLIC_HOST: str = os.getenv("GO2RTC_PUBLIC_HOST", "")
            # Candidatos ICE extra (coma-separados), ej. "stun:stun.l.google.com:19302"
            # o una IP pública/TURN para acceso remoto. Vacío = solo LAN (host).
            self.GO2RTC_WEBRTC_CANDIDATES: str = os.getenv("GO2RTC_WEBRTC_CANDIDATES", "")

            # Aceleración por HARDWARE del transcode dual-lens en go2rtc.
            # "" = software (libx264, ~1.5 cores). "qsv" = Intel QuickSync (iGPU),
            # "cuda" = NVIDIA, "dxva2"/"vaapi" según plataforma. Hace el HEVC→H264
            # + crop por GPU (~0.1-0.3 core). En este equipo se verificó que
            # h264_qsv funciona (la iGPU Intel); nvenc requería driver 570+.
            # Si el directo dual-lens se rompe tras activarlo, vuelve a "".
            self.GO2RTC_HWACCEL: str = os.getenv("GO2RTC_HWACCEL", "qsv").strip()

            # WebRTC signaling (WHEP) en el backend. Requiere GO2RTC_ENABLED.
            self.WEBRTC_ENABLED: bool = (
                os.getenv("WEBRTC_ENABLED", "false").lower() == "true"
            )

            # Cuando True, el FFmpegWorker (decodifica para IA/grabación) lee
            # del RESTREAM de go2rtc en vez de la cámara directa. CRÍTICO para
            # cámaras que solo aceptan 1 conexión RTSP (XiongMai, muchos
            # chinos baratos): así go2rtc es el ÚNICO que habla con la cámara
            # y todos los demás (worker, grabación) consumen go2rtc sin
            # contención. Requiere GO2RTC_ENABLED y que go2rtc YA esté
            # sirviendo el stream. Default false: actívalo SOLO cuando hayas
            # confirmado que go2rtc puede leer la cámara (revisa go2rtc.log).
            self.GO2RTC_AS_SOURCE: bool = (
                os.getenv("GO2RTC_AS_SOURCE", "false").lower() == "true"
            )

            # Grabación continua en modo "-c copy" (remux del H.264 de la cámara,
            # CPU ≈ 0) en vez de re-encodear con libx264. Requiere una fuente RTSP
            # estable (idealmente el restream de go2rtc). Default false para no
            # alterar la grabación actual hasta validar.
            self.RECORDING_COPY_MODE: bool = (
                os.getenv("RECORDING_COPY_MODE", "false").lower() == "true"
            )

            # Sincronizar la hora de la cámara con la del servidor (ONVIF
            # SetSystemDateAndTime) al arrancarla. Corrige cámaras con la fecha
            # desfasada (XiongMai, etc.) → OSD y marcas de evento correctas.
            self.CAMERA_SYNC_TIME_ON_START: bool = (
                os.getenv("CAMERA_SYNC_TIME_ON_START", "true").lower() == "true"
            )

            # Las cámaras inactivas (status != active / sin worker) NO aparecen en
            # el "en vivo". Activado por defecto: comportamiento esperado de un VMS.
            self.LIVE_HIDE_INACTIVE_CAMERAS: bool = (
                os.getenv("LIVE_HIDE_INACTIVE_CAMERAS", "true").lower() == "true"
            )

            # ==============================
            # URLS FIRMADAS DE MEDIOS (seguridad)
            # ==============================
            # Los reproductores nativos (ExoPlayer/AVPlayer/VLC) NO envían el
            # header Authorization al pedir segmentos/MP4. Para no exponer medios
            # sin auth, se firman URLs con HMAC y caducidad corta. El secreto cae
            # por defecto al JWT_SECRET_KEY si no se define uno dedicado.
            self.MEDIA_URL_SECRET: str = (
                os.getenv("MEDIA_URL_SECRET") or self.JWT_SECRET_KEY
            )
            # Caducidad por defecto de un token de medios (segundos).
            self.MEDIA_URL_TTL_SECONDS: int = int(
                os.getenv("MEDIA_URL_TTL_SECONDS", "300")
            )

            # ==============================
            # SETUP
            # ==============================
            self._ensure_directories()
            
            logger.info("Configuración cargada exitosamente")
            
        except ValueError as e:
            logger.error(f"Error al parsear variable de entorno: {e}")
            raise ValueError(f"Error en configuración: {e}") from e
        except Exception as e:
            logger.error(f"Error inesperado al cargar configuración: {e}")
            raise
    
    def _ensure_directories(self) -> None:
        """
        Crea el árbol de directorios de datos (grabaciones + snapshots) si no
        existe. Idempotente (exist_ok=True). Llamado por __init__ al final de la
        carga. Sin esto, el primer write de grabación/snapshot fallaría.
        """
        # Directorio de grabaciones
        os.makedirs(self.RECORDINGS_PATH, exist_ok=True)
        
        # Directorio de snapshots
        snapshots_dir = os.path.join(self.RECORDINGS_PATH, "snapshots")
        os.makedirs(snapshots_dir, exist_ok=True)
    
    def get_database_url(self) -> str:
        """
        Genera la URL de conexión SQLAlchemy a PostgreSQL a partir de los
        atributos POSTGRES_*.

        Outputs:
            str — "postgresql+psycopg2://user:pass@host:port/db".
        Llamado por:
            DatabaseManager (database/connection.py) al crear el engine/pool.
            Es la ÚNICA fuente de la URL de BD (SQLite es legado/no usado).
        """
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # Claves OPERACIONALES que pueden vivir en la BD (SystemConfig) y aplicarse
    # EN CALIENTE sobre el singleton. clave_BD → (atributo_settings, tipo).
    #
    # CRITERIO (arquitectura): aquí solo van ajustes OPERATIVOS y tuneables por el
    # usuario en runtime. NUNCA secretos (SECRET_KEY, JWT_*, MEDIA_URL_SECRET,
    # TELEGRAM_BOT_TOKEN) ni infraestructura/arranque (POSTGRES_*, DB_POOL_*,
    # puertos, GO2RTC_BINARY/HWACCEL, RTSP_TRANSPORT, USE_GPU_AI/AI_BACKEND/
    # AI_FORMAT): esos diferencian el despliegue/máquina y deben vivir SOLO en
    # .env (principio 12-factor + no exponer secretos en la BD).
    _DB_OVERRIDE_KEYS = {
        "recordings_path": ("RECORDINGS_PATH", "path"),
        "max_storage_gb": ("MAX_STORAGE_GB", "float"),
        "ai_confidence": ("AI_CONFIDENCE", "float"),
        "ai_event_cooldown_seconds": ("AI_EVENT_COOLDOWN_SECONDS", "int"),
        "telemetry_enabled": ("TELEMETRY_ENABLED", "bool"),
        "telemetry_interval": ("TELEMETRY_INTERVAL", "float"),
    }

    def reload_runtime_config_from_db(self) -> None:
        """
        Relee los overrides OPERACIONALES desde SystemConfig y los aplica EN
        CALIENTE sobre el singleton. El valor de .env actúa como DEFAULT; si hay
        un valor en la BD, gana (persistente entre reinicios y editable desde el
        cliente sin reiniciar). Solo afecta a las claves de `_DB_OVERRIDE_KEYS`
        (ajustes operativos); secretos e infraestructura NO se tocan.

        Se llama: (1) al arrancar (tras init_db) y (2) tras guardar config desde
        el cliente. Import diferido para evitar dependencia circular.
        """
        try:
            from backend.app.database.connection import db_manager
            from backend.app.database.models import SystemConfig
            keys = list(self._DB_OVERRIDE_KEYS.keys())
            with db_manager.get_session() as session:
                rows = {
                    c.key: c.value
                    for c in session.query(SystemConfig).filter(
                        SystemConfig.key.in_(keys)
                    ).all()
                }
        except Exception:
            # En el primer arranque la tabla puede no existir aún; es benigno.
            return

        import os as _os
        for key, (attr, kind) in self._DB_OVERRIDE_KEYS.items():
            raw = rows.get(key)
            if raw is None or str(raw).strip() == "":
                continue
            raw = str(raw).strip()
            try:
                if kind == "path":
                    _os.makedirs(raw, exist_ok=True)
                    setattr(self, attr, raw)
                elif kind == "float":
                    setattr(self, attr, float(raw))
                elif kind == "int":
                    setattr(self, attr, int(float(raw)))
                elif kind == "bool":
                    setattr(self, attr, str(raw).lower() in ("1", "true", "yes", "on"))
            except (TypeError, ValueError, OSError):
                continue

    def reload_storage_from_db(self) -> None:
        """Compat: el nombre antiguo sigue funcionando. Ahora recarga TODO el
        conjunto operacional (incluye almacenamiento)."""
        self.reload_runtime_config_from_db()


# Instancia global de configuración
settings = Settings()