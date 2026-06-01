# desktop_app/src/ui/components/camera_control_panel.py
"""
Panel de control para PTZ, LEDs y Audio de cámara.
"""
import logging
from PySide6.QtWidgets import QMessageBox
from typing import Optional

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QGroupBox, QSlider, QComboBox,
                               QGridLayout, QSizePolicy, QScrollArea, QFrame,
                               QTabWidget)
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
        lbl_title = QLabel("Control de Iluminación")
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
    """Control de audio bidireccional (talk + listen) con selector de micrófono."""

    talk_started = Signal()
    talk_ended = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, border_radius=8)

        self.camera_id: Optional[int] = None
        self._talking = False
        self._listening = False
        self._mics_loaded = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Título
        lbl_title = QLabel("Audio Bidireccional")
        lbl_title.setStyleSheet(f"""
            color: {config.THEME_ACCENT};
            font-weight: bold;
            font-size: 14px;
        """)
        layout.addWidget(lbl_title)

        # Selector de micrófono
        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("Mic:"))
        self.cmb_mic = QComboBox()
        self.cmb_mic.addItem("(default)")
        self.cmb_mic.setStyleSheet(f"""
            QComboBox {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 4px;
            }}
        """)
        mic_row.addWidget(self.cmb_mic, 1)
        self.btn_reload_mics = QPushButton("")
        self.btn_reload_mics.setMaximumWidth(36)
        self.btn_reload_mics.setToolTip("Detectar micrófonos disponibles")
        self.btn_reload_mics.clicked.connect(self._load_mics)
        mic_row.addWidget(self.btn_reload_mics)
        layout.addLayout(mic_row)

        # Botón PTT (Push to Talk)
        self.btn_ptt = QPushButton("MANTENER PRESIONADO PARA HABLAR")
        self.btn_ptt.setMinimumHeight(50)
        self.btn_ptt.setStyleSheet(f"""
            QPushButton {{
                background-color: {config.THEME_DANGER};
                color: white;
                border: none;
                border-radius: 8px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:pressed {{
                background-color: #dc2626;
            }}
        """)
        self.btn_ptt.pressed.connect(self._start_talk)
        self.btn_ptt.released.connect(self._stop_talk)
        layout.addWidget(self.btn_ptt)

        # Botón Listen (toggle)
        self.btn_listen = QPushButton("Escuchar cámara")
        self.btn_listen.setMinimumHeight(40)
        self.btn_listen.setCheckable(True)
        self.btn_listen.toggled.connect(self._toggle_listen)
        self.btn_listen.setStyleSheet(f"""
            QPushButton {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 8px;
                padding: 8px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:checked {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
            }}
        """)
        layout.addWidget(self.btn_listen)

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
        # Cargar mics solo una vez (es lento: ffmpeg list_devices)
        if not self._mics_loaded:
            self._mics_loaded = True
            self._load_mics()

    def _load_mics(self):
        """Carga la lista de micrófonos disponibles desde el backend."""
        def on_response(response):
            self.cmb_mic.clear()
            self.cmb_mic.addItem("(default)")
            if response.success and response.data:
                for d in response.data.get("devices", []):
                    self.cmb_mic.addItem(d)

        api_client.get("cameras/audio/devices", on_response)
    
    def _start_talk(self):
        if not self.camera_id or self._talking:
            return

        self._talking = True
        self.lbl_status.setText("Transmitiendo...")
        self.lbl_status.setStyleSheet(f"color: {config.THEME_DANGER}; font-weight: bold;")

        # Incluir el mic seleccionado (o nada si está en "default")
        body = {}
        mic = self.cmb_mic.currentText()
        if mic and mic != "(default)":
            body["mic_device"] = mic

        def on_response(response):
            if response.success:
                self.talk_started.emit()
            else:
                self._talking = False
                err = response.error or "Error al iniciar"
                self.lbl_status.setText(f"Error: {err[:60]}")
                self.lbl_status.setStyleSheet(f"color: {config.THEME_DANGER};")

        api_client.post(f"cameras/{self.camera_id}/audio/talk", on_response, data=body)

    def _stop_talk(self):
        if not self._talking:
            return

        self._talking = False
        self.lbl_status.setText("Listo")
        self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")

        def on_response(response):
            self.talk_ended.emit()

        api_client.post(f"cameras/{self.camera_id}/audio/stop", on_response)

    def _toggle_listen(self, checked: bool):
        """Activa/desactiva la reproducción del audio de la cámara en el host."""
        if not self.camera_id:
            self.btn_listen.setChecked(False)
            return

        if checked:
            self.btn_listen.setText("Detener escucha")

            def on_started(response):
                if response.success:
                    self._listening = True
                    self.lbl_status.setText("Escuchando audio de la cámara")
                    self.lbl_status.setStyleSheet(
                        f"color: {config.THEME_ACCENT}; font-weight: bold;"
                    )
                else:
                    err = response.error or "Error"
                    self.btn_listen.setChecked(False)
                    self.btn_listen.setText("Escuchar cámara")
                    self.lbl_status.setText(f"Listen falló: {err[:60]}")
                    self.lbl_status.setStyleSheet(f"color: {config.THEME_DANGER};")
                    QMessageBox.warning(self, "Escuchar", f"No se pudo iniciar:\n{err[:300]}")

            api_client.post(f"cameras/{self.camera_id}/audio/listen/start", on_started)
        else:
            self.btn_listen.setText("Escuchar cámara")

            def on_stopped(response):
                self._listening = False
                self.lbl_status.setText("Escucha detenida")
                self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")

            api_client.post(f"cameras/{self.camera_id}/audio/listen/stop", on_stopped)
    
    def _set_volume(self, value):
        # Aquí se ajustaría el volumen del stream de audio entrante
        pass


