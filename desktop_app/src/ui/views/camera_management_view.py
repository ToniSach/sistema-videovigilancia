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
                               QScrollArea, QFrame, QGridLayout, QSplitter,
                               QProgressDialog, QApplication, QSizePolicy)
from PySide6.QtCore import Qt, Signal, QThread, QTimer, Slot
from PySide6.QtGui import QIcon, QFont, QPixmap, QImage

from desktop_app.src.config import config
from desktop_app.src.ui.icons import icon
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
        
        # Credenciales de la cámara (usuario/contraseña). Visibles porque son
        # lo único que el usuario normalmente necesita escribir.
        cred_group = QGroupBox("Credenciales de la cámara")
        cred_layout = QFormLayout(cred_group)

        self.txt_username = QLineEdit()
        self.txt_username.setPlaceholderText("admin")
        cred_layout.addRow("Usuario:", self.txt_username)

        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.Password)
        cred_layout.addRow("Contraseña:", self.txt_password)
        form_layout.addRow(cred_group)

        # ── AVANZADO (colapsable, oculto por defecto) ───────────────────────
        # Las URLs RTSP/ONVIF se autocompletan con la IP; el usuario rara vez
        # las toca. Un QGroupBox checkable funciona como sección plegable.
        adv_group = QGroupBox("Avanzado (URLs de conexión)")
        adv_group.setCheckable(True)
        adv_group.setChecked(False)
        adv_layout = QFormLayout(adv_group)

        self.txt_rtsp = QLineEdit()
        self.txt_rtsp.setPlaceholderText("Se completa automáticamente con la IP")
        adv_layout.addRow("URL RTSP:", self.txt_rtsp)

        self.txt_onvif = QLineEdit()
        self.txt_onvif.setPlaceholderText("Se completa automáticamente (opcional)")
        adv_layout.addRow("URL ONVIF:", self.txt_onvif)

        # Colapsar/expandir: ocultar los hijos cuando no está marcado.
        def _toggle_adv(on):
            for w in (self.txt_rtsp, self.txt_onvif):
                w.setVisible(on)
            for lbl in adv_group.findChildren(QLabel):
                lbl.setVisible(on)
        adv_group.toggled.connect(_toggle_adv)
        _toggle_adv(False)
        form_layout.addRow(adv_group)

        # Autocompletar RTSP/ONVIF al escribir la IP o las credenciales.
        # Solo rellena si el campo está vacío o sigue siendo el autogenerado.
        self.txt_ip.editingFinished.connect(self._autocomplete_urls)
        self.txt_username.editingFinished.connect(self._autocomplete_urls)
        self.txt_password.editingFinished.connect(self._autocomplete_urls)
        
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
        
        # Resolución y FPS — AVANZADO/colapsable: se detectan automáticamente
        # al descubrir/probar la cámara, así que normalmente no hace falta tocarlo.
        res_group = QGroupBox("Resolución y FPS (avanzado)")
        res_group.setCheckable(True)
        res_group.setChecked(False)
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

        def _toggle_res(on):
            for w in (self.spin_width, self.spin_height, self.spin_fps):
                w.setVisible(on)
            for lbl in res_group.findChildren(QLabel):
                lbl.setVisible(on)
        res_group.toggled.connect(_toggle_res)
        _toggle_res(False)
        form_layout.addRow(res_group)
        
        # Configuración IA
        ai_group = QGroupBox("Inteligencia Artificial")
        ai_layout = QVBoxLayout(ai_group)
        
        self.chk_ai = QCheckBox("Habilitar detección de objetos")
        self.chk_ai.stateChanged.connect(self._on_ai_changed)
        ai_layout.addWidget(self.chk_ai)

        # Nota: solo UNA cámara puede tener la detección activa a la vez.
        ai_note = QLabel(
            "Solo una cámara puede tener la detección activa al mismo tiempo."
        )
        ai_note.setWordWrap(True)
        ai_note.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 10px;")
        ai_layout.addWidget(ai_note)

        self.cmb_ai_mode = QComboBox()
        self.cmb_ai_mode.addItems(["Bajo consumo (rápido)", "Alta precisión"])
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

        # Probar conexión antes de guardar (verifica IP/credenciales por ONVIF).
        self.btn_test = QPushButton("  Probar conexión")
        try:
            self.btn_test.setIcon(icon("search"))
        except Exception:
            pass
        self.btn_test.clicked.connect(self._test_connection)
        btn_layout.addWidget(self.btn_test)

        self.lbl_test_result = QLabel("")
        self.lbl_test_result.setStyleSheet("font-size: 11px;")
        btn_layout.addWidget(self.lbl_test_result)

        btn_layout.addStretch()

        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("Guardar")
        self.btn_save.clicked.connect(self._validate_and_accept)
        btn_layout.addWidget(self.btn_save)

        layout.addLayout(btn_layout)

    def _test_connection(self):
        """Llama a POST /cameras/test-connection con la IP/credenciales del form."""
        ip = self.txt_ip.text().strip()
        if not ip:
            self.lbl_test_result.setText("Escribe una IP primero")
            self.lbl_test_result.setStyleSheet("color: #f59e0b; font-size: 11px;")
            return
        self.btn_test.setEnabled(False)
        self.lbl_test_result.setText("Probando…")
        self.lbl_test_result.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 11px;")
        payload = {
            "ip_address": ip,
            "username": self.txt_username.text().strip() or None,
            "password": self.txt_password.text() or None,
        }

        def on_result(response):
            self.btn_test.setEnabled(True)
            if not response.success:
                self.lbl_test_result.setText(f"Error: {response.error or 'sin respuesta'}")
                self.lbl_test_result.setStyleSheet("color: #ef4444; font-size: 11px;")
                return
            d = response.data or {}
            if d.get("reachable"):
                self.lbl_test_result.setText(
                    f"✓ {d.get('manufacturer','?')} {d.get('model','')} · {d.get('resolution','')}"
                )
                self.lbl_test_result.setStyleSheet("color: #22c55e; font-size: 11px;")
                # Autocompletar capacidades detectadas (sin pisar lo marcado).
                if d.get("has_ptz"):
                    self.chk_ptz.setChecked(True)
                if d.get("has_audio"):
                    self.chk_audio.setChecked(True)
            else:
                self.lbl_test_result.setText("✗ No responde (revisa IP/credenciales)")
                self.lbl_test_result.setStyleSheet("color: #ef4444; font-size: 11px;")

        api_client.post("cameras/test-connection", on_result, data=payload)
    
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
    
    def _autocomplete_urls(self):
        """Rellena RTSP/ONVIF a partir de la IP (formato XiongMai/iCSee).

        Solo escribe si el campo está vacío o aún contiene el valor
        autogenerado, para no pisar una URL editada a mano por el usuario.
        """
        ip = self.txt_ip.text().strip()
        if not ip:
            return
        user = self.txt_username.text().strip()
        pwd = self.txt_password.text()
        cred = ""
        if user:
            cred = f"{user}:{pwd}@" if pwd else f"{user}@"
        auto_rtsp = f"rtsp://{cred}{ip}:554/cam/realmonitor?channel=1&subtype=0"
        auto_onvif = f"http://{ip}:8899/onvif/device_service"
        cur_rtsp = self.txt_rtsp.text().strip()
        if not cur_rtsp or "/cam/realmonitor" in cur_rtsp:
            self.txt_rtsp.setText(auto_rtsp)
        cur_onvif = self.txt_onvif.text().strip()
        if not cur_onvif or "/onvif/device_service" in cur_onvif:
            self.txt_onvif.setText(auto_onvif)

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

    def __init__(self, api_token: str, timeout_s: int = 15,
                 subnet_scan: bool = True):
        """
        Args:
            api_token: JWT del usuario.
            timeout_s: tiempo que WS-Discovery escucha en el backend.
            subnet_scan: si True y WS-Disc encuentra 0, escanea subnet
                         (útil cuando Windows firewall bloquea multicast).
        """
        super().__init__()
        self.api_token = api_token
        self.timeout_s = timeout_s
        self.subnet_scan = subnet_scan

    def run(self):
        try:
            import requests

            headers = {"Authorization": f"Bearer {self.api_token}"}
            # HTTP read timeout debe ser MAYOR que el cap interno del backend
            # (90s) para que el cap del backend devuelva resultados parciales
            # antes de que el cliente corte la conexión. 180s = backend cap
            # + buffer de red + WS-Discovery setup.
            http_timeout = max(180, self.timeout_s + 120)

            resp = requests.post(
                f"{config.API_BASE_URL}/cameras/discover",
                headers=headers,
                json={"timeout": self.timeout_s, "subnet_scan": self.subnet_scan},
                timeout=(10, http_timeout),
            )

            if resp.status_code == 200:
                data = resp.json()
                cameras = data.get("data", [])
                self.cameras_found.emit(cameras)
            else:
                self.error.emit(f"Error del servidor: {resp.status_code} - {resp.text[:200]}")

        except requests.exceptions.ConnectTimeout:
            self.error.emit("Timeout conectando al backend. ¿Está corriendo?")
        except requests.exceptions.ReadTimeout:
            self.error.emit(
                f"El descubrimiento tardó más de {http_timeout}s.\n\n"
                "Causas habituales:\n"
                "  • El backend está sobrecargado con probes ONVIF lentos.\n"
                "  • Una cámara anuncia su IP por multicast pero no es alcanzable\n"
                "    (IP estática de otra subred). Cambia su IP a DHCP desde el\n"
                "    panel web de la cámara.\n"
                "  • Firewall bloqueando multicast WS-Discovery (puerto 3702).\n\n"
                "Workaround: agrega la cámara manualmente con su IP y URL RTSP."
            )
        except requests.exceptions.ConnectionError:
            self.error.emit(f"No se pudo conectar al backend ({config.API_BASE_URL}).")
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
        self._loaded_once = False
        self._setup_ui()
        # IMPORTANTE: NO cargamos cámaras en __init__ porque esto se ejecuta
        # ANTES del login → 401 "Sesión expirada" → la lista queda vacía
        # para siempre. Cargamos al mostrar la vista por primera vez
        # (showEvent) o cuando MainWindow nos lo pida tras el login.

    def showEvent(self, event):
        """Carga cámaras al mostrar la vista (primera vez o al cambiar de pestaña)."""
        super().showEvent(event)
        # Solo intentamos cargar si hay sesión (evita el 401 spam pre-login)
        from desktop_app.src.services.api_client import api_client
        if api_client.tokens:
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

        from desktop_app.src.ui.components.help_button import HelpButton
        header.addWidget(HelpButton("camera_management_view", parent=self))

        header.addStretch()

        # Botón descubrir
        self.btn_discover = QPushButton("  Descubrir Cámaras")
        self.btn_discover.setIcon(icon("search"))
        self.btn_discover.setMinimumHeight(40)
        self.btn_discover.clicked.connect(self._discover_cameras)
        header.addWidget(self.btn_discover)

        # Botón agregar
        self.btn_add = QPushButton("  Agregar Cámara")
        self.btn_add.setIcon(icon("add"))
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
        self.btn_edit = QPushButton("  Editar")
        self.btn_edit.setIcon(icon("edit"))
        self.btn_edit.clicked.connect(self._edit_selected)
        self.btn_edit.setEnabled(False)
        list_layout.addWidget(self.btn_edit)

        self.btn_delete = QPushButton("  Eliminar")
        self.btn_delete.setIcon(icon("delete"))
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_delete.setEnabled(False)
        list_layout.addWidget(self.btn_delete)

        self.btn_toggle = QPushButton("  Activar/Desactivar")
        self.btn_toggle.setIcon(icon("power"))
        self.btn_toggle.clicked.connect(self._toggle_selected)
        self.btn_toggle.setEnabled(False)
        list_layout.addWidget(self.btn_toggle)
        
        splitter.addWidget(list_widget)
        
        # Panel de detalles + preview de video
        self.details_widget = GlassCard()
        details_layout = QVBoxLayout(self.details_widget)
        details_layout.setSpacing(12)

        self.lbl_details = QLabel(
            "Selecciona una cámara de la lista\n   para ver el preview y detalles"
        )
        self.lbl_details.setAlignment(Qt.AlignCenter)
        self.lbl_details.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 14px; padding: 40px;"
        )
        details_layout.addWidget(self.lbl_details)

        # Container que se muestra al seleccionar una cámara
        self.info_widget = QWidget()
        info_v = QVBoxLayout(self.info_widget)
        info_v.setContentsMargins(0, 0, 0, 0)
        info_v.setSpacing(8)

        # --- Preview de video (go2rtc/RTSP) ---
        # Selector de lente (solo visible para cámaras dual-lens)
        from PySide6.QtWidgets import QComboBox
        self.preview_lens_row = QHBoxLayout()
        self.preview_lens_row.setContentsMargins(0, 0, 0, 0)
        self.lbl_preview_lens = QLabel("Lente:")
        self.lbl_preview_lens.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")
        self.cmb_preview_lens = QComboBox()
        self.cmb_preview_lens.setStyleSheet(f"""
            QComboBox {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 4px; padding: 4px 8px;
            }}
        """)
        self.cmb_preview_lens.currentIndexChanged.connect(self._on_preview_lens_change)
        self.preview_lens_row.addWidget(self.lbl_preview_lens)
        self.preview_lens_row.addWidget(self.cmb_preview_lens)
        self.preview_lens_row.addStretch()
        self.preview_lens_widget = QWidget()
        self.preview_lens_widget.setLayout(self.preview_lens_row)
        self.preview_lens_widget.hide()  # solo se muestra para dual-lens
        info_v.addWidget(self.preview_lens_widget)

        from desktop_app.src.ui.components.rtsp_video import RtspVideoWidget
        self.preview_video = RtspVideoWidget(placeholder="Selecciona una cámara…")
        self.preview_video.setMinimumSize(400, 240)
        info_v.addWidget(self.preview_video, 1)

        # --- Info textual ---
        info_form = QFormLayout()
        info_form.setSpacing(8)
        info_form.setContentsMargins(8, 0, 8, 0)

        self.lbl_info_name = QLabel()
        self.lbl_info_name.setStyleSheet(
            f"color: {config.THEME_ACCENT}; font-weight: bold; font-size: 16px;"
        )
        info_form.addRow(self.lbl_info_name)

        self.lbl_info_ip = QLabel()
        self.lbl_info_ip.setStyleSheet(f"color: {config.THEME_TEXT};")
        info_form.addRow("IP:", self.lbl_info_ip)

        self.lbl_info_status = QLabel()
        info_form.addRow("Estado:", self.lbl_info_status)

        self.lbl_info_capabilities = QLabel()
        self.lbl_info_capabilities.setWordWrap(True)
        info_form.addRow("Capacidades:", self.lbl_info_capabilities)

        self.lbl_info_ai = QLabel()
        info_form.addRow("IA:", self.lbl_info_ai)

        info_v.addLayout(info_form)
        self.info_widget.hide()
        details_layout.addWidget(self.info_widget)

        splitter.addWidget(self.details_widget)
        splitter.setSizes([320, 680])

        layout.addWidget(splitter)

        # Preview pull-based (consistente con LiveView/CameraControlView).
        # Sin signals → sin acumulación de frames en queue de Qt si el GUI
        # se queda detrás. Un QTimer cada 67ms (15fps) pide el último frame.
        self._preview_camera_id: Optional[int] = None
        self._preview_stream_type: str = "main"
        self._preview_camera = None  # objeto Camera actual (para stream_url por lente)
    
    def _load_cameras(self):
        """Carga lista de cámaras desde API."""
        def on_response(response):
            if not response.success:
                logger.warning(f"No se pudieron cargar cámaras: {response.error}")
                return
            self.cameras = []
            self.list_cameras.clear()
            for cam_data in (response.data or []):
                # from_dict tolera campos extra del backend (connection_type, etc.)
                camera = Camera.from_dict(cam_data)
                self.cameras.append(camera)
                # Semáforo de estado: 🟢 OK / 🟡 reconectando / 🔴 caída/inactiva.
                dot, _ = self._status_semaphore(cam_data, camera)
                item = QListWidgetItem(f"{dot}  {camera.name}\n      {camera.ip_address}")
                item.setData(Qt.UserRole, camera.id)
                if not camera.is_active:
                    item.setForeground(Qt.gray)
                self.list_cameras.addItem(item)

        api_client.get("cameras/", on_response)

    def _status_semaphore(self, cam_data: dict, camera) -> tuple:
        """Devuelve (emoji_dot, texto) según el estado del worker de la cámara."""
        if not getattr(camera, "is_active", True):
            return "🔴", "Inactiva"
        ws = cam_data.get("worker_status") or {}
        status = (ws.get("status") if isinstance(ws, dict) else None) or ""
        status = str(status).lower()
        if status in ("running", "healthy", "ok"):
            return "🟢", "En línea"
        if status in ("reconnecting", "starting", "connecting"):
            return "🟡", "Reconectando"
        if status in ("error", "stalled", "frozen", "offline"):
            return "🔴", "Sin conexión"
        # Activa pero sin estado de worker conocido → asumimos OK suave.
        return "🟢", "Activa"
    
    def _on_camera_selected(self, current, previous):
        if not current:
            self.btn_edit.setEnabled(False)
            self.btn_delete.setEnabled(False)
            self.btn_toggle.setEnabled(False)
            self.lbl_details.show()
            self.info_widget.hide()
            self._stop_preview()
            return

        camera_id = current.data(Qt.UserRole)
        camera = next((c for c in self.cameras if c.id == camera_id), None)

        if camera:
            self.btn_edit.setEnabled(True)
            self.btn_delete.setEnabled(True)
            self.btn_toggle.setEnabled(True)

            # Iniciar preview de la cámara
            self._start_preview(camera)
            
            self.lbl_details.hide()
            self.info_widget.show()
            
            self.lbl_info_name.setText(camera.name)
            self.lbl_info_ip.setText(camera.ip_address)
            
            # Estado con semáforo de color + tooltip del último error si lo hay.
            ws = getattr(camera, "worker_status", None) or {}
            dot, txt = self._status_semaphore({"worker_status": ws}, camera)
            color = {"🟢": "#22c55e", "🟡": "#f59e0b", "🔴": "#ef4444"}.get(dot, "#94a3b8")
            self.lbl_info_status.setText(f"{dot} {txt}")
            self.lbl_info_status.setStyleSheet(f"color: {color}; font-weight: bold;")
            err = getattr(camera, "last_error_code", None)
            self.lbl_info_status.setToolTip(f"Último error: {err}" if err else "")
            
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
            
            # IA: distinguir "habilitada y corriendo" de "configurada pero en
            # pausa porque la cámara está inactiva" — sin esta diferenciación,
            # una cámara auto-desactivada por MAX_RETRIES seguía mostrándose
            # como "IA: Habilitada" aunque en runtime ya esté apagada.
            has_ai_cfg = getattr(camera, 'has_ai', False)
            cam_active = getattr(camera, 'is_active', True)
            if has_ai_cfg and not cam_active:
                ai_status = "Configurada (pausada · cámara inactiva)"
            elif has_ai_cfg:
                ai_status = "Habilitada"
            else:
                ai_status = "Deshabilitada"
            self.lbl_info_ai.setText(ai_status)
            
            self.camera_selected.emit(camera_id)
    
    # ------------------------------------------------------------------
    # Preview en vivo (go2rtc/RTSP) en panel de detalles
    # ------------------------------------------------------------------
    def _pick_preview_url(self, camera, stream_type: str) -> str:
        """URL go2rtc para (cámara, lente). Calidad alta para el preview."""
        if camera is None:
            return ""
        urls = getattr(camera, "stream_urls", None) or {}
        key = stream_type if stream_type in ("l1", "l2") else "main"
        by_q = urls.get(key) or {}
        legacy = {
            "l1": getattr(camera, "stream_url_l1", None),
            "l2": getattr(camera, "stream_url_l2", None),
        }.get(stream_type)
        return by_q.get("high") or legacy or (getattr(camera, "stream_url", "") or "")

    def _start_preview(self, camera: Camera):
        """Inicia el preview en vivo (go2rtc/VLC) de la cámara seleccionada."""
        self._stop_preview()

        # Configurar selector de lente
        self.cmb_preview_lens.blockSignals(True)
        self.cmb_preview_lens.clear()
        if camera.is_dual_lens:
            self.cmb_preview_lens.addItem("Lente 1 (L1)", "l1")
            self.cmb_preview_lens.addItem("Lente 2 (L2)", "l2")
            self.preview_lens_widget.show()
            stream_type = "l1"  # default
        else:
            self.preview_lens_widget.hide()
            stream_type = "main"
        self.cmb_preview_lens.blockSignals(False)

        self._preview_camera = camera
        self._preview_camera_id = camera.id
        self._preview_stream_type = stream_type

        url = self._pick_preview_url(camera, stream_type)
        if url:
            self.preview_video.play(url)
        else:
            self.preview_video.show_message("Sin stream go2rtc disponible")

    def _on_preview_lens_change(self, idx: int):
        """Cambia el lente mostrado en el preview (sin recrear el player)."""
        if self._preview_camera is None:
            return
        new_lens = self.cmb_preview_lens.itemData(idx)
        if not new_lens or new_lens == self._preview_stream_type:
            return
        self._preview_stream_type = new_lens
        url = self._pick_preview_url(self._preview_camera, new_lens)
        if url:
            self.preview_video.set_url(url)

    def _stop_preview(self):
        """Detiene el preview actual si está activo."""
        try:
            self.preview_video.stop()
        except Exception as e:
            logger.debug(f"Error parando preview: {e}")
        self._preview_camera = None
        self._preview_camera_id = None
        self._preview_stream_type = "main"
        self.preview_lens_widget.hide()
        self.preview_video.show_message("Preview detenido")

    def hideEvent(self, event):
        """Detener preview al salir de la pestaña (ahorra red y CPU)."""
        self._stop_preview()
        super().hideEvent(event)

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
        from desktop_app.src.services.api_client import api_client
        token = api_client.get_stream_token() or ""

        if not token:
            QMessageBox.warning(self, "Error", "No hay sesión activa")
            return

        # Diálogo modal de progreso (con animación indeterminada)
        self._progress_dialog = QProgressDialog(
            "Buscando cámaras ONVIF en la red…\n\n"
            "Esto puede tardar 15-30 segundos:\n"
            "  • WS-Discovery (multicast)\n"
            "  • Escaneo del subnet local\n"
            "  • Sondeo ONVIF + autenticación",
            "Cancelar",
            0, 0,  # min=max=0 → barra indeterminada (animada)
            self,
        )
        self._progress_dialog.setWindowTitle("Descubrimiento de cámaras")
        self._progress_dialog.setWindowModality(Qt.WindowModal)
        self._progress_dialog.setMinimumWidth(420)
        self._progress_dialog.setMinimumDuration(0)  # mostrar inmediatamente
        self._progress_dialog.setAutoClose(False)
        self._progress_dialog.setAutoReset(False)
        self._progress_dialog.canceled.connect(self._on_discovery_canceled)
        self._progress_dialog.show()
        QApplication.processEvents()

        # Timeout corto + subnet_scan automático (cubre el caso de firewall
        # bloqueando multicast WS-Discovery, común en Windows).
        self.discovery_thread = CameraDiscoveryThread(
            token, timeout_s=15, subnet_scan=True
        )
        self.discovery_thread.cameras_found.connect(self._on_discovered_cameras)
        self.discovery_thread.error.connect(self._on_discovery_error)
        self.discovery_thread.finished_search.connect(self._reset_discover_button)
        self.discovery_thread.start()

    def _reset_discover_button(self):
        if hasattr(self, "_progress_dialog") and self._progress_dialog:
            self._progress_dialog.close()
            self._progress_dialog = None

    def _on_discovery_canceled(self):
        """El usuario clicó Cancelar — solo cerramos el dialog,
        el thread sigue en background hasta que termine (no podemos matar
        el WS-Discovery sin riesgo de corruption)."""
        logger.info("Usuario canceló descubrimiento (continúa en background)")
        if hasattr(self, "_progress_dialog") and self._progress_dialog:
            self._progress_dialog.close()
            self._progress_dialog = None
        # Desconectamos el callback para que no muestre el mensaje al usuario
        try:
            self.discovery_thread.cameras_found.disconnect(self._on_discovered_cameras)
        except (TypeError, RuntimeError):
            pass

    def _on_discovery_error(self, error: str):
        if hasattr(self, "_progress_dialog") and self._progress_dialog:
            self._progress_dialog.close()
            self._progress_dialog = None
        QMessageBox.critical(self, "Error en descubrimiento", error)

    def _on_discovered_cameras(self, cameras):
        if not cameras:
            QMessageBox.information(
                self, "Descubrimiento",
                "No se encontraron cámaras en la red.\n"
                "Verifica que la cámara esté en la misma red y que su "
                "servicio ONVIF esté habilitado."
            )
            return

        # Separar cámaras alcanzables de las que el backend reportó como
        # no alcanzables (IP estática de otra subred, etc.).
        unreachable = [c for c in cameras if c.get("connection_type") == "unreachable"]
        reachable = [c for c in cameras if c.get("connection_type") != "unreachable"]

        # Filtrar duplicadas con cámaras ya existentes (por IP)
        existing_ips = {c.ip_address for c in self.cameras}
        new_cameras = [c for c in reachable if c.get("ip_address") not in existing_ips]
        already_in = len(reachable) - len(new_cameras)

        # Si hay cámaras detectadas pero no alcanzables, avisar primero — no
        # tiene sentido intentar agregarlas porque el backend tampoco podrá
        # arrancar su worker.
        if unreachable:
            warn = "Cámaras detectadas pero NO alcanzables desde este PC:\n\n"
            for cam in unreachable:
                ip = cam.get("ip_address", "?")
                warn += f"  • {ip}\n"
            warn += (
                "\nProbablemente tienen IP estática de otra subred (por ejemplo,\n"
                "192.168.1.X cuando este PC está en 10.99.130.X).\n\n"
                "Cómo arreglarlo:\n"
                "  1. Conecta un PC con cable directo a la cámara (o ponle al PC\n"
                "     una IP estática en el mismo rango que la cámara).\n"
                "  2. Entra a su panel web y cámbiale la IP a DHCP o a una IP\n"
                "     del rango actual de tu router.\n"
                "  3. Vuelve a buscar cámaras."
            )
            QMessageBox.warning(self, "Cámaras no alcanzables", warn)

        if not new_cameras:
            if not unreachable:
                QMessageBox.information(
                    self, "Descubrimiento",
                    f"Se encontraron {len(reachable)} cámaras pero todas ya están agregadas."
                    if already_in else "No se encontraron cámaras nuevas."
                )
            return

        msg = f"Se encontraron {len(new_cameras)} cámaras nuevas alcanzables:\n\n"
        for cam in new_cameras:
            man = cam.get("manufacturer", "")
            mod = cam.get("model", "")
            msg += f"• {cam['ip_address']}  {man}/{mod}\n"
        if already_in:
            msg += f"\n(Se omitieron {already_in} ya existentes)\n"
        msg += "\n¿Desea agregarlas automáticamente?"

        reply = QMessageBox.question(self, "Cámaras Encontradas", msg)
        if reply == QMessageBox.Yes:
            for cam_data in new_cameras:
                # Preguntar por cada cámara si es DUAL-LENS. El backend no puede
                # adivinarlo de forma fiable (la resolución que devuelve ONVIF
                # ya es la combinada de los dos sensores y no hay un flag
                # estándar). Default: No, porque la mayoría son monolente.
                ip   = cam_data.get("ip_address", "?")
                name = cam_data.get("name", "Cámara")
                w    = cam_data.get("resolution_width", "?")
                h    = cam_data.get("resolution_height", "?")
                dual_reply = QMessageBox.question(
                    self,
                    "¿Cámara dual-lens?",
                    f"¿La cámara «{name}» ({ip}) es de tipo DUAL-LENS\n"
                    f"(dos objetivos físicos, un solo stream side-by-side)?\n\n"
                    f"Resolución detectada: {w}×{h}\n"
                    f"  • Mono típico: 1920×1080, 1280×720, 2560×1440\n"
                    f"  • Dual típico: 2560×720, 3840×1080, 1280×1440\n\n"
                    f"Si no estás seguro, elige «No» — siempre puedes editarla después.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                is_dual = (dual_reply == QMessageBox.Yes)

                # skip_probe=True: el discovery YA probó la cámara con éxito,
                # no hace falta que el backend vuelva a probarla (lento).
                payload = dict(cam_data)
                payload["skip_probe"] = True
                payload["is_active"] = True
                payload["is_dual_lens"] = is_dual

                def on_added(response, ip=cam_data.get("ip_address")):
                    if response.success:
                        self._load_cameras()
                        self.camera_updated.emit()
                    else:
                        logger.warning(f"No se pudo agregar {ip}: {response.error}")

                api_client.post("cameras/", on_added, data=payload)