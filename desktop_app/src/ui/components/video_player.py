"""
================================================================================
MÓDULO: ui.components.video_player — Superficie de vídeo para REPRODUCCIÓN (VOD)
================================================================================

PROPÓSITO
    Widget contenedor de vídeo que provee la ventana nativa (QFrame.winId)
    sobre la que el VLC singleton de `playback_service` renderiza las
    grabaciones (VOD). NO crea ni posee instancia de VLC propia: solo expone
    una superficie y la enlaza al reproductor compartido.

RESPONSABILIDAD
    - Crear un QFrame con ventana nativa real (WA_NativeWindow) → winId() válido.
    - Enlazar (set_hwnd/set_xwindow) ese winId al `playback_service.player`
      cuando la pestaña se hace visible y tras redimensionar.
    - Delegar play/stop en el servicio (NO mantiene estado de reproducción).

DISTINCIÓN CLAVE vs. rtsp_video.py
    - rtsp_video.py = DIRECTO en vivo (Pipeline #3); crea SU PROPIO VLCPlayer
      con flags de baja latencia y reproduce el restream RTSP de go2rtc.
    - video_player.py = REPRODUCCIÓN/VOD (Pipeline #14); NO tiene VLC propio,
      comparte el VLC único de playback_service (que también controla la
      timeline, seek, velocidad, etc.).

POR QUÉ NO HAY VLC PROPIO (historia)
    Antes este widget creaba su PROPIA instancia de VLC (independiente de
    playback_service) → había DOS reproductores: uno con la ventana pero sin
    medios, otro con medios pero sin ventana. El usuario veía un panel negro
    aunque VLC sí estuviera reproduciendo. Ahora el widget solo expone el
    QFrame; el VLC único vive en playback_service.

DEPENDENCIAS
    PySide6 (QWidget/QFrame), playback_service (VLC singleton, import diferido
    para evitar ciclos), config (color de fondo del tema).

COMPONENTES RELACIONADOS
    services/playback_service.py (dueño del VLC + lógica de seek/velocidad),
    timeline_widget.py (barra de tiempo que dirige el seek), rtsp_video.py
    (su homólogo para el DIRECTO).

DÓNDE SE USA
    Lo instancia la vista de Reproducción (ui/views/playback_view) como panel
    central de vídeo.
================================================================================
"""
import logging
import platform
from typing import Optional

from PySide6.QtWidgets import QWidget, QVBoxLayout, QFrame
from PySide6.QtCore import Qt, QTimer

from desktop_app.src.config import config

logger = logging.getLogger(__name__)


class VideoPlayerWidget(QWidget):
    """
    Superficie (QFrame) de vídeo para reproducción VOD. La instancia VLC vive
    en playback_service; este widget solo aporta la ventana donde pinta.

    Rol: panel central de vídeo de la vista de Reproducción.

    Quién la instancia/consume:
        ui/views/playback_view la crea y la coloca; el seek/velocidad lo dirige
        la timeline_widget a través de playback_service, no este widget.

    Señales Qt: no emite ni recibe señales propias (es un contenedor pasivo;
        toda la lógica de reproducción la lleva playback_service).

    Dependencias: playback_service.player (VLCPlayer compartido), config.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"background-color: {config.THEME_PRIMARY};")

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # Frame contenedor (necesario para VLC: get winId() válido).
        # WA_NativeWindow fuerza una ventana nativa real → winId() válido y VLC
        # NO abre una ventana flotante ni pinta en una ventana inexistente.
        self._video_frame = QFrame()
        self._video_frame.setAttribute(Qt.WA_NativeWindow, True)
        self._video_frame.setStyleSheet(
            "background-color: black; border-radius: 8px;"
        )
        self._layout.addWidget(self._video_frame)

        # El enlace real se hace en showEvent (cuando la pestaña es VISIBLE y el
        # winId ya es una ventana válida). Antes se hacía a los 150ms del
        # arranque, cuando la pestaña de Reproducción aún no se había mostrado →
        # VLC quedaba enlazado a una ventana no realizada y no se veía nada.

    def showEvent(self, event):
        """Enlaza VLC cuando la pestaña se hace VISIBLE (winId ya válido).

        Se difiere 50ms porque, justo en el primer showEvent, el QFrame puede
        no tener todavía su ventana nativa realizada; un pequeño retardo
        garantiza un winId() válido.
        """
        super().showEvent(event)
        QTimer.singleShot(50, self._bind_to_playback_service)

    def _bind_to_playback_service(self):
        """Conecta el VLC singleton de playback_service a este QFrame.

        Propósito: pasar el winId() del QFrame al VLCPlayer compartido para que
            pinte aquí (set_hwnd en Windows, set_xwindow en Linux/X11).
        Inputs: ninguno (lee el winId actual del _video_frame).
        Outputs: ninguno (efecto lateral: VLC queda enlazado a esta ventana).
        Llamado por: showEvent (diferido 50ms).
        Llama a: playback_service.player.set_hwnd / set_xwindow.
        """
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
        """Compat: devuelve el objeto MediaPlayer de libVLC del servicio.

        Propósito: dar acceso al MediaPlayer subyacente a código antiguo que lo
            esperaba en el widget; hoy vive en playback_service.
        Inputs: ninguno.
        Outputs: el MediaPlayer de libVLC, o None si el servicio no está listo.
        Llamado por: código de compatibilidad de la vista de Reproducción.
        """
        try:
            from desktop_app.src.services.playback_service import playback_service
            return playback_service.player.player
        except Exception:
            return None

    def play(self, url: str):
        """Compat: delega la reproducción en playback_service.

        Propósito: reproducir una URL/archivo VOD a través del VLC compartido.
        Inputs: url (ruta o URL del medio a reproducir).
        Outputs: ninguno.
        Llamado por: vista de Reproducción (compat).
        Llama a: playback_service.player.play_url.
        """
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
        """Re-bindea VLC si Qt asigna una winId distinta tras redimensionar.

        Algunos backends de ventana recrean la ventana nativa al cambiar de
        tamaño, invalidando el enlace previo; reenlazar evita que el vídeo
        desaparezca. Llamado por Qt en cada redimensionado.
        """
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
