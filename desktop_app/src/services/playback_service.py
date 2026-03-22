"""
Servicio de playback de grabaciones usando VLC.
"""
import logging
import os
import tempfile
from typing import Optional, Callable
from dataclasses import dataclass

import vlc
from PySide6.QtCore import QObject, Signal, QThread, QTimer

logger = logging.getLogger(__name__)


@dataclass
class PlaybackState:
    """Estado del reproductor."""
    is_playing: bool = False
    position: float = 0.0  # 0.0 - 1.0
    time: int = 0  # segundos
    duration: int = 0  # segundos
    speed: float = 1.0


class VLCPlayer(QObject):
    """Wrapper de VLC para Qt."""
    
    state_changed = Signal(PlaybackState)
    position_changed = Signal(float)  # 0.0 - 1.0
    time_changed = Signal(int)  # segundos
    ended = Signal()
    error = Signal(str)
    
    def __init__(self, config_options: list = None):
        super().__init__()
        
        # Inicializar VLC
        if config_options is None:
            config_options = ['--quiet', '--no-video-title-show']
        
        self.instance = vlc.Instance(config_options)
        self.player = self.instance.media_player_new()
        
        # Timer para actualizar posición
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_position)
        self._timer.start(100)  # 100ms
        
        self._current_media = None
        self._is_seeking = False
    
    def play_url(self, url: str):
        """Reproduce URL (http o file)."""
        try:
            self.player.stop()
            
            media = self.instance.media_new(url)
            media.add_option("network-caching=300")
            self.player.set_media(media)
            self._current_media = media
            
            result = self.player.play()
            if result == -1:
                self.error.emit("No se pudo iniciar reproducción")
            else:
                logger.info(f"Reproduciendo: {url}")
        except Exception as e:
            logger.error(f"Error reproduciendo URL: {e}")
            self.error.emit(str(e))
    
    def play_file(self, file_path: str):
        """Reproduce archivo local."""
        if not os.path.exists(file_path):
            self.error.emit(f"Archivo no existe: {file_path}")
            return
        
        self.play_url(f"file://{file_path}")
    
    def pause(self):
        """Pausa/Resume."""
        self.player.pause()
    
    def stop(self):
        """Detiene."""
        self.player.stop()
    
    def seek(self, position: float):
        """
        Seek a posición (0.0 - 1.0).
        """
        if self.player.is_seekable():
            self._is_seeking = True
            self.player.set_position(position)
            self._is_seeking = False
    
    def seek_time(self, seconds: int):
        """Seek a tiempo específico en segundos."""
        if self.player.is_seekable():
            self.player.set_time(seconds * 1000)  # VLC usa milisegundos
    
    def set_speed(self, speed: float):
        """Cambia velocidad (0.5, 1.0, 2.0, 4.0)."""
        self.player.set_rate(speed)
    
    def get_duration(self) -> int:
        """Duración en segundos."""
        length_ms = self.player.get_length()
        return length_ms // 1000 if length_ms > 0 else 0
    
    def get_time(self) -> int:
        """Tiempo actual en segundos."""
        time_ms = self.player.get_time()
        return time_ms // 1000 if time_ms > 0 else 0
    
    def set_hwnd(self, hwnd: int):
        """Establece ventana para renderizado (Windows)."""
        if os.name == 'nt':
            self.player.set_hwnd(hwnd)
    
    def set_xwindow(self, xid: int):
        """Establece ventana X11 (Linux)."""
        if os.name != 'nt':
            self.player.set_xwindow(xid)
    
    def _update_position(self):
        """Actualiza estado periódicamente."""
        if not self._is_seeking:
            try:
                state = PlaybackState(
                    is_playing=self.player.is_playing(),
                    position=self.player.get_position(),
                    time=self.get_time(),
                    duration=self.get_duration(),
                    speed=self.player.get_rate()
                )
                self.state_changed.emit(state)
                
                if state.position >= 0:
                    self.position_changed.emit(state.position)
                if state.time >= 0:
                    self.time_changed.emit(state.time)
                
                # Detectar fin
                if state.position >= 0.99 and not state.is_playing:
                    self.ended.emit()
                    
            except Exception as e:
                pass  # Ignorar errores menores durante updates


class DownloadThread(QThread):
    """Thread para descargar grabación antes de reproducir."""
    
    progress = Signal(int)  # 0-100
    finished_download = Signal(str)  # path
    error = Signal(str)
    
    def __init__(self, url: str, output_path: str, headers: dict = None):
        super().__init__()
        self.url = url
        self.output_path = output_path
        self.headers = headers or {}
        self._cancelled = False
    
    def run(self):
        try:
            import requests
            response = requests.get(self.url, headers=self.headers, stream=True, timeout=30)
            
            if response.status_code != 200:
                self.error.emit(f"HTTP {response.status_code}")
                return
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(self.output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if self._cancelled:
                        return
                    
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if total_size > 0:
                            progress = int((downloaded / total_size) * 100)
                            self.progress.emit(progress)
            
            self.finished_download.emit(self.output_path)
            
        except Exception as e:
            self.error.emit(str(e))
    
    def cancel(self):
        self._cancelled = True


class PlaybackService(QObject):
    """Servicio de playback con gestión de descargas."""
    
    download_progress = Signal(int)
    download_finished = Signal(str)
    download_error = Signal(str)
    
    def __init__(self):
        super().__init__()
        self.player = VLCPlayer()
        self._download_thread: Optional[DownloadThread] = None
        self._temp_dir = tempfile.gettempdir()
        self._current_recording_id: Optional[int] = None
    
    def play_recording(self, recording_id: int, api_url: str, token: str, 
                      local_file: Optional[str] = None):
        """
        Reproduce grabación.
        
        Si local_file existe, reproduce local.
        Si no, descarga y luego reproduce.
        """
        self._current_recording_id = recording_id
        
        if local_file and os.path.exists(local_file):
            self.player.play_file(local_file)
        else:
            # Descargar primero
            url = f"{api_url}/recordings/play/{recording_id}"
            output_path = os.path.join(self._temp_dir, f"recording_{recording_id}.mp4")
            
            headers = {"Authorization": f"Bearer {token}"}
            
            self._download_thread = DownloadThread(url, output_path, headers)
            self._download_thread.progress.connect(self.download_progress.emit)
            self._download_thread.finished_download.connect(self._on_download_finished)
            self._download_thread.error.connect(self.download_error.emit)
            self._download_thread.start()
    
    def _on_download_finished(self, path: str):
        self.download_finished.emit(path)
        self.player.play_file(path)
    
    def play_local_file(self, file_path: str):
        """Reproduce archivo local directamente."""
        self.player.play_file(file_path)
    
    def stop(self):
        """Detiene reproducción y descarga."""
        self.player.stop()
        if self._download_thread and self._download_thread.isRunning():
            self._download_thread.cancel()
            self._download_thread.wait(1000)
    
    def get_player(self) -> VLCPlayer:
        """Retorna instancia del player para conectar señales."""
        return self.player
    
    def cleanup(self):
        """Limpia archivos temporales."""
        self.stop()
        # Opcional: eliminar archivos temporales antiguos


# Instancia global
playback_service = PlaybackService()