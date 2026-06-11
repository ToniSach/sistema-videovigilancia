"""
================================================================================
MÓDULO: desktop_app.models.user — DTOs de usuario y autenticación (lado cliente)
================================================================================

PROPÓSITO
    Definir los objetos de transferencia (DTOs) del CLIENTE para el usuario
    autenticado (`User`) y sus tokens JWT (`AuthTokens`). Espejan el JSON que la
    API devuelve en /auth/* y /users, pero NO son las clases SQLAlchemy del
    backend: son dataclasses locales y desacopladas.

RESPONSABILIDAD
    - User: identidad y rol del usuario logueado, con helper is_admin y
      serialización to_dict para la UI.
    - AuthTokens: par access/refresh que api_client mantiene vivo (set_tokens)
      y refresca (pipeline #2 Auth).

DEPENDENCIAS
    - dataclasses, typing, datetime (sin dependencias de Qt ni del backend).

COMPONENTES RELACIONADOS
    - services/api_client.py importa AuthTokens (tipo de self.tokens) y User.
    - login_view construye AuthTokens/User a partir del JSON de /auth/login y
      llama api_client.set_tokens.

PUNTO DE ENTRADA
    Importación directa: `from desktop_app.src.models.user import User, AuthTokens`.

SINCRONIZACIÓN
    Mantener en línea con backend/app/database/models.py (User) y el payload del
    JWT cuando cambien campos como role o accessible_cameras.
================================================================================
"""
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime


@dataclass
class User:
    """
    NIVEL 2 — DTO del usuario autenticado (espejo del JSON de la API).

    Rol: representar en la UI al usuario logueado y sus permisos básicos. `role`
    es "admin" o "user"; `accessible_cameras` lista los IDs de cámara a los que
    tiene acceso (propias + compartidas vía permisos en el backend). No persiste
    nada: es un contenedor de paso entre la respuesta de la API y las vistas.
    """
    id: int
    username: str
    role: str  # admin/user
    is_active: bool = True
    accessible_cameras: List[int] = field(default_factory=list)
    created_at: Optional[datetime] = None
    
    @property
    def is_admin(self) -> bool:
        return self.role == "admin"
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active,
            "accessible_cameras": self.accessible_cameras
        }


@dataclass
class AuthTokens:
    """
    NIVEL 2 — DTO del par de tokens JWT (pipeline #2 Auth).

    Rol: guardar el access_token (corta vida, ~15 min, viaja en el header
    Authorization de cada request) y el refresh_token (larga vida, sirve para
    renovar el access sin re-login). api_client es el dueño de la instancia
    viva: la fija con set_tokens y muta access_token al refrescar. `expires_at`
    es opcional; api_client suele decodificar el `exp` del propio JWT.
    """
    access_token: str
    refresh_token: str
    expires_at: Optional[datetime] = None