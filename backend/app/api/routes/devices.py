"""
================================================================================
MÓDULO: api.routes.devices — Registro y ciclo de vida de dispositivos móviles
================================================================================

PROPÓSITO
    Blueprint REST del onboarding y sesión de la app móvil (CamLink): alta de
    un MobileDevice tras escanear el QR de vinculación, emisión/renovación de
    JWT propios del móvil, logout (revocación + baja) y listado/baja de
    dispositivos del usuario.

DECISIÓN CLAVE (no duplicar autenticación)
    Tras vincular vía QR el móvil recibe JWT REALES (no tokens custom): así
    puede usar TODOS los endpoints existentes ya protegidos por @jwt_required()
    (cameras/ptz, leds, audio, recordings...) sin lógica de auth aparte. Los
    tokens llevan claims extra: device_id (distingue móvil de desktop y permite
    revocar por dispositivo) y src="mobile". Vida más larga que desktop
    (access 24h, refresh ≥30 días) para no relogear todo el día.

RESPONSABILIDAD
    Contrato HTTP + validación/saneo de entrada (UUID y nombre de dispositivo)
    + emisión de tokens. La persistencia del dispositivo vive en DeviceService;
    el consumo del link_token de un solo uso vive en QRService; la revocación
    en jwt_blocklist.

DEPENDENCIAS
    services.device_service.DeviceService  alta/baja/listado de MobileDevice
    services.qr_service.QRService .......... consume el link_token (5 min)
    database.connection.db_manager ......... lee User/MobileDevice frescos de BD
    core.jwt_blocklist.jwt_blocklist ....... revoca el JWT en logout
    flask_jwt_extended ..................... emisión de access/refresh tokens
    api.helpers.api_error_response ......... error JSON sin filtrar stack trace

COMPONENTES RELACIONADOS
    routes/qr.py + routes/auth.py(/qr)  generan el QR/link_token que aquí se consume
    routes/mobile.py ................... endpoints de datos del cliente móvil
    routes/telegram_link.py ............ otro canal de notificación del usuario
    database.models.MobileDevice ....... modelo ORM (.to_dict() en /my-devices)

PUNTO DE ENTRADA
    Registrado en main.create_app() vía safe_register(devices_bp).
    Prefijo: /api/v1/devices. Best-effort.

PIPELINE(S)
    Pipeline #2 (Autenticación) — variante móvil: emisión/renovación/revocación
    de sesión por dispositivo. Habilita además al móvil como destino del
    Pipeline #13 (Notificaciones push/FCM).

ENDPOINTS
    POST   /register      → register_device()       [público, link_token] alta + JWT
    POST   /refresh       → refresh_device_token()   [refresh JWT] renueva access
    POST   /logout        → logout_device()          [auth] revoca + desactiva
    GET    /my-devices    → get_my_devices()         [auth] lista dispositivos activos
    DELETE /<device_id>   → unregister_device()      [auth+ownership] baja dispositivo

HELPERS INTERNOS
    _validate_device_uuid / _validate_device_name  saneo de entrada del QR.
    _issue_mobile_tokens  emite el par access/refresh con claims de móvil.
================================================================================
"""
import logging
import re
import uuid as _uuid
from datetime import timedelta

from flask import Blueprint, request, jsonify
from flask_jwt_extended import (
    jwt_required, get_jwt_identity, get_jwt,
    create_access_token, create_refresh_token,
)

from backend.app.database.connection import db_manager
from backend.app.database.models import MobileDevice, User
from backend.app.services.device_service import DeviceService
from backend.app.config import settings
from backend.app.core.jwt_blocklist import jwt_blocklist
from backend.app.api.helpers import api_error_response

logger = logging.getLogger(__name__)

devices_bp = Blueprint("devices", __name__, url_prefix="/api/v1/devices")
device_service = DeviceService()


# ---- Validadores -----------------------------------------------------------

_DEVICE_NAME_RE = re.compile(r"^[\w\s\-\._]{1,100}$", re.UNICODE)


def _validate_device_uuid(raw: str) -> str | None:
    """Devuelve UUID en formato canónico o None si inválido."""
    if not raw or not isinstance(raw, str) or len(raw) > 64:
        return None
    try:
        return str(_uuid.UUID(raw))
    except (ValueError, AttributeError):
        return None


def _validate_device_name(raw) -> str:
    """Sanea device_name; si inválido devuelve genérico."""
    if not raw or not isinstance(raw, str):
        return "Dispositivo móvil"
    raw = raw.strip()[:100]
    if not _DEVICE_NAME_RE.match(raw):
        return "Dispositivo móvil"
    return raw


