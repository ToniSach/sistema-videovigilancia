"""
================================================================================
MÓDULO: api.routes.telegram_link — Vinculación de cuentas de Telegram (capa HTTP)
================================================================================

PROPÓSITO
    Blueprint REST que permite a un usuario vincular su cuenta con un chat de
    Telegram (canal de notificación), configurar el bot del servidor (admin),
    listar/desvincular chats, probar el envío y dar un resumen administrativo.

FLUJO DE VINCULACIÓN (handshake con polling)
    1. Frontend → POST /generate-code → recibe {code, bot_username, deep_link...}
    2. Frontend hace polling cada 2s: GET /link-status?code=XXX
    3. Usuario abre Telegram, busca @bot_username y envía /vincular XXX
    4. TelegramBotPoller (corriendo en backend) detecta el comando, llama
       link_service.verify_code() y crea UserTelegramChat.
    5. Frontend ve linked=true en /link-status y cierra el diálogo.
    (El endpoint público /verify existe para que el propio bot confirme el
    código; normalmente lo invoca el poller, no el frontend.)

RESPONSABILIDAD
    Contrato HTTP + orquestación del handshake + gating admin para configurar el
    bot. La lógica de códigos/vinculación vive en TelegramLinkService; el envío
    real y el ciclo de vida del bot en telegram_bot_poller / telegram_notifier.

DEPENDENCIAS
    services.telegram_link_service.TelegramLinkService  códigos + vínculos
    notifications.telegram_bot_poller .... estado/arranque del bot, @username
    notifications.telegram_notifier ...... envío de mensajes (/test)
    database.connection.db_manager ....... lee TelegramVerificationCode/UserTelegramChat
    services.user_service.UserService .... gating admin (/configure, /admin/overview)

COMPONENTES RELACIONADOS
    routes/notifications.py  preferencias que deciden CUÁNDO notificar por Telegram
    NotificationRouter ..... enruta eventos a estos chats
    database.models.TelegramVerificationCode, UserTelegramChat, SystemConfig

PUNTO DE ENTRADA
    Registrado en main.create_app() vía safe_register(telegram_link_bp).
    Prefijo: /api/v1/telegram. Best-effort.

PIPELINE(S)
    Pipeline #13 (Notificaciones) — etapa de ALTA/gestión del canal Telegram
    (define destinos); el disparo real lo ejecutan EventManager + notifiers.

ENDPOINTS
    POST   /generate-code        → generate_code()    [auth] genera código + arranca poller
    GET    /link-status          → link_status()      [auth] polling del estado del código
    GET    /bot-info             → bot_info()         [auth] estado del bot del servidor
    POST   /configure            → configure_bot()    [admin] set token + reload en caliente
    POST   /verify               → verify_code()      [público/bot] confirma vinculación
    GET    /chats                → get_chats()        [auth] chats vinculados del usuario
    POST   /test                 → test_my_telegram() [auth] envío de prueba
    GET    /admin/overview       → admin_overview()   [admin] resumen usuarios↔Telegram
    DELETE /chats/<chat_id>      → unlink_chat()      [auth] desvincula un chat

NOTA: /configure referencia SystemConfig (modelo ORM) — verificar que esté
    importado al editar este archivo.
================================================================================
"""
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt

from backend.app.services.telegram_link_service import TelegramLinkService
from backend.app.database.connection import db_manager
from backend.app.database.models import TelegramVerificationCode, UserTelegramChat

telegram_link_bp = Blueprint("telegram_link", __name__, url_prefix="/api/v1/telegram")
link_service = TelegramLinkService()


def _device_id_from_jwt():
    """device_id del JWT (Telegram es POR DISPOSITIVO). El token móvil lo lleva;
    el de escritorio no → None (alcance "de cuenta")."""
    dev = get_jwt().get("device_id")
    return int(dev) if dev is not None else None


