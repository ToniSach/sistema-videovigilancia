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
from desktop_app.src.ui.icons import icon

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
                 stream_type: str = "main", stream_url: str = "", parent=None):
        super().__init__(parent)

        self.camera_id = camera_id
        self.camera_name = camera_name
        self.stream_type = stream_type  # "main" | "l1" | "l2"
        self.stream_url = stream_url or ""
        # Modo RTSP/go2rtc (OPT-IN). Si hay stream_url Y USE_GO2RTC_LIVE=true,
        # el directo se reproduce con VLC (baja latencia, una sola conexión a
        # la cámara) en vez de MJPEG. Si no, comportamiento clásico intacto.
        from desktop_app.src.config import config as _cfg
        self._use_rtsp = bool(self.stream_url) and getattr(_cfg, "USE_GO2RTC_LIVE", False)
        self._vlc = None
        self.is_maximized = False
        self._signal_connected = False
        # PULL-BASED rendering (modo MJPEG): un QTimer cada 67ms pregunta al
        # thread por el último frame decodificado. En modo RTSP NO se usa.
        self._last_pixmap_seq = -1
        self._pull_timer = QTimer(self)
        self._pull_timer.setInterval(67)  # ~15 fps
        self._pull_timer.timeout.connect(self._pull_latest_frame)
        if not self._use_rtsp:
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

        # Reloj en vivo (hora actual) sobre la barra de la cámara.
        self.lbl_clock = QLabel("")
        self.lbl_clock.setStyleSheet("color: #94a3b8; font-size: 10px;")
        info_layout.addWidget(self.lbl_clock)

        # Indicador REC (rojo) — visible solo si la cámara está grabando.
        self.lbl_rec = QLabel("●REC")
        self.lbl_rec.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
        self.lbl_rec.setVisible(False)
        info_layout.addWidget(self.lbl_rec)

        info_layout.addStretch()

        self.lbl_status = QLabel("●")
        self.lbl_status.setStyleSheet("color: #22c55e; font-size: 10px;")
        info_layout.addWidget(self.lbl_status)

        # Botón snapshot (captura rápida del fotograma actual).
        self.btn_snapshot = QPushButton()
        self.btn_snapshot.setIcon(icon("snapshot", "#888888"))
        self.btn_snapshot.setMaximumWidth(30)
        self.btn_snapshot.setToolTip("Capturar imagen")
        self.btn_snapshot.setStyleSheet("""
            QPushButton { background-color: transparent; color: #888; border: none; font-size: 14px; }
            QPushButton:hover { color: #38bdf8; }
        """)
        self.btn_snapshot.clicked.connect(
            lambda: self.snapshot_requested.emit(self.camera_id)
        )
        info_layout.addWidget(self.btn_snapshot)

        self.btn_config = QPushButton()
        self.btn_config.setIcon(icon("settings"))
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

        # Reloj que actualiza la hora cada segundo (barato).
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(1000)
        self._tick_clock()

    def _tick_clock(self):
        from datetime import datetime as _dt
        try:
            self.lbl_clock.setText(_dt.now().strftime("%H:%M:%S"))
        except Exception:
            pass

    def set_recording(self, on: bool):
        """Muestra/oculta el indicador ●REC."""
        if hasattr(self, "lbl_rec"):
            self.lbl_rec.setVisible(bool(on))

        # Ya no nos conectamos al signal global frame_updated — usamos
        # polling con _pull_timer. Esto evita la acumulación de signals.
        # Mantenemos la conexión para errores (camera_error) por compat.
        self._signal_connected = False

        # Modo RTSP: arrancar VLC tras tener winId nativo (QTimer 0ms).
        if self._use_rtsp:
            self.lbl_video.setText("Conectando (RTSP)…")
            QTimer.singleShot(0, self._start_rtsp)

    def _start_rtsp(self):
        """
        Reproduce el restream RTSP de go2rtc con VLC, tuneado a LATENCIA MÍNIMA
        para directo (no VOD). VLC pinta directamente sobre el HWND/xwindow del
        QLabel de vídeo, así que no pasa por el pipeline MJPEG/pixmaps.
        """
        try:
            from desktop_app.src.services.playback_service import VLCPlayer
            import os as _os
            opts = [
                "--quiet", "--no-video-title-show",
                "--network-caching=150",   # buffer mínimo (ms)
                "--rtsp-tcp",              # RTSP sobre TCP (robusto en WiFi)
                "--clock-jitter=0", "--clock-synchro=0",
            ]
            self._vlc = VLCPlayer(config_options=opts)
            wid = int(self.lbl_video.winId())
            if _os.name == "nt":
                self._vlc.set_hwnd(wid)
            else:
                self._vlc.set_xwindow(wid)
            self._vlc.error.connect(
                lambda m: logger.error(f"VLC live cam {self.camera_id}: {m}")
            )
            self._vlc.play_url(self.stream_url)
            # Rellenar el panel completo (sin barras negras). Se aplica tras un
            # instante (cuando el vídeo ya tiene tamaño) y en cada resize.
            QTimer.singleShot(300, self._apply_fill)
            self.lbl_status.setStyleSheet("color: #22c55e;")
        except Exception as e:
            logger.error(f"No se pudo iniciar RTSP live cam {self.camera_id}: {e}")
            self.lbl_video.setText("Error RTSP")

    def _apply_fill(self):
        """
        Hace que el vídeo RELLENE todo el panel (sin barras negras). Fuerza la
        relación de aspecto al tamaño del contenedor → VLC estira para llenar.
        Se llama al iniciar y en cada resize.
        """
        try:
            if self._vlc is None:
                return
            w = max(1, self.lbl_video.width())
            h = max(1, self.lbl_video.height())
            self._vlc.player.video_set_aspect_ratio(f"{w}:{h}".encode("ascii"))
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_use_rtsp", False):
            self._apply_fill()

    def hideEvent(self, event):
        # Pausar el reloj cuando el widget no se ve (queda en cache de páginas):
        # evita ~1 tick/seg × N widgets ocultos corriendo en background.
        if hasattr(self, "_clock_timer") and self._clock_timer.isActive():
            self._clock_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        if hasattr(self, "_clock_timer") and not self._clock_timer.isActive():
            self._clock_timer.start(1000)
            self._tick_clock()
        super().showEvent(event)

    def set_live_url(self, url: str):
        """
        Cambia la fuente del directo SIN recrear el player (evita el crash de
        destruir/crear VLC). Reutiliza la misma ventana: VLC hace stop+play.
        Usado por el selector de calidad.
        """
        if not getattr(self, "_use_rtsp", False) or not url or self._vlc is None:
            return
        if url == self.stream_url:
            return
        self.stream_url = url
        try:
            # ASÍNCRONO: player.stop() de libVLC es bloqueante (espera al decoder
            # RTSP). En el hilo UI y para cada panel congelaba la app al cambiar
            # calidad. play_url_async hace el swap en un hilo de fondo.
            self._vlc.play_url_async(url)
            QTimer.singleShot(800, self._apply_fill)
        except Exception as e:
            logger.error(f"set_live_url cam {self.camera_id}: {e}")

    def stop_video(self):
        """
        Detiene VLC y lo DESLIGA de la ventana (idempotente). Desligar el HWND
        antes de que el QLabel se destruya evita que VLC pinte sobre una ventana
        liberada (causa típica de crash nativo al cerrar/cambiar de vista).
        """
        try:
            if self._vlc is None:
                return
            import os as _os
            p = self._vlc.player
            try:
                p.stop()
            except Exception:
                pass
            try:
                if _os.name == "nt":
                    p.set_hwnd(0)
                else:
                    p.set_xwindow(0)
            except Exception:
                pass
        except Exception:
            pass

    def closeEvent(self, event):
        self.stop_video()
        super().closeEvent(event)

    def _pull_latest_frame(self):
        if self._use_rtsp:
            return  # en modo RTSP VLC pinta solo; no hay pull de pixmaps
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
        # Calidad del directo: "auto" | "high" | "medium" | "low".
        # "auto" se resuelve UNA vez por nº de núcleos (sin monitoreo continuo).
        self._quality = "auto"

        self._setup_ui()
        self._setup_shortcuts()
        self._start_stream_signal.connect(self._do_start_stream)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._check_cameras_status)
        self._status_timer.start(5000)
        # El estado de grabación (●REC) cambia raramente; consultar /cameras/
        # cada 5s solo para eso era derrochador. Timer aparte cada 15s.
        self._rec_timer = QTimer(self)
        self._rec_timer.timeout.connect(self._refresh_recording_badges)
        self._rec_timer.start(15000)

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
        self.btn_help = QPushButton()
        self.btn_help.setIcon(icon("help"))
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
            ("1×1 (cámara grande)", 1),
            ("2×2 (4 cámaras)", 2),
            ("3×3 (9 cámaras)", 3),
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

        # Selector de CALIDAD del directo. "Auto" decide por CPU (una vez, sin
        # monitoreo continuo). Solo afecta al directo (no a grabación/IA).
        header.addWidget(QLabel("Calidad:"))
        self.cmb_quality = QComboBox()
        for text, val in [
            ("Auto", "auto"), ("Alta", "high"), ("Media", "medium"), ("Baja", "low"),
        ]:
            self.cmb_quality.addItem(text, val)
        self.cmb_quality.setCurrentIndex(0)
        self.cmb_quality.setToolTip(
            "Alta = sin recodificar (más calidad). Media/Baja = transcode "
            "(menos red/CPU del cliente). Auto = según los núcleos del equipo."
        )
        self.cmb_quality.currentIndexChanged.connect(self._on_quality_change)
        self.cmb_quality.setStyleSheet("""
            QComboBox {
                background-color: #1e293b; color: #f1f5f9;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 6px; padding: 6px 12px; min-width: 90px;
            }
        """)
        header.addWidget(self.cmb_quality)

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
        self.btn_prev = QPushButton("  Anterior")
        self.btn_prev.setIcon(icon("prev"))
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

        self.btn_next = QPushButton("Siguiente  ")
        self.btn_next.setIcon(icon("next"))
        self.btn_next.setStyleSheet(btn_style)
        self.btn_next.setToolTip("Página siguiente (Page Down)")
        self.btn_next.clicked.connect(self._on_next_page)
        header.addWidget(self.btn_next)

        # Modo TV: rota automáticamente entre páginas cada N segundos (ideal
        # para una pantalla de vigilancia desatendida). Botón conmutable.
        self.btn_tv = QPushButton("  Modo TV")
        self.btn_tv.setIcon(icon("live"))
        self.btn_tv.setCheckable(True)
        self.btn_tv.setStyleSheet(btn_style + """
            QPushButton:checked {
                background-color: #38bdf8; color: #0f172a; font-weight: bold;
            }
        """)
        self.btn_tv.setToolTip("Rota automáticamente entre páginas cada 10 s")
        self.btn_tv.toggled.connect(self._on_tv_toggle)
        header.addWidget(self.btn_tv)

        # Timer de rotación del modo TV (10 s por página).
        self._tv_timer = QTimer(self)
        self._tv_timer.setInterval(10000)
        self._tv_timer.timeout.connect(self._tv_advance)

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
        # Al cambiar de página se sale del modo maximizado (evita estado colgado).
        if getattr(self, "_maximized_key", None) is not None and not getattr(self, "_restoring", False):
            self._maximized_key = None
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
                stream_url = self._pick_stream_url(
                    _cam, stream_type, self._effective_quality()
                )
                widget = CameraWidget(
                    cam_id, label, stream_type=stream_type, stream_url=stream_url
                )
                widget.clicked.connect(self._on_camera_click)
                widget.double_clicked.connect(self._on_camera_double_click)
                widget.ptz_requested.connect(self._on_ptz_request)
                widget.snapshot_requested.connect(self._on_snapshot)
                widget.config_requested.connect(self._on_config_request)
                self._widget_cache[key] = widget

                # En modo RTSP (go2rtc) el widget arranca VLC solo; NO registramos
                # cliente MJPEG. En modo MJPEG, arrancamos el stream escalonado.
                if not widget._use_rtsp:
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
    # Modo TV (rotación automática de páginas)
    # ------------------------------------------------------------------
    def _on_tv_toggle(self, on: bool):
        if on:
            # Solo tiene sentido si hay más de una página.
            if self._get_page_count() <= 1:
                self.btn_tv.setChecked(False)
                return
            self._tv_timer.start()
        else:
            self._tv_timer.stop()

    def _tv_advance(self):
        """Avanza a la siguiente página, volviendo a la primera al final (loop)."""
        total = self._get_page_count()
        if total <= 1:
            self._tv_timer.stop()
            self.btn_tv.setChecked(False)
            return
        nxt = (self._current_page + 1) % total
        self._load_page(nxt)

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
    # Calidad del directo
    # ------------------------------------------------------------------
    def _effective_quality(self) -> str:
        """
        Resuelve la calidad concreta. "auto" se decide UNA vez por nº de núcleos
        (coste cero, sin monitoreo continuo): <4 → baja, 4-7 → media, ≥8 → alta.
        """
        if self._quality != "auto":
            return self._quality
        import os
        cores = os.cpu_count() or 4
        if cores < 4:
            return "low"
        if cores < 8:
            return "medium"
        return "high"

    def _pick_stream_url(self, cam, stream_type: str, quality: str) -> str:
        """URL de go2rtc para (cam, lente, calidad), con fallbacks."""
        urls = getattr(cam, "stream_urls", None) or {}
        key = stream_type if stream_type in ("l1", "l2") else "main"
        by_q = urls.get(key) or {}
        legacy = {
            "l1": getattr(cam, "stream_url_l1", None),
            "l2": getattr(cam, "stream_url_l2", None),
        }.get(stream_type)
        return (
            by_q.get(quality)
            or by_q.get("high")
            or legacy
            or (getattr(cam, "stream_url", "") or "")
        )

    def _on_quality_change(self, idx: int):
        new_q = self.cmb_quality.itemData(idx)
        if new_q == self._quality:
            return
        self._quality = new_q
        q = self._effective_quality()
        # IMPORTANTE: NO destruimos/recreamos widgets (eso crasheaba VLC). Solo
        # le pedimos a cada player que reproduzca la nueva URL de calidad
        # (VLC hace stop+set_media+play reutilizando la misma ventana).
        for (cam_id, stream_type), widget in list(self._widget_cache.items()):
            cam = next((c for c in self._camera_list if c.id == cam_id), None)
            if cam is None:
                continue
            widget.set_live_url(self._pick_stream_url(cam, stream_type, q))

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
        """Doble-clic = maximizar esa cámara a todo el grid (y restaurar)."""
        # ¿Ya hay una maximizada? Si es la misma, restaurar; si no, cambiar.
        maxed = getattr(self, "_maximized_key", None)
        target_key = None
        for (cid, stype), _w in self.cameras.items():
            if cid == camera_id:
                target_key = (cid, stype)
                break
        if target_key is None:
            return
        if maxed == target_key:
            self._restore_grid()
        else:
            self._maximize_camera(target_key)

    def _maximize_camera(self, key):
        """Oculta las demás y expande la cámara `key` a todo el grid."""
        self._maximized_key = key
        for k, widget in self.cameras.items():
            if k == key:
                self.grid.removeWidget(widget)
                self.grid.addWidget(widget, 0, 0,
                                    max(1, self._grid_cols), max(1, self._grid_cols))
                widget.show()
            else:
                widget.hide()

    def _restore_grid(self):
        """Vuelve a la rejilla normal de la página actual."""
        self._maximized_key = None
        self._load_page(self._current_page)

    def _on_ptz_request(self, camera_id: int):
        logger.info(f"PTZ solicitado para cámara {camera_id}")
        self.ptz_requested.emit(Camera(id=camera_id, name=""))

    def _on_snapshot(self, camera_id: int):
        widget = None
        for (cid, _stype), w in self.cameras.items():
            if cid == camera_id:
                widget = w
                break
        if not widget:
            return
        from PySide6.QtCore import QStandardPaths
        import time, os
        pictures_path = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        full_path = os.path.join(pictures_path, f"snapshot_{camera_id}_{int(time.time())}.png")

        ok = False
        # Modo RTSP (VLC): usar video_take_snapshot del player.
        vlc = getattr(widget, "_vlc", None)
        if vlc is not None:
            try:
                w = max(0, widget.lbl_video.width())
                vlc.player.video_take_snapshot(0, full_path, w, 0)
                ok = True  # VLC escribe el archivo de forma asíncrona
            except Exception as e:
                logger.error(f"snapshot VLC cam {camera_id}: {e}")
        # Modo MJPEG (pixmap): guardar el frame actual.
        elif widget.lbl_video.pixmap() and not widget.lbl_video.pixmap().isNull():
            ok = widget.lbl_video.pixmap().save(full_path)

        if ok:
            logger.info(f"Snapshot guardado: {full_path}")
            self._toast(f"Imagen guardada en {pictures_path}")
        else:
            self._toast("No se pudo capturar la imagen")

    def _toast(self, message: str):
        """Notificación breve no intrusiva (si la ventana la soporta)."""
        try:
            from desktop_app.src.ui.components.toast import show_toast
            show_toast(self, message)
        except Exception:
            logger.info(message)

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

    def _refresh_recording_badges(self):
        """Consulta qué cámaras están grabando para mostrar el ●REC."""
        if not self.cameras:
            return
        # Import diferido (mismo patrón que `config` en este archivo): api_client
        # no está importado a nivel de módulo.
        from desktop_app.src.services.api_client import api_client

        def on_cams(response):
            if not response.success:
                return
            rec_by_cam = {}
            for c in (response.data or []):
                ws = c.get("worker_status") or {}
                # 'recording' puede venir en worker_status o como flag de la cámara.
                rec = False
                if isinstance(ws, dict):
                    rec = bool(ws.get("recording") or ws.get("is_recording"))
                rec = rec or bool(c.get("is_recording"))
                rec_by_cam[c.get("id")] = rec
            for (cam_id, _stype), widget in self.cameras.items():
                widget.set_recording(rec_by_cam.get(cam_id, False))
        api_client.get("cameras/", on_cams)

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
            try:
                widget.stop_video()  # cierra VLC si estaba en modo RTSP/go2rtc
            except Exception:
                pass
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
        if hasattr(self, "_rec_timer") and self._rec_timer.isActive():
            self._rec_timer.stop()
        # Streams se mantienen vivos en background — el usuario eligió
        # "siempre vivos" en la configuración del modo paginación.

    def hideEvent(self, event):
        # Detener la rotación TV al salir de la vista (no rotar en background).
        if hasattr(self, "_tv_timer"):
            self._tv_timer.stop()
        if hasattr(self, "btn_tv"):
            self.btn_tv.setChecked(False)
        self.cleanup()
        super().hideEvent(event)

    def showEvent(self, event):
        if not self._status_timer.isActive():
            self._status_timer.start(5000)
        if hasattr(self, "_rec_timer") and not self._rec_timer.isActive():
            self._rec_timer.start(15000)
            self._refresh_recording_badges()  # actualización inmediata al entrar
        super().showEvent(event)

    def closeEvent(self, event):
        self._destroy_all_widgets()
        video_streamer.stop_all()
        if self._status_timer.isActive():
            self._status_timer.stop()
        super().closeEvent(event)
