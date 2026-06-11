"""
================================================================================
MÓDULO: notifications.telegram_notifier — Canal de salida a Telegram
================================================================================

PROPÓSITO
    Entrega alertas (texto + snapshot/video) a uno o varios chats de Telegram
    vía la Bot HTTP API (api.telegram.org). Es uno de los CONSUMIDORES finales
    del bus de eventos: convierte un EventData en un mensaje formateado y lo
    envía con reintentos y backoff.

RESPONSABILIDAD PRINCIPAL
    Formatear y enviar notificaciones a Telegram de forma resiliente y segura:
    multi-chat, reintentos con backoff exponencial, validación de la ruta del
    snapshot (anti path-traversal, debe quedar bajo RECORDINGS_PATH) y un pool
    HTTP dedicado para que las peticiones lentas NO bloqueen el resto del sistema.

    Modo híbrido:
      • PASIVO (recomendado, auto_subscribe=False): NotificationRouter decide a
        quién notificar (reglas/preferencias/cooldown) y llama a send_message /
        send_event_notification. Es la instancia global por defecto.
      • ACTIVO (auto_subscribe=True o suscripción manual en main): se suscribe a
        EventManager.subscribe_all y notifica TODO evento que pase sus filtros
        de SystemConfig (telegram_enabled, notify_<tipo>). main lo activa cuando
        hay credenciales en .env (ver _bootstrap_telegram_from_env).

DEPENDENCIAS
    requests ........................ HTTP a la Bot API
    database.connection.db_manager .. lee credenciales/filtros de SystemConfig
    database.models.SystemConfig .... almacén clave-valor de config
    events.event_manager ............ EventData (payload) y bus (modo activo)
    config.settings.RECORDINGS_PATH . base permitida para snapshots

COMPONENTES RELACIONADOS
    NotificationRouter ... llamador principal en modo pasivo (#13)
    EventManager ......... origen de los eventos en modo activo (#10)
    ws_broker ............ canal hermano (WebSocket) del mismo pipeline #13

PUNTO DE ENTRADA
    Singleton de módulo `telegram_notifier` (al final). Métodos públicos:
    send_message, send_event_notification, send_text, send_video,
    send_event_with_media, reload_config.

PIPELINE(S)
    Pipeline #13 (Notificaciones), etapa de ENTREGA final. En modo activo es
    consumidor directo del pipeline #10 (Eventos).
================================================================================
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
    """
    Notificador de Telegram (canal de entrega del pipeline #13).

    Rol: traduce EventData → mensaje Telegram y lo envía a los chat_ids
    configurados, con reintentos/backoff y validación de adjuntos. Thread-safe
    (lock sobre la config; pool HTTP dedicado para los envíos).

    Quién lo instancia/consume: se crea una instancia GLOBAL `telegram_notifier`
    al final del módulo, en modo PASIVO. La consume NotificationRouter (modo
    pasivo) y, si hay credenciales en .env, main lo suscribe a EventManager para
    el modo ACTIVO.

    NO es un singleton por __new__: es una instancia de módulo. Si se construyera
    otra con auto_subscribe=True habría doble suscripción al bus → usar la global.

    Dependencias: SystemConfig (credenciales/filtros), requests (HTTP),
    settings.RECORDINGS_PATH (validación de snapshots), event_manager (modo activo).

    Atributos de clase TYPE_NAMES/EMOJIS: traducción a español y emoji por tipo
    de evento, usados para construir el texto del mensaje.
    """

    TYPE_NAMES = {
        "person": "persona",
        "vehicle": "vehículo",
        "motion": "movimiento",
        "camera_offline": "cámara desconectada",
    }

    EMOJIS = {
        "person": "🚨",
        "vehicle": "🚗",
        "motion": "📹",
        "camera_offline": "⚠️",
    }

    def __init__(self, auto_subscribe: bool = False):
        """
        Construye el notificador y carga su config desde SystemConfig.

        Inputs:
            auto_subscribe: False → modo PASIVO (recomendado; lo invoca el
                NotificationRouter). True → modo ACTIVO (se suscribe a
                EventManager.subscribe_all y notifica todo evento que pase los
                filtros).
        Efectos: crea el pool HTTP dedicado (2 workers), fija la base permitida
            para snapshots (RECORDINGS_PATH) y carga token/chats/filtros de BD.
        """
        self._bot_token = ""
        self._enabled = False
        self._chat_ids: List[str] = []
        self._notify_types = set()
        self._lock = threading.Lock()

        # Anti-spam (cooldown) del modo ACTIVO: como mucho una notificación por
        # combinación (cámara, tipo de evento) cada N segundos. Sin esto, persona/
        # vehículo se enviaban al ritmo del scheduler (cada ~5s) → inundación.
        # Configurable vía TELEGRAM_NOTIFY_COOLDOWN (.env); por defecto 20s.
        try:
            from backend.app.config import settings as _settings
            self._cooldown_seconds = float(getattr(_settings, "TELEGRAM_NOTIFY_COOLDOWN", 60))
        except Exception:
            self._cooldown_seconds = 60.0
        self._cooldown_cache: Dict[str, float] = {}
        self._cooldown_lock = threading.Lock()

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
        """
        Lee credenciales y filtros de SystemConfig (clave-valor en BD).

        Carga: telegram_bot_token, telegram_enabled, telegram_chat_ids (CSV,
        con fallback a telegram_chat_id), y el conjunto de tipos habilitados a
        partir de las claves notify_<tipo>=true. Si falla, deja _enabled=False
        (fail-safe: no enviar) en vez de propagar.
        """
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
        """
        Recarga la config desde BD bajo lock (tras cambios en la app/escritorio).

        Llamado por: main._bootstrap_telegram_from_env() y las rutas de Telegram
        cuando el usuario edita token/chats/filtros. Thread-safe.
        """
        with self._lock:
            self._load_config()

    # =========================================================================
    # MODO ACTIVO (OPCIONAL)
    # =========================================================================

    def chat_wants_event(self, chat_id, event_type, camera_id) -> bool:
        """
        ¿El usuario DUEÑO de este chat de Telegram quiere este evento?

        Si el chat está VINCULADO a un usuario (UserTelegramChat), se respeta su
        NotificationPreference (tipo habilitado + cámara + horario + día). Así, al
        DESACTIVAR un tipo en el móvil, deja de llegar también por Telegram (antes
        la ruta activa usaba la config GLOBAL e ignoraba las preferencias → las
        alertas seguían llegando aunque el usuario las desactivara).

        Si el chat NO pertenece a ningún usuario (chat global del admin, sin
        vinculación), se permite por compatibilidad. Ante un fallo de BD se
        permite (fail-open) para no perder alertas por un error transitorio.
        """
        try:
            from backend.app.database.models import UserTelegramChat
            from backend.app.notifications.preference_eval import (
                wanted_channels, TELEGRAM_CHANNEL,
            )
            with db_manager.get_session() as session:
                link = session.query(UserTelegramChat).filter_by(
                    telegram_chat_id=str(chat_id)
                ).first()
                if link is None:
                    return True  # chat global sin dueño → compat

                # Fuente ÚNICA de verdad (compartida con router y ws_broker):
                # el chat recibe solo si el dueño tiene el canal 'telegram'
                # habilitado para este evento ahora.
                channels = wanted_channels(
                    session, link.user_id, event_type, camera_id
                )
                return TELEGRAM_CHANNEL in channels
        except Exception as e:
            logger.debug(f"chat_wants_event error: {e}")
            return True  # fail-open

    def _on_event(self, event_data: EventData):
        """
        Callback del bus en modo ACTIVO (consume pipeline #10 → #13).

        Propósito: filtrar el evento contra la config (habilitado, hay chats, el
        tipo está en notify_<tipo>) y, si pasa, ENCOLAR el envío en el pool HTTP
        dedicado para no bloquear el hilo del EventManager.

        Inputs:  event_data — evento publicado en el bus.
        Outputs: None (efecto: tarea encolada o evento descartado/logueado).
        Excepciones: captura RuntimeError si el pool ya está cerrado.
        Llamado por: EventManager (suscrito vía subscribe_all en main).
        Llama a: self._http_pool.submit(send_event_notification).
        """
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

        # Cooldown anti-spam por (cámara, tipo): evita inundar Telegram cuando un
        # evento se repite muy seguido (p.ej. persona/vehículo cada pocos seg).
        cd_key = f"{event_data.camera_id}:{event_data.event_type}"
        now = time.time()
        with self._cooldown_lock:
            last = self._cooldown_cache.get(cd_key, 0.0)
            if now - last < self._cooldown_seconds:
                logger.info(
                    f"[TELEGRAM] Evento '{event_data.event_type}' cam "
                    f"{event_data.camera_id} en cooldown ({self._cooldown_seconds:.0f}s) "
                    f"→ no reenviado"
                )
                return
            self._cooldown_cache[cd_key] = now

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

    def send_message(self, chat_id: str, text: str) -> bool:
        """
        Envía un texto a UN chat concreto (API pública del modo pasivo, #13).

        Propósito: el NotificationRouter, que ya decidió el destinatario, manda
        aquí el mensaje a un chat_id puntual (wrapper público de _send_message).

        Inputs:  chat_id (destino), text (Markdown).
        Outputs: True si Telegram aceptó (HTTP 200), False tras agotar reintentos.
        Llamado por: NotificationRouter.
        Llama a: self._send_message (reintentos + backoff).
        """
        return self._send_message(chat_id, text)

    def send_event_notification(self, event_data: EventData) -> bool:
        """
        Notifica un evento a TODOS los chat_ids configurados (#13, entrega).

        Propósito: punto de entrada principal del modo pasivo. Construye el
        mensaje, valida el snapshot y envía foto+caption (o sólo texto si no hay
        snapshot válido) a cada chat.

        Inputs:  event_data (incluye metadata["snapshot_path"] opcional).
        Outputs: True sólo si TODOS los envíos tuvieron éxito; False si falta
            config (deshabilitado / sin chats) o falló algún chat.
        Llamado por: NotificationRouter (modo pasivo) y _on_event (modo activo,
            vía el pool HTTP).
        Llama a: _build_message, _validate_snapshot_path, _send_photo/_send_message.
        """
        if not self._enabled or not self._chat_ids:
            return False

        message = self._build_message(event_data)

        snapshot_path = event_data.metadata.get("snapshot_path") if event_data.metadata else None
        validated = self._validate_snapshot_path(snapshot_path)

        # Respetar las preferencias del usuario dueño de cada chat: si desactivó
        # este tipo en el móvil, NO se le envía (los chats globales sin dueño sí).
        # Este es el método que usa la ruta ACTIVA (_on_event), así que aquí es
        # donde el "desactivar notificaciones" surte efecto para Telegram.
        allowed_chats = [
            c for c in self._chat_ids
            if self.chat_wants_event(c, event_data.event_type, event_data.camera_id)
        ]
        if not allowed_chats:
            logger.info(
                f"[TELEGRAM] Evento '{event_data.event_type}' cam "
                f"{event_data.camera_id}: ningún chat lo quiere (preferencias del "
                f"usuario) → no enviado"
            )
            return True

        success = True

        for chat_id in allowed_chats:
            if validated:
                ok = self._send_photo(chat_id, validated, message)
            else:
                ok = self._send_message(chat_id, message)

            success = success and ok

        if success:
            logger.info(
                f"[TELEGRAM] ✓ Notificación '{event_data.event_type}' "
                f"enviada a {len(allowed_chats)} chat(s)"
                + (" (con foto)" if validated else " (solo texto)")
            )
        else:
            logger.error(
                f"[TELEGRAM] ✗ FALLÓ envío de '{event_data.event_type}'. "
                f"Revisa bot_token y conectividad a api.telegram.org"
            )

        return success

    def send_text(self, text: str) -> bool:
        """
        Difunde un texto simple a TODOS los chats (utilidad, sin evento).

        Inputs: text (Markdown). Outputs: True si todos los chats lo aceptaron.
        Uso: mensajes de sistema/pruebas ("Telegram conectado correctamente").
        """
        if not self._enabled:
            return False

        return all(self._send_message(chat, text) for chat in self._chat_ids)

    # =========================================================================
    # MENSAJES
    # =========================================================================

    def _build_message(self, event_data: EventData) -> str:
        """
        Construye el texto Markdown de la alerta a partir del EventData.

        Traduce el tipo a español (TYPE_NAMES) + emoji, pluraliza según
        metadata["object_count"], y compone cámara/confianza/hora. Devuelve el
        string listo para sendMessage/sendPhoto.
        """
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
        """
        Valida que el snapshot quede DENTRO de RECORDINGS_PATH y exista (anti-traversal).

        Propósito de seguridad: el path llega en metadata y NO debe permitir leer
        archivos arbitrarios del disco (ej. '../../etc/passwd'). Resuelve la ruta
        real y exige que empiece por la base permitida y sea un fichero existente.

        Inputs:  path relativo o absoluto (o None).
        Outputs: ruta real validada (str) o None si es insegura/inexistente
            (en ese caso el llamador envía sólo texto, sin foto).
        """
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
        """
        POST sendMessage con 3 reintentos y backoff exponencial (1s, 2s, 4s).

        Inputs: chat_id, text (Markdown). Outputs: True en el primer HTTP 200,
        False si los 3 intentos fallan. Síncrono (corre en el pool HTTP dedicado).
        """
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
        """
        POST sendPhoto (snapshot + caption) con 3 reintentos y backoff.

        Inputs: chat_id, path (ya validado por _validate_snapshot_path), caption.
        Outputs: True en HTTP 200, False tras agotar intentos. timeout=30s (la
        subida del fichero es más lenta que un texto).
        """
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
                    logger.info(f"Foto enviada exitosamente: {path}")
                    return True
                # Telegram respondió pero NO 200 (p.ej. 400 por caption Markdown
                # mal formado o imagen inválida). Antes esto no se logueaba → la
                # foto "desaparecía" sin rastro. Ahora queda el motivo en el log.
                logger.warning(
                    f"Foto Telegram HTTP {r.status_code}: {r.text[:200]} (intento {i+1}/3)"
                )
            except Exception as e:
                logger.error(f"Error foto Telegram: {e}")

            time.sleep(2 ** i)

        return False
    
    def send_video(self, chat_id: str, video_path: str, caption: str) -> bool:
        """
        Envía un clip MP4 a un chat (clips de evento ~15s, pipeline #12→#13).

        Inputs:  chat_id, video_path (clip generado en pipeline #12), caption.
        Outputs: True en HTTP 200; False si deshabilitado, no existe el fichero
            o se agotan los 3 reintentos. supports_streaming=true permite que
            Telegram lo reproduzca progresivamente. timeout=30s.
        Llamado por: send_event_with_media (y rutas que adjuntan clips).
        """
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
        """
        Envía foto inmediata y, después, el clip del evento (baja latencia).

        Estrategia: primero la foto (snapshot, llega en <2s) para alertar ya;
        luego el video (metadata["clip_path"], puede tardar 10-15s en generarse
        en el pipeline #12). Así el usuario ve la alerta sin esperar al clip.

        Inputs:  event_data con metadata snapshot_path y/o clip_path.
        Outputs: True si arrancó el envío (no garantiza entrega de cada parte);
            False si deshabilitado o sin chats.
        Llama a: _validate_snapshot_path, _send_photo, send_video.
        """
        if not self._enabled or not self._chat_ids:
            return False
        
        # 1. Enviar foto inmediata (baja latencia)
        snapshot_path = event_data.metadata.get("snapshot_path") if event_data.metadata else None
        validated_photo = self._validate_snapshot_path(snapshot_path)
        
        message = self._build_message(event_data)
        
        # Respetar las preferencias del usuario dueño de cada chat: si desactivó
        # este tipo en el móvil, no se le envía (los chats globales sin dueño sí).
        et = event_data.event_type
        cid = event_data.camera_id
        allowed_chats = [c for c in self._chat_ids if self.chat_wants_event(c, et, cid)]

        for chat_id in allowed_chats:
            if validated_photo:
                # Foto inmediata (< 2 segundos)
                self._send_photo(chat_id, validated_photo, message)

        # 2. Enviar video cuando esté listo (async, puede tardar 10-15s en procesarse)
        clip_path = event_data.metadata.get("clip_path") if event_data.metadata else None
        if clip_path and os.path.exists(clip_path):
            video_caption = f"🎥 Video del evento\n{message}"
            for chat_id in allowed_chats:
                self.send_video(chat_id, clip_path, video_caption)
        
        return True


# Instancia global en modo PASIVO: la usa NotificationRouter. main la pasa a
# modo activo (subscribe_all) si hay credenciales en .env.
telegram_notifier = TelegramNotifier(auto_subscribe=False)