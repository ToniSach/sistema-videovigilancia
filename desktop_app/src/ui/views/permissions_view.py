"""
Vista de Permisos por cámara — quién puede ver/controlar qué.

UX: seleccionas una cámara a la izquierda, ves quién tiene acceso a la
derecha, con checks por capacidad (view, PTZ, LEDs, audio, descarga). Botón
"otorgar" abre selector de usuario.
"""
import logging
from typing import Optional, List

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView, QDialog, QComboBox, QCheckBox, QFormLayout,
    QMessageBox, QSplitter,
)
from PySide6.QtCore import Qt

from desktop_app.src.config import config
from desktop_app.src.services.api_client import api_client
from desktop_app.src.ui.components.glass_card import GlassCard

logger = logging.getLogger(__name__)


class GrantPermissionDialog(QDialog):
    """Diálogo para otorgar permisos a un usuario sobre una cámara."""

    def __init__(self, camera_name: str, users: List[dict], parent=None):
        super().__init__(parent)
        self.users = users
        self.setWindowTitle(f"Otorgar acceso a {camera_name}")
        self.setMinimumWidth(380)
        self.setModal(True)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.cmb_user = QComboBox()
        for u in self.users:
            self.cmb_user.addItem(
                f"{u.get('username')} ({u.get('role', 'user')})", u.get("id")
            )
        form.addRow("Usuario:", self.cmb_user)

        layout.addLayout(form)

        # Checks de permisos
        perms_box = QVBoxLayout()
        perms_box.addWidget(QLabel("<b>Capacidades:</b>"))
        self.chk_view = QCheckBox("👁 Ver stream en vivo")
        self.chk_view.setChecked(True)
        self.chk_ptz = QCheckBox("🎮 Controlar PTZ")
        self.chk_leds = QCheckBox("💡 Controlar LEDs / IR-Cut")
        self.chk_audio = QCheckBox("🎤 Audio bidireccional")
        self.chk_download = QCheckBox("⬇ Descargar grabaciones")
        for c in (self.chk_view, self.chk_ptz, self.chk_leds,
                  self.chk_audio, self.chk_download):
            perms_box.addWidget(c)
        layout.addLayout(perms_box)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        save = QPushButton("Otorgar")
        save.clicked.connect(self.accept)
        save.setDefault(True)
        btns.addWidget(save)
        layout.addLayout(btns)

        self.setStyleSheet(f"""
            QDialog {{ background-color: {config.THEME_PRIMARY}; }}
            QLabel, QCheckBox {{ color: {config.THEME_TEXT}; }}
            QComboBox {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 6px;
            }}
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
        """)

    def get_data(self) -> tuple[int, dict]:
        return self.cmb_user.currentData(), {
            "can_view": self.chk_view.isChecked(),
            "can_control_ptz": self.chk_ptz.isChecked(),
            "can_control_leds": self.chk_leds.isChecked(),
            "can_control_audio": self.chk_audio.isChecked(),
            "can_download_recordings": self.chk_download.isChecked(),
        }


