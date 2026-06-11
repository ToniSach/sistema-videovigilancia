"""
================================================================================
MÓDULO: api.routes.users — CRUD de usuarios (capa HTTP)
================================================================================

PROPÓSITO
    Blueprint REST de administración de cuentas de usuario: listar, crear,
    actualizar y dar de baja (soft delete) usuarios, más el endpoint de
    "usuario actual" que agrega las cámaras accesibles. Es la cara HTTP del
    sistema de identidades sobre el que se apoya toda la autorización.

RESPONSABILIDAD
    SOLO contrato HTTP: validar el body mínimo, exigir rol admin donde
    corresponde y traducir el resultado del servicio a JSON
    {success, data|error} + código. Toda la lógica de negocio (hashing de
    contraseñas, unicidad de username, soft delete) vive en UserService; la
    resolución de cámaras compartidas vive en PermissionService.

DEPENDENCIAS
    services.user_service.UserService ......... CRUD real de usuarios
    services.permission_service.PermissionService  cámaras accesibles (/me)
    core.security.admin_required .............. decorator que exige role=admin
    flask_jwt_extended ........................ @jwt_required + identidad JWT

COMPONENTES RELACIONADOS
    routes/auth.py ...... login/registro/cambio de contraseña (sesión)
    routes/permissions.py  concede/revoca acceso por cámara (UserCameraPermission)
    database.models.User  modelo ORM (.to_dict() serializa la respuesta)

PUNTO DE ENTRADA
    Registrado en main.create_app() vía safe_register(users_bp). Prefijo
    de ruta: /api/v1/users. El registro es best-effort (un fallo de import se
    loguea y NO aborta el arranque).

PIPELINE(S)
    Pipeline #2 (Autenticación/identidad) — etapa de administración de cuentas.
    No participa en captura/medios; es transversal: las cuentas que gestiona
    son las dueñas de las cámaras y eventos del resto de pipelines.

ENDPOINTS
    GET    /api/v1/users/            → get_users()       [admin] lista usuarios activos
    POST   /api/v1/users/            → create_user()     [admin] crea usuario
    PUT    /api/v1/users/<user_id>   → update_user()     [admin] actualiza usuario
    DELETE /api/v1/users/<user_id>   → delete_user()     [admin] baja (soft delete)
    GET    /api/v1/users/me          → get_current_user() usuario actual + cámaras

NOTA DE SEGURIDAD
    Todos los handlers atrapan Exception y devuelven un 500 genérico sin
    filtrar el detalle al cliente (el stack queda en logs del servicio).
================================================================================
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from backend.app.services.user_service import UserService
from backend.app.services.permission_service import PermissionService
from backend.app.core.security import admin_required

users_bp = Blueprint("users", __name__, url_prefix="/api/v1/users")
user_service = UserService()
permission_service = PermissionService()


@users_bp.route("/", methods=["GET"])
@jwt_required()
@admin_required
def get_users():
    """
    Lista todos los usuarios activos del sistema (panel de administración).

    Método+Ruta: GET /api/v1/users/
    Permiso: JWT válido + role=admin (@jwt_required + @admin_required).
    Inputs: ninguno.
    Outputs:
        200 {success:true, data:[User.to_dict(), ...]}  (filtra is_active=False)
        500 {success:false, error} ante fallo interno.
    Llama a: UserService.get_all_users().
    """
    try:
        users = user_service.get_all_users()
        return jsonify({
            "success": True,
            "data": [u.to_dict() for u in users if u.is_active]
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@users_bp.route("/", methods=["POST"])
@jwt_required()
@admin_required
def create_user():
    """
    Crea un nuevo usuario.

    Método+Ruta: POST /api/v1/users/
    Permiso: JWT válido + role=admin.
    Inputs (body JSON):
        username (str, requerido), password (str, requerido),
        role (str, opcional; default "user" — admin|user).
    Outputs:
        201 {success:true, data:User.to_dict()}
        400 {success:false, error} si faltan campos o ValueError del servicio
            (p.ej. username duplicado).
        500 ante fallo interno.
    Excepciones: ValueError del servicio → 400 con su mensaje; resto → 500.
    Llama a: UserService.create_user(username, password, role).
    """
    try:
        data = request.get_json()
        if not data or "username" not in data or "password" not in data:
            return jsonify({"success": False, "error": "Username y password requeridos"}), 400
        
        user = user_service.create_user(
            username=data["username"],
            password=data["password"],
            role=data.get("role", "user")
        )
        return jsonify({
            "success": True,
            "data": user.to_dict()
        }), 201
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@users_bp.route("/<int:user_id>", methods=["PUT"])
@jwt_required()
@admin_required
def update_user(user_id):
    """
    Actualiza campos de un usuario existente.

    Método+Ruta: PUT /api/v1/users/<user_id>
    Permiso: JWT válido + role=admin.
    Inputs:
        Path: user_id (int).
        Body JSON: campos a modificar (se reenvían como **kwargs al servicio,
        p.ej. role, is_active, password...). El servicio decide cuáles aplica.
    Outputs:
        200 {success:true, data:User.to_dict()}
        404 {success:false, error} si el usuario no existe.
        500 ante fallo interno.
    Llama a: UserService.update_user(user_id, **data).
    """
    try:
        data = request.get_json()
        user = user_service.update_user(user_id, **data)
        if not user:
            return jsonify({"success": False, "error": "Usuario no encontrado"}), 404
        return jsonify({"success": True, "data": user.to_dict()}), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@users_bp.route("/<int:user_id>", methods=["DELETE"])
@jwt_required()
@admin_required
def delete_user(user_id):
    """
    Da de baja un usuario (soft delete: marca is_active=False, no borra fila).

    Método+Ruta: DELETE /api/v1/users/<user_id>
    Permiso: JWT válido + role=admin.
    Inputs: Path user_id (int).
    Outputs:
        200 {success:true, message} si se desactivó.
        404 {success:false, error} si el usuario no existe.
        500 ante fallo interno.
    Llama a: UserService.delete_user(user_id) → bool.
    """
    try:
        if user_service.delete_user(user_id):
            return jsonify({"success": True, "message": "Usuario eliminado"}), 200
        return jsonify({"success": False, "error": "Usuario no encontrado"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@users_bp.route("/me", methods=["GET"])
@jwt_required()
def get_current_user():
    """
    Devuelve el perfil del usuario autenticado MÁS las cámaras a las que tiene
    acceso (propias + compartidas vía UserCameraPermission).

    Método+Ruta: GET /api/v1/users/me
    Permiso: JWT válido (cualquier rol). La identidad sale del token, no del path.
    Inputs: ninguno (user_id = get_jwt_identity()).
    Outputs:
        200 {success:true, data:{...User.to_dict(), accessible_cameras:[ids]}}
        404 {success:false, error} si el usuario del token ya no existe.
        500 ante fallo interno.
    Llama a: UserService.get_user_by_id() + PermissionService.get_accessible_cameras().
    """
    try:
        user_id = get_jwt_identity()
        user = user_service.get_user_by_id(int(user_id))
        if not user:
            return jsonify({"success": False, "error": "Usuario no encontrado"}), 404
        
        # Incluir cámaras accesibles
        cameras = permission_service.get_accessible_cameras(int(user_id))
        
        return jsonify({
            "success": True,
            "data": {
                **user.to_dict(),
                "accessible_cameras": cameras
            }
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500