"""
================================================================================
MÓDULO: api.routes.auth — Blueprint REST de autenticación y sesiones
================================================================================

PROPÓSITO
    Capa HTTP del Pipeline #2 (Autenticación). Expone el alta inicial del
    administrador, el login (emisión de tokens JWT), el refresh del access
    token, el cierre de sesión (revocación) y utilidades de cuenta (cambio de
    contraseña, datos del usuario actual, QR de emparejamiento móvil).

RESPONSABILIDAD
    SOLO traducir HTTP ↔ servicio: valida forma del body, delega en AuthService
    / UserService, y serializa la respuesta al contrato JSON. No contiene lógica
    de negocio (hashing, verificación de credenciales, reglas de rol) — eso vive
    en los servicios.

DEPENDENCIAS
    services.auth_service.AuthService ... login, refresh, get_user_by_id,
                                          change_password (lógica de credenciales).
    services.user_service.UserService .. alta de usuarios y conteo (setup/registro).
    core.qr_generator.QRGenerator ...... genera el PNG del QR de conexión.
    core.jwt_blocklist.jwt_blocklist ... revocación de tokens en logout.
    core.security.get_current_user_id .. extrae user_id del JWT vigente.
    flask_jwt_extended ................. @jwt_required, creación de access tokens.
    config.settings .................... TTLs de tokens, puerto del servidor.

COMPONENTES RELACIONADOS
    main.create_app() instala los loaders JWT (expirado/inválido/revocado) y la
    blocklist; este blueprint asume que ya están activos. El blocklist es en
    memoria (se rehidrata desde BD al arrancar) — ver core/jwt_blocklist.py.

PUNTO DE ENTRADA
    Registrado en main.register_blueprints() como "auth_bp" (best-effort).
    url_prefix = /api/v1/auth.

PIPELINE(S)
    #2 Autenticación — TODAS las etapas (setup → login → refresh → logout).
    El QR (#emparejamiento móvil) sirve de puente hacia el cliente Android.

ENDPOINTS DEL BLUEPRINT
    GET  /setup-status      → ¿el sistema necesita configuración inicial?  [público]
    POST /setup             → crea el PRIMER admin + login                 [público]
    POST /register          → crea un admin adicional (sin login)          [público]
    POST /login             → autentica y emite access+refresh tokens       [público]
    POST /refresh           → renueva el access token                  [refresh JWT]
    POST /logout            → revoca el token actual                       [JWT]
    GET  /me                → datos del usuario autenticado                [JWT]
    GET  /qr                → PNG con QR de conexión para la app móvil      [JWT]
    POST /change-password   → cambia la contraseña propia                  [JWT]
================================================================================
"""
import logging
import socket
from datetime import timedelta

from flask import Blueprint, request, jsonify, make_response
from flask_jwt_extended import jwt_required, get_jwt_identity, create_access_token, get_jwt

from backend.app.services.auth_service import AuthService
from backend.app.core.security import get_current_user_id
from backend.app.core.qr_generator import QRGenerator
from backend.app.core.jwt_blocklist import jwt_blocklist
from backend.app.config import settings

logger = logging.getLogger(__name__)

# Crear blueprint de autenticación
auth_bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")

# Instancias de servicios
auth_service = AuthService()
qr_generator = QRGenerator()


