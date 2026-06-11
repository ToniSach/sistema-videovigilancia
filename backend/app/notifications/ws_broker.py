"""
================================================================================
MÓDULO: notifications.ws_broker — Puente EventManager ↔ clientes WebSocket
================================================================================

PROPÓSITO
    Reenvía en tiempo real los eventos del bus a los clientes conectados por
    WebSocket (ruta /ws/notifications, usada por la app Android CamLink y la de
    escritorio). Es un CONSUMIDOR del bus de eventos: serializa cada EventData a
    JSON y lo entrega SÓLO a los clientes con permiso sobre la cámara origen.

RESPONSABILIDAD PRINCIPAL
    Mantener el registro de clientes WS (con su user_id y un cache de cámaras
    accesibles), filtrar por permisos y hacer push del evento, sin bloquear el
    hilo del EventManager (cada send() va en su propio hilo daemon corto).

DEPENDENCIAS
    ..events.event_manager .......... bus al que se suscribe (subscribe_all)
    ..services.permission_service ... qué cámaras puede ver cada usuario
    simple_websocket (vía flask-sock) . el objeto ws de cada cliente
    json/threading .................. serialización y despacho concurrente

COMPONENTES RELACIONADOS
    api/routes/ws.py ..... declara la ruta /ws/notifications y llama a
        register()/unregister(); el Sock se enlaza en main (sock.init_app).
    EventManager ......... origen de los eventos (#10).
    telegram_notifier .... canal hermano del mismo pipeline #13.

PUNTO DE ENTRADA
    Singleton global `ws_broker`. La ruta WS llama register(ws, user_id) al
    conectar y unregister(ws) al cerrar; el reenvío es automático vía _on_event.

PIPELINE(S)
    Pipeline #13 (Notificaciones), etapa de ENTREGA por WebSocket. Consume el
    pipeline #10 (Eventos).

DISEÑO LAN
    Pensado para 1-10 clientes simultáneos. Todo en memoria del proceso (sin
    Redis ni colas externas), coherente con el backend de PROCESO ÚNICO (Flask
    app.run threaded + singletons).

USO (desde la ruta WS)
    from backend.app.notifications.ws_broker import ws_broker
    ws_broker.register(ws, user_id)   # al conectar
    try:
        while True:                   # mantener viva la conexión (pings)
            msg = ws.receive(timeout=30)
            if msg == "ping":
                ws.send("pong")
    finally:
        ws_broker.unregister(ws)
================================================================================
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class _Client:
    """
    Estado de un cliente WebSocket conectado (interno del broker).

    Guarda el objeto ws, el user_id autenticado y un cache de las cámaras que
    puede ver (con TTL) para no consultar permisos en cada evento.
    """
    ws: Any                     # simple_websocket.ws.Server
    user_id: int
    # Dispositivo del cliente (del JWT). None = login sin dispositivo (escritorio)
    # → usa preferencias "de cuenta". Las notis in-app son POR DISPOSITIVO.
    device_id: Optional[int] = None
    connected_at: float = field(default_factory=time.time)
    # Cache de cámaras accesibles (rellenado lazy en _allowed_to_see)
    accessible_cameras: Optional[set[int]] = None
    last_perm_check: float = 0.0


class WSNotificationBroker:
    """
    Broker WebSocket de notificaciones, SINGLETON thread-safe (pipeline #13).

    Rol: registrar clientes WS y reenviarles, filtrados por permiso, los eventos
    del bus. Se suscribe a EventManager.subscribe_all la PRIMERA vez que un
    cliente se conecta (suscripción lazy para no acoplar en import-time).

    SINGLETON (__new__ + lock): único registro de clientes por proceso, acorde a
    la arquitectura de proceso único — todo el estado vive en memoria.

    Quién lo instancia/consume: instancia global `ws_broker`; la ruta
    /ws/notifications (api/routes/ws.py) llama register/unregister. Lo alimenta
    EventManager.

    Concurrencia: send() de simple-websocket es SÍNCRONO. Para no bloquear el
    hilo del EventManager (pool compartido de 8), cada envío se hace en su propio
    hilo daemon corto. Suficiente para decenas de clientes LAN; si creciera,
    cambiar a un ThreadPoolExecutor dedicado.
    """

    _instance = None
    _instance_lock = threading.Lock()

    # Re-chequear permisos cada 30s para reflejar cambios sin desconectar
    PERM_CACHE_TTL = 30.0

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._clients: list[_Client] = []
        self._lock = threading.Lock()
        self._subscribed_to_event_manager = False

    # ------------------------------------------------------------------
    # Registro/desregistro de clientes
    # ------------------------------------------------------------------
    def register(self, ws: Any, user_id: int, device_id: Optional[int] = None) -> _Client:
        """
        Da de alta un cliente WS y asegura la suscripción al bus (etapa #13).

        Propósito: tras autenticar la conexión WS, registra al cliente para que
        empiece a recibir eventos. La primera alta dispara la suscripción lazy a
        EventManager.

        Inputs:  ws (objeto WebSocket), user_id autenticado, device_id del JWT
            (None = login sin dispositivo → preferencias "de cuenta"). Las notis
            in-app se filtran POR DISPOSITIVO.
        Outputs: el _Client creado (el llamador lo usa para su bucle de vida).
        Llamado por: la ruta /ws/notifications (api/routes/ws.py) al conectar.
        Llama a: self._ensure_subscribed().
        """
        client = _Client(ws=ws, user_id=user_id, device_id=device_id)
        with self._lock:
            self._clients.append(client)
            count = len(self._clients)
        # Suscribirse a EventManager la primera vez que llega un cliente
        # (evita acoplar el módulo en import-time si nadie lo usa).
        self._ensure_subscribed()
        logger.info(
            f"[WS-Notif] Cliente registrado user_id={user_id} device_id={device_id} (total={count})"
        )
        return client

    def unregister(self, ws: Any) -> None:
        """
        Da de baja un cliente WS (al cerrarse la conexión).

        Inputs:  ws (el mismo objeto pasado a register). Outputs: None.
        Llamado por: el finally de la ruta /ws/notifications.
        """
        with self._lock:
            self._clients = [c for c in self._clients if c.ws is not ws]
            count = len(self._clients)
        logger.info(f"[WS-Notif] Cliente desregistrado (total={count})")

    def client_count(self) -> int:
        """Nº de clientes WS conectados (para diagnóstico/health)."""
        with self._lock:
            return len(self._clients)

    # ------------------------------------------------------------------
    # Integración con EventManager
    # ------------------------------------------------------------------
    def _ensure_subscribed(self) -> None:
        """
        Suscribe el broker a EventManager.subscribe_all UNA sola vez (lazy).

        Idempotente (flag _subscribed_to_event_manager). Se difiere hasta el
        primer cliente para no acoplar el módulo al bus en import-time si nadie
        usa WebSocket. Llamado por register().
        """
        if self._subscribed_to_event_manager:
            return
        try:
            from ..events.event_manager import event_manager
            event_manager.subscribe_all(self._on_event)
            self._subscribed_to_event_manager = True
            logger.info("[WS-Notif] Suscrito a EventManager (subscribe_all)")
        except Exception as e:
            logger.error(f"[WS-Notif] No pude suscribir al EventManager: {e}")

    def _on_event(self, event_data) -> None:
        """
        Callback del bus: reenvía el evento a los clientes con permiso (#10→#13).

        Propósito: serializa el EventData a JSON (sin el frame) y lo hace push a
        cada cliente cuyo usuario puede ver la cámara origen. Cada envío va en su
        propio hilo daemon para no bloquear el hilo del EventManager.

        Inputs:  event_data publicado en el bus.
        Outputs: None (efecto: N envíos WS; limpia clientes muertos).
        Llamado por: EventManager (suscrito vía subscribe_all).
        Llama a: _serialize_event, _allowed_to_see, _safe_send (en hilos).
        """
        try:
            payload = self._serialize_event(event_data)
        except Exception as e:
            logger.error(f"[WS-Notif] Error serializando evento: {e}")
            return

        with self._lock:
            clients = list(self._clients)

        if not clients:
            return

        dead: list[_Client] = []
        for client in clients:
            if not self._allowed_to_see(client, event_data.camera_id):
                continue
            # Respetar las PREFERENCIAS del usuario: si desactivó las
            # notificaciones (o este tipo/horario/día no está habilitado), NO se
            # le envía el push. Antes el broker mandaba a todo cliente con
            # permiso de cámara IGNORANDO las preferencias → "desactivar
            # notificaciones" no surtía efecto en el móvil.
            if not self._user_wants_event(client, event_data):
                continue
            try:
                # send() puede bloquear si el cliente está lento.
                # Lo dispatchamos en un thread corto para no bloquear el
                # callback del EventManager (que se reusa entre eventos).
                threading.Thread(
                    target=self._safe_send,
                    args=(client, payload, dead),
                    daemon=True,
                    name=f"WSSend-uid{client.user_id}",
                ).start()
            except Exception as e:
                logger.warning(f"[WS-Notif] Error encolando envío: {e}")
                dead.append(client)

        # Limpieza diferida — el thread send marca dead si falla.
        if dead:
            with self._lock:
                self._clients = [c for c in self._clients if c not in dead]

    def _safe_send(self, client: _Client, payload: str, dead: list[_Client]) -> None:
        try:
            client.ws.send(payload)
        except Exception as e:
            logger.info(
                f"[WS-Notif] Cliente user_id={client.user_id} desconectado al enviar: {e}"
            )
            dead.append(client)

    # ------------------------------------------------------------------
    # Helpers de serialización y permisos
    # ------------------------------------------------------------------
    def _serialize_event(self, event_data) -> str:
        """
        Convierte EventData → JSON listo para mandar por WS.

        Omite el frame numpy (binario pesado). El cliente Android puede pedir
        snapshot por separado vía /api/v1/mobile/cameras/<id>/thumbnail si
        lo necesita.
        """
        return json.dumps({
            "type": "event",
            "event_type": event_data.event_type,
            "camera_id": event_data.camera_id,
            "camera_name": event_data.camera_name,
            "timestamp": event_data.timestamp,
            "confidence": float(event_data.confidence or 0.0),
            "metadata": event_data.metadata or {},
        }, ensure_ascii=False)

    def _allowed_to_see(self, client: _Client, camera_id: int) -> bool:
        """
        Devuelve True si el usuario puede recibir notificaciones de la cámara.
        Cachea la lista de cámaras accesibles por PERM_CACHE_TTL segundos.
        """
        now = time.time()
        if (client.accessible_cameras is None
                or now - client.last_perm_check > self.PERM_CACHE_TTL):
            try:
                from ..services.permission_service import PermissionService
                ids = PermissionService().get_accessible_cameras(client.user_id)
                client.accessible_cameras = set(ids or [])
                client.last_perm_check = now
            except Exception as e:
                logger.error(
                    f"[WS-Notif] Error consultando permisos user_id="
                    f"{client.user_id}: {e}"
                )
                # Si fallan permisos, mejor no entregar (fail-closed) salvo
                # eventos sin camera_id concreto (camera_id=0 o negativo).
                return camera_id is None or camera_id <= 0
        if camera_id is None or camera_id <= 0:
            # Eventos globales del sistema (sin cámara) van a todos los clientes
            return True
        return camera_id in client.accessible_cameras

    def _user_wants_event(self, client: "_Client", event_data) -> bool:
        """
        ¿Este DISPOSITIVO quiere recibir el evento in-app (push WS)?

        Las notis in-app son POR DISPOSITIVO: se evalúan las preferencias del
        alcance del cliente (client.device_id). Si ese teléfono no tiene fila
        propia para el evento, cae a las preferencias "de cuenta" (device_id NULL)
        como valor por defecto. Debe existir una preferencia HABILITADA que
        aplique a la cámara (específica>general), con horario/día válidos y un
        canal de app/push. Modelo OPT-IN: sin preferencia aplicable → NO notifica.

        - Eventos de sistema sin cámara (camera_id<=0) se entregan siempre.
        - Ante un fallo de BD se entrega (fail-open) para no perder alertas.
        """
        cam_id = event_data.camera_id
        if cam_id is None or cam_id <= 0:
            return True
        etype = event_data.event_type
        try:
            from ..database.connection import db_manager
            from .preference_eval import wanted_channels, APP_CHANNELS

            with db_manager.get_session() as session:
                # Fuente ÚNICA de verdad (compartida con router y telegram), pero
                # con el ALCANCE del dispositivo del cliente: in-app por teléfono,
                # con respaldo a las preferencias de cuenta si el teléfono no las
                # tiene configuradas. Solo cuenta para WS un canal de app/push.
                channels = wanted_channels(
                    session, client.user_id, etype, cam_id,
                    device_id=client.device_id, fallback_to_account=True,
                )
                return bool(channels & APP_CHANNELS)
        except Exception as e:
            logger.error(
                f"[WS-Notif] Error evaluando preferencias user_id={client.user_id}: {e}"
            )
            return True  # fail-open: no silenciar por un error transitorio

    # ------------------------------------------------------------------
    # Broadcast manual (útil para mensajes de sistema, no de cámara)
    # ------------------------------------------------------------------
    def broadcast(self, payload: dict) -> None:
        """
        Difunde un mensaje arbitrario a TODOS los clientes (sin filtro de cámara).

        Para mensajes de sistema (no ligados a una cámara): se serializa el dict
        y se envía a cada cliente, eliminando los que fallen. A diferencia de
        _on_event, NO comprueba permisos por cámara.
        """
        data = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            clients = list(self._clients)
        dead: list[_Client] = []
        for client in clients:
            try:
                client.ws.send(data)
            except Exception:
                dead.append(client)
        if dead:
            with self._lock:
                self._clients = [c for c in self._clients if c not in dead]


# Singleton global: único broker WS del proceso.
ws_broker = WSNotificationBroker()
