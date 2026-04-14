"""
Gestión de conexión PostgreSQL con SQLAlchemy 2.0.
Patrón Singleton para el pool de conexiones.
"""
import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool

from backend.app.config import settings

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Singleton para gestionar conexiones PostgreSQL.
    Configura pooling y provee sesiones con context manager.
    """
    _instance = None
    _engine = None
    _session_factory = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _initialize(self):
        """Inicializa el engine y session factory si no existen."""
        if self._engine is None:
            database_url = settings.get_database_url()
            
            self._engine = create_engine(
                database_url,
                poolclass=QueuePool,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,  # Verifica conexiones antes de usar
                pool_recycle=3600,   # Recicla conexiones cada hora
                echo=False
            )
            
            self._session_factory = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self._engine
            )

    def init_db(self):
        """
        Crea todas las tablas definidas en los modelos si no existen.
        Importación diferida para evitar circular imports.
        """
        try:
            from ..database.models import Base
            engine = self.get_engine()
            Base.metadata.create_all(bind=engine)
            logger.info("Tablas de base de datos creadas/verificadas correctamente")
            return True
        except Exception as e:
            logger.error(f"Error creando tablas: {e}")
            raise

    def get_engine(self):
        """Retorna el engine SQLAlchemy."""
        self._initialize()
        return self._engine

    def get_session_factory(self):
        """Retorna la fábrica de sesiones."""
        self._initialize()
        return self._session_factory

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """
        Context manager para sesiones de base de datos.
        Maneja automáticamente commit/rollback y cierre.
        
        Uso:
            with db_manager.get_session() as session:
                session.query(Model).all()
        """
        self._initialize()
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def health_check(self) -> bool:
        """Verifica que la conexión a PostgreSQL funciona."""
        try:
            with self.get_session() as session:
                session.execute(text("SELECT 1"))
                return True
        except Exception:
            return False

    def dispose(self):
        """Cierra todas las conexiones del pool."""
        if self._engine:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None


# Singleton instance
db_manager = DatabaseManager()