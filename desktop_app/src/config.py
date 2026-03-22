"""
Configuración global del frontend.
"""
import os
from dataclasses import dataclass
from typing import Optional


def lighten_color(hex_color: str, percent: int = 20) -> str:
    """Aclara un color hex para usar en QSS."""
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    
    r = min(255, int(r + (255 - r) * percent / 100))
    g = min(255, int(g + (255 - g) * percent / 100))
    b = min(255, int(b + (255 - b) * percent / 100))
    
    return f"#{r:02x}{g:02x}{b:02x}"


def darken_color(hex_color: str, percent: int = 20) -> str:
    """Oscurece un color hex para usar en QSS."""
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    
    r = max(0, int(r * (100 - percent) / 100))
    g = max(0, int(g * (100 - percent) / 100))
    b = max(0, int(b * (100 - percent) / 100))
    
    return f"#{r:02x}{g:02x}{b:02x}"


@dataclass
class Config:
    """Configuración de la aplicación."""
    
    # Backend
    API_BASE_URL: str = "http://localhost:5000/api/v1"
    TIMEOUT: int = 30
    
    # Video
    VLC_OPTIONS: list = None
    STREAM_BUFFER_SIZE: int = 1024 * 1024
    
    # UI - Colores base
    THEME_PRIMARY: str = "#0f172a"
    THEME_SECONDARY: str = "#1e293b"
    THEME_ACCENT: str = "#38bdf8"
    THEME_ACCENT_SECONDARY: str = "#818cf8"
    THEME_TEXT: str = "#f1f5f9"
    THEME_TEXT_MUTED: str = "#94a3b8"
    THEME_DANGER: str = "#ef4444"
    
    # Colores calculados (para QSS)
    @property
    def THEME_ACCENT_LIGHT(self):
        return lighten_color(self.THEME_ACCENT, 20)
    
    @property
    def THEME_ACCENT_DARK(self):
        return darken_color(self.THEME_ACCENT, 20)
    
    # Glassmorphism
    GLASS_BG: str = "rgba(30, 41, 59, 0.7)"
    GLASS_BORDER: str = "rgba(255, 255, 255, 0.1)"
    BORDER_RADIUS: int = 12
    
    def __post_init__(self):
        if self.VLC_OPTIONS is None:
            self.VLC_OPTIONS = [
                '--quiet',
                '--no-video-title-show',
                '--network-caching=500',
            ]


# Instancia global
config = Config()