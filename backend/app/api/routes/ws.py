"""
Endpoint WebSocket para notificaciones push en LAN.

Diseñado para que el cliente Android (CamLink_app) reciba eventos del backend
en tiempo real sin depender de FCM/Internet. Pensado para uso LAN domestic
(1-10 clientes simultáneos).

ROL EN LA ARQUITECTURA (Pipeline #13 Notificaciones — entrega push)
    Es el canal de SALIDA WebSocket del bus de eventos. EventManager publica un
    EventData → NotificationRouter decide destinatarios → ws_broker
    (notifications/ws_broker.py) serializa y empuja por este socket a los
    clientes registrados con permiso. Este módulo solo gestiona la CONEXIÓN
    (handshake JWT, ping/pong, alta/baja en el broker); el fan-out de mensajes
    lo hace ws_broker. El blueprint `ws_bp` es un placeholder: el objeto `sock`
    global se enlaza a la app en main.create_app() vía `sock.init_app(app)`.

Wire format
-----------
- URL:     ws(s)://<host>:5000/ws/notifications?token=<jwt_access>
- Auth:    JWT por query string (mismo patrón que /api/v1/cameras/<id>/stream).
- Ping/Pong:
    cliente envía  "ping"   → servidor responde "pong"
    servidor envía "ping"   → cliente debe responder "pong"
  Si no hay tráfico durante WS_IDLE_TIMEOUT, el servidor cierra la conexión.

- Mensajes server→cliente (JSON, una línea por mensaje):
    {
      "type": "hello",
      "user_id": 2,
      "server_time": 1779754000.123
    }
    {
      "type": "event",
      "event_type": "person",
      "camera_id": 1,
      "camera_name": "Foco H265",
      "timestamp": 1779754100.42,
      "confidence": 0.87,
      "metadata": { "lens": "main", "boxes": [...] }
    }
    {
      "type": "pong"
    }

- Mensajes cliente→servidor (texto):
    "ping"           → "pong"
    "ack <event_id>" → reservado para acuses (no implementado aún)

Implementación con flask-sock (sobre simple-websocket). flask-sock funciona
con el servidor de desarrollo de Flask (app.run threaded, el modo que usamos).
"""
from __future__ import annotations

import json
import logging
import time

from flask import Blueprint, request
from flask_jwt_extended import decode_token
from flask_sock import Sock

from backend.app.notifications.ws_broker import ws_broker

logger = logging.getLogger(__name__)

# Sock se inicializa sin la app — la app lo enlaza en main.py: sock.init_app(app)
sock = Sock()
ws_bp = Blueprint("ws", __name__)

WS_IDLE_TIMEOUT = 60          # cierre si no llega ni un ping en 60s
WS_PING_INTERVAL = 25         # cada N s mandamos un ping de salud


def _auth_identity() -> tuple[int | None, int | None]:
    """
    Extrae (user_id, device_id) del JWT en ?token=...; (None, None) si inválido.
    device_id viene en el token móvil (las notis in-app son POR DISPOSITIVO); en
    el de escritorio no está → None (alcance "de cuenta").
    """
    token = request.args.get("token", "")
    if not token:
        return None, None
    try:
        decoded = decode_token(token)
        user_id = int(decoded.get("sub"))
        dev = decoded.get("device_id")
        device_id = int(dev) if dev is not None else None
        return user_id, device_id
    except Exception as e:
        logger.warning(f"[WS-Notif] Token inválido: {e}")
        return None, None


@sock.route("/ws/notifications")
def notifications_ws(ws):
    """
    Handler WebSocket. flask-sock pasa el objeto `ws` (simple_websocket.Server).

    Mantiene la conexión viva con ping/pong y delega los eventos al broker.
    Cuando el cliente se desconecta o lanza una excepción, el broker se limpia
    automáticamente en el finally.
    """
    user_id, device_id = _auth_identity()
    if user_id is None:
        try:
            ws.send(json.dumps({"type": "error", "code": "AUTH",
                                "message": "Token JWT inválido o ausente"}))
        finally:
            ws.close()
        return

    # Saludo inicial — útil para que el cliente Android sepa que la conexión
    # se autenticó correctamente.
    try:
        ws.send(json.dumps({
            "type": "hello",
            "user_id": user_id,
            "server_time": time.time(),
            "ping_interval": WS_PING_INTERVAL,
        }))
    except Exception as e:
        logger.warning(f"[WS-Notif] No pude enviar hello a user_id={user_id}: {e}")
        return

    ws_broker.register(ws, user_id, device_id)
    logger.info(f"[WS-Notif] WebSocket abierto user_id={user_id} device_id={device_id}")

    last_ping_sent = time.time()
    try:
        while True:
            # receive(timeout=N) bloquea hasta N s esperando mensaje del cliente.
            # Si vence el timeout, devuelve None y aprovechamos para mandar ping.
            try:
                msg = ws.receive(timeout=WS_PING_INTERVAL)
            except Exception as e:
                logger.info(
                    f"[WS-Notif] receive lanzó excepción (probable cierre) "
                    f"user_id={user_id}: {e}"
                )
                break

            now = time.time()
            if msg is None:
                # Timeout esperando. Mandar ping para sondear.
                if now - last_ping_sent >= WS_PING_INTERVAL:
                    try:
                        ws.send(json.dumps({"type": "ping",
                                            "server_time": now}))
                        last_ping_sent = now
                    except Exception:
                        break
                continue

            # Hubo mensaje del cliente
            txt = (msg or "").strip().lower()
            if txt == "ping":
                try:
                    ws.send(json.dumps({"type": "pong", "server_time": now}))
                except Exception:
                    break
            elif txt == "pong":
                # respuesta a nuestro ping; no hacer nada
                pass
            elif txt.startswith("ack "):
                # placeholder: en el futuro registrar acuses
                logger.debug(f"[WS-Notif] ack recibido user_id={user_id}: {msg}")
            else:
                # Mensaje no soportado; logueamos y seguimos
                logger.debug(
                    f"[WS-Notif] mensaje no reconocido user_id={user_id}: "
                    f"{msg[:120]!r}"
                )
    finally:
        ws_broker.unregister(ws)
        logger.info(f"[WS-Notif] WebSocket cerrado user_id={user_id}")