@telegram_link_bp.route("/generate-code", methods=["POST"])
@jwt_required()
def generate_code():
    """
    Genera un código de vinculación para el usuario autenticado.

    Método+Ruta: POST /api/v1/telegram/generate-code
    Permiso: JWT válido. Pipeline #13, etapa alta de canal Telegram.
    Efecto colateral: GARANTIZA que el poller esté vivo (lo (re)arranca si
        está configurado pero caído) — sin esto el bot no procesa el /vincular.
    Inputs: ninguno (user_id del token).
    Outputs:
        200 {success:true, data:{code, bot_username, bot_configured,
             expires_in_seconds, telegram_deep_link, instructions[]}, code}
             (campo top-level `code` duplicado por compat con versión anterior).
        400 {success:false, error} ante ValueError del servicio.
        500 ante fallo interno.
    Llama a: TelegramLinkService.generate_code() + telegram_bot_poller.
    """
    try:
        user_id = int(get_jwt_identity())
        code = link_service.generate_code(user_id, _device_id_from_jwt())

        # Datos auxiliares para que el frontend muestre instrucciones completas
        from backend.app.notifications.telegram_bot_poller import telegram_bot_poller
        # GARANTÍA: el poller debe estar vivo SIEMPRE que alguien intenta
        # vincular. Si murió (p.ej. getMe falló sin red al boot) o nunca
        # arrancó, lo (re)arrancamos aquí. Sin esto, el usuario generaba
        # código pero el bot no procesaba el /vincular → ni confirmaba ni el
        # móvil detectaba la vinculación.
        if telegram_bot_poller.is_configured() and not telegram_bot_poller.is_running():
            telegram_bot_poller.start()
        bot_username = telegram_bot_poller.get_bot_username()
        if not bot_username:
            telegram_bot_poller.start()
            bot_username = telegram_bot_poller.get_bot_username()

        expires_in = link_service.CODE_EXPIRY_MINUTES * 60

        return jsonify({
            "success": True,
            "data": {
                "code": code,
                "bot_username": bot_username or "",
                "bot_configured": telegram_bot_poller.is_configured(),
                "expires_in_seconds": expires_in,
                # Deep link MANUAL: abre el bot SIN el código (sin ?start=CÓDIGO),
                # para que el usuario envíe él mismo "/vincular CÓDIGO". Antes el
                # ?start=CÓDIGO auto-enviaba el código y vinculaba de un toque
                # "sin pedirlo"; ahora el paso es explícito.
                "telegram_deep_link": (
                    f"https://t.me/{bot_username}" if bot_username else None
                ),
                "instructions": [
                    f"Abre Telegram y busca el bot @{bot_username}" if bot_username
                    else "El bot de Telegram no está configurado en el servidor",
                    f"Escríbele (o pega) el mensaje: /vincular {code}",
                    "Recibirás confirmación tanto en el bot como en esta ventana",
                ],
            },
            "code": code,  # compat con versión anterior
        }), 200
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@telegram_link_bp.route("/link-status", methods=["GET"])
@jwt_required()
def link_status():
    """
    Indica si un código ya fue consumido (vinculado) por el bot. Polling.

    Método+Ruta: GET /api/v1/telegram/link-status
    Permiso: JWT válido (solo consulta códigos del propio usuario).
    Inputs: Query `code` (str, REQUERIDO; se normaliza a mayúsculas).
    Outputs:
        200 {success:true, data:{code, linked, expired, expires_at, chat|null}}
            El frontend hace polling cada 2s hasta linked=true o expired=true.
        400 {success:false, error} si falta `code`.
        404 {success:false, error} si el código no existe para ese usuario.
        500 ante fallo interno.
    Llama a: lectura directa de TelegramVerificationCode/UserTelegramChat (BD).
    """
    try:
        user_id = int(get_jwt_identity())
        code = request.args.get("code", "").strip().upper()
        if not code:
            return jsonify({"success": False, "error": "Falta parámetro code"}), 400

        with db_manager.get_session() as session:
            verif = session.query(TelegramVerificationCode).filter_by(
                code=code, user_id=user_id
            ).first()
            if not verif:
                return jsonify({"success": False, "error": "Código no encontrado"}), 404

            expired = verif.expires_at < datetime.utcnow()
            linked = bool(verif.used)

            chat_info = None
            if linked:
                # Chat más reciente del MISMO alcance del código (user+device);
                # Telegram es por dispositivo, así que acotamos a verif.device_id.
                chat = (
                    session.query(UserTelegramChat)
                    .filter_by(user_id=user_id, device_id=verif.device_id, is_active=True)
                    .order_by(UserTelegramChat.linked_at.desc())
                    .first()
                )
                if chat:
                    chat_info = {
                        "id": chat.id,
                        "telegram_username": chat.telegram_username,
                        "linked_at": chat.linked_at.isoformat(),
                    }

            return jsonify({
                "success": True,
                "data": {
                    "code": code,
                    "linked": linked,
                    "expired": expired and not linked,
                    "expires_at": verif.expires_at.isoformat(),
                    "chat": chat_info,
                }
            }), 200
    except Exception:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@telegram_link_bp.route("/bot-info", methods=["GET"])
