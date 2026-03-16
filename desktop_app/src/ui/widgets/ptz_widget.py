
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QComboBox, QInputDialog, QMessageBox
)
from PySide6.QtCore import Qt, QThread, QObject, Signal
from PySide6.QtGui import QMouseEvent
# ✅ CORREGIDO: Import relativo
from services.api_client import api_client

class PTZControlButton(QPushButton):
    def __init__(self, text, direction, parent=None):
        super().__init__(text, parent)
        self.direction = direction
        self.setFixedSize(40, 40)
        self.setAutoRepeat(False)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.parent()._start_move(self.direction)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.parent()._stop_move()
        super().mouseReleaseEvent(event)

class Worker(QObject):
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

class PTZWidget(QWidget):
    def __init__(self, camera_id=None, parent=None):
        super().__init__(parent)
        self._camera_id = camera_id
        self._capabilities = {}
        self._audio_active = False
        self._thread = None
        self._init_ui()
        if camera_id is None:
            self.setEnabled(False)
        else:
            self.set_camera(camera_id)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        title = QLabel("Control PTZ")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title)
        grid = QGridLayout()
        grid.setSpacing(5)
        self.btn_up = PTZControlButton("↑", "up", self)
        self.btn_down = PTZControlButton("↓", "down", self)
        self.btn_left = PTZControlButton("←", "left", self)
        self.btn_right = PTZControlButton("→", "right", self)
        self.btn_stop_dir = QPushButton("⊙")
        self.btn_stop_dir.setFixedSize(40, 40)
        self.btn_stop_dir.clicked.connect(lambda: self._stop_move())
        grid.addWidget(self.btn_up, 0, 1)
        grid.addWidget(self.btn_left, 1, 0)
        grid.addWidget(self.btn_stop_dir, 1, 1)
        grid.addWidget(self.btn_right, 1, 2)
        grid.addWidget(self.btn_down, 2, 1)
        layout.addLayout(grid)
        zoom_layout = QHBoxLayout()
        self.btn_zoom_in = QPushButton("🔍+")
        self.btn_zoom_out = QPushButton("🔍-")
        self.btn_zoom_in.setFixedWidth(60)
        self.btn_zoom_out.setFixedWidth(60)
        zoom_layout.addStretch()
        zoom_layout.addWidget(self.btn_zoom_in)
        zoom_layout.addWidget(self.btn_zoom_out)
        zoom_layout.addStretch()
        layout.addLayout(zoom_layout)
        line = QLabel()
        line.setFrameStyle(QLabel.Shape.HLine | QLabel.Shadow.Sunken)
        line.setFixedHeight(2)
        line.setStyleSheet("background-color: #ccc;")
        layout.addWidget(line)
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("Presets:"))
        self.presets_combo = QComboBox()
        self.presets_combo.setMinimumWidth(120)
        preset_layout.addWidget(self.presets_combo)
        self.btn_go_preset = QPushButton("Ir")
        self.btn_go_preset.clicked.connect(self._goto_preset)
        preset_layout.addWidget(self.btn_go_preset)
        self.btn_save_preset = QPushButton("Guardar preset")
        self.btn_save_preset.clicked.connect(self._save_preset)
        preset_layout.addWidget(self.btn_save_preset)
        preset_layout.addStretch()
        layout.addLayout(preset_layout)
        line2 = QLabel()
        line2.setFrameStyle(QLabel.Shape.HLine | QLabel.Shadow.Sunken)
        line2.setFixedHeight(2)
        line2.setStyleSheet("background-color: #ccc;")
        layout.addWidget(line2)
        led_layout = QHBoxLayout()
        led_layout.addWidget(QLabel("LEDs:"))
        self.leds_combo = QComboBox()
        self.leds_combo.addItems(["Auto", "Encendido", "Apagado"])
        led_layout.addWidget(self.leds_combo)
        self.btn_apply_leds = QPushButton("Aplicar")
        self.btn_apply_leds.clicked.connect(self._apply_leds)
        led_layout.addWidget(self.btn_apply_leds)
        led_layout.addStretch()
        layout.addLayout(led_layout)
        audio_layout = QHBoxLayout()
        audio_layout.addWidget(QLabel("Audio:"))
        self.btn_audio_toggle = QPushButton("🎤 Hablar")
        self.btn_audio_toggle.setCheckable(True)
        self.btn_audio_toggle.clicked.connect(self._toggle_audio)
        audio_layout.addWidget(self.btn_audio_toggle)
        audio_layout.addStretch()
        layout.addLayout(audio_layout)
        layout.addStretch()

    def set_camera(self, camera_id):
        self._camera_id = camera_id
        self.setEnabled(True)
        self._load_capabilities()

    def _run_in_thread(self, func, *args, callback=None):
        self._thread = QThread()
        self._worker = Worker(func, *args)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        if callback:
            self._worker.finished.connect(callback)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _load_capabilities(self):
        def load():
            return api_client.get_capabilities(self._camera_id)
        def on_loaded(caps):
            if not caps:
                return
            self._capabilities = caps
            has_ptz = caps.get("ptz", False)
            has_leds = caps.get("leds", False)
            has_audio = caps.get("audio", False)
            for btn in [self.btn_up, self.btn_down, self.btn_left,
                       self.btn_right, self.btn_stop_dir]:
                btn.setEnabled(has_ptz)
            self.presets_combo.setEnabled(has_ptz)
            self.btn_go_preset.setEnabled(has_ptz)
            self.btn_save_preset.setEnabled(has_ptz)
            self.leds_combo.setEnabled(has_leds)
            self.btn_apply_leds.setEnabled(has_leds)
            self.btn_audio_toggle.setEnabled(has_audio)
            if has_ptz:
                self._load_presets()
        self._run_in_thread(load, callback=on_loaded)

    def _load_presets(self):
        def load():
            return api_client.get_ptz_presets(self._camera_id)
        def on_loaded(presets):
            self.presets_combo.clear()
            if presets:
                for preset in presets:
                    name = preset.get("name", "Unknown")
                    token = preset.get("token", "")
                    self.presets_combo.addItem(name, token)
        self._run_in_thread(load, callback=on_loaded)

    def _start_move(self, direction):
        self._run_in_thread(api_client.ptz_move, self._camera_id, direction, 0.5)

    def _stop_move(self):
        self._run_in_thread(api_client.ptz_stop, self._camera_id)

    def _goto_preset(self):
        token = self.presets_combo.currentData()
        if token:
            self._run_in_thread(api_client.go_to_preset, self._camera_id, token)

    def _save_preset(self):
        name, ok = QInputDialog.getText(self, "Guardar preset", "Nombre del preset:")
        if ok and name:
            def save():
                return api_client._request(
                    "POST",
                    f"/api/v1/cameras/{self._camera_id}/ptz/presets",
                    json={"name": name}
                )
            def on_saved(result):
                if result:
                    QMessageBox.information(self, "Exito", "Preset guardado")
                    self._load_presets()
                else:
                    QMessageBox.warning(self, "Error", "No se pudo guardar el preset")
            self._run_in_thread(save, callback=on_saved)

    def _apply_leds(self):
        mode_text = self.leds_combo.currentText()
        mode_map = {"Auto": "auto", "Encendido": "on", "Apagado": "off"}
        mode = mode_map.get(mode_text, "auto")
        self._run_in_thread(api_client.set_leds, self._camera_id, mode)

    def _toggle_audio(self):
        if self.btn_audio_toggle.isChecked():
            self._run_in_thread(api_client.start_audio, self._camera_id)
            self.btn_audio_toggle.setText("🔇 Detener")
        else:
            self._run_in_thread(api_client.stop_audio, self._camera_id)
            self.btn_audio_toggle.setText("🎤 Hablar")
