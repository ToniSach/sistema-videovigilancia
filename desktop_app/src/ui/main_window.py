"""
================================================================================
MÓDULO: ui.main_window — Shell raíz de la UI del cliente desktop (PySide6)
================================================================================

PROPÓSITO
    Contenedor raíz de toda la interfaz: orquesta el ciclo login → app → logout,
    construye la barra de navegación lateral y el QStackedWidget con TODAS las
    vistas, y reacciona a la expiración de sesión devolviendo al usuario al
    login. Es la única QMainWindow de la app; la instancia main.py tras aplicar
    el tema global.

RESPONSABILIDAD
    - Mantener DOS niveles de stack:
        * `self.stack` (nivel externo): índice 0 = login_view, índice 1 = app.
        * `self.content_stack` (nivel interno, dentro de la vista de app): una
          página por cada pantalla (live, eventos, playback, cámaras, …).
    - Reaccionar a la expiración de sesión: el api_client emite `auth_error`
      cuando el refresh del token falla. Esa señal la escucha login_view (no
      esta ventana) para mostrar "Sesión expirada"; el retorno efectivo al
      login lo provoca el usuario (logout) o un re-login desde esa pantalla.
      MainWindow, por su parte, escucha `request_error` para feedback de fallos
      transitorios en la barra de estado.
    - Instanciar una sola vez cada vista y conectar sus señales Qt a los hooks
      de navegación de esta ventana (p.ej. saltar de un evento a su playback).
    - Conmutar entre vistas (`_switch_view`) y mantener el botón de sidebar
      "checked" coherente con la página visible.
    - Aplicar control de acceso por rol en la UI: ocultar/mostrar las opciones
      "admin_only" tras autenticarse.
    - Limpiar recursos pesados (VLC, QPixmaps GPU, workers del thread pool) al
      cerrar sesión para evitar fugas en ciclos login/logout repetidos.

ROL EN LA NAVEGACIÓN
    Es el ENRUTADOR de la app: las vistas no se conocen entre sí; emiten señales
    (p.ej. `jump_to_playback`, `ptz_requested`) que esta ventana traduce en
    cambios de página del content_stack. Las constantes VIEW_* son los índices
    canónicos de ese stack y DEBEN coincidir con el orden de inserción en
    `_create_main_view`.

DEPENDENCIAS
    - services/api_client.py (singleton): toda la I/O REST + señal
      `request_error` (errores de red/HTTP → statusbar). El logout limpia
      tokens aquí y vacía el QThreadPool global de respuestas pendientes.
    - ui/views/* : todas las pantallas que se montan en el content_stack.
    - ui/components/glass_card.GlassCard : superficie de la sidebar.
    - ui/icons.icon : iconos de los botones de navegación.
    - models/user.User , models/camera.Camera : DTOs locales del backend.
    - config : paleta de tema (THEME_*) usada en los QSS inline de la sidebar.

COMPONENTES RELACIONADOS
    main.py la crea y la muestra. login_view emite `login_successful`; el resto
    de vistas emiten las señales que se cablean en `_create_main_view`. Los
    diálogos (onboarding_wizard, qr_link_dialog) se abren bajo demanda.

PUNTO DE ENTRADA
    `MainWindow()` (desde main.py) → `__init__` arranca en login. El flujo real
    empieza en `_on_login_success` cuando login_view confirma credenciales.

LAYOUT
    [ Sidebar (240px) ] | [ content_stack con la vista activa ]

    La sidebar agrupa la navegación en secciones:
      MONITOREO       Inicio · En vivo · Eventos · Reproducción
      CONFIGURACIÓN   Cámaras · Notificaciones · Sistema
      ADMINISTRACIÓN  Usuarios · Permisos · Dispositivos Telegram · Ajustes
                      (sección visible solo para el rol admin)
================================================================================
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

from desktop_app.src.ui.views.login_view import LoginView
from desktop_app.src.ui.views.live_view import LiveView
from desktop_app.src.ui.views.playback_view import PlaybackView
from desktop_app.src.ui.views.camera_management_view import CameraManagementView
from desktop_app.src.ui.views.camera_control_view import CameraControlView
from desktop_app.src.ui.views.events_view import EventsView
from desktop_app.src.ui.views.users_view import UsersView
from desktop_app.src.ui.views.permissions_view import PermissionsView
from desktop_app.src.ui.views.notifications_view import NotificationPreferencesView
from desktop_app.src.ui.views.telegram_devices_view import TelegramDevicesView
from desktop_app.src.ui.views.system_view import SystemHealthView

try:
    from desktop_app.src.ui.views.settings_view import SettingsView
    SETTINGS_AVAILABLE = True
except ImportError:
    SETTINGS_AVAILABLE = False

from desktop_app.src.ui.components.camera_control_panel import CameraControlPanel
from desktop_app.src.ui.components.glass_card import GlassCard

logger = logging.getLogger(__name__)


# Índices canónicos del content_stack. Son el "mapa de rutas" de la app: cada
# constante es la página a la que navega un botón de sidebar o un hook de vista.
# CRÍTICO: el valor de cada constante DEBE coincidir con el orden en que la
# vista se añade en _create_main_view (el comentario "# N" de cada addWidget).
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
VIEW_DASHBOARD = 10  # pantalla de inicio (se añade al final del stack)
VIEW_TELEGRAM_DEVICES = 11  # tabla de dispositivos Telegram (solo admin)


class MainWindow(QMainWindow):
    """
    Ventana principal y enrutador de la UI del cliente NVR.

    RESPONSABILIDAD / ROL
        Shell que aloja el login y, una vez autenticado, la app completa
        (sidebar + content_stack). Centraliza la navegación entre vistas y la
        gestión de sesión (login/logout/expiración).

    QUIÉN LA INSTANCIA / CONSUME
        La crea `main()` en main.py (única instancia). No la consume nadie más;
        es la cima del árbol de widgets.

    SEÑALES QT
        - Escucha `login_view.login_successful(dict)` → `_on_login_success`.
        - Escucha `api_client.request_error(str)` → `_on_api_error` (muestra el
          error en la barra de estado). La señal de expiración de sesión
          (`api_client.auth_error`, tras un refresh fallido) la escucha
          login_view, no esta ventana; `show_login` es el punto de retorno
          común a login para logout y re-autenticación.
        - Escucha las señales de cada vista cableadas en `_create_main_view`
          (p.ej. `events_view.jump_to_playback`, `live_view.ptz_requested`,
          `camera_control_view.back_requested`, las `dashboard_view.open_*`).
        - No define señales propias: actúa como sumidero/enrutador.

    ESTADO
        - `current_user`: User del login (rol → gating admin de la sidebar).
        - `cameras`: lista de Camera cacheada; se reparte a live/playback.
        - `_nav_buttons`: {índice_vista: QPushButton} para sincronizar "checked".
    """

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Sistema de videovigilancia")
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
        """Construye la página de "app" (índice 1 de self.stack): sidebar +
        content_stack con todas las vistas instanciadas una sola vez.

        Aquí se realiza el CABLEADO de navegación: cada vista emite señales y se
        conectan a los hooks de esta ventana (saltar a control de cámara, a
        playback de un evento, volver atrás, abrir páginas del dashboard…). El
        ORDEN de los addWidget es contractual: fija los índices VIEW_* (ver los
        comentarios "# N"). Llamado una vez desde `_setup_ui`."""
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

        # Dashboard de inicio (índice 10). Sus botones navegan a otras vistas.
        from desktop_app.src.ui.views.dashboard_view import DashboardView
        self.dashboard_view = DashboardView()
        self.dashboard_view.open_live.connect(lambda: self._switch_view(VIEW_LIVE))
        self.dashboard_view.open_events.connect(lambda: self._switch_view(VIEW_EVENTS))
        self.dashboard_view.open_playback.connect(lambda: self._switch_view(VIEW_PLAYBACK))
        self.dashboard_view.open_cameras.connect(lambda: self._switch_view(VIEW_CAMERAS))
        self.content_stack.addWidget(self.dashboard_view)        # 10

        # Dispositivos Telegram (índice 11, solo admin). Se refresca solo en su
        # showEvent al navegar a ella.
        self.telegram_devices_view = TelegramDevicesView()
        self.content_stack.addWidget(self.telegram_devices_view)  # 11

        layout.addWidget(self.content_stack, stretch=1)
        return widget

    def _create_sidebar(self):
        sidebar = GlassCard()
        sidebar.setFixedWidth(240)

        layout = QVBoxLayout(sidebar)
        layout.setSpacing(4)
        layout.setContentsMargins(12, 16, 12, 12)

        # Logo
        lbl_logo = QLabel("Sistema de videovigilancia")
        lbl_logo.setWordWrap(True)
        lbl_logo.setStyleSheet(f"""
            color: {config.THEME_ACCENT};
            font-size: 18px;
            font-weight: bold;
            padding: 4px 8px;
        """)
        layout.addWidget(lbl_logo)
        layout.addSpacing(12)

        # ===== Sección MONITOREO =====
        layout.addWidget(self._section_label("MONITOREO"))
        self._add_nav(layout, "Inicio", VIEW_DASHBOARD, active=True, icon_name="system")
        self._add_nav(layout, "En vivo", VIEW_LIVE, icon_name="live")
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
        self._add_nav(layout, "Dispositivos Telegram", VIEW_TELEGRAM_DEVICES,
                      admin_only=True, icon_name="telegram")
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
        """Crea un botón de navegación de la sidebar y lo registra.

        El botón es "checkable" (se ilumina la página activa) y al pulsarlo
        llama a `_switch_view(view_index)`. Si `admin_only`, marca la propiedad
        Qt "admin_only" y nace oculto: `_on_login_success` lo revela solo si el
        usuario es admin. Guarda el botón en `_nav_buttons[view_index]` para que
        `_switch_view` pueda sincronizar el estado "checked"."""
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
        """Suscribe la ventana a los errores generales del cliente HTTP.
        `api_client.request_error(str)` se emite ante fallos de red/HTTP no
        atados a la sesión; se enruta a `_on_api_error`, que los muestra en la
        barra de estado. La señal de sesión expirada (`auth_error`) la consume
        login_view, no esta ventana."""
        api_client.request_error.connect(self._on_api_error)

    # ------------------------------------------------------------------
    # Login / Logout
    # ------------------------------------------------------------------
    def show_login(self):
        """Vuelve a la pantalla de login (índice 0 del stack externo).

        Es el punto de retorno tras un logout o una expiración de sesión:
        resetea el formulario de login, oculta el menú y la barra de estado
        (no procede sin sesión) y muestra la vista de login. NO toca tokens —
        eso es responsabilidad de `_logout`/api_client. Llamado por `__init__`
        (arranque) y `_logout`."""
        self.stack.setCurrentIndex(0)
        self.login_view.reset()
        self.menuBar().setVisible(False)
        self.statusbar.setVisible(False)

    def _on_login_success(self, user_data: dict):
        """Slot del éxito de login: arranca la app autenticada.

        Propósito: materializar el usuario, aplicar gating por rol en la
        sidebar, inicializar las vistas que dependen del rol, cargar las
        cámaras y conmutar del login a la app mostrando el Dashboard.

        Inputs: `user_data` (dict del backend con id/username/role/
        accessible_cameras), emitido por `login_view.login_successful`.

        Señales: conectado a `LoginView.login_successful`.
        Llama a: `_load_cameras`, `_switch_view(VIEW_DASHBOARD)`, y de forma
        diferida (QTimer) al onboarding y a `_maybe_prompt_recordings_path`.
        Efectos: pasa `self.stack` al índice 1 (app) y revela menú/statusbar."""
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
        self.dashboard_view.set_user(self.current_user.username)

        self._load_cameras()

        # Switch a vista principal y abrir el Dashboard de inicio.
        self.stack.setCurrentIndex(1)
        self._switch_view(VIEW_DASHBOARD)
        self.menuBar().setVisible(True)
        self.statusbar.setVisible(True)

        # Tutorial automático en el primer login de este equipo
        try:
            from PySide6.QtCore import QTimer
            from desktop_app.src.ui.dialogs.onboarding_wizard import maybe_show_onboarding
            # Diferido para que la ventana principal ya esté pintada
            QTimer.singleShot(400, lambda: maybe_show_onboarding(parent=self))
            # Tras el wizard, ofrecer elegir la carpeta de grabaciones (1ª vez).
            QTimer.singleShot(600, self._maybe_prompt_recordings_path)
        except Exception as e:
            logger.warning(f"No se pudo mostrar onboarding: {e}")

    def _maybe_prompt_recordings_path(self):
        """Primer uso (solo admin): si la ruta de grabaciones aún no se ha
        elegido, ofrecer elegir una carpeta ahora. Se pregunta como mucho UNA
        vez por equipo (QSettings). Espera a que se cierre cualquier diálogo
        modal (p.ej. el wizard de bienvenida) para no solaparse."""
        from PySide6.QtCore import QSettings, QTimer
        from PySide6.QtWidgets import QApplication

        # Si hay un modal abierto (wizard), reintentar más tarde.
        if QApplication.activeModalWidget() is not None:
            QTimer.singleShot(800, self._maybe_prompt_recordings_path)
            return

        if not self.current_user or (self.current_user.role or "").lower() != "admin":
            return
        s = QSettings("NVR", "DesktopApp")
        if bool(s.value("onboarding/recordings_path_prompted", False, type=bool)):
            return

        def on_config(response):
            if not getattr(response, "success", False):
                return
            data = response.data or {}
            if isinstance(data, list):
                data = {item.get("key"): item.get("value") for item in data}
            path = str(data.get("recordings_path") or "").strip()
            # "Sin configurar" = vacío o un default relativo.
            configured = path and path not in ("./recordings", "recordings", ".\\recordings")
            if configured:
                # Ya hay ruta real: no molestar, y no volver a preguntar.
                s.setValue("onboarding/recordings_path_prompted", True)
                return
            self._ask_recordings_folder(s)

        api_client.get("system/config", on_config)

    def _ask_recordings_folder(self, settings):
        """Diálogo de elección de carpeta de grabaciones + guardado en backend."""
        from PySide6.QtWidgets import QMessageBox, QFileDialog
        # Marcar como preguntado pase lo que pase (no insistir en cada login).
        settings.setValue("onboarding/recordings_path_prompted", True)

        ans = QMessageBox.question(
            self, "Carpeta de grabaciones",
            "¿Quieres elegir ahora dónde se guardarán las grabaciones?\n\n"
            "Recomendado: una carpeta en un disco con espacio y FUERA de "
            "OneDrive o carpetas sincronizadas (la sincronización corrompe los "
            "vídeos). Podrás cambiarla luego en Ajustes → Almacenamiento.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if ans != QMessageBox.Yes:
            return
        path = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta de grabaciones"
        )
        if not path:
            return

        def on_saved(response):
            from PySide6.QtWidgets import QMessageBox as _MB
            if getattr(response, "success", False):
                _MB.information(
                    self, "Listo", f"Las grabaciones se guardarán en:\n{path}"
                )
            else:
                _MB.warning(
                    self, "Error",
                    f"No se pudo guardar la ruta: {getattr(response, 'error', '')}",
                )

        api_client.post("storage/config", on_saved, data={"path": path})

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
        """Cierra la sesión de forma ordenada y vuelve al login.

        Secuencia (orden importante para evitar fugas y callbacks huérfanos):
          1. Confirma con el usuario.
          2. Destruye los widgets de live_view → para VLC y libera QPixmaps de
             GPU (sin esto, login/logout repetidos hacían crecer la VRAM).
          3. Vacía el QThreadPool global del api_client (cancela pendientes,
             espera ≤2s a los activos) ANTES de invalidar el token: así ninguna
             respuesta tardía toca widgets ya destruidos.
          4. Limpia los tokens (`api_client.clear_tokens`) y muestra el login.

        Llamado por: botón "Cerrar sesión" de la sidebar. Llama a:
        `live_view._destroy_all_widgets`, `api_client.clear_tokens`,
        `show_login`."""
        ans = QMessageBox.question(
            self, "Cerrar sesión", "¿Seguro que quieres cerrar la sesión?"
        )
        if ans != QMessageBox.Yes:
            return

        # Destruir widgets del live view: detiene VLC y libera QPixmaps en GPU.
        # Sin esto, hacer logout/login repetidos causaba GPU memory creep.
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
        """Pide la lista de cámaras al backend y la reparte a las vistas.

        Async: `api_client.get("cameras/", …)` ejecuta en el QThreadPool y el
        callback `on_cameras` corre en el hilo de UI. Cachea las cámaras en
        `self.cameras` (DTOs Camera tolerantes a campos extra del backend) y las
        inyecta en live_view (con un token de stream de go2rtc) y playback_view.
        Llamado por: `_on_login_success` y `_reload_cameras` (tras editar
        cámaras en CameraManagementView)."""
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
        """Conmuta la página visible del content_stack (núcleo de la navegación).

        Inputs: `index` = una constante VIEW_*. Efectos: si se entra a LIVE,
        reinicia el grid de streams con un token fresco de go2rtc; sincroniza el
        estado "checked" de los botones de sidebar; y pone el content_stack en
        esa página. Llamado por: todos los botones de navegación, los hooks de
        vista (`_open_camera_control`, `_on_jump_to_playback`) y los `open_*` del
        dashboard. No detiene el directo al salir: cada vista cachea su player en
        su hideEvent (go2rtc multiplexa una sola conexión por cámara)."""
        # El directo lo gestiona cada vista con VLC (go2rtc). Al salir del live
        # view, su hideEvent mantiene los players en cache (go2rtc multiplexa
        # una sola conexión a la cámara, así que no penaliza). No hace falta
        # detener nada globalmente aquí.

        # Si vamos a LIVE, reiniciar grid de streams.
        # (CameraControlView gestiona su propio stream en su showEvent.)
        if index == VIEW_LIVE:
            token = api_client.get_stream_token() or ""
            self.live_view.restart_streams(token)

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

    def _on_jump_to_playback(self, camera_id: int, timestamp):
        """Hook: ir de un evento a su reproducción exacta.

        Slot de `events_view.jump_to_playback(camera_id, timestamp)`. Conmuta a
        la vista de reproducción y, diferido 200ms (para que la vista ya esté
        visible antes del seek), pide a playback_view cargar la cámara/día y
        saltar al segundo del evento. Llama a: `_switch_view(VIEW_PLAYBACK)`,
        `playback_view.jump_to_time`."""
        self._switch_view(VIEW_PLAYBACK)
        # Deferir un poco para que la vista esté visible antes de cargar/seek.
        QTimer.singleShot(
            200, lambda: self.playback_view.jump_to_time(camera_id, timestamp)
        )

    def _on_api_error(self, error: str):
        """Slot de `api_client.request_error`: muestra el fallo HTTP/red en la
        barra de estado durante 5s. Feedback global no intrusivo de errores
        transitorios. La expiración de sesión (refresh fallido) viaja por la
        señal `auth_error`, que escucha login_view, no este slot."""
        self.statusbar.showMessage(f"Error: {error}", 5000)

    def _show_about(self):
        QMessageBox.about(
            self, "Acerca de",
            "<b>Sistema de videovigilancia v2.0</b><br>"
            "Videovigilancia profesional<br>"
            "para redes LAN.<br><br>"
            "<i>Soporta cámaras ONVIF, detección de objetos por IA, "
            "PTZ, audio bidireccional, grabación continua "
            "y notificaciones Telegram.</i>"
        )
