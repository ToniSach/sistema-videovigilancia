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
    """
    
    _instance: Optional['DependencyContainer'] = None
    _initialized: bool = False
    
    def __new__(cls) -> 'DependencyContainer':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            logger.debug("Nueva instancia de DependencyContainer creada")
        return cls._instance
    
    def __init__(self):
        if DependencyContainer._initialized:
            return
        
        logger.info("Inicializando DependencyContainer...")
        
        # Inicializar el diccionario PRIMERO
        self._services: dict[str, Any] = {}
        
        try:
            # ✅ IMPORTS ABSOLUTOS DESDE EL BACKEND ÚNICAMENTE
            from backend.app.database.repositories.camera_repository import CameraRepository
            from backend.app.database.repositories.event_repository import EventRepository
            from backend.app.database.repositories.recording_repository import RecordingRepository
            from backend.app.services.auth_service import AuthService
            
            self.camera_repository: CameraRepository = CameraRepository()
            logger.debug("CameraRepository registrado")
            
            self.event_repository: EventRepository = EventRepository()
            logger.debug("EventRepository registrado")
            
            self.recording_repository: RecordingRepository = RecordingRepository()
            logger.debug("RecordingRepository registrado")
            
            self.auth_service: AuthService = AuthService()
            logger.debug("AuthService registrado")
            
            # ===============================
            # 🔧 SERVICIOS DE CÁMARA (MODIFICADO)
            # ===============================
            try:
                from backend.app.cameras.onvif_discovery import ONVIFDiscovery
                from backend.app.cameras.camera_manager import CameraManager
                from backend.app.services.camera_service import CameraService
                
                # ✅ CAMBIO: usar atributo privado consistente
                self._onvif_discovery = ONVIFDiscovery()
                self._services["onvif_discovery"] = self._onvif_discovery
                logger.debug("ONVIFDiscovery registrado")
                
                # Crear CameraManager singleton
                camera_manager = CameraManager()
                
                # Crear CameraService con dependencias
                camera_service = CameraService(
                    camera_repo=self.camera_repository,
                    camera_manager=camera_manager,
                    onvif_discovery=self._onvif_discovery  # ✅ usar el privado
                )
                
                self._services["camera_service"] = camera_service
                logger.info("CameraService registrado correctamente")
                
            except ImportError as e:
                logger.error(f"Error importando dependencias de cámara: {e}")
                logger.error("Instale: pip install ifaddr wsdiscovery onvif-zeep")
            except Exception as e:
                logger.error(f"Error inicializando servicios de cámara: {e}", exc_info=True)
            
            # ===============================
            # 🔧 EVENT SERVICE SINGLETON (NUEVO)
            # ===============================
            try:
                from backend.app.services.event_service import EventService
                
                # Crear EventService singleton (ya registra su callback en EventManager)
                event_service = EventService(self.event_repository)
                self._services["event_service"] = event_service
                logger.info("EventService registrado correctamente")
            except Exception as e:
                logger.error(f"Error registrando EventService: {e}", exc_info=True)
            
            DependencyContainer._initialized = True
            logger.info("DependencyContainer inicializado correctamente")
            
        except Exception as error:
            logger.error(f"Error al inicializar DependencyContainer: {error}")
            raise RuntimeError(f"Fallo en inicialización de dependencias: {error}") from error
    
    def register(self, name: str, instance: object) -> None:
        """Registra un servicio adicional en el contenedor."""
        if not name or not isinstance(name, str):
            raise ValueError("Nombre de servicio debe ser string no vacío")
        
        if not instance:
            raise ValueError("Instancia no puede ser None")
        
        self._services[name] = instance
        logger.debug(f"Servicio registrado: {name}")
    
    def get(self, name: str) -> Any:
        """
        Obtiene un servicio registrado por su nombre.
        """
        # Primero buscar como atributo directo
        if hasattr(self, name):
            return getattr(self, name)
        
        # Luego buscar en diccionario de servicios dinámicos
        return self._services.get(name)
    
    def has(self, name: str) -> bool:
        """Verifica si un servicio está registrado."""
        return hasattr(self, name) or name in self._services
    
    def unregister(self, name: str) -> bool:
        """Elimina un servicio del contenedor."""
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
    """Obtiene la instancia global del contenedor de dependencias."""
    global _container_instance
    
    if _container_instance is None:
        _container_instance = DependencyContainer()
    
    return _container_instance


# Alias para compatibilidad
Container = DependencyContainer