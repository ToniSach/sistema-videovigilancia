"""
Blueprint de API para autenticación y gestión de sesiones.
Incluye login, refresh, logout y generación de QR de conexión.
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


@auth_bp.route("/login", methods=["POST"])
def login():
    """
    Endpoint de autenticación de usuarios.
    Recibe credenciales y retorna tokens JWT.
    
    Request Body:
        {
            "username": "string",
            "password": "string"
        }
        
    Returns:
        200: Tokens y datos de usuario
        401: Credenciales inválidas
        400: Datos faltantes
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
    Endpoint para refrescar token de acceso usando refresh token.
    Requiere header Authorization con refresh token válido.
    
    Returns:
        200: Nuevo access_token
        401: Refresh token inválido o expirado
    """
    try:
        identity = get_jwt_identity()
        user_id = int(identity)
        
        # Verificar que usuario sigue existiendo y activo
        user = auth_service.get_user_by_id(user_id)
        if not user or not user.is_active:
            return jsonify({"error": "Usuario no válido o inactivo"}), 401
        
        # Crear nuevo access token
        new_token = create_access_token(
            identity=identity,
            additional_claims={"role": user.role},
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
    Cierra sesión revocando el token actual.

    El jti del token se añade al blocklist en memoria hasta su exp natural.
    Tokens revocados devuelven 401 en cualquier request posterior.
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
    Obtiene información del usuario autenticado actual.
    
    Returns:
        200: Datos del usuario
        404: Usuario no encontrado (caso raro, token válido pero usuario borrado)
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
    Genera código QR para conexión rápida desde app móvil.
    Incluye servidor, puerto y token de acceso actual.
    
    Returns:
        200: Imagen PNG del código QR
        500: Error al generar imagen
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
    Cambia la contraseña del usuario autenticado.
    
    Request Body:
        {
            "old_password": "string",
            "new_password": "string"
        }
        
    Returns:
        200: Contraseña cambiada
        400: Datos inválidos
        401: Contraseña anterior incorrecta
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
