"""
Control PTZ tipo joystick con layout 3×3 + zoom + control de velocidad.

Patrón estándar NVR (Hikvision, Dahua, Milestone): press → start continuous
move; release → stop. NO usa auto-repeat — el ONVIF `ContinuousMove` ya
mueve la cámara continuamente hasta recibir `Stop`. El auto-repeat causaba
spam de requests y rate-limit.
"""
import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton,
    QLabel, QSlider, QFrame,
)
from PySide6.QtCore import Qt, Signal

from desktop_app.src.config import config

logger = logging.getLogger(__name__)


class PTZJoystick(QWidget):
    """
    Joystick PTZ visual con 8 direcciones + stop + zoom + velocidad.

    Emite:
      - move(direction: str, speed: float) cuando se presiona un botón direccional
      - stop()                              cuando se suelta
    """

    move = Signal(str, float)
    stop = Signal()

    # Mapeo botón → dirección (compatible con backend ptz_controller)
    _DIRECTIONS = [
        ("↖", "up_left",   0, 0),
        ("↑", "up",        0, 1),
        ("↗", "up_right",  0, 2),
        ("←", "left",      1, 0),
        ("●", "stop",      1, 1),  # botón central = stop manual
        ("→", "right",     1, 2),
        ("↙", "down_left", 2, 0),
        ("↓", "down",      2, 1),
        ("↘", "down_right",2, 2),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._speed = 0.5
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        # Estilo de la tarjeta
        self.setStyleSheet(f"""
            PTZJoystick {{
                background-color: {config.GLASS_BG};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: {config.BORDER_RADIUS}px;
            }}
        """)

        # Título
        title = QLabel("Control PTZ")
        title.setStyleSheet(
            f"color: {config.THEME_ACCENT}; font-weight: bold; font-size: 14px;"
        )
        layout.addWidget(title)

        # === Grid de dirección 3×3 ===
        grid_wrapper = QHBoxLayout()
        grid_wrapper.addStretch()

        grid = QGridLayout()
        grid.setSpacing(6)

        for label, direction, row, col in self._DIRECTIONS:
            btn = QPushButton(label)
            btn.setFixedSize(56, 56)
            btn.setCursor(Qt.PointingHandCursor)
            if direction == "stop":
                btn.setStyleSheet(self._stop_button_style())
                btn.clicked.connect(self._on_stop_click)
            else:
                btn.setStyleSheet(self._direction_button_style())
                btn.setAutoRepeat(False)  # CLAVE: sin auto-repeat
                btn.pressed.connect(lambda d=direction: self._on_press(d))
                btn.released.connect(self._on_release)
            grid.addWidget(btn, row, col)

        grid_wrapper.addLayout(grid)
        grid_wrapper.addStretch()
        layout.addLayout(grid_wrapper)

        # Separador
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {config.GLASS_BORDER};")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # === Zoom in/out ===
        zoom_label = QLabel("Zoom")
        zoom_label.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 11px; font-weight: bold;"
        )
        layout.addWidget(zoom_label)

        zoom_row = QHBoxLayout()
        zoom_row.addStretch()
        btn_zin = QPushButton("Acercar")
        btn_zin.setFixedHeight(40)
        btn_zin.setMinimumWidth(110)
        btn_zin.setCursor(Qt.PointingHandCursor)
        btn_zin.setStyleSheet(self._zoom_button_style())
        btn_zin.pressed.connect(lambda: self._on_press("zoom_in"))
        btn_zin.released.connect(self._on_release)
        zoom_row.addWidget(btn_zin)

        btn_zout = QPushButton("Alejar")
        btn_zout.setFixedHeight(40)
        btn_zout.setMinimumWidth(110)
        btn_zout.setCursor(Qt.PointingHandCursor)
        btn_zout.setStyleSheet(self._zoom_button_style())
        btn_zout.pressed.connect(lambda: self._on_press("zoom_out"))
        btn_zout.released.connect(self._on_release)
        zoom_row.addWidget(btn_zout)
        zoom_row.addStretch()
        layout.addLayout(zoom_row)

        # === Slider de velocidad ===
        speed_row = QHBoxLayout()
        speed_lbl = QLabel("Velocidad:")
        speed_lbl.setStyleSheet(f"color: {config.THEME_TEXT}; font-size: 11px;")
        speed_row.addWidget(speed_lbl)

        self.slider_speed = QSlider(Qt.Horizontal)
        self.slider_speed.setRange(10, 100)
        self.slider_speed.setValue(50)
        self.slider_speed.valueChanged.connect(self._on_speed_change)
        self.slider_speed.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background-color: {config.THEME_SECONDARY};
                height: 4px;
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background-color: {config.THEME_ACCENT};
                width: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }}
        """)
        speed_row.addWidget(self.slider_speed, 1)

        self.lbl_speed_val = QLabel("50%")
        self.lbl_speed_val.setStyleSheet(
            f"color: {config.THEME_ACCENT}; font-weight: bold; min-width: 35px;"
        )
        speed_row.addWidget(self.lbl_speed_val)
        layout.addLayout(speed_row)

    # ------------------------------------------------------------------
    # Estilos de botones
    # ------------------------------------------------------------------
    def _direction_button_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 8px;
                font-size: 20px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
            }}
            QPushButton:pressed {{
                background-color: #0ea5e9;
            }}
        """

    def _stop_button_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: {config.THEME_DANGER};
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 18px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #dc2626;
            }}
        """

    def _zoom_button_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
            }}
            QPushButton:pressed {{
                background-color: #0ea5e9;
            }}
        """

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------
    def _on_press(self, direction: str):
        """
        Envía UN solo comando ContinuousMove cuando se presiona el botón.
        La cámara seguirá moviéndose hasta recibir Stop (al release).
        """
        logger.debug(f"PTZ press: {direction} @ speed={self._speed:.2f}")
        self.move.emit(direction, self._speed)

    def _on_release(self):
        """Envía Stop cuando se suelta el botón."""
        logger.debug("PTZ release → stop")
        self.stop.emit()

    def _on_stop_click(self):
        """Botón central rojo: stop explícito (por si quedó moviéndose)."""
        logger.debug("PTZ stop (manual)")
        self.stop.emit()

    def _on_speed_change(self, value: int):
        self._speed = value / 100.0
        self.lbl_speed_val.setText(f"{value}%")
