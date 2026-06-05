"""
Módulo de configuración global del sistema.
Lee variables de entorno desde archivo .env
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Cargar variables de entorno: se usa «.env» si existe; si no, se cae a la
# plantilla «.env-example» para que un clon recién bajado arranque igualmente
# (con valores por defecto). En despliegues reales crea tu propio «.env».
_root = Path(__file__).parent.parent.parent
env_path = _root / '.env'
if not env_path.exists():
    example_path = _root / '.env-example'
    if example_path.exists():
        env_path = example_path
load_dotenv(dotenv_path=env_path, verbose=True)

logger = logging.getLogger(__name__)


class Settings:
    """
    Clase de configuración que centraliza todas las variables de entorno.
    Proporciona valores por defecto seguros para desarrollo.
    """
    
    def __init__(self) -> None:
        """Inicializa la configuración desde variables de entorno."""
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
        """Crea los directorios necesarios si no existen."""
        # Directorio de grabaciones
        os.makedirs(self.RECORDINGS_PATH, exist_ok=True)
        
        # Directorio de snapshots
        snapshots_dir = os.path.join(self.RECORDINGS_PATH, "snapshots")
        os.makedirs(snapshots_dir, exist_ok=True)
    
    def get_database_url(self) -> str:
        """
        Genera la URL de conexión a la base de datos PostgreSQL.
        
        Returns:
            str: URL de conexión SQLAlchemy (postgresql+psycopg2://...)
        """
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    def reload_storage_from_db(self) -> None:
        """
        Relee los overrides de almacenamiento (ruta de grabaciones y cuota) desde
        SystemConfig y los aplica EN CALIENTE sobre el singleton.

        Se llama: (1) al arrancar (tras init_db) para que un valor guardado
        antes persista entre reinicios, y (2) tras guardar la config desde la
        app de escritorio para que aplique sin reiniciar. Import diferido para
        evitar dependencia circular en el import-time de config.
        """
        try:
            import os as _os
            from backend.app.database.connection import db_manager
            from backend.app.database.models import SystemConfig
            with db_manager.get_session() as session:
                rows = {
                    c.key: c.value
                    for c in session.query(SystemConfig).filter(
                        SystemConfig.key.in_(["recordings_path", "max_storage_gb"])
                    ).all()
                }
            path = (rows.get("recordings_path") or "").strip()
            if path:
                try:
                    _os.makedirs(path, exist_ok=True)
                    self.RECORDINGS_PATH = path
                except OSError:
                    pass
            gb = rows.get("max_storage_gb")
            if gb:
                try:
                    self.MAX_STORAGE_GB = float(gb)
                except (TypeError, ValueError):
                    pass
        except Exception:
            # En el primer arranque la tabla puede no existir aún; es benigno.
            pass


# Instancia global de configuración
settings = Settings()