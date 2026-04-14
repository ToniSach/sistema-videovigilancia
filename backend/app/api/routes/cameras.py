from flask import Blueprint, request, jsonify, Response
from flask_jwt_extended import jwt_required, decode_token, get_jwt_identity
import logging
import time

from ...container import get_container
from ...streaming.mjpeg_streamer import mjpeg_streamer
from backend.app.services.permission_service import PermissionService, require_camera_permission
from ...cameras.camera_manager import CameraManager
from ...workers.ffmpeg_worker import WorkerStatus
from backend.app.services.ptz_lock_service import ptz_lock_service
from backend.app.services.user_service import UserService

cameras_bp = Blueprint("cameras", __name__, url_prefix="/api/v1/cameras")
logger = logging.getLogger(__name__)

def _get_service():
    """Helper para obtener el CameraService del contenedor."""
    service = get_container().get("camera_service")
    if service is None:
        logger.error("CameraService no está registrado en el contenedor")
        raise RuntimeError("Servicio de cámaras no disponible")
    return service

# --- Endpoints CRUD y Básicos ---

@cameras_bp.route("/", methods=["GET"])
@jwt_required()
def get_cameras():
    try:
        service = _get_service()
        cameras = service.get_all_cameras()
        return jsonify({"success": True, "data": cameras})
    except Exception as e:
        logger.error(f"Error al obtener cámaras: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@cameras_bp.route("/<int:camera_id>", methods=["GET"])
@jwt_required()
def get_camera(camera_id: int):
    try:
        service = _get_service()
        camera = service.get_camera(camera_id)
        if not camera:
            return jsonify({"success": False, "error": "Cámara no encontrada"}), 404
        return jsonify({"success": True, "data": camera})
    except Exception as e:
        logger.error(f"Error al obtener cámara {camera_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@cameras_bp.route("/", methods=["POST"])
@jwt_required()
def add_camera():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No se proporcionaron datos"}), 400
        service = _get_service()
        camera = service.add_camera(data)
        return jsonify({"success": True, "data": camera}), 201
    except Exception as e:
        logger.error(f"Error al agregar cámara: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@cameras_bp.route("/<int:camera_id>", methods=["PUT"])
@jwt_required()
def update_camera(camera_id: int):
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No se proporcionaron datos"}), 400
        service = _get_service()
        camera = service.update_camera(camera_id, data)
        if not camera:
            return jsonify({"success": False, "error": "Cámara no encontrada"}), 404
        return jsonify({"success": True, "data": camera})
    except Exception as e:
        logger.error(f"Error al actualizar cámara {camera_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@cameras_bp.route("/<int:camera_id>", methods=["DELETE"])
@jwt_required()
def delete_camera(camera_id: int):
    try:
        service = _get_service()
        deleted = service.delete_camera(camera_id)
        if not deleted:
            return jsonify({"success": False, "error": "Cámara no encontrada"}), 404
        return jsonify({"success": True, "message": "Cámara eliminada"}), 200
    except Exception as e:
        logger.error(f"Error al eliminar cámara {camera_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@cameras_bp.route("/<int:camera_id>/toggle", methods=["PATCH"])
@jwt_required()
def toggle_camera(camera_id: int):
    try:
        data = request.get_json()
        if data is None or "active" not in data:
            return jsonify({"success": False, "error": "Se requiere campo 'active'"}), 400
        service = _get_service()
        camera = service.toggle_camera(camera_id, data["active"])
        if not camera:
            return jsonify({"success": False, "error": "Cámara no encontrada"}), 404
        return jsonify({"success": True, "data": camera})
    except Exception as e:
        logger.error(f"Error al toggle cámara {camera_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@cameras_bp.route("/discover", methods=["POST"])
@jwt_required()
def discover_cameras():
    try:
        service = _get_service()
        cameras = service.discover_cameras()
        return jsonify({"success": True, "data": cameras, "count": len(cameras)})
    except Exception as e:
        logger.error(f"Error en descubrimiento: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# --- Endpoints de Control (PTZ, LEDs, Audio) ---

@cameras_bp.route("/<int:camera_id>/ptz/<string:direction>", methods=["POST"])
@jwt_required()
@require_camera_permission("control_ptz")
def ptz_control(camera_id, direction):
    try:
        user_id = int(get_jwt_identity())
        user = UserService().get_user_by_id(user_id)
        username = user.username if user else str(user_id)
        
        # Adquirir lock
        acquired, error = ptz_lock_service.acquire_lock(camera_id, user_id, username)
        if not acquired:
            return jsonify({"success": False, "error": error}), 423
        
        try:
            service = _get_service()
            result = service.ptz_control(camera_id, direction)
            # Extender lock por 10s después del movimiento
            ptz_lock_service.extend_lock(camera_id, user_id, 10)
            return jsonify({"success": True, "data": result})
        finally:
            ptz_lock_service.release_lock(camera_id, user_id)
            
    except Exception as e:
        logger.error(f"Error en PTZ {camera_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@cameras_bp.route("/<int:camera_id>/ptz/status", methods=["GET"])
@jwt_required()
def ptz_status(camera_id):
    """Obtiene estado del lock PTZ."""
    try:
        status = ptz_lock_service.get_lock_status(camera_id)
        return jsonify({
            "success": True,
            "data": {
                "locked": status is not None,
                "lock_info": status
            }
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@cameras_bp.route("/<int:camera_id>/leds/<string:state>", methods=["POST"])
@jwt_required()
@require_camera_permission("control_leds")
def led_control(camera_id, state):
    try:
        service = _get_service()
        result = service.set_led_state(camera_id, state)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error LEDs {camera_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@cameras_bp.route("/<int:camera_id>/audio/talk", methods=["POST"])
@jwt_required()
@require_camera_permission("control_audio")
def audio_talk(camera_id):
    try:
        data = request.get_json() or {}
        service = _get_service()
        result = service.audio_talk(camera_id, data)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error audio talk {camera_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# --- Diagnóstico y Streaming ---

@cameras_bp.route("/<int:camera_id>/diagnose", methods=["GET"])
@jwt_required()
def diagnose_camera(camera_id: int):
    try:
        cm = CameraManager()
        worker = cm.get_worker(camera_id)
        camera = cm._camera_repo.get_by_id(camera_id)

        if not camera:
            return jsonify({"success": False, "error": "Cámara no encontrada"}), 404

        status = {
            "camera_id": camera_id,
            "name": camera.name,
            "connection_type": camera.connection_type,
            "last_error_code": camera.last_error_code,
            "last_connected_at": camera.last_connected_at.isoformat() if camera.last_connected_at else None,
            "is_active": camera.is_active
        }

        if worker:
            worker_status = worker.get_status()
            status.update({
                "worker_status": worker_status,
                "health": "ok" if worker_status["status"] == WorkerStatus.RUNNING.value else "degraded"
            })
        else:
            all_status = cm.get_all_status()
            perm = all_status.get(camera_id, {}).get('permanent_error', False) if all_status else False
            status.update({
                "worker_status": None,
                "health": "permanent_error" if perm else "stopped"
            })
        return jsonify({"success": True, "data": status})
    except Exception as e:
        logger.error(f"Error en diagnóstico: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

# FIX F0.5 + NUEVO: Endpoint de streaming con soporte para lentes duales
@cameras_bp.route("/<int:camera_id>/stream", methods=["GET"])
def get_camera_stream(camera_id: int):
    """
    Endpoint de streaming MJPEG con validación de token y permisos de cámara.
    Soporta parámetro 'type': main (default), l1, l2 para cámaras dual-lens.
    """
    try:
        # 1. Obtener tipo de stream (main, l1, l2)
        stream_type = request.args.get("type", "main")
        
        # Validar tipo permitido
        if stream_type not in ("main", "l1", "l2"):
            return jsonify({"success": False, "error": "Tipo de stream inválido. Use main, l1 o l2"}), 400

        # 2. Validación de Token obligatoria
        token = request.args.get("token")
        if not token:
            return jsonify({"success": False, "error": "Token requerido"}), 401
        
        try:
            decoded = decode_token(token)
            user_id = int(decoded['sub'])  # 'sub' es el identity (user_id)
        except Exception as e:
            logger.warning(f"Token inválido en stream cámara {camera_id}: {e}")
            return jsonify({"success": False, "error": "Token inválido"}), 401

        # 3. Verificar permiso 'view' sobre la cámara (el permiso es para la cámara física, no por lente)
        permission_service = PermissionService()
        if not permission_service.check_permission(user_id, camera_id, 'view'):
            logger.warning(f"Usuario {user_id} intentó acceder a stream cámara {camera_id} sin permiso")
            return jsonify({"success": False, "error": "Permiso denegado para esta cámara"}), 403

        # 4. Generar ID de cliente único para esta solicitud
        client_id = f"http_{int(time.time())}_{id(request)}"

        # 5. Registrar cliente en el MJPEG streamer, pasando camera_id y stream_type
        queue = mjpeg_streamer.register_client(camera_id, stream_type, client_id)

        if queue is None:
            return jsonify({
                "success": False, 
                "error": f"Límite de conexiones alcanzado para el stream {stream_type} de esta cámara (máx 5)"
            }), 503

        # 6. Retornar la respuesta de streaming
        return Response(
            mjpeg_streamer.generate_stream(camera_id, stream_type, client_id),
            mimetype="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )

    except Exception as e:
        logger.error(f"Error en stream de cámara {camera_id}: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor de streaming"}), 500