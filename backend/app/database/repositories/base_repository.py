"""
================================================================================
MÓDULO: base_repository — Repositorio genérico (patrón Repository) sobre la BD
================================================================================

PROPÓSITO
    Implementar el patrón Repository de forma GENÉRICA: una clase base que
    encapsula el CRUD estándar (get_by_id, get_all, create, update, delete)
    sobre cualquier modelo SQLAlchemy, ocultando el manejo de sesiones y el
    desacople de objetos de la sesión (`expunge`).

RESPONSABILIDAD PRINCIPAL
    Abstraer el acceso a datos para que los servicios trabajen con objetos del
    dominio sin tocar `Session` directamente, y garantizar que las entidades
    devueltas estén DESVINCULADAS de la sesión (no lazy-loading fuera del `with`).

PATRÓN CLAVE — expunge + sesión por operación
    Cada método abre su propia `db_manager.get_session()`, ejecuta la operación
    y hace `session.expunge(obj)` antes de retornar. Así el objeto sobrevive
    fuera del context manager (la sesión ya se cerró) sin disparar errores de
    "DetachedInstanceError" en accesos posteriores.

DEPENDENCIAS IMPORTANTES
    database.connection.db_manager — abre las sesiones transaccionales.

COMPONENTES RELACIONADOS (quién lo consume)
    Lo extienden los repos concretos: CameraRepository, EventRepository,
    RecordingRepository (y cualquier otro). Estos heredan el CRUD y añaden
    consultas específicas.

PUNTO DE ENTRADA EN LA ARQUITECTURA
    Capa de acceso a datos, intermedia entre servicios (lógica de negocio) y
    `connection`/`models` (persistencia). Participa en todos los pipelines que
    leen/escriben entidades.
================================================================================
"""
import logging
from typing import TypeVar, Generic, Type, List, Optional

from ..connection import db_manager

logger = logging.getLogger(__name__)

T = TypeVar('T')


class BaseRepository(Generic[T]):
    """
    Base genérica de todos los repositorios (patrón Repository).

    ROL
        Provee el CRUD estándar (get_by_id, get_all, create, update, delete) para
        el modelo `T`, abriendo una sesión por operación y desvinculando
        (`expunge`) las entidades devueltas para que sean seguras fuera del `with`.

    QUIÉN LA INSTANCIA/CONSUME
        No se usa directa: la subclasifican los repos concretos (CameraRepository,
        EventRepository, RecordingRepository), cada uno pasando su modelo en
        `super().__init__(Modelo)`. Los servicios consumen esos repos concretos.

    DEPENDENCIAS
        database.connection.db_manager (sesiones).

    Type Parameters:
        T: Tipo del modelo SQLAlchemy gestionado por el repositorio.
    """
    
    def __init__(self, model_class: Type[T]):
        """
        Inicializa el repositorio con la clase de modelo específica.
        
        Args:
            model_class: Clase del modelo SQLAlchemy (ej: Camera, User)
        """
        self.model_class = model_class
        self.logger = logging.getLogger(f"{__name__}.{model_class.__name__}")
    
    def get_by_id(self, id: int) -> Optional[T]:
        """
        Busca una entidad por su ID primario.
        
        Args:
            id: Identificador numérico
            
        Returns:
            Instancia del modelo o None si no existe
        """
        try:
            with db_manager.get_session() as session:
                result = session.get(self.model_class, id)
                if result:
                    session.expunge(result)  # Desvincular de la sesión
                return result
        except Exception as error:
            self.logger.error(f"Error al obtener {self.model_class.__name__} por ID {id}: {error}")
            raise
    
    def get_all(self) -> List[T]:
        """
        Obtiene todas las entidades del tipo especificado.
        
        Returns:
            Lista de instancias del modelo
        """
        try:
            with db_manager.get_session() as session:
                results = session.query(self.model_class).all()
                # Desvincular todos los resultados de la sesión
                for result in results:
                    session.expunge(result)
                return results
        except Exception as error:
            self.logger.error(f"Error al obtener todos los {self.model_class.__name__}: {error}")
            raise
    
    def create(self, obj: T) -> T:
        """
        Crea una nueva entidad en la base de datos.

        Hace `flush()` (no commit) para que la BD asigne el ID antes de salir, y
        `expunge()` para devolver el objeto ya desvinculado. El COMMIT real lo
        realiza el context manager `get_session()` al cerrar sin error.

        Args:
            obj: Instancia del modelo a crear
        Returns:
            Instancia creada con ID asignado
        """
        try:
            with db_manager.get_session() as session:
                session.add(obj)
                session.flush()  # Generar ID sin commit
                session.expunge(obj)  # Desvincular para retornar fuera de contexto
                return obj
        except Exception as error:
            self.logger.error(f"Error al crear {self.model_class.__name__}: {error}")
            raise
    
    def update(self, obj: T) -> T:
        """
        Actualiza una entidad existente.

        Usa `merge()` para RECONECTAR a la sesión un objeto que venía desvinculado
        (típico tras un get previo con expunge); copia su estado a la instancia
        gestionada y la devuelve también desvinculada.

        Args:
            obj: Instancia del modelo con cambios
        Returns:
            Instancia actualizada
        """
        try:
            with db_manager.get_session() as session:
                merged = session.merge(obj)  # Reconectar objeto a sesión
                session.flush()
                session.expunge(merged)
                return merged
        except Exception as error:
            self.logger.error(f"Error al actualizar {self.model_class.__name__}: {error}")
            raise
    
    def delete(self, id: int) -> bool:
        """
        Elimina una entidad por su ID.
        
        Args:
            id: Identificador de la entidad a eliminar
            
        Returns:
            True si se eliminó, False si no existía
        """
        try:
            with db_manager.get_session() as session:
                obj = session.get(self.model_class, id)
                if obj:
                    session.delete(obj)
                    return True
                return False
        except Exception as error:
            self.logger.error(f"Error al eliminar {self.model_class.__name__} con ID {id}: {error}")
            raise
