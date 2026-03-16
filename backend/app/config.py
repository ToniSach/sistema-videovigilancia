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
            # Claves de seguridad (obligatorias)
            self.SECRET_KEY: str = os.getenv("SECRET_KEY", "default-secret-key-change-in-production")
            self.JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "default-jwt-secret-change-immediately")
            
            # Configuración de base de datos
            self.DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/surveillance.db")
            
            # Configuración de almacenamiento
            self.RECORDINGS_PATH: str = os.getenv("RECORDINGS_PATH", "recordings")
            self.MAX_STORAGE_GB: float = float(os.getenv("MAX_STORAGE_GB", "50.0"))
            
            # Configuración de logging
            self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
            
            # Límites del sistema
            self.MAX_CAMERAS: int = int(os.getenv("MAX_CAMERAS", "4"))
            self.AI_CAMERA_ID: str | None = os.getenv("AI_CAMERA_ID") or None
            
            # Configuración de Telegram
            self.TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
            self.TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
            
            # Configuración de JWT
            self.JWT_ACCESS_TOKEN_MINUTES: int = int(os.getenv("JWT_ACCESS_TOKEN_MINUTES", "15"))
            self.JWT_REFRESH_TOKEN_DAYS: int = int(os.getenv("JWT_REFRESH_TOKEN_DAYS", "7"))
            
            # Configuración del servidor
            self.SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
            self.SERVER_PORT: int = int(os.getenv("SERVER_PORT", "5000"))
            
            # Asegurar que los directorios existen
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
        # Directorio de base de datos
        db_dir = os.path.dirname(self.DATABASE_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        
        # Directorio de grabaciones
        os.makedirs(self.RECORDINGS_PATH, exist_ok=True)
    
    def get_database_url(self) -> str:
        """
        Genera la URL de conexión a la base de datos SQLite.
        
        Returns:
            str: URL de conexión SQLAlchemy (sqlite:///path/to/db)
        """
        return f"sqlite:///{self.DATABASE_PATH}"


# Instancia global de configuración
settings = Settings()