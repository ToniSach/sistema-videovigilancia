"""
Ventana principal de la aplicación NVR.

Layout:
  [ Sidebar ] | [ Stack de vistas según opción seleccionada ]

Sidebar agrupa las opciones en secciones:
  📡 MONITOREO       En vivo · Eventos · Reproducción
  ⚙ CONFIGURACIÓN    Cámaras · Notificaciones · Sistema
  👥 ADMINISTRACIÓN  Usuarios · Permisos
"""
import logging
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QPushButton, QLabel,
    QStatusBar, QMessageBox, QDialog, QFrame,
)
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QAction, QKeySequence, QFont

from desktop_app.src.config import config
from desktop_app.src.ui.icons import icon
from desktop_app.src.models.user import User
from desktop_app.src.services.api_client import api_client
from desktop_app.src.services.video_streamer import video_streamer

from desktop_app.src.ui.views.login_view import LoginView
from desktop_app.src.ui.views.live_view import LiveView
from desktop_app.src.ui.views.playback_view import PlaybackView
from desktop_app.src.ui.views.camera_management_view import CameraManagementView
from desktop_app.src.ui.views.camera_control_view import CameraControlView
from desktop_app.src.ui.views.events_view import EventsView
from desktop_app.src.ui.views.users_view import UsersView
from desktop_app.src.ui.views.permissions_view import PermissionsView
from desktop_app.src.ui.views.notifications_view import NotificationPreferencesView
from desktop_app.src.ui.views.system_view import SystemHealthView

try:
    from desktop_app.src.ui.views.settings_view import SettingsView
    SETTINGS_AVAILABLE = True
except ImportError:
    SETTINGS_AVAILABLE = False

from desktop_app.src.ui.components.camera_control_panel import CameraControlPanel
from desktop_app.src.ui.components.glass_card import GlassCard

logger = logging.getLogger(__name__)


# Índices del stack de vistas (deben coincidir con el orden en _create_main_view)
VIEW_LIVE = 0
VIEW_CONTROL = 1  # vista dedicada de una cámara (no aparece en sidebar)
VIEW_EVENTS = 2
VIEW_PLAYBACK = 3
VIEW_CAMERAS = 4
VIEW_NOTIFICATIONS = 5
VIEW_SYSTEM = 6
VIEW_USERS = 7
VIEW_PERMISSIONS = 8
VIEW_SETTINGS = 9


