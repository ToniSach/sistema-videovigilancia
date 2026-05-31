"""
[DEPRECADO] Cliente de streaming MJPEG.

Antes contenía `MJPEGThread` + `VideoStreamerService`, que consumían el endpoint
`/api/v1/cameras/<id>/stream` (MJPEG por HTTP) y decodificaban frames a QPixmap.

El directo se MIGRÓ a go2rtc (WebRTC/RTSP reproducido con VLC en
`ui/components/rtsp_video.py` y `live_view.CameraWidget`). El backend ya no
expone MJPEG. Por eso la implementación MJPEG se eliminó.

Queda este SHIM no-op para no romper los imports de las vistas que todavía lo
referencian (live_view, main_window). Sus métodos no hacen nada; el directo lo
maneja VLC/go2rtc. Se puede borrar por completo cuando se refactorice live_view
para quitar las llamadas residuales.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap


@dataclass
class Frame:
    """Compat: frame mínimo (ya no se usa para render; VLC pinta directo)."""
    camera_id: int = 0
    stream_type: str = "main"
    pixmap: Optional[QPixmap] = None


class VideoStreamerService(QObject):
    """
    Shim no-op. La reproducción en vivo la hace VLC/go2rtc (RtspVideoWidget /
    CameraWidget). Estos métodos existen solo por compatibilidad de imports.
    """

    frame_updated = Signal(object)

    def set_base_url(self, url: str) -> None:
        pass

    def start_stream(self, camera_id: int, token: str, stream_type: str = "main") -> None:
        pass

    def stop_stream(self, camera_id: int, stream_type: str = "main") -> None:
        pass

    def stop_all(self) -> None:
        pass

    def is_streaming(self, camera_id: int, stream_type: str = "main") -> bool:
        # True para que el chequeo de estado no marque "offline" a los paneles
        # que en realidad reproduce VLC.
        return True

    def pop_latest_pixmap(
        self, camera_id: int, stream_type: str, last_seq: int
    ) -> Tuple[Optional[QPixmap], int, int]:
        # Sin frames MJPEG: el modo RTSP/VLC no usa pull de pixmaps.
        return (None, last_seq, 0)


# Singleton de compatibilidad.
video_streamer = VideoStreamerService()
