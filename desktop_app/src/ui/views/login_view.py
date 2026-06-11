"""
================================================================================
MÓDULO: ui.views.login_view — Pantalla de autenticación del cliente desktop
================================================================================

PROPÓSITO
    Primera pantalla que ve el usuario (índice 0 del stack externo de
    MainWindow). Cubre TRES flujos en una sola tarjeta glassmorphism que muta
    sus etiquetas/botones según el modo:
      1. Login normal (usuario + contraseña).
      2. "Crear administrador" — primer arranque del sistema (sin usuarios):
         se detecta automáticamente al construir la vista y crea la primera
         cuenta admin.
      3. "Crear cuenta" — registro de un usuario normal (alternable con un
         enlace bajo el botón principal).

RESPONSABILIDAD
    - Validar campos y delegar la I/O en api_client (login/registro/setup).
    - Guardar los tokens devueltos (api_client.set_tokens) y emitir
      `login_successful` con el dict de usuario para que MainWindow arranque la
      app autenticada.
    - Mostrar feedback de estado en `lbl_status` (autenticando, errores,
      "Sesión expirada").

DEPENDENCIAS
    - services/api_client.py (singleton):
        * check_setup_status → GET /auth/setup-status (¿hay que crear admin?)
        * login(usuario, contraseña) → POST /auth/login
        * setup_admin(...)           → POST /auth/setup (primer admin)
        * register(...)              → POST /auth/register (usuario normal)
        * set_tokens(...)            → persiste el JWT en el cliente
        * señal auth_error           → refresh fallido / sesión expirada
    - ui/components/glass_card.GlassCard : tarjeta contenedora.
    - models/user.AuthTokens : DTO de tokens (import diferido).
    - config : paleta de tema (THEME_*) para el QSS inline.

COMPONENTES RELACIONADOS
    GlassCard (superficie de la tarjeta). No usa otros widgets de components/.

PUNTO DE ENTRADA
    La instancia MainWindow._setup_ui (una sola vez) y conecta su señal
    `login_successful` a MainWindow._on_login_success. MainWindow llama a
    `reset()` cada vez que vuelve al login (logout/expiración).

PIPELINE(S)
    #1 Inicio (detección de primer arranque → crear admin).
    #2 Auth (login/registro, emisión de tokens, manejo de sesión expirada).

NOTA HISTÓRICA
    El QSS usa colores PRECALCULADOS de config (THEME_ACCENT_LIGHT/DARK) en
    lugar de .lighter()/.darker(), que no son válidos dentro de una hoja QSS.
================================================================================
"""
import logging
from typing import Callable

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                               QLineEdit, QPushButton, QCheckBox, QFrame, QMessageBox)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QFont

from desktop_app.src.config import config
from desktop_app.src.ui.components.glass_card import GlassCard
from desktop_app.src.services.api_client import api_client

logger = logging.getLogger(__name__)


