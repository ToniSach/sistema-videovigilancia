"""
================================================================================
MÓDULO: desktop_app.models.camera — DTO de cámara (lado cliente)
================================================================================

PROPÓSITO
    Definir el DTO `Camera` del cliente (espejo del JSON de /cameras) y el
    pequeño value-object `PTZCommand`. Es lo que las vistas (dashboard, live,
    gestión de cámaras) manejan tras pedir cámaras por api_client.

RESPONSABILIDAD
    - Camera: agrupar identidad/red, URLs de stream (live por go2rtc), flags de
      capacidades (ptz/leds/audio/ia/dual_lens), estado, datos ONVIF/diagnóstico
      y los permisos del usuario actual sobre la cámara.
    - from_dict: construir tolerando campos EXTRA que el backend añada (forward-
      compatibility: no rompe el cliente si la API gana columnas).
    - to_dict: serializar el subconjunto editable para PUT/POST al backend.
    - PTZCommand: dirección + velocidad de un movimiento PTZ.

DEPENDENCIAS
    - dataclasses, typing, datetime (sin Qt ni backend).

COMPONENTES RELACIONADOS
    - Las vistas lo construyen con Camera.from_dict(resp.data) sobre la respuesta
      de api_client. stream_url / stream_url_l1/l2 / stream_urls los rellena el
      backend cuando GO2RTC_ENABLED=true; rtsp_video.py los reproduce con VLC
      (pipeline #3 Live), no van por api_client.

PUNTO DE ENTRADA
    `from desktop_app.src.models.camera import Camera, PTZCommand`.

SINCRONIZACIÓN
    Mantener en línea con backend/app/database/models.py:Camera (campos y
    semántica de is_dual_lens, permisos, etc.).
================================================================================
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
    # URL del restream de go2rtc (la añade el backend si GO2RTC_ENABLED).
    # Si está presente, el live se reproduce por RTSP/VLC vía go2rtc.
    stream_url: Optional[str] = None
    # Para cámaras dual-lens: una URL por lente (sub-streams recortados de go2rtc).
    stream_url_l1: Optional[str] = None
    stream_url_l2: Optional[str] = None
    # URLs por (lente|main) y calidad (high/medium/low). Lo llena el backend.
    # Ej: {"main": {"high": "...", "medium": "...", "low": "..."}}
    stream_urls: Optional[Dict] = None
    webrtc_url: Optional[str] = None
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
    """
    NIVEL 2 — DTO de un comando PTZ (pan/tilt/zoom).

    Rol: encapsular un movimiento de cámara que el joystick PTZ envía al backend
    (que lo traduce a ONVIF). `direction` es uno de up/down/left/right/zoom_in/
    zoom_out/stop; `speed` ∈ [0,1]. Lo serializa la vista al hacer el POST PTZ.
    """
    direction: str  # up, down, left, right, zoom_in, zoom_out, stop
    speed: float = 0.5
