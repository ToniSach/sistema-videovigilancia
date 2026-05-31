"""
Vista de gestión de usuarios (solo admin).
CRUD completo + cambio de rol + reset de password.
"""
import logging
from typing import Optional, List

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialog,
    QLineEdit, QComboBox, QFormLayout, QMessageBox, QCheckBox,
)
from PySide6.QtCore import Qt, Signal

from desktop_app.src.config import config
from desktop_app.src.ui.icons import icon
from desktop_app.src.services.api_client import api_client
from desktop_app.src.ui.components.glass_card import GlassCard

logger = logging.getLogger(__name__)


class UserEditDialog(QDialog):
    """Diálogo para crear o editar un usuario."""

    def __init__(self, user: Optional[dict] = None, parent=None):
        super().__init__(parent)
        self.user = user
        self.is_edit = user is not None
        self.setWindowTitle("Editar usuario" if self.is_edit else "Nuevo usuario")
        self.setMinimumWidth(400)
        self.setModal(True)
        self._setup_ui()
        if self.is_edit:
            self._load_user()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.txt_username = QLineEdit()
        self.txt_username.setPlaceholderText("usuario_login")
        if self.is_edit:
            self.txt_username.setReadOnly(True)
        form.addRow("Usuario:", self.txt_username)

        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.Password)
        if self.is_edit:
            self.txt_password.setPlaceholderText("(dejar vacío para no cambiar)")
        else:
            self.txt_password.setPlaceholderText("Contraseña inicial")
        form.addRow("Password:", self.txt_password)

        self.cmb_role = QComboBox()
        self.cmb_role.addItems(["user", "admin"])
        form.addRow("Rol:", self.cmb_role)

        self.chk_active = QCheckBox("Cuenta activa")
        self.chk_active.setChecked(True)
        form.addRow(self.chk_active)

        layout.addLayout(form)

        # Botones
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        save = QPushButton("Guardar")
        save.clicked.connect(self._validate)
        save.setDefault(True)
        btns.addWidget(save)
        layout.addLayout(btns)

        self.setStyleSheet(_DIALOG_STYLE)

    def _load_user(self):
        self.txt_username.setText(self.user.get("username", ""))
        role = self.user.get("role", "user")
        idx = self.cmb_role.findText(role)
        if idx >= 0:
            self.cmb_role.setCurrentIndex(idx)
        self.chk_active.setChecked(self.user.get("is_active", True))

    def _validate(self):
        if not self.txt_username.text().strip():
            QMessageBox.warning(self, "Falta dato", "El usuario es obligatorio")
            return
        if not self.is_edit and not self.txt_password.text():
            QMessageBox.warning(self, "Falta dato", "La contraseña es obligatoria")
            return
        self.accept()

    def get_data(self) -> dict:
        data = {
            "username": self.txt_username.text().strip(),
            "role": self.cmb_role.currentText(),
            "is_active": self.chk_active.isChecked(),
        }
        pwd = self.txt_password.text()
        if pwd:
            data["password"] = pwd
        return data


