"""
WSNotificationBroker — puente entre EventManager y los clientes WebSocket.

Mantiene un registro de clientes WS conectados, con su user_id y un cache
de cámaras accesibles. Cuando llega un EventData a EventManager, lo serializa
a JSON y lo reenvía a los clientes que tienen permiso sobre la cámara origen.

Diseñado para LAN (típico 1-10 clientes simultáneos). No usa Redis ni queues
externas; todo en memoria del proceso. Esto encaja con el modelo single-process
del backend (Flask app.run threaded; un solo proceso por la arquitectura singleton).

Uso:
    from backend.app.notifications.ws_broker import ws_broker
    ws_broker.register(ws, user_id)   # al conectar
    try:
        # mantener viva la conexión (lectura de pings desde el cliente)
        while True:
            msg = ws.receive(timeout=30)
            if msg == "ping":
                ws.send("pong")
    finally:
        ws_broker.unregister(ws)
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
    ws: Any                     # simple_websocket.ws.Server
    user_id: int
    connected_at: float = field(default_factory=time.time)
    # Cache de cámaras accesibles (rellenado lazy en _allowed_to_see)
    accessible_cameras: Optional[set[int]] = None
    last_perm_check: float = 0.0


class WSNotificationBroker:
    """
    Singleton thread-safe. Mantiene clientes WS y reenvía eventos.

    Hilo de bloqueo: el send() de simple-websocket es síncrono. Para no bloquear
    el callback de EventManager (que corre en un thread del executor compartido
    de 8 workers), cada envío se dispatcha en su propio thread daemon corto.
    Para un par de docenas de clientes simultáneos esto es trivial; si crece,
    sustituir por un ThreadPoolExecutor dedicado.
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
    def register(self, ws: Any, user_id: int) -> _Client:
        client = _Client(ws=ws, user_id=user_id)
        with self._lock:
            self._clients.append(client)
            count = len(self._clients)
        # Suscribirse a EventManager la primera vez que llega un cliente
        # (evita acoplar el módulo en import-time si nadie lo usa).
        self._ensure_subscribed()
        logger.info(
            f"[WS-Notif] Cliente registrado user_id={user_id} (total={count})"
        )
        return client

    def unregister(self, ws: Any) -> None:
        with self._lock:
            self._clients = [c for c in self._clients if c.ws is not ws]
            count = len(self._clients)
        logger.info(f"[WS-Notif] Cliente desregistrado (total={count})")

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    # ------------------------------------------------------------------
    # Integración con EventManager
    # ------------------------------------------------------------------
    def _ensure_subscribed(self) -> None:
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
        """Callback de EventManager. Reenvía a clientes con permiso."""
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

    # ------------------------------------------------------------------
    # Broadcast manual (útil para mensajes de sistema, no de cámara)
    # ------------------------------------------------------------------
    def broadcast(self, payload: dict) -> None:
        """Envía un mensaje arbitrario a todos los clientes conectados."""
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


# Singleton global
ws_broker = WSNotificationBroker()
