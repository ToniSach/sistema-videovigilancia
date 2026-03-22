"""
Componente de tarjeta con efecto Glassmorphism.
"""
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from desktop_app.src.config import config


class GlassCard(QFrame):
    """Frame con efecto glassmorphism."""
    
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