def _issue_mobile_tokens(user_id: int, role: str, device_id: int) -> tuple[str, str]:
    """
    Emite JWT REALES para un dispositivo móvil.
    El claim 'device_id' permite distinguir tokens de móvil vs desktop y
    revocar todos los tokens de un dispositivo si se hace logout/baja.
    Tiempo de vida más largo que desktop para no estar logueando todo el día.
    """
    additional_claims = {"role": role, "device_id": device_id, "src": "mobile"}
    access = create_access_token(
        identity=str(user_id),
        additional_claims=additional_claims,
        expires_delta=timedelta(days=1),  # 24h en móvil (vs 15 min en desktop)
    )
    refresh = create_refresh_token(
        identity=str(user_id),
        additional_claims=additional_claims,
        expires_delta=timedelta(days=max(settings.JWT_REFRESH_TOKEN_DAYS, 30)),
    )
    return access, refresh


# ---- Endpoints -------------------------------------------------------------

@devices_bp.route("/register", methods=["POST"])
def register_device():
    """
    Alta de dispositivo móvil tras escanear el QR de vinculación.

    Método+Ruta: POST /api/v1/devices/register
    Permiso: PÚBLICO (sin JWT). La autorización es el link_token de un solo uso
        (5 min de validez) generado por el desktop/QR.
    Inputs (body JSON):
        link_token (str, REQUERIDO; uuid generado por desktop, se consume),
        device_uuid (str, REQUERIDO; UUID único del móvil, validado a canónico),
        device_name (str, opcional; saneado, default "Dispositivo móvil"),
        platform ("android"|"ios"|"unknown"; cualquier otro → "unknown").
    Outputs:
        201 {success:true, data:{device_id, access_token, refresh_token,
             server_url, user:{id, username, role}}} (JWT reales para el móvil).
        400 si falta link_token o device_uuid inválido.
        401 si link_token inválido/expirado o usuario asociado no válido.
        5xx vía api_error_response ante fallo interno.
    Llama a: QRService.consume_link_token() + DeviceService.register_device()
        + _issue_mobile_tokens().
    """
    try:
        data = request.get_json(silent=True) or {}
        link_token = data.get("link_token", "").strip()
        if not link_token:
            return jsonify({"success": False, "error": "link_token requerido"}), 400

        device_uuid = _validate_device_uuid(data.get("device_uuid", ""))
        if not device_uuid:
            return jsonify({
                "success": False,
                "error": "device_uuid inválido (debe ser UUID válido)"
            }), 400

        device_name = _validate_device_name(data.get("device_name"))
        platform = data.get("platform", "unknown")
        if platform not in ("android", "ios", "unknown"):
            platform = "unknown"

        # Validar y consumir link_token
        from backend.app.services.qr_service import QRService
        qr_service = QRService()
        user_id = qr_service.consume_link_token(link_token)
        if not user_id:
            return jsonify({
                "success": False,
                "error": "link_token inválido o expirado"
            }), 401

        # Registrar/actualizar dispositivo en BD (el viejo token custom queda
        # almacenado pero ya no lo usamos; lo dejamos por compat con DB schema).
        device, _legacy_access, _legacy_refresh = device_service.register_device(
            user_id=user_id,
            device_uuid=device_uuid,
            device_name=device_name,
            platform=platform,
        )

        # Obtener role del usuario para meterlo en el JWT
        with db_manager.get_session() as session:
            user = session.get(User, user_id)
            if not user or not user.is_active:
                return jsonify({
                    "success": False,
                    "error": "Usuario asociado no válido"
                }), 401
            role = user.role
            username = user.username

        # Emitir JWT reales — el móvil los usará en todos los endpoints
        access_token, refresh_token = _issue_mobile_tokens(user_id, role, device.id)

        logger.info(
            f"Dispositivo móvil registrado: {device_uuid[:8]}... "
            f"para user={user_id} ({username})"
        )

        return jsonify({
            "success": True,
            "data": {
                "device_id": device.id,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "server_url": request.host_url.rstrip("/"),
                "user": {
                    "id": user_id,
                    "username": username,
                    "role": role,
                },
            }
        }), 201

    except Exception as e:
        return api_error_response(e, message="Error registrando dispositivo")


