"""
================================================================================
MÓDULO: ui.views.permissions_view — Permisos por cámara (UserCameraPermission)
================================================================================

PROPÓSITO
    Pantalla (solo administradores) para gestionar QUIÉN puede ver/controlar
    cada cámara. Materializa en la UI el modelo `UserCameraPermission` del
    backend: por cada (usuario, cámara) hay flags de capacidad (ver, PTZ, LEDs,
    audio, descargar grabaciones).

RESPONSABILIDAD
    - Listar cámaras (izquierda) y, al seleccionar una, mostrar la tabla de
      usuarios con acceso y sus capacidades (derecha).
    - Otorgar acceso a un usuario sobre la cámara (diálogo `GrantPermissionDialog`
      con checks por capacidad) y revocar todos sus permisos.
    - Gating de rol: si el usuario no es admin, deshabilita la sección.

DEPENDENCIAS (endpoints consumidos)
    - GET    cameras/ ........................... lista de cámaras (panel izq.).
    - GET    users/ ............................. usuarios activos (para otorgar).
    - GET    permissions/camera/{cam_id} ........ accesos actuales de la cámara.
    - POST   permissions/camera/{cam_id}/user/{user_id} .. otorga/actualiza flags.
    - DELETE permissions/camera/{cam_id}/user/{user_id} .. revoca el acceso.

COMPONENTES RELACIONADOS
    main_window la instancia (índice VIEW_PERMISSIONS=8) y le pasa el rol con
    `set_current_user`. Reutiliza `_TABLE_STYLE` de users_view y `GlassCard`.

PUNTO DE ENTRADA (en la app)
    Sidebar «Permisos» (solo admin) → MainWindow._switch_view(VIEW_PERMISSIONS).

PIPELINE(S)
    #2 Auth/autorización: estos permisos los aplica `PermissionService` en el
    backend para decidir el acceso a cámaras compartidas (no basta `owner_id`).

UX
    [ Cámaras (lista) ] | [ Usuarios con acceso + capacidades (tabla) ]
    Botón «Otorgar acceso» abre el selector de usuario; «Revocar» quita todo.
================================================================================
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
from desktop_app.src.ui.icons import icon
from desktop_app.src.services.api_client import api_client
from desktop_app.src.ui.components.glass_card import GlassCard

logger = logging.getLogger(__name__)


class GrantPermissionDialog(QDialog):
    """Diálogo modal para otorgar permisos a un usuario sobre una cámara.

    ROL: elige el usuario y marca las capacidades (ver, PTZ, LEDs, audio,
    descargar); devuelve (user_id, dict_de_flags) vía `get_data()`. No habla con
    el backend; el POST lo hace `PermissionsView._grant`.
    QUIÉN LO INSTANCIA: PermissionsView (botón «Otorgar acceso»).
    """

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
        self.chk_view = QCheckBox("Ver stream en vivo")
        self.chk_view.setIcon(icon("view"))
        self.chk_view.setChecked(True)
        self.chk_ptz = QCheckBox("Controlar PTZ")
        self.chk_ptz.setIcon(icon("ptz_ctrl"))
        self.chk_leds = QCheckBox("Controlar LEDs / IR-Cut")
        self.chk_leds.setIcon(icon("led"))
        self.chk_audio = QCheckBox("Audio bidireccional")
        self.chk_audio.setIcon(icon("audio"))
        self.chk_download = QCheckBox("Descargar grabaciones")
        self.chk_download.setIcon(icon("download"))
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
        """Devuelve (user_id, flags) con las capacidades marcadas (can_view,
        can_control_ptz/leds/audio, can_download_recordings). Llamado por
        `PermissionsView._grant` al aceptar."""
        return self.cmb_user.currentData(), {
            "can_view": self.chk_view.isChecked(),
            "can_control_ptz": self.chk_ptz.isChecked(),
            "can_control_leds": self.chk_leds.isChecked(),
            "can_control_audio": self.chk_audio.isChecked(),
            "can_download_recordings": self.chk_download.isChecked(),
        }


class PermissionsView(QWidget):
    """Vista de gestión de permisos por cámara (solo admin; autorización #2).

    RESPONSABILIDAD / ROL
        Página del content_stack que edita las filas UserCameraPermission:
        selecciona cámara → ve/otorga/revoca accesos de usuarios sobre ella.

    QUIÉN LA INSTANCIA
        main_window (índice VIEW_PERMISSIONS=8). Recibe el rol vía
        `set_current_user` (deshabilita la vista si no es admin).

    SEÑALES QT
        No define señales propias. Reacciona a la selección de la lista de
        cámaras y de la tabla; I/O por callbacks async del api_client.

    ESTADO
        _cameras / _users: cachés de la API. _current_camera_id: cámara
        seleccionada. _is_admin: gating de las acciones.
    """

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
        title = QLabel("Permisos por cámara")
        title.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 22px; font-weight: bold;"
        )
        header.addWidget(title)
        header.addStretch()

        self.btn_refresh = QPushButton("  Refrescar")
        self.btn_refresh.setIcon(icon("refresh"))
        self.btn_refresh.clicked.connect(self.refresh)
        header.addWidget(self.btn_refresh)
        layout.addLayout(header)

        self.lbl_warn = QLabel(
            "Esta sección solo está disponible para administradores."
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
        self.btn_grant = QPushButton("  Otorgar acceso")
        self.btn_grant.setIcon(icon("grant"))
        self.btn_grant.clicked.connect(self._grant)
        self.btn_grant.setEnabled(False)
        r_header.addWidget(self.btn_grant)
        self.btn_revoke = QPushButton("  Revocar")
        self.btn_revoke.setIcon(icon("delete"))
        self.btn_revoke.clicked.connect(self._revoke)
        self.btn_revoke.setEnabled(False)
        r_header.addWidget(self.btn_revoke)
        r_layout.addLayout(r_header)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Usuario", "Ver", "PTZ", "LEDs", "Audio", "Descargar"
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
        """Aplica el rol: si es admin, habilita y refresca; si no, muestra el
        aviso y bloquea. Llamado por MainWindow tras el login."""
        self._is_admin = (role == "admin")
        if not self._is_admin:
            self.lbl_warn.setVisible(True)
            self.btn_refresh.setEnabled(False)
        else:
            self.lbl_warn.setVisible(False)
            self.btn_refresh.setEnabled(True)
            self.refresh()

    def refresh(self):
        """Recarga cámaras (panel izq.) y usuarios (para otorgar) en paralelo.
        Llamado por: `set_current_user` (si admin) y el botón «Refrescar»."""
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
                        r, col, QTableWidgetItem("Sí" if p.get(key) else "—")
                    )
        api_client.get(f"permissions/camera/{cam_id}", on_perms)

    def _update_buttons(self):
        rows = self.table.selectionModel().selectedRows()
        self.btn_revoke.setEnabled(bool(rows) and self._is_admin)

    def _grant(self):
        """Otorga/actualiza permisos de un usuario sobre la cámara actual.

        Abre GrantPermissionDialog y, al aceptar, hace POST a
        permissions/camera/{cam}/user/{user} con los flags. Recarga la tabla al
        terminar. Llamado por: botón «Otorgar acceso»."""
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
        """Revoca TODOS los permisos del usuario seleccionado sobre la cámara.

        Pide confirmación y hace DELETE permissions/camera/{cam}/user/{user};
        recarga la tabla. Llamado por: botón «Revocar»."""
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
