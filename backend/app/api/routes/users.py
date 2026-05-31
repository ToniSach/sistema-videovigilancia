"""
API Endpoints para gestión de usuarios.
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from backend.app.services.user_service import UserService
from backend.app.services.permission_service import PermissionService
from backend.app.core.security import admin_required

users_bp = Blueprint("users", __name__, url_prefix="/api/v1/users")
user_service = UserService()
permission_service = PermissionService()


@users_bp.route("/", methods=["GET"])
@jwt_required()
@admin_required
def get_users():
    """Obtiene lista de usuarios (solo admin)."""
    try:
        users = user_service.get_all_users()
        return jsonify({
            "success": True,
            "data": [u.to_dict() for u in users if u.is_active]
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@users_bp.route("/", methods=["POST"])
@jwt_required()
@admin_required
def create_user():
    """Crea nuevo usuario."""
    try:
        data = request.get_json()
        if not data or "username" not in data or "password" not in data:
            return jsonify({"success": False, "error": "Username y password requeridos"}), 400
        
        user = user_service.create_user(
            username=data["username"],
            password=data["password"],
            role=data.get("role", "user")
        )
        return jsonify({
            "success": True,
            "data": user.to_dict()
        }), 201
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@users_bp.route("/<int:user_id>", methods=["PUT"])
@jwt_required()
@admin_required
def update_user(user_id):
    """Actualiza usuario."""
    try:
        data = request.get_json()
        user = user_service.update_user(user_id, **data)
        if not user:
            return jsonify({"success": False, "error": "Usuario no encontrado"}), 404
        return jsonify({"success": True, "data": user.to_dict()}), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@users_bp.route("/<int:user_id>", methods=["DELETE"])
@jwt_required()
@admin_required
def delete_user(user_id):
    """Elimina usuario (soft delete)."""
    try:
        if user_service.delete_user(user_id):
            return jsonify({"success": True, "message": "Usuario eliminado"}), 200
        return jsonify({"success": False, "error": "Usuario no encontrado"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@users_bp.route("/me", methods=["GET"])
@jwt_required()
def get_current_user():
    """Obtiene usuario actual."""
    try:
        user_id = get_jwt_identity()
        user = user_service.get_user_by_id(int(user_id))
        if not user:
            return jsonify({"success": False, "error": "Usuario no encontrado"}), 404
        
        # Incluir cámaras accesibles
        cameras = permission_service.get_accessible_cameras(int(user_id))
        
        return jsonify({
            "success": True,
            "data": {
                **user.to_dict(),
                "accessible_cameras": cameras
            }
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500