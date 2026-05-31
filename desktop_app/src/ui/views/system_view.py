"""
Vista de salud del sistema — dashboard de monitoreo.

Muestra en tiempo real:
- CPU / RAM / disco del servidor
- Estado de cada cámara (FPS, conexión, último frame)
- Información de hardware
- Estadísticas de eventos (últimas 24h por tipo)
"""
import logging
from typing import List

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QGroupBox, QGridLayout, QTableWidget,
    QTableWidgetItem, QHeaderView,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush

from desktop_app.src.config import config
from desktop_app.src.services.api_client import api_client
from desktop_app.src.ui.components.glass_card import GlassCard

logger = logging.getLogger(__name__)


class StatCard(GlassCard):
    """Tarjeta con un número grande + label + progress bar opcional."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent, border_radius=8)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 12px; font-weight: bold;"
        )
        layout.addWidget(self.lbl_title)

        self.lbl_value = QLabel("—")
        self.lbl_value.setStyleSheet(
            f"color: {config.THEME_ACCENT}; font-size: 28px; font-weight: bold;"
        )
        layout.addWidget(self.lbl_value)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {config.THEME_SECONDARY};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background-color: {config.THEME_ACCENT};
                border-radius: 3px;
            }}
        """)
        layout.addWidget(self.bar)

        self.lbl_sub = QLabel("")
        self.lbl_sub.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 10px;"
        )
        layout.addWidget(self.lbl_sub)

    def update_value(self, value: str, percent: float = None,
                     subtitle: str = "", color: str = None):
        self.lbl_value.setText(value)
        if color:
            self.lbl_value.setStyleSheet(
                f"color: {color}; font-size: 28px; font-weight: bold;"
            )
        if percent is not None:
            self.bar.setValue(int(percent))
            # Color del bar según nivel
            if percent >= 90:
                bar_color = "#ef4444"
            elif percent >= 70:
                bar_color = "#f59e0b"
            else:
                bar_color = config.THEME_ACCENT
            self.bar.setStyleSheet(f"""
                QProgressBar {{
                    background-color: {config.THEME_SECONDARY};
                    border: none;
                    border-radius: 3px;
                }}
                QProgressBar::chunk {{
                    background-color: {bar_color};
                    border-radius: 3px;
                }}
            """)
        self.lbl_sub.setText(subtitle)


