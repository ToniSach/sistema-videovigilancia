"""
Vista dedicada: Dispositivos conectados a Telegram.

Muestra, en una pestaña propia del menú lateral (solo administradores), la
tabla de TODOS los chats/dispositivos de Telegram vinculados al sistema: a qué
usuario pertenecen, su @usuario de Telegram, desde cuándo están vinculados y
cuántas reglas de notificación activas tiene ese usuario.

Fuente de datos: GET /api/v1/telegram/admin/overview (requiere rol admin).
Cada chat vinculado es una fila (un usuario con 2 chats ocupa 2 filas).
"""
import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PySide6.QtCore import Qt

from desktop_app.src.config import config
from desktop_app.src.services.api_client import api_client
from desktop_app.src.ui.icons import icon

logger = logging.getLogger(__name__)


class TelegramDevicesView(QWidget):
    """Tabla de dispositivos Telegram vinculados (vista admin)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Cabecera
        header = QHBoxLayout()
        title = QLabel("Dispositivos Telegram")
        title.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 22px; font-weight: bold;"
        )
        header.addWidget(title)
        header.addStretch()

        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 12px;"
        )
        header.addWidget(self.lbl_count)

        self.btn_refresh = QPushButton()
        self.btn_refresh.setIcon(icon("refresh"))
        self.btn_refresh.setToolTip("Recargar")
        self.btn_refresh.setMaximumWidth(36)
        self.btn_refresh.clicked.connect(self.refresh)
        header.addWidget(self.btn_refresh)
        layout.addLayout(header)

        sub = QLabel(
            "<i>Dispositivos vinculados al sistema (Telegram y móviles) y las "
            "preferencias de notificación de cada usuario. Cada usuario vincula "
            "sus dispositivos desde la app móvil; aquí los administradores los "
            "consultan.</i>"
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(sub)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Usuario", "Rol", "Telegram", "Móviles", "Preferencias", "Reglas",
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        try:
            from desktop_app.src.ui.views.users_view import _TABLE_STYLE
            self.table.setStyleSheet(_TABLE_STYLE)
        except Exception:
            pass
        layout.addWidget(self.table, 1)

        # Mensaje cuando no hay dispositivos / no hay permisos.
        self.lbl_empty = QLabel("")
        self.lbl_empty.setAlignment(Qt.AlignCenter)
        self.lbl_empty.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 13px; padding: 20px;"
        )
        self.lbl_empty.setVisible(False)
        layout.addWidget(self.lbl_empty)

    # ------------------------------------------------------------------
    # Datos
    # ------------------------------------------------------------------
    def refresh(self):
        def on_overview(response):
            if not response.success:
                # 403 (no admin) o error de red.
                self.table.setRowCount(0)
                self.lbl_empty.setText(
                    "No se pudo cargar (¿tienes permisos de administrador?)."
                )
                self.lbl_empty.setVisible(True)
                self.lbl_count.setText("")
                return
            self._populate(response.data or [])
        api_client.get("telegram/admin/overview", on_overview)

    def _populate(self, users: list):
        self.table.setRowCount(0)
        total_devices = 0
        rows_shown = 0

        for u in users:
            chats = u.get("telegram") or []
            mobiles = u.get("mobile_devices") or []
            # Solo mostramos usuarios con ALGÚN dispositivo vinculado.
            if not chats and not mobiles:
                continue

            total_devices += len(chats) + len(mobiles)
            r = self.table.rowCount()
            self.table.insertRow(r)
            rows_shown += 1

            self.table.setItem(r, 0, QTableWidgetItem(u.get("username", "")))
            self.table.setItem(r, 1, QTableWidgetItem(u.get("role", "user")))

            # Telegram: lista de @alias (o "No vinculado").
            if chats:
                names = ", ".join("@" + (c.get("username") or "?") for c in chats)
                tg_item = QTableWidgetItem(names)
                tg_item.setIcon(icon("ok", "#22c55e"))
            else:
                tg_item = QTableWidgetItem("No vinculado")
                tg_item.setForeground(Qt.gray)
            self.table.setItem(r, 2, tg_item)

            # Móviles vinculados.
            mob_label = ", ".join(mobiles) if mobiles else "—"
            self.table.setItem(r, 3, QTableWidgetItem(mob_label))

            # Resumen de preferencias (tipos de evento que recibe).
            self.table.setItem(r, 4, QTableWidgetItem(u.get("preferences_summary", "—")))

            # Nº de reglas activas.
            self.table.setItem(r, 5, QTableWidgetItem(str(u.get("active_preferences", 0))))

        if rows_shown == 0:
            self.lbl_empty.setText(
                "Todavía no hay ningún dispositivo vinculado.\n"
                "Los usuarios vinculan su Telegram y su móvil desde la app móvil."
            )
            self.lbl_empty.setVisible(True)
            self.table.setVisible(False)
        else:
            self.lbl_empty.setVisible(False)
            self.table.setVisible(True)

        self.lbl_count.setText(f"{total_devices} dispositivo(s) vinculado(s)")

    # ------------------------------------------------------------------
    # Refresco automático al mostrar la vista
    # ------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
