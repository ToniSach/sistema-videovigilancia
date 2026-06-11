"""
Paquete `notifications` — Entrega final de alertas (pipeline #13).

Consumidores del bus de eventos (#10) que materializan las notificaciones por
los distintos canales: Telegram (telegram_notifier), WebSocket LAN (ws_broker)
y el poller del bot para vincular chats (telegram_bot_poller). Reexporta el
notificador de Telegram, que es el canal principal; los demás se importan por
su módulo concreto cuando se necesitan.
"""
from .telegram_notifier import TelegramNotifier, telegram_notifier

__all__ = ["TelegramNotifier", "telegram_notifier"]
