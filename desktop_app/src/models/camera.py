"""
Modelos de datos para cámaras.
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from datetime import datetime


@dataclass
class Camera:
    """Modelo de cámara - sincronizado con backend SQLAlchemy."""
    id: int
    name: str
    ip_address: str
    rtsp_url: str = ""
    is_active: bool = True
    has_ptz: bool = False
    has_leds: bool = False
    has_audio: bool = False
    has_ai: bool = False  # NUEVO
    is_dual_lens: bool = False
    resolution_width: int = 1920
    resolution_height: int = 1080
    fps: int = 15
    status: str = "offline"
    owner_id: Optional[int] = None
    created_at: Optional[str] = None  # NUEVO - viene como string ISO del backend
    
    # Campos ONVIF opcionales
    onvif_url: Optional[str] = None  # NUEVO
    username: Optional[str] = None  # NUEVO
    password: Optional[str] = None  # NUEVO
    profile_token: Optional[str] = None  # NUEVO
    
    # Worker status (no viene de BD, se agrega dinámicamente)
    worker_status: Optional[Dict] = None
    
    # Permisos del usuario actual sobre esta cámara
    permissions: Dict[str, bool] = field(default_factory=lambda: {
        "view": True,
        "control_ptz": False,
        "control_leds": False,
        "control_audio": False,
        "download": False
    })
    
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
            "profile_token": self.profile_token
        }


@dataclass
class PTZCommand:
    """Comando PTZ."""
    direction: str  # up, down, left, right, zoom_in, zoom_out, stop
    speed: float = 0.5