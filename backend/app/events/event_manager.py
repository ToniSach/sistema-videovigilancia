"""
================================================================================
MÓDULO: events.event_manager — Bus central de eventos (pub/sub) del sistema
================================================================================

PROPÓSITO
    Implementa el BUS DE EVENTOS del NVR: un publicador/suscriptor (pub/sub)
    thread-safe y singleton. Toda fuente de eventos del sistema (IA/YOLO,
    detector de movimiento, watchdog de cámaras offline) publica un
    `EventData` aquí; los consumidores se suscriben y reaccionan. Las fuentes
    NUNCA llaman a los notificadores directamente — siempre pasan por este bus.

RESPONSABILIDAD PRINCIPAL
    Desacoplar PRODUCTORES de eventos (workers de captura/IA/movimiento) de los
    CONSUMIDORES (persistencia en BD, Telegram, WebSocket, métricas), y despachar
    cada evento a sus suscriptores en un pool COMPARTIDO de 8 hilos (sin crear
    un hilo nuevo por callback → evita explosión de hilos bajo ráfagas).

DEPENDENCIAS
    ..database.models.EventType ... enum de tipos de evento (referencia de valores)
    concurrent.futures.ThreadPoolExecutor ... pool de despacho (8 workers)
    numpy ......................... el frame opcional viaja como np.ndarray

COMPONENTES RELACIONADOS (suscriptores típicos — se cablean fuera de aquí)
    EventService ......... persiste cada evento en la tabla `events` (BD)
    TelegramNotifier ..... envía alerta + snapshot a Telegram (modo activo)
    NotificationRouter ... aplica reglas por usuario/preferencias/cooldown
    WSNotificationBroker . reenvía a clientes WebSocket /ws/notifications
    MetricsCollector ..... contabiliza eventos para salud/estadísticas

PUNTO DE ENTRADA
    Singleton global `event_manager` (al final del módulo). Publicar:
    `event_manager.emit(EventData(...))`. Suscribir: `subscribe(tipo, cb)` o
    `subscribe_all(cb)`. El apagado lo invoca el `finally` de main (vía shutdown).

PIPELINE(S)
    Pipeline #10 (Eventos): ESTE módulo ES el bus — etapa central.
    Pipeline #13 (Notificaciones): este bus es el ORIGEN; emite hacia
        NotificationRouter → TelegramNotifier / ws_broker.
    Productores aguas arriba: pipeline #9 (IA), #3/#4 (cámaras/RTSP, offline).

DIAGRAMA DEL BUS DE EVENTOS
    ┌──────────────── PRODUCTORES (publican EventData) ────────────────┐
    │  AIScheduler/YOLO    MotionDetector    FFmpegWorker (offline)     │
    │  (#9 IA)             (movimiento)       (#3/#4 cámaras)            │
    └──────────────┬──────────────┬──────────────┬─────────────────────┘
                   │              │              │
                   ▼              ▼              ▼
            ╔══════════════════════════════════════════╗
            ║          EventManager.emit()             ║   (#10 Eventos)
            ║  copia listas de suscriptores bajo lock  ║
            ║  → submit() al pool COMPARTIDO (8 hilos)  ║
            ║  → _safe_call() aísla excepciones         ║
            ╚════════════════════╤═════════════════════╝
                                 │  (fan-out async)
        ┌────────────┬───────────┼─────────────┬───────────────┐
        ▼            ▼           ▼             ▼               ▼
   EventService  Telegram   NotificationRouter  WSBroker   MetricsCollector
   (BD events)   Notifier    (#13 reglas)      (#13 WS)    (salud/stats)
                 (#13)        │
                             ▼
                   Telegram / ws_broker  (entrega final, #13)

    Dos canales de suscripción:
      • subscribe(event_type, cb) → sólo eventos de ESE tipo (filtrado fino).
      • subscribe_all(cb)         → TODOS los eventos (routers/notificadores).
================================================================================
"""
import threading
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor
import time
import numpy as np

from ..database.models import EventType