@jwt_required()
def bot_info():
    """
    Estado del bot del servidor (para que el frontend guíe al usuario).

    Método+Ruta: GET /api/v1/telegram/bot-info
    Permiso: JWT válido.
    Inputs: ninguno.
    Outputs:
        200 {success:true, data:{configured, running, username, deep_link_base}}
        Si configured=false, el frontend explica cómo crearlo con @BotFather.
    Llama a: telegram_bot_poller (is_configured/is_running/get_bot_username).
    """
    from backend.app.notifications.telegram_bot_poller import telegram_bot_poller
    return jsonify({
        "success": True,
        "data": {
            "configured": telegram_bot_poller.is_configured(),
            "running": telegram_bot_poller.is_running(),
            "username": telegram_bot_poller.get_bot_username(),
            "deep_link_base": (
                f"https://t.me/{telegram_bot_poller.get_bot_username()}"
                if telegram_bot_poller.get_bot_username() else None
            ),
        }
    }), 200


@telegram_link_bp.route("/configure", methods=["POST"])
@jwt_required()
def configure_bot():
    """
    Configura el bot de Telegram del servidor (SOLO admin) y RECARGA el poller
    en caliente, sin reiniciar el backend.

    PIPELINE:
      Paso 1. Verificar que el solicitante es admin.
      Paso 2. Guardar el token en SystemConfig['telegram_bot_token'] (+ habilitar).
      Paso 3. Recargar el TelegramBotPoller para que lea el nuevo token y arranque.
      Paso 4. Devolver el estado del bot (configurado / corriendo / @username) para
              que el desktop confirme inmediatamente si el token es válido.

    Body: { "bot_token": "123456:ABC-DEF..." }
    El token NUNCA se devuelve en la respuesta (es un secreto).
    """
    try:
        user_id = int(get_jwt_identity())
        from backend.app.services.user_service import UserService
        if not UserService().is_admin(user_id):
            return jsonify({"success": False, "error": "Se requiere rol admin"}), 403

        data = request.get_json(silent=True) or {}
        token = (data.get("bot_token") or "").strip()
        if not token or ":" not in token:
            return jsonify({
                "success": False,
                "error": "Token inválido. Pega el token completo que te dio @BotFather "
                         "(formato 123456789:AA...).",
            }), 400

        # Paso 2: persistir token + habilitar Telegram.
        with db_manager.get_session() as session:
            session.merge(SystemConfig(key="telegram_bot_token", value=token))
            session.merge(SystemConfig(key="telegram_enabled", value="true"))
            session.commit()

        # Paso 3: recargar el poller en caliente.
        from backend.app.notifications.telegram_bot_poller import telegram_bot_poller
        telegram_bot_poller.reload()

        # Paso 4: estado para feedback inmediato (username presente = token válido).
        username = telegram_bot_poller.get_bot_username()
        return jsonify({
            "success": True,
            "data": {
                "configured": telegram_bot_poller.is_configured(),
                "running": telegram_bot_poller.is_running(),
                "username": username,
                "valid": bool(username),
                "deep_link_base": f"https://t.me/{username}" if username else None,
            },
        }), 200
    except Exception:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@telegram_link_bp.route("/verify", methods=["POST"])
