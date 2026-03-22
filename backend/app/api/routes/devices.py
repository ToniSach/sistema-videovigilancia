"""
API Endpoints para gestión de dispositivos móviles.
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from backend.app.services.device_service import DeviceService

devices_bp = Blueprint("devices", __name__, url_prefix="/api/v1/devices")
device_service = DeviceService()


@devices_bp.route("/register", methods=["POST"])
def register_device():
    """
    Registro de dispositivo desde QR scan.
    No requiere JWT, usa link_token.
    """
    try:
        data = request.get_json()
        if not data or "link_token" not in data:
            return jsonify({"success": False, "error": "link_token requerido"}), 400
        
        # Validar link_token y obtener user_id
        from backend.app.services.qr_service import QRService
        qr_service = QRService()
        user_id = qr_service.consume_link_token(data["link_token"])
        
        if not user_id:
            return jsonify({"success": False, "error": "Token inválido o expirado"}), 401
        
        # Registrar dispositivo
        device, access_token, refresh_token = device_service.register_device(
            user_id=user_id,
            device_uuid=data["device_uuid"],
            device_name=data.get("device_name", "Dispositivo"),
            platform=data.get("platform", "unknown"),
            fcm_token=data["fcm_token"]
        )
        
        return jsonify({
            "success": True,
            "data": {
                "device_id": device.id,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "user": {
                    "id": user_id,
                    "username": device.user.username if hasattr(device, 'user') else None
                }
            }
        }), 201
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@devices_bp.route("/refresh", methods=["POST"])
def refresh_device_token():
    """Renueva tokens usando refresh_token."""
    try:
        data = request.get_json()
        if not data or "device_uuid" not in data or "refresh_token" not in data:
            return jsonify({"success": False, "error": "device_uuid y refresh_token requeridos"}), 400
        
        device = device_service.validate_refresh_token(
            data["device_uuid"],
            data["refresh_token"]
        )
        
        if not device:
            return jsonify({"success": False, "error": "Token inválido"}), 401
        
        # Generar nuevos tokens
        import secrets
        import hashlib
        
        new_refresh = secrets.token_urlsafe(32)
        new_access = secrets.token_urlsafe(32)
        
        with db_manager.get_session() as session:
            d = session.get(MobileDevice, device.id)
            d.refresh_token_hash = hashlib.sha256(new_refresh.encode()).hexdigest()
        
        return jsonify({
            "success": True,
            "data": {
                "access_token": new_access,
                "refresh_token": new_refresh
            }
        }), 200
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@devices_bp.route("/my-devices", methods=["GET"])
@jwt_required()
def get_my_devices():
    """Obtiene dispositivos del usuario."""
    try:
        user_id = int(get_jwt_identity())
        devices = device_service.get_user_devices(user_id)
        return jsonify({
            "success": True,
            "data": [d.to_dict() for d in devices]
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@devices_bp.route("/<int:device_id>", methods=["DELETE"])
@jwt_required()
def unregister_device(device_id):
    """Desactiva dispositivo."""
    try:
        user_id = int(get_jwt_identity())
        devices = device_service.get_user_devices(user_id)
        
        if not any(d.id == device_id for d in devices):
            return jsonify({"success": False, "error": "No autorizado"}), 403
        
        if device_service.deactivate_device(device_id):
            return jsonify({"success": True}), 200
        return jsonify({"success": False, "error": "No encontrado"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500