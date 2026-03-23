# desktop_app/src/ui/views/camera_management_view.py
"""
Vista de gestión de cámaras (agregar, editar, configurar IA).
"""
import logging
from typing import Optional, List

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                               QPushButton, QListWidget, QListWidgetItem, 
                               QDialog, QLineEdit, QCheckBox, QSpinBox, 
                               QFormLayout, QMessageBox, QComboBox, QGroupBox,
                               QScrollArea, QFrame, QGridLayout, QSplitter)
from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtGui import QIcon, QFont

from desktop_app.src.config import config
from desktop_app.src.models.camera import Camera
from desktop_app.src.services.api_client import api_client
from desktop_app.src.ui.components.glass_card import GlassCard

logger = logging.getLogger(__name__)


class CameraEditDialog(QDialog):
    """Diálogo para agregar/editar cámara - AHORA CON SCROLL."""
    
    def __init__(self, camera: Optional[Camera] = None, parent=None):
        super().__init__(parent)
        
        self.camera = camera
        self.setWindowTitle("Configurar Cámara" if camera else "Agregar Cámara")
        # 🔧 TAMAÑO REDUCIDO y scrollable
        self.setMinimumWidth(400)
        self.setMaximumWidth(500)
        self.setMinimumHeight(500)
        self.setMaximumHeight(700)
        self.setModal(True)
        
        self._setup_ui()
        self._apply_styles()
        
        if camera:
            self._load_camera_data()
    
    def _setup_ui(self):
        """Construye interfaz con SCROLL AREA."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # 🔧 SCROLL AREA principal
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background-color: transparent;
            }}
            QScrollBar:vertical {{
                background-color: {config.THEME_SECONDARY};
                width: 12px;
                border-radius: 6px;
            }}
            QScrollBar::handle:vertical {{
                background-color: {config.THEME_ACCENT};
                border-radius: 6px;
                min-height: 20px;
            }}
        """)
        
        # Widget contenedor dentro del scroll
        scroll_content = QWidget()
        form_layout = QFormLayout(scroll_content)
        form_layout.setSpacing(10)
        form_layout.setContentsMargins(8, 8, 8, 8)
        
        # Nombre
        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText("Cámara Principal")
        form_layout.addRow("Nombre:", self.txt_name)
        
        # IP
        self.txt_ip = QLineEdit()
        self.txt_ip.setPlaceholderText("192.168.1.100")
        form_layout.addRow("Dirección IP:", self.txt_ip)
        
        # RTSP URL
        self.txt_rtsp = QLineEdit()
        self.txt_rtsp.setPlaceholderText("rtsp://admin:pass@192.168.1.100:554/stream1")
        form_layout.addRow("URL RTSP:", self.txt_rtsp)
        
        # ONVIF URL (opcional)
        self.txt_onvif = QLineEdit()
        self.txt_onvif.setPlaceholderText("http://192.168.1.100/onvif/device_service (opcional)")
        form_layout.addRow("URL ONVIF:", self.txt_onvif)
        
        # Credenciales
        cred_group = QGroupBox("Credenciales ONVIF")
        cred_layout = QFormLayout(cred_group)
        
        self.txt_username = QLineEdit()
        self.txt_username.setPlaceholderText("admin")
        cred_layout.addRow("Usuario:", self.txt_username)
        
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.Password)
        cred_layout.addRow("Contraseña:", self.txt_password)
        
        form_layout.addRow(cred_group)
        
        # Capacidades
        capabilities_group = QGroupBox("Capacidades de la Cámara")
        capabilities_layout = QVBoxLayout(capabilities_group)
        
        self.chk_ptz = QCheckBox("Soporta PTZ (Pan-Tilt-Zoom)")
        self.chk_leds = QCheckBox("Soporta LEDs/IR")
        self.chk_audio = QCheckBox("Audio Bidireccional")
        self.chk_dual_lens = QCheckBox("Cámara Dual Lens")
        
        capabilities_layout.addWidget(self.chk_ptz)
        capabilities_layout.addWidget(self.chk_leds)
        capabilities_layout.addWidget(self.chk_audio)
        capabilities_layout.addWidget(self.chk_dual_lens)
        
        form_layout.addRow(capabilities_group)
        
        # Resolución y FPS
        res_group = QGroupBox("Configuración de Video")
        res_layout = QFormLayout(res_group)
        
        self.spin_width = QSpinBox()
        self.spin_width.setRange(640, 3840)
        self.spin_width.setValue(1920)
        res_layout.addRow("Ancho (px):", self.spin_width)
        
        self.spin_height = QSpinBox()
        self.spin_height.setRange(480, 2160)
        self.spin_height.setValue(1080)
        res_layout.addRow("Alto (px):", self.spin_height)
        
        self.spin_fps = QSpinBox()
        self.spin_fps.setRange(1, 60)
        self.spin_fps.setValue(15)
        res_layout.addRow("FPS:", self.spin_fps)
        
        form_layout.addRow(res_group)
        
        # Configuración IA
        ai_group = QGroupBox("Inteligencia Artificial")
        ai_layout = QVBoxLayout(ai_group)
        
        self.chk_ai = QCheckBox("Habilitar Detección IA (YOLO)")
        self.chk_ai.stateChanged.connect(self._on_ai_changed)
        ai_layout.addWidget(self.chk_ai)
        
        self.cmb_ai_mode = QComboBox()
        self.cmb_ai_mode.addItems(["Bajo consumo CPU", "Alta calidad"])
        self.cmb_ai_mode.setEnabled(False)
        ai_layout.addWidget(QLabel("Modo:"))
        ai_layout.addWidget(self.cmb_ai_mode)
        
        self.chk_notify_person = QCheckBox("Notificar Personas")
        self.chk_notify_person.setChecked(True)
        self.chk_notify_person.setEnabled(False)
        ai_layout.addWidget(self.chk_notify_person)
        
        self.chk_notify_vehicle = QCheckBox("Notificar Vehículos")
        self.chk_notify_vehicle.setEnabled(False)
        ai_layout.addWidget(self.chk_notify_vehicle)
        
        form_layout.addRow(ai_group)
        
        # Settear el widget al scroll
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        
        # Botones (fuera del scroll, siempre visibles)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)
        
        self.btn_save = QPushButton("Guardar")
        self.btn_save.clicked.connect(self._validate_and_accept)
        btn_layout.addWidget(self.btn_save)
        
        layout.addLayout(btn_layout)
    
    def _apply_styles(self):
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {config.THEME_PRIMARY};
            }}
            QLabel {{
                color: {config.THEME_TEXT};
                font-family: Inter, sans-serif;
                font-size: 12px;
            }}
            QLineEdit, QSpinBox, QComboBox {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 6px;
                font-size: 12px;
            }}
            QGroupBox {{
                color: {config.THEME_ACCENT};
                font-weight: bold;
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 8px;
                margin-top: 8px;
                padding-top: 8px;
                font-size: 13px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }}
            QCheckBox {{
                color: {config.THEME_TEXT};
                spacing: 6px;
                font-size: 12px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid {config.GLASS_BORDER};
                background-color: {config.THEME_SECONDARY};
            }}
            QCheckBox::indicator:checked {{
                background-color: {config.THEME_ACCENT};
            }}
            QPushButton {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {config.THEME_ACCENT}.lighter(120);
            }}
            QPushButton#cancel {{
                background-color: transparent;
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
            }}
        """)
        self.btn_cancel.setObjectName("cancel")
    
    def _on_ai_changed(self, state):
        enabled = state == Qt.Checked
        self.cmb_ai_mode.setEnabled(enabled)
        self.chk_notify_person.setEnabled(enabled)
        self.chk_notify_vehicle.setEnabled(enabled)
    
    def _load_camera_data(self):
        if not self.camera:
            return
        
        self.txt_name.setText(self.camera.name)
        self.txt_ip.setText(self.camera.ip_address)
        self.txt_rtsp.setText(getattr(self.camera, 'rtsp_url', ''))
        self.txt_onvif.setText(getattr(self.camera, 'onvif_url', ''))
        self.txt_username.setText(getattr(self.camera, 'username', ''))
        self.txt_password.setText(getattr(self.camera, 'password', ''))
        
        self.chk_ptz.setChecked(getattr(self.camera, 'has_ptz', False))
        self.chk_leds.setChecked(getattr(self.camera, 'has_leds', False))
        self.chk_audio.setChecked(getattr(self.camera, 'has_audio', False))
        self.chk_dual_lens.setChecked(getattr(self.camera, 'is_dual_lens', False))
        
        self.spin_width.setValue(getattr(self.camera, 'resolution_width', 1920))
        self.spin_height.setValue(getattr(self.camera, 'resolution_height', 1080))
        self.spin_fps.setValue(getattr(self.camera, 'fps', 15))
        
        self.chk_ai.setChecked(getattr(self.camera, 'has_ai', False))
    
    def _validate_and_accept(self):
        if not self.txt_name.text().strip():
            QMessageBox.warning(self, "Validación", "El nombre es requerido")
            return
        if not self.txt_ip.text().strip():
            QMessageBox.warning(self, "Validación", "La dirección IP es requerida")
            return
        if not self.txt_rtsp.text().strip():
            QMessageBox.warning(self, "Validación", "La URL RTSP es requerida")
            return
        
        self.accept()
    
    def get_camera_data(self) -> dict:
        return {
            "name": self.txt_name.text().strip(),
            "ip_address": self.txt_ip.text().strip(),
            "rtsp_url": self.txt_rtsp.text().strip(),
            "onvif_url": self.txt_onvif.text().strip() or None,
            "username": self.txt_username.text().strip() or None,
            "password": self.txt_password.text() or None,
            "has_ptz": self.chk_ptz.isChecked(),
            "has_leds": self.chk_leds.isChecked(),
            "has_audio": self.chk_audio.isChecked(),
            "is_dual_lens": self.chk_dual_lens.isChecked(),
            "resolution_width": self.spin_width.value(),
            "resolution_height": self.spin_height.value(),
            "fps": self.spin_fps.value(),
            "is_active": True,
            "has_ai": self.chk_ai.isChecked()
        }


class CameraDiscoveryThread(QThread):
    """Thread para descubrir cámaras ONVIF usando el backend."""
    
    cameras_found = Signal(list)
    error = Signal(str)
    finished_search = Signal()
    
    def __init__(self, api_token):
        super().__init__()
        self.api_token = api_token
    
    def run(self):
        try:
            import requests
            
            headers = {"Authorization": f"Bearer {self.api_token}"}
            resp = requests.post(
                "http://localhost:5000/api/v1/cameras/discover",
                headers=headers,
                #estaba en 15
                timeout=50
            )
            
            if resp.status_code == 200:
                data = resp.json()
                cameras = data.get("data", [])
                self.cameras_found.emit(cameras)
            else:
                self.error.emit(f"Error del servidor: {resp.status_code}")
                
        except requests.exceptions.ConnectionError:
            self.error.emit("No se pudo conectar al backend. ¿Está corriendo en localhost:5000?")
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished_search.emit()


class CameraManagementView(QWidget):
    """Vista principal de gestión de cámaras."""
    
    camera_selected = Signal(int)
    camera_updated = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.cameras: List[Camera] = []
        self._setup_ui()
        self._load_cameras()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        
        # Header
        header = QHBoxLayout()
        
        self.lbl_title = QLabel("Gestión de Cámaras")
        self.lbl_title.setStyleSheet(f"""
            color: {config.THEME_TEXT};
            font-size: 24px;
            font-weight: bold;
        """)
        header.addWidget(self.lbl_title)
        
        header.addStretch()
        
        # Botón descubrir
        self.btn_discover = QPushButton("🔍 Descubrir Cámaras")
        self.btn_discover.setMinimumHeight(40)
        self.btn_discover.clicked.connect(self._discover_cameras)
        header.addWidget(self.btn_discover)
        
        # Botón agregar
        self.btn_add = QPushButton("➕ Agregar Cámara")
        self.btn_add.setMinimumHeight(40)
        self.btn_add.clicked.connect(self._add_camera)
        header.addWidget(self.btn_add)
        
        layout.addLayout(header)
        
        # Splitter para lista y detalles
        splitter = QSplitter(Qt.Horizontal)
        
        # Lista de cámaras
        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setContentsMargins(0, 0, 0, 0)
        
        self.list_cameras = QListWidget()
        self.list_cameras.setMinimumWidth(300)
        self.list_cameras.currentItemChanged.connect(self._on_camera_selected)
        self.list_cameras.setStyleSheet(f"""
            QListWidget {{
                background-color: {config.GLASS_BG};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 8px;
                padding: 8px;
            }}
            QListWidget::item {{
                background-color: {config.THEME_SECONDARY};
                border-radius: 6px;
                margin: 4px 0px;
                padding: 12px;
                color: {config.THEME_TEXT};
            }}
            QListWidget::item:hover {{
                background-color: {config.THEME_ACCENT}.darker(140);
            }}
            QListWidget::item:selected {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
            }}
        """)
        list_layout.addWidget(self.list_cameras)
        
        # Botones de acción para item seleccionado
        self.btn_edit = QPushButton("✏️ Editar")
        self.btn_edit.clicked.connect(self._edit_selected)
        self.btn_edit.setEnabled(False)
        list_layout.addWidget(self.btn_edit)
        
        self.btn_delete = QPushButton("🗑️ Eliminar")
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_delete.setEnabled(False)
        list_layout.addWidget(self.btn_delete)
        
        self.btn_toggle = QPushButton("⏯ Activar/Desactivar")
        self.btn_toggle.clicked.connect(self._toggle_selected)
        self.btn_toggle.setEnabled(False)
        list_layout.addWidget(self.btn_toggle)
        
        splitter.addWidget(list_widget)
        
        # Panel de detalles
        self.details_widget = GlassCard()
        details_layout = QVBoxLayout(self.details_widget)
        
        self.lbl_details = QLabel("Seleccione una cámara para ver detalles")
        self.lbl_details.setAlignment(Qt.AlignCenter)
        self.lbl_details.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 16px;")
        details_layout.addWidget(self.lbl_details)
        
        # Información detallada (inicialmente oculta)
        self.info_widget = QWidget()
        info_layout = QFormLayout(self.info_widget)
        info_layout.setSpacing(12)
        
        self.lbl_info_name = QLabel()
        self.lbl_info_name.setStyleSheet(f"color: {config.THEME_ACCENT}; font-weight: bold; font-size: 18px;")
        info_layout.addRow(self.lbl_info_name)
        
        self.lbl_info_ip = QLabel()
        self.lbl_info_ip.setStyleSheet(f"color: {config.THEME_TEXT};")
        info_layout.addRow("IP:", self.lbl_info_ip)
        
        self.lbl_info_status = QLabel()
        info_layout.addRow("Estado:", self.lbl_info_status)
        
        self.lbl_info_capabilities = QLabel()
        self.lbl_info_capabilities.setWordWrap(True)
        info_layout.addRow("Capacidades:", self.lbl_info_capabilities)
        
        self.lbl_info_ai = QLabel()
        info_layout.addRow("IA:", self.lbl_info_ai)
        
        self.info_widget.hide()
        details_layout.addWidget(self.info_widget)
        details_layout.addStretch()
        
        splitter.addWidget(self.details_widget)
        splitter.setSizes([350, 650])
        
        layout.addWidget(splitter)
    
    def _load_cameras(self):
        """Carga lista de cámaras desde API."""
        def on_response(response):
            if response.success:
                self.cameras = []
                self.list_cameras.clear()
                
                for cam_data in response.data:
                    camera = Camera(
                        id=cam_data.get("id"),
                        name=cam_data.get("name"),
                        ip_address=cam_data.get("ip_address"),
                        **{k: v for k, v in cam_data.items() if k not in ["id", "name", "ip_address"]}
                    )
                    self.cameras.append(camera)
                    
                    item = QListWidgetItem(f"{camera.name}\n{camera.ip_address}")
                    item.setData(Qt.UserRole, camera.id)
                    if not cam_data.get("is_active", True):
                        item.setForeground(Qt.gray)
                    self.list_cameras.addItem(item)
        
        api_client.get("cameras/", on_response)
    
    def _on_camera_selected(self, current, previous):
        if not current:
            self.btn_edit.setEnabled(False)
            self.btn_delete.setEnabled(False)
            self.btn_toggle.setEnabled(False)
            self.lbl_details.show()
            self.info_widget.hide()
            return
        
        camera_id = current.data(Qt.UserRole)
        camera = next((c for c in self.cameras if c.id == camera_id), None)
        
        if camera:
            self.btn_edit.setEnabled(True)
            self.btn_delete.setEnabled(True)
            self.btn_toggle.setEnabled(True)
            
            self.lbl_details.hide()
            self.info_widget.show()
            
            self.lbl_info_name.setText(camera.name)
            self.lbl_info_ip.setText(camera.ip_address)
            
            status = "🟢 Activa" if getattr(camera, 'is_active', True) else "🔴 Inactiva"
            self.lbl_info_status.setText(status)
            
            caps = []
            if getattr(camera, 'has_ptz', False):
                caps.append("PTZ")
            if getattr(camera, 'has_leds', False):
                caps.append("LEDs")
            if getattr(camera, 'has_audio', False):
                caps.append("Audio")
            if getattr(camera, 'is_dual_lens', False):
                caps.append("Dual Lens")
            self.lbl_info_capabilities.setText(", ".join(caps) if caps else "Ninguna")
            
            ai_status = "Habilitada" if getattr(camera, 'has_ai', False) else "Deshabilitada"
            self.lbl_info_ai.setText(ai_status)
            
            self.camera_selected.emit(camera_id)
    
    def _add_camera(self):
        dialog = CameraEditDialog(parent=self)
        if dialog.exec() == QDialog.Accepted:
            data = dialog.get_camera_data()
            
            def on_created(response):
                if response.success:
                    self._load_cameras()
                    self.camera_updated.emit()
                else:
                    QMessageBox.critical(self, "Error", f"No se pudo crear la cámara: {response.error}")
            
            api_client.post("cameras/", on_created, data=data)
    
    def _edit_selected(self):
        current = self.list_cameras.currentItem()
        if not current:
            return
        
        camera_id = current.data(Qt.UserRole)
        camera = next((c for c in self.cameras if c.id == camera_id), None)
        
        if camera:
            dialog = CameraEditDialog(camera=camera, parent=self)
            if dialog.exec() == QDialog.Accepted:
                data = dialog.get_camera_data()
                
                def on_updated(response):
                    if response.success:
                        self._load_cameras()
                        self.camera_updated.emit()
                    else:
                        QMessageBox.critical(self, "Error", f"No se pudo actualizar: {response.error}")
                
                api_client.put(f"cameras/{camera_id}", on_updated, data=data)
    
    def _delete_selected(self):
        current = self.list_cameras.currentItem()
        if not current:
            return
        
        camera_id = current.data(Qt.UserRole)
        camera = next((c for c in self.cameras if c.id == camera_id), None)
        
        reply = QMessageBox.question(
            self, 
            "Confirmar Eliminación",
            f"¿Está seguro de eliminar la cámara '{camera.name}'?\n\nEsta acción no se puede deshacer.",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            def on_deleted(response):
                if response.success:
                    self._load_cameras()
                    self.camera_updated.emit()
                else:
                    QMessageBox.critical(self, "Error", f"No se pudo eliminar: {response.error}")
            
            api_client.delete(f"cameras/{camera_id}", on_deleted)
    
    def _toggle_selected(self):
        current = self.list_cameras.currentItem()
        if not current:
            return
        
        camera_id = current.data(Qt.UserRole)
        camera = next((c for c in self.cameras if c.id == camera_id), None)
        
        if camera:
            new_state = not getattr(camera, 'is_active', True)
            
            def on_toggled(response):
                if response.success:
                    self._load_cameras()
                    self.camera_updated.emit()
                else:
                    QMessageBox.critical(self, "Error", f"No se pudo cambiar estado: {response.error}")
            
            api_client.patch(f"cameras/{camera_id}/toggle", on_toggled, data={"active": new_state})
    
    def _discover_cameras(self):
        self.btn_discover.setEnabled(False)
        self.btn_discover.setText("🔍 Buscando...")
        
        # Obtener token actual
        from desktop_app.src.services.api_client import api_client
        token = api_client.tokens.access_token if api_client.tokens else ""
        
        if not token:
            QMessageBox.warning(self, "Error", "No hay sesión activa")
            self.btn_discover.setEnabled(True)
            return
        
        self.discovery_thread = CameraDiscoveryThread(token)
        self.discovery_thread.cameras_found.connect(self._on_discovered_cameras)
        self.discovery_thread.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        self.discovery_thread.finished_search.connect(lambda: self.btn_discover.setEnabled(True))
        self.discovery_thread.finished_search.connect(lambda: self.btn_discover.setText("🔍 Descubrir Cámaras"))
        self.discovery_thread.start()
    
    def _on_discovered_cameras(self, cameras):
        if not cameras:
            QMessageBox.information(self, "Descubrimiento", "No se encontraron cámaras en la red.")
            return
        
        msg = f"Se encontraron {len(cameras)} cámaras:\n\n"
        for cam in cameras:
            msg += f"• {cam['name']} ({cam['ip_address']})\n"
        
        msg += "\n¿Desea agregarlas automáticamente?"
        
        reply = QMessageBox.question(self, "Cámaras Encontradas", msg)
        if reply == QMessageBox.Yes:
            # Agregar cámaras encontradas
            for cam_data in cameras:
                def on_added(response):
                    if response.success:
                        self._load_cameras()
                
                api_client.post("cameras/", on_added, data=cam_data)