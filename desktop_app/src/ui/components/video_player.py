"""
Widget de video basado en VLC.
"""
import logging
import platform
from typing import Optional

from PySide6.QtWidgets import QWidget, QVBoxLayout, QFrame
from PySide6.QtCore import Qt, QTimer

import vlc

from desktop_app.src.config import config

logger = logging.getLogger(__name__)


class VideoPlayerWidget(QWidget):
    """Widget contenedor para reproductor VLC."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"background-color: {config.THEME_PRIMARY};")
        
        # Layout
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        
        # Frame contenedor (necesario para VLC)
        self._video_frame = QFrame()
        self._video_frame.setStyleSheet(f"background-color: black; border-radius: 8px;")
        self._layout.addWidget(self._video_frame)
        
        # VLC setup se hace después de que el widget tenga ventana nativa
        self._vlc_instance: Optional[vlc.Instance] = None
        self._vlc_player: Optional[vlc.MediaPlayer] = None
        self._setup_timer = QTimer(self)
        self._setup_timer.timeout.connect(self._setup_vlc)
        self._setup_timer.setSingleShot(True)
        self._setup_timer.start(100)  # Esperar a que esté renderizado
    
    def _setup_vlc(self):
        """Inicializa VLC con ventana nativa."""
        try:
            self._vlc_instance = vlc.Instance(config.VLC_OPTIONS)
            self._vlc_player = self._vlc_instance.media_player_new()
            
            # Obtener handle nativo según plataforma
            if platform.system() == "Windows":
                self._vlc_player.set_hwnd(int(self._video_frame.winId()))
            elif platform.system() == "Darwin":  # macOS
                # macOS requiere tratamiento especial
                pass
            else:  # Linux
                self._vlc_player.set_xwindow(int(self._video_frame.winId()))
            
            logger.info("VLC inicializado correctamente")
            
        except Exception as e:
            logger.error(f"Error inicializando VLC: {e}")
    
    def get_player(self) -> Optional[vlc.MediaPlayer]:
        """Retorna player VLC para control externo."""
        return self._vlc_player
    
    def play(self, url: str):
        """Reproduce URL."""
        if self._vlc_player:
            media = self._vlc_instance.media_new(url)
            self._vlc_player.set_media(media)
            self._vlc_player.play()
    
    def stop(self):
        """Detiene reproducción."""
        if self._vlc_player:
            self._vlc_player.stop()
    
    def resizeEvent(self, event):
        """Asegurar que VLC se ajuste al redimensionar."""
        super().resizeEvent(event)
        if self._video_frame and self._vlc_player:
            if platform.system() == "Windows":
                self._vlc_player.set_hwnd(int(self._video_frame.winId()))
            elif platform.system() == "Linux":
                self._vlc_player.set_xwindow(int(self._video_frame.winId()))
    
    def cleanup(self):
        """Limpieza al cerrar."""
        if self._vlc_player:
            self._vlc_player.stop()
            self._vlc_player.release()
        if self._vlc_instance:
            self._vlc_instance.release()