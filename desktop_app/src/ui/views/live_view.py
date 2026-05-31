"""
Vista de cámaras en vivo con paginación tipo NVR comercial.

Diseño:
  - Layout selector 1×1 / 2×2 / 3×3 (slots_per_page = cols²).
  - Paginación ◀ Pág X/Y ▶ navega entre grupos de slots.
  - Cache persistente de widgets: cambiar de página NO destruye streams,
    sólo hide/show. Volver a una página atrás es instantáneo.
  - Atajos: PageUp / PageDown / Ctrl+← / Ctrl+→.
  - Dual-lens: cada cámara dual cuenta como 2 slots (L1, L2).
"""
import logging
import math
from typing import List, Dict, Optional, Tuple

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QGridLayout, QFrame, QMenu, QPushButton,
    QComboBox, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer, Slot, QThread, QCoreApplication
from PySide6.QtGui import (
    QPixmap, QMouseEvent, QContextMenuEvent, QShortcut, QKeySequence,
)

from desktop_app.src.models.camera import Camera
from desktop_app.src.services.video_streamer import video_streamer, Frame

logger = logging.getLogger(__name__)


# Tipo: (camera_id, label, stream_type, camera_obj)
SlotTuple = Tuple[int, str, str, Camera]


class CameraWidget(QFrame):
    """
    Widget individual de cámara o lente (para dual-lens).
    Identifica el frame que le toca por (camera_id, stream_type).
    """

    clicked = Signal(int)
    double_clicked = Signal(int)
    ptz_requested = Signal(int)
    snapshot_requested = Signal(int)
    config_requested = Signal(int)

    def __init__(self, camera_id: int, camera_name: str,
                 stream_type: str = "main", parent=None):
        super().__init__(parent)

        self.camera_id = camera_id
        self.camera_name = camera_name
        self.stream_type = stream_type  # "main" | "l1" | "l2"
        self.is_maximized = False
        self._signal_connected = False
        # PULL-BASED rendering: en lugar de recibir cada frame por signal
        # (que se acumulaba en la queue de Qt si el GUI iba lento), tenemos
        # un QTimer que cada 67ms pregunta al thread por el último frame
        # decodificado. Si no hay nuevo no hace nada. Si hay, lo pinta.
        # Esto elimina TODO el delay acumulado.
        self._last_pixmap_seq = -1
        self._pull_timer = QTimer(self)
        self._pull_timer.setInterval(67)  # ~15 fps
        self._pull_timer.timeout.connect(self._pull_latest_frame)
        self._pull_timer.start()

        self.setMinimumSize(280, 200)
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self.lbl_video = QLabel("Esperando video...")
        self.lbl_video.setAlignment(Qt.AlignCenter)
        self.lbl_video.setStyleSheet("""
            background-color: #000000;
            color: #888888;
            border-radius: 4px;
            font-size: 12px;
        """)
        self.lbl_video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.lbl_video.setMinimumHeight(160)
        layout.addWidget(self.lbl_video, 1)

        info_layout = QHBoxLayout()
        self.lbl_name = QLabel(camera_name)
        self.lbl_name.setStyleSheet("color: white; font-weight: bold; font-size: 11px;")
        info_layout.addWidget(self.lbl_name)

        self.lbl_status = QLabel("●")
        self.lbl_status.setStyleSheet("color: #22c55e; font-size: 10px;")
        info_layout.addWidget(self.lbl_status, alignment=Qt.AlignRight)

        self.btn_config = QPushButton("⚙")
        self.btn_config.setMaximumWidth(30)
        self.btn_config.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888888;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover { color: #38bdf8; }
        """)
        self.btn_config.clicked.connect(
            lambda: self.config_requested.emit(self.camera_id)
        )
        info_layout.addWidget(self.btn_config)

        layout.addLayout(info_layout)

        # Ya no nos conectamos al signal global frame_updated — usamos
        # polling con _pull_timer. Esto evita la acumulación de signals.
        # Mantenemos la conexión para errores (camera_error) por compat.
        self._signal_connected = False

    def _pull_latest_frame(self):
        """
        Polling 15fps que pregunta al MJPEGThread por el último pixmap.
        Si hay uno nuevo lo pinta. Si no, no hace nada.

        Como solo procesamos el ÚLTIMO frame disponible (descartando los
        intermedios automáticamente), nunca hay backlog acumulado aunque
        el widget esté oculto durante minutos.
        """
        try:
            pixmap, seq, age_ms = video_streamer.pop_latest_pixmap(
                self.camera_id, self.stream_type, self._last_pixmap_seq
            )
            if pixmap is None or seq == self._last_pixmap_seq:
                return
            self._last_pixmap_seq = seq

            w = self.lbl_video.width()
            h = self.lbl_video.height()
            if w <= 0 or h <= 0:
                return

            scaled = pixmap.scaled(
                w, h,
                Qt.KeepAspectRatio,
                Qt.FastTransformation
            )
            if scaled.isNull():
                return

            self.lbl_video.setPixmap(scaled)
            self.lbl_status.setStyleSheet("color: #22c55e;")
        except Exception as e:
            logger.error(f"Error pull frame: {e}")

    @Slot(Frame)
    def _on_frame(self, frame: Frame):
        """
        [LEGACY] Mantenido por compatibilidad. Ya NO se conecta a la signal
        global frame_updated (usamos pull-based via _pull_latest_frame).

        COALESCING POR TIMESTAMP: si el frame que llega ya tiene >250ms de
        antigüedad significa que la queue de signals de Qt acumuló backlog
        (típicamente porque este widget estuvo oculto un rato y las signals
        se quedaron en cola). Lo descartamos: no tiene sentido pintar un
        frame de hace 10 segundos cuando ya hay frames más nuevos detrás.

        Esto eliminó el bug "al volver a ver la cámara lleva N segundos de
        retraso y luego se recupera": antes Qt procesaba TODAS las signals
        acumuladas en orden; ahora descarta las viejas instantáneamente.
        """
        if frame.camera_id != self.camera_id:
            return
        if getattr(frame, "stream_type", "main") != self.stream_type:
            return

        import time as _time
        try:
            frame_ts = float(frame.timestamp)
        except Exception:
            frame_ts = 0.0
        if frame_ts > 0 and (_time.time() - frame_ts) > 0.25:
            # Frame con backlog → descartar y dejar pasar los más nuevos
            return

        try:
            w = self.lbl_video.width()
            h = self.lbl_video.height()
            if w <= 0 or h <= 0:
                return

            # FastTransformation: ~5x más rápido que SmoothTransformation,
            # imperceptible en stream a 15fps.
            scaled = frame.pixmap.scaled(
                w, h,
                Qt.KeepAspectRatio,
                Qt.FastTransformation
            )

            if scaled.isNull():
                return

            self.lbl_video.setPixmap(scaled)
            self.lbl_status.setStyleSheet("color: #22c55e;")

        except Exception as e:
            logger.error(f"Error mostrando frame: {e}")

    def disconnect_signals(self):
        """Desconecta señales de forma segura e idempotente."""
        if not self._signal_connected:
            return
        try:
            video_streamer.frame_updated.disconnect(self._on_frame)
        except (TypeError, RuntimeError):
            pass
        self._signal_connected = False

    def release_resources(self):
        """
        Libera pixmap y desconecta signals ANTES de deleteLater().
        Sin esto, los QPixmap quedaban en memoria GPU aunque el widget
        se destruyera (memory creep tras cambios frecuentes de layout).
        """
        # Detener el pull timer
        try:
            if hasattr(self, "_pull_timer") and self._pull_timer.isActive():
                self._pull_timer.stop()
        except Exception:
            pass
        self.disconnect_signals()
        self.lbl_video.setPixmap(QPixmap())  # libera el pixmap anterior

    def set_offline(self):
        """Marca como offline."""
        self.lbl_video.setText("Cámara offline")
        self.lbl_status.setStyleSheet("color: #ef4444;")

    def set_online_pending(self):
        """Estado intermedio: aún sin frame, pero conectando."""
        self.lbl_status.setStyleSheet("color: #f59e0b;")

    def closeEvent(self, event):
        self.release_resources()
        super().closeEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.camera_id)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self.double_clicked.emit(self.camera_id)

    def contextMenuEvent(self, event: QContextMenuEvent):
        menu = QMenu(self)
        action_ptz = menu.addAction("Control PTZ")
        action_snapshot = menu.addAction("Capturar imagen")
        action_maximize = menu.addAction(
            "Maximizar" if not self.is_maximized else "Restaurar"
        )
        action = menu.exec(event.globalPos())
        if action == action_ptz:
            self.ptz_requested.emit(self.camera_id)
        elif action == action_snapshot:
            self.snapshot_requested.emit(self.camera_id)
        elif action == action_maximize:
            self.double_clicked.emit(self.camera_id)


class LiveView(QWidget):
    """Vista principal de cámaras en vivo con paginación."""

    camera_selected = Signal(Camera)
    ptz_requested = Signal(Camera)
    camera_config_requested = Signal(int)

    # Marshalling al thread principal (camera_id, token, stream_type)
    _start_stream_signal = Signal(int, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Cache: widgets vivos indexados por (camera_id, stream_type).
        # No se destruyen al cambiar de página — sólo hide/show.
        # Sólo se destruyen al cargar una lista de cámaras totalmente
        # diferente o al hacer logout.
        self._widget_cache: Dict[Tuple[int, str], CameraWidget] = {}

        # Slots actualmente en el grid (visibles)
        self.cameras: Dict[Tuple[int, str], CameraWidget] = {}

        # Estado
        self._camera_list: List[Camera] = []
        self._all_slots: List[SlotTuple] = []
        self._current_api_token: str = ""
        self._grid_cols = 2
        self._current_page = 0
        self._restart_timer: Optional[QTimer] = None

        self._setup_ui()
        self._setup_shortcuts()
        self._start_stream_signal.connect(self._do_start_stream)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._check_cameras_status)
        self._status_timer.start(5000)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header: título, layout selector, paginación
        header = QHBoxLayout()

        self.lbl_title = QLabel("Cámaras en Vivo")
        self.lbl_title.setStyleSheet(
            "color: #f1f5f9; font-size: 20px; font-weight: bold;"
        )
        header.addWidget(self.lbl_title)

        # Botón de ayuda contextual
        self.btn_help = QPushButton("❔")
        self.btn_help.setToolTip("Ayuda — Cámaras en vivo")
        self.btn_help.setMaximumWidth(36)
        self.btn_help.setStyleSheet("""
            QPushButton {
                background-color: transparent; color: #94a3b8;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 6px; padding: 4px 8px; font-size: 14px;
            }
            QPushButton:hover { color: #38bdf8; border-color: #38bdf8; }
        """)
        self.btn_help.clicked.connect(self._show_help)
        header.addWidget(self.btn_help)

        header.addStretch()

        # Selector de layout
        header.addWidget(QLabel("Vista:"))
        self.cmb_layout = QComboBox()
        for text, cols in [
            ("⬜  1×1 (cámara grande)", 1),
            ("⬜⬜  2×2 (4 cámaras)", 2),
            ("⬜⬜⬜  3×3 (9 cámaras)", 3),
        ]:
            self.cmb_layout.addItem(text, cols)
        self.cmb_layout.setCurrentIndex(1)
        self.cmb_layout.currentIndexChanged.connect(self._on_layout_change)
        self.cmb_layout.setStyleSheet("""
            QComboBox {
                background-color: #1e293b;
                color: #f1f5f9;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 6px 12px;
                min-width: 180px;
            }
        """)
        header.addWidget(self.cmb_layout)

        # Separador
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: rgba(255,255,255,0.15);")
        header.addWidget(sep)

        # Paginación
        btn_style = """
            QPushButton {
                background-color: #1e293b;
                color: #f1f5f9;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 6px;
                padding: 6px 12px;
                min-width: 90px;
            }
            QPushButton:hover:!disabled { background-color: #334155; }
            QPushButton:disabled { color: #475569; }
        """
        self.btn_prev = QPushButton("◀ Anterior")
        self.btn_prev.setStyleSheet(btn_style)
        self.btn_prev.setToolTip("Página anterior (Page Up)")
        self.btn_prev.clicked.connect(self._on_prev_page)
        header.addWidget(self.btn_prev)

        self.lbl_page = QLabel("Pág 1/1")
        self.lbl_page.setAlignment(Qt.AlignCenter)
        self.lbl_page.setMinimumWidth(80)
        self.lbl_page.setStyleSheet(
            "color: #f1f5f9; font-size: 13px; font-weight: 600;"
        )
        header.addWidget(self.lbl_page)

        self.btn_next = QPushButton("Siguiente ▶")
        self.btn_next.setStyleSheet(btn_style)
        self.btn_next.setToolTip("Página siguiente (Page Down)")
        self.btn_next.clicked.connect(self._on_next_page)
        header.addWidget(self.btn_next)

        layout.addLayout(header)

        # Grid
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setSpacing(8)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.grid_widget, 1)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key_PageDown), self, activated=self._on_next_page)
        QShortcut(QKeySequence(Qt.Key_PageUp), self, activated=self._on_prev_page)
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=self._on_next_page)
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=self._on_prev_page)

    def _show_help(self):
        from desktop_app.src.ui.dialogs.info_dialog import InfoDialog
        from desktop_app.src.ui.help_texts import get_help
        title, sections = get_help("live_view")
        InfoDialog(title=title, sections=sections, parent=self).exec()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def set_cameras(self, cameras: List[Camera], api_token: str):
        """
        Configura cámaras a mostrar.

        Si la lista es la misma que ya teníamos (mismos IDs), reutiliza
        el cache; sólo reasigna a la página actual. Si la lista cambió,
        destruye widgets de cámaras que ya no existen y crea los nuevos.
        """
        self._camera_list = cameras
        self._current_api_token = api_token

        # Expandir cámaras dual-lens en slots independientes
        new_slots: List[SlotTuple] = []
        for cam in cameras:
            if getattr(cam, "is_dual_lens", False):
                new_slots.append((cam.id, f"{cam.name} · L1", "l1", cam))
                new_slots.append((cam.id, f"{cam.name} · L2", "l2", cam))
            else:
                new_slots.append((cam.id, cam.name, "main", cam))

        # Limpiar widgets que ya no existen en la nueva lista
        new_keys = {(s[0], s[2]) for s in new_slots}
        stale = [k for k in self._widget_cache.keys() if k not in new_keys]
        for key in stale:
            widget = self._widget_cache.pop(key)
            video_streamer.stop_stream(key[0], key[1])
            widget.release_resources()
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()

        self._all_slots = new_slots
        # Si el current_page se queda fuera de rango, ajustar
        max_pages = max(1, self._get_page_count())
        if self._current_page >= max_pages:
            self._current_page = 0

        self._load_page(self._current_page)

    def restart_streams(self, api_token: str):
        """Reinicia los streams con un nuevo token (cambio de sesión)."""
        logger.debug("Reiniciando streams de video con token nuevo...")

        if self._restart_timer:
            self._restart_timer.stop()
            self._restart_timer.deleteLater()
            self._restart_timer = None

        # Forzar reinicio total: destruir todo el cache para que se vuelvan
        # a crear los streams con el token nuevo.
        self._destroy_all_widgets()
        video_streamer.stop_all()

        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.timeout.connect(
            lambda: self.set_cameras(self._camera_list, api_token)
        )
        self._restart_timer.start(500)

    def cleanup(self):
        """Limpieza al ocultar la vista o cerrar la app."""
        if QThread.currentThread() != self.thread():
            QTimer.singleShot(0, self._do_cleanup)
            return
        self._do_cleanup()

    # ------------------------------------------------------------------
    # Paginación
    # ------------------------------------------------------------------
    def _slots_per_page(self) -> int:
        return self._grid_cols * self._grid_cols

    def _get_page_count(self) -> int:
        if not self._all_slots:
            return 1
        return math.ceil(len(self._all_slots) / self._slots_per_page())

    def _get_page_slots(self, page: int) -> List[SlotTuple]:
        per = self._slots_per_page()
        start = page * per
        return self._all_slots[start:start + per]

    def _load_page(self, page: int):
        """Pinta los widgets de la página solicitada usando el cache."""
        if page < 0:
            page = 0
        max_pages = max(1, self._get_page_count())
        if page >= max_pages:
            page = max_pages - 1
        self._current_page = page

        slots = self._get_page_slots(page)

        # Quitar TODOS los widgets del grid (sin destruir — quedan en cache)
        for key, widget in list(self.cameras.items()):
            self.grid.removeWidget(widget)
            widget.hide()
        self.cameras.clear()

        # Limpiar stretches viejos (importante al pasar de 3x3 → 1x1)
        for i in range(self.grid.rowCount()):
            self.grid.setRowStretch(i, 0)
        for i in range(self.grid.columnCount()):
            self.grid.setColumnStretch(i, 0)

        # Colocar widgets de esta página (creándolos si es la primera vez)
        cols = self._grid_cols
        for i, (cam_id, label, stream_type, _cam) in enumerate(slots):
            key = (cam_id, stream_type)
            widget = self._widget_cache.get(key)
            if widget is None:
                widget = CameraWidget(cam_id, label, stream_type=stream_type)
                widget.clicked.connect(self._on_camera_click)
                widget.double_clicked.connect(self._on_camera_double_click)
                widget.ptz_requested.connect(self._on_ptz_request)
                widget.snapshot_requested.connect(self._on_snapshot)
                widget.config_requested.connect(self._on_config_request)
                self._widget_cache[key] = widget

                # Arrancar el stream (escalonado para no martillar el server)
                QTimer.singleShot(
                    i * 200,
                    lambda c=cam_id, t=self._current_api_token, s=stream_type:
                        self._start_stream(c, t, s)
                )
            else:
                # Reutilizado del cache: actualizar label por si cambió el name
                widget.lbl_name.setText(label)
                # Asegurar que el stream esté vivo. Si no lo está (porque la
                # app se quedó sin conexión o fue logout previo), reiniciarlo.
                if not video_streamer.is_streaming(cam_id, stream_type):
                    self._start_stream(cam_id, self._current_api_token, stream_type)

            row = i // cols
            col = i % cols
            self.grid.addWidget(widget, row, col)
            widget.show()
            self.cameras[key] = widget

        # Aplicar stretch sólo a las filas/cols usadas
        for r in range(cols):
            self.grid.setRowStretch(r, 1)
            self.grid.setColumnStretch(r, 1)

        # Actualizar UI de paginación
        self._update_pagination_ui()
        QCoreApplication.processEvents()

    def _update_pagination_ui(self):
        total = self._get_page_count()
        self.lbl_page.setText(f"Pág {self._current_page + 1}/{total}")
        self.btn_prev.setEnabled(self._current_page > 0)
        self.btn_next.setEnabled(self._current_page < total - 1)

    @Slot()
    def _on_prev_page(self):
        if self._current_page > 0:
            self._load_page(self._current_page - 1)

    @Slot()
    def _on_next_page(self):
        if self._current_page < self._get_page_count() - 1:
            self._load_page(self._current_page + 1)

    # ------------------------------------------------------------------
    # Cambio de layout (1×1 / 2×2 / 3×3)
    # ------------------------------------------------------------------
    def _on_layout_change(self, idx: int):
        new_cols = self.cmb_layout.itemData(idx)
        if new_cols == self._grid_cols:
            return
        self._grid_cols = new_cols
        self._current_page = 0
        # No destruye nada — sólo recoloca desde el cache
        self._load_page(0)

    # ------------------------------------------------------------------
    # Streams
    # ------------------------------------------------------------------
    def _start_stream(self, camera_id: int, api_token: str,
                      stream_type: str = "main"):
        if QThread.currentThread() != self.thread():
            self._start_stream_signal.emit(camera_id, api_token, stream_type)
            return
        self._do_start_stream(camera_id, api_token, stream_type)

    @Slot(int, str, str)
    def _do_start_stream(self, camera_id: int, api_token: str,
                         stream_type: str = "main"):
        from desktop_app.src.config import config
        video_streamer.set_base_url(config.API_BASE_URL)
        video_streamer.start_stream(camera_id, api_token, stream_type=stream_type)
        # Marcar widget como "esperando frame" si está visible
        widget = self.cameras.get((camera_id, stream_type))
        if widget:
            widget.set_online_pending()

    # ------------------------------------------------------------------
    # Click handlers
    # ------------------------------------------------------------------
    def _on_camera_click(self, camera_id: int):
        logger.debug(f"Cámara seleccionada: {camera_id}")

    def _on_camera_double_click(self, camera_id: int):
        for (cid, _stype), widget in self.cameras.items():
            if cid == camera_id:
                widget.is_maximized = not widget.is_maximized
                break

    def _on_ptz_request(self, camera_id: int):
        logger.info(f"PTZ solicitado para cámara {camera_id}")
        self.ptz_requested.emit(Camera(id=camera_id, name=""))

    def _on_snapshot(self, camera_id: int):
        widget = None
        for (cid, _stype), w in self.cameras.items():
            if cid == camera_id:
                widget = w
                break
        if widget and widget.lbl_video.pixmap():
            from PySide6.QtCore import QStandardPaths
            import time
            pictures_path = QStandardPaths.writableLocation(
                QStandardPaths.PicturesLocation
            )
            filename = f"snapshot_{camera_id}_{int(time.time())}.png"
            full_path = f"{pictures_path}/{filename}"
            if widget.lbl_video.pixmap().save(full_path):
                logger.info(f"Snapshot guardado: {full_path}")

    def _on_config_request(self, camera_id: int):
        logger.info(f"Configuración solicitada para cámara {camera_id}")
        self.camera_config_requested.emit(camera_id)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def _check_cameras_status(self):
        for (cam_id, stream_type), widget in self.cameras.items():
            if not video_streamer.is_streaming(cam_id, stream_type):
                widget.set_offline()

    # ------------------------------------------------------------------
    # Limpieza
    # ------------------------------------------------------------------
    def _destroy_all_widgets(self):
        """
        Destruye TODO el cache de widgets (logout, cambio total de lista).
        Garantiza release_resources antes de deleteLater para evitar leaks
        de QPixmap en GPU.
        """
        for key, widget in list(self._widget_cache.items()):
            widget.release_resources()
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        self._widget_cache.clear()
        self.cameras.clear()

        # Limpiar grid layout
        while self.grid.count():
            item = self.grid.takeAt(0)
            if w := item.widget():
                w.setParent(None)

    def _do_cleanup(self):
        """
        Limpieza al ocultar la vista. NO destruye widgets — sólo para que
        cuando vuelvas a la vista los streams ya estén vivos.
        Cuando se hace logout real, se llama a _destroy_all_widgets vía
        restart_streams o explícitamente desde MainWindow._logout.
        """
        if self._status_timer.isActive():
            self._status_timer.stop()
        # Streams se mantienen vivos en background — el usuario eligió
        # "siempre vivos" en la configuración del modo paginación.

    def hideEvent(self, event):
        self.cleanup()
        super().hideEvent(event)

    def showEvent(self, event):
        if not self._status_timer.isActive():
            self._status_timer.start(5000)
        super().showEvent(event)

    def closeEvent(self, event):
        self._destroy_all_widgets()
        video_streamer.stop_all()
        if self._status_timer.isActive():
            self._status_timer.stop()
        super().closeEvent(event)
