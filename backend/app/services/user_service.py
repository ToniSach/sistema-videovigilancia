"""
Servicio de gestión de usuarios multiusuario.
"""
import logging
from typing import Optional, List
from datetime import datetime

from backend.app.database.models import User, UserRole
from backend.app.database.connection import db_manager
from backend.app.core.security import hash_password, verify_password

logger = logging.getLogger(__name__)


class UserService:
    """Servicio para operaciones CRUD de usuarios."""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def create_user(self, username: str, password: str, role: str = "user") -> User:
        """
        Crea un nuevo usuario.
        
        Args:
            username: Nombre de usuario único
            password: Contraseña en texto plano
            role: 'admin' o 'user'
        
        Returns:
            Usuario creado
            
        Raises:
            ValueError: Si el usuario existe o datos inválidos
        """
        if len(username) < 3:
            raise ValueError("Username debe tener al menos 3 caracteres")
        if len(password) < 6:
            raise ValueError("Password debe tener al menos 6 caracteres")
        if role not in ["admin", "user"]:
            raise ValueError("Rol debe ser 'admin' o 'user'")
        
        try:
            with db_manager.get_session() as session:
                # Verificar si existe
                existing = session.query(User).filter_by(username=username).first()
                if existing:
                    raise ValueError(f"El usuario '{username}' ya existe")
                
                user = User(
                    username=username,
                    password_hash=hash_password(password),
                    role=role,
                    is_active=True
                )
                session.add(user)
                session.flush()
                session.expunge(user)
                self.logger.info(f"Usuario creado: {username} (rol: {role})")
                return user
        except Exception as e:
            self.logger.error(f"Error creando usuario: {e}")
            raise
    
    def count_users(self) -> int:
        """Número total de usuarios (para detectar el primer arranque)."""
        try:
            with db_manager.get_session() as session:
                return session.query(User).count()
        except Exception as e:
            self.logger.error(f"Error contando usuarios: {e}")
            return 0

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Obtiene usuario por ID."""
        try:
            with db_manager.get_session() as session:
                user = session.get(User, user_id)
                if user:
                    session.expunge(user)
                return user
        except Exception as e:
            self.logger.error(f"Error obteniendo usuario {user_id}: {e}")
            raise
    
    def get_user_by_username(self, username: str) -> Optional[User]:
        """Obtiene usuario por username."""
        try:
            with db_manager.get_session() as session:
                user = session.query(User).filter_by(username=username).first()
                if user:
                    session.expunge(user)
                return user
        except Exception as e:
            self.logger.error(f"Error obteniendo usuario {username}: {e}")
            raise
    
    def get_all_users(self) -> List[User]:
        """Obtiene todos los usuarios."""
        try:
            with db_manager.get_session() as session:
                users = session.query(User).all()
                for u in users:
                    session.expunge(u)
                return users
        except Exception as e:
            self.logger.error(f"Error obteniendo usuarios: {e}")
            raise
    
    def update_user(self, user_id: int, **kwargs) -> Optional[User]:
        """
        Actualiza datos de usuario.
        
        Args:
            user_id: ID del usuario
            **kwargs: Campos a actualizar (is_active, role, password)
        """
        try:
            with db_manager.get_session() as session:
                user = session.get(User, user_id)
                if not user:
                    return None
                
                if "password" in kwargs:
                    user.password_hash = hash_password(kwargs.pop("password"))
                
                for key, value in kwargs.items():
                    if hasattr(user, key):
                        setattr(user, key, value)
                
                session.flush()
                session.expunge(user)
                self.logger.info(f"Usuario {user_id} actualizado")
                return user
        except Exception as e:
            self.logger.error(f"Error actualizando usuario: {e}")
            raise
    
    def delete_user(self, user_id: int) -> bool:
        """Elimina usuario (soft delete)."""
        return self.update_user(user_id, is_active=False) is not None
    
    def is_admin(self, user_id: int) -> bool:
        """Verifica si usuario es admin."""
        user = self.get_user_by_id(user_id)
        return user is not None and user.role == "admin"