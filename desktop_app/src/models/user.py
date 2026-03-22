"""
Modelos de datos para usuario y autenticación.
"""
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime


@dataclass
class User:
    """Modelo de usuario."""
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
    """Tokens de autenticación."""
    access_token: str
    refresh_token: str
    expires_at: Optional[datetime] = None