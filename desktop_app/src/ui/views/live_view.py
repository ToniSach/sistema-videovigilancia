"""
Vista de cámaras en vivo (grid 2x2).
"""
import logging
from typing import List, Dict

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QGridLayout, QFrame, QMenu, QInputDialog, QPushButton
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QPixmap, QImage, QMouseEvent, QContextMenuEvent

from desktop_app.src.config import config
from desktop_app.src.models.camera import Camera
from desktop_app.src.services.video_streamer import video_streamer, Frame
from desktop_app.src.ui.components.glass_card import GlassCard

logger = logging.getLogger(__name__)


class CameraWidget(GlassCard):
    """Widget individual de cámara."""
    
    clicked = Signal(int)
    double_clicked = Signal(int)
    ptz_requested = Signal(int)
    snapshot_requested = Signal(int)
    config_requested = Signal(int)  # ✅ NUEVA SEÑAL
    
    def __init__(self, camera_id: int, camera_name: str, parent=None):
        super().__init__(parent, border_radius=8)
        
        self.camera_id = camera_id
        self.camera_name = camera_name
        self.is_maximized = False
        
        self.setMinimumSize(320, 240)
        
        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        
        # Label de video
        self.lbl_video = QLabel("Esperando video...")
        self.lbl_video.setAlignment(Qt.AlignCenter)
        self.lbl_video.setStyleSheet(f"""
            background-color: #000000;
            color: {config.THEME_TEXT_MUTED};
            border-radius: 6px;
            font-size: 12px;
        """)
        self.lbl_video.setMinimumHeight(180)
        layout.addWidget(self.lbl_video)
        
        # Info bar
        info_layout = QHBoxLayout()
        
        self.lbl_name = QLabel(camera_name)
        self.lbl_name.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-weight: bold; font-size: 11px;"
        )
        info_layout.addWidget(self.lbl_name)
        
        self.lbl_status = QLabel("●")
        self.lbl_status.setStyleSheet("color: #22c55e; font-size: 10px;")
        info_layout.addWidget(self.lbl_status, alignment=Qt.AlignRight)
        
        # 🔧 BOTÓN DE CONFIGURACIÓN
        self.btn_config = QPushButton("⚙")
        self.btn_config.setMaximumWidth(30)
        self.btn_config.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {config.THEME_TEXT_MUTED};
                border: none;
                font-size: 14px;
            }}
            QPushButton:hover {{
                color: {config.THEME_ACCENT};
            }}
        """)
        self.btn_config.clicked.connect(
            lambda: self.config_requested.emit(self.camera_id)
        )
        info_layout.addWidget(self.btn_config)
        
        layout.addLayout(info_layout)
        
        # Conectar señales video
        video_streamer.frame_updated.connect(self._on_frame)
    
    def _on_frame(self, frame: Frame):
        """Recibe frame del streamer."""
        if frame.camera_id == self.camera_id:
            w = self.lbl_video.width()
            h = self.lbl_video.height()
            scaled = frame.pixmap.scaled(
                w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.lbl_video.setPixmap(scaled)
            self.lbl_status.setStyleSheet("color: #22c55e;")
    
    def set_offline(self):
        """Marca como offline."""
        self.lbl_video.setText("Cámara offline")
        self.lbl_status.setStyleSheet("color: #ef4444;")
    
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.camera_id)
    
    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self.double_clicked.emit(self.camera_id)
    
    def contextMenuEvent(self, event: QContextMenuEvent):
        """Menú contextual."""
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 8px;
            }}
            QMenu::item {{
                padding: 6px 20px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
            }}
        """)
        
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
    """Vista principal de cámaras en vivo."""
    
    camera_selected = Signal(Camera)
    ptz_requested = Signal(Camera)
    camera_config_requested = Signal(int)  # ✅ NUEVA SEÑAL
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.cameras: Dict[int, CameraWidget] = {}
        self._setup_ui()
        
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._check_cameras_status)
        self._status_timer.start(5000)
    
    def _setup_ui(self):
        """Construye interfaz."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        
        # Header
        header = QHBoxLayout()
        
        self.lbl_title = QLabel("Cámaras en Vivo")
        self.lbl_title.setStyleSheet(f"""
            color: {config.THEME_TEXT};
            font-size: 20px;
            font-weight: bold;
        """)
        header.addWidget(self.lbl_title)
        header.addStretch()
        
        self.btn_layout = QLabel("Layout: 2x2")
        self.btn_layout.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED};"
        )
        header.addWidget(self.btn_layout)
        
        layout.addLayout(header)
        
        # Grid
        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        layout.addLayout(self.grid)
        
        layout.addStretch()
    
    def set_cameras(self, cameras: List[Camera], api_token: str):
        """Configura cámaras a mostrar."""
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        self.cameras.clear()
        
        for i, cam in enumerate(cameras[:4]):
            widget = CameraWidget(cam.id, cam.name)
            
            widget.clicked.connect(self._on_camera_click)
            widget.double_clicked.connect(self._on_camera_double_click)
            widget.ptz_requested.connect(self._on_ptz_request)
            widget.snapshot_requested.connect(self._on_snapshot)
            widget.config_requested.connect(self._on_config_request)  # ✅ NUEVO
            
            row = i // 2
            col = i % 2
            self.grid.addWidget(widget, row, col)
            self.cameras[cam.id] = widget
            
            video_streamer.set_base_url("http://localhost:5000/api/v1")
            video_streamer.start_stream(cam.id, api_token)
    
    def _on_camera_click(self, camera_id: int):
        logger.debug(f"Cámara seleccionada: {camera_id}")
    
    def _on_camera_double_click(self, camera_id: int):
        widget = self.cameras.get(camera_id)
        if widget:
            widget.is_maximized = not widget.is_maximized
    
    def _on_ptz_request(self, camera_id: int):
        logger.info(f"PTZ solicitado para cámara {camera_id}")
        self.ptz_requested.emit(Camera(id=camera_id, name=""))
    
    def _on_snapshot(self, camera_id: int):
        widget = self.cameras.get(camera_id)
        if widget and widget.lbl_video.pixmap():
            from PySide6.QtCore import QStandardPaths
            import time
            
            pictures_path = QStandardPaths.writableLocation(
                QStandardPaths.PicturesLocation
            )
            filename = f"snapshot_{camera_id}_{int(time.time())}.png"
            full_path = f"{pictures_path}/{filename}"
            
            widget.lbl_video.pixmap().save(full_path)
            logger.info(f"Snapshot guardado: {full_path}")
    
    def _on_config_request(self, camera_id: int):
        """Configuración de cámara."""
        logger.info(f"Configuración solicitada para cámara {camera_id}")
        self.camera_config_requested.emit(camera_id)
    
    def _check_cameras_status(self):
        for cam_id, widget in self.cameras.items():
            if not video_streamer.is_streaming(cam_id):
                widget.set_offline()
    
    def cleanup(self):
        video_streamer.stop_all()
        self._status_timer.stop()
    
    def showEvent(self, event):
        super().showEvent(event)
    
    def hideEvent(self, event):
        super().hideEvent(event)