@dataclass
class EventData:
    """
    Carga útil (payload) inmutable-por-convención que viaja por el bus.

    Es el CONTRATO entre productores y consumidores: todo suscriptor recibe una
    instancia de esta clase. La construyen las fuentes (IA, movimiento, cámaras)
    y la consumen los notificadores/persistencia. El frame es opcional y pesado
    (np.ndarray): los serializadores (p.ej. WSNotificationBroker) lo OMITEN al
    convertir a JSON y, si hace falta imagen, se referencia vía
    metadata["snapshot_path"].

    Campos:
        event_type: valor del enum EventType (ej. "person", "vehicle", "motion",
            "camera_offline"). Es la clave de enrutado en subscribe().
        camera_id:  id de la cámara origen (<=0 o None = evento global de sistema).
        camera_name: nombre legible de la cámara (para mensajes).
        timestamp:  epoch float (time.time()) del instante del evento.
        confidence: confianza 0.0-1.0 (0.0 si no aplica, p.ej. movimiento puro).
        frame:      fotograma asociado (np.ndarray) o None; NO se serializa a JSON.
        metadata:   dict con info extra (snapshot_path, clip_path, object_count…).
    """
    event_type: str           # Valor de EventType enum (ej: "person", "motion")
    camera_id: int
    camera_name: str
    timestamp: float          # time.time()
    confidence: float         # 0.0 si no aplica
    frame: Optional[np.ndarray] = None  # np.ndarray | None
    metadata: Dict[str, Any] = field(default_factory=dict)  # info extra


