"""
Contenedor de dependencias (Dependency Injection Container).
Implementa patrón Singleton para gestión centralizada de servicios.
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DependencyContainer:
    """
    Contenedor de inyección de dependencias.
    Mantiene instancias singleton de repositorios y servicios.
    
    Implementa patrón Singleton para asegurar una única instancia global.
    """
    
    _instance: Optional['DependencyContainer'] = None
    _initialized: bool = False
    
    def __new__(cls) -> 'DependencyContainer':
        """
        Sobrescribe __new__ para implementar Singleton.
        Asegura que solo exista una instancia del contenedor.
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            logger.debug("Nueva instancia de DependencyContainer creada")
        return cls._instance
    
    def __init__(self):
        """
        Inicializa el contenedor solo una vez.
        Crea instancias de repositorios base en orden de dependencias.
        """
        # Evitar re-inicialización
        if DependencyContainer._initialized:
            return
        
        logger.info("Inicializando DependencyContainer...")
        
        try:
            # Importaciones diferidas para evitar circular imports
            from backend.app.database.repositories.camera_repository import CameraRepository
            from backend.app.database.repositories.event_repository import EventRepository
            from backend.app.database.repositories.recording_repository import RecordingRepository
            from backend.app.services.auth_service import AuthService
            
            # Instanciar repositorios (sin dependencias entre ellos)
            self.camera_repository: CameraRepository = CameraRepository()
            logger.debug("CameraRepository registrado")
            
            self.event_repository: EventRepository = EventRepository()
            logger.debug("EventRepository registrado")
            
            self.recording_repository: RecordingRepository = RecordingRepository()
            logger.debug("RecordingRepository registrado")
            
            # Instanciar servicios
            self.auth_service: AuthService = AuthService()
            logger.debug("AuthService registrado")
            
            # Diccionario para servicios adicionales registrados dinámicamente
            self._services: dict[str, Any] = {}
            
            DependencyContainer._initialized = True
            logger.info("DependencyContainer inicializado correctamente")
            
        except Exception as error:
            logger.error(f"Error al inicializar DependencyContainer: {error}")
            raise RuntimeError(f"Fallo en inicialización de dependencias: {error}") from error
    
    def register(self, name: str, instance: object) -> None:
        """
        Registra un servicio adicional en el contenedor.
        Permite extensión dinámica de dependencias.
        
        Args:
            name: Nombre identificador del servicio
            instance: Instancia del servicio a registrar
            
        Example:
            container.register("notification_service", NotificationService())
        """
        if not name or not isinstance(name, str):
            raise ValueError("Nombre de servicio debe ser string no vacío")
        
        if not instance:
            raise ValueError("Instancia no puede ser None")
        
        self._services[name] = instance
        setattr(self, name, instance)
        logger.debug(f"Servicio registrado: {name}")
    
    def get(self, name: str) -> Any:
        """
        Obtiene un servicio registrado por su nombre.
        
        Args:
            name: Nombre del servicio
            
        Returns:
            Instancia del servicio o None si no existe
            
        Example:
            auth_svc = container.get("auth_service")
        """
        # Primero buscar como atributo directo
        if hasattr(self, name):
            return getattr(self, name)
        
        # Luego buscar en diccionario de servicios dinámicos
        return self._services.get(name)
    
    def has(self, name: str) -> bool:
        """
        Verifica si un servicio está registrado.
        
        Args:
            name: Nombre del servicio
            
        Returns:
            bool: True si existe, False en caso contrario
        """
        return hasattr(self, name) or name in self._services
    
    def unregister(self, name: str) -> bool:
        """
        Elimina un servicio del contenedor.
        Útil para testing o reconfiguración.
        
        Args:
            name: Nombre del servicio a eliminar
            
        Returns:
            bool: True si se eliminó, False si no existía
        """
        if name in self._services:
            del self._services[name]
            if hasattr(self, name):
                delattr(self, name)
            logger.debug(f"Servicio eliminado: {name}")
            return True
        return False


# Instancia global del contenedor
_container_instance: Optional[DependencyContainer] = None


def get_container() -> DependencyContainer:
    """
    Obtiene la instancia global del contenedor de dependencias.
    Función factory que asegura inicialización lazy.
    
    Returns:
        DependencyContainer: Instancia única del contenedor
        
    Example:
        from backend.app.container import get_container
        container = get_container()
        camera_repo = container.camera_repository
    """
    global _container_instance
    
    if _container_instance is None:
        _container_instance = DependencyContainer()
    
    return _container_instance
