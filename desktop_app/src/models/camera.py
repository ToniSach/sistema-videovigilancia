"""
Modelos de datos para cámaras.

Importante: el constructor tolera campos EXTRA que el backend pueda añadir
en futuras versiones (no rompe el frontend si la API gana columnas).
"""
from dataclasses import dataclass, field, fields
from typing import Optional, Dict, Any
from datetime import datetime


@dataclass
class Camera:
    """
    Modelo de cámara — sincronizado con el backend SQLAlchemy.

    Construir con `Camera.from_dict(data)` en vez de `Camera(**data)` para
    ignorar automáticamente claves que el backend devuelva pero el modelo
    no conozca (forward-compatibility).
    """
    id: int
    name: str
    ip_address: str
    rtsp_url: str = ""
    is_active: bool = True
    has_ptz: bool = False
    has_leds: bool = False
    has_audio: bool = False
    has_ai: bool = False
    is_dual_lens: bool = False
    resolution_width: int = 1920
    resolution_height: int = 1080
    fps: int = 15
    status: str = "offline"
    owner_id: Optional[int] = None
    created_at: Optional[str] = None

    # Campos ONVIF y conexión
    onvif_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    profile_token: Optional[str] = None

    # Campos de estado/diagnóstico (los devuelve el backend, sin estos rompía)
    connection_type: Optional[str] = None      # "onvif" | "rtsp_fallback" | "manual"
    last_error_code: Optional[str] = None      # "AUTH_FAILED", "CONN_REFUSED", etc.
    last_connected_at: Optional[str] = None
    fallback_url: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None

    # Worker status (dinámico)
    worker_status: Optional[Dict] = None

    # Permisos del usuario actual sobre esta cámara
    permissions: Dict[str, bool] = field(default_factory=lambda: {
        "view": True,
        "control_ptz": False,
        "control_leds": False,
        "control_audio": False,
        "download": False,
    })

    @classmethod
    def from_dict(cls, data: dict) -> "Camera":
        """
        Construye una Camera filtrando claves desconocidas.
        Esto evita que el frontend se rompa si el backend devuelve campos nuevos.
        """
        allowed = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in allowed}
        return cls(**filtered)

    def to_dict(self) -> dict:
        """Serializar para enviar al backend."""
        return {
            "id": self.id,
            "name": self.name,
            "ip_address": self.ip_address,
            "rtsp_url": self.rtsp_url,
            "is_active": self.is_active,
            "has_ptz": self.has_ptz,
            "has_leds": self.has_leds,
            "has_audio": self.has_audio,
            "has_ai": self.has_ai,
            "is_dual_lens": self.is_dual_lens,
            "resolution_width": self.resolution_width,
            "resolution_height": self.resolution_height,
            "fps": self.fps,
            "onvif_url": self.onvif_url,
            "username": self.username,
            "password": self.password,
            "profile_token": self.profile_token,
        }


@dataclass
class PTZCommand:
    """Comando PTZ."""
    direction: str  # up, down, left, right, zoom_in, zoom_out, stop
    speed: float = 0.5
