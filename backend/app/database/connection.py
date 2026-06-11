"""
================================================================================
MÓDULO: connection — Gestor de conexión a PostgreSQL (engine + pool de sesiones)
================================================================================

PROPÓSITO
    Proveer el ÚNICO punto de acceso a la base de datos: crea el engine
    SQLAlchemy 2.0 contra PostgreSQL, configura el pool de conexiones
    (QueuePool) y entrega sesiones transaccionales mediante un context manager
    que hace commit/rollback/close automáticamente.

RESPONSABILIDAD PRINCIPAL
    Centralizar la configuración del acceso a datos (URL, tamaño de pool,
    pre-ping, reciclado) y exponer `get_session()` como la forma canónica de
    abrir una transacción. También crea las tablas en el arranque (`init_db`).

DEPENDENCIAS IMPORTANTES
    config.settings ......... `get_database_url()` → postgresql+psycopg2://...
    database.models.Base .... metadata para `create_all` (import diferido en init_db
                              para evitar import circular).
    sqlalchemy (Core/ORM/pool) — engine, sessionmaker, QueuePool.

COMPONENTES RELACIONADOS (quién lo consume)
    main.create_app() ....... llama a `db_manager.init_db()` en el paso 4 del
                              pipeline #1 Inicio del sistema.
    database.repositories.* . TODOS los repos abren `db_manager.get_session()`.
    Servicios y rutas que necesiten BD pasan SIEMPRE por este singleton.

PUNTO DE ENTRADA EN LA ARQUITECTURA
    Capa de infraestructura de persistencia. Es el cuello de botella controlado
    de todo acceso a datos del backend.

PIPELINES
    Participa, como acceso a BD, en TODOS los pipelines que leen/escriben datos
    (#2 Autenticación, #10 Eventos, #11 Grabación, #13 Notificaciones,
    #14 Reproducción...). En el #1 Inicio crea/verifica el esquema.
================================================================================
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
    Gestor singleton del acceso a PostgreSQL.

    ROL
        Posee el engine SQLAlchemy y la fábrica de sesiones, configura el pool
        (QueuePool: pool_size=10, max_overflow=20, pre_ping, recycle=3600s) y
        entrega sesiones transaccionales seguras vía `get_session()`.

    POR QUÉ SINGLETON (__new__)
        El engine encapsula un POOL de conexiones TCP vivas a la BD: debe existir
        UNA sola instancia por proceso para no fragmentar/duplicar conexiones.
        Encaja con la restricción "proceso único" del backend (ver main.py). El
        engine se construye perezosamente la primera vez que se pide (`_initialize`).

    QUIÉN LO INSTANCIA/CONSUME
        Se instancia una vez al final del módulo (`db_manager`). Lo consumen
        `main.create_app()` (init_db) y todos los repositorios.

    DEPENDENCIAS
        config.settings (URL de BD) y database.models.Base (metadata, import
        diferido en init_db).
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
        Crea/verifica todas las tablas del esquema (paso 4 del pipeline #1 Inicio).

        Ejecuta `Base.metadata.create_all` con import DIFERIDO de los modelos
        para evitar el ciclo de imports (models no necesita conocer connection).
        Idempotente: solo crea lo que falte; una BD ya migrada no se altera.

        Inputs:  ninguno (usa el engine del singleton).
        Outputs: True si las tablas quedaron creadas/verificadas.
        Excepciones: re-lanza cualquier error de BD (sin BD no hay sistema → el
            arranque debe abortar; ver main.create_app, que NO lo captura).
        Llamado por: main.create_app() durante el arranque.
        Llama a: get_engine() y Base.metadata.create_all().
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
        Forma CANÓNICA de abrir una transacción contra la BD.

        Patrón "unit of work": entrega una `Session` del pool y, al salir del
        bloque `with`, hace COMMIT si todo fue bien o ROLLBACK si saltó una
        excepción; en cualquier caso CIERRA (devuelve la conexión al pool).

        Inputs:  ninguno.
        Outputs: genera (yield) una `Session` viva dentro del `with`.
        Excepciones: re-lanza la excepción tras hacer rollback (no la silencia).
        Llamado por: todos los repositorios y servicios que tocan la BD.

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