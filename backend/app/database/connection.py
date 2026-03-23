"""
Gestión de conexión a base de datos y sesiones SQLAlchemy.
Implementa patrón Singleton para el DatabaseManager.
"""
import logging
import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, inspect
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
            # Crear engine
            self.engine = create_engine(
                settings.get_database_url(),
                connect_args={"check_same_thread": False},
                echo=False,
                pool_pre_ping=True
            )

            # 🔍 DEBUG: ruta real de la DB
            print("🧠 DB URL REAL:", settings.get_database_url())

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

        # ✅ PRIMERO: verificar migraciones (ANTES de usar la DB)
        if os.getenv('FLASK_ENV') == 'development':
            self.check_migrations()

        # ✅ DESPUÉS: crear tablas
        self.init_db()

    # =========================================================
    # MIGRACIONES (DEV)
    # =========================================================
    def check_migrations(self) -> None:
        """
        Verifica discrepancias entre modelos y BD.
        Si falta una columna crítica (como owner_id), reconstruye la BD.
        SOLO para desarrollo.
        """
        try:
            inspector = inspect(self.engine)

            # Si no existe la tabla, no hay nada que migrar
            if "cameras" not in inspector.get_table_names():
                return

            columns = [col["name"] for col in inspector.get_columns("cameras")]

            # Verificar columna nueva
            if "owner_id" not in columns:
                logger.warning("⚠ Falta columna owner_id. Recreando base de datos...")

                Base.metadata.drop_all(bind=self.engine)
                Base.metadata.create_all(bind=self.engine)
                self._seed_initial_data()

                logger.info("✅ Base de datos recreada correctamente con owner_id")

        except Exception as e:
            logger.error(f"Error en check_migrations: {e}")
            raise

    # =========================================================
    # INICIALIZACIÓN
    # =========================================================
    def init_db(self) -> None:
        """
        Inicializa la base de datos creando todas las tablas
        y cargando datos iniciales si es necesario.
        """
        try:
            Base.metadata.create_all(bind=self.engine)
            logger.info("Tablas de base de datos creadas/verificadas")

            self._seed_initial_data()
            logger.info("Datos iniciales verificados")

        except Exception as error:
            logger.error(f"Error al inicializar base de datos: {error}")
            raise RuntimeError(f"Error en init_db: {error}") from error

    # =========================================================
    # SEEDING
    # =========================================================
    def _seed_initial_data(self) -> None:
        """
        Crea datos iniciales si no existen:
        - Usuario admin
        - Configuración básica
        """
        try:
            with self.get_session() as session:

                # Admin por defecto
                existing_admin = session.query(User).filter_by(username="admin").first()
                if not existing_admin:
                    admin_user = User(
                        username="admin",
                        password_hash=generate_password_hash("admin123"),
                        role="admin",
                        is_active=True
                    )
                    session.add(admin_user)
                    logger.info("Usuario admin creado (admin/admin123)")

                # Configuración inicial
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

                session.commit()

        except Exception as error:
            logger.error(f"Error en seeding inicial: {error}")
            raise RuntimeError(f"Error al crear datos iniciales: {error}") from error

    # =========================================================
    # SESIONES
    # =========================================================
    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """
        Context manager para sesiones seguras.
        """
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as error:
            session.rollback()
            logger.error(f"Error en sesión de BD: {error}")
            raise
        finally:
            try:
                session.close()
            except Exception as e:
                logger.error(f"Error cerrando sesión: {e}")
                import gc
                gc.collect()

    # =========================================================
    # UTILIDADES
    # =========================================================
    def get_engine(self):
        return self.engine


# =========================================================
# INSTANCIA GLOBAL
# =========================================================
db_manager = DatabaseManager()