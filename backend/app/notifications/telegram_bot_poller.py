"""
================================================================================
MÓDULO: notifications.telegram_bot_poller — Long-poll del bot (vinculación)
================================================================================

PROPÓSITO
    Escucha los mensajes ENTRANTES del bot de Telegram (getUpdates long-poll) y
    procesa comandos de vinculación de chats con cuentas de usuario. Es el flujo
    INVERSO al de telegram_notifier (que sólo envía): aquí Telegram → backend.

RESPONSABILIDAD PRINCIPAL
    Detectar el comando /vincular CÓDIGO (y sus variantes) para asociar un
    chat_id de Telegram a un usuario (tabla UserTelegramChat), de modo que el
    NotificationRouter pueda luego enrutar notificaciones a ese chat. También
    gestiona /desvincular, /estado, /start (con deep-link) y /ayuda.

¿POR QUÉ POLLING Y NO WEBHOOK?
    El backend es LAN-only (no accesible desde internet), así que un webhook de
    Telegram no funcionaría. getUpdates long-poll sale desde cualquier red con
    conexión saliente.

COMANDOS SOPORTADOS
    /start [CÓDIGO]    → bienvenida; con código (deep-link t.me/<bot>?start=) vincula
    /vincular CÓDIGO   → vincula este chat con el usuario dueño del código
    CÓDIGO (suelto)    → atajo: si el mensaje es sólo el código, vincula igual
    /desvincular       → desactiva todos los UserTelegramChat de este chat_id
    /estado            → indica si el chat está vinculado y a qué cuentas
    /ayuda /help       → lista de comandos

DEPENDENCIAS
    requests ........................ HTTP a la Bot API (getMe/getUpdates/sendMessage)
    database.connection.db_manager .. lee token/offset, escribe vinculaciones
    database.models ................. SystemConfig (config/offset), UserTelegramChat
    services.telegram_link_service .. verifica el código y crea la vinculación

COMPONENTES RELACIONADOS
    telegram_link_service ... valida códigos generados por la app/escritorio
    telegram_notifier ....... canal de SALIDA (este módulo es el de ENTRADA)
    NotificationRouter ...... consume las vinculaciones que crea este poller

PUNTO DE ENTRADA
    Singleton global `telegram_bot_poller`. main.create_app() llama start() en
    el arranque; el finally del proceso llama stop().

PIPELINE(S)
    Soporte del pipeline #13 (Notificaciones): construye el destino (chat
    vinculado) que el router usa después. No emite eventos al bus #10.

PERSISTENCIA DEL OFFSET
    getUpdates devuelve un update_id incremental; tras un reinicio hay que
    recordar el último procesado para no reprocesar mensajes. Se guarda en
    SystemConfig key='telegram_last_update_id'.
================================================================================
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

import requests

from backend.app.database.connection import db_manager
from backend.app.database.models import SystemConfig, UserTelegramChat

logger = logging.getLogger(__name__)


_CONFIG_KEY_LAST_UPDATE = "telegram_last_update_id"
_CONFIG_KEY_BOT_USERNAME = "telegram_bot_username"

# Aceptamos código de 4-10 chars alfanuméricos en mayúscula
_CODE_RE = re.compile(r"^([A-Z0-9]{4,10})$")


class TelegramBotPoller:
    """
    Poller del bot de Telegram, SINGLETON thread-safe (soporte pipeline #13).

    Rol: corre un hilo daemon que hace long-poll de getUpdates, parsea comandos
    y crea/elimina vinculaciones chat↔usuario. Resiliente: si no hay red en el
    arranque, el loop igual arranca y reintenta identidad+updates al volver la
    conexión (self-healing con backoff agresivo).

    SINGLETON (__new__ + lock): un único hilo de polling por proceso (dos
    consumirían los mismos updates y duplicarían respuestas).

    Quién lo instancia/consume: instancia global `telegram_bot_poller`. main lo
    arranca (start) y lo detiene en el shutdown. Usa TelegramLinkService para
    verificar códigos.

    Dependencias: SystemConfig (token, offset, username del bot), UserTelegramChat
    (vinculaciones), requests (HTTP).
    """

    _instance = None
    _instance_lock = threading.Lock()
    POLL_TIMEOUT = 25  # segundos de long-poll; <30 para no chocar con timeouts HTTP

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
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._bot_token: str = ""
        self._bot_username: str = ""
        self._last_update_id: int = 0
        # Servicio de vinculación (lazy import para evitar ciclos)
        self._link_service = None

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """
        Lee config de Telegram desde BD; si hay bot_token, arranca el poller.
        Devuelve True si arrancó, False si no había token configurado.

        IMPORTANTE (resiliencia): el arranque del loop NO depende de que
        getMe responda. Si en el momento del boot no hay internet (o Telegram
        tarda), antes el poller quedaba MUERTO para siempre y el bot no
        procesaba ningún /vincular hasta reiniciar el backend. Ahora el loop
        arranca igual y reintenta identidad + getUpdates en cuanto vuelve la
        red (self-healing). Sólo se exige tener bot_token configurado.
        """
        self._load_config()
        if not self._bot_token:
            logger.info("[TelegramPoller] No hay bot_token en SystemConfig; poller no arranca")
            return False

        if self._thread and self._thread.is_alive():
            logger.warning("[TelegramPoller] Ya está corriendo")
            return True

        # Verificar token con /getMe para obtener el @username cuanto antes.
        # Si falla (sin red, timeout), NO abortamos: el loop reintentará.
        if not self._fetch_bot_identity():
            logger.warning(
                "[TelegramPoller] No pude verificar el bot ahora (¿sin red?). "
                "Arranco el loop igual y reintentaré automáticamente."
            )

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="TelegramBotPoller"
        )
        self._thread.start()
        logger.info(
            f"[TelegramPoller] Iniciado"
            + (f" para @{self._bot_username}" if self._bot_username else " (identidad pendiente)")
        )
        return True

    def stop(self) -> None:
        """
        Señala el fin del loop y espera al hilo (hasta 5s). Llamado en shutdown.
        """
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("[TelegramPoller] Detenido")

    def reload(self) -> None:
        """
        Reinicia el poller con la config nueva (p.ej. tras cambiar el bot_token).
        Equivale a stop() + start(). Llamado por las rutas de Telegram al editar
        las credenciales.
        """
        self.stop()
        self.start()

    def get_bot_username(self) -> str:
        """Devuelve el @username del bot (vacío si aún no se resolvió getMe)."""
        with self._lock:
            return self._bot_username

    def is_configured(self) -> bool:
        """True si hay bot_token en SystemConfig (haya o no arrancado el poller)."""
        self._load_config()
        return bool(self._bot_token)

    def is_running(self) -> bool:
        """True si el hilo de polling está vivo."""
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------
    def _load_config(self) -> None:
        try:
            with db_manager.get_session() as session:
                configs = {c.key: c.value for c in session.query(SystemConfig).all()}
            with self._lock:
                self._bot_token = configs.get("telegram_bot_token", "").strip()
                self._bot_username = configs.get(_CONFIG_KEY_BOT_USERNAME, "").strip()
                try:
                    self._last_update_id = int(configs.get(_CONFIG_KEY_LAST_UPDATE, "0"))
                except (TypeError, ValueError):
                    self._last_update_id = 0
        except Exception as e:
            logger.error(f"[TelegramPoller] Error leyendo config: {e}")

    def _save_offset(self, update_id: int) -> None:
        """Persiste el último update_id procesado en SystemConfig."""
        try:
            with db_manager.get_session() as session:
                config = session.query(SystemConfig).filter_by(
                    key=_CONFIG_KEY_LAST_UPDATE
                ).first()
                if config:
                    config.value = str(update_id)
                else:
                    session.add(SystemConfig(
                        key=_CONFIG_KEY_LAST_UPDATE,
                        value=str(update_id),
                    ))
                session.commit()
        except Exception as e:
            logger.warning(f"[TelegramPoller] No pude persistir offset: {e}")

    def _save_bot_username(self, username: str) -> None:
        try:
            with db_manager.get_session() as session:
                config = session.query(SystemConfig).filter_by(
                    key=_CONFIG_KEY_BOT_USERNAME
                ).first()
                if config:
                    config.value = username
                else:
                    session.add(SystemConfig(
                        key=_CONFIG_KEY_BOT_USERNAME,
                        value=username,
                    ))
                session.commit()
        except Exception as e:
            logger.warning(f"[TelegramPoller] No pude guardar bot_username: {e}")

    def _fetch_bot_identity(self) -> bool:
        """Llama getMe y guarda el username del bot."""
        try:
            url = f"https://api.telegram.org/bot{self._bot_token}/getMe"
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                logger.error(f"[TelegramPoller] getMe HTTP {r.status_code}: {r.text[:200]}")
                return False
            data = r.json()
            if not data.get("ok"):
                logger.error(f"[TelegramPoller] getMe error: {data}")
                return False
            username = data["result"].get("username", "")
            with self._lock:
                self._bot_username = username
            self._save_bot_username(username)
            return True
        except Exception as e:
            logger.error(f"[TelegramPoller] Error consultando identidad del bot: {e}")
            return False

    # ------------------------------------------------------------------
    # Loop principal
    # ------------------------------------------------------------------
    def _poll_loop(self) -> None:
        """
        Bucle principal del hilo: long-poll de updates con backoff adaptativo.

        Mientras no se pida parar: resuelve identidad pendiente, pide updates y
        procesa cada uno. Ante fallos de red sostenidos sube el backoff hasta 5
        min (no spamear logs) y lo resetea a 1s en cuanto la red vuelve. Aísla
        cualquier excepción para que el hilo no muera. Llama a _get_updates y
        _process_update.
        """
        backoff = 1
        offline_streak = 0  # contador consecutivo de errores de red
        while not self._stop_event.is_set():
            try:
                # Si la identidad quedó pendiente (getMe falló en el arranque
                # por falta de red), reintentar aquí en cuanto haya conexión.
                if not self._bot_username:
                    self._fetch_bot_identity()

                updates, offline_err = self._get_updates()
                if updates is None:
                    if offline_err:
                        offline_streak += 1
                        # Si la red está caída sostenidamente, sube backoff a
                        # 5 min para no spamear logs cada 16s. Vuelve a 1s en
                        # cuanto la red regrese.
                        if offline_streak >= 3:
                            backoff = min(max(backoff * 2, 60), 300)
                        else:
                            backoff = min(backoff * 2, 30)
                    else:
                        # Error HTTP no relacionado con conectividad: backoff corto
                        offline_streak = 0
                        backoff = min(backoff * 2, 60)
                    if self._stop_event.wait(timeout=backoff):
                        break
                    continue
                # Petición OK → reset
                if offline_streak >= 3:
                    logger.info("[TelegramPoller] Red restablecida, reanudando polling")
                offline_streak = 0
                backoff = 1

                for update in updates:
                    if self._stop_event.is_set():
                        break
                    self._process_update(update)

            except Exception as e:
                logger.error(f"[TelegramPoller] Error en loop: {e}", exc_info=True)
                if self._stop_event.wait(timeout=5):
                    break

    def _get_updates(self):
        """
        Long-polling de getUpdates.

        Returns:
            (updates, offline_err): updates es la lista (o None si hubo error).
            offline_err = True si el error fue de conectividad (DNS, socket reset);
            en ese caso el caller debe subir el backoff agresivamente para no
            spamear logs cuando la red está caída.
        """
        try:
            url = f"https://api.telegram.org/bot{self._bot_token}/getUpdates"
            params = {
                "timeout": self.POLL_TIMEOUT,
                "offset": self._last_update_id + 1 if self._last_update_id else None,
                "allowed_updates": ["message"],
            }
            if params["offset"] is None:
                params.pop("offset")

            r = requests.get(url, params=params, timeout=self.POLL_TIMEOUT + 5)
            if r.status_code != 200:
                logger.warning(f"[TelegramPoller] getUpdates HTTP {r.status_code}")
                return None, False
            data = r.json()
            if not data.get("ok"):
                logger.warning(f"[TelegramPoller] getUpdates not ok: {data}")
                return None, False
            return data.get("result", []), False
        except requests.exceptions.Timeout:
            # Long-poll timeout es normal; volver a pedir
            return [], False
        except (requests.exceptions.ConnectionError,
                requests.exceptions.RetryError) as e:
            # DNS / socket reset / sin internet → log a INFO (no ERROR) y
            # avisar al caller para que aplique backoff agresivo.
            msg = str(e)
            # Solo loggear los 2 primeros y luego silenciar hasta que vuelva
            # la red — el contador está en el caller.
            logger.info(
                f"[TelegramPoller] Sin red para contactar Telegram "
                f"({msg.split(':')[0][:80]}…)"
            )
            return None, True
        except Exception as e:
            logger.error(f"[TelegramPoller] getUpdates error: {e}")
            return None, False

    # ------------------------------------------------------------------
    # Procesado de mensajes
    # ------------------------------------------------------------------
    def _process_update(self, update: dict) -> None:
        """
        Procesa UN update: extrae chat/texto, despacha el comando y avanza offset.

        Inputs:  update (dict de la Bot API). Outputs: None.
        El offset se persiste DESPUÉS de procesar (en _advance_offset) para no
        perder mensajes si crashea a mitad de batch. Llama a _dispatch_command.
        """
        update_id = int(update.get("update_id", 0))
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (message.get("text") or "").strip()
        from_user = message.get("from") or {}
        username = from_user.get("username") or from_user.get("first_name") or "usuario"

        if not chat_id:
            self._advance_offset(update_id)
            return

        logger.info(f"[TelegramPoller] msg de @{username} ({chat_id}): {text!r}")

        try:
            self._dispatch_command(chat_id, username, text)
        except Exception as e:
            logger.error(f"[TelegramPoller] Error procesando mensaje: {e}", exc_info=True)
            self._reply(chat_id, "❌ Ocurrió un error procesando tu mensaje.")

        # Persistimos el offset DESPUÉS de procesar para no perder mensajes
        # si crashea a mitad de batch.
        self._advance_offset(update_id)

    def _advance_offset(self, update_id: int) -> None:
        with self._lock:
            if update_id > self._last_update_id:
                self._last_update_id = update_id
        self._save_offset(update_id)

    def _dispatch_command(self, chat_id: str, username: str, text: str) -> None:
        """
        Enruta el texto del mensaje al comando correspondiente (/start, /vincular,
        /desvincular, /estado, /ayuda) o al atajo de CÓDIGO suelto.

        Inputs:  chat_id, username (para personalizar), text del mensaje.
        Outputs: None (efecto: responde por Telegram vía los _cmd_*).
        Por defecto (texto no reconocido) muestra la ayuda.
        """
        if not text:
            return

        if text.startswith("/start"):
            # Deep link de Telegram: t.me/<bot>?start=CODIGO llega como
            # "/start CODIGO" → vincula directamente (un toque desde la app).
            # Sin parámetro (o si no parece código) → bienvenida normal.
            parts = text.split(maxsplit=1)
            if len(parts) >= 2:
                arg = parts[1].strip().upper()
                if _CODE_RE.match(arg):
                    self._cmd_verify(chat_id, username, arg)
                    return
            self._cmd_start(chat_id, username)
            return

        if text.lower().startswith("/desvincular"):
            self._cmd_unlink(chat_id)
            return

        if text.lower().startswith("/estado"):
            self._cmd_status(chat_id)
            return

        if text.lower().startswith("/help") or text.lower().startswith("/ayuda"):
            self._cmd_help(chat_id)
            return

        # /vincular CODIGO  o  CODIGO solo
        code = None
        if text.lower().startswith("/vincular"):
            parts = text.split(maxsplit=1)
            if len(parts) >= 2:
                code = parts[1].strip().upper()
        else:
            m = _CODE_RE.match(text.upper())
            if m:
                code = m.group(1)

        if code:
            self._cmd_verify(chat_id, username, code)
            return

        # Default: ayuda
        self._cmd_help(chat_id)

    # ---- Comandos individuales ---------------------------------------

    def _cmd_start(self, chat_id: str, username: str) -> None:
        self._reply(chat_id,
            f"👋 ¡Hola @{username}!\n\n"
            "Soy el bot del NVR. Para recibir notificaciones de tus cámaras "
            "necesitas vincular este chat con tu cuenta.\n\n"
            "1️⃣ Abre la app del NVR en tu PC.\n"
            "2️⃣ Ve a *Notificaciones → Telegram → Vincular*.\n"
            "3️⃣ Copia el código de 6 caracteres que te muestra.\n"
            "4️⃣ Vuelve aquí y envíame: `/vincular ABC123`\n\n"
            "También puedes enviar sólo el código y lo detectaré.\n\n"
            "Comandos:\n"
            "• `/vincular CÓDIGO` — vincula este chat\n"
            "• `/estado` — muestra si estás vinculado\n"
            "• `/desvincular` — desactiva las notificaciones aquí\n"
            "• `/ayuda` — esta lista"
        )

    def _cmd_help(self, chat_id: str) -> None:
        self._reply(chat_id,
            "ℹ️ *Comandos del bot NVR*\n\n"
            "• `/vincular CÓDIGO` — vincula este chat con tu cuenta\n"
            "• `/estado` — muestra el estado de vinculación\n"
            "• `/desvincular` — deja de recibir notificaciones aquí\n"
            "• `/ayuda` — esta ayuda\n\n"
            "Para obtener un código, abre la app NVR en tu PC."
        )

    def _cmd_verify(self, chat_id: str, username: str, code: str) -> None:
        """
        Verifica un código y vincula el chat con la cuenta (núcleo de /vincular).

        Propósito (etapa clave del soporte #13): delega en TelegramLinkService la
        validación del código (caduca a los 5 min) y la creación de la fila
        UserTelegramChat. Responde éxito o error al usuario.

        Inputs:  chat_id, username, code (4-10 alfanum. en mayúscula).
        Outputs: None (responde por Telegram).
        Llama a: TelegramLinkService.verify_code, self._reply.
        """
        link = self._get_link_service()
        try:
            ok = link.verify_code(
                code=code,
                telegram_chat_id=chat_id,
                telegram_username=username,
            )
        except Exception as e:
            logger.error(f"[TelegramPoller] Error verificando {code}: {e}")
            ok = False

        if ok:
            self._reply(chat_id,
                f"✅ *¡Vinculado correctamente!*\n\n"
                f"A partir de ahora recibirás aquí las notificaciones "
                f"de tus cámaras según tus preferencias.\n\n"
                f"Para dejar de recibirlas envía /desvincular."
            )
        else:
            self._reply(chat_id,
                "❌ Código inválido o expirado.\n\n"
                "Genera uno nuevo en la app del NVR y vuelve a intentarlo. "
                "Los códigos duran 5 minutos."
            )

    def _cmd_unlink(self, chat_id: str) -> None:
        # Desactiva TODOS los UserTelegramChat con este chat_id
        try:
            with db_manager.get_session() as session:
                rows = session.query(UserTelegramChat).filter_by(
                    telegram_chat_id=chat_id,
                    is_active=True,
                ).all()
                if not rows:
                    self._reply(chat_id,
                        "ℹ️ Este chat no está vinculado a ninguna cuenta."
                    )
                    return
                for r in rows:
                    r.is_active = False
                session.commit()
            self._reply(chat_id,
                "✅ Desvinculado. No recibirás más notificaciones aquí.\n\n"
                "Para volver a vincular envía /vincular CÓDIGO."
            )
        except Exception as e:
            logger.error(f"[TelegramPoller] Error desvinculando {chat_id}: {e}")
            self._reply(chat_id, "❌ No pude desvincular el chat. Intenta de nuevo.")

    def _cmd_status(self, chat_id: str) -> None:
        try:
            with db_manager.get_session() as session:
                rows = session.query(UserTelegramChat).filter_by(
                    telegram_chat_id=chat_id,
                    is_active=True,
                ).all()
                if not rows:
                    self._reply(chat_id,
                        "ℹ️ Este chat *no está vinculado*.\n\n"
                        "Envía /vincular CÓDIGO para vincularlo."
                    )
                    return
                lines = [f"✅ Este chat está vinculado a {len(rows)} cuenta(s):"]
                for r in rows:
                    lines.append(f"  • Usuario ID {r.user_id} — desde {r.linked_at:%Y-%m-%d %H:%M}")
                self._reply(chat_id, "\n".join(lines))
        except Exception as e:
            logger.error(f"[TelegramPoller] Error estado {chat_id}: {e}")
            self._reply(chat_id, "❌ Error consultando estado.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_link_service(self):
        if self._link_service is None:
            from backend.app.services.telegram_link_service import TelegramLinkService
            self._link_service = TelegramLinkService()
        return self._link_service

    def _reply(self, chat_id: str, text: str) -> bool:
        """
        Responde un texto al chat (sendMessage). Si Markdown falla por caracteres
        especiales, reintenta sin parse_mode. Usado por todos los _cmd_*.
        """
        try:
            url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
            r = requests.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }, timeout=10)
            if r.status_code != 200:
                # Markdown puede fallar si hay caracteres especiales — reintenta sin parse_mode
                requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
            return True
        except Exception as e:
            logger.error(f"[TelegramPoller] No pude responder a {chat_id}: {e}")
            return False


# Instancia global (singleton): único poller del bot por proceso.
telegram_bot_poller = TelegramBotPoller()
