"""
Telegram Notifier - Notificaciones de seguridad vía Telegram Bot.
Versión corregida: UX mejorada, plurales en español, y seguridad de rutas.
"""

import requests
import logging
import threading
import time
import os
from datetime import datetime
from typing import Optional, Dict

from backend.app.events.event_manager import EventManager, EventData, event_manager
from backend.app.database.models import SystemConfig
from backend.app.database.connection import db_manager


class TelegramNotifier:
    # Mapeo de tipos a español con manejo de plural
    TYPE_NAMES: Dict[str, str] = {
        "person": "persona",
        "vehicle": "vehículo",
        "car": "auto",
        "motorcycle": "moto",
        "bus": "autobús",
        "truck": "camión",
        "motion": "movimiento",
        "camera_offline": "cámara desconectada",
        "tampering": "sabotaje",
        "ai_detection": "detección IA"
    }

    # Emojis por tipo
    EMOJIS: Dict[str, str] = {
        "motion": "📹",
        "person": "🚨",
        "vehicle": "🚗",
        "car": "🚗",
        "motorcycle": "🏍️",
        "bus": "🚌",
        "truck": "🚛",
        "camera_offline": "⚠️",
        "tampering": "🔴",
        "ai_detection": "🤖"
    }

    def __init__(self):
        self._bot_token = ""
        self._chat_id = ""
        self._enabled = False
        self._notify_types = {"person", "vehicle", "camera_offline", "tampering"}
        self._lock = threading.Lock()
        
        # Seguridad: directorio base permitido para snapshots
        self._allowed_base_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "recordings")
        )

        self._load_config()
        event_manager.subscribe_all(self._on_event)
        logging.info("TelegramNotifier inicializado")

    def _load_config(self) -> None:
        """Carga configuración desde SystemConfig (tabla key-value)"""
        try:
            with db_manager.get_session() as session:
                configs = session.query(SystemConfig).all()
                config_dict = {c.key: c.value for c in configs}
                
                self._bot_token = config_dict.get("telegram_bot_token", "")
                self._chat_id = config_dict.get("telegram_chat_id", "")
                self._enabled = config_dict.get("telegram_enabled", "false").lower() == "true"

                self._notify_types = set()
                if config_dict.get("notify_person", "true").lower() == "true":
                    self._notify_types.add("person")
                if config_dict.get("notify_vehicle", "true").lower() == "true":
                    self._notify_types.add("vehicle")
                if config_dict.get("notify_motion", "false").lower() == "true":
                    self._notify_types.add("motion")
                if config_dict.get("notify_offline", "true").lower() == "true":
                    self._notify_types.add("camera_offline")
                if config_dict.get("notify_tampering", "true").lower() == "true":
                    self._notify_types.add("tampering")

                logging.info(f"Config Telegram cargada: enabled={self._enabled}")
        except Exception as e:
            logging.error(f"Error cargando config Telegram: {e}")
            self._enabled = False

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

    def _get_spanish_name(self, event_type: str, count: int = 1) -> str:
        """
        Obtiene el nombre en español manejando plurales correctamente.
        
        Args:
            event_type: Tipo de evento en inglés (person, vehicle, etc.)
            count: Cantidad de objetos
            
        Returns:
            String formateado en español (ej: "3 personas", "1 vehículo")
        """
        base_name = self.TYPE_NAMES.get(event_type, event_type)
        
        if count == 1:
            return f"1 {base_name}"
        
        # Pluralización en español
        if base_name.endswith(("a", "e", "i", "o", "u")):
            # Persona -> Personas, Auto -> Autos
            plural = f"{base_name}s"
        elif base_name.endswith("z"):
            # Autobús -> Autobuses (ya está en plural en el dict, pero por si acaso)
            plural = f"{base_name[:-1]}ces"
        else:
            # Camión -> Camiones, etc.
            plural = f"{base_name}es"
            
        return f"{count} {plural}"

    def _validate_snapshot_path(self, snapshot_path: Optional[str]) -> Optional[str]:
        if not snapshot_path:
            return None
        
        try:
            # 1. Normalizar y resolver symlinks (seguridad)
            real_base = os.path.realpath(self._allowed_base_path)
            real_path = os.path.realpath(os.path.join(real_base, snapshot_path))
            
            # 2. Verificar que está dentro del directorio permitido
            if not real_path.startswith(real_base + os.sep):
                logger.error(f"Path traversal detectado: {snapshot_path}")
                return None
            
            # 3. Verificar que es archivo (no symlink a otro lado)
            if not os.path.isfile(real_path):
                return None
                
            return real_path
            
        except Exception as e:
            logger.error(f"Error validando ruta: {e}")
            return None

    def send_notification(self, event_data: EventData) -> bool:
        """
        Construye y envía notificación de Telegram.
        UX mejorada: mensajes limpios sin datos técnicos crudos.
        """
        emoji = self.EMOJIS.get(event_data.event_type, "📋")
        dt = datetime.fromtimestamp(event_data.timestamp)
        datetime_str = dt.strftime("%d/%m/%Y %H:%M:%S")  # Formato español

        # Obtener conteo y nombre formateado en español
        object_count = event_data.metadata.get("object_count", 1) if event_data.metadata else 1
        object_text = self._get_spanish_name(event_data.event_type, object_count)

        # Construir mensaje principal limpio
        message_lines = [
            f"{emoji} *Alerta de Videovigilancia*",
            "",
            f"📹 *Cámara ID:* `{event_data.camera_id}`",
            f"🎯 *Detectado:* {object_text}",
            f"📊 *Confianza:* {event_data.confidence:.0%}",
            f"🕐 *Hora:* {datetime_str}"
        ]

        # Metadata adicional seleccionada (solo lo relevante para el usuario)
        if event_data.metadata:
            # Si hay múltiples tipos diferentes (ej: 1 persona + 2 autos)
            objects_list = event_data.metadata.get("objects", [])
            if objects_list and len(set(objects_list)) > 1:
                unique_types = [self.TYPE_NAMES.get(t, t) for t in set(objects_list)]
                message_lines.append(f"📋 *Tipos detectados:* {', '.join(unique_types)}")
            
            # Mostrar posición solo si es objeto único (útil para debug visual)
            if object_count == 1 and "bbox" in event_data.metadata:
                bbox = event_data.metadata["bbox"]
                # Solo mostrar coordenadas redondeadas, no el dict completo
                x_center = (bbox['x1'] + bbox['x2']) // 2
                y_center = (bbox['y1'] + bbox['y2']) // 2
                message_lines.append(f"📍 *Posición:* ({x_center}, {y_center})")

        # Unir todo el mensaje
        message = "\n".join(message_lines)

        # Intentar enviar foto con validación de seguridad
        snapshot_path = event_data.metadata.get("snapshot_path") if event_data.metadata else None
        validated_path = self._validate_snapshot_path(snapshot_path)
        
        if validated_path:
            return self._send_photo(message, validated_path)
        else:
            # Fallback: mensaje sin imagen (indicar que hay imagen disponible en app)
            if snapshot_path:
                message += "\n\n⚠️ _Imagen disponible en la aplicación de escritorio_"
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
        return self._send_message("✅ *Sistema NVR conectado correctamente*\n\nEl bot está configurado y listo para enviar alertas de videovigilancia.")


telegram_notifier = TelegramNotifier()