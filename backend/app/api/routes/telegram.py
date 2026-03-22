"""
API Endpoints para vinculación con Telegram.
"""
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity

from backend.app.services.telegram_link_service import TelegramLinkService

telegram_bp = Blueprint("telegram", __name__, url_prefix="/api/v1/telegram")
telegram_service = TelegramLinkService()


@telegram_bp.route("/link-code", methods=["POST"])
@jwt_required()
def generate_link_code():
    """Genera código de vinculación."""
    try:
        user_id = int(get_jwt_identity())
        code = telegram_service.generate_code(user_id)
        
        return jsonify({
            "success": True,
            "data": {
                "code": code,
                "expires_in_minutes": 5,
                "instructions": f"Abre @TuNVRBot y envía: /link {code}"
            }
        }), 201
        
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 429
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@telegram_bp.route("/chats", methods=["GET"])
@jwt_required()
def get_linked_chats():
    """Obtiene chats vinculados."""
    try:
        user_id = int(get_jwt_identity())
        chats = telegram_service.get_user_chats(user_id)
        return jsonify({"success": True, "data": chats}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@telegram_bp.route("/chats/<int:chat_id>", methods=["DELETE"])
@jwt_required()
def unlink_chat(chat_id):
    """Desvincula chat."""
    try:
        user_id = int(get_jwt_identity())
        if telegram_service.unlink_telegram(user_id, chat_id):
            return jsonify({"success": True}), 200
        return jsonify({"success": False, "error": "No encontrado"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@telegram_bp.route("/webhook", methods=["POST"])
def telegram_webhook():
    """
    Webhook para recibir mensajes del bot de Telegram.
    Configurar en BotFather: https://api.telegram.org/bot<TOKEN>/setWebhook?url=<URL>/api/v1/telegram/webhook
    """
    try:
        data = request.get_json()
        if not data or "message" not in data:
            return jsonify({"ok": True}), 200  # Responder siempre 200 a Telegram
        
        message = data["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")
        username = message["from"].get("username")
        
        # Procesar comando /link
        if text.startswith("/link"):
            parts = text.split()
            if len(parts) != 2:
                _send_telegram_message(chat_id, "Uso: /link CÓDIGO")
                return jsonify({"ok": True}), 200
            
            code = parts[1].upper()
            success = telegram_service.verify_code(code, chat_id, username)
            
            if success:
                _send_telegram_message(chat_id, "✅ Cuenta vinculada correctamente. Recibirás notificaciones aquí.")
            else:
                _send_telegram_message(chat_id, "❌ Código inválido o expirado.")
        
        elif text == "/start":
            _send_telegram_message(
                chat_id, 
                "Bienvenido al Bot de NVR. Usa /link CÓDIGO para vincular tu cuenta."
            )
        
        return jsonify({"ok": True}), 200
        
    except Exception as e:
        current_app.logger.error(f"Error en webhook Telegram: {e}")
        return jsonify({"ok": True}), 200  # Siempre 200 para Telegram


def _send_telegram_message(chat_id: str, text: str):
    """Helper para enviar mensaje."""
    import requests
    from backend.app.config import settings
    
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return
    
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=5
        )
    except:
        pass