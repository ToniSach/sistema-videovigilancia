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
from desktop_app.src.ui.icons import icon

logger = logging.getLogger(__name__)


EVENT_TYPES_OPTIONS = [
    ("person", "Persona"),
    ("vehicle", "Vehículo"),
    ("motion", "Movimiento"),
    ("camera_offline", "Cámara offline"),
    ("tampering", "Sabotaje"),
]
EVENT_ICONS = {
    "person": "person", "vehicle": "vehicle", "motion": "motion",
    "camera_offline": "offline", "tampering": "tamper",
}

# En el ESCRITORIO las notificaciones solo se VEN aquí mismo (canal "app").
# El envío por Telegram o al móvil se configura desde la app MÓVIL.
CHANNEL_OPTIONS = [
    ("app", "Ver en la app"),
]
CHANNEL_ICONS = {"app": "notifications"}

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
            self.cmb_event.addItem(icon(EVENT_ICONS.get(key, "events")), label, key)
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
            cb.setIcon(icon(CHANNEL_ICONS.get(key, "")))
            self.chk_channels[key] = cb
            canales.addWidget(cb)
        # Por defecto, marcar el primer canal disponible ("app" en escritorio).
        first_channel = CHANNEL_OPTIONS[0][0]
        self.chk_channels[first_channel].setChecked(True)
        canales.addStretch()
        layout.addLayout(canales)

        nota_canal = QLabel(
            "ℹ️ En el escritorio las alertas se ven aquí mismo. Para recibirlas "
            "en <b>Telegram</b> o en el <b>móvil</b>, configúralo desde la app móvil."
        )
        nota_canal.setWordWrap(True)
        nota_canal.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(nota_canal)

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
        self._role: str = ""
        self._ai_active_cams: List[int] = []
        self._dialog_open = False  # guard anti-reentrada (doble clic)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Notificaciones")
        title.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 22px; font-weight: bold;"
        )
        header.addWidget(title)
        header.addStretch()

        self.btn_help = QPushButton()
        self.btn_help.setIcon(icon("help"))
        self.btn_help.setToolTip("Ver ayuda sobre notificaciones")
        self.btn_help.setMaximumWidth(36)
        self.btn_help.clicked.connect(self._show_help)
        header.addWidget(self.btn_help)

        self.btn_refresh = QPushButton()
        self.btn_refresh.setIcon(icon("refresh"))
        self.btn_refresh.setToolTip("Recargar")
        self.btn_refresh.setMaximumWidth(36)
        self.btn_refresh.clicked.connect(self.refresh)
        header.addWidget(self.btn_refresh)

        self.btn_add = QPushButton("  Nueva preferencia")
        self.btn_add.setIcon(icon("add"))
        self.btn_add.clicked.connect(self._add)
        header.addWidget(self.btn_add)
        layout.addLayout(header)

        # Aviso de IA: las notificaciones SOLO existen para la cámara con IA
        # activa. Si no hay ninguna activa, no se puede personalizar.
        self.lbl_ai_banner = QLabel()
        self.lbl_ai_banner.setWordWrap(True)
        self.lbl_ai_banner.setVisible(False)
        self.lbl_ai_banner.setStyleSheet(
            "background-color: #422006; color: #fcd34d; border: 1px solid #a16207;"
            "border-radius: 8px; padding: 10px; font-size: 12px;"
        )
        layout.addWidget(self.lbl_ai_banner)

        # ============ SECCIÓN TELEGRAM ============
        self._setup_telegram_section(layout)

        # ============ TABLA ADMIN: Usuarios ↔ Telegram ↔ preferencias ========
        self._setup_admin_overview(layout)

        # ============ SECCIÓN PREFERENCIAS ============
        prefs_header = QHBoxLayout()
        prefs_title = QLabel("Mis preferencias")
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

    def set_current_user(self, _user_id: int, _role: str = ""):
        self._role = (_role or "").lower()
        is_admin = self._role == "admin"
        # El botón de configurar el token del bot solo tiene sentido para admin.
        if hasattr(self, "btn_telegram_config"):
            self.btn_telegram_config.setVisible(is_admin)
        # La tabla "Usuarios y Telegram" es solo para admin.
        if hasattr(self, "admin_card"):
            self.admin_card.setVisible(is_admin)
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

        # Gateo por IA activa
        self._check_ai_and_gate()

        # Tabla admin (solo si es admin y la tarjeta existe)
        if self._role == "admin" and hasattr(self, "admin_table"):
            self._refresh_admin_overview()

    def _check_ai_and_gate(self):
        """
        Consulta si hay IA activa. Las notificaciones SOLO tienen sentido para
        la cámara con IA activa; si no hay ninguna, se bloquea la personalización
        y se muestra un aviso (regla de producto).
        """
        def on_status(response):
            active = []
            if response.success and response.data:
                active = response.data.get("active", []) or []
            has_ai = len(active) > 0
            self.btn_add.setEnabled(has_ai)
            self.table.setEnabled(has_ai)
            if has_ai:
                cams = ", ".join(f"#{a.get('camera_id')}" for a in active)
                self.lbl_ai_banner.setVisible(False)
                self.btn_add.setToolTip("")
                self._ai_active_cams = [a.get("camera_id") for a in active]
            else:
                self._ai_active_cams = []
                self.lbl_ai_banner.setText(
                    "⚠️ <b>La detección por IA no está activa.</b> Las notificaciones "
                    "se generan a partir de lo que detecta la IA, así que primero "
                    "actívala en una cámara (pestaña «En vivo» → panel de la cámara "
                    "→ Activar IA). Mientras tanto no puedes crear preferencias."
                )
                self.lbl_ai_banner.setVisible(True)
                self.btn_add.setToolTip("Activa la IA en una cámara para personalizar notificaciones")
        api_client.get("ai/status", on_status)

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
                r, 2, QTableWidgetItem("Sí" if p.get("enabled") else "No")
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
        if self._dialog_open:
            return
        if not self._ai_active_cams:
            QMessageBox.information(
                self, "IA no activa",
                "Activa la detección por IA en una cámara antes de crear "
                "preferencias de notificación.\n\nVe a «En vivo», abre el panel "
                "de la cámara y pulsa «Activar IA»."
            )
            return
        self._dialog_open = True
        try:
            dlg = PreferenceDialog(self._cameras, parent=self)
            accepted = dlg.exec() == QDialog.Accepted
        finally:
            self._dialog_open = False
        if not accepted:
            return

        def on_create(response):
            if response.success:
                self.refresh()
            else:
                QMessageBox.critical(self, "Error", response.error or "Error")
        api_client.post("notifications/preferences", on_create, data=dlg.get_data())

    def _edit(self):
        if self._dialog_open:
            return
        p = self._selected_pref()
        if not p:
            return
        self._dialog_open = True
        try:
            dlg = PreferenceDialog(self._cameras, preference=p, parent=self)
            accepted = dlg.exec() == QDialog.Accepted
        finally:
            self._dialog_open = False
        if not accepted:
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
    # TABLA ADMIN: Usuarios ↔ Telegram ↔ preferencias
    # ==================================================================
    def _setup_admin_overview(self, parent_layout: QVBoxLayout):
        """Tarjeta (solo admin) que resume cada usuario: Telegram + nº prefs."""
        from PySide6.QtWidgets import QFrame

        self.admin_card = QFrame()
        self.admin_card.setStyleSheet(
            "QFrame { background-color: #1e293b; border-radius: 10px; padding: 12px; }"
        )
        v = QVBoxLayout(self.admin_card)
        v.setSpacing(8)

        title = QLabel("Usuarios y Telegram")
        title.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 15px; font-weight: bold;"
        )
        v.addWidget(title)

        sub = QLabel(
            "<i>Resumen de cada usuario: si tiene Telegram vinculado y cuántas "
            "reglas de notificación activas. Cada usuario gestiona sus propias "
            "preferencias; aquí solo las consultas.</i>"
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 11px;")
        v.addWidget(sub)

        self.admin_table = QTableWidget(0, 4)
        self.admin_table.setHorizontalHeaderLabels(
            ["Usuario", "Rol", "Telegram", "Reglas activas"]
        )
        self.admin_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.admin_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.admin_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.admin_table.setMaximumHeight(200)
        try:
            from desktop_app.src.ui.views.users_view import _TABLE_STYLE
            self.admin_table.setStyleSheet(_TABLE_STYLE)
        except Exception:
            pass
        v.addWidget(self.admin_table)

        self.admin_card.setVisible(False)  # set_current_user lo muestra si admin
        parent_layout.addWidget(self.admin_card)

    def _refresh_admin_overview(self):
        def on_overview(response):
            if not response.success:
                return
            rows = response.data or []
            self.admin_table.setRowCount(0)
            for u in rows:
                r = self.admin_table.rowCount()
                self.admin_table.insertRow(r)
                self.admin_table.setItem(r, 0, QTableWidgetItem(u.get("username", "")))
                self.admin_table.setItem(r, 1, QTableWidgetItem(u.get("role", "user")))

                tg = u.get("telegram") or []
                if tg:
                    names = ", ".join("@" + (c.get("username") or "?") for c in tg)
                    tg_item = QTableWidgetItem(names)
                    tg_item.setIcon(icon("ok", "#22c55e"))
                else:
                    tg_item = QTableWidgetItem("No vinculado")
                    tg_item.setForeground(Qt.gray)
                self.admin_table.setItem(r, 2, tg_item)

                self.admin_table.setItem(
                    r, 3, QTableWidgetItem(str(u.get("active_preferences", 0)))
                )
        api_client.get("telegram/admin/overview", on_overview)

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
        lbl = QLabel("Telegram")
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

        # Botón solo-admin para definir el token del bot (de @BotFather).
        # Oculto por defecto; set_current_user lo muestra si el rol es admin.
        self.btn_telegram_config = QPushButton("Configurar bot")
        self.btn_telegram_config.setToolTip(
            "Definir el token del bot de Telegram (solo administradores)"
        )
        self.btn_telegram_config.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #f1f5f9;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 6px; padding: 7px 12px;
            }
            QPushButton:hover { background-color: #334155; }
        """)
        self.btn_telegram_config.setVisible(False)
        self.btn_telegram_config.clicked.connect(self._configure_telegram_bot)
        h.addWidget(self.btn_telegram_config)

        self.btn_telegram_test = QPushButton("  Enviar prueba")
        self.btn_telegram_test.setIcon(icon("telegram"))
        self.btn_telegram_test.setToolTip(
            "Envía un mensaje de prueba a tus chats vinculados"
        )
        self.btn_telegram_test.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #f1f5f9;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 6px; padding: 7px 12px;
            }
            QPushButton:hover { background-color: #334155; }
        """)
        self.btn_telegram_test.clicked.connect(self._test_my_telegram)
        h.addWidget(self.btn_telegram_test)

        self.btn_telegram_link = QPushButton("  Vincular nuevo chat")
        self.btn_telegram_link.setIcon(icon("link"))
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
        self.btn_telegram_unlink = QPushButton("  Desvincular seleccionado")
        self.btn_telegram_unlink.setIcon(icon("unlink"))
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
                self.lbl_telegram_status.setText("Bot no disponible")
                return
            data = response.data or {}
            if not data.get("configured"):
                self.lbl_telegram_status.setText(
                    "Bot no configurado en el servidor"
                )
                self.btn_telegram_link.setEnabled(False)
            elif data.get("username"):
                self.lbl_telegram_status.setText(
                    f"Conectado al bot @{data['username']}"
                )
                self.btn_telegram_link.setEnabled(True)
            else:
                self.lbl_telegram_status.setText("Conectando con el bot…")
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
                item = QListWidgetItem(f"@{name}    ·    Vinculado: {linked}")
                item.setIcon(icon("ok", "#22c55e"))
                item.setData(Qt.UserRole, c.get("id"))
                self.list_telegram_chats.addItem(item)
        api_client.get("telegram/chats", on_chats)

    def _test_my_telegram(self):
        """Envía un mensaje de prueba a los chats vinculados del usuario."""
        def on_test(response):
            if response.success:
                n = (response.data or {}).get("sent", 0)
                self._toast(f"Mensaje de prueba enviado a {n} chat(s).", "success")
            else:
                self._toast(response.error or "No se pudo enviar la prueba.", "error")
        api_client.post("telegram/test", on_test, data={})

    def _toast(self, message: str, level: str = "info"):
        try:
            from desktop_app.src.ui.components.toast import show_toast
            show_toast(self, message, level=level)
        except Exception:
            QMessageBox.information(self, "Telegram", message)

    def _open_telegram_dialog(self):
        from desktop_app.src.ui.dialogs.telegram_link_dialog import TelegramLinkDialog
        dlg = TelegramLinkDialog(self)
        dlg.exec()
        # Tras cerrar, recargamos por si vinculó
        self._refresh_telegram_chats()

    def _configure_telegram_bot(self):
        """
        Configura el token del bot (solo admin). Pide el token de @BotFather y
        lo envía a POST /telegram/configure, que lo guarda y RECARGA el poller
        en caliente (sin reiniciar el servidor).
        """
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        token, ok = QInputDialog.getText(
            self,
            "Configurar bot de Telegram",
            "Cómo obtener el token (una sola vez):\n"
            "  1) En Telegram abre @BotFather y envía  /newbot\n"
            "  2) Elige nombre y usuario del bot\n"
            "  3) Copia el token que te da (formato 123456789:AA...)\n\n"
            "Pega el token aquí:",
            QLineEdit.Normal,
            "",
        )
        if not ok or not token.strip():
            return

        def on_cfg(response):
            if not response.success:
                QMessageBox.critical(
                    self, "Error",
                    response.error or "No se pudo configurar el bot"
                )
                return
            data = response.data or {}
            if data.get("valid"):
                QMessageBox.information(
                    self, "Bot configurado",
                    f"Bot @{data.get('username')} conectado correctamente.\n\n"
                    "Ahora los usuarios ya pueden pulsar «Vincular nuevo chat».",
                )
            else:
                QMessageBox.warning(
                    self, "Token guardado, pero sin respuesta",
                    "Se guardó el token, pero Telegram no respondió. "
                    "Revisa que el token sea correcto y que el servidor tenga "
                    "acceso a internet.",
                )
            self._refresh_telegram_chats()

        api_client.post(
            "telegram/configure", on_cfg, data={"bot_token": token.strip()}
        )

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
                ("Primero: activa la IA",
                 "Las notificaciones se generan a partir de lo que detecta la "
                 "IA (personas, vehículos…). Si la IA no está activa en ninguna "
                 "cámara, no puedes crear preferencias. Actívala en «En vivo» → "
                 "panel de la cámara → «Activar IA». Las alertas serán de esa "
                 "cámara."),
                ("Escritorio vs. móvil",
                 "En el ESCRITORIO las alertas se ven aquí mismo, en la app "
                 "(canal «Ver en la app»). Para recibirlas en TELEGRAM o en el "
                 "MÓVIL, hazlo desde la app móvil: allí vinculas tu Telegram y "
                 "eliges esos canales. (El push por Firebase fue eliminado; el "
                 "móvil recibe por la red local, sin internet.)"),
                ("Telegram (lo configura el admin una vez)",
                 "El administrador define el bot en «Configurar bot». Luego, "
                 "desde la app móvil, cada usuario vincula su Telegram enviando "
                 "el código de 6 caracteres al bot. La tabla «Dispositivos "
                 "Telegram» (menú lateral, admin) muestra quién está vinculado."),
                ("Preferencias",
                 "Cada preferencia es una regla: «qué evento + qué cámara + qué "
                 "horario + qué días». Puedes tener varias. Sin preferencias no "
                 "recibes avisos (las cámaras siguen grabando igual)."),
                ("Horario y días",
                 "Si no marcas horario, recibes 24 horas. Si marcas días, solo "
                 "esos días aplica. Útil para silenciar avisos cuando estás en "
                 "casa (las cámaras siguen grabando)."),
            ],
            parent=self,
        )