class LoginView(QWidget):
    """
    Vista de autenticación (login / crear admin / registro).

    RESPONSABILIDAD / ROL
        Única puerta de entrada a la app: autentica o crea cuentas y, en éxito,
        propaga los datos del usuario hacia arriba para que MainWindow conmute a
        la app. Es un QWidget autocontenido: no conoce a ninguna otra vista.

    QUIÉN LA INSTANCIA
        MainWindow._setup_ui (índice 0 del stack externo).

    SEÑALES QT
        - EMITE `login_successful(dict)`: datos del usuario (id/username/role/
          accessible_cameras) tras un login/setup exitoso. La escucha
          MainWindow._on_login_success.
        - ESCUCHA `api_client.auth_error` → `_on_auth_error`: el refresh del
          token falló (sesión expirada) → muestra el aviso y rehabilita el botón.

    ESTADO
        - `_setup_mode`: True cuando la pantalla está en "Crear administrador"
          (primer arranque sin usuarios).
        - `_register_mode`: True cuando está en "Crear cuenta" (usuario normal).
        Ambos mutan las etiquetas/placeholders/botones de la MISMA tarjeta.

    DEPENDENCIAS
        api_client (auth), GlassCard (UI), AuthTokens (DTO de tokens).
    """

    login_successful = Signal(dict)  # datos del usuario
    
    def __init__(self, parent=None):
        super().__init__(parent)

        self._setup_mode = False  # True = crear primer admin
        self._register_mode = False  # True = registrar usuario normal

        self._setup_ui()
        self._setup_styles()

        # Conectar señales API
        api_client.auth_error.connect(self._on_auth_error)

        # Detectar primer arranque (sin usuarios) → modo "Crear administrador".
        self._check_setup()
    
    def _setup_ui(self):
        """Construye interfaz."""
        self.setObjectName("loginView")
        
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(20)
        
        # Card principal
        self.card = GlassCard(self)
        self.card.setMinimumWidth(400)
        self.card.setMaximumWidth(450)
        
        card_layout = QVBoxLayout(self.card)
        card_layout.setSpacing(16)
        card_layout.setContentsMargins(32, 32, 32, 32)
        
        # Logo/Título
        self.lbl_title = QLabel("Sistema de videovigilancia")
        self.lbl_title.setFont(QFont("Inter", 22, QFont.Bold))
        self.lbl_title.setAlignment(Qt.AlignCenter)
        self.lbl_title.setWordWrap(True)
        card_layout.addWidget(self.lbl_title)

        self.lbl_subtitle = QLabel("Para red local (LAN)")
        self.lbl_subtitle.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.lbl_subtitle)
        
        card_layout.addSpacing(20)
        
        # Username
        self.txt_username = QLineEdit()
        self.txt_username.setPlaceholderText("Usuario")
        self.txt_username.setMinimumHeight(36)
        self.txt_username.returnPressed.connect(self._on_login)
        card_layout.addWidget(self.txt_username)
        
        # Password
        self.txt_password = QLineEdit()
        self.txt_password.setPlaceholderText("Contraseña")
        self.txt_password.setEchoMode(QLineEdit.Password)
        self.txt_password.setMinimumHeight(36)
        self.txt_password.returnPressed.connect(self._on_login)
        card_layout.addWidget(self.txt_password)

        # Confirmar contraseña (solo visible en modo "crear administrador")
        self.txt_password2 = QLineEdit()
        self.txt_password2.setPlaceholderText("Repite la contraseña")
        self.txt_password2.setEchoMode(QLineEdit.Password)
        self.txt_password2.setMinimumHeight(36)
        self.txt_password2.returnPressed.connect(self._on_login)
        self.txt_password2.setVisible(False)
        card_layout.addWidget(self.txt_password2)

        # Recordar sesión
        self.chk_remember = QCheckBox("Recordar sesión")
        card_layout.addWidget(self.chk_remember)
        
        card_layout.addSpacing(10)
        
        # Botón login
        self.btn_login = QPushButton("Iniciar Sesión")
        self.btn_login.setMinimumHeight(40)
        self.btn_login.setCursor(Qt.PointingHandCursor)
        self.btn_login.clicked.connect(self._on_login)
        card_layout.addWidget(self.btn_login)

        # Enlace registrarse / volver a login
        self.btn_register = QPushButton("¿No tienes cuenta? Regístrate")
        self.btn_register.setObjectName("linkButton")
        self.btn_register.setFlat(True)
        self.btn_register.setCursor(Qt.PointingHandCursor)
        self.btn_register.clicked.connect(self._toggle_register_mode)
        card_layout.addWidget(self.btn_register)

        # Status
        self.lbl_status = QLabel("")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.lbl_status)
        
        layout.addWidget(self.card, alignment=Qt.AlignCenter)
        
        # Versión
        self.lbl_version = QLabel("v2.0.0")
        self.lbl_version.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        layout.addWidget(self.lbl_version)
    
    def _setup_styles(self):
        """Aplica estilos glassmorphism."""
        # FIX: Usar las propiedades de color precalculadas en config
        # en lugar de llamar .lighter()/.darker() en el string QSS (inválido)
        self.setStyleSheet(f"""
            #loginView {{
                background-color: {config.THEME_PRIMARY};
                background-image: linear-gradient(to bottom right, {config.THEME_PRIMARY}, #1e293b);
            }}
            QLabel {{
                color: {config.THEME_TEXT};
                font-family: Inter, sans-serif;
            }}
            QLineEdit {{
                background-color: rgba(15, 23, 42, 0.6);
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 8px;
                padding: 8px 12px;
                color: {config.THEME_TEXT};
                font-size: 14px;
            }}
            QLineEdit:focus {{
                border: 1px solid {config.THEME_ACCENT};
            }}
            QPushButton {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
                border: none;
                border-radius: 8px;
                font-weight: bold;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: {config.THEME_ACCENT_LIGHT};
            }}
            QPushButton:pressed {{
                background-color: {config.THEME_ACCENT_DARK};
            }}
            QPushButton#linkButton {{
                background: transparent;
                color: {config.THEME_ACCENT};
                font-weight: normal;
                font-size: 13px;
            }}
            QPushButton#linkButton:hover {{
                background: transparent;
                color: {config.THEME_ACCENT_LIGHT};
                text-decoration: underline;
            }}
            QCheckBox {{
                color: {config.THEME_TEXT_MUTED};
                font-size: 12px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid {config.GLASS_BORDER};
                background-color: rgba(15, 23, 42, 0.6);
            }}
            QCheckBox::indicator:checked {{
                background-color: {config.THEME_ACCENT};
            }}
        """)
    
    def _check_setup(self):
        """Detecta primer arranque para entrar en modo "Crear administrador".

        Propósito: si el backend reporta `needs_setup` (no hay ningún usuario),
        muta la pantalla a creación del primer admin. Async (callback en hilo
        UI). Llamado por: `__init__`. Llama a: api_client.check_setup_status
        (GET /auth/setup-status) → `_enter_setup_mode`."""
        def on_status(response):
            if response.success and (response.data or {}).get("needs_setup"):
                self._enter_setup_mode()
        api_client.check_setup_status(on_status)

    def _enter_setup_mode(self):
        """Convierte la pantalla en 'Crear administrador' (primer arranque)."""
        self._setup_mode = True
        self.lbl_title.setText("Crear administrador")
        self.lbl_subtitle.setText(
            "Primer uso: crea la cuenta de administrador del sistema"
        )
        self.txt_username.setPlaceholderText("Nuevo usuario admin")
        self.txt_password.setPlaceholderText("Contraseña (mín. 6 caracteres)")
        self.txt_password2.setVisible(True)
        self.chk_remember.setVisible(False)
        self.btn_register.setVisible(False)
        self.btn_login.setText("Crear administrador")
        self.lbl_status.setText(
            "Aún no hay usuarios. Crea el administrador para empezar."
        )

    def _toggle_register_mode(self):
        """Alterna entre la pantalla de login y la de registro de usuario."""
        if self._register_mode:
            self._exit_register_mode()
        else:
            self._enter_register_mode()

    def _enter_register_mode(self):
        """Convierte la pantalla en 'Crear cuenta' (usuario normal)."""
        self._register_mode = True
        self.lbl_title.setText("Crear cuenta")
        self.lbl_subtitle.setText("Regístrate con un usuario y contraseña")
        self.txt_username.setPlaceholderText("Nuevo usuario")
        self.txt_password.setPlaceholderText("Contraseña (mín. 6 caracteres)")
        self.txt_password2.setVisible(True)
        self.chk_remember.setVisible(False)
        self.btn_login.setText("Registrarme")
        self.btn_register.setText("¿Ya tienes cuenta? Inicia sesión")
        self.lbl_status.setText("")
        self.txt_username.clear()
        self.txt_password.clear()
        self.txt_password2.clear()
        self.txt_username.setFocus()

    def _exit_register_mode(self):
        """Vuelve a la pantalla de inicio de sesión."""
        self._register_mode = False
        self.lbl_title.setText("Sistema de videovigilancia")
        self.lbl_subtitle.setText("Sistema de Videovigilancia")
        self.txt_username.setPlaceholderText("Usuario")
        self.txt_password.setPlaceholderText("Contraseña")
        self.txt_password2.setVisible(False)
        self.chk_remember.setVisible(True)
        self.btn_login.setText("Iniciar Sesión")
        self.btn_register.setText("¿No tienes cuenta? Regístrate")
        self.lbl_status.setText("")
        self.txt_username.clear()
        self.txt_password.clear()
        self.txt_password2.clear()
        self.txt_username.setFocus()

    def _on_login(self):
        """Slot del botón principal: enruta al flujo activo (login/setup/registro).

        Propósito: validar que hay usuario+contraseña y, según el modo, derivar
        a crear admin, registrar usuario o autenticar. Inputs: el texto de los
        campos. Outputs/Señales: en login exitoso → `_apply_login_success`
        (emite `login_successful`). Llamado por: clic en `btn_login` y
        `returnPressed` de los campos. Llama a: `_do_create_admin`,
        `_do_register`, o api_client.login (POST /auth/login)."""
        username = self.txt_username.text().strip()
        password = self.txt_password.text()

        if not username or not password:
            self.lbl_status.setText("Complete todos los campos")
            return

        if self._setup_mode:
            self._do_create_admin(username, password)
            return

        if self._register_mode:
            self._do_register(username, password)
            return

        self.lbl_status.setText("Autenticando...")
        self.btn_login.setEnabled(False)

        def on_response(response):
            self.btn_login.setEnabled(True)

            if response.success:
                self._apply_login_success(response.data)
            else:
                error_msg = response.error or "Error de autenticación"
                self.lbl_status.setText(f"Error: {error_msg}")
                logger.warning(f"Login fallido: {error_msg}")

        api_client.login(username, password, on_response)

    def _do_create_admin(self, username: str, password: str):
        """Crea el primer administrador del sistema (primer arranque).

        Propósito: validar (contraseña ≥6, confirmación coincide) y crear la
        cuenta admin. Si el backend devuelve tokens, entra directo; si no, vuelve
        a modo login. Inputs: usuario/contraseña del form. Señales: en auto-login
        emite `login_successful` vía `_apply_login_success`. Llamado por:
        `_on_login` (cuando `_setup_mode`). Llama a: api_client.setup_admin
        (POST /auth/setup)."""
        if len(password) < 6:
            self.lbl_status.setText("La contraseña debe tener al menos 6 caracteres")
            return
        if password != self.txt_password2.text():
            self.lbl_status.setText("Las contraseñas no coinciden")
            return

        self.lbl_status.setText("Creando administrador...")
        self.btn_login.setEnabled(False)

        def on_response(response):
            self.btn_login.setEnabled(True)
            if response.success and response.data:
                # El backend ya devuelve tokens → entrar directo.
                self._setup_mode = False
                self._apply_login_success(response.data)
            elif response.success:
                # Creado pero sin auto-login: volver a modo login.
                self._setup_mode = False
                self.txt_password2.setVisible(False)
                self.chk_remember.setVisible(True)
                self.lbl_title.setText("Sistema de videovigilancia")
                self.lbl_subtitle.setText("Sistema de Videovigilancia")
                self.btn_login.setText("Iniciar Sesión")
                self.lbl_status.setText("Administrador creado. Inicia sesión.")
            else:
                self.lbl_status.setText(f"Error: {response.error or 'No se pudo crear'}")

        api_client.setup_admin(username, password, on_response)

    def _do_register(self, username: str, password: str):
        """Registra un usuario normal y vuelve a la pantalla de login.

        Propósito: validar (usuario ≥3, contraseña ≥6, confirmación coincide) y
        crear la cuenta; NO autentica (deja al usuario iniciar sesión él mismo).
        Inputs: usuario/contraseña del form. Llamado por: `_on_login` (cuando
        `_register_mode`). Llama a: api_client.register (POST /auth/register)."""
        if len(username) < 3:
            self.lbl_status.setText("El usuario debe tener al menos 3 caracteres")
            return
        if len(password) < 6:
            self.lbl_status.setText("La contraseña debe tener al menos 6 caracteres")
            return
        if password != self.txt_password2.text():
            self.lbl_status.setText("Las contraseñas no coinciden")
            return

        self.lbl_status.setText("Creando cuenta...")
        self.btn_login.setEnabled(False)

        def on_response(response):
            self.btn_login.setEnabled(True)
            if response.success:
                # Cuenta creada: volver al login para iniciar sesión.
                self._exit_register_mode()
                self.txt_username.setText(username)
                self.txt_password.setFocus()
                self.lbl_status.setText("Cuenta creada. Inicia sesión.")
            else:
                self.lbl_status.setText(f"Error: {response.error or 'No se pudo crear'}")

        api_client.register(username, password, on_response)

    def _apply_login_success(self, data: dict):
        """Persiste los tokens y notifica el login a MainWindow.

        Propósito: extraer access/refresh token del payload del backend,
        guardarlos en el api_client (para que toda I/O posterior vaya
        autenticada) y emitir el usuario hacia arriba. Inputs: `data` (dict de
        respuesta con `access_token`/`refresh_token`/`user`). Señales: EMITE
        `login_successful(user)`. Llamado por: `_on_login` y `_do_create_admin`.
        Llama a: api_client.set_tokens."""
        from desktop_app.src.models.user import AuthTokens
        data = data or {}
        tokens = AuthTokens(
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
        )
        api_client.set_tokens(tokens)
        self.login_successful.emit(data.get("user", {}))
        self.lbl_status.setText("")
    
    def _on_auth_error(self):
        """Maneja error de autenticación."""
        self.lbl_status.setText("Sesión expirada")
        self.btn_login.setEnabled(True)
    
    def reset(self):
        """Limpia campos."""
        if self._register_mode:
            self._exit_register_mode()
        self.txt_username.clear()
        self.txt_password.clear()
        self.txt_password2.clear()
        self.lbl_status.clear()
        self.btn_login.setEnabled(True)