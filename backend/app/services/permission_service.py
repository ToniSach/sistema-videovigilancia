"""
================================================================================
MÓDULO: services.permission_service — Autorización por cámara (multi-tenancy)
================================================================================

PROPÓSITO
    Capa de autorización granular: decide si un usuario puede ver/controlar una
    cámara concreta. Gestiona la tabla UserCameraPermission (cámaras compartidas)
    y resuelve la jerarquía admin > propietario > permiso explícito.

RESPONSABILIDAD PRINCIPAL
    Ser el ÚNICO árbitro de "¿puede este usuario hacer X sobre esta cámara?".
    REGLA CLAVE: comprobar SIEMPRE el permiso vía este servicio; owner_id NO
    basta, porque existen cámaras compartidas con otros usuarios mediante
    UserCameraPermission (con flags por acción: view, PTZ, LEDs, audio, descargas).

DEPENDENCIAS
    database.models ........... User, Camera, UserCameraPermission, UserRole
    database.connection ....... db_manager.get_session()
    flask_jwt_extended ........ get_jwt_identity (en el decorador)

COMPONENTES RELACIONADOS
    Lo INSTANCIA: se construye bajo demanda (`PermissionService()`) en las rutas
        y en el decorador; es ligero y sin estado (no es singleton del contenedor).
    Lo CONSUME: blueprint `permissions_bp` (grant/revoke/listar) y, vía el
        decorador `require_camera_permission`, cualquier ruta que opere sobre una
        cámara (cameras_bp, recordings_bp, etc.).

PUNTO DE ENTRADA
    `check_permission(user_id, camera_id, permission_type)` y el decorador
    `require_camera_permission(...)` que lo envuelve para endpoints Flask.

PIPELINE(S)
    Transversal — guardia de autorización presente en los pipelines de cara al
    usuario (#3 Live, #8 PTZ, #12 Clips, #14 Reproducción): valida el acceso
    antes de ejecutar la acción.
================================================================================
"""
import logging
from typing import Optional, List
from functools import wraps
from flask import jsonify

from backend.app.database.models import User, Camera, UserCameraPermission, UserRole
from backend.app.database.connection import db_manager

logger = logging.getLogger(__name__)


