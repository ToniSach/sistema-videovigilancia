from flask import Blueprint, request, jsonify, Response
from flask_jwt_extended import jwt_required, decode_token, get_jwt_identity
import logging
import time

from ...container import get_container
from backend.app.services.permission_service import PermissionService, require_camera_permission
from backend.app.api.helpers import api_error_response, require_admin
from ...cameras.camera_manager import CameraManager
from ...workers.ffmpeg_worker import WorkerStatus
from backend.app.services.ptz_lock_service import ptz_lock_service
from backend.app.services.user_service import UserService
from ...config import settings
from ...streaming.go2rtc_manager import Go2RtcManager
from ...streaming.webrtc_signaling import webrtc_signaling, WebRTCSignalingError

cameras_bp = Blueprint("cameras", __name__, url_prefix="/api/v1/cameras")


def _enrich_live_fields(cam: dict) -> dict:
    """
    Enriquece el dict de una cámara con campos de "en vivo" cuando go2rtc está
    activo. Aditivo: si go2rtc está desactivado, devuelve el dict tal cual y los
    clientes siguen usando `rtsp_url` / MJPEG como hoy.

      - stream_url: URL RTSP del restream de go2rtc (desktop/móvil la consumen
                    con ExoPlayer/VLC, una sola conexión a la cámara).
      - webrtc_url: ruta de signaling WebRTC (si WEBRTC_ENABLED).
    """
    try:
        go2rtc = Go2RtcManager()
        if go2rtc.is_enabled() and cam.get("id") is not None:
            cam = dict(cam)
            cid = cam["id"]
            qualities = ("high", "medium", "low")
            if cam.get("is_dual_lens"):
                # Dual-lens: por lente y por calidad.
                cam["stream_url"] = go2rtc.rtsp_restream_url(cid)  # combinado (fallback)
                cam["stream_url_l1"] = go2rtc.rtsp_restream_url(cid, "l1")
                cam["stream_url_l2"] = go2rtc.rtsp_restream_url(cid, "l2")
                cam["stream_urls"] = {
                    "l1": {q: go2rtc.rtsp_restream_url(cid, "l1", q) for q in qualities},
                    "l2": {q: go2rtc.rtsp_restream_url(cid, "l2", q) for q in qualities},
                }
                # HLS por lente (la móvil lo prefiere; RTSP queda como fallback).
                cam["hls_url"] = go2rtc.hls_url(cid)
                cam["hls_url_l1"] = go2rtc.hls_url(cid, "l1")
                cam["hls_url_l2"] = go2rtc.hls_url(cid, "l2")
            else:
                cam["stream_url"] = go2rtc.rtsp_restream_url(cid)
                cam["stream_urls"] = {
                    "main": {q: go2rtc.rtsp_restream_url(cid, None, q) for q in qualities}
                }
                cam["hls_url"] = go2rtc.hls_url(cid)
            if settings.WEBRTC_ENABLED:
                cam["webrtc_url"] = f"/api/v1/cameras/{cid}/webrtc"
    except Exception:
        # El enriquecimiento NUNCA debe tumbar el listado de cámaras.
        pass
    return cam
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
    """
    Lista cámaras visibles para el usuario actual.
    - Admin: todas
    - Otros: cámaras que poseen + las que tienen permiso 'view' explícito
    """
    try:
        user_id = int(get_jwt_identity())
        service = _get_service()
        all_cameras = service.get_all_cameras()

        # Admin ve todo
        perm_service = PermissionService()
        accessible_ids = set(perm_service.get_accessible_cameras(user_id))
        filtered = [c for c in all_cameras if c.get("id") in accessible_ids]

        # Filtro de "EN VIVO": con ?live=1 se ocultan las cámaras inactivas
        # (requisito: las inactivas no deben aparecer en el en vivo). Las vistas
        # de gestión NO pasan live=1 y siguen viendo todas.
        live = request.args.get("live", "").lower() in ("1", "true", "yes")
        if live and settings.LIVE_HIDE_INACTIVE_CAMERAS:
            filtered = [c for c in filtered if c.get("is_active", False)]

        filtered = [_enrich_live_fields(c) for c in filtered]
        return jsonify({"success": True, "data": filtered})
    except Exception as e:
        return api_error_response(e, message="Error al obtener cámaras")

@cameras_bp.route("/<int:camera_id>", methods=["GET"])
@jwt_required()
@require_camera_permission("view")
def get_camera(camera_id: int):
    try:
        service = _get_service()
        camera = service.get_camera(camera_id)
        if not camera:
            return jsonify({"success": False, "error": "Cámara no encontrada"}), 404
        return jsonify({"success": True, "data": _enrich_live_fields(camera)})
    except Exception as e:
        return api_error_response(e, message="Error al obtener cámara")


