"""
Vista de control de cámara individual.

Layout estándar NVR comercial:
  [ video grande, full-screen ]  [ panel controles ]
       (75% del ancho)              (25% del ancho)

Se accede al hacer click en una cámara desde LiveView o desde
CameraManagementView. Reemplaza el viejo diálogo modal que se quedaba
chico y no permitía interacción cómoda.
"""
import logging
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSplitter, QSizePolicy, QFrame,
)
from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import QPixmap

from desktop_app.src.config import config
from desktop_app.src.services.api_client import api_client
from desktop_app.src.ui.components.camera_control_panel import CameraControlPanel
from desktop_app.src.ui.components.rtsp_video import RtspVideoWidget

logger = logging.getLogger(__name__)


class CameraControlView(QWidget):
    """Vista grande de una sola cámara con panel de controles a la derecha."""

    # Señal para volver a la vista anterior (LiveView)
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_camera_id: Optional[int] = None
        self._current_stream_type: str = "main"
        self._is_dual_lens = False
        self._cameras_cache: list = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Header: botón volver + selector de cámara + selector de lente
        header = QHBoxLayout()

        self.btn_back = QPushButton("◀  Volver a En Vivo")
        self.btn_back.setMinimumHeight(36)
        self.btn_back.clicked.connect(self.back_requested.emit)
        self.btn_back.setStyleSheet(f"""
            QPushButton {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 0 16px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
            }}
        """)
        header.addWidget(self.btn_back)

        header.addSpacing(16)

        header.addWidget(QLabel("Cámara:"))
        self.cmb_camera = QComboBox()
        self.cmb_camera.setMinimumWidth(220)
        self.cmb_camera.currentIndexChanged.connect(self._on_camera_change)
        header.addWidget(self.cmb_camera)

        header.addSpacing(8)
        header.addWidget(QLabel("Lente:"))
        self.cmb_lens = QComboBox()
        self.cmb_lens.addItem("main", "main")
        self.cmb_lens.currentIndexChanged.connect(self._on_lens_change)
        header.addWidget(self.cmb_lens)

        header.addStretch()

        # FPS indicator
        self.lbl_fps = QLabel("— fps")
        self.lbl_fps.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; "
            f"font-family: monospace; font-size: 11px; padding: 0 8px;"
        )
        header.addWidget(self.lbl_fps)

        layout.addLayout(header)

        # Body: splitter video | panel controles
        self.splitter = QSplitter(Qt.Horizontal)

        # Video area (izquierda, grande)
        video_container = QFrame()
        video_container.setStyleSheet(
            "background-color: #000; border-radius: 4px;"
        )
        video_layout = QVBoxLayout(video_container)
        video_layout.setContentsMargins(2, 2, 2, 2)

        self.video_widget = RtspVideoWidget(placeholder="Selecciona una cámara…")
        video_layout.addWidget(self.video_widget)

        self.splitter.addWidget(video_container)

        # Panel de controles (derecha)
        self.control_panel = CameraControlPanel()
        self.control_panel.setMinimumWidth(340)
        self.control_panel.setMaximumWidth(420)
        self.splitter.addWidget(self.control_panel)

        # Proporción inicial 75% video / 25% panel
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([900, 360])

        layout.addWidget(self.splitter, 1)
        self.lbl_fps.setText("go2rtc")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def set_cameras(self, cameras: list):
        """Actualiza la lista de cámaras disponibles en el combo."""
        self._cameras_cache = list(cameras)
        prev_id = self._current_camera_id

        self.cmb_camera.blockSignals(True)
        self.cmb_camera.clear()
        self.cmb_camera.addItem("— Selecciona —", None)
        for cam in cameras:
            label = f"#{cam.id} · {cam.name}"
            if getattr(cam, "is_dual_lens", False):
                label += "  (dual-lens)"
            self.cmb_camera.addItem(label, cam.id)
        self.cmb_camera.blockSignals(False)

        # Restaurar selección si existía
        if prev_id is not None:
            for i in range(self.cmb_camera.count()):
                if self.cmb_camera.itemData(i) == prev_id:
                    self.cmb_camera.setCurrentIndex(i)
                    return

    def show_camera(self, camera_id: int, lens: str = "main"):
        """Muestra una cámara específica (llamado desde LiveView)."""
        # Asegurar que está en el combo
        for i in range(self.cmb_camera.count()):
            if self.cmb_camera.itemData(i) == camera_id:
                self.cmb_camera.setCurrentIndex(i)
                break

        # Establecer el lente solicitado
        if self._is_dual_lens and lens in ("l1", "l2"):
            for i in range(self.cmb_lens.count()):
                if self.cmb_lens.itemData(i) == lens:
                    self.cmb_lens.setCurrentIndex(i)
                    return

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    def _on_camera_change(self, idx: int):
        camera_id = self.cmb_camera.itemData(idx)
        self._stop_current_stream()

        if camera_id is None:
            self.video_widget.show_message("Selecciona una cámara…")
            self._current_camera_id = None
            return

        camera = next((c for c in self._cameras_cache if c.id == camera_id), None)
        if not camera:
            return

        self._current_camera_id = camera_id
        self._is_dual_lens = getattr(camera, "is_dual_lens", False)

        # Reconfigurar el combo de lente
        self.cmb_lens.blockSignals(True)
        self.cmb_lens.clear()
        if self._is_dual_lens:
            self.cmb_lens.addItem("L1 (lente 1)", "l1")
            self.cmb_lens.addItem("L2 (lente 2)", "l2")
            self._current_stream_type = "l1"
        else:
            self.cmb_lens.addItem("Principal", "main")
            self._current_stream_type = "main"
        self.cmb_lens.blockSignals(False)

        # Cargar datos completos en el panel de controles
        def on_cam_data(response):
            if response.success:
                self.control_panel.set_camera(camera_id, response.data or {})
        api_client.get(f"cameras/{camera_id}", on_cam_data)

        # Iniciar stream
        self._start_stream()

    def _on_lens_change(self, idx: int):
        lens = self.cmb_lens.itemData(idx)
        if not lens or lens == self._current_stream_type:
            return
        self._stop_current_stream()
        self._current_stream_type = lens
        self._start_stream()

    def _pick_url(self, camera, stream_type: str) -> str:
        """URL go2rtc para (cámara, lente). Calidad alta por defecto."""
        if camera is None:
            return ""
        urls = getattr(camera, "stream_urls", None) or {}
        key = stream_type if stream_type in ("l1", "l2") else "main"
        by_q = urls.get(key) or {}
        legacy = {
            "l1": getattr(camera, "stream_url_l1", None),
            "l2": getattr(camera, "stream_url_l2", None),
        }.get(stream_type)
        return by_q.get("high") or legacy or (getattr(camera, "stream_url", "") or "")

    def _start_stream(self):
        if self._current_camera_id is None:
            return
        camera = next(
            (c for c in self._cameras_cache if c.id == self._current_camera_id), None
        )
        url = self._pick_url(camera, self._current_stream_type)
        if url:
            self.video_widget.play(url)
        else:
            self.video_widget.show_message(
                "Sin stream go2rtc (¿GO2RTC_ENABLED en el backend?)"
            )

    def _stop_current_stream(self):
        try:
            self.video_widget.stop()
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        # Si volvemos a la vista, restaurar stream
        if self._current_camera_id is not None:
            self._start_stream()

    def hideEvent(self, event):
        # Liberar stream cuando se cambia de vista (ahorra red+CPU)
        self._stop_current_stream()
        super().hideEvent(event)
