"""
Widget contenedor de video. Provee la ventana nativa (QFrame.winId) para
que `playback_service.player` (VLC) renderice ahí.

ANTES creaba SU PROPIA instancia de VLC (independiente de playback_service)
→ había DOS reproductores: uno con la ventana pero sin medios, otro con
medios pero sin ventana. El usuario veía un panel negro aunque VLC sí
estuviera reproduciendo. Ahora el widget solo expone el QFrame; el VLC
único vive en playback_service.
"""
import logging
import platform
from typing import Optional

from PySide6.QtWidgets import QWidget, QVBoxLayout, QFrame
from PySide6.QtCore import Qt, QTimer

from desktop_app.src.config import config

logger = logging.getLogger(__name__)


class VideoPlayerWidget(QWidget):
    """Frame de video. La instancia VLC vive en playback_service."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"background-color: {config.THEME_PRIMARY};")

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # Frame contenedor (necesario para VLC: get winId() válido)
        self._video_frame = QFrame()
        self._video_frame.setStyleSheet(
            "background-color: black; border-radius: 8px;"
        )
        self._layout.addWidget(self._video_frame)

        # Registrar ESTA ventana en el VLC de playback_service tras
        # que el QFrame esté realmente creado en el toolkit nativo.
        # 100ms es suficiente en práctica; si la ventana cambia de tamaño,
        # resizeEvent vuelve a registrarla.
        QTimer.singleShot(150, self._bind_to_playback_service)

    def _bind_to_playback_service(self):
        """Conecta el VLC singleton de playback_service a este QFrame."""
        try:
            # Import diferido: evita ciclos y respeta la inicialización
            # lazy del playback_service.
            from desktop_app.src.services.playback_service import playback_service

            hwnd = int(self._video_frame.winId())
            if platform.system() == "Windows":
                playback_service.player.set_hwnd(hwnd)
            else:
                playback_service.player.set_xwindow(hwnd)
            logger.info(f"VideoPlayerWidget bindeado a VLC (hwnd={hwnd})")
        except Exception as e:
            logger.error(f"No se pudo bindear VLC al widget: {e}")

    def get_player(self):
        """Compat: devuelve el VLC del servicio (no uno propio)."""
        try:
            from desktop_app.src.services.playback_service import playback_service
            return playback_service.player.player
        except Exception:
            return None

    def play(self, url: str):
        """Compat: delega en el servicio."""
        try:
            from desktop_app.src.services.playback_service import playback_service
            playback_service.player.play_url(url)
        except Exception as e:
            logger.error(f"play() falló: {e}")

    def stop(self):
        try:
            from desktop_app.src.services.playback_service import playback_service
            playback_service.player.stop()
        except Exception:
            pass

    def resizeEvent(self, event):
        """Re-bindear si Qt nos da una winId distinta tras redimensionar."""
        super().resizeEvent(event)
        try:
            from desktop_app.src.services.playback_service import playback_service
            hwnd = int(self._video_frame.winId())
            if platform.system() == "Windows":
                playback_service.player.set_hwnd(hwnd)
            elif platform.system() != "Darwin":
                playback_service.player.set_xwindow(hwnd)
        except Exception:
            pass

    def cleanup(self):
        """No tenemos VLC propio que liberar; solo delegamos stop."""
        self.stop()
