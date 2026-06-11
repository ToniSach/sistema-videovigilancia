"""
================================================================================
MÓDULO: ui.components.glass_card — Tarjeta con efecto "glassmorphism"
================================================================================

PROPÓSITO
    QFrame base reutilizable con aspecto glassmorphism (fondo semitransparente,
    borde sutil, esquinas redondeadas y sombra suave). Sirve de contenedor visual
    coherente para agrupar controles en toda la app.

RESPONSABILIDAD
    Fijar el estilo (QSS), el efecto de sombra (QGraphicsDropShadowEffect) y un
    tamaño mínimo para que la sombra sea visible. No tiene lógica de negocio.

DEPENDENCIAS
    PySide6 (QFrame, QGraphicsDropShadowEffect), config (colores GLASS_BG /
    GLASS_BORDER y radio por defecto BORDER_RADIUS).

COMPONENTES RELACIONADOS
    camera_control_panel.py (sus sub-cards LED/Audio/AI/Recording heredan de
    GlassCard), y vistas que lo usan como tarjeta de sección/estadísticas.

DÓNDE SE USA
    Como clase base de las sub-cards de control y como contenedor en dashboards.
================================================================================
"""
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from desktop_app.src.config import config


class GlassCard(QFrame):
    """QFrame con efecto glassmorphism (fondo, borde, esquinas y sombra).

    Rol: contenedor visual reutilizable (decorativo, sin lógica).
    Quién la instancia/consume: las sub-cards de camera_control_panel heredan de
        ella; vistas y diálogos la usan como tarjeta de sección.
    Señales Qt: ninguna.
    Dependencias: config (GLASS_BG, GLASS_BORDER, BORDER_RADIUS).

    Parámetros:
        border_radius: radio de esquina en px; si es None usa config.BORDER_RADIUS.
    """

    def __init__(self, parent=None, border_radius: int = None):
        super().__init__(parent)
        
        if border_radius is None:
            border_radius = config.BORDER_RADIUS
        
        self.setObjectName("glassCard")
        
        # Configurar frame
        self.setFrameShape(QFrame.StyledPanel)
        self.setAttribute(Qt.WA_StyledBackground, True)
        
        # Efecto de sombra
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 60))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)
        
        # Estilo CSS
        self.setStyleSheet(f"""
            #glassCard {{
                background-color: {config.GLASS_BG};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: {border_radius}px;
            }}
        """)
        
        # Tamaño mínimo para sombra visible
        self.setMinimumSize(100, 50)