"""
Endpoints para activación/desactivación de IA (YOLOv8) por cámara y por lente.

Rutas:
    GET  /api/v1/ai/status                         → estado global
    POST /api/v1/ai/<camera_id>/activate           → activa IA (body: lens, mode)
    POST /api/v1/ai/<camera_id>/deactivate         → desactiva IA (body: lens)
    GET  /api/v1/ai/<camera_id>                    → estado de una cámara
"""
import logging

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required

from ...container import get_container

ai_bp = Blueprint("ai", __name__, url_prefix="/api/v1/ai")
logger = logging.getLogger(__name__)

_VALID_LENSES = ("main", "l1", "l2")
_VALID_MODES = ("low_cpu", "high_quality")


def _svc():
    svc = get_container().get("ai_service")
    if svc is None:
        raise RuntimeError("AIService no disponible en el contenedor")
    return svc


@ai_bp.route("/status", methods=["GET"])
@jwt_required()
def get_status():
    try:
        return jsonify({"success": True, "data": _svc().get_ai_status()}), 200
    except Exception as e:
        logger.error(f"Error obteniendo status IA: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@ai_bp.route("/<int:camera_id>", methods=["GET"])
@jwt_required()
def get_camera_status(camera_id: int):
    try:
        svc = _svc()
        return jsonify({
            "success": True,
            "data": {
                "camera_id": camera_id,
                "main": svc.is_active(camera_id, "main"),
                "l1": svc.is_active(camera_id, "l1"),
                "l2": svc.is_active(camera_id, "l2"),
            }
        }), 200
    except Exception as e:
        logger.error(f"Error estado IA cámara {camera_id}: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@ai_bp.route("/<int:camera_id>/activate", methods=["POST"])
@jwt_required()
def activate(camera_id: int):
    try:
        data = request.get_json(silent=True) or {}
        lens = (data.get("lens") or "main").lower()
        mode = (data.get("mode") or "low_cpu").lower()

        if lens not in _VALID_LENSES:
            return jsonify({
                "success": False,
                "error": f"lens debe ser uno de {_VALID_LENSES}"
            }), 400
        if mode not in _VALID_MODES:
            return jsonify({
                "success": False,
                "error": f"mode debe ser uno de {_VALID_MODES}"
            }), 400

        ok = _svc().activate_ai(camera_id, lens=lens, mode=mode)
        if not ok:
            return jsonify({
                "success": False,
                "error": (
                    "No se pudo activar IA. Verifica que la cámara esté activa y, "
                    "si pediste lens=l1/l2, que sea dual-lens."
                )
            }), 400

        return jsonify({
            "success": True,
            "data": {"camera_id": camera_id, "lens": lens, "mode": mode}
        }), 200
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except RuntimeError as re:
        # Discriminar dependencias faltantes (503) vs regla de exclusividad
        # de lente (409). El mensaje de exclusividad menciona "lente"; el
        # de dependencias menciona "pip" o "torch".
        msg = str(re)
        if "lente" in msg.lower() or "lens" in msg.lower():
            logger.warning(f"Conflicto de lente cám {camera_id}: {msg}")
            return jsonify({
                "success": False,
                "error": msg,
                "error_code": "AI_LENS_CONFLICT",
            }), 409
        logger.error(f"Dependencias IA faltantes cám {camera_id}: {re}")
        return jsonify({
            "success": False,
            "error": msg,
            "error_code": "AI_DEPENDENCIES_MISSING"
        }), 503
    except Exception as e:
        logger.error(f"Error activando IA cámara {camera_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@ai_bp.route("/<int:camera_id>/deactivate", methods=["POST"])
@jwt_required()
def deactivate(camera_id: int):
    try:
        data = request.get_json(silent=True) or {}
        lens = (data.get("lens") or "main").lower()

        if lens not in _VALID_LENSES:
            return jsonify({
                "success": False,
                "error": f"lens debe ser uno de {_VALID_LENSES}"
            }), 400

        ok = _svc().deactivate_ai(camera_id, lens=lens)
        if not ok:
            return jsonify({
                "success": False,
                "error": "No había IA activa en esa cámara/lente"
            }), 404

        return jsonify({
            "success": True,
            "data": {"camera_id": camera_id, "lens": lens}
        }), 200
    except Exception as e:
        logger.error(f"Error desactivando IA cámara {camera_id}: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@ai_bp.route("/<int:camera_id>/mode", methods=["PATCH"])
@jwt_required()
def change_mode(camera_id: int):
    try:
        data = request.get_json(silent=True) or {}
        lens = (data.get("lens") or "main").lower()
        mode = (data.get("mode") or "").lower()

        if lens not in _VALID_LENSES:
            return jsonify({"success": False, "error": f"lens inválido"}), 400
        if mode not in _VALID_MODES:
            return jsonify({"success": False, "error": f"mode inválido"}), 400

        ok = _svc().change_mode(camera_id, mode, lens=lens)
        if not ok:
            return jsonify({"success": False, "error": "No hay IA activa"}), 404

        return jsonify({"success": True, "data": {
            "camera_id": camera_id, "lens": lens, "mode": mode
        }}), 200
    except Exception as e:
        logger.error(f"Error cambiando modo IA: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500
