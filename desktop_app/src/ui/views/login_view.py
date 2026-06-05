"""
Vista de login con diseño glassmorphism - FIX Stylesheet.
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
    """Pantalla de autenticación."""
    
    login_successful = Signal(dict)  # datos del usuario
    
    def __init__(self, parent=None):
        super().__init__(parent)

        self._setup_mode = False  # True = crear primer admin

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
        self.lbl_title = QLabel("NVR VMS")
        self.lbl_title.setFont(QFont("Inter", 24, QFont.Bold))
        self.lbl_title.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.lbl_title)
        
        self.lbl_subtitle = QLabel("Sistema de Videovigilancia")
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
        """Consulta si hay que crear el primer admin (sistema sin usuarios)."""
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
        self.btn_login.setText("Crear administrador")
        self.lbl_status.setText(
            "Aún no hay usuarios. Crea el administrador para empezar."
        )

    def _on_login(self):
        """Inicia sesión o, en primer arranque, crea el admin."""
        username = self.txt_username.text().strip()
        password = self.txt_password.text()

        if not username or not password:
            self.lbl_status.setText("Complete todos los campos")
            return

        if self._setup_mode:
            self._do_create_admin(username, password)
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
        """Valida y crea el primer administrador, luego entra automáticamente."""
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
                self.lbl_title.setText("NVR VMS")
                self.lbl_subtitle.setText("Sistema de Videovigilancia")
                self.btn_login.setText("Iniciar Sesión")
                self.lbl_status.setText("Administrador creado. Inicia sesión.")
            else:
                self.lbl_status.setText(f"Error: {response.error or 'No se pudo crear'}")

        api_client.setup_admin(username, password, on_response)

    def _apply_login_success(self, data: dict):
        """Guarda tokens y emite el éxito de login."""
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
        self.txt_username.clear()
        self.txt_password.clear()
        self.lbl_status.clear()
        self.btn_login.setEnabled(True)