def verify_code():
    """
    Verifica un código y crea el vínculo. Endpoint PÚBLICO (lo llama el bot).

    Método+Ruta: POST /api/v1/telegram/verify
    Permiso: PÚBLICO (sin JWT). Lo invoca el TelegramBotPoller al recibir
        /vincular CODE; la "autorización" es el propio código de un solo uso.
    Inputs (body JSON):
        code (str, REQUERIDO), chat_id (str, REQUERIDO; chat de Telegram),
        username (str, opcional).
    Outputs:
        200 {success:true, message} si el código era válido → crea UserTelegramChat.
        400 {success:false, error} si faltan campos o código inválido/expirado.
        500 ante fallo interno.
    Llama a: TelegramLinkService.verify_code(code, chat_id, username).
    """
    try:
        data = request.get_json()
        if not data or "code" not in data or "chat_id" not in data:
            return jsonify({"success": False, "error": "code y chat_id requeridos"}), 400
        
        success = link_service.verify_code(
            code=data["code"],
            telegram_chat_id=data["chat_id"],
            telegram_username=data.get("username")
        )
        if success:
            return jsonify({"success": True, "message": "Cuenta vinculada correctamente"}), 200
        else:
            return jsonify({"success": False, "error": "Código inválido o expirado"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@telegram_link_bp.route("/chats", methods=["GET"])
@jwt_required()
def get_chats():
    """
    Lista los chats de Telegram vinculados al usuario autenticado.

    Método+Ruta: GET /api/v1/telegram/chats
    Permiso: JWT válido.
    Inputs: ninguno.
    Outputs:
        200 {success:true, data:[chat...]}  (no expone los telegram_chat_id crudos)
        500 ante fallo interno.
    Llama a: TelegramLinkService.get_user_chats(user_id).
    """
    try:
        user_id = int(get_jwt_identity())
        # Solo los chats del alcance de ESTE cliente (Telegram es por dispositivo).
        chats = link_service.get_user_chats(
            user_id, _device_id_from_jwt(), _scope_device=True
        )
        return jsonify({"success": True, "data": chats}), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@telegram_link_bp.route("/test", methods=["POST"])
@jwt_required()
def test_my_telegram():
    """
    Envía un mensaje de prueba a los chats de Telegram vinculados al usuario
    actual. Verifica de extremo a extremo que las notificaciones llegan, sin
    disparar un evento real ni grabar vídeo.
    """
    try:
        user_id = int(get_jwt_identity())
        device_id = _device_id_from_jwt()
        # Prueba acotada al alcance del cliente (Telegram es por dispositivo).
        chats = link_service.get_user_chats(user_id, device_id, _scope_device=True)
        if not chats:
            return jsonify({
                "success": False,
                "error": "No tienes ningún chat de Telegram vinculado.",
            }), 400
        # Necesitamos los chat_id reales (get_user_chats no los expone); leerlos.
        from backend.app.notifications.telegram_notifier import telegram_notifier
        sent = 0
        with db_manager.get_session() as session:
            rows = session.query(UserTelegramChat).filter_by(
                user_id=user_id, device_id=device_id, is_active=True
            ).all()
            chat_ids = [r.telegram_chat_id for r in rows]
        for cid in chat_ids:
            try:
                if telegram_notifier.send_message(
                    cid, "✅ Mensaje de prueba del sistema de videovigilancia. "
                         "Si ves esto, tus notificaciones funcionan."
                ):
                    sent += 1
            except Exception:
                pass
        if sent > 0:
            return jsonify({"success": True, "data": {"sent": sent}}), 200
        return jsonify({
            "success": False,
            "error": "No se pudo enviar (¿bot configurado en el servidor?).",
        }), 502
    except Exception:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@telegram_link_bp.route("/admin/overview", methods=["GET"])
@jwt_required()
def admin_overview():
    """
    Resumen para el ADMIN: por cada usuario, su(s) chat(s) de Telegram vinculados
    y cuántas preferencias de notificación tiene activas. Alimenta la tabla
    "Usuarios y Telegram" del panel de notificaciones (vista comercial, sin
    exponer tokens ni pedir chat_id a mano).
    """
    from backend.app.services.user_service import UserService
    try:
        user_id = int(get_jwt_identity())
        if not UserService().is_admin(user_id):
            return jsonify({"success": False, "error": "Solo administradores"}), 403

        from backend.app.database.models import (
            User, UserTelegramChat, NotificationPreference, MobileDevice,
        )
        # Etiquetas legibles de tipo de evento para el resumen de preferencias.
        _EV = {
            "person": "Persona", "vehicle": "Vehículo", "motion": "Movimiento",
            "camera_offline": "Cámara offline",
        }
        rows = []
        with db_manager.get_session() as session:
            users = session.query(User).all()
            for u in users:
                chats = session.query(UserTelegramChat).filter_by(
                    user_id=u.id, is_active=True
                ).all()
                prefs = session.query(NotificationPreference).filter_by(
                    user_id=u.id, enabled=True
                ).all()
                devices = session.query(MobileDevice).filter_by(
                    user_id=u.id, is_active=True
                ).all()
                # Resumen compacto de tipos de evento que el usuario quiere recibir.
                ev_types = sorted({p.event_type for p in prefs})
                pref_summary = ", ".join(_EV.get(e, e) for e in ev_types) if ev_types else "—"
                rows.append({
                    "user_id": u.id,
                    "username": u.username,
                    "role": getattr(u, "role", "user"),
                    "telegram": [
                        {
                            "id": c.id,
                            "username": c.telegram_username,
                            "linked_at": c.linked_at.isoformat() if c.linked_at else None,
                        }
                        for c in chats
                    ],
                    "telegram_linked": len(chats) > 0,
                    "mobile_devices": [d.device_name for d in devices],
                    "mobile_count": len(devices),
                    "active_preferences": len(prefs),
                    "preferences_summary": pref_summary,
                })
        return jsonify({"success": True, "data": rows}), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@telegram_link_bp.route("/chats/<int:chat_id>", methods=["DELETE"])
@jwt_required()
def unlink_chat(chat_id):
    """Desvincula un chat específico."""
    try:
        user_id = int(get_jwt_identity())
        success = link_service.unlink_telegram(user_id, chat_id)
        if success:
            return jsonify({"success": True}), 200
        return jsonify({"success": False, "error": "No encontrado"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500