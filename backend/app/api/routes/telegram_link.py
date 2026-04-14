"""
Endpoints para que los usuarios vinculen su cuenta de Telegram.
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from backend.app.services.telegram_link_service import TelegramLinkService

telegram_link_bp = Blueprint("telegram_link", __name__, url_prefix="/api/v1/telegram")
link_service = TelegramLinkService()


@telegram_link_bp.route("/generate-code", methods=["POST"])
@jwt_required()
def generate_code():
    """Genera código de vinculación para el usuario autenticado."""
    try:
        user_id = int(get_jwt_identity())
        code = link_service.generate_code(user_id)
        return jsonify({"success": True, "code": code}), 200
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


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
        return jsonify({"success": False, "error": str(e)}), 500


@telegram_link_bp.route("/chats", methods=["GET"])
@jwt_required()
def get_chats():
    """Obtiene los chats de Telegram vinculados al usuario."""
    try:
        user_id = int(get_jwt_identity())
        chats = link_service.get_user_chats(user_id)
        return jsonify({"success": True, "data": chats}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


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
        return jsonify({"success": False, "error": str(e)}), 500