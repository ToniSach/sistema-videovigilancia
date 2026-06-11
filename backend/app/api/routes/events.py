"""
================================================================================
MÓDULO: api.routes.events — Blueprint REST de eventos y snapshots
================================================================================

PROPÓSITO
    Capa HTTP del Pipeline #10 (Eventos). Lista los eventos detectados (IA,
    movimiento, cámara offline...), permite verlos en detalle,
    reconocerlos (acknowledge), consultar estadísticas y descargar el snapshot
    JPEG asociado a cada evento.

RESPONSABILIDAD
    Contrato HTTP de lectura/consulta de eventos. La GENERACIÓN de eventos NO
    ocurre aquí: los workers (IA/movimiento/FFmpeg) publican EventData en
    EventManager, y EventService los persiste en BD. Este blueprint solo los
    expone de vuelta a los clientes.

DEPENDENCIAS
    services.event_service.EventService (vía DI; fallback a instancia nueva si el
        contenedor no inicializó) → get_events/acknowledge_event/get_stats.
    database.repositories.event_repository.EventRepository → get_by_id.
    services.permission_service.PermissionService → check_permission para el
        snapshot (autorización por cámara del evento).
    config.settings.RECORDINGS_PATH → raíz permitida para servir snapshots.

SEGURIDAD DEL SNAPSHOT (FIX F0.4 / F0.5)
    get_event_snapshot valida (a) permiso 'view' sobre la cámara del evento y
    (b) que el path resuelto del snapshot caiga DENTRO de RECORDINGS_PATH/snapshots
    (pathlib.resolve + startswith), bloqueando path traversal y symlinks que
    escapen del área permitida.

PUNTO DE ENTRADA
    Registrado en main.register_blueprints() como "events_bp". url_prefix=
    /api/v1/events. get_events está EXENTO del rate limiter (polling de la UI).

PIPELINE(S)
    #10 Eventos — listado, detalle, acknowledge, stats, snapshot.

ENDPOINTS DEL BLUEPRINT
    GET   /                       → lista de eventos (filtros)            [JWT]
    GET   /<event_id>             → detalle de un evento                  [JWT]
    PATCH /<event_id>/acknowledge → marca el evento como reconocido       [JWT]
    GET   /stats                  → estadísticas agregadas                [JWT]
    GET   /<event_id>/snapshot    → JPEG del evento (permiso por cámara)  [JWT]
================================================================================
"""
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
    """
    Propósito: lista de eventos detectados, con filtros (Pipeline #10, consulta).
        Lo poletea la UI (exento del rate limiter).
    Método+Ruta: GET /api/v1/events/
    Inputs (query):
        camera_id?: int — filtra por cámara.
        event_type?: str — person|vehicle|motion|... .
        hours?: int (def 24) — ventana hacia atrás.
        limit?: int (def 50). Permiso: @jwt_required().
    Outputs:
        200 → {"success": true, "data": [<evento>...], "count": int}.
        500 → {"success": false, "error": <str(e)>}.
    Llama a: EventService.get_events(camera_id, event_type, hours, limit).
    """
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
    """
    Propósito: detalle de UN evento (Pipeline #10).
    Método+Ruta: GET /api/v1/events/<event_id>
    Inputs: path event_id. Permiso: @jwt_required().
    Outputs:
        200 → {"success": true, "data": event.to_dict()}.
        404 → evento no encontrado.
        500 → {"success": false, "error": <str(e)>}.
    Llama a: EventRepository.get_by_id().
    Nota: NO revalida permiso por cámara (solo el snapshot lo hace); el detalle
        textual se considera de baja sensibilidad.
    """
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
    """
    Propósito: marca un evento como reconocido/atendido por el operador
        (Pipeline #10, gestión). Apaga su badge de "no leído" en la UI.
    Método+Ruta: PATCH /api/v1/events/<event_id>/acknowledge
    Inputs: path event_id. Permiso: @jwt_required().
    Outputs:
        200 → {"success": true, "message": "Evento reconocido"}.
        400 → no se pudo reconocer (no existe / ya reconocido).
        500 → {"success": false, "error": <str(e)>}.
    Llama a: EventService.acknowledge_event(event_id).
    """
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
    """
    Propósito: estadísticas agregadas de eventos (conteos por tipo/cámara para
        dashboards). Pipeline #10.
    Método+Ruta: GET /api/v1/events/stats
    Inputs: ninguno. Permiso: @jwt_required().
    Outputs:
        200 → {"success": true, "data": <stats de EventService>}.
        500 → {"success": false, "error": <str(e)>}.
    Llama a: EventService.get_stats().
    """
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
    Propósito: sirve el JPEG snapshot capturado en el instante del evento
        (Pipeline #10, evidencia visual). Endpoint sensible: aplica doble defensa
        (permiso por cámara + anti path-traversal).
    Método+Ruta: GET /api/v1/events/<event_id>/snapshot
    Inputs: path event_id. Permiso: @jwt_required() + check_permission 'view'
        sobre event.camera_id.
    Outputs:
        200 → image/jpeg (con soporte conditional/ETag).
        400 → path inválido (OSError/ValueError al resolver).
        403 → sin permiso 'view', o path fuera de RECORDINGS_PATH/snapshots
              (traversal o symlink que escapa).
        404 → evento sin snapshot, o archivo inexistente/no-fichero.
        500 → error interno.
    Llama a: EventRepository.get_by_id(), PermissionService.check_permission().
    Seguridad: resuelve el path con pathlib.resolve() y exige que empiece por
        (RECORDINGS_PATH/snapshots) resuelto; re-chequea si es symlink.
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