"""
Control PTZ tipo joystick/virtual pad.
"""
import logging
from typing import Callable

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent

from desktop_app.src.config import config

logger = logging.getLogger(__name__)


class PTZJoystick(QWidget):
    """Control direccional PTZ con soporte para presionar y mantener."""
    
    # Señales
    move = Signal(str, float)  # dirección, velocidad
    stop = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self._setup_ui()
        self._pressed_button: str = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_repeat)
        self._repeat_interval = 100  # ms
        self._speed = 0.5
    
    def _setup_ui(self):
        """Construye interfaz de joystick."""
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 8, 8, 8)
        
        # Estilo glass
        self.setStyleSheet(f"""
            PTZJoystick {{
                background-color: {config.GLASS_BG};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: {config.BORDER_RADIUS}px;
            }}
            QPushButton {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: none;
                border-radius: 6px;
                padding: 8px;
                min-width: 30px;
                min-height: 30px;
            }}
            QPushButton:hover {{
                background-color: {config.THEME_ACCENT};
            }}
            QPushButton:pressed {{
                background-color: {config.THEME_ACCENT}.darker(120);
            }}
        """)
        
        # Grid direccional
        grid = QHBoxLayout()
        
        # Botón Up
        self.btn_up = QPushButton("▲")
        self.btn_up.setAutoRepeat(True)
        self.btn_up.setAutoRepeatInterval(self._repeat_interval)
        self.btn_up.pressed.connect(lambda: self._start_move("up"))
        self.btn_up.released.connect(self._stop_move)
        
        # Botón Down
        self.btn_down = QPushButton("▼")
        self.btn_down.setAutoRepeat(True)
        self.btn_down.setAutoRepeatInterval(self._repeat_interval)
        self.btn_down.pressed.connect(lambda: self._start_move("down"))
        self.btn_down.released.connect(self._stop_move)
        
        # Botón Left
        self.btn_left = QPushButton("◀")
        self.btn_left.setAutoRepeat(True)
        self.btn_left.setAutoRepeatInterval(self._repeat_interval)
        self.btn_left.pressed.connect(lambda: self._start_move("left"))
        self.btn_left.released.connect(self._stop_move)
        
        # Botón Right
        self.btn_right = QPushButton("▶")
        self.btn_right.setAutoRepeat(True)
        self.btn_right.setAutoRepeatInterval(self._repeat_interval)
        self.btn_right.pressed.connect(lambda: self._start_move("right"))
        self.btn_right.released.connect(self._stop_move)
        
        # Layout cruz
        v_layout = QVBoxLayout()
        v_layout.addWidget(self.btn_up, alignment=Qt.AlignCenter)
        
        h_layout = QHBoxLayout()
        h_layout.addWidget(self.btn_left)
        h_layout.addWidget(self.btn_right)
        v_layout.addLayout(h_layout)
        
        v_layout.addWidget(self.btn_down, alignment=Qt.AlignCenter)
        
        layout.addLayout(v_layout)
        
        # Zoom buttons
        zoom_layout = QHBoxLayout()
        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_out = QPushButton("-")
        
        for btn, direction in [(self.btn_zoom_in, "zoom_in"), (self.btn_zoom_out, "zoom_out")]:
            btn.setAutoRepeat(True)
            btn.setAutoRepeatInterval(self._repeat_interval)
            btn.pressed.connect(lambda d=direction: self._start_move(d))
            btn.released.connect(self._stop_move)
            zoom_layout.addWidget(btn)
        
        layout.addLayout(zoom_layout)
        
        # Label
        self.lbl_status = QLabel("PTZ Ready")
        self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 10px;")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_status)
    
    def _start_move(self, direction: str):
        """Inicia movimiento continuo."""
        self._pressed_button = direction
        self.move.emit(direction, self._speed)
        self.lbl_status.setText(f"Moving {direction}...")
    
    def _stop_move(self):
        """Detiene movimiento."""
        if self._pressed_button:
            self.stop.emit()
            self._pressed_button = None
            self.lbl_status.setText("PTZ Ready")
    
    def _on_repeat(self):
        """Repetición mientras se mantiene presionado."""
        if self._pressed_button:
            self.move.emit(self._pressed_button, self._speed)
    
    def set_speed(self, speed: float):
        """Cambia velocidad (0.1 - 1.0)."""
        self._speed = max(0.1, min(1.0, speed))