@cameras_bp.route("/<int:camera_id>/sync-time", methods=["POST"])
@jwt_required()
@require_camera_permission("view")
def sync_camera_time_endpoint(camera_id: int):
    """
    Empuja la hora del servidor a la cámara por ONVIF (SetSystemDateAndTime).
    Corrige el reloj/OSD de cámaras desfasadas (XiongMai, etc.).
    """
    try:
        from ...database.repositories.camera_repository import CameraRepository
        from ...cameras.time_sync import sync_camera_time

        cam = CameraRepository().get_by_id(camera_id)
        if not cam:
            return jsonify({"success": False, "error": "Cámara no encontrada"}), 404

        ok = sync_camera_time(cam)
        if ok:
            return jsonify({"success": True, "message": "Hora sincronizada con la cámara"}), 200
        return jsonify({
            "success": False,
            "error": "La cámara no aceptó la sincronización (¿soporta ONVIF?)",
        }), 502
    except Exception as e:
        return api_error_response(e, message="Error sincronizando la hora de la cámara")


@cameras_bp.route("/<int:camera_id>/webrtc", methods=["POST"])
@jwt_required()
@require_camera_permission("view")
def webrtc_offer(camera_id: int):
    """
    Signaling WebRTC (WHEP) — PIPELINE:
      Paso 1. Cliente autenticado (JWT) con permiso 'view' envía su SDP offer.
      Paso 2. Validamos que go2rtc + WebRTC estén habilitados.
      Paso 3. Reenviamos el offer a go2rtc y devolvemos su SDP answer.
    El medio viaja peer-a-peer; el backend solo autoriza y hace de proxy.

    Acepta el offer como SDP plano (Content-Type: application/sdp) o como
    JSON {"sdp": "..."} para clientes que prefieran JSON.
    """
    try:
        go2rtc = Go2RtcManager()
        if not (go2rtc.is_enabled() and settings.WEBRTC_ENABLED):
            return jsonify({
                "success": False,
                "error": "WebRTC no disponible (go2rtc/WEBRTC_ENABLED desactivado)",
            }), 503

        offer = request.get_data(as_text=True) or ""
        if offer.lstrip().startswith("{"):
            payload = request.get_json(silent=True) or {}
            offer = payload.get("sdp", "")

        answer = webrtc_signaling.exchange(camera_id, offer)
        return Response(answer, mimetype="application/sdp")
    except WebRTCSignalingError as e:
        return jsonify({"success": False, "error": str(e)}), 502
    except Exception as e:
        return api_error_response(e, message="Error en signaling WebRTC")

