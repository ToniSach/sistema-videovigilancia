"""
================================================================================
MÓDULO: ui.views.telegram_devices_view — Dispositivos Telegram vinculados (Pipeline #13)
================================================================================

PROPÓSITO
    Vista dedicada (solo administradores) que lista, en una tabla, TODOS los
    dispositivos vinculados del sistema —chats de Telegram y móviles— por
    usuario: rol, @alias de Telegram, móviles, resumen de preferencias y nº de
    reglas activas. Es la versión "pantalla completa" del resumen que también
    aparece embebido en notifications_view.

RESPONSABILIDAD
    - Pedir el panorama de dispositivos al backend y poblar la tabla.
    - Mostrar solo usuarios con ALGÚN dispositivo vinculado; si no hay ninguno
      (o no hay permisos), enseñar un mensaje vacío claro.
    - Refrescarse automáticamente al mostrarse (showEvent) y bajo demanda.

DEPENDENCIAS (endpoints consumidos)
    - GET telegram/admin/overview ... panorama por usuario (requiere rol admin;
      un 403 se traduce en el mensaje "¿tienes permisos de administrador?").

COMPONENTES RELACIONADOS
    main_window la instancia (índice VIEW_TELEGRAM_DEVICES=11, solo admin).
    Reutiliza `_TABLE_STYLE` de users_view e icons.icon. Comparte fuente de
    datos con la tabla admin de notifications_view.

PUNTO DE ENTRADA (en la app)
    Sidebar «Dispositivos Telegram» (solo admin) →
    MainWindow._switch_view(VIEW_TELEGRAM_DEVICES).

PIPELINE(S)
    #13 Notificaciones (consulta administrativa): muestra el estado de
    vinculación que habilita el envío de alertas por Telegram/móvil. La
    vinculación en sí se hace desde la app móvil / notifications_view.
================================================================================
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
    """Tabla de dispositivos Telegram/móviles vinculados (vista admin; Pipeline #13).

    RESPONSABILIDAD / ROL
        Página del content_stack que consulta y muestra (solo lectura) el
        panorama de dispositivos por usuario.

    QUIÉN LA INSTANCIA
        main_window (índice VIEW_TELEGRAM_DEVICES=11, solo admin).

    SEÑALES QT
        No define señales propias. Se autorrefresca en `showEvent`; I/O por
        callback async del api_client.
    """

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
        # Columnas cortas se ajustan al contenido; las largas (Telegram, Móviles,
        # Preferencias) se estiran Y permiten varias líneas (word-wrap), de modo
        # que el texto completo se ve sin recortar en "…". Las filas crecen de
        # alto según el contenido (resizeRowsToContents tras poblar).
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Usuario
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Rol
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)           # Telegram
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)           # Móviles
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)           # Preferencias
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # Reglas
        self.table.setWordWrap(True)
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
        """Recarga el panorama de dispositivos desde el backend.

        Llama a GET telegram/admin/overview y delega en `_populate`. Si falla
        (403 sin permisos o red), muestra el mensaje vacío. Llamado por:
        `showEvent` y el botón de recargar."""
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

            def _cell(text: str) -> QTableWidgetItem:
                # Tooltip con el texto completo, por si el usuario prefiere verlo
                # de un vistazo al pasar el ratón (además del word-wrap en celda).
                it = QTableWidgetItem(text)
                if text and text not in ("—", ""):
                    it.setToolTip(text)
                return it

            self.table.setItem(r, 0, _cell(u.get("username", "")))
            self.table.setItem(r, 1, _cell(u.get("role", "user")))

            # Telegram: lista de @alias (o "No vinculado").
            if chats:
                names = ", ".join("@" + (c.get("username") or "?") for c in chats)
                tg_item = _cell(names)
                tg_item.setIcon(icon("ok", "#22c55e"))
            else:
                tg_item = QTableWidgetItem("No vinculado")
                tg_item.setForeground(Qt.gray)
            self.table.setItem(r, 2, tg_item)

            # Móviles vinculados.
            mob_label = ", ".join(mobiles) if mobiles else "—"
            self.table.setItem(r, 3, _cell(mob_label))

            # Resumen de preferencias (tipos de evento que recibe).
            self.table.setItem(r, 4, _cell(u.get("preferences_summary", "—")))

            # Nº de reglas activas.
            self.table.setItem(r, 5, _cell(str(u.get("active_preferences", 0))))

        # Ajustar el alto de cada fila al contenido (las celdas con varias líneas
        # por word-wrap necesitan más alto para mostrarse completas).
        self.table.resizeRowsToContents()

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
        """Al navegar a esta vista, recarga la tabla (datos siempre frescos)."""
        super().showEvent(event)
        self.refresh()