class UsersView(QWidget):
    """Vista principal de usuarios (admin only)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._users: List[dict] = []
        self._is_admin = False
        self._current_user_id: Optional[int] = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Gestión de Usuarios")
        title.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 22px; font-weight: bold;"
        )
        header.addWidget(title)
        header.addStretch()

        self.btn_refresh = QPushButton("  Refrescar")
        self.btn_refresh.setIcon(icon("refresh"))
        self.btn_refresh.clicked.connect(self.refresh)
        header.addWidget(self.btn_refresh)

        self.btn_add = QPushButton("  Nuevo usuario")
        self.btn_add.setIcon(icon("add"))
        self.btn_add.clicked.connect(self._add)
        header.addWidget(self.btn_add)
        layout.addLayout(header)

        # Banner si no es admin
        self.lbl_warn = QLabel(
            "Esta sección solo está disponible para administradores."
        )
        self.lbl_warn.setStyleSheet(
            f"background:{config.THEME_DANGER}; color:white; padding:8px; "
            f"border-radius:6px; font-weight:bold;"
        )
        self.lbl_warn.setVisible(False)
        layout.addWidget(self.lbl_warn)

        # Tabla
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "ID", "Usuario", "Rol", "Estado", "Creado"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.cellDoubleClicked.connect(lambda *a: self._edit())
        self.table.setStyleSheet(_TABLE_STYLE)
        layout.addWidget(self.table, 1)

        # Botones inferiores
        actions = QHBoxLayout()
        self.btn_edit = QPushButton("  Editar")
        self.btn_edit.setIcon(icon("edit"))
        self.btn_edit.clicked.connect(self._edit)
        self.btn_edit.setEnabled(False)
        actions.addWidget(self.btn_edit)

        self.btn_delete = QPushButton("  Eliminar")
        self.btn_delete.setIcon(icon("delete"))
        self.btn_delete.clicked.connect(self._delete)
        self.btn_delete.setEnabled(False)
        actions.addWidget(self.btn_delete)

        actions.addStretch()
        layout.addLayout(actions)

    def set_current_user(self, user_id: int, role: str):
        """Llamado por MainWindow tras el login para ajustar privilegios."""
        self._current_user_id = user_id
        self._is_admin = (role == "admin")
        if not self._is_admin:
            self.lbl_warn.setVisible(True)
            self.btn_add.setEnabled(False)
            self.btn_refresh.setEnabled(False)
        else:
            self.lbl_warn.setVisible(False)
            self.btn_add.setEnabled(True)
            self.btn_refresh.setEnabled(True)
            self.refresh()

    def refresh(self):
        def on_users(response):
            if not response.success:
                if response.status_code == 403:
                    self.lbl_warn.setVisible(True)
                else:
                    QMessageBox.warning(
                        self, "Error", f"No se pudieron cargar usuarios: {response.error}"
                    )
                return
            self._users = response.data or []
            self._populate()
        api_client.get("users/", on_users)

    def _populate(self):
        self.table.setRowCount(0)
        for u in self._users:
            r = self.table.rowCount()
            self.table.insertRow(r)
            it_id = QTableWidgetItem(str(u.get("id", "")))
            it_id.setData(Qt.UserRole, u.get("id"))
            self.table.setItem(r, 0, it_id)
            self.table.setItem(r, 1, QTableWidgetItem(u.get("username", "")))
            self.table.setItem(r, 2, QTableWidgetItem(u.get("role", "user")))
            active = u.get("is_active", True)
            self.table.setItem(r, 3, QTableWidgetItem("Activo" if active else "Inactivo"))
            created = u.get("created_at", "")
            self.table.setItem(r, 4, QTableWidgetItem(str(created)[:19]))

    def _update_buttons(self):
        rows = self.table.selectionModel().selectedRows()
        sel = bool(rows) and self._is_admin
        self.btn_edit.setEnabled(sel)
        self.btn_delete.setEnabled(sel)

    def _selected_user(self) -> Optional[dict]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        uid = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        return next((u for u in self._users if u.get("id") == uid), None)

    def _add(self):
        dlg = UserEditDialog(parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        def on_create(response):
            if response.success:
                self.refresh()
            else:
                QMessageBox.critical(self, "Error", response.error or "Error")
        api_client.post("users/", on_create, data=dlg.get_data())

    def _edit(self):
        u = self._selected_user()
        if not u:
            return
        dlg = UserEditDialog(user=u, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        # No mandamos username en update (es read-only)
        data.pop("username", None)

        def on_update(response):
            if response.success:
                self.refresh()
            else:
                QMessageBox.critical(self, "Error", response.error or "Error")
        api_client.put(f"users/{u['id']}", on_update, data=data)

    def _delete(self):
        u = self._selected_user()
        if not u:
            return
        if u.get("id") == self._current_user_id:
            QMessageBox.warning(self, "No permitido", "No puedes eliminarte a ti mismo")
            return
        ans = QMessageBox.question(
            self, "Confirmar",
            f"¿Eliminar al usuario '{u.get('username')}'?\n"
            "Esta acción no se puede deshacer."
        )
        if ans != QMessageBox.Yes:
            return

        def on_delete(response):
            if response.success:
                self.refresh()
            else:
                QMessageBox.critical(self, "Error", response.error or "Error")
        api_client.delete(f"users/{u['id']}", on_delete)


# =============================================================================
# Estilos compartidos (los reuso en otras vistas administrativas)
# =============================================================================
_TABLE_STYLE = f"""
QTableWidget {{
    background-color: {config.GLASS_BG};
    color: {config.THEME_TEXT};
    border: 1px solid {config.GLASS_BORDER};
    border-radius: 8px;
    gridline-color: {config.GLASS_BORDER};
}}
QHeaderView::section {{
    background-color: {config.THEME_SECONDARY};
    color: {config.THEME_TEXT};
    padding: 6px;
    border: none;
    border-right: 1px solid {config.GLASS_BORDER};
    font-weight: bold;
}}
QTableWidget::item:selected {{
    background-color: {config.THEME_ACCENT};
    color: {config.THEME_PRIMARY};
}}
"""

_DIALOG_STYLE = f"""
QDialog {{
    background-color: {config.THEME_PRIMARY};
}}
QLabel {{ color: {config.THEME_TEXT}; }}
QLineEdit, QComboBox {{
    background-color: {config.THEME_SECONDARY};
    color: {config.THEME_TEXT};
    border: 1px solid {config.GLASS_BORDER};
    border-radius: 6px;
    padding: 6px;
}}
QCheckBox {{ color: {config.THEME_TEXT}; }}
QPushButton {{
    background-color: {config.THEME_SECONDARY};
    color: {config.THEME_TEXT};
    border: 1px solid {config.GLASS_BORDER};
    border-radius: 6px;
    padding: 6px 14px;
}}
QPushButton:default {{
    background-color: {config.THEME_ACCENT};
    color: {config.THEME_PRIMARY};
    font-weight: bold;
}}
"""
