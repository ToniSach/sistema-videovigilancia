"""
Ventana principal de la aplicación.
"""
import logging
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QPushButton, QLabel,
    QStatusBar, QMessageBox, QDialog
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence

from desktop_app.src.config import config
from desktop_app.src.models.user import User
from desktop_app.src.services.api_client import api_client
from desktop_app.src.services.video_streamer import video_streamer

from desktop_app.src.ui.views.login_view import LoginView
from desktop_app.src.ui.views.live_view import LiveView
from desktop_app.src.ui.views.playback_view import PlaybackView
from desktop_app.src.ui.views.camera_management_view import CameraManagementView

try:
    from desktop_app.src.ui.views.settings_view import SettingsView
    SETTINGS_AVAILABLE = True
except ImportError:
    SETTINGS_AVAILABLE = False

from desktop_app.src.ui.components.camera_control_panel import CameraControlPanel
from desktop_app.src.ui.components.glass_card import GlassCard

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Ventana principal del sistema NVR."""

    def __init__(self):
        super().__init__()

        self.setWindowTitle("NVR VMS Professional")
        self.setMinimumSize(1280, 720)
        self.resize(1600, 900)

        self.current_user: Optional[User] = None
        self.cameras: list = []

        self._setup_ui()
        self._setup_menu()
        self._setup_styles()
        self._connect_signals()

        self.show_login()

    def _setup_ui(self):
        self.central = QWidget()
        self.setCentralWidget(self.central)

        self.main_layout = QVBoxLayout(self.central)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.stack = QStackedWidget()
        self.main_layout.addWidget(self.stack)

        self.login_view = LoginView()
        self.login_view.login_successful.connect(self._on_login_success)
        self.stack.addWidget(self.login_view)

        self.main_view = self._create_main_view()
        self.stack.addWidget(self.main_view)

    def _create_main_view(self):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = self._create_sidebar()
        layout.addWidget(self.sidebar)

        self.content_stack = QStackedWidget()
        self.content_stack.setStyleSheet(
            f"background-color: {config.THEME_PRIMARY};"
        )

        # Vistas
        self.live_view = LiveView()
        self.live_view.ptz_requested.connect(self._on_ptz_request)
        self.content_stack.addWidget(self.live_view)

        self.playback_view = PlaybackView()
        self.content_stack.addWidget(self.playback_view)

        self.camera_management_view = CameraManagementView()
        self.camera_management_view.camera_updated.connect(self._reload_cameras)
        self.content_stack.addWidget(self.camera_management_view)

        self.settings_view = SettingsView()
        self.content_stack.addWidget(self.settings_view)

        layout.addWidget(self.content_stack, stretch=1)

        return widget

    def _create_sidebar(self):
        sidebar = GlassCard()
        sidebar.setFixedWidth(220)

        layout = QVBoxLayout(sidebar)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 20, 12, 20)

        lbl_logo = QLabel("NVR VMS")
        lbl_logo.setStyleSheet(f"""
            color: {config.THEME_ACCENT};
            font-size: 20px;
            font-weight: bold;
        """)
        layout.addWidget(lbl_logo)

        layout.addSpacing(20)

        # Botones
        self.btn_live = self._nav_button("📹 En Vivo", True)
        self.btn_live.clicked.connect(lambda: self._switch_view(0))
        layout.addWidget(self.btn_live)

        self.btn_playback = self._nav_button("⏯ Playback", False)
        self.btn_playback.clicked.connect(lambda: self._switch_view(1))
        layout.addWidget(self.btn_playback)

        self.btn_cameras = self._nav_button("📷 Cámaras", False)
        self.btn_cameras.clicked.connect(lambda: self._switch_view(2))
        layout.addWidget(self.btn_cameras)

        self.btn_settings = self._nav_button("⚙ Configuración", False)
        self.btn_settings.clicked.connect(lambda: self._switch_view(3))
        layout.addWidget(self.btn_settings)

        layout.addStretch()

        self.lbl_user = QLabel("No autenticado")
        self.lbl_user.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 11px;"
        )
        layout.addWidget(self.lbl_user)

        self.btn_logout = QPushButton("Cerrar Sesión")
        self.btn_logout.clicked.connect(self._logout)
        layout.addWidget(self.btn_logout)

        return sidebar

    def _nav_button(self, text: str, active: bool) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setChecked(active)
        btn.setMinimumHeight(44)
        btn.setCursor(Qt.PointingHandCursor)
        return btn

    def _setup_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("Archivo")

        exit_action = QAction("Salir", self)
        exit_action.setShortcut(QKeySequence.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menubar.addMenu("Ayuda")
        about_action = QAction("Acerca de", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _setup_styles(self):
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)
        self.statusbar.showMessage("Listo")

    def _connect_signals(self):
        api_client.request_error.connect(self._on_api_error)

    def show_login(self):
        self.stack.setCurrentIndex(0)
        self.login_view.reset()
        self.menuBar().setVisible(False)
        self.statusbar.setVisible(False)

    def _on_login_success(self, user_data: dict):
        self.current_user = User(
            id=user_data.get("id"),
            username=user_data.get("username"),
            role=user_data.get("role", "user"),
            accessible_cameras=user_data.get("accessible_cameras", [])
        )

        self.lbl_user.setText(f"👤 {self.current_user.username}")
        self._load_cameras()

        self.stack.setCurrentIndex(1)
        self.menuBar().setVisible(True)
        self.statusbar.setVisible(True)

    def _load_cameras(self):
        def on_cameras(response):
            if response.success:
                from desktop_app.src.models.camera import Camera
                self.cameras = [Camera(**c) for c in response.data]

                token = api_client.tokens.access_token if api_client.tokens else ""
                self.live_view.set_cameras(self.cameras, token)
                self.playback_view.set_cameras(self.cameras)

        api_client.get("cameras/", on_cameras)

    def _reload_cameras(self):
        self._load_cameras()

    def _switch_view(self, index: int):
        # Detener streams al salir de la vista en vivo
        if index != 0:  # No es la vista en vivo
            video_streamer.stop_all()
        elif index == 0:
            # Reiniciar streams con token actual
            token = api_client.tokens.access_token if api_client.tokens else ""
            self.live_view.restart_streams(token)

        # Actualizar botones
        for btn in [
            self.btn_live,
            self.btn_playback,
            self.btn_cameras,
            self.btn_settings
        ]:
            btn.setChecked(False)

        if index == 0:
            self.btn_live.setChecked(True)
            self.live_view.show()
            video_streamer.set_base_url(config.API_BASE_URL)
        elif index == 1:
            self.btn_playback.setChecked(True)
            self.live_view.hide()
        elif index == 2:
            self.btn_cameras.setChecked(True)
            self.camera_management_view._load_cameras()
        elif index == 3:
            self.btn_settings.setChecked(True)

        self.content_stack.setCurrentIndex(index)

    def _on_ptz_request(self, camera):
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Control: {camera.name}")
        dialog.setMinimumSize(400, 600)

        layout = QVBoxLayout(dialog)

        def on_camera_data(response):
            if response.success:
                panel = CameraControlPanel()
                panel.set_camera(camera.id, response.data)
                layout.addWidget(panel)
                dialog.exec()

        api_client.get(f"cameras/{camera.id}", on_camera_data)

    def _on_api_error(self, error: str):
        self.statusbar.showMessage(f"Error: {error}", 5000)

    def _logout(self):
        video_streamer.stop_all()
        api_client.clear_tokens()
        self.show_login()

    def _show_about(self):
        QMessageBox.about(
            self,
            "Acerca de",
            "NVR VMS v2.0.0\nSistema de Videovigilancia Profesional"
        )