"""
Vista de preferencias de notificaciones por usuario.

Cada usuario configura qué eventos quiere recibir, de qué cámaras, por qué
canales (Telegram/push/web), y en qué horario / días.
"""
import logging
from typing import Optional, List

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialog,
    QComboBox, QCheckBox, QFormLayout, QMessageBox, QTimeEdit,
)
from PySide6.QtCore import Qt, QTime

from desktop_app.src.config import config
from desktop_app.src.services.api_client import api_client
from desktop_app.src.ui.components.glass_card import GlassCard

logger = logging.getLogger(__name__)


EVENT_TYPES_OPTIONS = [
    ("person", "🚨 Persona"),
    ("vehicle", "🚗 Vehículo"),
    ("motion", "📹 Movimiento"),
    ("camera_offline", "⚠ Cámara offline"),
    ("tampering", "🔴 Sabotaje"),
]

CHANNEL_OPTIONS = [
    ("telegram", "💬 Telegram"),
    ("push", "📱 Push (móvil)"),
    ("email", "✉ Email"),
]

DAY_NAMES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


class PreferenceDialog(QDialog):
    """Crea o edita una preferencia."""

    def __init__(self, cameras: List[dict],
                 preference: Optional[dict] = None, parent=None):
        super().__init__(parent)
        self.cameras = cameras
        self.pref = preference
        self.is_edit = preference is not None
        self.setWindowTitle(
            "Editar preferencia" if self.is_edit else "Nueva preferencia"
        )
        self.setMinimumWidth(420)
        self.setModal(True)
        self._setup_ui()
        if self.is_edit:
            self._load_preference()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Tipo de evento
        self.cmb_event = QComboBox()
        for key, label in EVENT_TYPES_OPTIONS:
            self.cmb_event.addItem(label, key)
        form.addRow("Evento:", self.cmb_event)

        # Cámara (None = todas)
        self.cmb_camera = QComboBox()
        self.cmb_camera.addItem("— Todas las cámaras —", None)
        for c in self.cameras:
            self.cmb_camera.addItem(f"#{c['id']} {c.get('name', '')}", c["id"])
        form.addRow("Cámara:", self.cmb_camera)

        # Activa
        self.chk_enabled = QCheckBox("Activa")
        self.chk_enabled.setChecked(True)
        form.addRow(self.chk_enabled)

        layout.addLayout(form)

        # Canales
        layout.addWidget(QLabel("<b>Canales:</b>"))
        canales = QHBoxLayout()
        self.chk_channels = {}
        for key, label in CHANNEL_OPTIONS:
            cb = QCheckBox(label)
            self.chk_channels[key] = cb
            canales.addWidget(cb)
        self.chk_channels["telegram"].setChecked(True)
        canales.addStretch()
        layout.addLayout(canales)

        # Horario
        layout.addWidget(QLabel("<b>Horario (opcional):</b>"))
        horario = QHBoxLayout()
        self.chk_schedule = QCheckBox("Limitar a horario")
        self.chk_schedule.toggled.connect(self._toggle_schedule)
        horario.addWidget(self.chk_schedule)
        horario.addWidget(QLabel("De"))
        self.time_start = QTimeEdit()
        self.time_start.setTime(QTime(8, 0))
        self.time_start.setEnabled(False)
        horario.addWidget(self.time_start)
        horario.addWidget(QLabel("a"))
        self.time_end = QTimeEdit()
        self.time_end.setTime(QTime(22, 0))
        self.time_end.setEnabled(False)
        horario.addWidget(self.time_end)
        horario.addStretch()
        layout.addLayout(horario)

        # Días
        layout.addWidget(QLabel("<b>Días de la semana:</b>"))
        dias = QHBoxLayout()
        self.chk_days = []
        for i, dn in enumerate(DAY_NAMES):
            cb = QCheckBox(dn)
            cb.setChecked(True)
            self.chk_days.append(cb)
            dias.addWidget(cb)
        dias.addStretch()
        layout.addLayout(dias)

        # Botones
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        save = QPushButton("Guardar")
        save.clicked.connect(self.accept)
        save.setDefault(True)
        btns.addWidget(save)
        layout.addLayout(btns)

        self.setStyleSheet(f"""
            QDialog {{ background-color: {config.THEME_PRIMARY}; }}
            QLabel, QCheckBox {{ color: {config.THEME_TEXT}; }}
            QComboBox, QTimeEdit {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 4px;
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

    def _toggle_schedule(self, on: bool):
        self.time_start.setEnabled(on)
        self.time_end.setEnabled(on)

    def _load_preference(self):
        p = self.pref
        # Evento
        idx = self.cmb_event.findData(p.get("event_type"))
        if idx >= 0:
            self.cmb_event.setCurrentIndex(idx)
        # Cámara
        idx = self.cmb_camera.findData(p.get("camera_id"))
        if idx >= 0:
            self.cmb_camera.setCurrentIndex(idx)
        self.chk_enabled.setChecked(p.get("enabled", True))
        # Canales
        chans = set(p.get("channels", []))
        for key, cb in self.chk_channels.items():
            cb.setChecked(key in chans)
        # Horario
        sched = p.get("schedule", {}) or {}
        if sched.get("start") and sched.get("end"):
            self.chk_schedule.setChecked(True)
            try:
                h, m = sched["start"].split(":")[:2]
                self.time_start.setTime(QTime(int(h), int(m)))
                h, m = sched["end"].split(":")[:2]
                self.time_end.setTime(QTime(int(h), int(m)))
            except Exception:
                pass
        # Días
        days = set(p.get("days", []))
        if days:
            for i, cb in enumerate(self.chk_days):
                cb.setChecked(i in days)

    def get_data(self) -> dict:
        channels = [k for k, cb in self.chk_channels.items() if cb.isChecked()]
        days = [i for i, cb in enumerate(self.chk_days) if cb.isChecked()]
        data = {
            "event_type": self.cmb_event.currentData(),
            "camera_id": self.cmb_camera.currentData(),
            "enabled": self.chk_enabled.isChecked(),
            "channels": channels,
            "days_of_week": days,
        }
        if self.chk_schedule.isChecked():
            data["schedule_start"] = self.time_start.time().toString("HH:mm")
            data["schedule_end"] = self.time_end.time().toString("HH:mm")
        return data


class NotificationPreferencesView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._prefs: List[dict] = []
        self._cameras: List[dict] = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("🔔 Notificaciones")
        title.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 22px; font-weight: bold;"
        )
        header.addWidget(title)
        header.addStretch()

        self.btn_help = QPushButton("❔")
        self.btn_help.setToolTip("Ver ayuda sobre notificaciones")
        self.btn_help.setMaximumWidth(36)
        self.btn_help.clicked.connect(self._show_help)
        header.addWidget(self.btn_help)

        self.btn_refresh = QPushButton("🔄")
        self.btn_refresh.setToolTip("Recargar")
        self.btn_refresh.setMaximumWidth(36)
        self.btn_refresh.clicked.connect(self.refresh)
        header.addWidget(self.btn_refresh)

        self.btn_add = QPushButton("➕ Nueva preferencia")
        self.btn_add.clicked.connect(self._add)
        header.addWidget(self.btn_add)
        layout.addLayout(header)

        # ============ SECCIÓN TELEGRAM ============
        self._setup_telegram_section(layout)

        # ============ SECCIÓN PREFERENCIAS ============
        prefs_header = QHBoxLayout()
        prefs_title = QLabel("📋  Mis preferencias")
        prefs_title.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 15px; font-weight: bold;"
        )
        prefs_header.addWidget(prefs_title)
        prefs_header.addStretch()
        layout.addLayout(prefs_header)

        help_lbl = QLabel(
            "<i>Define qué eventos quieres recibir, de qué cámaras, por "
            "qué canales y en qué horario. Si no creas ninguna preferencia, "
            "no recibirás notificaciones.</i>"
        )
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(help_lbl)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Evento", "Cámara", "Activa", "Canales", "Horario", "Días"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.cellDoubleClicked.connect(lambda *a: self._edit())
        from desktop_app.src.ui.views.users_view import _TABLE_STYLE
        self.table.setStyleSheet(_TABLE_STYLE)
        layout.addWidget(self.table, 1)

        # Acciones
        actions = QHBoxLayout()
        self.btn_edit = QPushButton("✏ Editar")
        self.btn_edit.clicked.connect(self._edit)
        self.btn_edit.setEnabled(False)
        actions.addWidget(self.btn_edit)
        self.btn_delete = QPushButton("🗑 Eliminar")
        self.btn_delete.clicked.connect(self._delete)
        self.btn_delete.setEnabled(False)
        actions.addWidget(self.btn_delete)
        actions.addStretch()
        layout.addLayout(actions)

    def set_current_user(self, _user_id: int, _role: str):
        self.refresh()

    def refresh(self):
        def on_cams(response):
            if response.success:
                self._cameras = response.data or []
        api_client.get("cameras/", on_cams)

        def on_prefs(response):
            if not response.success:
                return
            self._prefs = response.data or []
            self._populate()
        api_client.get("notifications/preferences", on_prefs)

        # Recargar también la sección Telegram
        self._refresh_telegram_chats()

    def _populate(self):
        self.table.setRowCount(0)
        ev_labels = {k: lbl for k, lbl in EVENT_TYPES_OPTIONS}
        ch_labels = {k: lbl for k, lbl in CHANNEL_OPTIONS}
        for p in self._prefs:
            r = self.table.rowCount()
            self.table.insertRow(r)

            ev_label = ev_labels.get(p.get("event_type"), p.get("event_type", ""))
            it_ev = QTableWidgetItem(ev_label)
            it_ev.setData(Qt.UserRole, p.get("id"))
            self.table.setItem(r, 0, it_ev)

            cam_id = p.get("camera_id")
            if cam_id:
                cam = next((c for c in self._cameras if c.get("id") == cam_id), None)
                cam_label = f"#{cam_id} {cam.get('name', '') if cam else ''}"
            else:
                cam_label = "Todas"
            self.table.setItem(r, 1, QTableWidgetItem(cam_label))

            self.table.setItem(
                r, 2, QTableWidgetItem("✓" if p.get("enabled") else "✗")
            )

            chans = [ch_labels.get(c, c) for c in (p.get("channels") or [])]
            self.table.setItem(r, 3, QTableWidgetItem(", ".join(chans) or "—"))

            sched = p.get("schedule", {}) or {}
            if sched.get("start") and sched.get("end"):
                s = sched["start"][:5] if isinstance(sched["start"], str) else ""
                e = sched["end"][:5] if isinstance(sched["end"], str) else ""
                horario = f"{s} → {e}"
            else:
                horario = "Todo el día"
            self.table.setItem(r, 4, QTableWidgetItem(horario))

            days = p.get("days", [])
            if days and len(days) < 7:
                dias_str = ", ".join(DAY_NAMES[d] for d in sorted(days))
            else:
                dias_str = "Todos"
            self.table.setItem(r, 5, QTableWidgetItem(dias_str))

    def _update_buttons(self):
        sel = bool(self.table.selectionModel().selectedRows())
        self.btn_edit.setEnabled(sel)
        self.btn_delete.setEnabled(sel)

    def _selected_pref(self) -> Optional[dict]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        pid = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        return next((p for p in self._prefs if p.get("id") == pid), None)

    def _add(self):
        dlg = PreferenceDialog(self._cameras, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        def on_create(response):
            if response.success:
                self.refresh()
            else:
                QMessageBox.critical(self, "Error", response.error or "Error")
        api_client.post("notifications/preferences", on_create, data=dlg.get_data())

    def _edit(self):
        p = self._selected_pref()
        if not p:
            return
        dlg = PreferenceDialog(self._cameras, preference=p, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        def on_update(response):
            if response.success:
                self.refresh()
            else:
                QMessageBox.critical(self, "Error", response.error or "Error")
        api_client.put(
            f"notifications/preferences/{p['id']}",
            on_update, data=dlg.get_data(),
        )

    def _delete(self):
        p = self._selected_pref()
        if not p:
            return
        ans = QMessageBox.question(
            self, "Confirmar", "¿Eliminar esta preferencia?"
        )
        if ans != QMessageBox.Yes:
            return

        def on_del(response):
            if response.success:
                self.refresh()
            else:
                QMessageBox.critical(self, "Error", response.error or "Error")
        api_client.delete(f"notifications/preferences/{p['id']}", on_del)

    # ==================================================================
    # SECCIÓN TELEGRAM
    # ==================================================================
    def _setup_telegram_section(self, parent_layout: QVBoxLayout):
        """Crea la tarjeta con chats vinculados y botón de vinculación."""
        from PySide6.QtWidgets import QFrame, QListWidget, QListWidgetItem

        card = QFrame()
        card.setStyleSheet(
            "QFrame { background-color: #1e293b; border-radius: 10px; padding: 12px; }"
        )
        v = QVBoxLayout(card)
        v.setSpacing(8)

        # Cabecera de la tarjeta
        h = QHBoxLayout()
        lbl = QLabel("💬  Telegram")
        lbl.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 15px; font-weight: bold;"
        )
        h.addWidget(lbl)

        self.lbl_telegram_status = QLabel("…")
        self.lbl_telegram_status.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 11px;"
        )
        h.addWidget(self.lbl_telegram_status)
        h.addStretch()

        self.btn_telegram_link = QPushButton("📲  Vincular nuevo chat")
        self.btn_telegram_link.setStyleSheet("""
            QPushButton {
                background-color: #38bdf8; color: #0f172a;
                border: none; border-radius: 6px;
                padding: 7px 14px; font-weight: bold;
            }
            QPushButton:hover { background-color: #7dd3fc; }
        """)
        self.btn_telegram_link.clicked.connect(self._open_telegram_dialog)
        h.addWidget(self.btn_telegram_link)

        v.addLayout(h)

        # Lista de chats vinculados
        self.list_telegram_chats = QListWidget()
        self.list_telegram_chats.setStyleSheet("""
            QListWidget {
                background-color: #0f172a; color: #f1f5f9;
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 6px; padding: 4px;
            }
            QListWidget::item { padding: 6px; border-radius: 4px; }
            QListWidget::item:hover { background-color: #334155; }
        """)
        self.list_telegram_chats.setMaximumHeight(120)
        v.addWidget(self.list_telegram_chats)

        # Botones de la lista
        h2 = QHBoxLayout()
        self.btn_telegram_unlink = QPushButton("🗑  Desvincular seleccionado")
        self.btn_telegram_unlink.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #f87171;
                border: 1px solid rgba(248,113,113,0.4);
                border-radius: 6px; padding: 6px 12px;
            }
            QPushButton:hover:!disabled { background-color: #2a1717; }
            QPushButton:disabled { color: #475569; border-color: rgba(71,85,105,0.3); }
        """)
        self.btn_telegram_unlink.setEnabled(False)
        self.btn_telegram_unlink.clicked.connect(self._unlink_telegram_chat)
        h2.addWidget(self.btn_telegram_unlink)
        h2.addStretch()
        v.addLayout(h2)

        self.list_telegram_chats.itemSelectionChanged.connect(
            lambda: self.btn_telegram_unlink.setEnabled(
                bool(self.list_telegram_chats.currentItem())
            )
        )

        parent_layout.addWidget(card)

    def _refresh_telegram_chats(self):
        """Carga chats vinculados y estado del bot."""
        def on_bot_info(response):
            if not response.success:
                self.lbl_telegram_status.setText("⚠ Bot no disponible")
                return
            data = response.data or {}
            if not data.get("configured"):
                self.lbl_telegram_status.setText(
                    "⚠ Bot no configurado en el servidor"
                )
                self.btn_telegram_link.setEnabled(False)
            elif data.get("username"):
                self.lbl_telegram_status.setText(
                    f"✓ Conectado al bot @{data['username']}"
                )
                self.btn_telegram_link.setEnabled(True)
            else:
                self.lbl_telegram_status.setText("⏳ Conectando con el bot…")
        api_client.get("telegram/bot-info", on_bot_info)

        def on_chats(response):
            self.list_telegram_chats.clear()
            if not response.success:
                return
            chats = response.data or []
            if not chats:
                from PySide6.QtWidgets import QListWidgetItem
                item = QListWidgetItem(
                    "No tienes chats vinculados. "
                    "Pulsa «Vincular nuevo chat» para añadir uno."
                )
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
                self.list_telegram_chats.addItem(item)
                return
            from PySide6.QtWidgets import QListWidgetItem
            for c in chats:
                name = c.get("telegram_username") or "(sin usuario)"
                linked = c.get("linked_at", "")[:10]
                item = QListWidgetItem(f"✓  @{name}    ·    Vinculado: {linked}")
                item.setData(Qt.UserRole, c.get("id"))
                self.list_telegram_chats.addItem(item)
        api_client.get("telegram/chats", on_chats)

    def _open_telegram_dialog(self):
        from desktop_app.src.ui.dialogs.telegram_link_dialog import TelegramLinkDialog
        dlg = TelegramLinkDialog(self)
        dlg.exec()
        # Tras cerrar, recargamos por si vinculó
        self._refresh_telegram_chats()

    def _unlink_telegram_chat(self):
        item = self.list_telegram_chats.currentItem()
        if not item:
            return
        chat_id = item.data(Qt.UserRole)
        if chat_id is None:
            return
        ans = QMessageBox.question(
            self, "Desvincular Telegram",
            "¿Seguro que quieres desvincular este chat?\n\n"
            "Dejarás de recibir notificaciones en él."
        )
        if ans != QMessageBox.Yes:
            return

        def on_unlink(response):
            if response.success:
                self._refresh_telegram_chats()
            else:
                QMessageBox.critical(
                    self, "Error",
                    response.error or "No se pudo desvincular"
                )
        api_client.delete(f"telegram/chats/{chat_id}", on_unlink)

    # ==================================================================
    # AYUDA CONTEXTUAL
    # ==================================================================
    def _show_help(self):
        from desktop_app.src.ui.dialogs.info_dialog import InfoDialog
        InfoDialog(
            title="Ayuda — Notificaciones",
            sections=[
                ("📲  ¿Cómo funcionan las notificaciones?",
                 "Cuando una cámara detecta un evento (persona, vehículo, "
                 "movimiento) el sistema te avisa por los canales que hayas "
                 "configurado. Necesitas: 1) vincular al menos un canal (Telegram "
                 "es lo más común) y 2) crear preferencias que digan qué eventos "
                 "y de qué cámaras quieres recibir."),
                ("💬  Telegram",
                 "Telegram requiere que TÚ envíes el primer mensaje al bot "
                 "(es una regla de Telegram). Pulsa «Vincular nuevo chat» y "
                 "sigue los pasos: el sistema te dará un código, lo envías al "
                 "bot, y automáticamente quedará vinculado. Puedes tener varios "
                 "chats (PC, móvil, grupo familiar...)."),
                ("📋  Preferencias",
                 "Cada preferencia es una regla: «qué evento + qué cámara + "
                 "qué canales + qué horario + qué días». Puedes tener varias. "
                 "Ejemplo: «Persona en cámara puerta, por Telegram, 24/7» y "
                 "«Movimiento en cámara jardín, por push, sólo 22:00-07:00»."),
                ("⏰  Horario y días",
                 "Si no marcas horario, recibes 24 horas. Si marcas días, sólo "
                 "esos días se aplica. Útil para no recibir alertas mientras "
                 "trabajas en casa (las cámaras siguen grabando, sólo silencias "
                 "el aviso)."),
            ],
            parent=self,
        )
