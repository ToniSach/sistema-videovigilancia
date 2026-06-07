"""
Servicio de playback de grabaciones usando VLC.
"""
import logging
import os
import tempfile
import threading
from typing import Optional, Callable
from dataclasses import dataclass

import vlc
from PySide6.QtCore import QObject, Signal, QThread, QTimer

logger = logging.getLogger(__name__)


# ── Instancia VLC ÚNICA compartida ──────────────────────────────────────────
# Tener varias vlc.Instance() con salida de vídeo por HWND en Windows provoca
# crashes nativos (la app "se cierra sola"). Compartimos una sola instancia
# para todos los reproductores; las opciones específicas van por media.
_shared_vlc_instance = None


def _get_shared_vlc_instance():
    global _shared_vlc_instance
    if _shared_vlc_instance is None:
        _shared_vlc_instance = vlc.Instance(["--quiet", "--no-video-title-show"])
    return _shared_vlc_instance


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
    first_frame = Signal()  # emitido cuando aparece el primer frame de video (vout)

    def __init__(self, config_options: list = None):
        super().__init__()

        # UNA sola vlc.Instance COMPARTIDA por todos los reproductores
        # (directo l1/l2, control, grabaciones). Crear varias vlc.Instance con
        # render por HWND en Windows provoca crashes nativos al reproducir.
        # Las opciones por-reproductor (network-caching, rtsp-tcp...) se aplican
        # a nivel de MEDIA en play_url, no a nivel de instancia.
        self.instance = _get_shared_vlc_instance()
        self.player = self.instance.media_player_new()

        # Opciones de media derivadas de config_options (sin las de instancia).
        self._media_opts: list[str] = []
        for opt in (config_options or []):
            o = opt.lstrip("-").strip()
            if not o or o in ("quiet",) or o.startswith("no-video-title-show"):
                continue  # ya están a nivel de instancia compartida
            self._media_opts.append(o)  # ej: "network-caching=150", "rtsp-tcp"

        # Timer LAZY: se crea cuando arranca la primera reproducción,
        # NO en __init__. Motivo: VLCPlayer() puede instanciarse al
        # importar este módulo (cuando se hace `playback_service = ...`
        # a nivel de archivo), lo cual ocurre ANTES de QApplication.
        # Crear un QTimer sin QApplication viva ya causó crashes y el
        # warning "QObject::startTimer: Timers can only be used with
        # threads started with QThread".
        self._timer: Optional[QTimer] = None
        self._current_media = None
        self._is_seeking = False
        # Lock para serializar swaps de media en hilo de fondo (evita que dos
        # cambios de calidad solapados llamen stop()/play() a la vez).
        self._swap_lock = threading.Lock()
        self._swap_thread: Optional[threading.Thread] = None
        # True mientras play_url_async hace stop()+play() en el hilo de fondo.
        # libVLC player.stop() retiene un mutex interno; si el hilo de UI llama
        # a video_set_aspect_ratio()/audio_* en ese momento, SE BLOQUEA esperando
        # ese mutex → la app "no responde". Los widgets deben consultar
        # is_swapping() antes de tocar el player y reintentar luego.
        self._swapping = False
        self._vout_attached = False
        self._last_play_t = 0.0

    def _ensure_timer(self):
        """Crea el QTimer en el primer uso (con QApplication ya viva)."""
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._update_position)
            self._timer.start(100)  # 100ms
    
    def play_url(self, url: str):
        """Reproduce URL (http o file). Síncrono (úsalo solo en el arranque
        inicial del stream; para CAMBIAR de fuente en vivo usa play_url_async,
        porque player.stop() es bloqueante y congelaría la UI)."""
        try:
            self._ensure_timer()  # crear QTimer ahora que QApplication existe
            self._do_play(url)
        except Exception as e:
            logger.error(f"Error reproduciendo URL: {e}")
            self.error.emit(str(e))

    def _do_play(self, url: str):
        """Núcleo de reproducción (stop+set_media+play). Lo comparten play_url
        y play_url_async. OJO: player.stop() es BLOQUEANTE en libVLC 3."""
        import time as _time
        import vlc as _vlc
        _t0 = _time.perf_counter()
        self.player.stop()
        _stop_ms = (_time.perf_counter() - _t0) * 1000.0
        media = self.instance.media_new(url)
        # Opciones a nivel de media (por reproductor). Si no se especificó
        # network-caching, usar 300ms por defecto (VOD).
        if not any(o.startswith("network-caching") for o in self._media_opts):
            media.add_option(":network-caching=300")
        for o in self._media_opts:
            media.add_option(f":{o}")
        self.player.set_media(media)
        self._current_media = media
        # DEBUG TTFF: registra cuándo aparece el primer frame (evento Vout) para
        # ver dónde se van los segundos (stop vs arranque del transcoder/keyframe).
        # Se engancha UNA sola vez por player (evita acumular handlers).
        if not self._vout_attached:
            try:
                _em = self.player.event_manager()

                def _on_vout(_ev):
                    dt = (_time.perf_counter() - self._last_play_t) * 1000.0
                    logger.info(f"[TTFF] primer frame en {dt:.0f}ms")
                    try:
                        self.first_frame.emit()
                    except Exception:
                        pass

                _em.event_attach(_vlc.EventType.MediaPlayerVout, _on_vout)
                self._vout_attached = True
            except Exception:
                pass
        self._last_play_t = _time.perf_counter()
        result = self.player.play()
        if result == -1:
            self.error.emit("No se pudo iniciar reproducción")
        else:
            logger.info(f"Reproduciendo: {url} (stop previo={_stop_ms:.0f}ms)")

    def play_url_async(self, url: str):
        """
        Igual que play_url pero ejecuta stop()+set_media()+play() en un hilo de
        fondo. CRÍTICO para el DIRECTO: en libVLC 3 `player.stop()` es BLOQUEANTE
        (espera a que el decoder RTSP/TCP termine, segundos); en el hilo UI y
        repetido por panel (l1+l2) congelaba la app al cambiar de calidad. No
        toca widgets Qt (solo emite señales, que Qt encola de forma segura).
        Serializado por _swap_lock para que dos cambios seguidos no se pisen.
        """
        def _worker():
            with self._swap_lock:
                self._swapping = True
                try:
                    self._do_play(url)
                except Exception as e:
                    logger.error(f"play_url_async: {e}")
                    self.error.emit(str(e))
                finally:
                    self._swapping = False
        self._swap_thread = threading.Thread(
            target=_worker, name="VLCSwap", daemon=True
        )
        self._swap_thread.start()

    def is_swapping(self) -> bool:
        """True si hay un stop()+play() en curso en el hilo de fondo. Los widgets
        deben evitar llamar métodos del player (aspect ratio, audio) mientras sea
        True para no bloquear el hilo de UI en el mutex interno de libVLC."""
        return self._swapping
    
    def play_file(self, file_path: str):
        """Reproduce archivo local."""
        if not os.path.exists(file_path):
            self.error.emit(f"Archivo no existe: {file_path}")
            return
        # Path.as_uri() genera file:///C:/... correcto en Windows. Antes se usaba
        # f"file://{path}" → file://C:\... (URI malformada) y VLC no abría el
        # archivo (panel negro aunque el .mp4 estuviera descargado).
        from pathlib import Path
        self.play_url(Path(file_path).as_uri())

    def get_video_size(self):
        """(w, h) del vídeo en reproducción, o (0, 0) si no disponible aún."""
        try:
            return self.player.video_get_size(0)
        except Exception:
            return (0, 0)

    def set_crop(self, geometry):
        """Recorte de VLC ('WxH+X+Y' o None para quitarlo). Usado por lente."""
        try:
            self.player.video_set_crop_geometry(geometry)
        except Exception:
            pass
    
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
                      local_file: Optional[str] = None, lens: Optional[str] = None):
        """
        Reproduce grabación.

        Si local_file existe, reproduce local.
        Si no, descarga y luego reproduce.

        lens: "l1"/"l2" para recortar un lente de una grabación dual-lens (la
        grabación es el frame completo); None/"main" = sin recorte.
        """
        self._current_recording_id = recording_id
        self._pending_lens = lens  # se aplica el recorte tras cargar el vídeo

        if local_file and os.path.exists(local_file):
            self.player.play_file(local_file)
            self._schedule_lens_crop()
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

    def set_lens(self, lens: Optional[str]):
        """Cambia el lente recortado SIN re-descargar ni reiniciar el vídeo.
        El recorte es del lado CLIENTE (VLC video_set_crop_geometry), así que
        basta re-aplicarlo al vídeo en curso. Re-pedir el segmento completo al
        cambiar de lente durante la reproducción congelaba el reproductor."""
        self._pending_lens = lens
        self._apply_lens_crop()

    def _schedule_lens_crop(self):
        """Programa la aplicación del recorte de lente cuando el vídeo cargue."""
        QTimer.singleShot(600, self._apply_lens_crop)

    def _apply_lens_crop(self, _tries: int = 0):
        """Recorta el lente (l1=mitad inferior, l2=mitad superior) en grabaciones
        dual-lens. Necesita el tamaño del vídeo, que tarda en estar disponible →
        reintenta unas pocas veces. lens None/main → quita el recorte."""
        lens = getattr(self, "_pending_lens", None)
        try:
            if not lens or lens not in ("l1", "l2"):
                self.set_crop(None)
                return
            w, h = self.get_video_size()
            if not w or not h:
                if _tries < 10:
                    QTimer.singleShot(300, lambda: self._apply_lens_crop(_tries + 1))
                return
            half = h // 2
            # l1 = mitad inferior (offset y=half), l2 = mitad superior (y=0).
            top = 0 if lens == "l2" else half
            self.set_crop(f"{w}x{half}+0+{top}")
        except Exception as e:
            logger.debug(f"_apply_lens_crop: {e}")

    def _on_download_finished(self, path: str):
        self.download_finished.emit(path)
        self.player.play_file(path)
        self._schedule_lens_crop()
    
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