class MainWindow(QMainWindow):
    """Ventana principal del sistema NVR."""

    def __init__(self):
        super().__init__()

        self.setWindowTitle("NVR VMS Professional")
        self.setMinimumSize(1280, 720)
        self.resize(1600, 900)

        self.current_user: Optional[User] = None
        self.cameras: list = []
        self._nav_buttons = {}

        self._setup_ui()
        self._setup_menu()
        self._setup_styles()
        self._connect_signals()

        self.show_login()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
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

        # ---- Vistas (orden importante, coincide con constantes VIEW_*) ----
        self.live_view = LiveView()
        self.live_view.ptz_requested.connect(self._on_ptz_request)
        self.live_view.camera_config_requested.connect(self._on_camera_config_request)
        self.content_stack.addWidget(self.live_view)             # 0

        # Vista de control individual (video grande + panel a la derecha).
        # Se navega a ella desde click en cámara, NO desde sidebar.
        self.camera_control_view = CameraControlView()
        self.camera_control_view.back_requested.connect(
            lambda: self._switch_view(VIEW_LIVE)
        )
        self.content_stack.addWidget(self.camera_control_view)   # 1

        self.events_view = EventsView()
        self.events_view.jump_to_playback.connect(self._on_jump_to_playback)
        self.content_stack.addWidget(self.events_view)           # 2

        self.playback_view = PlaybackView()
        self.content_stack.addWidget(self.playback_view)         # 3

        self.camera_management_view = CameraManagementView()
        self.camera_management_view.camera_updated.connect(self._reload_cameras)
        self.content_stack.addWidget(self.camera_management_view)  # 4

        self.notifications_view = NotificationPreferencesView()
        self.content_stack.addWidget(self.notifications_view)    # 5

        self.system_view = SystemHealthView()
        self.content_stack.addWidget(self.system_view)           # 6

        self.users_view = UsersView()
        self.content_stack.addWidget(self.users_view)            # 7

        self.permissions_view = PermissionsView()
        self.content_stack.addWidget(self.permissions_view)      # 8

        if SETTINGS_AVAILABLE:
            self.settings_view = SettingsView()
            self.content_stack.addWidget(self.settings_view)     # 9
        else:
            self.settings_view = None

        layout.addWidget(self.content_stack, stretch=1)
        return widget

    def _create_sidebar(self):
        sidebar = GlassCard()
        sidebar.setFixedWidth(240)

        layout = QVBoxLayout(sidebar)
        layout.setSpacing(4)
        layout.setContentsMargins(12, 16, 12, 12)

        # Logo
        lbl_logo = QLabel("NVR VMS")
        lbl_logo.setStyleSheet(f"""
            color: {config.THEME_ACCENT};
            font-size: 20px;
            font-weight: bold;
            padding: 4px 8px;
        """)
        layout.addWidget(lbl_logo)
        layout.addSpacing(12)

        # ===== Sección MONITOREO =====
        layout.addWidget(self._section_label("MONITOREO"))
        self._add_nav(layout, "En vivo", VIEW_LIVE, active=True, icon_name="live")
        self._add_nav(layout, "Eventos", VIEW_EVENTS, icon_name="events")
        self._add_nav(layout, "Reproducción", VIEW_PLAYBACK, icon_name="playback")

        layout.addSpacing(12)

        # ===== Sección CONFIGURACIÓN =====
        layout.addWidget(self._section_label("CONFIGURACIÓN"))
        self._add_nav(layout, "Cámaras", VIEW_CAMERAS, icon_name="cameras")
        self._add_nav(layout, "Notificaciones", VIEW_NOTIFICATIONS, icon_name="notifications")
        self._add_nav(layout, "Sistema", VIEW_SYSTEM, icon_name="system")

        layout.addSpacing(12)

        # ===== Sección ADMINISTRACIÓN (solo admin) =====
        self.lbl_section_admin = self._section_label("ADMINISTRACIÓN")
        layout.addWidget(self.lbl_section_admin)
        self._add_nav(layout, "Usuarios", VIEW_USERS, admin_only=True, icon_name="users")
        self._add_nav(layout, "Permisos", VIEW_PERMISSIONS, admin_only=True, icon_name="permissions")
        if SETTINGS_AVAILABLE:
            self._add_nav(layout, "Ajustes", VIEW_SETTINGS, admin_only=True, icon_name="settings")

        layout.addStretch()

        # Usuario y logout
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {config.GLASS_BORDER};")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        self.lbl_user = QLabel("No autenticado")
        self.lbl_user.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 12px; "
            f"font-weight: bold; padding: 8px 4px 2px;"
        )
        layout.addWidget(self.lbl_user)

        self.lbl_user_role = QLabel("—")
        self.lbl_user_role.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 10px; padding: 0 4px 6px;"
        )
        layout.addWidget(self.lbl_user_role)

        # Botón para abrir el tutorial otra vez
        self.btn_tutorial = QPushButton("  Tutorial")
        self.btn_tutorial.setIcon(icon("tutorial"))
        self.btn_tutorial.setIconSize(QSize(18, 18))
        self.btn_tutorial.setMinimumHeight(36)
        self.btn_tutorial.setToolTip("Ver el tutorial de bienvenida otra vez")
        self.btn_tutorial.clicked.connect(self._open_tutorial)
        self.btn_tutorial.setStyleSheet(self._nav_button_style())
        layout.addWidget(self.btn_tutorial)

        # Botón para vincular móvil (QR)
        self.btn_link_mobile = QPushButton("  Vincular móvil")
        self.btn_link_mobile.setIcon(icon("link_mobile"))
        self.btn_link_mobile.setIconSize(QSize(18, 18))
        self.btn_link_mobile.setMinimumHeight(36)
        self.btn_link_mobile.clicked.connect(self._open_qr_link_dialog)
        self.btn_link_mobile.setStyleSheet(self._nav_button_style())
        layout.addWidget(self.btn_link_mobile)

        self.btn_logout = QPushButton("  Cerrar sesión")
        self.btn_logout.setIcon(icon("logout"))
        self.btn_logout.setIconSize(QSize(18, 18))
        self.btn_logout.setMinimumHeight(36)
        self.btn_logout.clicked.connect(self._logout)
        self.btn_logout.setStyleSheet(self._logout_button_style())
        layout.addWidget(self.btn_logout)

        return sidebar

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"""
            color: {config.THEME_TEXT_MUTED};
            font-size: 10px;
            font-weight: bold;
            letter-spacing: 1px;
            padding: 8px 8px 4px;
        """)
        return lbl

    def _add_nav(self, layout: QVBoxLayout, text: str, view_index: int,
                 active: bool = False, admin_only: bool = False,
                 icon_name: str = None):
        btn = QPushButton("  " + text if icon_name else text)
        if icon_name:
            btn.setIcon(icon(icon_name))
            btn.setIconSize(QSize(20, 20))
        btn.setCheckable(True)
        btn.setChecked(active)
        btn.setMinimumHeight(40)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(self._nav_button_style())
        btn.clicked.connect(lambda _checked=False, idx=view_index: self._switch_view(idx))
        if admin_only:
            btn.setProperty("admin_only", True)
            btn.setVisible(False)  # se mostrará tras login si es admin
        layout.addWidget(btn)
        self._nav_buttons[view_index] = btn

    def _nav_button_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: transparent;
                color: {config.THEME_TEXT};
                border: none;
                border-radius: 6px;
                text-align: left;
                padding: 8px 12px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {config.THEME_SECONDARY};
            }}
            QPushButton:checked {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
                font-weight: bold;
            }}
        """

    def _logout_button_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 6px;
            }}
            QPushButton:hover {{
                background-color: {config.THEME_DANGER};
                color: white;
                border-color: {config.THEME_DANGER};
            }}
        """

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

    # ------------------------------------------------------------------
    # Login / Logout
    # ------------------------------------------------------------------
    def show_login(self):
        self.stack.setCurrentIndex(0)
        self.login_view.reset()
        self.menuBar().setVisible(False)
        self.statusbar.setVisible(False)

    def _on_login_success(self, user_data: dict):
        role = user_data.get("role", "user")
        self.current_user = User(
            id=user_data.get("id"),
            username=user_data.get("username"),
            role=role,
            accessible_cameras=user_data.get("accessible_cameras", []),
        )

        self.lbl_user.setText(self.current_user.username)
        is_admin = (role == "admin")
        self.lbl_user_role.setText("Administrador" if is_admin else "Usuario")

        # Mostrar/ocultar opciones admin
        self.lbl_section_admin.setVisible(is_admin)
        for view_idx, btn in self._nav_buttons.items():
            if btn.property("admin_only"):
                btn.setVisible(is_admin)

        # Inicializar vistas que necesitan saber el rol
        self.users_view.set_current_user(self.current_user.id, role)
        self.permissions_view.set_current_user(self.current_user.id, role)
        self.notifications_view.set_current_user(self.current_user.id, role)

        self._load_cameras()

        # Switch a vista principal
        self.stack.setCurrentIndex(1)
        self.menuBar().setVisible(True)
        self.statusbar.setVisible(True)

        # Tutorial automático en el primer login de este equipo
        try:
            from PySide6.QtCore import QTimer
            from desktop_app.src.ui.dialogs.onboarding_wizard import maybe_show_onboarding
            # Diferido para que la ventana principal ya esté pintada
            QTimer.singleShot(400, lambda: maybe_show_onboarding(parent=self))
        except Exception as e:
            logger.warning(f"No se pudo mostrar onboarding: {e}")

    def _open_tutorial(self):
        """Abre el wizard de bienvenida (manual desde sidebar)."""
        try:
            from desktop_app.src.ui.dialogs.onboarding_wizard import show_onboarding
            show_onboarding(parent=self, force=True)
        except Exception as e:
            logger.error(f"Error abriendo tutorial: {e}")

    def _open_qr_link_dialog(self):
        """Muestra el diálogo de vinculación de móvil (QR)."""
        if not api_client.tokens:
            QMessageBox.warning(
                self, "Sesión requerida",
                "Debes iniciar sesión antes de vincular un móvil."
            )
            return
        try:
            from desktop_app.src.ui.dialogs.qr_link_dialog import QRLinkDialog
            dlg = QRLinkDialog(self)
            dlg.exec()
        except Exception as e:
            logger.error(f"Error abriendo diálogo QR: {e}")
            QMessageBox.critical(
                self, "Error",
                f"No se pudo abrir el diálogo de vinculación:\n{e}"
            )

    def _logout(self):
        ans = QMessageBox.question(
            self, "Cerrar sesión", "¿Seguro que quieres cerrar la sesión?"
        )
        if ans != QMessageBox.Yes:
            return

        # Detener streams MJPEG en curso (cierra sockets, libera threads)
        video_streamer.stop_all()

        # Destruir widgets del live view: libera QPixmaps en GPU y desconecta
        # signals que apuntaban a video_streamer. Sin esto, hacer logout/login
        # repetidos causaba GPU memory creep.
        try:
            self.live_view._destroy_all_widgets()
        except Exception as e:
            logger.warning(f"Error destruyendo widgets de live_view en logout: {e}")

        # Cancelar workers del thread pool global del API client antes de
        # invalidar el token: evita que respuestas tardías toquen widgets
        # que ya no existen.
        try:
            from PySide6.QtCore import QThreadPool
            pool = QThreadPool.globalInstance()
            pool.clear()           # cancela los que no han empezado
            pool.waitForDone(2000) # espera hasta 2s a los que ya empezaron
        except Exception as e:
            logger.warning(f"Error limpiando QThreadPool en logout: {e}")

        api_client.clear_tokens()
        self.show_login()

    # ------------------------------------------------------------------
    # Datos
    # ------------------------------------------------------------------
    def _load_cameras(self):
        def on_cameras(response):
            if not response.success:
                logger.warning(f"No se pudieron cargar cámaras: {response.error}")
                return
            from desktop_app.src.models.camera import Camera
            # from_dict ignora campos extra (forward-compat con backend)
            self.cameras = [Camera.from_dict(c) for c in (response.data or [])]
            logger.info(f"Cargadas {len(self.cameras)} cámaras desde el backend")
            token = api_client.get_stream_token() or ""
            self.live_view.set_cameras(self.cameras, token)
            self.playback_view.set_cameras(self.cameras)

        api_client.get("cameras/", on_cameras)

    def _reload_cameras(self):
        self._load_cameras()

    # ------------------------------------------------------------------
    # Navegación
    # ------------------------------------------------------------------
    def _switch_view(self, index: int):
        # Detener streams si vamos a una vista que no usa video.
        # VIEW_LIVE y VIEW_CONTROL ambos usan video — no detener entre ellos.
        if index not in (VIEW_LIVE, VIEW_CONTROL):
            video_streamer.stop_all()

        # Si vamos a LIVE, reiniciar grid de streams.
        # (CameraControlView gestiona su propio stream en su showEvent.)
        if index == VIEW_LIVE:
            token = api_client.get_stream_token() or ""
            self.live_view.restart_streams(token)
            video_streamer.set_base_url(config.API_BASE_URL)

        # Actualizar checked en sidebar (solo botones que existen en sidebar)
        for view_idx, btn in self._nav_buttons.items():
            btn.setChecked(view_idx == index)

        self.content_stack.setCurrentIndex(index)

    # ------------------------------------------------------------------
    # Hooks de vistas
    # ------------------------------------------------------------------
    def _on_ptz_request(self, camera):
        """Click derecho → Control PTZ: abrir vista dedicada."""
        self._open_camera_control(camera.id)

    def _on_camera_config_request(self, camera_id: int):
        """Click en ⚙ del CameraWidget → abrir vista de control completo."""
        self._open_camera_control(camera_id)

    def _open_camera_control(self, camera_id: int, lens: str = "main"):
        """
        Navega a CameraControlView (vista dedicada de una cámara).
        Reemplaza el viejo diálogo modal que era incómodo.
        """
        # Refrescar la lista de cámaras en la vista
        self.camera_control_view.set_cameras(self.cameras)
        # Pedirle que muestre esta cámara específica
        self.camera_control_view.show_camera(camera_id, lens=lens)
        # Cambiar a esa vista
        self._switch_view(VIEW_CONTROL)

    def _on_jump_to_playback(self, camera_id: int, _timestamp):
        """Doble-click en evento → cambiar a playback en esa cámara."""
        # Cambiar a la pestaña de playback
        self._switch_view(VIEW_PLAYBACK)
        # Seleccionar la cámara y cargar timeline
        for i in range(self.playback_view.cmb_camera.count()):
            if self.playback_view.cmb_camera.itemData(i) == camera_id:
                self.playback_view.cmb_camera.setCurrentIndex(i)
                QTimer.singleShot(200, self.playback_view._load_timeline)
                break

    def _on_api_error(self, error: str):
        self.statusbar.showMessage(f"Error: {error}", 5000)

    def _show_about(self):
        QMessageBox.about(
            self, "Acerca de",
            "<b>NVR VMS Professional v2.0</b><br>"
            "Sistema de Videovigilancia profesional<br>"
            "para redes LAN.<br><br>"
            "<i>Soporta cámaras ONVIF, IA con YOLOv8, "
            "PTZ, audio bidireccional, grabación continua "
            "y notificaciones Telegram.</i>"
        )