class AIControlWidget(GlassCard):
    """
    Control de IA (YOLOv8) por cámara y por lente.
    Llama a los endpoints REST:
        POST /api/v1/ai/<id>/activate    body: {lens, mode}
        POST /api/v1/ai/<id>/deactivate  body: {lens}
        GET  /api/v1/ai/<id>             → estado por lente
    """

    activated = Signal()
    deactivated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, border_radius=8)

        self.camera_id: Optional[int] = None
        self._is_dual_lens = False
        self._active_lenses: set[str] = set()

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        lbl_title = QLabel("Detección de objetos")
        lbl_title.setStyleSheet(f"""
            color: {config.THEME_ACCENT};
            font-weight: bold;
            font-size: 14px;
        """)
        layout.addWidget(lbl_title)

        # Aviso: solo una cámara puede tener la detección activa a la vez.
        # Si está en OTRA cámara, lo indicamos aquí (se actualiza en _refresh_status).
        self.lbl_global_ai = QLabel("")
        self.lbl_global_ai.setWordWrap(True)
        self.lbl_global_ai.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 11px;"
        )
        layout.addWidget(self.lbl_global_ai)

        # Selector de lente
        lens_layout = QHBoxLayout()
        lens_layout.addWidget(QLabel("Lente:"))
        self.cmb_lens = QComboBox()
        self.cmb_lens.addItem("main")
        self.cmb_lens.setStyleSheet(self._combo_style())
        lens_layout.addWidget(self.cmb_lens, 1)
        layout.addLayout(lens_layout)

        # Selector de modo
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Modo:"))
        # Etiquetas comerciales; el valor real (low_cpu/high_quality) va como data.
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItem("Bajo consumo (rápido)", "low_cpu")
        self.cmb_mode.addItem("Alta precisión", "high_quality")
        self.cmb_mode.setStyleSheet(self._combo_style())
        mode_layout.addWidget(self.cmb_mode, 1)
        layout.addLayout(mode_layout)

        # Botones
        btn_layout = QHBoxLayout()
        self.btn_activate = QPushButton("▶ Activar IA")
        self.btn_activate.clicked.connect(self._activate)
        self.btn_deactivate = QPushButton("Desactivar")
        self.btn_deactivate.clicked.connect(self._deactivate)
        for b in (self.btn_activate, self.btn_deactivate):
            b.setStyleSheet(self._button_style())
        btn_layout.addWidget(self.btn_activate)
        btn_layout.addWidget(self.btn_deactivate)
        layout.addLayout(btn_layout)

        # Estado
        self.lbl_status = QLabel("IA: desactivada")
        self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(self.lbl_status)

    def _combo_style(self) -> str:
        return f"""
            QComboBox {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 6px;
            }}
        """

    def _button_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 8px 12px;
            }}
            QPushButton:hover {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
            }}
        """

    def set_camera(self, camera_id: int, is_dual_lens: bool = False):
        self.camera_id = camera_id
        self._is_dual_lens = is_dual_lens
        self.cmb_lens.clear()
        if is_dual_lens:
            self.cmb_lens.addItems(["l1", "l2"])
        else:
            self.cmb_lens.addItem("main")
        self._refresh_status()

    def _activate(self):
        if not self.camera_id:
            return
        lens = self.cmb_lens.currentText()
        mode = self.cmb_mode.currentData() or "low_cpu"
        mode_label = self.cmb_mode.currentText()

        def on_response(response):
            if response.success:
                self._active_lenses.add(lens)
                self.lbl_status.setText(f"Detección activa en {lens} · {mode_label}")
                self.lbl_status.setStyleSheet(
                    f"color: {config.THEME_ACCENT}; font-weight: bold;"
                )
                self.activated.emit()
            else:
                err = response.error or "Error desconocido"
                self.lbl_status.setText(f"Error: {err[:60]}")
                self.lbl_status.setStyleSheet(f"color: {config.THEME_DANGER};")

        api_client.post(
            f"ai/{self.camera_id}/activate", on_response,
            data={"lens": lens, "mode": mode},
        )

    def _deactivate(self):
        if not self.camera_id:
            return
        lens = self.cmb_lens.currentText()

        def on_response(response):
            if response.success:
                self._active_lenses.discard(lens)
                self.lbl_status.setText("IA: desactivada")
                self.lbl_status.setStyleSheet(
                    f"color: {config.THEME_TEXT_MUTED}; font-size: 12px;"
                )
                self.deactivated.emit()
            else:
                err = response.error or "Error desconocido"
                self.lbl_status.setText(f"Error: {err[:60]}")

        api_client.post(
            f"ai/{self.camera_id}/deactivate", on_response,
            data={"lens": lens},
        )

    def _refresh_status(self):
        if not self.camera_id:
            return

        def on_response(response):
            if response.success and response.data:
                active = []
                for lens in ("main", "l1", "l2"):
                    if response.data.get(lens):
                        active.append(lens)
                        self._active_lenses.add(lens)
                if active:
                    self.lbl_status.setText(f"Detección activa en: {', '.join(active)}")
                    self.lbl_status.setStyleSheet(
                        f"color: {config.THEME_ACCENT}; font-weight: bold;"
                    )
                else:
                    self.lbl_status.setText("Detección: desactivada")
                    self.lbl_status.setStyleSheet(
                        f"color: {config.THEME_TEXT_MUTED}; font-size: 12px;"
                    )

        api_client.get(f"ai/{self.camera_id}", on_response)
        self._refresh_global_ai()

    def _refresh_global_ai(self):
        """Indica si la detección está activa en OTRA cámara (solo 1 a la vez)."""
        def on_global(response):
            if not response.success:
                self.lbl_global_ai.setText("")
                return
            active = (response.data or {}).get("active") or []
            others = [a for a in active if a.get("camera_id") != self.camera_id]
            if others:
                cams = ", ".join(f"cámara {a['camera_id']}" for a in others)
                self.lbl_global_ai.setText(
                    f"⚠ La detección está activa en {cams}. Solo una cámara puede "
                    f"tenerla a la vez; activarla aquí la moverá."
                )
                self.lbl_global_ai.setStyleSheet(
                    "color: #fbbf24; font-size: 11px;"
                )
            else:
                self.lbl_global_ai.setText("")
        api_client.get("ai/status", on_global)


class RecordingControlWidget(GlassCard):
    """
    Control de grabación continua manual.
    Llama a los endpoints REST:
        POST /api/v1/recordings/manual/start/<id>
        POST /api/v1/recordings/manual/stop/<id>
        GET  /api/v1/recordings/manual/status/<id>
    """

    started = Signal()
    stopped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, border_radius=8)

        self.camera_id: Optional[int] = None
        self._recording = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        lbl_title = QLabel("Grabación manual")
        lbl_title.setStyleSheet(f"""
            color: {config.THEME_ACCENT};
            font-weight: bold;
            font-size: 14px;
        """)
        layout.addWidget(lbl_title)

        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("Iniciar")
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QPushButton("Detener")
        self.btn_stop.clicked.connect(self._stop)
        for b in (self.btn_start, self.btn_stop):
            b.setStyleSheet(f"""
                QPushButton {{
                    background-color: {config.THEME_SECONDARY};
                    color: {config.THEME_TEXT};
                    border: 1px solid {config.GLASS_BORDER};
                    border-radius: 6px;
                    padding: 8px 12px;
                }}
                QPushButton:hover {{
                    background-color: {config.THEME_ACCENT};
                    color: {config.THEME_PRIMARY};
                }}
            """)
        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)
        layout.addLayout(btn_layout)

        self.lbl_status = QLabel("Sin grabar")
        self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(self.lbl_status)

    def set_camera(self, camera_id: int):
        self.camera_id = camera_id
        self._refresh_status()

    def _start(self):
        if not self.camera_id:
            return

        def on_response(response):
            if response.success:
                self._recording = True
                self.lbl_status.setText("Grabando")
                self.lbl_status.setStyleSheet(
                    f"color: {config.THEME_DANGER}; font-weight: bold;"
                )
                self.started.emit()
            else:
                err = response.error or "Error"
                self.lbl_status.setText(f"Error: {err[:80]}")

        api_client.post(f"recordings/manual/start/{self.camera_id}", on_response)

    def _stop(self):
        if not self.camera_id:
            return

        def on_response(response):
            self._recording = False
            self.lbl_status.setText("Sin grabar")
            self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")
            self.stopped.emit()

        api_client.post(f"recordings/manual/stop/{self.camera_id}", on_response)

    def _refresh_status(self):
        if not self.camera_id:
            return

        def on_response(response):
            if response.success and response.data:
                self._recording = response.data.get("recording", False)
                if self._recording:
                    self.lbl_status.setText("Grabando")
                    self.lbl_status.setStyleSheet(
                        f"color: {config.THEME_DANGER}; font-weight: bold;"
                    )
                else:
                    self.lbl_status.setText("Sin grabar")
                    self.lbl_status.setStyleSheet(
                        f"color: {config.THEME_TEXT_MUTED};"
                    )

        api_client.get(f"recordings/manual/status/{self.camera_id}", on_response)


class CameraControlPanel(QWidget):
    """Panel completo de control para cámara seleccionada."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.current_camera_id: Optional[int] = None
        
        self._setup_ui()
        self.hide()  # Inicialmente oculto
    
    def _setup_ui(self):
        """
        Layout NUEVO: pestañas en lugar de scroll vertical de 6 widgets.
        Antes todo estaba apilado en un QScrollArea, y aunque cabía, el
        usuario tenía que hacer scroll constantemente para llegar a IA,
        Grabación, etc. Con pestañas todo cabe sin scroll en una pantalla
        razonable y la navegación es más rápida.
        """
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header
        header = QLabel("Controles de Cámara")
        header.setStyleSheet(f"""
            color: {config.THEME_TEXT};
            font-size: 18px;
            font-weight: bold;
            padding: 8px 12px;
            border-bottom: 1px solid {config.GLASS_BORDER};
            background-color: {config.THEME_PRIMARY};
        """)
        outer.addWidget(header)

        # ----- Pestañas -----
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {config.GLASS_BORDER};
                background-color: {config.THEME_PRIMARY};
                border-top: none;
            }}
            QTabBar::tab {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT_MUTED};
                padding: 8px 14px;
                border: 1px solid {config.GLASS_BORDER};
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
                font-size: 11px;
            }}
            QTabBar::tab:selected {{
                background-color: {config.THEME_PRIMARY};
                color: {config.THEME_ACCENT};
                font-weight: bold;
            }}
            QTabBar::tab:hover:!selected {{
                color: {config.THEME_TEXT};
            }}
        """)
        outer.addWidget(self.tabs, 1)

        # ============= PESTAÑA 1: MOVIMIENTO (PTZ + presets) =============
        self.tabs.addTab(self._build_movement_tab(), "Movimiento")

        # ============= PESTAÑA 2: IA + Grabación =============
        self.tabs.addTab(self._build_ai_recording_tab(), "IA / REC")

        # ============= PESTAÑA 3: Audio + LEDs =============
        self.tabs.addTab(self._build_audio_leds_tab(), "Audio / Luz")

    def _scroll_wrap(self, content: QWidget) -> QScrollArea:
        """Envuelve un widget en QScrollArea (fallback si contenido es alto)."""
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setStyleSheet(f"""
            QScrollArea {{ background-color: transparent; }}
            QScrollBar:vertical {{
                background-color: {config.THEME_SECONDARY};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background-color: {config.THEME_ACCENT};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        sa.setWidget(content)
        return sa

    def _build_movement_tab(self) -> QWidget:
        """Tab Movimiento: PTZ joystick + presets."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # PTZ joystick (PTZ 3x3 + zoom + velocidad)
        self.ptz_widget = PTZJoystick()
        self.ptz_widget.move.connect(self._on_ptz_move)
        self.ptz_widget.stop.connect(self._on_ptz_stop)
        layout.addWidget(self.ptz_widget)

        # Card de Presets (separada visualmente)
        preset_card = GlassCard(border_radius=8)
        preset_layout = QVBoxLayout(preset_card)
        preset_layout.setContentsMargins(12, 12, 12, 12)
        preset_layout.setSpacing(8)

        preset_title = QLabel("Presets PTZ")
        preset_title.setStyleSheet(
            f"color: {config.THEME_ACCENT}; font-weight: bold; font-size: 13px;"
        )
        preset_layout.addWidget(preset_title)

        # Row 1: combo de presets ocupa todo el ancho
        self.cmb_presets = QComboBox()
        self.cmb_presets.setPlaceholderText("Ir a preset…")
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

        # Row 2: dos botones a ancho igual
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_go_preset = QPushButton("▶  Ir")
        self.btn_go_preset.setMinimumHeight(34)
        self.btn_go_preset.clicked.connect(self._go_to_preset)
        self.btn_save_preset = QPushButton("Guardar actual")
        self.btn_save_preset.setMinimumHeight(34)
        self.btn_save_preset.clicked.connect(self._save_preset)
        for b in (self.btn_go_preset, self.btn_save_preset):
            b.setStyleSheet(f"""
                QPushButton {{
                    background-color: {config.THEME_SECONDARY};
                    color: {config.THEME_TEXT};
                    border: 1px solid {config.GLASS_BORDER};
                    border-radius: 6px;
                    padding: 6px;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background-color: {config.THEME_ACCENT};
                    color: {config.THEME_PRIMARY};
                }}
            """)
        btn_row.addWidget(self.btn_go_preset, 1)
        btn_row.addWidget(self.btn_save_preset, 1)
        preset_layout.addLayout(btn_row)

        layout.addWidget(preset_card)
        layout.addStretch()
        return self._scroll_wrap(content)

    def _build_ai_recording_tab(self) -> QWidget:
        """Tab IA + Grabación manual."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        self.ai_widget = AIControlWidget()
        layout.addWidget(self.ai_widget)

        self.rec_widget = RecordingControlWidget()
        layout.addWidget(self.rec_widget)

        layout.addStretch()
        return self._scroll_wrap(content)

    def _build_audio_leds_tab(self) -> QWidget:
        """Tab Audio + LEDs."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        self.audio_widget = AudioControlWidget()
        layout.addWidget(self.audio_widget)

        self.led_widget = LEDControlWidget()
        layout.addWidget(self.led_widget)

        layout.addStretch()
        return self._scroll_wrap(content)

    def set_camera(self, camera_id: int, camera_data: dict):
        """Configura el panel para una cámara específica.

        IMPORTANTE: NO deshabilitamos secciones aunque el modelo diga que la
        cámara no tiene la capability. Razón: el modelo se rellena al hacer
        ONVIF probe pero algunas cámaras no reportan correctamente sus
        capabilities (Hikvision/Dahua/XiongMai cada una en distinto formato).
        Mejor mostrar todo y dejar que el endpoint backend responda con error
        si la cámara realmente no lo soporta. El usuario al menos puede
        intentar y verá feedback claro.
        """
        self.current_camera_id = camera_id

        is_dual_lens = camera_data.get("is_dual_lens", False)

        # TODOS los widgets disponibles (NO deshabilitar)
        self.led_widget.set_camera(camera_id)
        self.audio_widget.set_camera(camera_id)
        self.ai_widget.set_camera(camera_id, is_dual_lens=is_dual_lens)
        self.rec_widget.set_camera(camera_id)

        # PTZ: solo cargar presets si el modelo dice que es PTZ-capable
        if camera_data.get("has_ptz", False):
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