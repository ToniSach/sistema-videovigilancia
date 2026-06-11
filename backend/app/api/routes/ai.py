"""
================================================================================
MÓDULO: api.routes.ai — Blueprint REST de control de IA (YOLOv8)
================================================================================

PROPÓSITO
    Capa HTTP del Pipeline #9 (IA). Permite activar/desactivar la inferencia
    YOLOv8 por cámara y por lente (main/l1/l2), consultar su estado y cambiar el
    modo de inferencia (low_cpu / high_quality) sin reiniciar.

RESPONSABILIDAD
    SOLO contrato HTTP: valida lens/mode contra las listas blancas, delega en
    AIService y mapea sus excepciones a códigos HTTP con error_code. Toda la
    lógica (gating por movimiento, pool de modelos, exclusividad de lente) vive
    en AIService / AIScheduler / YLOModelPool.

EXCLUSIVIDAD DE CÁMARA
    Solo UNA cámara corre YOLO a la vez (selector AI_CAMERA_ID). No asumir IA
    por-cámara simultánea: activar otra puede chocar con la regla de exclusividad.

DEPENDENCIAS
    services.ai_service.AIService (vía DI container) → activate_ai/deactivate_ai/
        change_mode/is_active/get_ai_status. _svc() lo resuelve y lanza
        RuntimeError si no está registrado.
    flask_jwt_extended.@jwt_required → todos los endpoints exigen JWT.

COMPONENTES RELACIONADOS
    processing.ai.model_pool.check_dependencies → si torch/ultralytics faltan,
        AIService.activate_ai lanza un RuntimeError que aquí se traduce a
        503 + error_code AI_DEPENDENCIES_MISSING (con el comando pip exacto).
    AIScheduler (worker), InferenceQueue, YLOModelPool — capa de inferencia real.

PUNTO DE ENTRADA
    Registrado en main.register_blueprints() como "ai_bp". url_prefix=/api/v1/ai.
    get_camera_status y get_status están EXENTOS del rate limiter (polling UI).

PIPELINE(S)
    #9 IA — activación/desactivación/estado/modo.

ENDPOINTS DEL BLUEPRINT
    GET   /status                  → estado global de IA                  [JWT]
    GET   /<camera_id>             → estado por lente de una cámara        [JWT]
    POST  /<camera_id>/activate    → activa IA (body: lens, mode)          [JWT]
    POST  /<camera_id>/deactivate  → desactiva IA (body: lens)             [JWT]
    PATCH /<camera_id>/mode        → cambia modo de inferencia (body)      [JWT]

CÓDIGOS DE ERROR PROPIOS (campo error_code)
    AI_DEPENDENCIES_MISSING (503) → falta torch/ultralytics (mensaje = comando pip).
    AI_LENS_CONFLICT        (409) → otra lente/cámara ya ocupa el slot de IA.
================================================================================
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
    """
    Propósito: estado GLOBAL de la IA (qué cámara/lente corre YOLO, modo, modelo).
        Lo poletea la UI (exento del rate limiter). Pipeline #9.
    Método+Ruta: GET /api/v1/ai/status
    Inputs: ninguno. Permiso: @jwt_required().
    Outputs:
        200 → {"success": true, "data": <AIService.get_ai_status()>}.
        500 → error interno.
    Llama a: AIService.get_ai_status().
    """
    try:
        return jsonify({"success": True, "data": _svc().get_ai_status()}), 200
    except Exception as e:
        logger.error(f"Error obteniendo status IA: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@ai_bp.route("/<int:camera_id>", methods=["GET"])
@jwt_required()
def get_camera_status(camera_id: int):
    """
    Propósito: estado de IA por lente de UNA cámara (la UI lo poletea para pintar
        el toggle main/l1/l2). Pipeline #9.
    Método+Ruta: GET /api/v1/ai/<camera_id>
    Inputs: path camera_id. Permiso: @jwt_required().
    Outputs:
        200 → {"success": true, "data": {"camera_id", "main": bool, "l1": bool,
              "l2": bool}} (activo por lente).
        500 → error interno.
    Llama a: AIService.is_active(camera_id, lens) por cada lente.
    """
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
    """
    Propósito: activa YOLOv8 en una cámara/lente (Pipeline #9, etapa de arranque
        de la inferencia). El chequeo de dependencias se hace UPFRONT en
        AIService.activate_ai para que el fallo aflore aquí (503) en vez de morir
        callado en el worker.
    Método+Ruta: POST /api/v1/ai/<camera_id>/activate
    Inputs:
        Body JSON: { "lens": "main"|"l1"|"l2" (def main),
                     "mode": "low_cpu"|"high_quality" (def low_cpu) }.
        Permiso: @jwt_required().
    Outputs:
        200 → {"success": true, "data": {"camera_id", "lens", "mode"}}.
        400 → lens/mode inválido, o no se pudo activar (cámara inactiva, o se
              pidió l1/l2 en una cámara que no es dual-lens), o ValueError.
        409 → error_code AI_LENS_CONFLICT: otra lente/cámara ya ocupa el slot.
        503 → error_code AI_DEPENDENCIES_MISSING: falta torch/ultralytics
              (el campo error trae el comando pip para instalarlas).
        500 → error interno.
    Excepciones: RuntimeError se discrimina por su texto ("lente"/"lens" → 409;
        resto → 503); ValueError → 400.
    Llama a: AIService.activate_ai(camera_id, lens, mode).
    """
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
    """
    Propósito: detiene la inferencia YOLO de una cámara/lente y libera el slot
        (Pipeline #9, parada). Pasos del pipeline IA: revertir activate().
    Método+Ruta: POST /api/v1/ai/<camera_id>/deactivate
    Inputs:
        Body JSON: { "lens": "main"|"l1"|"l2" (def main) }. Permiso: @jwt_required().
    Outputs:
        200 → {"success": true, "data": {"camera_id", "lens"}}.
        400 → lens inválido.
        404 → no había IA activa en esa cámara/lente.
        500 → error interno.
    Llama a: AIService.deactivate_ai(camera_id, lens).
    """
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
    """
    Propósito: cambia el modo de inferencia (low_cpu / high_quality) de una IA YA
        activa, sin desactivarla (Pipeline #9, ajuste en caliente).
    Método+Ruta: PATCH /api/v1/ai/<camera_id>/mode
    Inputs:
        Body JSON: { "lens": "main"|"l1"|"l2" (def main),
                     "mode": "low_cpu"|"high_quality" (requerido) }.
        Permiso: @jwt_required().
    Outputs:
        200 → {"success": true, "data": {"camera_id", "lens", "mode"}}.
        400 → lens o mode inválido.
        404 → no hay IA activa que reconfigurar.
        500 → error interno.
    Llama a: AIService.change_mode(camera_id, mode, lens).
    """
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
