from flask import Blueprint, request, jsonify, Response
from flask_jwt_extended import jwt_required, decode_token, get_jwt_identity
import logging
import time

from ...container import get_container
from ...streaming.mjpeg_streamer import mjpeg_streamer
from backend.app.services.permission_service import PermissionService, require_camera_permission
from backend.app.api.helpers import api_error_response, require_admin
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
        return jsonify({"success": True, "data": camera})
    except Exception as e:
        return api_error_response(e, message="Error al obtener cámara")

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
        stream = request.args.get("stream", "main")
        from backend.app.streaming.mjpeg_streamer import mjpeg_streamer as ms

        cm = CameraManager()
        worker = cm.get_worker(camera_id)
        worker_info = worker.get_status() if worker else None

        mjpeg = ms.get_latency_stats(camera_id, stream)

        # Sugerencia rápida según los datos.
        # IMPORTANTE: frame_age mide SOLO la latencia interna del backend
        # (captura → encoder → enviado al socket). NO incluye TCP buffering,
        # serialización Flask, ni latencia del cliente. Si VLC abre la misma
        # cámara y va fluido pero la app va lenta, el delay está entre el
        # socket TCP del backend y la pantalla del cliente Qt (no en la cámara).
        hint = "OK"
        age_max = (mjpeg.get("frame_age") or {}).get("max_ms")
        enc_max = (mjpeg.get("encode") or {}).get("max_ms")
        if age_max is None:
            hint = "Sin tráfico — abre el stream al menos 30s para tener muestras"
        elif age_max > 1500:
            hint = ("frame_age alto: cuello en el backend (CPU saturada por "
                    "IA/recording, o encode JPEG lento)")
        elif age_max > 500:
            hint = ("frame_age moderado: probablemente IA o grabación "
                    "compiten por CPU")
        elif enc_max and enc_max > 50:
            hint = "encode JPEG lento: revisa MJPEG_MAX_WIDTH/MJPEG_QUALITY"
        else:
            hint = ("Backend interno fluido. Si VLC va fluido y la app no, "
                    "el delay está en el cliente Qt o en el TCP entre backend "
                    "y cliente (verifica TCP_NODELAY y direct_passthrough).")

        return jsonify({
            "success": True,
            "data": {
                "mjpeg": mjpeg,
                "worker": worker_info,
                "hint": hint,
            }
        }), 200
    except Exception as e:
        logger.error(f"Error en latency: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


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
        # BUG fix: antes era f"http_{int(time.time())}_{id(request)}". El
        # `id(request)` puede colisionar entre requests concurrentes porque
        # Python recicla direcciones de memoria. Cuando dos clientes llegaban
        # con el mismo client_id, el segundo SOBREESCRIBÍA al primero en el
        # dict del MJPEGStreamer; el primero quedaba huérfano (su queue ya
        # no estaba en _clients[key], el encoder no le mandaba frames más).
        # Con uuid4 es imposible que colisionen.
        import uuid as _uuid
        client_id = f"http_{_uuid.uuid4().hex}"

        # 5. Registrar cliente en el MJPEG streamer, pasando camera_id y stream_type
        queue = mjpeg_streamer.register_client(camera_id, stream_type, client_id)

        if queue is None:
            return jsonify({
                "success": False, 
                "error": f"Límite de conexiones alcanzado para el stream {stream_type} de esta cámara (máx 5)"
            }), 503

        # 6. Retornar la respuesta de streaming
        # direct_passthrough=True   → CRÍTICO: Flask envía cada `yield` del
        #                              generator inmediatamente al socket, sin
        #                              acumular en su buffer interno. Sin esto,
        #                              Flask agrupaba varios frames antes de
        #                              hacer flush, añadiendo 100-500ms de delay.
        # X-Accel-Buffering=no      → evita que nginx/proxies bufferen la
        #                              respuesta si algún día se pone delante.
        # Connection: keep-alive    → mantiene la conexión abierta para
        #                              streaming continuo.
        return Response(
            mjpeg_streamer.generate_stream(camera_id, stream_type, client_id),
            mimetype="multipart/x-mixed-replace; boundary=frame",
            direct_passthrough=True,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            }
        )

    except Exception as e:
        logger.error(f"Error en stream de cámara {camera_id}: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor de streaming"}), 500