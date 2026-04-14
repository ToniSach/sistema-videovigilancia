from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from ...services.event_service import EventService
from ...database.repositories.event_repository import EventRepository

# FIX F0.4: Imports adicionales para seguridad
from pathlib import Path
from backend.app.services.permission_service import PermissionService
from backend.app.config import settings
from flask import send_file
import os
import logging

logger = logging.getLogger(__name__)

events_bp = Blueprint("events", __name__, url_prefix="/api/v1/events")


def get_event_service():
    """Obtiene EventService del contenedor con fallback seguro."""
    from backend.app.container import get_container
    service = get_container().get("event_service")
    if service is None:
        # Fallback seguro si el contenedor no inicializó
        from backend.app.services.event_service import EventService
        from backend.app.database.repositories.event_repository import EventRepository
        return EventService(EventRepository())
    return service


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
        event_repo = EventRepository()
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


# ============================================================================
# Snapshot endpoint - CORREGIDO F0.4 y F0.5
# ============================================================================

@events_bp.route("/<int:event_id>/snapshot", methods=["GET"])
@jwt_required()
def get_event_snapshot(event_id):
    """
    Retorna la imagen snapshot asociada a un evento.
    Valida permisos sobre la cámara del evento y previene path traversal.
    """
    try:
        user_id = int(get_jwt_identity())
        event_repo = EventRepository()
        permission_service = PermissionService()
        
        event = event_repo.get_by_id(event_id)
        
        if not event:
            return jsonify({
                "success": False,
                "error": "Evento no encontrado"
            }), 404
        
        # FIX F0.4: Verificar permisos sobre la cámara específica
        if not permission_service.check_permission(user_id, event.camera_id, 'view'):
            return jsonify({
                "success": False,
                "error": "Permiso denegado para esta cámara"
            }), 403
        
        if not event.snapshot_path:
            return jsonify({
                "success": False,
                "error": "Snapshot no disponible"
            }), 404
        
        # FIX F0.5: Validación robusta de path traversal usando pathlib
        try:
            # Resolver paths absolutos y normalizados
            recordings_path = Path(settings.RECORDINGS_PATH).resolve()
            allowed_base = (recordings_path / "snapshots").resolve()
            
            # Resolver el path solicitado
            requested_path = Path(event.snapshot_path).resolve()
            
            # Verificar que el path solicitado esté dentro de allowed_base
            # Usar str().startswith() es seguro después de resolve() en ambos
            if not str(requested_path).startswith(str(allowed_base)):
                logger.warning(f"Path traversal bloqueado: {requested_path} no está en {allowed_base}")
                return jsonify({
                    "success": False,
                    "error": "Acceso no permitido"
                }), 403
            
            # Verificar que sea archivo (no directorio) y exista
            if not requested_path.is_file():
                return jsonify({
                    "success": False,
                    "error": "Archivo no encontrado"
                }), 404
            
            # Verificar que no sea un symlink fuera del área permitida
            if requested_path.is_symlink():
                real_path = requested_path.resolve()
                if not str(real_path).startswith(str(allowed_base)):
                    logger.warning(f"Symlink traversal bloqueado: {real_path}")
                    return jsonify({
                        "success": False,
                        "error": "Acceso no permitido"
                    }), 403
            
        except (OSError, ValueError) as path_error:
            logger.error(f"Error validando path: {path_error}")
            return jsonify({
                "success": False,
                "error": "Path inválido"
            }), 400
        
        return send_file(
            str(requested_path),  # send_file acepta string
            mimetype="image/jpeg",
            as_attachment=False,
            conditional=True
        )
        
    except Exception as e:
        logger.error(f"Error en snapshot de evento {event_id}: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500