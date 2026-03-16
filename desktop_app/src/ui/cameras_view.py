from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, 
    QPushButton, QComboBox, QLabel, QDialog,
    QLineEdit, QCheckBox, QSpinBox, QFormLayout,
    QDialogButtonBox, QProgressDialog, QListWidget,
    QListWidgetItem, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread, QObject, QCoreApplication
from PySide6.QtGui import QGuiApplication
from ui.widgets.camera_widget import CameraWidget
from ui.widgets.ptz_widget import PTZWidget
from services.api_client import api_client

class CameraCell(QWidget):
    camera_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.camera_id: int | None = None
        self._camera_widget: CameraWidget | None = None

        self.setMinimumSize(320, 240)
        self.setStyleSheet("background-color: #2a2a2a; border: 2px solid #444;")

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        # Empty state
        self._empty_widget = QWidget()
        empty_layout = QVBoxLayout(self._empty_widget)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        empty_label = QLabel("Sin cámara")
        empty_label.setStyleSheet("color: #888; font-size: 16px;")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_label)

        self._assign_btn = QPushButton("Asignar cámara")
        self._assign_btn.clicked.connect(self._on_assign_clicked)
        empty_layout.addWidget(self._assign_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._layout.addWidget(self._empty_widget)
        self._empty_widget.show()

    def set_camera(self, camera_data: dict):
        self.clear()

        self.camera_id = camera_data.get("id")
        camera_name = camera_data.get("name", f"Cámara {self.camera_id}")

        self._camera_widget = CameraWidget(self.camera_id, camera_name)
        self._layout.addWidget(self._camera_widget)
        self._empty_widget.hide()

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def clear(self):
        if self._camera_widget:
            self._camera_widget.stop_stream()
            self._camera_widget.deleteLater()
            self._camera_widget = None
        self.camera_id = None
        self._empty_widget.show()
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event):
        if self.camera_id and self._camera_widget:
            self.camera_selected.emit(self.camera_id)
        super().mousePressEvent(event)

    def _on_assign_clicked(self):
        pass

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

