"""
================================================================================
MÓDULO: services.user_service — Gestión CRUD de usuarios (multiusuario)
================================================================================

PROPÓSITO
    Capa de negocio para el ciclo de vida de las cuentas: crear, consultar,
    actualizar y dar de baja usuarios; comprobar rol admin y contar usuarios
    (para detectar el primer arranque y crear el admin inicial).

RESPONSABILIDAD PRINCIPAL
    CRUD de la entidad User con la política de credenciales (longitudes mínimas,
    roles válidos, unicidad de username) y hashing de contraseñas. Devuelve
    siempre objetos DESLIGADOS de la sesión (expunge) para uso seguro tras cerrar
    la transacción.

DEPENDENCIAS
    database.models ........... User, UserRole
    database.connection ....... db_manager.get_session()
    core.security ............. hash_password / verify_password

COMPONENTES RELACIONADOS
    Lo INSTANCIA/CONSUME: blueprint `users_bp` (api/routes/users.py) para el
        panel de administración de usuarios; también puede usarse en el bootstrap
        del primer admin. No es singleton del contenedor (se instancia ad-hoc).
    Relación con AuthService: AuthService cubre login/tokens y un alta con roles
        admin/viewer; UserService es el CRUD general multiusuario (roles
        admin/user). Tener presente esa divergencia de roles al elegir cuál usar.

PUNTO DE ENTRADA
    Cada método público corresponde a una operación del panel de usuarios.

PIPELINE(S)
    Soporte de #2 Autenticación — provee las cuentas que login() valida; no es
    una etapa de captura/medios.
================================================================================
"""
import logging
from typing import Optional, List
from datetime import datetime

from backend.app.database.models import User, UserRole
from backend.app.database.connection import db_manager
from backend.app.core.security import hash_password, verify_password

logger = logging.getLogger(__name__)


class UserService:
    """
    Servicio CRUD de usuarios (panel multiusuario).

    Rol: gestor de cuentas sin estado (abre sesión por operación). Distinto de
    AuthService, que se ocupa de login/tokens.
    Lo instancia/consume: users_bp.
    Dependencias: modelos User/UserRole, db_manager, core.security.
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def create_user(self, username: str, password: str, role: str = "user") -> User:
        """
        Crea un usuario nuevo con la contraseña hasheada.

        Inputs: username (único, >=3), password (texto plano, >=6), role
            ('admin'|'user' — OJO: AuthService usa 'admin'|'viewer').
        Outputs: User creado (desligado de la sesión).
        Excepciones: ValueError si el username ya existe o los datos no validan.
        Llamado por: POST /api/v1/users (users_bp).
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
        Actualiza campos de un usuario de forma genérica.

        Si llega "password" en kwargs se hashea y se aplica a password_hash (nunca
        se guarda texto plano); el resto de claves se asignan si existen como
        atributo de User.

        Inputs: user_id; kwargs (is_active, role, password, ...).
        Outputs: User actualizado (desligado) o None si no existe.
        Llamado por: PUT/PATCH /api/v1/users/<id> (users_bp); también lo usa
            delete_user para la baja lógica.
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
        """Baja LÓGICA del usuario (is_active=False, no borra la fila), delegando
        en update_user. Outputs: True si el usuario existía. Llamado por: DELETE
        /api/v1/users/<id> (users_bp)."""
        return self.update_user(user_id, is_active=False) is not None

    def is_admin(self, user_id: int) -> bool:
        """True si el usuario tiene rol 'admin' (guardia de rutas solo-admin)."""
        user = self.get_user_by_id(user_id)
        return user is not None and user.role == "admin"