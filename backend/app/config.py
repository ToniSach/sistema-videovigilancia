"""
Módulo de configuración global del sistema.
Lee variables de entorno desde archivo .env
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
env_path = Path(__file__).parent.parent.parent / '.env'
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
            self.SECRET_KEY: str = os.getenv("SECRET_KEY", "default-secret-key-change-in-production")
            self.JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "default-jwt-secret-change-immediately")
            
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
            self.MAX_STORAGE_GB: float = float(os.getenv("MAX_STORAGE_GB", "50.0"))
            
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
            self.MAX_MJPEG_CLIENTS_PER_CAMERA: int = int(os.getenv("MAX_MJPEG_CLIENTS_PER_CAMERA", "5"))
            
            # ==============================
            # CONFIGURACIÓN AI/HARDWARE
            # ==============================
            # use_gpu_ai: "auto", "true", "false"
            self.USE_GPU_AI: str = os.getenv("USE_GPU_AI", "auto")
            # ai_backend: "auto", "cuda", "cpu"
            self.AI_BACKEND: str = os.getenv("AI_BACKEND", "auto")
            
            # ==============================
            # FFMPEG OPTIMIZACIÓN (720p15 por defecto)
            # ==============================
            self.FFMPEG_RESOLUTION_WIDTH: int = int(os.getenv("FFMPEG_RESOLUTION_WIDTH", "1280"))
            self.FFMPEG_RESOLUTION_HEIGHT: int = int(os.getenv("FFMPEG_RESOLUTION_HEIGHT", "720"))
            self.FFMPEG_FPS: int = int(os.getenv("FFMPEG_FPS", "15"))
            
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


# Instancia global de configuración
settings = Settings()