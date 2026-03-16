"""
Gestión de conexión a base de datos y sesiones SQLAlchemy.
Implementa patrón Singleton para el DatabaseManager.
"""
import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from werkzeug.security import generate_password_hash

from backend.app.config import settings
from .models import Base, User, SystemConfig

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Gestor centralizado de la base de datos.
    Maneja la conexión, creación de tablas y seeding inicial.
    """
    
    def __init__(self) -> None:
        """Inicializa el motor de base de datos y el creador de sesiones."""
        try:
            # Crear engine con configuración específica para SQLite
            self.engine = create_engine(
                settings.get_database_url(),
                connect_args={"check_same_thread": False},  # Necesario para SQLite con threads
                echo=False,
                pool_pre_ping=True
            )
            
            # Crear fábrica de sesiones
            self.SessionLocal = sessionmaker(
                autocommit=False, 
                autoflush=False, 
                bind=self.engine
            )
            
            logger.info("DatabaseManager inicializado correctamente")
            
        except Exception as error:
            logger.error(f"Error al inicializar DatabaseManager: {error}")
            raise RuntimeError(f"No se pudo conectar a la base de datos: {error}") from error
    
    def init_db(self) -> None:
        """
        Inicializa la base de datos creando todas las tablas
        y cargando datos iniciales si es necesario.
        """
        try:
            # Crear todas las tablas definidas en los modelos
            Base.metadata.create_all(bind=self.engine)
            logger.info("Tablas de base de datos creadas/verificadas")
            
            # Cargar datos iniciales
            self._seed_initial_data()
            logger.info("Datos iniciales verificados")
            
        except Exception as error:
            logger.error(f"Error al inicializar base de datos: {error}")
            raise RuntimeError(f"Error en init_db: {error}") from error
    
    def _seed_initial_data(self) -> None:
        """
        Crea datos iniciales si no existen:
        - Usuario admin por defecto
        - Configuraciones del sistema
        """
        try:
            with self.get_session() as session:
                # Crear usuario admin si no existe
                existing_admin = session.query(User).filter_by(username="admin").first()
                if not existing_admin:
                    admin_user = User(
                        username="admin",
                        password_hash=generate_password_hash("admin123"),
                        role="admin",
                        is_active=True
                    )
                    session.add(admin_user)
                    logger.info("Usuario admin creado (username: admin, password: admin123)")
                
                # Configuraciones iniciales de Telegram
                initial_configs = [
                    ("telegram_bot_token", ""),
                    ("telegram_chat_id", ""),
                    ("telegram_enabled", "false"),
                    ("notify_motion", "true"),
                    ("notify_person", "true"),
                    ("notify_vehicle", "true"),
                    ("notify_offline", "true"),
                    ("notify_tampering", "true")
                ]
                
                for key, value in initial_configs:
                    existing = session.query(SystemConfig).filter_by(key=key).first()
                    if not existing:
                        config = SystemConfig(key=key, value=value)
                        session.add(config)
                        logger.debug(f"Configuración inicial creada: {key}")
                
                session.commit()
                
        except Exception as error:
            logger.error(f"Error en seeding inicial: {error}")
            raise RuntimeError(f"Error al crear datos iniciales: {error}") from error
    
    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """
        Context manager que proporciona una sesión de base de datos.
        Maneja automáticamente el cierre y rollback en caso de error.
        
        Yields:
            Session: Sesión de SQLAlchemy activa
            
        Example:
            with db_manager.get_session() as session:
                user = session.query(User).first()
        """
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as error:
            session.rollback()
            logger.error(f"Error en sesión de base de datos: {error}")
            raise
        finally:
            session.close()
    
    def get_engine(self):
        """
        Retorna el engine de SQLAlchemy para operaciones avanzadas.
        
        Returns:
            Engine: Motor de base de datos configurado
        """
        return self.engine


# Instancia global del gestor de base de datos
db_manager = DatabaseManager()
