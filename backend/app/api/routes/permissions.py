"""
================================================================================
MÓDULO: api.routes.permissions — Permisos de acceso por cámara (capa HTTP)
================================================================================

PROPÓSITO
    Blueprint REST que gestiona UserCameraPermission: el mecanismo de
    compartición que permite a un usuario distinto del dueño ver/controlar una
    cámara concreta con flags granulares (ver, PTZ, LEDs, audio, descargar
    grabaciones). También expone la lista de cámaras accesibles del usuario.

RESPONSABILIDAD
    SOLO contrato HTTP + control de acceso de quién PUEDE administrar permisos
    (admin). La persistencia y la evaluación efectiva de permisos viven en
    PermissionService. Recordar (ver CLAUDE.md): owner_id por sí solo NO basta;
    el resto del sistema debe consultar PermissionService porque existen
    cámaras compartidas vía esta tabla.

DEPENDENCIAS
    services.permission_service.PermissionService  CRUD de UserCameraPermission
    services.user_service.UserService.is_admin()   gating de administración
    flask_jwt_extended ............................ identidad + @jwt_required

COMPONENTES RELACIONADOS
    routes/users.py ........ /me también devuelve accessible_cameras
    database.models.UserCameraPermission  modelo ORM con los flags can_*
    require_camera_permission (en permission_service) decorator usado por las
        rutas que SÍ consumen estos permisos (cameras/ptz/leds/recordings...).

PUNTO DE ENTRADA
    Registrado en main.create_app() vía safe_register(permissions_bp).
    Prefijo: /api/v1/permissions. Best-effort.

PIPELINE(S)
    Pipeline #2 (Autenticación/autorización) — etapa de autorización por
    recurso. Es transversal a #3 Live, #8 PTZ, #11 Grabación y #12 Clips: los
    flags que aquí se conceden son los que esas rutas verifican.

ENDPOINTS
    GET    /camera/<camera_id>                       → get_camera_permissions() [admin]
    POST   /camera/<camera_id>/user/<target_user_id> → grant_permission()       [admin]
    DELETE /camera/<camera_id>/user/<target_user_id> → revoke_permission()      [admin]
    GET    /my-cameras                               → get_my_cameras()         [auth]
================================================================================
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from backend.app.services.permission_service import PermissionService, require_camera_permission
from backend.app.services.user_service import UserService

permissions_bp = Blueprint("permissions", __name__, url_prefix="/api/v1/permissions")
permission_service = PermissionService()
user_service = UserService()


@permissions_bp.route("/camera/<int:camera_id>", methods=["GET"])
@jwt_required()
def get_camera_permissions(camera_id):
    """
    Lista qué usuarios tienen permisos sobre una cámara y con qué flags.

    Método+Ruta: GET /api/v1/permissions/camera/<camera_id>
    Permiso: JWT válido + role=admin (verificado en código con
        UserService.is_admin; NO usa el decorator @admin_required).
    Inputs: Path camera_id (int).
    Outputs:
        200 {success:true, data:[{user_id, username, can_view, can_control_ptz,
             can_control_leds, can_control_audio, can_download_recordings}, ...]}
        403 {success:false, error:"Admin requerido"} si no es admin.
        500 ante fallo interno.
    Llama a: UserService.is_admin() + PermissionService.get_camera_permissions().
    """
    try:
        user_id = int(get_jwt_identity())
        
        # Verificar si es admin o tiene permisos de administración sobre la cámara
        if not user_service.is_admin(user_id):
            return jsonify({"success": False, "error": "Admin requerido"}), 403
        
        perms = permission_service.get_camera_permissions(camera_id)
        return jsonify({
            "success": True,
            "data": [{
                "user_id": p.user_id,
                "username": p.user.username if hasattr(p, 'user') else None,
                "can_view": p.can_view,
                "can_control_ptz": p.can_control_ptz,
                "can_control_leds": p.can_control_leds,
                "can_control_audio": p.can_control_audio,
                "can_download_recordings": p.can_download_recordings
            } for p in perms]
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@permissions_bp.route("/camera/<int:camera_id>/user/<int:target_user_id>", methods=["POST"])
@jwt_required()
def grant_permission(camera_id, target_user_id):
    """
    Concede (o actualiza) permisos de un usuario sobre una cámara.

    Método+Ruta: POST /api/v1/permissions/camera/<camera_id>/user/<target_user_id>
    Permiso: JWT válido. NOTA: el check de admin está presente pero NO bloquea
        (TODO pendiente: debería exigir admin o ser owner de la cámara). Hoy
        cualquier usuario autenticado puede otorgar — punto a endurecer.
    Inputs:
        Path: camera_id (int), target_user_id (int) = beneficiario.
        Body JSON (todos opcionales, default seguro):
            can_view (bool, default True), can_control_ptz (default False),
            can_control_leds (default False), can_control_audio (default False),
            can_download_recordings (default False).
    Outputs:
        201 {success:true, data:{id, user_id, camera_id, permissions:{view, ptz,
             leds, audio, download}}}
        500 ante fallo interno.
    Llama a: PermissionService.grant_permission(...).
    """
    try:
        user_id = int(get_jwt_identity())

        # TODO de seguridad: este check NO restringe (cae en pass). Debería
        # exigir admin o ser owner de la cámara antes de conceder permisos.
        if not user_service.is_admin(user_id):
            pass

        data = request.get_json() or {}
        
        perm = permission_service.grant_permission(
            user_id=target_user_id,
            camera_id=camera_id,
            can_view=data.get("can_view", True),
            can_control_ptz=data.get("can_control_ptz", False),
            can_control_leds=data.get("can_control_leds", False),
            can_control_audio=data.get("can_control_audio", False),
            can_download_recordings=data.get("can_download_recordings", False)
        )
        
        return jsonify({
            "success": True,
            "data": {
                "id": perm.id,
                "user_id": perm.user_id,
                "camera_id": perm.camera_id,
                "permissions": {
                    "view": perm.can_view,
                    "ptz": perm.can_control_ptz,
                    "leds": perm.can_control_leds,
                    "audio": perm.can_control_audio,
                    "download": perm.can_download_recordings
                }
            }
        }), 201
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@permissions_bp.route("/camera/<int:camera_id>/user/<int:target_user_id>", methods=["DELETE"])
@jwt_required()
def revoke_permission(camera_id, target_user_id):
    """
    Revoca TODOS los permisos de un usuario sobre una cámara (borra la fila).

    Método+Ruta: DELETE /api/v1/permissions/camera/<camera_id>/user/<target_user_id>
    Permiso: JWT válido + role=admin (verificado en código; aquí SÍ bloquea).
    Inputs: Path camera_id (int), target_user_id (int).
    Outputs:
        200 {success:true, message} si se revocó.
        403 {success:false, error:"Admin requerido"} si no es admin.
        404 {success:false, error} si no existía permiso.
        500 ante fallo interno.
    Llama a: PermissionService.revoke_permission(target_user_id, camera_id) → bool.
    """
    try:
        user_id = int(get_jwt_identity())
        
        if not user_service.is_admin(user_id):
            return jsonify({"success": False, "error": "Admin requerido"}), 403
        
        if permission_service.revoke_permission(target_user_id, camera_id):
            return jsonify({"success": True, "message": "Permisos revocados"}), 200
        return jsonify({"success": False, "error": "No se encontraron permisos"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@permissions_bp.route("/my-cameras", methods=["GET"])
@jwt_required()
def get_my_cameras():
    """
    Devuelve los IDs de cámaras accesibles para el usuario autenticado
    (propias como owner + compartidas vía UserCameraPermission).

    Método+Ruta: GET /api/v1/permissions/my-cameras
    Permiso: JWT válido (cualquier rol).
    Inputs: ninguno (user_id = get_jwt_identity()).
    Outputs:
        200 {success:true, data:[camera_ids]}
        500 ante fallo interno.
    Llama a: PermissionService.get_accessible_cameras(user_id).
    """
    try:
        user_id = int(get_jwt_identity())
        cameras = permission_service.get_accessible_cameras(user_id)
        return jsonify({
            "success": True,
            "data": cameras
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500