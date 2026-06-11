"""
================================================================================
MÓDULO: ui.views.playback_view — Reproducción histórica de grabaciones (Pipeline #14)
================================================================================

PROPÓSITO
    Pantalla de REPRODUCCIÓN: permite recorrer las grabaciones de una cámara en
    una fecha dada, navegando por una línea de tiempo (timeline) con segmentos
    continuos (clips de ~2 min) y marcas de eventos. Reproduce, encadena
    segmentos automáticamente, salta a un instante exacto (desde un evento) y
    exporta el clip en curso.

RESPONSABILIDAD
    - Pedir el timeline del día al backend y materializarlo en `RecordingSegment`.
    - Coordinar tres piezas: el `TimelineWidget` (barra de segmentos + marcas de
      eventos), el `VideoPlayerWidget` (lienzo VLC) y el `playback_service`
      (singleton que descarga el clip y maneja el player VLC real).
    - Reproducción CONTINUA: al terminar un segmento encadena el siguiente
      (`_on_playback_ended`), de modo que "Play" recorre el día entero.
    - "Ver en playback" desde un evento: recibe (camera_id, datetime), carga el
      día y hace seek al segundo exacto del evento (`jump_to_time`).
    - Cámaras DUAL-LENS: la grabación es el stream COMBINADO (ambos lentes
      apilados). El selector de lente recorta en el CLIENTE (VLC) sin re-descargar
      ni reiniciar el segmento (`_on_lens_change`).
    - Controles de transporte (play/pausa/stop, slider, velocidad, atajos de
      teclado) y exportación a fichero.

DEPENDENCIAS
    - api_client (singleton): GET recordings/timeline (segmentos del día),
      GET events/ (marcas de eventos sobre el timeline), get_stream_token()
      (token go2rtc para autorizar la descarga del clip).
    - playback_service (singleton): descarga el clip y controla el player VLC;
      expone señales (state_changed, position_changed, time_changed, ended,
      error, download_progress/finished/error) y métodos play_recording / stop /
      set_lens / seek. Es la capa que realmente toca VLC.
    - Componentes UI: TimelineWidget, VideoPlayerWidget, GlassCard, HelpButton,
      icons.icon.
    - models.recording.RecordingSegment / TimelineDay (DTOs locales del JSON).

COMPONENTES RELACIONADOS
    main_window la instancia (índice VIEW_PLAYBACK=3 del content_stack) e inyecta
    las cámaras con `set_cameras`. El salto evento→reproducción llega vía
    `MainWindow._on_jump_to_playback` → `jump_to_time`. events_view es el origen
    de ese salto (emite `jump_to_playback`).

PUNTO DE ENTRADA (en la app)
    Sidebar «Reproducción» → MainWindow._switch_view(VIEW_PLAYBACK). O bien desde
    un evento: events_view.jump_to_playback → MainWindow → jump_to_time().

PIPELINE(S)
    #14 Reproducción: timeline (backend) → segmentos → playback_service descarga
    el clip → VideoPlayerWidget lo pinta con VLC → encadenado continuo de
    segmentos. (Auth #2 subyace: todas las peticiones van con JWT.)
================================================================================
"""
import logging
import os  # ← FALTABA ESTA IMPORTACIÓN
from datetime import datetime, timedelta
from typing import Optional

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                               QPushButton, QComboBox, QDateEdit, QSlider,
                               QFileDialog, QMessageBox, QProgressBar)
from PySide6.QtCore import Qt, Signal, QDate
from PySide6.QtGui import QIcon, QShortcut, QKeySequence

from desktop_app.src.config import config
from desktop_app.src.models.recording import RecordingSegment, TimelineDay
from desktop_app.src.services.api_client import api_client
from desktop_app.src.services.playback_service import playback_service
from desktop_app.src.ui.components.glass_card import GlassCard
from desktop_app.src.ui.components.timeline_widget import TimelineWidget
from desktop_app.src.ui.components.video_player import VideoPlayerWidget
from desktop_app.src.ui.icons import icon

logger = logging.getLogger(__name__)


