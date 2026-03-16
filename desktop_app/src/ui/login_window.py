import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, 
    QPushButton, QWidget
)
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QGuiApplication
# ✅ CORREGIDO: Import relativo
from services.api_client import api_client

class LoginThread(QThread):
    finished = Signal(object)  # Emite dict o None

    def __init__(self, username: str, password: str):
        super().__init__()
        self.username = username
        self.password = password

    def run(self):
        try:
            response = api_client.login(self.username, self.password)
            self.finished.emit(response)
        except Exception as e:
            logging.error(f"Login error: {e}")
            self.finished.emit(None)

class LoginWindow(QDialog):
    login_success = Signal(str, str)  # access_token, refresh_token

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("NVR System — Iniciar sesión")
        self.setFixedSize(400, 300)
        self._center_on_screen()
        self.setWindowFlags(
            Qt.WindowType.Dialog | 
            Qt.WindowType.MSWindowsFixedSizeDialogHint
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(30, 30, 30, 30)

        # Title
        title = QLabel("Sistema NVR")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        layout.addSpacing(20)

        # Username
        user_label = QLabel("Usuario:")
        layout.addWidget(user_label)
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Ingrese usuario")
        layout.addWidget(self.username_input)

        # Password
        pass_label = QLabel("Contraseña:")
        layout.addWidget(pass_label)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Ingrese contraseña")
        self.password_input.returnPressed.connect(self._do_login)
        layout.addWidget(self.password_input)

        # Error label
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: red;")
        self.error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        layout.addStretch()

        # Login button
        self.login_btn = QPushButton("Iniciar sesión")
        self.login_btn.setStyleSheet("""
            QPushButton {
                padding: 10px;
                font-size: 14px;
                background-color: #0078d7;
                color: white;
                border: none;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #006cbd;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.login_btn.clicked.connect(self._do_login)
        layout.addWidget(self.login_btn)

        self._thread: LoginThread | None = None

    def _center_on_screen(self):
        """Centra la ventana en la pantalla actual"""
        screen = QGuiApplication.primaryScreen()
        if screen:
            screen_geo = screen.availableGeometry()
            x = (screen_geo.width() - self.width()) // 2
            y = (screen_geo.height() - self.height()) // 2
            self.move(x, y)

    def _do_login(self):
        """Inicia el proceso de login en thread separado"""
        username = self.username_input.text().strip()
        password = self.password_input.text()

        if not username or not password:
            self.error_label.setText("Ingrese usuario y contraseña")
            self.error_label.show()
            return

        self.error_label.hide()
        self.login_btn.setEnabled(False)
        self.login_btn.setText("Conectando...")

        # Crear y ejecutar thread de login
        self._thread = LoginThread(username, password)
        self._thread.finished.connect(self._on_login_finished)
        self._thread.start()

    def _on_login_finished(self, response: dict | None):
        """Callback cuando el thread de login termina"""
        self.login_btn.setEnabled(True)
        self.login_btn.setText("Iniciar sesión")

        if response and "access_token" in response:
            access_token = response.get("access_token", "")
            refresh_token = response.get("refresh_token", "")
            self.login_success.emit(access_token, refresh_token)
            self.accept()  # Cierra el diálogo con éxito
        else:
            self.error_label.setText("Credenciales inválidas")
            self.error_label.show()
            self.password_input.clear()
            self.password_input.setFocus()

    def closeEvent(self, event):
        """Asegura que el thread termine al cerrar la ventana"""
        if self._thread and self._thread.isRunning():
            self._thread.wait(1000)
        event.accept()