class PermissionService:
    """
    Gestiona permisos granulares de usuarios sobre cámaras (autorización).

    Rol: árbitro de acceso multi-tenant. Sin estado propio (abre una sesión por
    operación), por lo que se instancia ad-hoc donde haga falta.

    Lo instancia/consume: permissions_bp y el decorador require_camera_permission.
    Dependencias: modelos User/Camera/UserCameraPermission + db_manager.
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def grant_permission(self, user_id: int, camera_id: int,
                        can_view: bool = True,
                        can_control_ptz: bool = False,
                        can_control_leds: bool = False,
                        can_control_audio: bool = False,
                        can_download_recordings: bool = False) -> UserCameraPermission:
        """
        Otorga (o actualiza) los permisos de un usuario sobre una cámara
        compartida — upsert: si ya existe la fila, sobreescribe los flags.

        Inputs: user_id, camera_id y los flags por acción (view/PTZ/LEDs/audio/
            descargas).
        Outputs: la UserCameraPermission creada o actualizada (desligada de la
            sesión vía expunge).
        Llamado por: POST de compartición en permissions_bp (solo admin/owner).
        """
        try:
            with db_manager.get_session() as session:
                # Verificar si ya existe
                existing = session.query(UserCameraPermission).filter_by(
                    user_id=user_id, camera_id=camera_id
                ).first()
                
                if existing:
                    # Actualizar
                    existing.can_view = can_view
                    existing.can_control_ptz = can_control_ptz
                    existing.can_control_leds = can_control_leds
                    existing.can_control_audio = can_control_audio
                    existing.can_download_recordings = can_download_recordings
                    session.flush()
                    session.expunge(existing)
                    self.logger.info(f"Permisos actualizados para user {user_id} en camera {camera_id}")
                    return existing
                else:
                    # Crear nuevo
                    perm = UserCameraPermission(
                        user_id=user_id,
                        camera_id=camera_id,
                        can_view=can_view,
                        can_control_ptz=can_control_ptz,
                        can_control_leds=can_control_leds,
                        can_control_audio=can_control_audio,
                        can_download_recordings=can_download_recordings
                    )
                    session.add(perm)
                    session.flush()
                    session.expunge(perm)
                    self.logger.info(f"Permisos creados para user {user_id} en camera {camera_id}")
                    return perm
        except Exception as e:
            self.logger.error(f"Error otorgando permisos: {e}")
            raise
    
    def revoke_permission(self, user_id: int, camera_id: int) -> bool:
        """Revoca (borra la fila) todos los permisos de un usuario sobre una
        cámara compartida. Outputs: True si existía y se borró; False si no.
        Llamado por: endpoint de revocación de permissions_bp."""
        try:
            with db_manager.get_session() as session:
                perm = session.query(UserCameraPermission).filter_by(
                    user_id=user_id, camera_id=camera_id
                ).first()
                
                if perm:
                    session.delete(perm)
                    self.logger.info(f"Permisos revocados para user {user_id} en camera {camera_id}")
                    return True
                return False
        except Exception as e:
            self.logger.error(f"Error revocando permisos: {e}")
            raise
    
    def get_user_permissions(self, user_id: int) -> List[UserCameraPermission]:
        """Lista los permisos explícitos de un usuario (filas UserCameraPermission
        desligadas de la sesión). Llamado por: permissions_bp."""
        try:
            with db_manager.get_session() as session:
                perms = session.query(UserCameraPermission).filter_by(user_id=user_id).all()
                for p in perms:
                    session.expunge(p)
                return perms
        except Exception as e:
            self.logger.error(f"Error obteniendo permisos: {e}")
            raise
    
    def get_camera_permissions(self, camera_id: int) -> List[UserCameraPermission]:
        """
        Obtiene todos los permisos sobre una cámara.

        IMPORTANTE: usa joinedload sobre `user` para que el endpoint pueda
        acceder a `p.user.username` después de cerrar la sesión sin disparar
        un lazy-load que produce DetachedInstanceError → HTTP 500.
        """
        try:
            from sqlalchemy.orm import joinedload
            with db_manager.get_session() as session:
                perms = (
                    session.query(UserCameraPermission)
                    .options(joinedload(UserCameraPermission.user))
                    .filter_by(camera_id=camera_id)
                    .all()
                )
                # Forzar acceso al user dentro de la sesión (rellena el cache)
                # y expungar todo el grafo para uso post-sesión.
                for p in perms:
                    _ = p.user.username if p.user else None
                    session.expunge(p)
                    if p.user:
                        session.expunge(p.user)
                return perms
        except Exception as e:
            self.logger.error(f"Error obteniendo permisos de cámara: {e}")
            raise
    
    def check_permission(self, user_id: int, camera_id: int, permission_type: str) -> bool:
        """
        Núcleo de autorización: ¿puede `user_id` hacer `permission_type` sobre
        `camera_id`? Resuelve la jerarquía en orden de cortocircuito:
            1) admin  → True (acceso total).
            2) owner de la cámara → True.
            3) permiso explícito (UserCameraPermission) según el flag pedido.
        Si ninguno aplica → False. Cualquier excepción también devuelve False
        (fail-closed: ante la duda, denegar).

        Inputs: user_id, camera_id, permission_type ∈ {'view','control_ptz',
            'control_leds','control_audio','download'}.
        Outputs: True/False.
        Llamado por: el decorador require_camera_permission y rutas que validan
            acceso a una cámara concreta.
        """
        try:
            with db_manager.get_session() as session:
                # Verificar si es admin
                user = session.get(User, user_id)
                if user and user.role == UserRole.ADMIN.value:
                    return True
                
                # Verificar ownership
                camera = session.get(Camera, camera_id)
                if camera and camera.owner_id == user_id:
                    return True
                
                # Verificar permiso específico
                perm = session.query(UserCameraPermission).filter_by(
                    user_id=user_id, camera_id=camera_id
                ).first()
                
                if not perm:
                    return False
                
                if permission_type == 'view':
                    return perm.can_view
                elif permission_type == 'control_ptz':
                    return perm.can_control_ptz
                elif permission_type == 'control_leds':
                    return perm.can_control_leds
                elif permission_type == 'control_audio':
                    return perm.can_control_audio
                elif permission_type == 'download':
                    return perm.can_download_recordings
                
                return False
        except Exception as e:
            self.logger.error(f"Error verificando permiso: {e}")
            return False
    
    def get_accessible_cameras(self, user_id: int) -> List[int]:
        """
        IDs de cámaras visibles para el usuario = (propias ∪ compartidas con
        can_view). Admin recibe TODAS. Sirve para filtrar listados por usuario.

        Outputs: lista de camera_id (sin duplicados).
        Llamado por: rutas que listan cámaras/eventos acotadas al usuario actual.
        """
        try:
            with db_manager.get_session() as session:
                user = session.get(User, user_id)
                if not user:
                    return []
                
                # Admin ve todo
                if user.role == UserRole.ADMIN.value:
                    cameras = session.query(Camera).all()
                    return [c.id for c in cameras]
                
                # Owner ve sus cámaras
                owned = session.query(Camera).filter_by(owner_id=user_id).all()
                owned_ids = {c.id for c in owned}
                
                # Permisos explícitos
                perms = session.query(UserCameraPermission).filter_by(user_id=user_id, can_view=True).all()
                perm_ids = {p.camera_id for p in perms}
                
                return list(owned_ids.union(perm_ids))
        except Exception as e:
            self.logger.error(f"Error obteniendo cámaras accesibles: {e}")
            raise


def require_camera_permission(permission_type: str):
    """
    Decorador de ruta Flask que exige un permiso de cámara antes de ejecutar.

    Lee el user_id del JWT (get_jwt_identity) y el camera_id de los kwargs de la
    ruta (debe llamarse `camera_id` en la URL), y llama a
    PermissionService.check_permission. Respuestas:
        400 si falta camera_id · 403 si el permiso se deniega · si pasa, ejecuta f.

    Inputs: permission_type ∈ {'view','control_ptz','control_leds',
        'control_audio','download'}.
    Uso: apilar BAJO @jwt_required() en los endpoints que tocan una cámara.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            from flask_jwt_extended import get_jwt_identity
            from flask import jsonify
            
            user_id = get_jwt_identity()
            camera_id = kwargs.get('camera_id')
            
            if not camera_id:
                return jsonify({"error": "Camera ID no proporcionado"}), 400
            
            perm_service = PermissionService()
            if not perm_service.check_permission(int(user_id), int(camera_id), permission_type):
                return jsonify({"error": "Permiso denegado"}), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator