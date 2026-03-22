"""
API Endpoints para preferencias de notificación.
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from backend.app.services.notification_preference_service import NotificationPreferenceService

notifications_bp = Blueprint("notifications", __name__, url_prefix="/api/v1/notifications")
pref_service = NotificationPreferenceService()


@notifications_bp.route("/preferences", methods=["GET"])
@jwt_required()
def get_preferences():
    """Obtiene preferencias del usuario."""
    try:
        user_id = int(get_jwt_identity())
        prefs = pref_service.get_user_preferences(user_id)
        
        result = []
        for p in prefs:
            channels = [c.channel for c in p.channels]
            days = [d.day_of_week for d in p.days]
            
            result.append({
                "id": p.id,
                "event_type": p.event_type,
                "camera_id": p.camera_id,
                "enabled": p.enabled,
                "channels": channels,
                "schedule": {
                    "start": p.schedule_start.isoformat() if p.schedule_start else None,
                    "end": p.schedule_end.isoformat() if p.schedule_end else None
                },
                "days": days
            })
        
        return jsonify({"success": True, "data": result}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@notifications_bp.route("/preferences", methods=["POST"])
@jwt_required()
def create_preference():
    """Crea preferencia."""
    try:
        user_id = int(get_jwt_identity())
        data = request.get_json()
        
        if not data or "event_type" not in data:
            return jsonify({"success": False, "error": "event_type requerido"}), 400
        
        from datetime import datetime
        
        # Parsear horarios si vienen
        schedule_start = None
        schedule_end = None
        if data.get("schedule_start"):
            schedule_start = datetime.strptime(data["schedule_start"], "%H:%M").time()
        if data.get("schedule_end"):
            schedule_end = datetime.strptime(data["schedule_end"], "%H:%M").time()
        
        pref = pref_service.create_preference(
            user_id=user_id,
            event_type=data["event_type"],
            camera_id=data.get("camera_id"),
            enabled=data.get("enabled", True),
            channels=data.get("channels", ["push"]),
            schedule_start=schedule_start,
            schedule_end=schedule_end,
            days_of_week=data.get("days_of_week", [0, 1, 2, 3, 4, 5, 6])
        )
        
        return jsonify({"success": True, "data": {"id": pref.id}}), 201
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@notifications_bp.route("/preferences/<int:pref_id>", methods=["PUT"])
@jwt_required()
def update_preference(pref_id):
    """Actualiza preferencia."""
    try:
        user_id = int(get_jwt_identity())
        data = request.get_json()
        
        # Verificar ownership
        prefs = pref_service.get_user_preferences(user_id)
        if not any(p.id == pref_id for p in prefs):
            return jsonify({"success": False, "error": "No autorizado"}), 403
        
        pref = pref_service.update_preference(pref_id, **data)
        if not pref:
            return jsonify({"success": False, "error": "No encontrado"}), 404
        
        return jsonify({"success": True, "data": {"id": pref.id}}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@notifications_bp.route("/preferences/<int:pref_id>", methods=["DELETE"])
@jwt_required()
def delete_preference(pref_id):
    """Elimina preferencia."""
    try:
        user_id = int(get_jwt_identity())
        prefs = pref_service.get_user_preferences(user_id)
        if not any(p.id == pref_id for p in prefs):
            return jsonify({"success": False, "error": "No autorizado"}), 403
        
        if pref_service.delete_preference(pref_id):
            return jsonify({"success": True}), 200
        return jsonify({"success": False, "error": "No encontrado"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500