@auth_bp.route("/setup-status", methods=["GET"])
def setup_status():
    """
    Propósito: ¿el sistema necesita configuración inicial? (Pipeline #2, paso 0).
        Lo consulta el cliente la PRIMERA vez para mostrar "Crear administrador"
        en lugar del login. Público (sin JWT) porque aún no hay credenciales.
    Método+Ruta: GET /api/v1/auth/setup-status
    Inputs: ninguno. Permiso: público.
    Outputs:
        200 → {"success": true, "data": {"needs_setup": <bool>}}
              needs_setup=true cuando count_users()==0.
        500 → {"success": false, "error": "Error interno del servidor"}
    Llama a: UserService.count_users().
    """
    try:
        from backend.app.services.user_service import UserService
        needs = UserService().count_users() == 0
        return jsonify({"success": True, "data": {"needs_setup": needs}}), 200
    except Exception as error:
        logger.error(f"Error en setup-status: {error}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@auth_bp.route("/setup", methods=["POST"])
def setup_admin():
    """
    Propósito: crea el PRIMER administrador y deja la sesión iniciada
        (Pipeline #2, etapa de bootstrap). Solo opera si la BD no tiene usuarios.
    Método+Ruta: POST /api/v1/auth/setup
    Inputs:
        Body JSON: { "username": str, "password": str }. Permiso: público.
    Outputs:
        201 → {"success": true, "data": <tokens+usuario>} (login automático);
              o {"success": true, "data": null} si el alta fue OK pero el login
              falló (caso degenerado: el cliente debe iniciar sesión a mano).
        400 → {"success": false, "error": <validación de UserService>}.
        403 → ya existen usuarios → "El sistema ya está configurado".
        500 → error interno.
    Excepciones: ValueError de create_user() → 400.
    Llama a: UserService.count_users()/create_user(role="admin"),
        AuthService.login().
    """
    try:
        from backend.app.services.user_service import UserService
        svc = UserService()
        if svc.count_users() > 0:
            return jsonify({
                "success": False,
                "error": "El sistema ya está configurado. Inicia sesión.",
            }), 403

        data = request.get_json() or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        try:
            svc.create_user(username, password, role="admin")
        except ValueError as ve:
            return jsonify({"success": False, "error": str(ve)}), 400

        result = auth_service.login(username, password)
        if result is None:
            # Creado pero login falló (no debería pasar): pide iniciar sesión.
            return jsonify({"success": True, "data": None}), 201
        return jsonify({"success": True, "data": result}), 201
    except Exception as error:
        logger.error(f"Error en setup admin: {error}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@auth_bp.route("/register", methods=["POST"])
def register():
    """
    Propósito: registro público de un admin adicional (Pipeline #2). A diferencia
        de /setup (solo el PRIMER admin), opera en cualquier momento y NO inicia
        sesión: el cliente debe loguearse con las nuevas credenciales.
    Método+Ruta: POST /api/v1/auth/register
    Inputs:
        Body JSON: { "username": str, "password": str }. Permiso: público.
    Outputs:
        201 → {"success": true, "data": {"username": str}}.
        400 → faltan campos, o ValueError de create_user (usuario ya existe).
        500 → error interno.
    Llama a: UserService.create_user(role="admin").
    """
    try:
        from backend.app.services.user_service import UserService
        svc = UserService()

        data = request.get_json() or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""

        if not username or not password:
            return jsonify({
                "success": False,
                "error": "Username y password son requeridos",
            }), 400

        try:
            svc.create_user(username, password, role="admin")
        except ValueError as ve:
            return jsonify({"success": False, "error": str(ve)}), 400

        return jsonify({
            "success": True,
            "data": {"username": username},
        }), 201
    except Exception as error:
        logger.error(f"Error en registro de usuario: {error}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@auth_bp.route("/login", methods=["POST"])
def login():
    """
    Propósito: autentica al usuario y emite los tokens JWT (Pipeline #2, etapa
        principal). El access token lleva el claim role; el refresh permite
        renovarlo sin re-introducir credenciales.
    Método+Ruta: POST /api/v1/auth/login
    Inputs:
        Body JSON: { "username": str, "password": str }. Permiso: público.
    Outputs (formato CRUDO de AuthService, sin envoltorio success/data):
        200 → {"access_token", "refresh_token", "user": {...}} (lo que devuelva
              AuthService.login()).
        400 → {"error": "..."} body ausente o campos vacíos.
        401 → {"error": "Credenciales inválidas"} (login devolvió None).
        500 → {"error": "Error interno del servidor"}.
    Llama a: AuthService.login().
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "Se requiere body JSON"}), 400
        
        username = data.get("username", "").strip()
        password = data.get("password", "")
        
        if not username or not password:
            return jsonify({"error": "Username y password son requeridos"}), 400
        
        result = auth_service.login(username, password)
        
        if result is None:
            return jsonify({"error": "Credenciales inválidas"}), 401
        
        return jsonify(result), 200
        
    except Exception as error:
        logger.error(f"Error en endpoint login: {error}")
        return jsonify({"error": "Error interno del servidor"}), 500


@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    """
    Propósito: renueva el access token a partir de un refresh token válido
        (Pipeline #2, mantenimiento de sesión). Revalida que el usuario siga
        existiendo y activo antes de re-emitir (un usuario desactivado pierde
        acceso aunque conserve el refresh).
    Método+Ruta: POST /api/v1/auth/refresh
    Inputs:
        Header: Authorization: Bearer <refresh_token>. Permiso: @jwt_required(refresh=True).
    Outputs:
        200 → {"access_token": <nuevo>} (claim role + TTL de settings).
        401 → refresh inválido/expirado, o usuario inexistente/inactivo.
    Llama a: AuthService.get_user_by_id(), create_access_token().
    """
    try:
        identity = get_jwt_identity()
        user_id = int(identity)
        
        # Verificar que usuario sigue existiendo y activo
        user = auth_service.get_user_by_id(user_id)
        if not user or not user.is_active:
            return jsonify({"error": "Usuario no válido o inactivo"}), 401

        # PRESERVAR los claims de dispositivo del refresh token. Las notificaciones
        # son POR DISPOSITIVO y el scope se deduce del claim device_id del access
        # token; si al refrescar se perdía device_id/src, tras ~15 min el móvil
        # caía a scope "de cuenta" y dejaba de respetar sus prefs por dispositivo.
        refresh_claims = get_jwt()
        additional_claims = {"role": user.role}
        if refresh_claims.get("device_id") is not None:
            additional_claims["device_id"] = refresh_claims["device_id"]
        if refresh_claims.get("src"):
            additional_claims["src"] = refresh_claims["src"]

        # Crear nuevo access token
        new_token = create_access_token(
            identity=identity,
            additional_claims=additional_claims,
            expires_delta=timedelta(minutes=settings.JWT_ACCESS_TOKEN_MINUTES)
        )
        
        return jsonify({"access_token": new_token}), 200
        
    except Exception as error:
        logger.error(f"Error en refresh token: {error}")
        return jsonify({"error": "No se pudo refrescar token"}), 401


@auth_bp.route("/logout", methods=["POST"])
@jwt_required()
def logout():
    """
    Propósito: cierra sesión revocando el token actual (Pipeline #2, cierre).
        El jti se añade al blocklist en memoria hasta su exp natural; cualquier
        request posterior con ese token devuelve 401 (loader revoked en main).
    Método+Ruta: POST /api/v1/auth/logout
    Inputs: Header Authorization: Bearer <access_token>. Permiso: @jwt_required().
    Outputs:
        200 → {"message": "Sesión cerrada correctamente"} (siempre, incluso si el
              token no traía jti/exp — es idempotente).
    Llama a: jwt_blocklist.revoke(jti, exp).
    """
    claims = get_jwt()
    jti = claims.get("jti")
    exp = claims.get("exp")
    if jti and exp:
        jwt_blocklist.revoke(jti, exp)
        logger.info(f"Token jti={jti[:8]}... revocado (blocklist size={jwt_blocklist.size()})")
    return jsonify({"message": "Sesión cerrada correctamente"}), 200


@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def get_current_user():
    """
    Propósito: datos del usuario autenticado (lo usa la UI tras login para
        pintar nombre/rol). Pipeline #2.
    Método+Ruta: GET /api/v1/auth/me
    Inputs: Header Authorization: Bearer. Permiso: @jwt_required().
    Outputs:
        200 → user.to_dict() (formato crudo, sin envoltorio success/data).
        404 → token válido pero usuario borrado (caso raro).
        401 → fallo de autenticación.
    Llama a: AuthService.get_user_by_id() vía get_current_user_id().
    """
    try:
        user_id = get_current_user_id()
        user = auth_service.get_user_by_id(user_id)
        
        if not user:
            return jsonify({"error": "Usuario no encontrado"}), 404
        
        return jsonify(user.to_dict()), 200
        
    except Exception as error:
        logger.error(f"Error al obtener usuario actual: {error}")
        return jsonify({"error": "Error de autenticación"}), 401


@auth_bp.route("/qr", methods=["GET"])
@jwt_required()
def get_connection_qr():
    """
    Propósito: genera un QR de emparejamiento para la app móvil (Pipeline #2 →
        puente al cliente Android). Codifica usuario, IP+puerto del servidor y el
        token de acceso ACTUAL (tomado del propio header Authorization), de modo
        que el móvil quede autenticado al escanearlo.
    Método+Ruta: GET /api/v1/auth/qr
    Inputs: Header Authorization: Bearer. Permiso: @jwt_required().
    Outputs:
        200 → image/png (bytes del QR), Cache-Control: no-store.
        404 → usuario no válido.
        500 → error generando la imagen.
    Notas: la IP se resuelve por socket.gethostbyname(hostname); si falla cae a
        127.0.0.1 (el QR solo servirá en local).
    Llama a: QRGenerator.generate_connection_qr().
    """
    try:
        user_id = get_current_user_id()
        user = auth_service.get_user_by_id(user_id)
        
        if not user:
            return jsonify({"error": "Usuario no válido"}), 404
        
        # Obtener IP del servidor
        try:
            server_ip = socket.gethostbyname(socket.gethostname())
        except socket.gaierror:
            server_ip = "127.0.0.1"
            logger.warning("No se pudo determinar IP automáticamente, usando localhost")
        
        # Obtener token actual del header (para incluir en QR)
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "") if "Bearer " in auth_header else ""
        
        # Generar QR
        qr_bytes = qr_generator.generate_connection_qr(
            username=user.username,
            server_ip=server_ip,
            port=settings.SERVER_PORT,
            token=token
        )
        
        # Retornar como imagen PNG
        response = make_response(qr_bytes)
        response.headers.set("Content-Type", "image/png")
        response.headers.set("Cache-Control", "no-cache, no-store, must-revalidate")
        return response
        
    except Exception as error:
        logger.error(f"Error al generar QR: {error}")
        return jsonify({"error": "Error al generar código QR"}), 500


@auth_bp.route("/change-password", methods=["POST"])
@jwt_required()
def change_password():
    """
    Propósito: cambia la contraseña del usuario autenticado (Pipeline #2).
    Método+Ruta: POST /api/v1/auth/change-password
    Inputs:
        Body JSON: { "old_password": str, "new_password": str }.
        Header Authorization: Bearer. Permiso: @jwt_required().
    Outputs:
        200 → {"message": "Contraseña actualizada correctamente"}.
        400 → body ausente, campos vacíos, o ValueError (nueva contraseña no
              cumple política) → {"error": <detalle>}.
        401 → old_password incorrecta.
        500 → error interno.
    Llama a: AuthService.change_password(user_id, old, new).
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "Se requiere body JSON"}), 400
        
        old_password = data.get("old_password", "")
        new_password = data.get("new_password", "")
        
        if not old_password or not new_password:
            return jsonify({"error": "old_password y new_password son requeridos"}), 400
        
        user_id = get_current_user_id()
        success = auth_service.change_password(user_id, old_password, new_password)
        
        if not success:
            return jsonify({"error": "Contraseña anterior incorrecta"}), 401
        
        return jsonify({"message": "Contraseña actualizada correctamente"}), 200
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as error:
        logger.error(f"Error al cambiar contraseña: {error}")
        return jsonify({"error": "Error interno"}), 500