class SystemHealthView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

        # Polling cada 3s mientras la vista esté visible
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(5000)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header
        header = QHBoxLayout()
        title = QLabel("📊 Estado del Sistema")
        title.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 22px; font-weight: bold;"
        )
        header.addWidget(title)
        header.addStretch()
        self.lbl_last_update = QLabel("Actualizando…")
        self.lbl_last_update.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")
        header.addWidget(self.lbl_last_update)
        layout.addLayout(header)

        # Stat cards (CPU, RAM, Disco, Cámaras activas)
        stats_row = QHBoxLayout()
        self.card_cpu = StatCard("CPU del servidor")
        self.card_ram = StatCard("Memoria RAM")
        self.card_disk = StatCard("Almacenamiento")
        self.card_cams = StatCard("Cámaras activas")
        for c in (self.card_cpu, self.card_ram, self.card_disk, self.card_cams):
            stats_row.addWidget(c)
        layout.addLayout(stats_row)

        # Hardware info
        hw_box = GlassCard()
        hw_layout = QGridLayout(hw_box)
        hw_layout.setContentsMargins(16, 12, 16, 12)
        title_hw = QLabel("🖥 Hardware del servidor")
        title_hw.setStyleSheet(
            f"color: {config.THEME_ACCENT}; font-weight: bold; font-size: 14px;"
        )
        hw_layout.addWidget(title_hw, 0, 0, 1, 4)

        self.lbl_cpu_info = QLabel("CPU: —")
        self.lbl_ram_info = QLabel("RAM total: —")
        self.lbl_gpu_info = QLabel("GPU: —")
        self.lbl_os_info = QLabel("OS: —")
        for w in (self.lbl_cpu_info, self.lbl_ram_info, self.lbl_gpu_info, self.lbl_os_info):
            w.setStyleSheet(f"color: {config.THEME_TEXT};")
        hw_layout.addWidget(self.lbl_cpu_info, 1, 0)
        hw_layout.addWidget(self.lbl_ram_info, 1, 1)
        hw_layout.addWidget(self.lbl_gpu_info, 1, 2)
        hw_layout.addWidget(self.lbl_os_info, 1, 3)
        layout.addWidget(hw_box)

        # Tabla de cámaras
        cams_box = GlassCard()
        cams_layout = QVBoxLayout(cams_box)
        cams_layout.setContentsMargins(16, 12, 16, 12)
        title_cams = QLabel("📷 Estado por cámara")
        title_cams.setStyleSheet(
            f"color: {config.THEME_ACCENT}; font-weight: bold; font-size: 14px;"
        )
        cams_layout.addWidget(title_cams)

        self.table_cams = QTableWidget(0, 5)
        self.table_cams.setHorizontalHeaderLabels([
            "ID", "Estado", "FPS", "Último frame", "Total frames"
        ])
        self.table_cams.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        from desktop_app.src.ui.views.users_view import _TABLE_STYLE
        self.table_cams.setStyleSheet(_TABLE_STYLE)
        cams_layout.addWidget(self.table_cams)
        layout.addWidget(cams_box, 1)

        # Carga inicial
        QTimer.singleShot(100, self._load_hardware)
        QTimer.singleShot(200, self._poll)

    def _load_hardware(self):
        """Una vez: info de hardware (no cambia)."""
        def on_hw(response):
            if not response.success:
                return
            hw = response.data or {}
            cpu = hw.get("cpu", {})
            self.lbl_cpu_info.setText(
                f"CPU: {cpu.get('brand', '?')} ({cpu.get('cores', '?')} cores)"
            )
            mem = hw.get("memory", {})
            self.lbl_ram_info.setText(f"RAM total: {mem.get('total_gb', '?')} GB")
            gpu = hw.get("gpu") or "No detectada"
            if isinstance(gpu, dict):
                gpu = gpu.get("name", "?")
            self.lbl_gpu_info.setText(f"GPU: {gpu}")
            os_info = hw.get("os") or hw.get("platform") or "?"
            self.lbl_os_info.setText(f"OS: {os_info}")
        api_client.get("system/hardware", on_hw)

    def _poll(self):
        """Polling de salud + estadísticas (cada 3s)."""
        def on_health(response):
            if not response.success:
                self.lbl_last_update.setText("⚠ Sin conexión")
                return
            from datetime import datetime
            self.lbl_last_update.setText(
                f"Actualizado: {datetime.now().strftime('%H:%M:%S')}"
            )
            data = response.data or {}
            self._update_stats(data)
            self._update_cameras(data.get("cameras", []))
        api_client.get("system/health", on_health)

    def _update_stats(self, data: dict):
        sys_info = data.get("system", {})

        # CPU
        cpu_pct = sys_info.get("cpu_percent", 0)
        self.card_cpu.update_value(
            f"{cpu_pct:.0f}%", percent=cpu_pct,
            color=("#ef4444" if cpu_pct > 85 else config.THEME_ACCENT),
        )

        # RAM
        mem_pct = sys_info.get("memory_percent", 0)
        self.card_ram.update_value(
            f"{mem_pct:.0f}%", percent=mem_pct,
            color=("#ef4444" if mem_pct > 85 else config.THEME_ACCENT),
        )

        # Disco
        disk_pct = sys_info.get("disk_percent", 0)
        self.card_disk.update_value(
            f"{disk_pct:.0f}%", percent=disk_pct,
            color=("#ef4444" if disk_pct > 90 else config.THEME_ACCENT),
        )

        # Cámaras
        cams = data.get("cameras", [])
        healthy = sum(1 for c in cams if c.get("status") == "healthy")
        total = len(cams)
        self.card_cams.update_value(
            f"{healthy}/{total}",
            subtitle=f"{total - healthy} inactivas" if total > healthy else "Todas OK",
        )

    def _update_cameras(self, cameras: List[dict]):
        self.table_cams.setRowCount(0)
        for c in cameras:
            r = self.table_cams.rowCount()
            self.table_cams.insertRow(r)

            self.table_cams.setItem(r, 0, QTableWidgetItem(str(c.get("id", ""))))

            status = c.get("status", "?")
            it_status = QTableWidgetItem(
                "🟢 Activa" if status == "healthy" else "🔴 Sin frames"
            )
            it_status.setForeground(QBrush(QColor(
                "#22c55e" if status == "healthy" else "#ef4444"
            )))
            self.table_cams.setItem(r, 1, it_status)

            self.table_cams.setItem(r, 2, QTableWidgetItem(f"{c.get('fps', 0):.1f}"))
            secs_ago = c.get("last_frame_seconds_ago", 0)
            self.table_cams.setItem(r, 3, QTableWidgetItem(f"hace {secs_ago:.0f}s"))
            self.table_cams.setItem(r, 4, QTableWidgetItem(str(c.get("total_frames", 0))))

    def showEvent(self, event):
        if not self._timer.isActive():
            self._timer.start(5000)
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)
