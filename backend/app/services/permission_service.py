"""
Servicio de gestión de permisos por cámara.
"""
import logging
from typing import Optional, List
from functools import wraps
from flask import jsonify

from backend.app.database.models import User, Camera, UserCameraPermission, UserRole
from backend.app.database.connection import db_manager

logger = logging.getLogger(__name__)


class PermissionService:
    """Gestiona permisos granulares de usuarios sobre cámaras."""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def grant_permission(self, user_id: int, camera_id: int, 
                        can_view: bool = True,
                        can_control_ptz: bool = False,
                        can_control_leds: bool = False,
                        can_control_audio: bool = False,
                        can_download_recordings: bool = False) -> UserCameraPermission:
        """Otorga permisos a un usuario sobre una cámara."""
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
        """Revoca todos los permisos de un usuario sobre una cámara."""
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
        """Obtiene todos los permisos de un usuario."""
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
        """Obtiene todos los permisos sobre una cámara."""
        try:
            with db_manager.get_session() as session:
                perms = session.query(UserCameraPermission).filter_by(camera_id=camera_id).all()
                for p in perms:
                    session.expunge(p)
                return perms
        except Exception as e:
            self.logger.error(f"Error obteniendo permisos de cámara: {e}")
            raise
    
    def check_permission(self, user_id: int, camera_id: int, permission_type: str) -> bool:
        """
        Verifica si un usuario tiene un permiso específico.
        
        Args:
            permission_type: 'view', 'control_ptz', 'control_leds', 'control_audio', 'download'
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
        """Obtiene IDs de cámaras a las que el usuario tiene acceso."""
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
    Decorador para endpoints Flask que requieren permisos sobre cámara.
    
    Args:
        permission_type: Tipo de permiso requerido
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