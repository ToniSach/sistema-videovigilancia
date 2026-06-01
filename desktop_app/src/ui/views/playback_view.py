"""
Vista de playback de grabaciones con timeline.
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
    """Vista de reproducción de grabaciones."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.current_camera_id: Optional[int] = None
        self.current_date: Optional[str] = None
        self.segments: list = []
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
        self.cmb_lens.addItem("Completa", "full")
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
        
        self.btn_play = QPushButton("  Play")
        self.btn_play.setIcon(icon("play"))
        self.btn_pause = QPushButton("  Pause")
        self.btn_pause.setIcon(icon("pause"))
        self.btn_stop = QPushButton("  Stop")
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
        self.btn_play.clicked.connect(lambda: playback_service.player.player.play())
        self.btn_pause.clicked.connect(lambda: playback_service.player.player.pause())
        self.btn_stop.clicked.connect(lambda: playback_service.stop())
        self.slider_pos.sliderReleased.connect(self._on_seek)
        
        # Descarga
        playback_service.download_progress.connect(self._on_download_progress)
        playback_service.download_finished.connect(self._on_download_finished)
        playback_service.download_error.connect(self._on_download_error)
    
    def set_cameras(self, cameras: list):
        """Carga lista de cámaras."""
        self.cmb_camera.clear()
        self._cam_dual.clear()
        for cam in cameras:
            self.cmb_camera.addItem(cam.name, cam.id)
            self._cam_dual[cam.id] = bool(getattr(cam, "is_dual_lens", False))
        self._on_camera_combo_change()

    def _on_camera_combo_change(self, *args):
        """Muestra el selector de lente solo si la cámara es dual-lens."""
        cam_id = self.cmb_camera.currentData()
        is_dual = self._cam_dual.get(cam_id, False)
        self.lbl_lens.setVisible(is_dual)
        self.cmb_lens.setVisible(is_dual)
        if not is_dual:
            # Cámara mono: sin recorte y reset del selector.
            self._lens = "full"
            self.cmb_lens.blockSignals(True)
            self.cmb_lens.setCurrentIndex(0)
            self.cmb_lens.blockSignals(False)
            playback_service.player.set_crop(None)

    def _on_lens_change(self, *args):
        """Cambia el lente visualizado aplicando un recorte de VLC."""
        self._lens = self.cmb_lens.currentData() or "full"
        self._apply_lens_crop()

    def _apply_lens_crop(self):
        """Aplica el recorte del lente sobre la grabación combinada.

        La grabación dual contiene los dos lentes apilados verticalmente. El
        recorte se calcula a partir del tamaño real del vídeo (video_get_size),
        por lo que es independiente de la resolución nativa de la cámara.
        l2 = mitad superior, l1 = mitad inferior (coherente con el split en vivo).
        """
        if self._lens == "full":
            playback_service.player.set_crop(None)
            return
        w, h = playback_service.player.get_video_size()
        if not w or not h:
            # El vídeo aún no ha cargado; reintentar en breve.
            from PySide6.QtCore import QTimer
            QTimer.singleShot(300, self._apply_lens_crop)
            return
        half = h // 2
        if self._lens == "l2":      # mitad superior
            geom = f"{w}x{half}+0+0"
        else:                        # l1 → mitad inferior
            geom = f"{w}x{half}+0+{half}"
        playback_service.player.set_crop(geom)
    
    def _load_timeline(self):
        """Carga timeline desde backend."""
        camera_id = self.cmb_camera.currentData()
        if not camera_id:
            return
        
        date = self.date_edit.date().toString("yyyy-MM-dd")
        self.current_camera_id = camera_id
        self.current_date = date
        
        self.lbl_status.setText("Cargando timeline...")
        
        def on_timeline(response):
            if response.success:
                segments_data = response.data.get("segments", [])
                self.segments = [
                    RecordingSegment(
                        recording_id=s["recording_id"],
                        camera_id=camera_id,
                        start=datetime.fromisoformat(s["start"]),
                        end=datetime.fromisoformat(s["end"]) if s["end"] else None,
                        duration_seconds=s["duration_seconds"],
                        file_size_mb=s["file_size_mb"],
                        has_clip=s["has_clip"]
                    ) for s in segments_data
                ]
                
                self.timeline.set_segments(self.segments)
                self.lbl_status.setText(f"{len(self.segments)} segmentos encontrados")
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
        """Click en segmento del timeline."""
        self.lbl_status.setText(f"Cargando grabación {recording_id}...")
        self.progress_download.setVisible(True)
        self.progress_download.setValue(0)
        
        # Obtener token
        token = api_client.get_stream_token() or ""
        api_url = config.API_BASE_URL
        
        playback_service.play_recording(recording_id, api_url, token)
    
    def _on_download_progress(self, progress: int):
        """Actualiza progreso de descarga."""
        self.progress_download.setValue(progress)
    
    def _on_download_finished(self, path: str):
        """Archivo descargado."""
        self.progress_download.setVisible(False)
        self.lbl_status.setText(f"Reproduciendo: {path}")
        # Reaplicar el recorte del lente cuando el vídeo ya tenga tamaño válido.
        if self._lens != "full":
            from PySide6.QtCore import QTimer
            QTimer.singleShot(600, self._apply_lens_crop)
    
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
        """Fin de reproducción."""
        self.lbl_status.setText("Reproducción finalizada")
    
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