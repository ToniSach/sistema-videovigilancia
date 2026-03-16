from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from ...services.event_service import EventService
from ...database.repositories.event_repository import EventRepository
from ...database.connection import db_manager


events_bp = Blueprint("events", __name__, url_prefix="/api/v1/events")


def get_event_service():
    event_repo = EventRepository(db_manager)
    return EventService(event_repo)


@events_bp.route("/", methods=["GET"])
@jwt_required()
def get_events():
    try:
        camera_id = request.args.get("camera_id", type=int)
        event_type = request.args.get("event_type", type=str)
        hours = request.args.get("hours", default=24, type=int)
        limit = request.args.get("limit", default=50, type=int)

        service = get_event_service()
        events = service.get_events(
            camera_id=camera_id,
            event_type=event_type,
            hours=hours,
            limit=limit
        )

        return jsonify({
            "success": True,
            "data": events,
            "count": len(events)
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@events_bp.route("/<int:event_id>", methods=["GET"])
@jwt_required()
def get_event(event_id):
    try:
        event_repo = EventRepository(db_manager)
        event = event_repo.get_by_id(event_id)

        if not event:
            return jsonify({
                "success": False,
                "error": "Evento no encontrado"
            }), 404

        return jsonify({
            "success": True,
            "data": event.to_dict()
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@events_bp.route("/<int:event_id>/acknowledge", methods=["PATCH"])
@jwt_required()
def acknowledge_event(event_id):
    try:
        service = get_event_service()
        success = service.acknowledge_event(event_id)

        if success:
            return jsonify({
                "success": True,
                "message": "Evento reconocido"
            }), 200
        else:
            return jsonify({
                "success": False,
                "error": "No se pudo reconocer el evento"
            }), 400

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@events_bp.route("/stats", methods=["GET"])
@jwt_required()
def get_stats():
    try:
        service = get_event_service()
        stats = service.get_stats()

        return jsonify({
            "success": True,
            "data": stats
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