@devices_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh_device_token():
    """
    Renueva el access_token del móvil usando el refresh_token JWT.

    Método+Ruta: POST /api/v1/devices/refresh
    Permiso: @jwt_required(refresh=True) — el refresh_token DEBE venir en header
        Authorization: Bearer ... y llevar claim device_id (es de móvil).
    Inputs: ninguno en body; identidad y device_id salen del token.
    Outputs:
        200 {success:true, data:{access_token}} (nuevo access, vida 24h).
        400 si el token no es de dispositivo móvil (sin device_id).
        401 si el usuario está inactivo o el dispositivo fue desactivado.
    Llama a: db_manager (revalida User/MobileDevice) + create_access_token.
    """
    try:
        identity = get_jwt_identity()
        user_id = int(identity)
        claims = get_jwt() or {}
        device_id = claims.get("device_id")
        role = claims.get("role", "user")

        if not device_id:
            return jsonify({
                "success": False,
                "error": "Token no es de dispositivo móvil"
            }), 400

        # Verificar que el usuario sigue activo y el dispositivo no fue revocado
        with db_manager.get_session() as session:
            user = session.get(User, user_id)
            if not user or not user.is_active:
                return jsonify({"success": False, "error": "Usuario inactivo"}), 401
            device = session.get(MobileDevice, device_id)
            if not device or not device.is_active:
                return jsonify({"success": False, "error": "Dispositivo desactivado"}), 401
            role = user.role  # tomar role fresco de BD

        new_access = create_access_token(
            identity=identity,
            additional_claims={"role": role, "device_id": device_id, "src": "mobile"},
            expires_delta=timedelta(days=1),
        )

        return jsonify({
            "success": True,
            "data": {"access_token": new_access}
        }), 200

    except Exception as e:
        return api_error_response(e, message="Error renovando token", status=401)


@devices_bp.route("/logout", methods=["POST"])
@jwt_required()
def logout_device():
    """
    Cierra sesión del dispositivo móvil.

    Método+Ruta: POST /api/v1/devices/logout
    Permiso: JWT válido (access del móvil).
    Efectos: revoca el JWT actual en jwt_blocklist (hasta su exp natural) y, si
        el token lleva device_id, marca el MobileDevice como is_active=False
        (futuros refresh fallarán con 401).
    Inputs: ninguno (jti/exp/device_id salen de los claims).
    Outputs:
        200 {success:true, message}
        5xx vía api_error_response ante fallo interno.
    Llama a: jwt_blocklist.revoke() + DeviceService.deactivate_device().
    """
    try:
        claims = get_jwt() or {}
        jti = claims.get("jti")
        exp = claims.get("exp")
        device_id = claims.get("device_id")

        if jti and exp:
            jwt_blocklist.revoke(jti, exp)

        if device_id:
            device_service.deactivate_device(int(device_id))
            logger.info(f"Dispositivo móvil {device_id} desactivado por logout")

        return jsonify({"success": True, "message": "Sesión móvil cerrada"}), 200

    except Exception as e:
        return api_error_response(e, message="Error cerrando sesión móvil")


@devices_bp.route("/my-devices", methods=["GET"])
@jwt_required()
def get_my_devices():
    """
    Lista los dispositivos móviles activos del usuario autenticado.

    Método+Ruta: GET /api/v1/devices/my-devices
    Permiso: JWT válido (cualquier rol).
    Inputs: ninguno.
    Outputs:
        200 {success:true, data:[MobileDevice.to_dict(), ...]}
        5xx vía api_error_response ante fallo interno.
    Llama a: DeviceService.get_user_devices(user_id).
    """
    try:
        user_id = int(get_jwt_identity())
        devices = device_service.get_user_devices(user_id)
        return jsonify({
            "success": True,
            "data": [d.to_dict() for d in devices]
        }), 200
    except Exception as e:
        return api_error_response(e, message="Error obteniendo dispositivos")


@devices_bp.route("/<int:device_id>", methods=["DELETE"])
@jwt_required()
def unregister_device(device_id):
    """
    Desactiva un dispositivo concreto del usuario (baja remota).

    Método+Ruta: DELETE /api/v1/devices/<device_id>
    Permiso: JWT válido + OWNERSHIP (solo el dueño puede dar de baja sus
        dispositivos; si no le pertenece → 403).
    Inputs: Path device_id (int).
    Outputs:
        200 {success:true}
        403 {success:false, error:"No autorizado"} si no es del usuario.
        404 {success:false, error:"No encontrado"} si no existe.
        5xx vía api_error_response ante fallo interno.
    Nota: cualquier JWT ya emitido para ese dispositivo sigue válido hasta su
        exp; el cliente debe descartarlo (no se revoca por jti aquí).
    Llama a: DeviceService.get_user_devices() (ownership) + deactivate_device().
    """
    try:
        user_id = int(get_jwt_identity())
        devices = device_service.get_user_devices(user_id)

        if not any(d.id == device_id for d in devices):
            return jsonify({"success": False, "error": "No autorizado"}), 403

        if device_service.deactivate_device(device_id):
            return jsonify({"success": True}), 200
        return jsonify({"success": False, "error": "No encontrado"}), 404
    except Exception as e:
        return api_error_response(e, message="Error desactivando dispositivo")