class EventManager:
    """
    Bus de eventos pub/sub, SINGLETON thread-safe (pipeline #10, etapa central).

    Rol: punto único por el que pasan todos los eventos del sistema. Mantiene dos
    registros de suscriptores (por tipo y globales) y un pool COMPARTIDO de 8
    hilos para despachar sin bloquear a quien publica ni reventar el nº de hilos.

    SINGLETON: el patrón `__new__` + doble-check con lock garantiza UNA sola
    instancia por proceso. Esto es OBLIGATORIO por la arquitectura de proceso
    único del backend: el estado vivo (listas de suscriptores, executor) reside
    en memoria de proceso; con varios workers WSGI cada uno tendría su propio bus
    aislado y los eventos no llegarían a los notificadores correctos.

    Quién lo instancia/consume: se importa como `event_manager` (global). Lo
    suscriben EventService, TelegramNotifier, NotificationRouter, WSNotificationBroker
    y MetricsCollector; lo publican los workers de IA/movimiento/cámaras.

    Dependencias: ThreadPoolExecutor (despacho), EventData (payload). No accede a
    BD ni a red directamente — eso es responsabilidad de los suscriptores.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # Guard de singleton: __new__ devuelve siempre la misma instancia, pero
        # Python vuelve a llamar a __init__ en cada EventManager() — el flag
        # evita reinicializar las listas y, sobre todo, recrear el executor.
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

        self._subscribers: Dict[str, List[Callable]] = {}  # event_type → lista de callbacks
        self._global_subscribers: List[Callable] = []       # reciben TODOS los eventos
        self._lock = threading.Lock()
        # Pool COMPARTIDO para despachar todos los eventos (no 1 hilo/callback):
        # evita la explosión de hilos bajo ráfagas de detecciones.
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="EventManager")

    def subscribe(self, event_type: str, callback: Callable[[EventData], None]) -> None:
        """
        Registra un suscriptor para UN tipo de evento concreto (etapa #10).

        Propósito: filtrado fino — el callback sólo se invoca para eventos cuyo
        `event_type` coincide (p.ej. sólo "person"). Útil para consumidores
        especializados que no quieren ver todo el tráfico del bus.

        Inputs:
            event_type: clave del tipo (valor de EventType).
            callback:   función que recibe un EventData; debe ser rápida o
                delegar trabajo pesado a su propio hilo (corre en el pool de 8).
        Outputs: None (registro idempotente-no: permite duplicados si se llama 2x).
        Excepciones: ninguna propia (toma el lock interno).
        Llamado por: consumidores que filtran por tipo.
        Llama a: nada externo (sólo manipula el dict bajo lock).
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)
            logging.debug(f"Subscriber añadido para event_type={event_type}")

    def subscribe_all(self, callback: Callable[[EventData], None]) -> None:
        """
        Registra un suscriptor GLOBAL: recibe TODOS los eventos (etapa #10→#13).

        Propósito: lo usan los consumidores transversales que necesitan ver todo
        el flujo — EventService (persistencia), TelegramNotifier (modo activo),
        NotificationRouter y WSNotificationBroker (pipeline #13), MetricsCollector.

        Inputs:  callback que recibe cada EventData publicado.
        Outputs: None.
        Excepciones: ninguna propia.
        Llamado por: main._bootstrap_telegram_from_env() (suscribe TelegramNotifier),
            WSNotificationBroker._ensure_subscribed(), EventService, etc.
        Llama a: nada externo.
        """
        with self._lock:
            self._global_subscribers.append(callback)
            logging.debug("Subscriber global añadido")

    def unsubscribe(self, event_type: str, callback: Callable[[EventData], None]) -> None:
        """
        Elimina un suscriptor de un tipo concreto (inverso de subscribe()).

        Inputs:  event_type y el MISMO objeto callback usado al suscribir.
        Outputs: None; si el callback no estaba, no hace nada (silencioso).
        Nota: no existe contraparte para subscribe_all (los globales son
        permanentes durante la vida del proceso).
        """
        with self._lock:
            if event_type in self._subscribers:
                try:
                    self._subscribers[event_type].remove(callback)
                except ValueError:
                    pass

    def emit(self, event_data: EventData) -> None:
        """
        PUBLICA un evento en el bus y lo despacha a sus suscriptores (etapa #10).

        Es el punto de entrada de TODA fuente de eventos del sistema. El despacho
        es ASÍNCRONO: cada callback se ejecuta vía submit() en el pool compartido
        de 8 hilos, así emit() retorna casi al instante y el productor (worker de
        IA/movimiento/cámara) no se bloquea esperando a Telegram, BD o WebSocket.

        Para evitar mantener el lock durante el despacho (los callbacks pueden ser
        lentos), copia las listas de suscriptores bajo lock y luego despacha fuera.

        Inputs:  event_data — el EventData a difundir.
        Outputs: None (efecto: N tareas encoladas en el executor).
        Excepciones: no propaga; las de cada callback las absorbe _safe_call.
        Llamado por: productores — AIScheduler/YOLO (#9), MotionDetector,
            FFmpegWorker en caída de cámara (#3/#4), etc.
        Llama a: self._safe_call (vía executor.submit) por cada suscriptor
            específico del tipo + cada suscriptor global.
        """
        with self._lock:
            specific_callbacks = self._subscribers.get(event_data.event_type, []).copy()
            global_callbacks = self._global_subscribers.copy()

        # Despacho en el pool COMPARTIDO (no un hilo por callback): evita la
        # explosión de hilos bajo ráfagas de eventos.
        for callback in specific_callbacks:
            self._executor.submit(self._safe_call, callback, event_data)

        for callback in global_callbacks:
            self._executor.submit(self._safe_call, callback, event_data)

    def _safe_call(self, callback: Callable[[EventData], None], event_data: EventData) -> None:
        """
        Ejecuta un callback aislando sus excepciones (corre en el pool de 8).

        Garantiza que un suscriptor que lance no tumbe el hilo del pool ni impida
        que el resto de suscriptores reciba el evento — registra el error y sigue.
        """
        try:
            callback(event_data)
        except Exception as e:
            logging.error(f"Error en subscriber: {e}", exc_info=True)

    def shutdown(self) -> None:
        """
        Cierra el pool de despacho al apagar la aplicación (pipeline #1, cierre).

        wait=False: no espera a que terminen los callbacks en vuelo — el apagado
        del backend es best-effort y los suscriptores (Telegram/WS) son daemons.
        Llamado por: la secuencia de shutdown del proceso.
        """
        self._executor.shutdown(wait=False)
        logging.info("EventManager executor detenido")


# Instancia global (singleton): único bus de eventos del proceso.
event_manager = EventManager()