class CamerasView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._cells: list[CameraCell] = []
        self._active_camera_id: int | None = None
        self._ptz_widget: PTZWidget | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Toolbar
        toolbar = QHBoxLayout()

        self._discover_btn = QPushButton("🔍 Descubrir cámaras")
        self._discover_btn.clicked.connect(self._on_discover)
        toolbar.addWidget(self._discover_btn)

        self._add_btn = QPushButton("➕ Agregar manualmente")
        self._add_btn.clicked.connect(self._on_add_manual)
        toolbar.addWidget(self._add_btn)

        toolbar.addStretch()

        layout_label = QLabel("Layout:")
        toolbar.addWidget(layout_label)

        self._layout_combo = QComboBox()
        self._layout_combo.addItems(["1 cámara", "2x2 (4 cámaras)"])
        self._layout_combo.currentTextChanged.connect(self._on_layout_changed)
        toolbar.addWidget(self._layout_combo)

        layout.addLayout(toolbar)

        # Grid
        self._grid = QGridLayout()
        self._grid.setSpacing(10)
        layout.addLayout(self._grid)

        # Create cells
        for i in range(4):
            cell = CameraCell()
            cell.camera_selected.connect(self._on_camera_selected)
            self._cells.append(cell)

        self._update_grid_layout(1)

        # PTZ Panel
        self._ptz_container = QWidget()
        ptz_layout = QVBoxLayout(self._ptz_container)
        ptz_label = QLabel("Controles PTZ")
        ptz_layout.addWidget(ptz_label)
        self._ptz_placeholder = QLabel("Seleccione una cámara con PTZ")
        ptz_layout.addWidget(self._ptz_placeholder)
        layout.addWidget(self._ptz_container)
        self._ptz_container.hide()

        # Refresh timer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_cameras)
        self._refresh_timer.start(30000)

        self.refresh_cameras()

    def _update_grid_layout(self, count: int):
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().hide()

        if count == 1:
            self._grid.addWidget(self._cells[0], 0, 0)
            self._cells[0].show()
            for i in range(1, 4):
                self._cells[i].hide()
        else:
            for i in range(4):
                row = i // 2
                col = i % 2
                self._grid.addWidget(self._cells[i], row, col)
                self._cells[i].show()

    def _on_layout_changed(self, text: str):
        if "1" in text:
            self._update_grid_layout(1)
        else:
            self._update_grid_layout(4)

    def refresh_cameras(self):
        self._thread = QThread()
        self._worker = Worker(api_client.get_cameras)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_cameras_loaded)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_cameras_loaded(self, response):
        # Extraer lista del response
        cameras = []
        if isinstance(response, dict) and "data" in response:
            cameras = response["data"]
        elif isinstance(response, list):
            cameras = response
            
        if not isinstance(cameras, list):
            return

        active_cameras = [c for c in cameras if c.get("is_active")]

        for i, cell in enumerate(self._cells):
            if i < len(active_cameras):
                if cell.camera_id != active_cameras[i].get("id"):
                    cell.set_camera(active_cameras[i])
            else:
                if cell.camera_id is not None:
                    cell.clear()

    def _on_camera_selected(self, camera_id: int):
        self._active_camera_id = camera_id
        self._ptz_container.show()

    def _on_discover(self):
        # ✅ CORREGIDO: Crear diálogo de progreso que no bloquee
        self._progress = QProgressDialog("Buscando cámaras ONVIF...", None, 0, 0, self)
        self._progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._progress.setMinimumDuration(0)
        self._progress.setValue(0)
        self._progress.show()
        
        # Procesar eventos para mostrar el diálogo inmediatamente
        QCoreApplication.processEvents()

        def discover():
            return api_client.discover_cameras()

        self._discover_thread = QThread()
        self._discover_worker = Worker(discover)
        self._discover_worker.moveToThread(self._discover_thread)
        self._discover_thread.started.connect(self._discover_worker.run)

        def on_finished(result):
            self._progress.close()
            self._on_discover_results(result)
            self._discover_thread.quit()

        def on_error(msg):
            self._progress.close()
            QMessageBox.critical(self, "Error", f"Error al descubrir: {msg}")
            self._discover_thread.quit()

        self._discover_worker.finished.connect(on_finished)
        self._discover_worker.error.connect(on_error)
        self._discover_worker.finished.connect(self._discover_worker.deleteLater)
        self._discover_thread.finished.connect(self._discover_thread.deleteLater)
        self._discover_thread.start()

    def _on_discover_results(self, response):
        cameras = []
        if isinstance(response, dict) and "data" in response:
            cameras = response["data"]
        elif isinstance(response, list):
            cameras = response
            
        if not cameras:
            QMessageBox.information(self, "Descubrimiento", "No se encontraron cámaras en la red.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Cámaras descubiertas ({len(cameras)})")
        dialog.setMinimumSize(400, 300)

        layout = QVBoxLayout(dialog)
        list_widget = QListWidget()

        for cam in cameras:
            ip = cam.get('ip_address', cam.get('ip', 'No IP'))
            name = cam.get('name', 'Unknown')
            item_text = f"{name}\nIP: {ip}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, cam)
            list_widget.addItem(item)

        layout.addWidget(list_widget)

        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Agregar seleccionada")
        add_btn.clicked.connect(lambda: self._add_discovered_camera(list_widget, dialog))
        btn_layout.addWidget(add_btn)

        close_btn = QPushButton("Cerrar")
        close_btn.clicked.connect(dialog.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)
        dialog.exec()

    def _add_discovered_camera(self, list_widget: QListWidget, dialog: QDialog):
        current = list_widget.currentItem()
        if not current:
            QMessageBox.warning(self, "Atención", "Seleccione una cámara primero")
            return

        cam_data = current.data(Qt.ItemDataRole.UserRole)
        dialog.close()

        camera_payload = {
            "name": cam_data.get('name', 'Cámara ONVIF'),
            "ip_address": cam_data.get('ip_address', cam_data.get('ip', '')),
            "rtsp_url": cam_data.get('rtsp_url', ''),
            "onvif_url": cam_data.get('onvif_url', ''),
            "username": cam_data.get('username', 'admin'),
            "password": cam_data.get('password', 'admin'),
            "profile_token": cam_data.get('profile_token', ''),
            "is_active": True,
            "has_ai": False,
            "has_ptz": cam_data.get('has_ptz', False),
            "has_leds": cam_data.get('has_leds', False),
            "has_audio": cam_data.get('has_audio', False),
            "is_dual_lens": False,
            "fps": cam_data.get('fps', 15),
            "resolution_width": cam_data.get('resolution_width', 1920),
            "resolution_height": cam_data.get('resolution_height', 1080)
        }

        result = api_client.add_camera(camera_payload)
        if result:
            QMessageBox.information(self, "Éxito", "Cámara agregada correctamente")
            self.refresh_cameras()
        else:
            QMessageBox.warning(self, "Error", "No se pudo agregar la cámara")

    def _on_add_manual(self):
        dialog = AddCameraDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()
            result = api_client.add_camera(data)
            if result:
                self.refresh_cameras()
            else:
                QMessageBox.warning(self, "Error", "No se pudo agregar la cámara")

class AddCameraDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Agregar cámara")
        self.setMinimumWidth(400)

        layout = QFormLayout(self)

        self.name_input = QLineEdit()
        layout.addRow("Nombre:", self.name_input)

        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("192.168.1.100")
        layout.addRow("IP Address:", self.ip_input)

        self.rtsp_input = QLineEdit()
        self.rtsp_input.setPlaceholderText("rtsp://...")
        layout.addRow("RTSP URL:", self.rtsp_input)

        self.onvif_input = QLineEdit()
        self.onvif_input.setPlaceholderText("http://.../onvif")
        layout.addRow("ONVIF URL:", self.onvif_input)

        self.username_input = QLineEdit()
        self.username_input.setText("admin")
        layout.addRow("Usuario:", self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setText("admin")
        layout.addRow("Contraseña:", self.password_input)

        self.active_check = QCheckBox("Activa")
        self.active_check.setChecked(True)
        layout.addRow(self.active_check)

        self.ai_check = QCheckBox("Tiene AI")
        layout.addRow(self.ai_check)

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(5, 30)
        self.fps_spin.setValue(15)
        layout.addRow("FPS:", self.fps_spin)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(640, 3840)
        self.width_spin.setValue(1920)
        layout.addRow("Ancho:", self.width_spin)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(480, 2160)
        self.height_spin.setValue(1080)
        layout.addRow("Alto:", self.height_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self) -> dict:
        return {
            "name": self.name_input.text(),
            "ip_address": self.ip_input.text(),
            "rtsp_url": self.rtsp_input.text(),
            "onvif_url": self.onvif_input.text(),
            "username": self.username_input.text(),
            "password": self.password_input.text(),
            "is_active": self.active_check.isChecked(),
            "has_ai": self.ai_check.isChecked(),
            "fps": self.fps_spin.value(),
            "resolution_width": self.width_spin.value(),
            "resolution_height": self.height_spin.value(),
            "has_ptz": False,
            "has_leds": False,
            "has_audio": False,
            "is_dual_lens": False
        }