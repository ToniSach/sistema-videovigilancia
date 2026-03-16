"""
Servicio de autenticación y gestión de usuarios.
Maneja login, creación de usuarios y gestión de tokens JWT.
"""
import logging
from datetime import timedelta
from typing import Optional

from flask_jwt_extended import create_access_token, create_refresh_token

from backend.app.database.repositories.base_repository import BaseRepository
from backend.app.database.models import User
from backend.app.database.connection import db_manager
from backend.app.core.security import hash_password, verify_password
from backend.app.config import settings

logger = logging.getLogger(__name__)


class AuthService:
    """
    Servicio centralizado para operaciones de autenticación.
    Gestiona ciclo de vida de sesiones y credenciales.
    """
    
    def __init__(self):
        """Inicializa el servicio con repositorio de usuarios."""
        self.user_repository = BaseRepository[User](User)
        self.logger = logging.getLogger(__name__)
    
    def login(self, username: str, password: str) -> Optional[dict]:
        """
        Autentica un usuario y genera tokens JWT.
        
        Args:
            username: Nombre de usuario
            password: Contraseña en texto plano
            
        Returns:
            Dict con tokens y datos de usuario, o None si credenciales inválidas
            
        Example return:
            {
                "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
                "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
                "user": {"id": 1, "username": "admin", "role": "admin", ...}
            }
        """
        try:
            with db_manager.get_session() as session:
                # Buscar usuario por username
                user = session.query(User).filter_by(username=username).first()
                
                if not user:
                    self.logger.warning(f"Intento de login con usuario inexistente: {username}")
                    return None
                
                # Verificar contraseña
                if not verify_password(password, user.password_hash):
                    self.logger.warning(f"Contraseña incorrecta para usuario: {username}")
                    return None
                
                if not user.is_active:
                    self.logger.warning(f"Intento de login con usuario inactivo: {username}")
                    return None
                
                # Generar tokens JWT con claims adicionales (role)
                additional_claims = {"role": user.role}
                
                access_token = create_access_token(
                    identity=str(user.id),
                    additional_claims=additional_claims,
                    expires_delta=timedelta(minutes=settings.JWT_ACCESS_TOKEN_MINUTES)
                )
                
                refresh_token = create_refresh_token(
                    identity=str(user.id),
                    expires_delta=timedelta(days=settings.JWT_REFRESH_TOKEN_DAYS)
                )
                
                self.logger.info(f"Login exitoso: {username} (ID: {user.id})")
                
                return {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "user": user.to_dict()
                }
                
        except Exception as error:
            self.logger.error(f"Error en proceso de login: {error}")
            raise RuntimeError("Error interno de autenticación") from error
    
    def get_user_by_id(self, user_id: int) -> Optional[User]:
        """
        Obtiene un usuario por su ID.
        
        Args:
            user_id: ID numérico del usuario
            
        Returns:
            Instancia de User o None si no existe
        """
        try:
            return self.user_repository.get_by_id(user_id)
        except Exception as error:
            self.logger.error(f"Error al obtener usuario {user_id}: {error}")
            raise
    
    def create_user(self, username: str, password: str, role: str = "viewer") -> User:
        """
        Crea un nuevo usuario en el sistema.
        
        Args:
            username: Nombre de usuario único
            password: Contraseña en texto plano (se hashea internamente)
            role: Rol del usuario (admin o viewer)
            
        Returns:
            Instancia de User creada
            
        Raises:
            ValueError: Si el username ya existe o datos inválidos
        """
        try:
            # Validaciones básicas
            if not username or len(username) < 3:
                raise ValueError("Username debe tener al menos 3 caracteres")
            
            if not password or len(password) < 6:
                raise ValueError("Password debe tener al menos 6 caracteres")
            
            if role not in ["admin", "viewer"]:
                raise ValueError("Rol debe ser 'admin' o 'viewer'")
            
            # Verificar si existe
            with db_manager.get_session() as session:
                existing = session.query(User).filter_by(username=username).first()
                if existing:
                    raise ValueError(f"El usuario '{username}' ya existe")
                
                # Crear nuevo usuario
                new_user = User(
                    username=username,
                    password_hash=hash_password(password),
                    role=role,
                    is_active=True
                )
                
                session.add(new_user)
                session.flush()
                session.expunge(new_user)
                
                self.logger.info(f"Usuario creado: {username} (rol: {role})")
                return new_user
                
        except ValueError:
            raise
        except Exception as error:
            self.logger.error(f"Error al crear usuario: {error}")
            raise RuntimeError("Error interno al crear usuario") from error
    
    def change_password(self, user_id: int, old_password: str, new_password: str) -> bool:
        """
        Cambia la contraseña de un usuario verificando la anterior.
        
        Args:
            user_id: ID del usuario
            old_password: Contraseña actual
            new_password: Nueva contraseña
            
        Returns:
            True si se cambió exitosamente, False si contraseña anterior incorrecta
            
        Raises:
            ValueError: Si nueva contraseña no cumple requisitos
        """
        try:
            if not new_password or len(new_password) < 6:
                raise ValueError("Nueva contraseña debe tener al menos 6 caracteres")
            
            if old_password == new_password:
                raise ValueError("La nueva contraseña debe ser diferente a la anterior")
            
            with db_manager.get_session() as session:
                user = session.get(User, user_id)
                if not user:
                    raise ValueError("Usuario no encontrado")
                
                # Verificar contraseña anterior
                if not verify_password(old_password, user.password_hash):
                    return False
                
                # Actualizar contraseña
                user.password_hash = hash_password(new_password)
                
                self.logger.info(f"Contraseña cambiada para usuario ID: {user_id}")
                return True
                
        except ValueError:
            raise
        except Exception as error:
            self.logger.error(f"Error al cambiar contraseña: {error}")
            raise RuntimeError("Error interno al cambiar contraseña") from error
    
    def deactivate_user(self, user_id: int) -> bool:
        """
        Desactiva un usuario (soft delete).
        
        Args:
            user_id: ID del usuario a desactivar
            
        Returns:
            True si se desactivó, False si no existía
        """
        try:
            with db_manager.get_session() as session:
                user = session.get(User, user_id)
                if not user:
                    return False
                
                user.is_active = False
                self.logger.info(f"Usuario desactivado: {user_id}")
                return True
                
        except Exception as error:
            self.logger.error(f"Error al desactivar usuario: {error}")
            raise
