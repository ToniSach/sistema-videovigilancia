"""
Endpoints para que los usuarios vinculen su cuenta de Telegram.

Flujo:
  1. Frontend → POST /generate-code → recibe {code, expires_at, bot_username}
  2. Frontend hace polling cada 2s: GET /link-status?code=XXX
  3. Usuario abre Telegram, busca @bot_username, envía /vincular XXX
  4. TelegramBotPoller (corriendo en backend) detecta el comando, llama
     link_service.verify_code(), crea UserTelegramChat
  5. Frontend ve linked=true en /link-status y cierra el diálogo
"""
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from backend.app.services.telegram_link_service import TelegramLinkService
from backend.app.database.connection import db_manager
from backend.app.database.models import TelegramVerificationCode, UserTelegramChat

telegram_link_bp = Blueprint("telegram_link", __name__, url_prefix="/api/v1/telegram")
link_service = TelegramLinkService()


@telegram_link_bp.route("/generate-code", methods=["POST"])
@jwt_required()
def generate_code():
    """
    Genera código de vinculación para el usuario autenticado.
    Devuelve también bot_username y expires_in_seconds para que el frontend
    pueda mostrar todo lo necesario sin más llamadas.
    """
    try:
        user_id = int(get_jwt_identity())
        code = link_service.generate_code(user_id)

        # Datos auxiliares para que el frontend muestre instrucciones completas
        from backend.app.notifications.telegram_bot_poller import telegram_bot_poller
        bot_username = telegram_bot_poller.get_bot_username()
        if not bot_username:
            # Si el poller aún no arrancó, intenta cargar config para devolver algo
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
                "telegram_deep_link": (
                    f"https://t.me/{bot_username}?start={code}"
                    if bot_username else None
                ),
                "instructions": [
                    f"Abre Telegram y busca el bot @{bot_username}" if bot_username
                    else "El bot de Telegram no está configurado en el servidor",
                    f"Envíale el mensaje: /vincular {code}",
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
    Indica si un código ya fue consumido (vinculado) por el bot.
    Query: ?code=ABCD12
    Devuelve {linked: bool, expired: bool, chat: {...} | null}.
    El frontend hace polling cada 2s hasta linked=true o expired=true.
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
                # Buscar el chat más reciente para este usuario (asumimos
                # que fue creado al verificarse este código).
                chat = (
                    session.query(UserTelegramChat)
                    .filter_by(user_id=user_id, is_active=True)
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
    Devuelve qué bot está configurado para que el frontend pueda decir
    "abre Telegram y busca @MiBot". Si el bot aún no está configurado, el
    frontend lo dice al usuario y le explica cómo crearlo con BotFather.
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
    Verifica código (endpoint público, llamado por el bot de Telegram).
    Body: { "code": "ABCD12", "chat_id": "123456789", "username": "opcional" }
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
    """Obtiene los chats de Telegram vinculados al usuario."""
    try:
        user_id = int(get_jwt_identity())
        chats = link_service.get_user_chats(user_id)
        return jsonify({"success": True, "data": chats}), 200
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