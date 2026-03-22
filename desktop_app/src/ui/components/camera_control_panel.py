# desktop_app/src/ui/components/camera_control_panel.py
"""
Panel de control para PTZ, LEDs y Audio de cámara.
"""
import logging
from typing import Optional

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                               QPushButton, QGroupBox, QSlider, QComboBox,
                               QGridLayout, QSizePolicy)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QIcon, QFont

from desktop_app.src.config import config
from desktop_app.src.services.api_client import api_client
from desktop_app.src.ui.components.glass_card import GlassCard
from desktop_app.src.ui.components.ptz_joystick import PTZJoystick

logger = logging.getLogger(__name__)


class LEDControlWidget(GlassCard):
    """Control de LEDs/IR."""
    
    def __init__(self, parent=None):
        super().__init__(parent, border_radius=8)
        
        self.camera_id: Optional[int] = None
        
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # Título
        lbl_title = QLabel("💡 Control de Iluminación")
        lbl_title.setStyleSheet(f"""
            color: {config.THEME_ACCENT};
            font-weight: bold;
            font-size: 14px;
        """)
        layout.addWidget(lbl_title)
        
        # Botones de modo
        btn_layout = QHBoxLayout()
        
        self.btn_auto = QPushButton("Auto")
        self.btn_auto.setCheckable(True)
        self.btn_auto.setChecked(True)
        self.btn_auto.clicked.connect(lambda: self._set_mode("auto"))
        
        self.btn_on = QPushButton("ON")
        self.btn_on.setCheckable(True)
        self.btn_on.clicked.connect(lambda: self._set_mode("on"))
        
        self.btn_off = QPushButton("OFF")
        self.btn_off.setCheckable(True)
        self.btn_off.clicked.connect(lambda: self._set_mode("off"))
        
        self.mode_buttons = [self.btn_auto, self.btn_on, self.btn_off]
        
        for btn in self.mode_buttons:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {config.THEME_SECONDARY};
                    color: {config.THEME_TEXT};
                    border: 1px solid {config.GLASS_BORDER};
                    border-radius: 6px;
                    padding: 8px 16px;
                }}
                QPushButton:checked {{
                    background-color: {config.THEME_ACCENT};
                    color: {config.THEME_PRIMARY};
                }}
            """)
            btn_layout.addWidget(btn)
        
        layout.addLayout(btn_layout)
        
        # Estado
        self.lbl_status = QLabel("Modo: Automático")
        self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(self.lbl_status)
    
    def set_camera(self, camera_id: int):
        self.camera_id = camera_id
    
    def _set_mode(self, mode: str):
        # Desmarcar otros botones
        for btn in self.mode_buttons:
            btn.setChecked(False)
        
        # Marcar el seleccionado
        if mode == "auto":
            self.btn_auto.setChecked(True)
            self.lbl_status.setText("Modo: Automático")
        elif mode == "on":
            self.btn_on.setChecked(True)
            self.lbl_status.setText("Modo: Forzado ON")
        else:
            self.btn_off.setChecked(True)
            self.lbl_status.setText("Modo: Forzado OFF")
        
        # Enviar a API
        if self.camera_id:
            def on_response(response):
                if not response.success:
                    logger.error(f"Error cambiando modo LED: {response.error}")
            
            api_client.post(f"cameras/{self.camera_id}/leds/{mode}", on_response)


class AudioControlWidget(GlassCard):
    """Control de audio bidireccional."""
    
    talk_started = Signal()
    talk_ended = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent, border_radius=8)
        
        self.camera_id: Optional[int] = None
        self._talking = False
        
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # Título
        lbl_title = QLabel("🎤 Audio Bidireccional")
        lbl_title.setStyleSheet(f"""
            color: {config.THEME_ACCENT};
            font-weight: bold;
            font-size: 14px;
        """)
        layout.addWidget(lbl_title)
        
        # Botón PTT (Push to Talk)
        self.btn_ptt = QPushButton("🔴 MANTENER PRESIONADO PARA HABLAR")
        self.btn_ptt.setMinimumHeight(60)
        self.btn_ptt.setStyleSheet(f"""
            QPushButton {{
                background-color: {config.THEME_DANGER};
                color: white;
                border: none;
                border-radius: 8px;
                font-weight: bold;
                font-size: 14px;
            }}
            QPushButton:pressed {{
                background-color: #dc2626;
            }}
        """)
        
        # Eventos de mouse para PTT
        self.btn_ptt.pressed.connect(self._start_talk)
        self.btn_ptt.released.connect(self._stop_talk)
        
        layout.addWidget(self.btn_ptt)
        
        # Volumen de recepción
        vol_layout = QHBoxLayout()
        vol_layout.addWidget(QLabel("Volumen:"))
        
        self.slider_volume = QSlider(Qt.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(80)
        self.slider_volume.valueChanged.connect(self._set_volume)
        
        vol_layout.addWidget(self.slider_volume)
        layout.addLayout(vol_layout)
        
        # Estado
        self.lbl_status = QLabel("Listo")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")
        layout.addWidget(self.lbl_status)
    
    def set_camera(self, camera_id: int):
        self.camera_id = camera_id
    
    def _start_talk(self):
        if not self.camera_id or self._talking:
            return
        
        self._talking = True
        self.lbl_status.setText("🔴 Transmitiendo...")
        self.lbl_status.setStyleSheet(f"color: {config.THEME_DANGER}; font-weight: bold;")
        
        def on_response(response):
            if response.success:
                self.talk_started.emit()
            else:
                self._talking = False
                self.lbl_status.setText("Error al iniciar")
                self.lbl_status.setStyleSheet(f"color: {config.THEME_DANGER};")
        
        api_client.post(f"cameras/{self.camera_id}/audio/talk", on_response, data={"action": "start"})
    
    def _stop_talk(self):
        if not self._talking:
            return
        
        self._talking = False
        self.lbl_status.setText("Listo")
        self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")
        
        def on_response(response):
            self.talk_ended.emit()
        
        api_client.post(f"cameras/{self.camera_id}/audio/talk", on_response, data={"action": "stop"})
    
    def _set_volume(self, value):
        # Aquí se ajustaría el volumen del stream de audio entrante
        pass


class CameraControlPanel(QWidget):
    """Panel completo de control para cámara seleccionada."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.current_camera_id: Optional[int] = None
        
        self._setup_ui()
        self.hide()  # Inicialmente oculto
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        
        # Header
        header = QLabel("🎛️ Controles de Cámara")
        header.setStyleSheet(f"""
            color: {config.THEME_TEXT};
            font-size: 18px;
            font-weight: bold;
            padding-bottom: 8px;
            border-bottom: 1px solid {config.GLASS_BORDER};
        """)
        layout.addWidget(header)
        
        # PTZ Joystick (usando el existente)
        self.ptz_widget = PTZJoystick()
        self.ptz_widget.move.connect(self._on_ptz_move)
        self.ptz_widget.stop.connect(self._on_ptz_stop)
        layout.addWidget(self.ptz_widget)
        
        # Presets PTZ
        preset_layout = QHBoxLayout()
        self.cmb_presets = QComboBox()
        self.cmb_presets.setPlaceholderText("Ir a preset...")
        self.cmb_presets.setStyleSheet(f"""
            QComboBox {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 6px;
            }}
        """)
        preset_layout.addWidget(self.cmb_presets)
        
        self.btn_go_preset = QPushButton("Ir")
        self.btn_go_preset.clicked.connect(self._go_to_preset)
        preset_layout.addWidget(self.btn_go_preset)
        
        self.btn_save_preset = QPushButton("Guardar Actual")
        self.btn_save_preset.clicked.connect(self._save_preset)
        preset_layout.addWidget(self.btn_save_preset)
        
        layout.addLayout(preset_layout)
        
        # LEDs
        self.led_widget = LEDControlWidget()
        layout.addWidget(self.led_widget)
        
        # Audio
        self.audio_widget = AudioControlWidget()
        layout.addWidget(self.audio_widget)
        
        layout.addStretch()
    
    def set_camera(self, camera_id: int, camera_data: dict):
        """Configura el panel para una cámara específica."""
        self.current_camera_id = camera_id
        
        # Habilitar/deshabilitar según capacidades
        has_ptz = camera_data.get("has_ptz", False)
        has_leds = camera_data.get("has_leds", False)
        has_audio = camera_data.get("has_audio", False)
        
        self.ptz_widget.setEnabled(has_ptz)
        self.led_widget.setEnabled(has_leds)
        self.led_widget.set_camera(camera_id)
        self.audio_widget.setEnabled(has_audio)
        self.audio_widget.set_camera(camera_id)
        
        if has_ptz:
            self._load_presets()
        
        self.show()
    
    def clear(self):
        """Limpia el panel."""
        self.current_camera_id = None
        self.hide()
    
    def _on_ptz_move(self, direction: str, speed: float):
        if not self.current_camera_id:
            return
        
        def on_response(response):
            if not response.success:
                logger.warning(f"Error PTZ: {response.error}")
        
        api_client.post(f"cameras/{self.current_camera_id}/ptz/{direction}", on_response)
    
    def _on_ptz_stop(self):
        if not self.current_camera_id:
            return
        
        def on_response(response):
            pass
        
        api_client.post(f"cameras/{self.current_camera_id}/ptz/stop", on_response)
    
    def _load_presets(self):
        # Cargar presets disponibles
        if not self.current_camera_id:
            return
        
        def on_response(response):
            if response.success:
                self.cmb_presets.clear()
                presets = response.data.get("presets", [])
                for preset in presets:
                    self.cmb_presets.addItem(preset.get("name", "Sin nombre"), preset.get("token"))
        
        api_client.get(f"cameras/{self.current_camera_id}/ptz/presets", on_response)
    
    def _go_to_preset(self):
        if not self.current_camera_id or self.cmb_presets.currentIndex() < 0:
            return
        
        preset_token = self.cmb_presets.currentData()
        
        def on_response(response):
            if not response.success:
                QMessageBox.warning(self, "PTZ", "No se pudo mover al preset")
        
        api_client.post(f"cameras/{self.current_camera_id}/ptz/goto/{preset_token}", on_response)
    
    def _save_preset(self):
        # Diálogo simple para nombre del preset
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Guardar Preset", "Nombre de la posición:")
        if ok and name:
            def on_response(response):
                if response.success:
                    self._load_presets()
            
            api_client.post(f"cameras/{self.current_camera_id}/ptz/preset", on_response, data={"name": name})