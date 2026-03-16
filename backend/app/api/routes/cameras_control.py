"""
API Routes para control de cámaras (PTZ, LEDs, Audio, Capabilities)
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
import logging

from ...container import Container
from ...database.models import Camera
from ....cameras.ptz_controller import PTZController
from ....cameras.led_controller import LEDController
from ....cameras.audio_controller import AudioController


cameras_control_bp = Blueprint("cameras_control", __name__, url_prefix="/api/v1/cameras")


def _get_camera(camera_id: int) -> Camera | None:
    """Helper para obtener cámara por ID usando repositorio"""
    try:
        camera_repo = Container.camera_repository()
        return camera_repo.get_by_id(camera_id)
    except Exception as e:
        logging.error(f"Error obteniendo cámara {camera_id}: {e}")
        return None


@cameras_control_bp.route("/<int:camera_id>/ptz/move", methods=["POST"])
@jwt_required()
def ptz_move(camera_id: int):
    """Mueve la cámara PTZ en la dirección especificada"""
    camera = _get_camera(camera_id)
    if not camera:
        return jsonify({"error": "Cámara no encontrada"}), 404

    if not camera.has_ptz:
        return jsonify({"error": "Cámara no soporta PTZ"}), 400

    data = request.get_json() or {}
    direction = data.get("direction")
    speed = data.get("speed", 0.5)

    valid_directions = ["up", "down", "left", "right", "zoom_in", "zoom_out"]
    if direction not in valid_directions:
        return jsonify({
            "error": f"Dirección inválida. Use: {valid_directions}"
        }), 400

    try:
        ptz = PTZController(camera)
        result = ptz.move(direction, float(speed))
        return jsonify({"moved": result}), 200 if result else 500
    except Exception as e:
        logging.error(f"Error PTZ move cámara {camera_id}: {e}")
        return jsonify({"error": str(e)}), 500


@cameras_control_bp.route("/<int:camera_id>/ptz/stop", methods=["POST"])
@jwt_required()
def ptz_stop(camera_id: int):
    """Detiene el movimiento PTZ"""
    camera = _get_camera(camera_id)
    if not camera:
        return jsonify({"error": "Cámara no encontrada"}), 404

    try:
        ptz = PTZController(camera)
        result = ptz.stop()
        return jsonify({"stopped": result}), 200 if result else 500
    except Exception as e:
        logging.error(f"Error PTZ stop cámara {camera_id}: {e}")
        return jsonify({"error": str(e)}), 500


@cameras_control_bp.route("/<int:camera_id>/ptz/presets", methods=["GET"])
@jwt_required()
def get_ptz_presets(camera_id: int):
    """Obtiene lista de presets PTZ"""
    camera = _get_camera(camera_id)
    if not camera:
        return jsonify({"error": "Cámara no encontrada"}), 404

    if not camera.has_ptz:
        return jsonify({"presets": []}), 200

    try:
        ptz = PTZController(camera)
        presets = ptz.get_presets()
        return jsonify({"presets": presets}), 200
    except Exception as e:
        logging.error(f"Error obteniendo presets cámara {camera_id}: {e}")
        return jsonify({"presets": []}), 200


@cameras_control_bp.route("/<int:camera_id>/ptz/presets", methods=["POST"])
@jwt_required()
def set_ptz_preset(camera_id: int):
    """Guarda posición actual como preset"""
    camera = _get_camera(camera_id)
    if not camera:
        return jsonify({"error": "Cámara no encontrada"}), 404

    if not camera.has_ptz:
        return jsonify({"error": "Cámara no soporta PTZ"}), 400

    data = request.get_json() or {}
    name = data.get("name")

    if not name:
        return jsonify({"error": "Nombre del preset requerido"}), 400

    try:
        ptz = PTZController(camera)
        token = ptz.set_preset(name)
        if token:
            return jsonify({"token": token, "name": name}), 200
        else:
            return jsonify({"error": "No se pudo guardar el preset"}), 500
    except Exception as e:
        logging.error(f"Error guardando preset cámara {camera_id}: {e}")
        return jsonify({"error": str(e)}), 500


@cameras_control_bp.route("/<int:camera_id>/ptz/presets/<string:token>/goto", methods=["POST"])
@jwt_required()
def goto_ptz_preset(camera_id: int, token: str):
    """Mueve la cámara a un preset específico"""
    camera = _get_camera(camera_id)
    if not camera:
        return jsonify({"error": "Cámara no encontrada"}), 404

    if not camera.has_ptz:
        return jsonify({"error": "Cámara no soporta PTZ"}), 400

    try:
        ptz = PTZController(camera)
        result = ptz.go_to_preset(token)
        return jsonify({"success": result}), 200 if result else 500
    except Exception as e:
        logging.error(f"Error yendo a preset cámara {camera_id}: {e}")
        return jsonify({"error": str(e)}), 500


@cameras_control_bp.route("/<int:camera_id>/leds", methods=["POST"])
@jwt_required()
def control_leds(camera_id: int):
    """
    Controla LEDs/IR Cut de la cámara.
    Body: {"mode": "on" | "off" | "auto"}
    """
    camera = _get_camera(camera_id)
    if not camera:
        return jsonify({"error": "Cámara no encontrada"}), 404

    if not camera.has_leds:
        return jsonify({"error": "Cámara no soporta control de LEDs"}), 400

    data = request.get_json() or {}
    mode = data.get("mode")

    if mode not in ["on", "off", "auto"]:
        return jsonify({"error": "Modo inválido. Use: on, off, auto"}), 400

    try:
        led = LEDController(camera)

        if mode == "on":
            result = led.turn_on()
        elif mode == "off":
            result = led.turn_off()
        else:  # auto
            result = led.set_auto()

        return jsonify({"success": result}), 200 if result else 500
    except Exception as e:
        logging.error(f"Error controlando LEDs cámara {camera_id}: {e}")
        return jsonify({"error": str(e)}), 500


@cameras_control_bp.route("/<int:camera_id>/audio/start", methods=["POST"])
@jwt_required()
def start_audio_talk(camera_id: int):
    """Inicia audio bidireccional (hablar por la cámara)"""
    camera = _get_camera(camera_id)
    if not camera:
        return jsonify({"error": "Cámara no encontrada"}), 404

    if not camera.has_audio:
        return jsonify({"error": "Cámara no soporta audio bidireccional"}), 400

    try:
        audio = AudioController(camera)
        result = audio.start_talk()
        return jsonify({"active": result}), 200 if result else 500
    except Exception as e:
        logging.error(f"Error iniciando audio cámara {camera_id}: {e}")
        return jsonify({"error": str(e)}), 500


@cameras_control_bp.route("/<int:camera_id>/audio/stop", methods=["POST"])
@jwt_required()
def stop_audio_talk(camera_id: int):
    """Detiene audio bidireccional"""
    camera = _get_camera(camera_id)
    if not camera:
        return jsonify({"error": "Cámara no encontrada"}), 404

    try:
        audio = AudioController(camera)
        result = audio.stop_talk()
        return jsonify({"active": False if result else True}), 200 if result else 500
    except Exception as e:
        logging.error(f"Error deteniendo audio cámara {camera_id}: {e}")
        return jsonify({"error": str(e)}), 500


@cameras_control_bp.route("/<int:camera_id>/capabilities", methods=["GET"])
@jwt_required()
def get_camera_capabilities(camera_id: int):
    """Obtiene capacidades de la cámara (PTZ, LEDs, Audio, etc.)"""
    camera = _get_camera(camera_id)
    if not camera:
        return jsonify({"error": "Cámara no encontrada"}), 404

    # Intentar obtener presets si tiene PTZ
    presets = []
    if camera.has_ptz:
        try:
            ptz = PTZController(camera)
            if ptz.is_supported():
                presets = ptz.get_presets()
        except Exception as e:
            logging.warning(f"No se pudieron obtener presets cámara {camera_id}: {e}")

    return jsonify({
        "camera_id": camera.id,
        "name": camera.name,
        "ptz": camera.has_ptz,
        "leds": camera.has_leds,
        "audio": camera.has_audio,
        "dual_lens": camera.is_dual_lens,
        "ai": camera.has_ai,
        "resolution": {
            "width": camera.resolution_width,
            "height": camera.resolution_height
        },
        "fps": camera.fps,
        "presets": presets
    }), 200
