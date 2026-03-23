"""
Vista de cámaras en vivo (grid 2x2) - FIX Threading completo.
"""
import logging
from typing import List, Dict

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QGridLayout, QFrame, QMenu, QInputDialog, QPushButton
)
from PySide6.QtCore import Qt, Signal, QTimer, Slot, QThread
from PySide6.QtGui import QPixmap, QImage, QMouseEvent, QContextMenuEvent

from desktop_app.src.config import config
from desktop_app.src.models.camera import Camera
from desktop_app.src.services.video_streamer import video_streamer, Frame

logger = logging.getLogger(__name__)


class CameraWidget(QFrame):
    """Widget individual de cámara."""
    
    clicked = Signal(int)
    double_clicked = Signal(int)
    ptz_requested = Signal(int)
    snapshot_requested = Signal(int)
    config_requested = Signal(int)
    
    def __init__(self, camera_id: int, camera_name: str, parent=None):
        super().__init__(parent)
        
        self.camera_id = camera_id
        self.camera_name = camera_name
        self.is_maximized = False
        
        self.setMinimumSize(320, 240)
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        
        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        
        # Label de video
        self.lbl_video = QLabel("Esperando video...")
        self.lbl_video.setAlignment(Qt.AlignCenter)
        self.lbl_video.setStyleSheet("""
            background-color: #000000;
            color: #888888;
            border-radius: 4px;
            font-size: 12px;
        """)
        self.lbl_video.setMinimumHeight(180)
        layout.addWidget(self.lbl_video)
        
        # Info bar
        info_layout = QHBoxLayout()
        
        self.lbl_name = QLabel(camera_name)
        self.lbl_name.setStyleSheet("color: white; font-weight: bold; font-size: 11px;")
        info_layout.addWidget(self.lbl_name)
        
        self.lbl_status = QLabel("●")
        self.lbl_status.setStyleSheet("color: #22c55e; font-size: 10px;")
        info_layout.addWidget(self.lbl_status, alignment=Qt.AlignRight)
        
        # Botón de configuración
        self.btn_config = QPushButton("⚙")
        self.btn_config.setMaximumWidth(30)
        self.btn_config.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888888;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover {
                color: #38bdf8;
            }
        """)
        self.btn_config.clicked.connect(
            lambda: self.config_requested.emit(self.camera_id)
        )
        info_layout.addWidget(self.btn_config)
        
        layout.addLayout(info_layout)
        
        # Conectar señal de frame
        video_streamer.frame_updated.connect(
            self._on_frame, 
            type=Qt.QueuedConnection
        )
    
    @Slot(Frame)
    def _on_frame(self, frame: Frame):
        """Recibe frame del streamer."""
        if frame.camera_id != self.camera_id:
            return
            
        try:
            w = self.lbl_video.width()
            h = self.lbl_video.height()
            
            if w <= 0 or h <= 0:
                return
                
            scaled = frame.pixmap.scaled(
                w, h, 
                Qt.KeepAspectRatio, 
                Qt.SmoothTransformation
            )
            
            if scaled.isNull():
                return
                
            self.lbl_video.setPixmap(scaled)
            self.lbl_status.setStyleSheet("color: #22c55e;")
            
        except Exception as e:
            logger.error(f"Error mostrando frame: {e}")
    
    def disconnect_signals(self):
        """Desconecta señales de forma segura antes de destruir."""
        try:
            video_streamer.frame_updated.disconnect(self._on_frame)
        except (TypeError, RuntimeError):
            pass
    
    def set_offline(self):
        """Marca como offline."""
        self.lbl_video.setText("Cámara offline")
        self.lbl_status.setStyleSheet("color: #ef4444;")
    
    def closeEvent(self, event):
        """Limpieza al cerrar."""
        self.disconnect_signals()
        super().closeEvent(event)
    
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.camera_id)
    
    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self.double_clicked.emit(self.camera_id)
    
    def contextMenuEvent(self, event: QContextMenuEvent):
        """Menú contextual."""
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
    """Vista principal de cámaras en vivo."""
    
    camera_selected = Signal(Camera)
    ptz_requested = Signal(Camera)
    camera_config_requested = Signal(int)
    
    # FIX: Señal para marshalling al thread principal
    _start_stream_signal = Signal(int, str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.cameras: Dict[int, CameraWidget] = {}
        self._camera_list: List[Camera] = []
        self._restart_timer = None
        self._setup_ui()
        
        # Conectar señal de marshalling al slot
        self._start_stream_signal.connect(self._do_start_stream)
        
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
        self.lbl_title.setStyleSheet("""
            color: #f1f5f9;
            font-size: 20px;
            font-weight: bold;
        """)
        header.addWidget(self.lbl_title)
        header.addStretch()
        
        self.btn_layout = QLabel("Layout: 2x2")
        self.btn_layout.setStyleSheet("color: #94a3b8;")
        header.addWidget(self.btn_layout)
        
        layout.addLayout(header)
        
        # Grid
        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        layout.addLayout(self.grid)
        
        layout.addStretch()
    
    def set_cameras(self, cameras: List[Camera], api_token: str):
        """Configura cámaras a mostrar."""
        self._camera_list = cameras
        
        # Detener streams y limpiar
        self.cleanup()
        
        # Limpiar grid
        while self.grid.count():
            item = self.grid.takeAt(0)
            if widget := item.widget():
                if isinstance(widget, CameraWidget):
                    widget.disconnect_signals()
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        
        # Procesar eventos pendientes
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()
        
        self.cameras.clear()
        
        # Crear widgets y programar inicio
        for i, cam in enumerate(cameras[:4]):
            widget = CameraWidget(cam.id, cam.name)
            
            widget.clicked.connect(self._on_camera_click)
            widget.double_clicked.connect(self._on_camera_double_click)
            widget.ptz_requested.connect(self._on_ptz_request)
            widget.snapshot_requested.connect(self._on_snapshot)
            widget.config_requested.connect(self._on_config_request)
            
            row = i // 2
            col = i % 2
            self.grid.addWidget(widget, row, col)
            self.cameras[cam.id] = widget
            
            # Usar singleShot desde el thread actual (debería ser principal)
            QTimer.singleShot(i * 500, lambda c=cam, t=api_token: self._start_stream(c.id, t))
    
    def restart_streams(self, api_token: str):
        """Reinicia los streams con un nuevo token."""
        logger.info("Reiniciando streams de video...")
        
        if self._restart_timer:
            self._restart_timer.stop()
            self._restart_timer.deleteLater()
        
        video_streamer.stop_all()
        
        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.timeout.connect(lambda: self.set_cameras(self._camera_list, api_token))
        self._restart_timer.start(500)
    
    def _start_stream(self, camera_id: int, api_token: str):
        """Inicia stream (verifica thread y hace marshalling si es necesario)."""
        if QThread.currentThread() != self.thread():
            logger.debug(f"Reprogramando stream {camera_id} al thread principal")
            # Emitir señal para ejecutar en thread principal
            self._start_stream_signal.emit(camera_id, api_token)
            return
        
        # Si ya estamos en thread principal, ejecutar directo
        self._do_start_stream(camera_id, api_token)
    
    @Slot(int, str)
    def _do_start_stream(self, camera_id: int, api_token: str):
        """Ejecución real del inicio de stream (siempre en thread principal)."""
        video_streamer.set_base_url("http://localhost:5000/api/v1")
        video_streamer.start_stream(camera_id, api_token)
    
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
            
            if widget.lbl_video.pixmap().save(full_path):
                logger.info(f"Snapshot guardado: {full_path}")
    
    def _on_config_request(self, camera_id: int):
        logger.info(f"Configuración solicitada para cámara {camera_id}")
        self.camera_config_requested.emit(camera_id)
    
    def _check_cameras_status(self):
        for cam_id, widget in self.cameras.items():
            if not video_streamer.is_streaming(cam_id):
                widget.set_offline()
    
    def cleanup(self):
        """Limpieza segura."""
        if QThread.currentThread() != self.thread():
            # FIX: Usar señal para ejecutar en thread principal en lugar de invokeMethod
            logger.debug("Reprogramando cleanup al thread principal")
            QTimer.singleShot(0, self._do_cleanup)
            return
        
        self._do_cleanup()
    
    def _do_cleanup(self):
        """Limpieza real (siempre ejecutada en thread principal)."""
        video_streamer.stop_all()
        if self._status_timer.isActive():
            self._status_timer.stop()
    
    def hideEvent(self, event):
        """Al ocultar, detener streams."""
        self.cleanup()
        super().hideEvent(event)
    
    def closeEvent(self, event):
        """Limpieza final."""
        self.cleanup()
        super().closeEvent(event)