"""
Vista de Eventos/Alarmas — corazón de un NVR comercial.

Lista todos los eventos detectados (persona, vehículo, movimiento, etc.) con
filtros por cámara/tipo/período, snapshot embebido, reconocer eventos, y
auto-refresh cada 5s para que el operador vea alertas en tiempo real.
"""
import logging
from datetime import datetime
from typing import Optional, List

import requests
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QSpinBox, QSplitter, QMessageBox, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread, QSize
from PySide6.QtGui import QPixmap, QImage, QFont, QColor, QBrush

from desktop_app.src.config import config
from desktop_app.src.services.api_client import api_client
from desktop_app.src.ui.components.glass_card import GlassCard

logger = logging.getLogger(__name__)


# Tipos de evento conocidos con etiqueta legible y emoji
EVENT_TYPES = {
    "person": ("🚨 Persona", "#ef4444"),
    "vehicle": ("🚗 Vehículo", "#f59e0b"),
    "motion": ("📹 Movimiento", "#38bdf8"),
    "camera_offline": ("⚠ Cámara offline", "#fa6"),
    "tampering": ("🔴 Sabotaje", "#ef4444"),
}


class SnapshotLoaderThread(QThread):
    """Descarga el snapshot del evento en background."""
    loaded = Signal(int, QPixmap)  # event_id, pixmap
    failed = Signal(int, str)

    def __init__(self, event_id: int, url: str, token: str):
        super().__init__()
        self.event_id = event_id
        self.url = url
        self.token = token

    def run(self):
        try:
            r = requests.get(
                self.url,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10,
            )
            if r.status_code != 200:
                self.failed.emit(self.event_id, f"HTTP {r.status_code}")
                return
            img = QImage()
            if not img.loadFromData(r.content):
                self.failed.emit(self.event_id, "No se pudo decodificar imagen")
                return
            self.loaded.emit(self.event_id, QPixmap.fromImage(img))
        except Exception as e:
            self.failed.emit(self.event_id, str(e))


