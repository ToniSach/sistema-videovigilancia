"""
Telegram Notifier PRO (híbrido)

✔ Modo pasivo (recomendado) → usado por NotificationRouter
✔ Modo activo opcional → puede suscribirse a eventos
✔ Multi-chat support
✔ Seguridad en rutas de snapshots
✔ UX mejorada (mensajes claros)
✔ Thread-safe
"""

import requests
import logging
import threading
import time
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional, Dict, List

from backend.app.database.connection import db_manager
from backend.app.database.models import SystemConfig
from backend.app.events.event_manager import EventData, event_manager

logger = logging.getLogger(__name__)


class TelegramNotifier:

    TYPE_NAMES = {
        "person": "persona",
        "vehicle": "vehículo",
        "motion": "movimiento",
        "camera_offline": "cámara desconectada",
        "tampering": "sabotaje"
    }

    EMOJIS = {
        "person": "🚨",
        "vehicle": "🚗",
        "motion": "📹",
        "camera_offline": "⚠️",
        "tampering": "🔴"
    }

    def __init__(self, auto_subscribe: bool = False):
        """
        auto_subscribe=False → modo pasivo (RECOMENDADO)
        auto_subscribe=True → modo activo (escucha eventos)
        """
        self._bot_token = ""
        self._enabled = False
        self._chat_ids: List[str] = []
        self._notify_types = set()
        self._lock = threading.Lock()

        # POOL DEDICADO para HTTP a Telegram. Aislado de GlobalExecutor.
        # Motivo: cada send_event_notification hace 1-2 requests HTTP con
        # timeout 10-30s. Si compartiéramos GlobalExecutor con la grabación
        # y la IA, esas tareas se quedarían en cola detrás de los Telegrams.
        # 2 workers = máx 2 envíos simultáneos por proceso → suficiente
        # para alertas (con cooldown=30s) y no satura red de salida.
        # Cualquier exceso se encola en el pool (no en global_executor).
        self._http_pool = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="TelegramHTTP"
        )

        # Seguridad snapshots: el path real es settings.RECORDINGS_PATH
        # (típicamente "recordings/" relativo al CWD). Antes calculábamos
        # "<backend>/../../recordings" que daba "<root>/backend/recordings/"
        # — directorio inexistente. Como los snapshots se guardan en
        # "<root>/recordings/snapshots/...", la validación SIEMPRE fallaba
        # y TelegramNotifier mandaba sólo texto sin foto.
        try:
            from backend.app.config import settings as _settings
            self._allowed_base_path = os.path.abspath(_settings.RECORDINGS_PATH)
        except Exception:
            self._allowed_base_path = os.path.abspath("recordings")

        self._load_config()

        if auto_subscribe:
            event_manager.subscribe_all(self._on_event)
            logger.info("TelegramNotifier en modo ACTIVO")

        logger.info("TelegramNotifier inicializado")

    # =========================================================================
    # CONFIG
    # =========================================================================

    def _load_config(self):
        try:
            with db_manager.get_session() as session:
                configs = session.query(SystemConfig).all()
                config_dict = {c.key: c.value for c in configs}

                self._bot_token = config_dict.get("telegram_bot_token", "")
                self._enabled = config_dict.get("telegram_enabled", "false").lower() == "true"

                # MULTI CHAT (separados por coma)
                chats = config_dict.get("telegram_chat_ids", "")
                self._chat_ids = [c.strip() for c in chats.split(",") if c.strip()]

                # fallback compatibilidad
                single_chat = config_dict.get("telegram_chat_id")
                if single_chat and not self._chat_ids:
                    self._chat_ids = [single_chat]

                # filtros
                self._notify_types = {
                    k.replace("notify_", "")
                    for k, v in config_dict.items()
                    if k.startswith("notify_") and v.lower() == "true"
                }

        except Exception as e:
            logger.error(f"Error cargando config Telegram: {e}")
            self._enabled = False

    def reload_config(self):
        with self._lock:
            self._load_config()

    # =========================================================================
    # MODO ACTIVO (OPCIONAL)
    # =========================================================================

    def _on_event(self, event_data: EventData):
        with self._lock:
            if not self._enabled:
                logger.warning(
                    f"[TELEGRAM] Evento '{event_data.event_type}' IGNORADO: "
                    f"Telegram no está activo (revisa SystemConfig.telegram_enabled)"
                )
                return
            if not self._chat_ids:
                logger.warning(
                    f"[TELEGRAM] Evento '{event_data.event_type}' IGNORADO: "
                    f"no hay chat_ids configurados"
                )
                return
            if self._notify_types and event_data.event_type not in self._notify_types:
                logger.info(
                    f"[TELEGRAM] Evento '{event_data.event_type}' filtrado "
                    f"(activos: {self._notify_types})"
                )
                return

        logger.info(
            f"[TELEGRAM] 📤 Enviando evento '{event_data.event_type}' "
            f"de cam {event_data.camera_id} a {len(self._chat_ids)} chat(s)"
        )
        # Pool DEDICADO de Telegram (no global_executor) → los HTTP lentos
        # NO bloquean al frame distributor ni a la grabación. Si los 2
        # workers están ocupados, la tarea se encola en el pool de Telegram,
        # NO en el del resto del sistema.
        try:
            self._http_pool.submit(self.send_event_notification, event_data)
        except RuntimeError:
            logger.warning(
                f"[TELEGRAM] HTTP pool cerrado, evento '{event_data.event_type}' "
                f"cam {event_data.camera_id} descartado"
            )

    # =========================================================================
    # API PRINCIPAL (PASIVA - USAR ESTA)
    # =========================================================================

    # FIX F0.1: Método público expuesto para NotificationRouter
    def send_message(self, chat_id: str, text: str) -> bool:
        """
        Método público requerido por NotificationRouter.
        Wrapper sobre _send_message interno.
        """
        return self._send_message(chat_id, text)

    def send_event_notification(self, event_data: EventData) -> bool:
        """
        Método principal para NotificationRouter
        """
        if not self._enabled or not self._chat_ids:
            return False

        message = self._build_message(event_data)

        snapshot_path = event_data.metadata.get("snapshot_path") if event_data.metadata else None
        validated = self._validate_snapshot_path(snapshot_path)

        success = True

        for chat_id in self._chat_ids:
            if validated:
                ok = self._send_photo(chat_id, validated, message)
            else:
                ok = self._send_message(chat_id, message)

            success = success and ok

        if success:
            logger.info(
                f"[TELEGRAM] ✓ Notificación '{event_data.event_type}' "
                f"enviada a {len(self._chat_ids)} chat(s)"
                + (" (con foto)" if validated else " (solo texto)")
            )
        else:
            logger.error(
                f"[TELEGRAM] ✗ FALLÓ envío de '{event_data.event_type}'. "
                f"Revisa bot_token y conectividad a api.telegram.org"
            )

        return success

    def send_text(self, text: str) -> bool:
        """Envía mensaje simple a todos los chats"""
        if not self._enabled:
            return False

        return all(self._send_message(chat, text) for chat in self._chat_ids)

    # =========================================================================
    # MENSAJES
    # =========================================================================

    def _build_message(self, event_data: EventData) -> str:
        emoji = self.EMOJIS.get(event_data.event_type, "📋")
        name = self.TYPE_NAMES.get(event_data.event_type, event_data.event_type)

        dt = datetime.fromtimestamp(event_data.timestamp)
        time_str = dt.strftime("%d/%m/%Y %H:%M:%S")

        count = event_data.metadata.get("object_count", 1) if event_data.metadata else 1

        if count > 1:
            name = f"{count} {name}s"
        else:
            name = f"1 {name}"

        return "\n".join([
            f"{emoji} *Alerta de Videovigilancia*",
            "",
            f"📹 Cámara: `{event_data.camera_id}`",
            f"🎯 Detectado: {name}",
            f"📊 Confianza: {event_data.confidence:.0%}",
            f"🕐 Hora: {time_str}"
        ])

    # =========================================================================
    # SEGURIDAD
    # =========================================================================

    def _validate_snapshot_path(self, path: Optional[str]) -> Optional[str]:
        if not path:
            logger.debug("[TELEGRAM] snapshot_path es None/vacío — no envío foto")
            return None

        try:
            base = os.path.realpath(self._allowed_base_path)
            # Si path ya es absoluto, os.path.join(base, abs_path) = abs_path
            # Si path es relativo (p.ej. 'snapshots/5/123.jpg' SIN 'recordings/'),
            # se concatena correctamente.
            real = os.path.realpath(os.path.join(base, path))

            if not real.startswith(base):
                logger.warning(
                    f"[TELEGRAM] Path traversal bloqueado o fuera de base. "
                    f"base={base} real={real}"
                )
                return None

            if not os.path.isfile(real):
                logger.warning(
                    f"[TELEGRAM] snapshot NO existe en disco: {real} "
                    f"(path original={path})"
                )
                return None

            return real

        except Exception as e:
            logger.warning(f"[TELEGRAM] _validate_snapshot_path error: {e}")
            return None

    # =========================================================================
    # ENVÍO
    # =========================================================================

    def _send_message(self, chat_id: str, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"

        for i in range(3):
            try:
                r = requests.post(url, json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown"
                }, timeout=10)

                if r.status_code == 200:
                    return True
            except Exception as e:
                logger.error(f"Error Telegram: {e}")

            time.sleep(2 ** i)

        return False

    def _send_photo(self, chat_id: str, path: str, caption: str) -> bool:
        url = f"https://api.telegram.org/bot{self._bot_token}/sendPhoto"

        for i in range(3):
            try:
                with open(path, "rb") as f:
                    r = requests.post(
                        url,
                        data={"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"},
                        files={"photo": f},
                        timeout=30
                    )

                if r.status_code == 200:
                    return True
            except Exception as e:
                logger.error(f"Error foto Telegram: {e}")

            time.sleep(2 ** i)

        return False
    
    def send_video(self, chat_id: str, video_path: str, caption: str) -> bool:
        """Envía video MP4 a Telegram (soporta clips de 15 segundos)."""
        if not self._enabled or not os.path.exists(video_path):
            return False
            
        url = f"https://api.telegram.org/bot{self._bot_token}/sendVideo"
        
        for i in range(3):  # 3 intentos con backoff
            try:
                with open(video_path, "rb") as f:
                    files = {"video": f}
                    data = {
                        "chat_id": chat_id,
                        "caption": caption,
                        "parse_mode": "Markdown",
                        "supports_streaming": "true"  # Permite streaming progresivo
                    }
                    
                    r = requests.post(url, data=data, files=files, timeout=30)
                    
                    if r.status_code == 200:
                        logger.info(f"Video enviado exitosamente: {video_path}")
                        return True
                    else:
                        logger.warning(f"Telegram API error: {r.text}")
                        
            except Exception as e:
                logger.error(f"Error enviando video: {e}")
                
            time.sleep(2 ** i)
        
        return False

    def send_event_with_media(self, event_data: EventData) -> bool:
        """Envía foto + video secuencialmente (primero foto rápida, luego video)."""
        if not self._enabled or not self._chat_ids:
            return False
        
        # 1. Enviar foto inmediata (baja latencia)
        snapshot_path = event_data.metadata.get("snapshot_path") if event_data.metadata else None
        validated_photo = self._validate_snapshot_path(snapshot_path)
        
        message = self._build_message(event_data)
        
        for chat_id in self._chat_ids:
            if validated_photo:
                # Foto inmediata (< 2 segundos)
                self._send_photo(chat_id, validated_photo, message)
        
        # 2. Enviar video cuando esté listo (async, puede tardar 10-15s en procesarse)
        clip_path = event_data.metadata.get("clip_path") if event_data.metadata else None
        if clip_path and os.path.exists(clip_path):
            video_caption = f"🎥 Video del evento (15 segundos)\n{message}"
            for chat_id in self._chat_ids:
                self.send_video(chat_id, clip_path, video_caption)
        
        return True


telegram_notifier = TelegramNotifier(auto_subscribe=False)