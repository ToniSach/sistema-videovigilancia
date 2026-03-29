from flask import Blueprint, request, jsonify, Response
from flask_jwt_extended import jwt_required, decode_token
from ...container import get_container
from ...streaming.mjpeg_streamer import mjpeg_streamer
import logging

from backend.app.services.permission_service import require_camera_permission
from ...cameras.camera_manager import CameraManager
from ...workers.ffmpeg_worker import WorkerStatus

cameras_bp = Blueprint("cameras", __name__, url_prefix="/api/v1/cameras")
logger = logging.getLogger(__name__)


def _get_service():
    """Helper para obtener el CameraService del contenedor."""
    service = get_container().get("camera_service")
    if service is None:
        logger.error("CameraService no está registrado en el contenedor")
        raise RuntimeError("Servicio de cámaras no disponible")
    return service


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

        return jsonify({
            "success": True,
            "data": cameras,
            "count": len(cameras)
        })

    except Exception as e:
        logger.error(f"Error en descubrimiento: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# =========================
# NUEVOS ENDPOINTS CONTROL
# =========================

@cameras_bp.route("/<int:camera_id>/ptz/<string:direction>", methods=["POST"])
@jwt_required()
@require_camera_permission("control_ptz")
def ptz_control(camera_id, direction):
    try:
        service = _get_service()
        result = service.ptz_control(camera_id, direction)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error en PTZ {camera_id}: {e}")
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


@cameras_bp.route("/<int:camera_id>/diagnose", methods=["GET"])
@jwt_required()
def diagnose_camera(camera_id: int):
    """
    Endpoint de diagnóstico para verificar el estado del stream.
    Retorna información detallada para debugging.
    """
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
            # Verificar si está en error permanente
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


@cameras_bp.route("/<int:camera_id>/stream", methods=["GET"])
def get_camera_stream(camera_id: int):
    try:
        token = request.args.get("token")

        if not token:
            return jsonify({"success": False, "error": "Token requerido"}), 401

        try:
            decode_token(token)
        except Exception:
            return jsonify({"success": False, "error": "Token inválido"}), 401

        return Response(
            mjpeg_streamer.generate_stream(camera_id),
            mimetype="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )

    except Exception as e:
        logger.error(f"Error en stream de cámara {camera_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500