class PlaybackView(QWidget):
    """Vista de reproducción histórica de grabaciones (Pipeline #14).

    RESPONSABILIDAD / ROL
        Página del content_stack que enlaza timeline + reproductor VLC +
        playback_service para recorrer las grabaciones de un día, con
        reproducción continua, salto a evento y recorte de lente dual.

    QUIÉN LA INSTANCIA
        main_window (índice VIEW_PLAYBACK=3). Recibe las cámaras por
        `set_cameras` y los saltos desde evento por `jump_to_time`.

    SEÑALES QT
        No define señales propias (es un sumidero). CONSUME las del
        `playback_service.get_player()` (state_changed, position_changed,
        time_changed, ended, error) y las de descarga del propio servicio
        (download_progress/finished/error), además de
        `TimelineWidget.segment_clicked`.

    ESTADO CLAVE
        - current_camera_id / current_date: contexto del timeline cargado.
        - segments: lista de RecordingSegment del día (orden cronológico).
        - _current_segment_index: segmento en reproducción (para encadenar).
        - _lens ("full"|"l1"|"l2") + _cam_dual: estado del recorte dual-lens.
        - _pending_seek / _pending_jump_dt: seek diferido al cargar (eventos).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.current_camera_id: Optional[int] = None
        self.current_date: Optional[str] = None
        self.segments: list = []
        # Índice del segmento que se está reproduciendo (para encadenar el
        # siguiente al terminar y para que Play arranque por el primero del día).
        self._current_segment_index: int = -1
        # Altura COMPLETA del vídeo combinado (sin recorte). Se cachea la
        # primera vez que se mide en pleno (full); el recorte de lente se
        # calcula SIEMPRE sobre esta altura, no sobre el tamaño ya recortado
        # (si no, recortaría "la mitad de la mitad").
        self._full_h: int = 0
        # Seek pendiente (segundos dentro del segmento) a aplicar cuando el
        # vídeo termine de cargar. Usado para "ver en playback" desde un evento.
        self._pending_seek: float = 0.0
        # Instante (datetime) al que saltar tras cargar el timeline (evento).
        self._pending_jump_dt: Optional[datetime] = None
        # id_cámara -> bool (es dual-lens). La grabación es el stream COMBINADO
        # (ambos lentes apilados verticalmente, -c copy nativo). El selector de
        # lente recorta en VLC (sin doble coste de grabación). Geometría coherente
        # con el split en vivo: l2=mitad superior, l1=mitad inferior.
        self._cam_dual: dict = {}
        self._lens = "full"  # "full" | "l1" | "l2"

        self._setup_ui()
        self._connect_signals()
        self._setup_shortcuts()

    def _setup_shortcuts(self):
        """Atajos: Espacio = play/pausa, ←/→ = ±10s."""
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self._toggle_play)
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=lambda: self._skip(-10))
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=lambda: self._skip(10))

    def _toggle_play(self):
        try:
            playback_service.player.player.pause()  # VLC pause() alterna play/pausa
        except Exception:
            pass

    def _skip(self, seconds: int):
        try:
            p = playback_service.player.player
            cur = p.get_time()  # ms
            if cur >= 0:
                p.set_time(max(0, cur + seconds * 1000))
        except Exception:
            pass

    def _setup_ui(self):
        """Construye interfaz."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Título + botón de ayuda
        title_row = QHBoxLayout()
        title_lbl = QLabel("Reproducción de grabaciones")
        title_lbl.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 22px; font-weight: bold;"
        )
        title_row.addWidget(title_lbl)
        try:
            from desktop_app.src.ui.components.help_button import HelpButton
            title_row.addWidget(HelpButton("playback_view", parent=self))
        except Exception:
            pass
        title_row.addStretch()
        layout.addLayout(title_row)

        # Banner explicativo
        info_banner = QLabel(
            "<i>Aquí ves todas las grabaciones de cada cámara: continuas "
            "(segmentos de 2 min sin parar) y de eventos. Elige cámara, "
            "fecha y pulsa «Cargar Timeline».</i>"
        )
        info_banner.setWordWrap(True)
        info_banner.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 11px; "
            f"padding: 8px 12px; background-color: rgba(56, 189, 248, 0.08); "
            f"border-left: 3px solid {config.THEME_ACCENT}; border-radius: 4px;"
        )
        layout.addWidget(info_banner)

        # Header controles
        controls = QHBoxLayout()
        
        # Selector de cámara
        self.cmb_camera = QComboBox()
        self.cmb_camera.setMinimumWidth(150)
        self.cmb_camera.setStyleSheet(f"""
            QComboBox {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 6px;
            }}
        """)
        controls.addWidget(QLabel("Cámara:"))
        controls.addWidget(self.cmb_camera)
        self.cmb_camera.currentIndexChanged.connect(self._on_camera_combo_change)

        # Selector de lente (solo cámaras dual-lens). Recorta la grabación
        # combinada en reproducción → "Lente 1" / "Lente 2" / "Completa".
        self.lbl_lens = QLabel("Lente:")
        self.cmb_lens = QComboBox()
        # "Completa" (vídeo con ambos lentes apilados) se quitó a propósito:
        # en dual-lens solo tiene sentido ver un lente a la vez.
        self.cmb_lens.addItem("Lente 1", "l1")
        self.cmb_lens.addItem("Lente 2", "l2")
        self.cmb_lens.setStyleSheet(self.cmb_camera.styleSheet())
        self.cmb_lens.currentIndexChanged.connect(self._on_lens_change)
        self.lbl_lens.setVisible(False)
        self.cmb_lens.setVisible(False)
        controls.addWidget(self.lbl_lens)
        controls.addWidget(self.cmb_lens)

        # Selector de fecha
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setMaximumDate(QDate.currentDate())
        self.date_edit.setStyleSheet(f"""
            QDateEdit {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 6px;
            }}
        """)
        controls.addWidget(QLabel("Fecha:"))
        controls.addWidget(self.date_edit)
        
        # Botón cargar
        self.btn_load = QPushButton("Cargar Timeline")
        self.btn_load.clicked.connect(self._load_timeline)
        controls.addWidget(self.btn_load)
        
        controls.addStretch()
        
        layout.addLayout(controls)
        
        # Área de video
        self.video_player = VideoPlayerWidget()
        self.video_player.setMinimumHeight(400)
        layout.addWidget(self.video_player)
        
        # Timeline
        self.timeline = TimelineWidget()
        self.timeline.segment_clicked.connect(self._on_segment_click)
        layout.addWidget(self.timeline)
        
        # Barra de progreso de descarga
        self.progress_download = QProgressBar()
        self.progress_download.setVisible(False)
        self.progress_download.setStyleSheet(f"""
            QProgressBar {{
                background-color: {config.THEME_SECONDARY};
                border-radius: 4px;
                color: {config.THEME_TEXT};
            }}
            QProgressBar::chunk {{
                background-color: {config.THEME_ACCENT};
                border-radius: 4px;
            }}
        """)
        layout.addWidget(self.progress_download)
        
        # Controles de playback
        playback_controls = QHBoxLayout()
        
        self.btn_play = QPushButton("  Reproducir")
        self.btn_play.setIcon(icon("play"))
        self.btn_pause = QPushButton("  Pausar")
        self.btn_pause.setIcon(icon("pause"))
        self.btn_stop = QPushButton("  Detener")
        self.btn_stop.setIcon(icon("stop"))
        
        for btn in [self.btn_play, self.btn_pause, self.btn_stop]:
            btn.setMinimumWidth(80)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {config.THEME_SECONDARY};
                    color: {config.THEME_TEXT};
                    border: 1px solid {config.GLASS_BORDER};
                    border-radius: 6px;
                    padding: 8px;
                }}
                QPushButton:hover {{
                    background-color: {config.THEME_ACCENT};
                    color: {config.THEME_PRIMARY};
                }}
            """)
            playback_controls.addWidget(btn)
        
        # Slider de posición
        self.slider_pos = QSlider(Qt.Horizontal)
        self.slider_pos.setRange(0, 1000)
        self.slider_pos.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background-color: {config.THEME_SECONDARY};
                height: 6px;
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background-color: {config.THEME_ACCENT};
                width: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{
                background-color: {config.THEME_ACCENT}.darker(120);
                border-radius: 3px;
            }}
        """)
        playback_controls.addWidget(self.slider_pos, stretch=1)
        
        # Velocidad
        self.cmb_speed = QComboBox()
        self.cmb_speed.addItems(["0.5x", "1x", "2x", "4x"])
        self.cmb_speed.setCurrentText("1x")
        self.cmb_speed.currentTextChanged.connect(self._on_speed_change)
        
        # Label de tiempo - CORREGIDO (sin walrus operator problemático)
        self.lbl_time = QLabel("00:00 / 00:00")
        playback_controls.addWidget(self.lbl_time)
        playback_controls.addWidget(self.cmb_speed)
        
        # Botón exportar
        self.btn_export = QPushButton("  Exportar")
        self.btn_export.setIcon(icon("export"))
        self.btn_export.clicked.connect(self._export_video)
        playback_controls.addWidget(self.btn_export)
        
        layout.addLayout(playback_controls)
        
        # Estado
        self.lbl_status = QLabel("Seleccione cámara y fecha para cargar grabaciones")
        self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")
        layout.addWidget(self.lbl_status)
    
    def _connect_signals(self):
        """Conecta señales del reproductor."""
        player = playback_service.get_player()
        player.state_changed.connect(self._on_player_state)
        player.position_changed.connect(self._on_position_changed)
        player.time_changed.connect(self._on_time_changed)
        player.ended.connect(self._on_playback_ended)
        player.error.connect(self._on_player_error)
        
        # Controles
        self.btn_play.clicked.connect(self._on_play_clicked)
        self.btn_pause.clicked.connect(lambda: playback_service.player.player.pause())
        self.btn_stop.clicked.connect(lambda: playback_service.stop())
        self.slider_pos.sliderReleased.connect(self._on_seek)
        
        # Descarga
        playback_service.download_progress.connect(self._on_download_progress)
        playback_service.download_finished.connect(self._on_download_finished)
        playback_service.download_error.connect(self._on_download_error)
    
    def set_cameras(self, cameras: list):
        """Rellena el selector de cámaras y memoriza cuáles son dual-lens.

        Inputs: `cameras` (lista de DTOs Camera con id, name, is_dual_lens).
        Outputs: combo de cámaras poblado; `_cam_dual` mapea id→bool.
        Llamado por: MainWindow._load_cameras (tras login y tras editar cámaras).
        """
        self.cmb_camera.clear()
        self._cam_dual.clear()
        for cam in cameras:
            self.cmb_camera.addItem(cam.name, cam.id)
            self._cam_dual[cam.id] = bool(getattr(cam, "is_dual_lens", False))
        self._on_camera_combo_change()

    def _on_camera_combo_change(self, *args):
        """Muestra el selector de lente solo si la cámara es dual-lens."""
        cam_id = self.cmb_camera.currentData()
        self._full_h = 0  # otra cámara → re-medir la altura completa
        is_dual = self._cam_dual.get(cam_id, False)
        self.lbl_lens.setVisible(is_dual)
        self.cmb_lens.setVisible(is_dual)
        if not is_dual:
            # Cámara mono: vista completa (sin recorte) — no hay lentes.
            self._lens = "full"
        else:
            # Dual-lens: ya no existe "Completa"; arrancar en Lente 1.
            self.cmb_lens.blockSignals(True)
            self.cmb_lens.setCurrentIndex(0)
            self.cmb_lens.blockSignals(False)
            self._lens = self.cmb_lens.currentData() or "l1"

    def _lens_param(self) -> Optional[str]:
        """Lente a pedir al backend ('l1'/'l2'), o None para el combinado."""
        return self._lens if self._lens in ("l1", "l2") else None

    def _on_lens_change(self, *args):
        """Cambia el lente recortando en el reproductor (lado CLIENTE, VLC) SIN
        re-descargar ni reiniciar el segmento. Antes se re-pedía el segmento
        entero al servidor, lo que CONGELABA la reproducción al cambiar de lente.
        El recorte se aplica al instante sobre el vídeo en curso."""
        self._lens = self.cmb_lens.currentData() or "full"
        playback_service.set_lens(self._lens_param())
    
    def _load_timeline(self):
        """Carga el timeline del día seleccionado y lo pinta.

        Propósito: pedir los segmentos de (cámara, fecha) al backend, ordenarlos
        cronológicamente, dibujarlos en el TimelineWidget y, si veníamos de un
        evento, disparar el seek pendiente.
        Outputs: `self.segments` poblado; timeline actualizado; estado en label.
        Señales: ninguna; usa callbacks async del api_client.
        Llamado por: botón «Cargar Timeline» y `jump_to_time`.
        Llama a: GET recordings/timeline?camera_id&date y `_load_event_markers`.
        """
        camera_id = self.cmb_camera.currentData()
        if not camera_id:
            return
        
        date = self.date_edit.date().toString("yyyy-MM-dd")
        self.current_camera_id = camera_id
        self.current_date = date
        
        self.lbl_status.setText("Cargando timeline...")
        
        def on_timeline(response):
            if response.success:
                segments_data = (response.data or {}).get("segments", []) or []
                # Construcción TOLERANTE: si un segmento llega incompleto o con
                # una fecha mal formada, se omite ese segmento en vez de romper
                # toda la carga del timeline.
                parsed = []
                for s in segments_data:
                    try:
                        if not isinstance(s, dict):
                            continue
                        start_raw = s.get("start")
                        if not start_raw:
                            continue  # sin inicio no se puede ubicar en la línea
                        end_raw = s.get("end")
                        parsed.append(RecordingSegment(
                            recording_id=s.get("recording_id"),
                            camera_id=camera_id,
                            start=datetime.fromisoformat(start_raw),
                            end=datetime.fromisoformat(end_raw) if end_raw else None,
                            duration_seconds=int(s.get("duration_seconds") or 0),
                            file_size_mb=float(s.get("file_size_mb") or 0),
                            has_clip=bool(s.get("has_clip", False)),
                        ))
                    except (ValueError, TypeError, KeyError) as e:
                        logger.warning(f"Segmento de grabación ignorado (datos inválidos): {e}")
                self.segments = parsed

                # Orden cronológico para que la reproducción continua avance
                # del segmento más antiguo al más reciente.
                self.segments.sort(key=lambda s: s.start)
                self._current_segment_index = -1  # reset al recargar timeline

                self.timeline.set_segments(self.segments)
                self.lbl_status.setText(
                    f"{len(self.segments)} segmentos. Pulsa Play para reproducir el día."
                )

                # Si venimos de "ver en playback" de un evento, saltar al
                # segmento que contiene ese instante y al segundo exacto.
                if self._pending_jump_dt is not None and self.segments:
                    self._seek_to_pending_event()
            else:
                self.lbl_status.setText(f"Error: {response.error}")

        api_client.get(f"recordings/timeline?camera_id={camera_id}&date={date}", on_timeline)
        # Cargar también los EVENTOS del día para marcarlos en la línea de tiempo.
        self._load_event_markers(camera_id, date)

    def _load_event_markers(self, camera_id: int, date: str):
        """Pinta marcas de detección sobre el timeline (puntos por evento)."""
        def on_events(response):
            if not response.success:
                return
            markers = []
            for ev in (response.data or []):
                if ev.get("camera_id") != camera_id:
                    continue
                ts = ev.get("created_at") or ev.get("timestamp")
                try:
                    d = datetime.fromisoformat(str(ts).replace("Z", "").split(".")[0])
                    if d.strftime("%Y-%m-%d") != date:
                        continue
                    secs = d.hour * 3600 + d.minute * 60 + d.second
                    markers.append((secs, ev.get("event_type", "")))
                except Exception:
                    continue
            self.timeline.set_event_markers(markers)
        # 168h cubre cualquier fecha reciente; filtramos por fecha arriba.
        api_client.get("events/", on_events, params={"hours": 168, "limit": 500,
                                                     "camera_id": camera_id})
    
    def _on_segment_click(self, recording_id: int, offset_seconds: int):
        """Click en segmento del timeline → reproduce ese segmento (y desde él
        seguirá encadenando los siguientes al terminar)."""
        idx = next(
            (i for i, s in enumerate(self.segments) if s.recording_id == recording_id),
            -1,
        )
        if idx >= 0:
            self._play_segment_index(idx)
        else:
            # Fallback: reproducir por id aunque no esté en la lista.
            self._current_segment_index = -1
            self.lbl_status.setText(f"Cargando grabación {recording_id}...")
            self.progress_download.setVisible(True)
            self.progress_download.setValue(0)
            token = api_client.get_stream_token() or ""
            playback_service.play_recording(
                recording_id, config.API_BASE_URL, token, lens=self._lens_param()
            )

    def _seek_to_pending_event(self):
        """Localiza el segmento que contiene `self._pending_jump_dt` y lo
        reproduce saltando al segundo del evento. Si ninguno lo contiene
        exactamente, usa el más cercano que empiece antes del evento."""
        when = self._pending_jump_dt
        self._pending_jump_dt = None
        if when is None:
            return
        # Normalizar a naive (los start/end del timeline son naive locales).
        try:
            if when.tzinfo is not None:
                when = when.replace(tzinfo=None)
        except Exception:
            pass

        target_idx = -1
        for i, s in enumerate(self.segments):
            start = s.start
            end = s.end or start
            if start <= when <= end:
                target_idx = i
                break
            if start <= when:
                target_idx = i  # último que empieza antes → candidato
        if target_idx < 0:
            target_idx = 0  # evento antes del primer segmento → primero

        seg = self.segments[target_idx]
        offset = 0.0
        try:
            offset = max(0.0, (when - seg.start).total_seconds())
            # No pasar del final del segmento.
            if seg.duration_seconds:
                offset = min(offset, max(0.0, seg.duration_seconds - 1))
        except Exception:
            offset = 0.0
        self._play_segment_index(target_idx, seek_seconds=offset)

    def _play_segment_index(self, idx: int, seek_seconds: float = 0.0):
        """Descarga y reproduce el segmento `idx` (núcleo de la reproducción).

        Inputs: `idx` (posición en self.segments), `seek_seconds` (posición a la
        que saltar al cargar; usado al venir de un evento).
        Outputs: fija `_current_segment_index` y `_pending_seek`; muestra progreso.
        Llamado por: `_on_play_clicked`, `_on_segment_click`, `_on_playback_ended`
        (encadenado) y `_seek_to_pending_event`.
        Llama a: `playback_service.play_recording(recording_id, base_url, token,
        lens=...)` — el servicio descarga el clip y arranca VLC.
        """
        if not (0 <= idx < len(self.segments)):
            return
        self._current_segment_index = idx
        self._pending_seek = max(0.0, seek_seconds)
        seg = self.segments[idx]
        self.progress_download.setVisible(True)
        self.progress_download.setValue(0)
        lens_txt = {"l1": " · Lente 1", "l2": " · Lente 2"}.get(self._lens, "")
        self.lbl_status.setText(
            f"Reproduciendo segmento {idx + 1}/{len(self.segments)}{lens_txt}…"
        )
        token = api_client.get_stream_token() or ""
        playback_service.play_recording(
            seg.recording_id, config.API_BASE_URL, token, lens=self._lens_param()
        )

    def _on_play_clicked(self):
        """
        Play: si no hay nada reproduciéndose aún, arranca por el PRIMER segmento
        del día; si ya hay un segmento cargado (pausado), reanuda.
        """
        if self._current_segment_index < 0:
            if self.segments:
                self._play_segment_index(0)
            else:
                self.lbl_status.setText("Carga el timeline: no hay grabaciones.")
            return
        try:
            playback_service.player.player.play()
        except Exception:
            pass
    
    def _on_download_progress(self, progress: int):
        """Actualiza progreso de descarga."""
        self.progress_download.setValue(progress)
    
    def _on_download_finished(self, path: str):
        """Archivo descargado."""
        self.progress_download.setVisible(False)
        from PySide6.QtCore import QTimer
        # El recorte del lente lo hace el SERVIDOR (la URL ya pidió ?lens=), así
        # que aquí no se recorta nada en el cliente.
        # Si hay un seek pendiente (evento), saltar a ese segundo una vez que
        # el vídeo es seekable (pequeño defer para que VLC cargue la duración).
        if self._pending_seek > 0:
            secs = self._pending_seek
            self._pending_seek = 0.0
            self.lbl_status.setText(f"Saltando al momento del evento ({int(secs)}s)…")
            QTimer.singleShot(700, lambda: playback_service.player.seek_time(int(secs)))
        else:
            self.lbl_status.setText("Reproduciendo…")

    def jump_to_time(self, camera_id: int, when: datetime):
        """Punto de entrada del salto evento→reproducción (Pipeline #14).

        Propósito: dado un evento, posicionar la reproducción en su instante
        exacto. Selecciona la cámara, fija la fecha del evento, recuerda el
        instante (`_pending_jump_dt`) y carga el timeline; al llegar los
        segmentos, `_seek_to_pending_event` localiza el segmento y hace seek.
        Inputs: `camera_id`, `when` (datetime del evento).
        Llamado por: `MainWindow._on_jump_to_playback` (diferido 200ms), que a su
        vez responde a `events_view.jump_to_playback`.
        Llama a: `_load_timeline`.
        """
        # Seleccionar cámara
        for i in range(self.cmb_camera.count()):
            if self.cmb_camera.itemData(i) == camera_id:
                self.cmb_camera.blockSignals(True)
                self.cmb_camera.setCurrentIndex(i)
                self.cmb_camera.blockSignals(False)
                self._on_camera_combo_change()
                break
        # Fijar la fecha del evento
        try:
            self.date_edit.setDate(QDate(when.year, when.month, when.day))
        except Exception:
            pass
        # Recordar el instante y cargar el timeline; al llegar los segmentos
        # se localiza el que contiene `when` y se reproduce con seek.
        self._pending_jump_dt = when
        self._load_timeline()
    
    def _on_download_error(self, error: str):
        """Error en descarga."""
        self.progress_download.setVisible(False)
        self.lbl_status.setText(f"Error de descarga: {error}")
    
    def _on_player_state(self, state):
        """Actualiza UI según estado del player."""
        pass  # Actualizar iconos si es necesario
    
    def _on_position_changed(self, position: float):
        """Actualiza slider de posición."""
        self.slider_pos.blockSignals(True)
        self.slider_pos.setValue(int(position * 1000))
        self.slider_pos.blockSignals(False)
    
    def _on_time_changed(self, seconds: int):
        """Actualiza display de tiempo."""
        duration = playback_service.player.get_duration()
        self.lbl_time.setText(f"{self._format_time(seconds)} / {self._format_time(duration)}")
        
        # Sincronizar con timeline
        # Convertir tiempo de video a segundos del día
        if self.segments:
            # Encontrar offset total
            total_offset = 0
            current_recording_id = getattr(playback_service, '_current_recording_id', None)
            for seg in self.segments:
                if seg.recording_id == current_recording_id:
                    total_offset += seconds
                    break
                total_offset += seg.duration_seconds
            
            # Actualizar línea del timeline
            # self.timeline.set_current_time(total_offset)
    
    def _on_seek(self):
        """Seek a posición."""
        position = self.slider_pos.value() / 1000.0
        playback_service.player.seek(position)
    
    def _on_speed_change(self, speed_text: str):
        """Cambia velocidad."""
        speed_map = {"0.5x": 0.5, "1x": 1.0, "2x": 2.0, "4x": 4.0}
        speed = speed_map.get(speed_text, 1.0)
        playback_service.player.set_speed(speed)
    
    def _on_playback_ended(self):
        """
        Fin de un segmento → encadena automáticamente el SIGUIENTE segmento del
        día (reproducción continua). Si era el último, termina.
        """
        next_idx = self._current_segment_index + 1
        if 0 <= self._current_segment_index and next_idx < len(self.segments):
            # Pequeño defer para no recrear el player dentro de su propio callback.
            from PySide6.QtCore import QTimer
            QTimer.singleShot(150, lambda: self._play_segment_index(next_idx))
        else:
            self.lbl_status.setText("Reproducción finalizada (fin del día)")
    
    def _on_player_error(self, error: str):
        """Error del player."""
        self.lbl_status.setText(f"Error: {error}")
    
    def _export_video(self):
        """Exporta video actual."""
        filename, _ = QFileDialog.getSaveFileName(
            self, "Guardar video", "", "Video files (*.mp4);;All files (*)"
        )
        if filename:
            # Copiar archivo temporal
            download_thread = getattr(playback_service, '_download_thread', None)
            temp_file = download_thread.output_path if download_thread else None
            
            if temp_file and os.path.exists(temp_file):
                import shutil
                shutil.copy(temp_file, filename)
                QMessageBox.information(self, "Exportar", "Video guardado correctamente")
            else:
                QMessageBox.warning(self, "Exportar", "No hay video descargado para exportar")
    
    def _format_time(self, seconds: int) -> str:
        """Formatea segundos a MM:SS."""
        m = seconds // 60
        s = seconds % 60
        return f"{m:02d}:{s:02d}"
    
    def cleanup(self):
        """Limpieza."""
        playback_service.cleanup()