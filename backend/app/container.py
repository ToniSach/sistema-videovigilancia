"""
================================================================================
MÓDULO: container — Contenedor de Inyección de Dependencias (DI) del backend
================================================================================

PROPÓSITO
    Construir UNA sola vez, en el orden correcto, todos los repositorios y
    servicios de larga vida del sistema y mantenerlos accesibles por nombre.
    Es el "cableado" central: quien necesita un servicio lo pide al contenedor
    en vez de instanciarlo (evita duplicar singletons y dependencias cruzadas).

RESPONSABILIDAD PRINCIPAL
    - Instanciar los repositorios (Camera/Event/Recording) y servicios
      (Auth, Camera, Event, AI, Recording) con sus dependencias inyectadas.
    - Resolver el grafo de dependencias en orden: repos → servicios que los
      usan → servicios que se autosuscriben al EventManager.
    - Tolerar fallos de subsistemas opcionales: cada bloque de servicio va en su
      propio try/except (best-effort), salvo el núcleo de repos/AuthService que,
      si falla, aborta el arranque (RuntimeError).

RESPONSABILIDAD (lo que NO hace)
    No arranca cámaras ni hilos de fondo (eso lo hace main.py tras pedir el
    contenedor). Aquí solo se CONSTRUYEN las instancias; algunos servicios, al
    construirse, se suscriben solos al EventManager (p. ej. EventService).

DEPENDENCIAS
    database.repositories.* ... CameraRepository / EventRepository /
                                RecordingRepository (acceso a BD).
    services.* ................ AuthService, CameraService, EventService,
                                AIService, RecordingManager.
    cameras.* ................. CameraManager (singleton), ONVIFDiscovery.
    (todos los imports son DIFERIDOS dentro de __init__ para evitar ciclos.)

COMPONENTES RELACIONADOS
    main.create_app() ......... llama a get_container() (paso 7 del pipeline #1).
    Las rutas de la API ....... obtienen servicios vía get_container().get(...).
    CameraManager / EventManager / DatabaseManager ... singletons de proceso que
                                el contenedor enlaza pero no posee.

PUNTO DE ENTRADA EN LA ARQUITECTURA
    `from backend.app.container import get_container`; get_container() devuelve
    el singleton (lo crea la primera vez). Encaja con la restricción de PROCESO
    ÚNICO: una sola instancia con todo el estado vivo en memoria.

PIPELINE(S)
    Pipeline #1 (Inicio), etapa 7. Indirectamente sostiene casi todos los demás,
    porque los servicios que construye atienden: Auth (#2), Cámaras/Live (#3),
    IA (#9), Eventos (#10), Grabación (#11).
================================================================================
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DependencyContainer:
    """
    Contenedor DI: registro central de repositorios y servicios singleton.

    ROL / RESPONSABILIDAD
        Construir e inyectar el grafo de dependencias del backend y exponerlo
        por nombre (atributo directo o diccionario `_services`).

    SINGLETON (por qué)
        Usa __new__ + flag `_initialized` para garantizar UNA sola instancia por
        proceso. Es imprescindible porque los servicios que crea (CameraManager,
        AIService, RecordingManager...) mantienen hilos, subprocesos FFmpeg,
        buffers y pools VIVOS en memoria; dos contenedores = dos copias de ese
        estado compitiendo por las mismas cámaras/archivos. Encaja con la
        restricción de PROCESO ÚNICO del sistema.

    QUIÉN LO INSTANCIA / CONSUME
        Lo crea get_container() (paso 7 del arranque en main.py). Lo consumen las
        rutas de la API y otros servicios vía get_container().get("nombre").

    DEPENDENCIAS
        Importa de forma diferida repos y servicios (ver docstring del módulo)
        para romper ciclos de import.

    PIPELINE
        #1 (Inicio), etapa 7.
    """

    _instance: Optional['DependencyContainer'] = None
    _initialized: bool = False

    def __new__(cls) -> 'DependencyContainer':
        """Devuelve SIEMPRE la misma instancia (singleton de proceso)."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            logger.debug("Nueva instancia de DependencyContainer creada")
        return cls._instance

    def __init__(self):
        """
        Construye e inyecta todo el grafo de dependencias (idempotente).

        Inputs: ninguno (toma los singletons/repos por import diferido).
        Outputs: self con repos y servicios registrados como atributos y en
            `_services`. El núcleo (repos + AuthService) se construye primero;
            luego, en bloques try/except independientes, los servicios de cámara,
            EventService, AIService y RecordingManager (best-effort: un fallo
            opcional se loguea pero no aborta el resto).
        Excepciones:
            RuntimeError — si falla la construcción del núcleo (repos/AuthService):
            sin eso el sistema no puede operar, así que se propaga al arranque.
        Llamado por: get_container() (la primera vez). Protegido por
            `_initialized` para no reconstruir si se vuelve a invocar.
        """
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

            # ===============================
            # 🤖 AI SERVICE SINGLETON
            # ===============================
            try:
                from backend.app.services.ai_service import AIService
                from backend.app.cameras.camera_manager import CameraManager

                ai_service = AIService(CameraManager())
                self._services["ai_service"] = ai_service
                logger.info("AIService registrado correctamente")
            except Exception as e:
                logger.error(f"Error registrando AIService: {e}", exc_info=True)

            # ===============================
            # 🎬 RECORDING MANAGER SINGLETON
            # ===============================
            try:
                from backend.app.recording.recording_manager import RecordingManager

                recording_manager = RecordingManager(
                    recording_repo=self.recording_repository,
                    event_repo=self.event_repository,
                )
                self._services["recording_manager"] = recording_manager
                logger.info("RecordingManager registrado correctamente")
            except Exception as e:
                logger.error(f"Error registrando RecordingManager: {e}", exc_info=True)
            
            DependencyContainer._initialized = True
            logger.info("DependencyContainer inicializado correctamente")
            
        except Exception as error:
            logger.error(f"Error al inicializar DependencyContainer: {error}")
            raise RuntimeError(f"Fallo en inicialización de dependencias: {error}") from error
    
    def register(self, name: str, instance: object) -> None:
        """
        Registra un servicio extra en el diccionario `_services` en runtime.

        Inputs: name (str no vacío), instance (objeto no None/falsy).
        Excepciones: ValueError si el nombre no es string válido o instance es
            None/falsy.
        Llamado por: código que añade servicios fuera del cableado fijo de
            __init__ (extensiones/tests).
        """
        if not name or not isinstance(name, str):
            raise ValueError("Nombre de servicio debe ser string no vacío")
        
        if not instance:
            raise ValueError("Instancia no puede ser None")
        
        self._services[name] = instance
        logger.debug(f"Servicio registrado: {name}")
    
    def get(self, name: str) -> Any:
        """
        Resuelve un servicio por nombre.

        Busca primero como atributo directo del contenedor (repos y servicios
        del núcleo) y luego en el diccionario `_services` (cámara, eventos, IA,
        grabación y registros dinámicos).

        Inputs: name (str).
        Outputs: la instancia registrada, o None si no existe.
        Llamado por: rutas de la API y servicios que necesitan colaboradores.
        """
        # Primero buscar como atributo directo
        if hasattr(self, name):
            return getattr(self, name)
        
        # Luego buscar en diccionario de servicios dinámicos
        return self._services.get(name)
    
    def has(self, name: str) -> bool:
        """True si `name` está registrado (como atributo o en `_services`)."""
        return hasattr(self, name) or name in self._services

    def unregister(self, name: str) -> bool:
        """
        Elimina un servicio dinámico del contenedor.

        Outputs: True si existía en `_services` y se eliminó; False si no estaba.
        Nota: solo opera sobre servicios registrados en `_services` (los del
        núcleo cableado en __init__ no se desregistran por diseño).
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
    Punto de entrada al contenedor DI (paso 7 del pipeline #1).

    Devuelve el singleton global, construyéndolo la primera vez (lo que dispara
    todo el cableado de repos/servicios). Las siguientes llamadas devuelven la
    misma instancia ya inicializada.

    Outputs: DependencyContainer (singleton).
    Llamado por: main.create_app() y cualquier ruta/servicio que necesite
        resolver dependencias.
    """
    global _container_instance
    
    if _container_instance is None:
        _container_instance = DependencyContainer()
    
    return _container_instance


# Alias para compatibilidad
Container = DependencyContainer