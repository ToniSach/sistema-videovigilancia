from flask import Blueprint, request, jsonify, Response, make_response
from flask_jwt_extended import jwt_required, decode_token
from ...container import get_container
from ...streaming.mjpeg_streamer import mjpeg_streamer
import logging


cameras_bp = Blueprint("cameras", __name__, url_prefix="/api/v1/cameras")
logger = logging.getLogger(__name__)


def _get_service():
    """Helper para obtener el CameraService del contenedor."""
    return get_container().get("camera_service")


@cameras_bp.route("/", methods=["GET"])
@jwt_required()
def get_cameras():
    """Obtener lista de todas las cámaras."""
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
    """Obtener una cámara específica."""
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
    """Agregar nueva cámara."""
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
    """Actualizar cámara existente."""
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
    """Eliminar cámara."""
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
    """Activar o desactivar cámara."""
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
    """Descubrir cámaras ONVIF en red local."""
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


@cameras_bp.route("/<int:camera_id>/stream", methods=["GET"])
def get_camera_stream(camera_id: int):
    """
    Endpoint de streaming MJPEG.
    NO requiere JWT en cookie/header, sino token en query param para compatibilidad con <img> tags.
    """
    try:
        # Validar token manualmente desde query param
        token = request.args.get("token")

        if not token:
            return jsonify({"success": False, "error": "Token requerido"}), 401

        try:
            # Verificar token
            decode_token(token)
        except Exception:
            return jsonify({"success": False, "error": "Token inválido"}), 401

        # Retornar stream MJPEG
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