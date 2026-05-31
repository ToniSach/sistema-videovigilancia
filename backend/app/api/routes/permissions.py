"""
API Endpoints para gestión de permisos de cámaras.
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from backend.app.services.permission_service import PermissionService, require_camera_permission
from backend.app.services.user_service import UserService

permissions_bp = Blueprint("permissions", __name__, url_prefix="/api/v1/permissions")
permission_service = PermissionService()
user_service = UserService()


@permissions_bp.route("/camera/<int:camera_id>", methods=["GET"])
@jwt_required()
def get_camera_permissions(camera_id):
    """Obtiene permisos sobre una cámara (solo admin o owner)."""
    try:
        user_id = int(get_jwt_identity())
        
        # Verificar si es admin o tiene permisos de administración sobre la cámara
        if not user_service.is_admin(user_id):
            return jsonify({"success": False, "error": "Admin requerido"}), 403
        
        perms = permission_service.get_camera_permissions(camera_id)
        return jsonify({
            "success": True,
            "data": [{
                "user_id": p.user_id,
                "username": p.user.username if hasattr(p, 'user') else None,
                "can_view": p.can_view,
                "can_control_ptz": p.can_control_ptz,
                "can_control_leds": p.can_control_leds,
                "can_control_audio": p.can_control_audio,
                "can_download_recordings": p.can_download_recordings
            } for p in perms]
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@permissions_bp.route("/camera/<int:camera_id>/user/<int:target_user_id>", methods=["POST"])
@jwt_required()
def grant_permission(camera_id, target_user_id):
    """Otorga permisos a un usuario sobre una cámara."""
    try:
        user_id = int(get_jwt_identity())
        
        # Solo admin o owner pueden otorgar permisos
        if not user_service.is_admin(user_id):
            # TODO: Verificar si es owner de la cámara
            pass
        
        data = request.get_json() or {}
        
        perm = permission_service.grant_permission(
            user_id=target_user_id,
            camera_id=camera_id,
            can_view=data.get("can_view", True),
            can_control_ptz=data.get("can_control_ptz", False),
            can_control_leds=data.get("can_control_leds", False),
            can_control_audio=data.get("can_control_audio", False),
            can_download_recordings=data.get("can_download_recordings", False)
        )
        
        return jsonify({
            "success": True,
            "data": {
                "id": perm.id,
                "user_id": perm.user_id,
                "camera_id": perm.camera_id,
                "permissions": {
                    "view": perm.can_view,
                    "ptz": perm.can_control_ptz,
                    "leds": perm.can_control_leds,
                    "audio": perm.can_control_audio,
                    "download": perm.can_download_recordings
                }
            }
        }), 201
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@permissions_bp.route("/camera/<int:camera_id>/user/<int:target_user_id>", methods=["DELETE"])
@jwt_required()
def revoke_permission(camera_id, target_user_id):
    """Revoca permisos."""
    try:
        user_id = int(get_jwt_identity())
        
        if not user_service.is_admin(user_id):
            return jsonify({"success": False, "error": "Admin requerido"}), 403
        
        if permission_service.revoke_permission(target_user_id, camera_id):
            return jsonify({"success": True, "message": "Permisos revocados"}), 200
        return jsonify({"success": False, "error": "No se encontraron permisos"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@permissions_bp.route("/my-cameras", methods=["GET"])
@jwt_required()
def get_my_cameras():
    """Obtiene cámaras accesibles para el usuario actual."""
    try:
        user_id = int(get_jwt_identity())
        cameras = permission_service.get_accessible_cameras(user_id)
        return jsonify({
            "success": True,
            "data": cameras
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500