@cameras_bp.route("/", methods=["POST"])
@jwt_required()
@require_admin
def add_camera():
    """Solo admin puede crear cámaras. La cámara queda asignada al admin
    como owner; usar /permissions para compartir con otros usuarios."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No se proporcionaron datos"}), 400
        # Asegurar que el owner_id quede asignado al usuario actual si no se especifica
        if "owner_id" not in data:
            data["owner_id"] = int(get_jwt_identity())
        service = _get_service()
        camera = service.add_camera(data)
        return jsonify({"success": True, "data": camera}), 201
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        return api_error_response(e, message="Error al agregar cámara")

@cameras_bp.route("/<int:camera_id>", methods=["PUT"])
@jwt_required()
@require_camera_permission("view")  # Mínimo view; service valida ownership real
def update_camera(camera_id: int):
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No se proporcionaron datos"}), 400
        # No permitir cambiar owner_id por aquí (sería escalación de privilegios)
        data.pop("owner_id", None)
        service = _get_service()
        camera = service.update_camera(camera_id, data)
        if not camera:
            return jsonify({"success": False, "error": "Cámara no encontrada"}), 404
        return jsonify({"success": True, "data": camera})
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        return api_error_response(e, message="Error al actualizar cámara")

@cameras_bp.route("/<int:camera_id>", methods=["DELETE"])
@jwt_required()
@require_admin
def delete_camera(camera_id: int):
    """Solo admin borra cámaras (operación destructiva)."""
    try:
        service = _get_service()
        deleted = service.delete_camera(camera_id)
        if not deleted:
            return jsonify({"success": False, "error": "Cámara no encontrada"}), 404
        return jsonify({"success": True, "message": "Cámara eliminada"}), 200
    except Exception as e:
        return api_error_response(e, message="Error al eliminar cámara")

@cameras_bp.route("/<int:camera_id>/toggle", methods=["PATCH"])
@jwt_required()
@require_camera_permission("view")
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
        return api_error_response(e, message="Error al cambiar estado de cámara")

@cameras_bp.route("/discover", methods=["POST"])
@jwt_required()
@require_admin
def discover_cameras():
    try:
        # Permite override de timeout/subnet_scan vía body o query string.
        body = request.get_json(silent=True) or {}
        timeout = request.args.get("timeout", type=int)
        if timeout is None:
            # Default razonable: 30s. WS-Discovery responde en <5s típicamente;
            # 30s da margen sin matar al cliente. El que quiera escanear lento
            # puede pasar timeout=120.
            timeout = body.get("timeout", 30)
        timeout = max(5, min(int(timeout), 600))  # [5, 600] s

        # subnet_scan: si True y WS-Discovery devuelve 0, escanea el subnet.
        # Es opt-in porque añade 30-60s; útil cuando el firewall bloquea multicast.
        subnet_scan = request.args.get("subnet_scan", type=int)
        if subnet_scan is None:
            subnet_scan = body.get("subnet_scan", True)
        subnet_scan = bool(subnet_scan)

        service = _get_service()
        cameras = service.discover_cameras(timeout=timeout, subnet_scan=subnet_scan)
        return jsonify({
            "success": True,
            "data": cameras,
            "count": len(cameras),
            "timeout_used": timeout,
            "subnet_scan_used": subnet_scan,
        })
    except Exception as e:
        logger.error(f"Error en descubrimiento: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500

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
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@cameras_bp.route("/<int:camera_id>/ptz/status", methods=["GET"])
@jwt_required()
@require_camera_permission("view")
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
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


# ── Presets PTZ (listar / ir / guardar) ──────────────────────────────────────
# Estos 3 endpoints faltaban: el frontend ya los llamaba (camera_control_panel.py
# usa GET /ptz/presets, POST /ptz/goto/{token} y POST /ptz/preset) pero el
# backend solo tenía `POST /ptz/<direction>`, que capturaba "presets"/"goto"/
# "preset" como direcciones inválidas y devolvía 405.

@cameras_bp.route("/<int:camera_id>/ptz/presets", methods=["GET"])
@jwt_required()
@require_camera_permission("view")
def ptz_presets_list(camera_id):
    """Lista los presets PTZ guardados en la cámara."""
    try:
        service = _get_service()
        presets = service.ptz_presets(camera_id)
        return jsonify({"success": True, "data": {"presets": presets}}), 200
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 404
    except Exception as e:
        logger.error(f"Error listando presets PTZ {camera_id}: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@cameras_bp.route("/<int:camera_id>/ptz/goto/<string:preset_token>", methods=["POST"])
@jwt_required()
@require_camera_permission("control_ptz")
def ptz_goto_preset(camera_id, preset_token):
    """Mueve la cámara al preset identificado por [preset_token]."""
    try:
        user_id = int(get_jwt_identity())
        user = UserService().get_user_by_id(user_id)
        username = user.username if user else str(user_id)

        acquired, error = ptz_lock_service.acquire_lock(camera_id, user_id, username)
        if not acquired:
            return jsonify({"success": False, "error": error}), 423
        try:
            service = _get_service()
            result = service.ptz_goto_preset(camera_id, preset_token)
            ptz_lock_service.extend_lock(camera_id, user_id, 10)
            return jsonify({"success": True, "data": result}), 200
        finally:
            ptz_lock_service.release_lock(camera_id, user_id)
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 404
    except RuntimeError as re:
        return jsonify({"success": False, "error": str(re)}), 502
    except Exception as e:
        logger.error(f"Error goto preset {camera_id}/{preset_token}: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@cameras_bp.route("/<int:camera_id>/ptz/preset", methods=["POST"])
@jwt_required()
@require_camera_permission("control_ptz")
def ptz_save_preset(camera_id):
    """Guarda la posición PTZ actual como nuevo preset.

    Body JSON: { "name": "Entrada principal" }
    """
    try:
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "name requerido"}), 400
        service = _get_service()
        result = service.ptz_save_preset(camera_id, name)
        return jsonify({"success": True, "data": result}), 201
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except RuntimeError as re:
        return jsonify({"success": False, "error": str(re)}), 502
    except Exception as e:
        logger.error(f"Error save preset {camera_id}: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500

@cameras_bp.route("/<int:camera_id>/leds/<string:state>", methods=["POST"])
@jwt_required()
@require_camera_permission("control_leds")
def led_control(camera_id, state):
    """state ∈ {on, off, auto} → controla IR-Cut filter."""
    try:
        service = _get_service()
        result = service.set_led_state(camera_id, state)
        return jsonify({"success": True, "data": result})
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        logger.error(f"Error LEDs {camera_id}: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@cameras_bp.route("/<int:camera_id>/leds/status", methods=["GET"])
@jwt_required()
@require_camera_permission("view")
def led_status(camera_id):
    try:
        from backend.app.cameras.led_controller import led_manager
        service = _get_service()
        camera = service._camera_repo.get_by_id(camera_id)
        if not camera:
            return jsonify({"success": False, "error": "Cámara no encontrada"}), 404
        ctrl = led_manager.get(camera)
        return jsonify({"success": True, "data": {
            "camera_id": camera_id, "supported": ctrl.is_supported()
        }}), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@cameras_bp.route("/<int:camera_id>/audio/talk", methods=["POST"])
@jwt_required()
@require_camera_permission("control_audio")
def audio_talk(camera_id):
    try:
        data = request.get_json(silent=True) or {}
        service = _get_service()
        result = service.audio_talk(camera_id, data)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error audio talk {camera_id}: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@cameras_bp.route("/<int:camera_id>/audio/stop", methods=["POST"])
@jwt_required()
@require_camera_permission("control_audio")
def audio_stop(camera_id):
    try:
        service = _get_service()
        result = service.audio_stop(camera_id)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"Error audio stop {camera_id}: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@cameras_bp.route("/<int:camera_id>/audio/status", methods=["GET"])
@jwt_required()
@require_camera_permission("view")
def audio_status(camera_id):
    try:
        service = _get_service()
        return jsonify({"success": True, "data": service.audio_status(camera_id)}), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@cameras_bp.route("/<int:camera_id>/audio/listen/start", methods=["POST"])
@jwt_required()
@require_camera_permission("control_audio")
def audio_listen_start(camera_id):
    """Inicia reproducción del audio de la cámara en los altavoces del host."""
    try:
        service = _get_service()
        return jsonify({"success": True, "data": service.audio_listen_start(camera_id)}), 200
    except Exception as e:
        logger.error(f"Error audio listen start {camera_id}: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@cameras_bp.route("/<int:camera_id>/audio/listen/stop", methods=["POST"])
@jwt_required()
@require_camera_permission("control_audio")
def audio_listen_stop(camera_id):
    try:
        service = _get_service()
        return jsonify({"success": True, "data": service.audio_listen_stop(camera_id)}), 200
    except Exception as e:
        logger.error(f"Error audio listen stop {camera_id}: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@cameras_bp.route("/audio/devices", methods=["GET"])
@jwt_required()
def list_audio_devices():
    """Lista los micrófonos disponibles en el host (para selector en GUI)."""
    try:
        service = _get_service()
        devices = service.list_audio_input_devices()
        return jsonify({"success": True, "data": {"devices": devices}}), 200
    except Exception as e:
        logger.error(f"Error listando audio devices: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500

# --- Diagnóstico y Streaming ---

@cameras_bp.route("/<int:camera_id>/diagnose", methods=["GET"])
@jwt_required()
@require_camera_permission("view")
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
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@cameras_bp.route("/<int:camera_id>/latency", methods=["GET"])
@jwt_required()
@require_camera_permission("view")
def stream_latency(camera_id: int):
    """
    Métricas de latencia del pipeline para diagnosticar dónde se acumula
    el delay. Query: ?stream=main|l1|l2 (default: main).

    Respuesta:
      encode      : tiempo de cv2.imencode (CPU backend, debería ser <30ms).
      frame_age   : tiempo entre captura del frame y envío al cliente.
                    <500ms → backend fluido, delay viene de cámara o red.
                    >500ms → backend tiene cuello de botella.
      dropped     : frames descartados por encoder ocupado (saludable: <1%).
      worker      : estado del FFmpegWorker + uptime + reconexiones.

    Si frame_age es bajo pero el cliente sigue viendo lag, el delay viene
    del GOP de la cámara (ajustar en su panel web a ≤30) o de la red.
    """
    try:
        # El directo ya no pasa por el backend (lo sirve go2rtc por WebRTC/RTSP),
        # así que aquí solo reportamos el estado del FFmpegWorker (que decodifica
        # para la IA). La latencia de vídeo se mide ahora en el cliente (VLC) o
        # en el dashboard de go2rtc.
        cm = CameraManager()
        worker = cm.get_worker(camera_id)
        worker_info = worker.get_status() if worker else None
        return jsonify({
            "success": True,
            "data": {
                "worker": worker_info,
                "hint": ("La latencia del directo se mide en el cliente/go2rtc. "
                         "Si hay lag, baja el GOP de la cámara o usa calidad Media/Baja."),
            }
        }), 200
    except Exception as e:
        logger.error(f"Error en latency: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500