class PermissionsView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cameras: List[dict] = []
        self._users: List[dict] = []
        self._current_camera_id: Optional[int] = None
        self._is_admin = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header
        header = QHBoxLayout()
        title = QLabel("🔐 Permisos por cámara")
        title.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 22px; font-weight: bold;"
        )
        header.addWidget(title)
        header.addStretch()

        self.btn_refresh = QPushButton("🔄 Refrescar")
        self.btn_refresh.clicked.connect(self.refresh)
        header.addWidget(self.btn_refresh)
        layout.addLayout(header)

        self.lbl_warn = QLabel(
            "⚠ Esta sección solo está disponible para administradores."
        )
        self.lbl_warn.setStyleSheet(
            f"background:{config.THEME_DANGER}; color:white; padding:8px; "
            f"border-radius:6px; font-weight:bold;"
        )
        self.lbl_warn.setVisible(False)
        layout.addWidget(self.lbl_warn)

        # Splitter: lista de cámaras a la izquierda, tabla de permisos a la derecha
        splitter = QSplitter(Qt.Horizontal)

        # Lista de cámaras
        left = GlassCard()
        l_layout = QVBoxLayout(left)
        l_layout.setContentsMargins(8, 8, 8, 8)
        l_layout.addWidget(QLabel("<b>Cámaras</b>"))
        self.list_cams = QListWidget()
        self.list_cams.itemSelectionChanged.connect(self._on_select_camera)
        self.list_cams.setStyleSheet(f"""
            QListWidget {{
                background-color: {config.GLASS_BG};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
            }}
            QListWidget::item {{ padding: 8px; }}
            QListWidget::item:selected {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
            }}
        """)
        l_layout.addWidget(self.list_cams, 1)
        splitter.addWidget(left)

        # Tabla de usuarios con acceso
        right = GlassCard()
        r_layout = QVBoxLayout(right)
        r_layout.setContentsMargins(8, 8, 8, 8)

        r_header = QHBoxLayout()
        self.lbl_cam_title = QLabel("Selecciona una cámara")
        self.lbl_cam_title.setStyleSheet(
            f"color: {config.THEME_ACCENT}; font-weight: bold; font-size: 14px;"
        )
        r_header.addWidget(self.lbl_cam_title)
        r_header.addStretch()
        self.btn_grant = QPushButton("➕ Otorgar acceso")
        self.btn_grant.clicked.connect(self._grant)
        self.btn_grant.setEnabled(False)
        r_header.addWidget(self.btn_grant)
        self.btn_revoke = QPushButton("🗑 Revocar")
        self.btn_revoke.clicked.connect(self._revoke)
        self.btn_revoke.setEnabled(False)
        r_header.addWidget(self.btn_revoke)
        r_layout.addLayout(r_header)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Usuario", "👁 Ver", "🎮 PTZ", "💡 LEDs", "🎤 Audio", "⬇ Download"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        from desktop_app.src.ui.views.users_view import _TABLE_STYLE
        self.table.setStyleSheet(_TABLE_STYLE)
        r_layout.addWidget(self.table, 1)
        splitter.addWidget(right)
        splitter.setSizes([300, 700])
        layout.addWidget(splitter, 1)

    def set_current_user(self, _user_id: int, role: str):
        self._is_admin = (role == "admin")
        if not self._is_admin:
            self.lbl_warn.setVisible(True)
            self.btn_refresh.setEnabled(False)
        else:
            self.lbl_warn.setVisible(False)
            self.btn_refresh.setEnabled(True)
            self.refresh()

    def refresh(self):
        # Cargar cámaras y usuarios en paralelo
        def on_cameras(response):
            if response.success:
                self._cameras = response.data or []
                self.list_cams.clear()
                for c in self._cameras:
                    it = QListWidgetItem(
                        f"#{c['id']} · {c.get('name', '')}\n   {c.get('ip_address', '')}"
                    )
                    it.setData(Qt.UserRole, c["id"])
                    self.list_cams.addItem(it)

        def on_users(response):
            if response.success:
                self._users = [u for u in (response.data or []) if u.get("is_active", True)]

        api_client.get("cameras/", on_cameras)
        api_client.get("users/", on_users)

    def _on_select_camera(self):
        sel = self.list_cams.selectedItems()
        if not sel:
            self._current_camera_id = None
            self.btn_grant.setEnabled(False)
            self.table.setRowCount(0)
            self.lbl_cam_title.setText("Selecciona una cámara")
            return
        cam_id = sel[0].data(Qt.UserRole)
        self._current_camera_id = cam_id
        cam = next((c for c in self._cameras if c["id"] == cam_id), None)
        self.lbl_cam_title.setText(
            f"Accesos para: {cam.get('name') if cam else cam_id}"
        )
        self.btn_grant.setEnabled(self._is_admin)
        self._load_permissions(cam_id)

    def _load_permissions(self, cam_id: int):
        def on_perms(response):
            self.table.setRowCount(0)
            if not response.success:
                return
            for p in response.data or []:
                r = self.table.rowCount()
                self.table.insertRow(r)
                it_u = QTableWidgetItem(p.get("username") or f"user {p.get('user_id')}")
                it_u.setData(Qt.UserRole, p.get("user_id"))
                self.table.setItem(r, 0, it_u)
                for col, key in enumerate(
                    ["can_view", "can_control_ptz", "can_control_leds",
                     "can_control_audio", "can_download_recordings"],
                    start=1
                ):
                    self.table.setItem(
                        r, col, QTableWidgetItem("✓" if p.get(key) else "—")
                    )
        api_client.get(f"permissions/camera/{cam_id}", on_perms)

    def _update_buttons(self):
        rows = self.table.selectionModel().selectedRows()
        self.btn_revoke.setEnabled(bool(rows) and self._is_admin)

    def _grant(self):
        if self._current_camera_id is None:
            return
        if not self._users:
            QMessageBox.information(self, "Sin usuarios", "Primero crea usuarios.")
            return
        cam = next(
            (c for c in self._cameras if c["id"] == self._current_camera_id), None
        )
        dlg = GrantPermissionDialog(
            camera_name=(cam.get("name") if cam else f"Cámara {self._current_camera_id}"),
            users=self._users,
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        user_id, perms = dlg.get_data()

        def on_grant(response):
            if response.success:
                self._load_permissions(self._current_camera_id)
            else:
                QMessageBox.critical(self, "Error", response.error or "Error")

        api_client.post(
            f"permissions/camera/{self._current_camera_id}/user/{user_id}",
            on_grant, data=perms,
        )

    def _revoke(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows or self._current_camera_id is None:
            return
        user_id = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        username = self.table.item(rows[0].row(), 0).text()
        ans = QMessageBox.question(
            self, "Confirmar",
            f"¿Revocar todos los permisos de '{username}' sobre esta cámara?"
        )
        if ans != QMessageBox.Yes:
            return

        def on_revoke(response):
            if response.success:
                self._load_permissions(self._current_camera_id)
            else:
                QMessageBox.critical(self, "Error", response.error or "Error")

        api_client.delete(
            f"permissions/camera/{self._current_camera_id}/user/{user_id}",
            on_revoke,
        )