class EventsView(QWidget):
    """Vista central de eventos del NVR."""

    # Señal emitida cuando el usuario hace doble-click en un evento que tiene
    # video asociado, para saltar al playback en ese momento.
    jump_to_playback = Signal(int, datetime)  # camera_id, timestamp

    def __init__(self, parent=None):
        super().__init__(parent)
        self._events: List[dict] = []
        self._cameras: List[dict] = []
        self._snapshot_thread: Optional[SnapshotLoaderThread] = None
        self._setup_ui()

        # Auto-refresh cada 10s (los eventos no son críticos en tiempo real)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._silent_refresh)
        self._refresh_timer.start(10000)

        # Carga inicial
        QTimer.singleShot(200, self._load_cameras)
        QTimer.singleShot(400, self._load_events)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header
        header = QHBoxLayout()
        title = QLabel("🔔 Eventos y Alarmas")
        title.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 22px; font-weight: bold;"
        )
        header.addWidget(title)

        from desktop_app.src.ui.components.help_button import HelpButton
        header.addWidget(HelpButton("events_view", parent=self))

        header.addStretch()

        self.lbl_count = QLabel("0 eventos")
        self.lbl_count.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")
        header.addWidget(self.lbl_count)

        self.btn_refresh = QPushButton("🔄 Refrescar")
        self.btn_refresh.clicked.connect(self._load_events)
        header.addWidget(self.btn_refresh)
        layout.addLayout(header)

        # Filtros
        filters = GlassCard()
        f_layout = QHBoxLayout(filters)
        f_layout.setContentsMargins(12, 8, 12, 8)
        f_layout.addWidget(QLabel("Cámara:"))
        self.cmb_camera = QComboBox()
        self.cmb_camera.addItem("Todas", None)
        self.cmb_camera.currentIndexChanged.connect(self._load_events)
        f_layout.addWidget(self.cmb_camera)

        f_layout.addWidget(QLabel("Tipo:"))
        self.cmb_type = QComboBox()
        self.cmb_type.addItem("Todos", None)
        for key, (label, _) in EVENT_TYPES.items():
            self.cmb_type.addItem(label, key)
        self.cmb_type.currentIndexChanged.connect(self._load_events)
        f_layout.addWidget(self.cmb_type)

        f_layout.addWidget(QLabel("Últimas:"))
        self.spin_hours = QSpinBox()
        self.spin_hours.setRange(1, 168)  # hasta 7 días
        self.spin_hours.setValue(24)
        self.spin_hours.setSuffix(" h")
        self.spin_hours.valueChanged.connect(self._debounce_load)
        f_layout.addWidget(self.spin_hours)

        f_layout.addStretch()
        self.lbl_auto = QLabel("⏱ Auto-refresh: ON")
        self.lbl_auto.setStyleSheet(f"color: {config.THEME_ACCENT}; font-size: 11px;")
        f_layout.addWidget(self.lbl_auto)
        layout.addWidget(filters)

        # Splitter: tabla + detalle
        splitter = QSplitter(Qt.Horizontal)

        # ----- Tabla de eventos -----
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "Hora", "Cámara", "Tipo", "Confianza", "Estado"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.setStyleSheet(self._table_style())
        splitter.addWidget(self.table)

        # ----- Panel de detalle -----
        detail = GlassCard()
        d_layout = QVBoxLayout(detail)
        d_layout.setContentsMargins(12, 12, 12, 12)
        d_layout.setSpacing(10)

        d_title = QLabel("Detalle del evento")
        d_title.setStyleSheet(
            f"color: {config.THEME_ACCENT}; font-weight: bold; font-size: 14px;"
        )
        d_layout.addWidget(d_title)

        self.lbl_snapshot = QLabel("Selecciona un evento")
        self.lbl_snapshot.setAlignment(Qt.AlignCenter)
        self.lbl_snapshot.setMinimumSize(320, 240)
        self.lbl_snapshot.setStyleSheet(
            "background:#000; color:#888; border-radius:6px;"
        )
        self.lbl_snapshot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        d_layout.addWidget(self.lbl_snapshot, 1)

        self.lbl_info = QLabel("—")
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setStyleSheet(f"color: {config.THEME_TEXT};")
        d_layout.addWidget(self.lbl_info)

        btns = QHBoxLayout()
        self.btn_ack = QPushButton("✓ Marcar como revisado")
        self.btn_ack.setEnabled(False)
        self.btn_ack.clicked.connect(self._acknowledge_selected)
        btns.addWidget(self.btn_ack)
        self.btn_jump = QPushButton("▶ Ver en Playback")
        self.btn_jump.setEnabled(False)
        self.btn_jump.clicked.connect(self._jump_selected)
        btns.addWidget(self.btn_jump)
        d_layout.addLayout(btns)

        splitter.addWidget(detail)
        splitter.setSizes([700, 500])
        layout.addWidget(splitter, 1)

    def _table_style(self) -> str:
        return f"""
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

    # ------------------------------------------------------------------
    # Carga de datos
    # ------------------------------------------------------------------
    def _load_cameras(self):
        def on_cams(response):
            if not response.success:
                return
            self._cameras = response.data or []
            self.cmb_camera.blockSignals(True)
            self.cmb_camera.clear()
            self.cmb_camera.addItem("Todas", None)
            for c in self._cameras:
                self.cmb_camera.addItem(f"#{c['id']} {c.get('name', '')}", c["id"])
            self.cmb_camera.blockSignals(False)
        api_client.get("cameras/", on_cams)

    def _debounce_load(self):
        # Debounce simple: cancela cualquier load programado y agenda uno nuevo
        QTimer.singleShot(400, self._load_events)

    def _load_events(self):
        params = {"hours": self.spin_hours.value(), "limit": 200}
        cam = self.cmb_camera.currentData()
        if cam is not None:
            params["camera_id"] = cam
        etype = self.cmb_type.currentData()
        if etype:
            params["event_type"] = etype

        def on_events(response):
            if not response.success:
                return
            self._events = response.data or []
            self._populate_table()

        api_client.get("events/", on_events, params=params)

    def _silent_refresh(self):
        """Refresh sin perder la selección."""
        selected_id = None
        rows = self.table.selectionModel().selectedRows()
        if rows:
            selected_id = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        self._pending_select_id = selected_id
        self._load_events()

    def _populate_table(self):
        self.table.setRowCount(0)
        for ev in self._events:
            r = self.table.rowCount()
            self.table.insertRow(r)

            # Hora (parsea ISO timestamp si existe)
            ts_str = ev.get("created_at") or ev.get("timestamp", "")
            try:
                if isinstance(ts_str, (int, float)):
                    dt = datetime.fromtimestamp(ts_str)
                else:
                    dt = datetime.fromisoformat(str(ts_str).replace("Z", ""))
                time_label = dt.strftime("%d/%m %H:%M:%S")
            except Exception:
                time_label = str(ts_str)[:19]

            it_time = QTableWidgetItem(time_label)
            it_time.setData(Qt.UserRole, ev.get("id"))
            self.table.setItem(r, 0, it_time)

            # Cámara (id + nombre si lo encontramos)
            cam_id = ev.get("camera_id")
            cam_name = next(
                (c.get("name", "") for c in self._cameras if c.get("id") == cam_id),
                f"Cam {cam_id}",
            )
            self.table.setItem(r, 1, QTableWidgetItem(f"#{cam_id} {cam_name}"))

            # Tipo (con color)
            etype = ev.get("event_type", "")
            label, color = EVENT_TYPES.get(etype, (etype, config.THEME_TEXT))
            it_type = QTableWidgetItem(label)
            it_type.setForeground(QBrush(QColor(color)))
            self.table.setItem(r, 2, it_type)

            # Confianza
            conf = ev.get("confidence", 0)
            self.table.setItem(r, 3, QTableWidgetItem(f"{float(conf):.0%}" if conf else "—"))

            # Estado
            ack = bool(ev.get("acknowledged", False))
            it_ack = QTableWidgetItem("✓ Revisado" if ack else "🔔 Nuevo")
            it_ack.setForeground(QBrush(QColor("#94a3b8" if ack else "#f59e0b")))
            self.table.setItem(r, 4, it_ack)

            # Resaltar fila si es nuevo
            if not ack:
                for col in range(self.table.columnCount()):
                    item = self.table.item(r, col)
                    if item:
                        item.setBackground(QBrush(QColor(34, 49, 71)))

        self.lbl_count.setText(f"{len(self._events)} eventos")

        # Restaurar selección si había una
        pending = getattr(self, "_pending_select_id", None)
        if pending is not None:
            for r in range(self.table.rowCount()):
                if self.table.item(r, 0).data(Qt.UserRole) == pending:
                    self.table.selectRow(r)
                    break
            self._pending_select_id = None

    # ------------------------------------------------------------------
    # Detalle de evento seleccionado
    # ------------------------------------------------------------------
    def _on_select(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.lbl_snapshot.setText("Selecciona un evento")
            self.lbl_snapshot.setPixmap(QPixmap())
            self.lbl_info.setText("—")
            self.btn_ack.setEnabled(False)
            self.btn_jump.setEnabled(False)
            return

        event_id = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        ev = next((e for e in self._events if e.get("id") == event_id), None)
        if not ev:
            return

        meta = ev.get("metadata", {}) or {}
        info_lines = [
            f"<b>ID:</b> {ev.get('id')}",
            f"<b>Tipo:</b> {ev.get('event_type')}",
            f"<b>Cámara:</b> {ev.get('camera_id')}",
            f"<b>Confianza:</b> {float(ev.get('confidence', 0)):.0%}",
        ]
        if meta:
            for k, v in meta.items():
                if k in ("snapshot_path", "frame"):
                    continue
                info_lines.append(f"<b>{k}:</b> {v}")
        self.lbl_info.setText("<br>".join(info_lines))

        self.btn_ack.setEnabled(not ev.get("acknowledged", False))
        self.btn_jump.setEnabled(True)

        # Cargar snapshot en background
        self._load_snapshot(event_id)

    def _load_snapshot(self, event_id: int):
        self.lbl_snapshot.setText("Cargando snapshot…")
        self.lbl_snapshot.setPixmap(QPixmap())

        token = api_client.get_stream_token() or ""
        url = f"{config.API_BASE_URL}/events/{event_id}/snapshot"

        # Detener thread anterior si existe
        if self._snapshot_thread and self._snapshot_thread.isRunning():
            self._snapshot_thread.quit()
            self._snapshot_thread.wait(500)

        self._snapshot_thread = SnapshotLoaderThread(event_id, url, token)
        self._snapshot_thread.loaded.connect(self._on_snapshot_loaded)
        self._snapshot_thread.failed.connect(self._on_snapshot_failed)
        self._snapshot_thread.start()

    def _on_snapshot_loaded(self, event_id: int, pix: QPixmap):
        # Verificar que aún es el evento seleccionado
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        current_id = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        if current_id != event_id:
            return
        scaled = pix.scaled(
            self.lbl_snapshot.size(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.lbl_snapshot.setPixmap(scaled)

    def _on_snapshot_failed(self, event_id: int, error: str):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        current_id = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        if current_id != event_id:
            return
        self.lbl_snapshot.setText(f"Sin snapshot\n({error[:50]})")

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def _acknowledge_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        event_id = self.table.item(rows[0].row(), 0).data(Qt.UserRole)

        def on_done(response):
            if response.success:
                self._load_events()
            else:
                QMessageBox.warning(self, "Error", f"No se pudo marcar: {response.error}")

        api_client.patch(f"events/{event_id}/acknowledge", on_done)

    def _on_double_click(self, row: int, _col: int):
        self._jump_selected()

    def _jump_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        event_id = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        ev = next((e for e in self._events if e.get("id") == event_id), None)
        if not ev:
            return
        cam_id = ev.get("camera_id")
        ts_str = ev.get("created_at") or ev.get("timestamp")
        try:
            if isinstance(ts_str, (int, float)):
                dt = datetime.fromtimestamp(ts_str)
            else:
                dt = datetime.fromisoformat(str(ts_str).replace("Z", ""))
        except Exception:
            dt = datetime.now()
        self.jump_to_playback.emit(cam_id, dt)

    # ------------------------------------------------------------------
    # Pausa el polling al ocultar la vista (ahorra CPU)
    # ------------------------------------------------------------------
    def showEvent(self, event):
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(10000)
        self.lbl_auto.setText("⏱ Auto-refresh: ON")
        super().showEvent(event)

    def hideEvent(self, event):
        self._refresh_timer.stop()
        self.lbl_auto.setText("⏱ Auto-refresh: OFF")
        super().hideEvent(event)
