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
from desktop_app.src.services.video_streamer import video_streamer, Frame
from desktop_app.src.ui.components.camera_control_panel import CameraControlPanel

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
        # PULL-BASED: igual que CameraWidget, polling 15fps en vez de signal
        self._last_pixmap_seq = -1
        self._setup_ui()

        # QTimer interno que pregunta al thread cada 67ms por el último frame
        self._pull_timer = QTimer(self)
        self._pull_timer.setInterval(67)
        self._pull_timer.timeout.connect(self._pull_latest_frame)
        self._pull_timer.start()

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

        self.video_label = QLabel("Selecciona una cámara…")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet(
            "background-color: #000; color: #888; font-size: 14px;"
        )
        self.video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        video_layout.addWidget(self.video_label)

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

        # FPS counter
        self._paint_count = 0
        self._paint_last_t = 0
        self._fps_timer = QTimer(self)
        self._fps_timer.timeout.connect(self._update_fps)
        self._fps_timer.start(1000)

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
        # Reset del seq para que el pull recoja frames desde 0 con la nueva cámara
        self._last_pixmap_seq = -1

        if camera_id is None:
            self.video_label.setText("Selecciona una cámara…")
            self.video_label.setPixmap(QPixmap())
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

    def _start_stream(self):
        if self._current_camera_id is None:
            return
        token = api_client.get_stream_token() or ""
        video_streamer.set_base_url(config.API_BASE_URL)
        self.video_label.setText(
            f"🔄 Conectando a cámara {self._current_camera_id} "
            f"({self._current_stream_type})…"
        )
        video_streamer.start_stream(
            self._current_camera_id, token,
            stream_type=self._current_stream_type
        )

    def _stop_current_stream(self):
        if self._current_camera_id is not None:
            try:
                video_streamer.stop_stream(
                    self._current_camera_id, self._current_stream_type
                )
            except Exception:
                pass

    def _pull_latest_frame(self):
        """Pull-based: pregunta al MJPEGThread por el último pixmap a 15fps."""
        if self._current_camera_id is None:
            return
        try:
            pixmap, seq, age_ms = video_streamer.pop_latest_pixmap(
                self._current_camera_id,
                self._current_stream_type,
                self._last_pixmap_seq,
            )
            if pixmap is None or seq == self._last_pixmap_seq:
                return
            self._last_pixmap_seq = seq

            scaled = pixmap.scaled(
                self.video_label.size(),
                Qt.KeepAspectRatio,
                Qt.FastTransformation,
            )
            self.video_label.setPixmap(scaled)
            self._paint_count += 1
        except Exception as e:
            logger.debug(f"Error pull frame: {e}")

    @Slot(Frame)
    def _on_frame(self, frame):
        """[LEGACY] ya no se conecta. Usamos _pull_latest_frame con QTimer."""
        pass

    def _update_fps(self):
        import time
        now = time.time()
        if self._paint_last_t > 0:
            dt = now - self._paint_last_t
            if dt > 0:
                fps = self._paint_count / dt
                color = "#22c55e" if fps >= 10 else (
                    "#fbbf24" if fps >= 5 else "#ef4444"
                )
                self.lbl_fps.setText(f"{fps:5.1f} fps")
                self.lbl_fps.setStyleSheet(
                    f"color: {color}; font-family: monospace; "
                    f"font-size: 11px; padding: 0 8px; font-weight: bold;"
                )
        self._paint_count = 0
        self._paint_last_t = now

    def showEvent(self, event):
        super().showEvent(event)
        # Si volvemos a la vista, restaurar stream
        if self._current_camera_id is not None:
            self._start_stream()
        if not self._fps_timer.isActive():
            self._fps_timer.start(1000)

    def hideEvent(self, event):
        # Liberar stream cuando se cambia de vista (ahorra red+CPU)
        self._stop_current_stream()
        self._fps_timer.stop()
        super().hideEvent(event)
