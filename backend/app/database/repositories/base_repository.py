"""
Repositorio base genérico que implementa operaciones CRUD básicas.
Utiliza tipos genéricos para type safety.
"""
import logging
from typing import TypeVar, Generic, Type, List, Optional

from ..connection import db_manager

logger = logging.getLogger(__name__)

T = TypeVar('T')


class BaseRepository(Generic[T]):
    """
    Clase base abstracta para todos los repositorios.
    Proporciona métodos CRUD estándar con manejo de sesiones automático.
    
    Type Parameters:
        T: Tipo del modelo SQLAlchemy
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
