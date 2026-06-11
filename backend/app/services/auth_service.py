"""
================================================================================
MÓDULO: services.auth_service — Servicio de autenticación (Pipeline #2)
================================================================================

PROPÓSITO
    Capa de lógica de negocio para autenticación: valida credenciales contra la
    BD, emite tokens JWT (access + refresh) y administra el ciclo de vida básico
    de las cuentas (alta, cambio de contraseña, baja lógica).

RESPONSABILIDAD PRINCIPAL
    Ser el ÚNICO punto que verifica password_hash y genera tokens firmados.
    Las rutas API nunca tocan hashing ni JWT directamente: delegan aquí para que
    la política de credenciales (longitudes mínimas, estado is_active, claims)
    viva en un solo sitio.

DEPENDENCIAS
    flask_jwt_extended ........ create_access_token / create_refresh_token
    core.security ............. hash_password / verify_password (bcrypt)
    database.connection ....... db_manager.get_session() (sesión transaccional)
    database.models.User ...... entidad de usuario
    config.settings ........... duraciones de token (JWT_ACCESS_TOKEN_MINUTES,
                                JWT_REFRESH_TOKEN_DAYS)

COMPONENTES RELACIONADOS
    Lo INSTANCIA: DependencyContainer (container.py) como singleton
        `auth_service`; también se importa directamente desde algunas rutas.
    Lo CONSUME: blueprint `auth_bp` (api/routes/auth.py) — login, refresh,
        registro y cambio de contraseña. Coexiste con UserService, que cubre el
        CRUD multiusuario más amplio.

PUNTO DE ENTRADA
    `AuthService().login(username, password)` es la entrada del Pipeline #2.

PIPELINE(S)
    #2 Autenticación — etapa central: credenciales válidas → tokens JWT que el
    resto de los endpoints exigen vía @jwt_required. El blocklist de logout y los
    loaders de error JWT se configuran aparte en main.create_app().
================================================================================
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
    Servicio centralizado de autenticación (capa de negocio del Pipeline #2).

    Rol: traduce "usuario + contraseña" en una sesión con tokens JWT y mantiene
    la política de credenciales. No guarda estado de sesión en memoria — la
    sesión vive en el token firmado; la revocación (logout) la lleva el blocklist
    JWT configurado en main.py.

    Lo instancia: container.py (singleton `auth_service`).
    Lo consume: api/routes/auth.py (auth_bp). Para CRUD multiusuario completo ver
        UserService.
    Dependencias: BaseRepository[User], db_manager, core.security, settings.
    """

    def __init__(self):
        # BaseRepository genérico sobre User: usado por get_user_by_id; el resto
        # de métodos abre su propia sesión transaccional con db_manager.
        self.user_repository = BaseRepository[User](User)
        self.logger = logging.getLogger(__name__)

    def login(self, username: str, password: str) -> Optional[dict]:
        """
        Autentica un usuario y genera tokens JWT — etapa central del Pipeline #2.

        Verifica, en orden: existencia del usuario, password (bcrypt) y estado
        is_active. Solo si los tres pasan emite tokens. Embebe el `role` como
        claim adicional para que los endpoints puedan autorizar por rol sin un
        roundtrip extra a BD.

        Inputs:
            username: nombre de usuario.
            password: contraseña en texto plano (se compara contra el hash).

        Outputs:
            dict con access_token, refresh_token y user.to_dict(); o None si las
            credenciales son inválidas o la cuenta está inactiva (las tres causas
            devuelven None a propósito, sin revelar cuál falló).

        Excepciones:
            RuntimeError ante fallo interno (p.ej. error de BD) — distingue
            "credenciales malas" (None) de "error del sistema" (excepción).

        Llamado por: POST /api/v1/auth/login (auth_bp).
        Llama a: verify_password, create_access_token/refresh_token.

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
        Obtiene un usuario por su ID (vía BaseRepository, no abre sesión propia).

        Inputs: user_id numérico (normalmente el `sub` del JWT).
        Outputs: instancia de User o None si no existe.
        Llamado por: endpoints que resuelven el usuario actual desde el token.
        """
        try:
            return self.user_repository.get_by_id(user_id)
        except Exception as error:
            self.logger.error(f"Error al obtener usuario {user_id}: {error}")
            raise
    
    def create_user(self, username: str, password: str, role: str = "viewer") -> User:
        """
        Crea un nuevo usuario, hasheando la contraseña antes de persistir.

        Valida longitudes mínimas y rol permitido; comprueba unicidad del
        username dentro de la misma sesión. Tras flush hace expunge para devolver
        un objeto desligado (usable tras cerrar la sesión sin DetachedInstance).

        Inputs: username (único, >=3), password (texto plano, >=6), role
            ('admin'|'viewer' — distinto del CRUD de UserService, que usa 'user').
        Outputs: instancia de User creada (desligada de la sesión).
        Excepciones: ValueError si el username ya existe o los datos no validan;
            RuntimeError ante fallo interno.

        Nota: este método usa el par de roles admin/viewer; el alta multiusuario
        general vive en UserService.create_user (admin/user). Mantener en mente la
        divergencia al elegir cuál llamar.
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
        Cambia la contraseña verificando la anterior (defensa contra secuestro
        de sesión: aunque el atacante tenga el token, sin la contraseña actual no
        puede cambiarla).

        Inputs: user_id, old_password (debe coincidir con el hash actual),
            new_password (>=6 chars y distinta de la anterior).
        Outputs: True si se actualizó; False si old_password no coincide.
        Excepciones: ValueError si la nueva no cumple requisitos; RuntimeError
            ante fallo interno.
        Llamado por: endpoint de cambio de contraseña (auth_bp).
        Llama a: verify_password (validar) + hash_password (persistir).
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
        Desactiva un usuario (baja lógica: is_active=False, NO borra la fila).

        Tras esto, login() rechazará a ese usuario aunque las credenciales sean
        correctas. No revoca tokens ya emitidos: estos caducan por su exp natural
        (o por logout explícito vía blocklist).

        Inputs: user_id a desactivar.
        Outputs: True si se desactivó; False si el usuario no existía.
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
