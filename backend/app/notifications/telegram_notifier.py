import requests
import logging
import threading
import time
import os
from datetime import datetime
from typing import Optional

from ..events.event_manager import EventManager, EventData, event_manager
from ..database.models import SystemConfig
from ..database.connection import db_manager


class TelegramNotifier:
    def __init__(self):
        self._bot_token = ""
        self._chat_id = ""
        self._enabled = False
        self._notify_types = {"person", "vehicle", "camera_offline", "tampering"}
        self._lock = threading.Lock()

        self._load_config()
        event_manager.subscribe_all(self._on_event)
        logging.info("TelegramNotifier inicializado")

    def _load_config(self) -> None:
        try:
            with db_manager.session_scope() as session:
                config = session.query(SystemConfig).first()
                if config:
                    self._bot_token = config.telegram_bot_token or ""
                    self._chat_id = config.telegram_chat_id or ""
                    self._enabled = getattr(config, 'telegram_enabled', False)

                    self._notify_types = set()
                    if getattr(config, 'notify_person', True):
                        self._notify_types.add("person")
                    if getattr(config, 'notify_vehicle', True):
                        self._notify_types.add("vehicle")
                    if getattr(config, 'notify_motion', False):
                        self._notify_types.add("motion")
                    if getattr(config, 'notify_offline', True):
                        self._notify_types.add("camera_offline")
                    if getattr(config, 'notify_tampering', True):
                        self._notify_types.add("tampering")

                    logging.info(f"Config Telegram cargada: enabled={self._enabled}")
        except Exception as e:
            logging.error(f"Error cargando config Telegram: {e}")

    def reload_config(self) -> None:
        with self._lock:
            self._load_config()

    def _on_event(self, event_data: EventData) -> None:
        with self._lock:
            if not self._enabled:
                return
            if event_data.event_type not in self._notify_types:
                return

        threading.Thread(
            target=self._send_async,
            args=(event_data,),
            daemon=True
        ).start()

    def _send_async(self, event_data: EventData) -> None:
        try:
            self.send_notification(event_data)
        except Exception as e:
            logging.error(f"Error enviando notificación: {e}")

    def send_notification(self, event_data: EventData) -> bool:
        EMOJIS = {
            "motion": "📹",
            "person": "🚨",
            "vehicle": "🚗",
            "camera_offline": "⚠️",
            "tampering": "🔴"
        }

        emoji = EMOJIS.get(event_data.event_type, "📋")
        dt = datetime.fromtimestamp(event_data.timestamp)
        datetime_str = dt.strftime("%Y-%m-%d %H:%M:%S")

        message = (
            f"{emoji} *Alerta de videovigilancia*

"
            f"Cámara: {event_data.camera_name}
"
            f"Tipo: {event_data.event_type}
"
            f"Hora: {datetime_str}
"
            f"Confianza: {event_data.confidence:.0%}"
        )

        if event_data.metadata:
            for key, value in event_data.metadata.items():
                message += f"
{key}: {value}"

        snapshot_path = event_data.metadata.get("snapshot_path") if event_data.metadata else None
        if snapshot_path and os.path.exists(snapshot_path):
            return self._send_photo(message, snapshot_path)
        else:
            return self._send_message(message)

    def _send_message(self, text: str) -> bool:
        if not self._bot_token or not self._chat_id:
            return False

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        data = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }

        for attempt in range(3):
            try:
                response = requests.post(url, json=data, timeout=10)
                if response.status_code == 200:
                    return True
                else:
                    logging.warning(f"Telegram API error: {response.status_code}")
            except Exception as e:
                logging.error(f"Error enviando mensaje (intento {attempt+1}): {e}")

            time.sleep(2 ** attempt)

        return False

    def _send_photo(self, caption: str, photo_path: str) -> bool:
        if not self._bot_token or not self._chat_id:
            return False

        url = f"https://api.telegram.org/bot{self._bot_token}/sendPhoto"

        for attempt in range(3):
            try:
                with open(photo_path, "rb") as f:
                    files = {"photo": f}
                    data = {
                        "chat_id": self._chat_id,
                        "caption": caption,
                        "parse_mode": "Markdown"
                    }
                    response = requests.post(url, data=data, files=files, timeout=30)

                    if response.status_code == 200:
                        return True
                    else:
                        logging.warning(f"Telegram API error (photo): {response.status_code}")
            except Exception as e:
                logging.error(f"Error enviando foto (intento {attempt+1}): {e}")

            time.sleep(2 ** attempt)

        return False

    def test_connection(self) -> bool:
        return self._send_message("✅ Sistema NVR conectado correctamente")


telegram_notifier = TelegramNotifier()
