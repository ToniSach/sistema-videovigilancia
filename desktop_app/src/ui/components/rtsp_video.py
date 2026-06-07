"""
Widget reutilizable de vídeo en VIVO por RTSP/go2rtc con VLC (baja latencia).

Es la única vía de directo en las vistas (control de cámara, preview de
gestión, etc.). Encapsula:
  - creación perezosa de un VLCPlayer con flags de baja latencia,
  - bind del HWND/xwindow a su superficie,
  - relleno del panel (sin barras negras),
  - cambio de fuente sin recrear (set_url) y parada segura (desliga el HWND).
"""
from __future__ import annotations

import logging
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QSizePolicy

logger = logging.getLogger(__name__)

# Flags de baja latencia para DIRECTO (no VOD). go2rtc solo sirve RTSP por TCP.
# IMPORTANTE: NO usar --clock-jitter=0 / --clock-synchro=0. Desactivan la
# resincronización de reloj de VLC: si el decoder se atrasa un poco, NUNCA
# recupera y la latencia CRECE sin parar (el síntoma "el vídeo se va retrasando"
# aunque los FPS sean correctos). Con el reloj activo, VLC DESCARTA frames
# tardíos para mantenerse al día → latencia ACOTADA (a costa de algún frame
# suelto, lo correcto en vigilancia). network-caching=150 fija el colchón mínimo.
_LIVE_VLC_OPTS = [
    "--quiet", "--no-video-title-show",
    "--network-caching=150",   # colchón mínimo (ms)
    "--rtsp-tcp",              # RTSP sobre TCP (robusto en WiFi)
    "--drop-late-frames",      # descarta frames tardíos (resync) → no acumula
    "--no-audio-time-stretch", # evita que el audio fuerce ralentizar el vídeo
]


class RtspVideoWidget(QFrame):
    """Superficie de vídeo en vivo VLC. Llama play(url) / set_url(url) / stop()."""

    def __init__(self, parent=None, placeholder: str = "Conectando…"):
        super().__init__(parent)
        self._vlc = None
        self._url = ""
        self._pending_url = None  # URL a reproducir cuando el widget sea visible
        # Audio del directo: por defecto SILENCIADO (convención NVR — el mosaico
        # en vivo no debe sonar). El botón "Escuchar cámara" lo desmutea para
        # que el usuario oiga el audio de la cámara por SUS auriculares (cliente).
        self._audio_enabled = False
        self._volume = 80
        self.setStyleSheet("background-color: #000000; border-radius: 4px;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(120)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        # VLC pinta sobre el HWND de este QLabel. WA_NativeWindow fuerza una
        # ventana nativa real para que winId() sea válido y VLC NO abra una
        # ventana aparte.
        self._surface = QLabel(placeholder)
        self._surface.setAttribute(Qt.WA_NativeWindow, True)
        self._surface.setAlignment(Qt.AlignCenter)
        self._surface.setStyleSheet("background-color: #000; color: #888; font-size: 13px;")
        self._surface.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay.addWidget(self._surface)

    # ------------------------------------------------------------------
    def play(self, url: str):
        """
        Reproduce una URL RTSP. Si el widget aún NO es visible, difiere la
        reproducción hasta showEvent (cuando el winId ya es una ventana real),
        para que VLC se incruste y no abra una ventana flotante.
        """
        if not url:
            self._surface.setText("Sin stream disponible")
            return
        self._url = url
        self._pending_url = url
        if self.isVisible():
            self._do_play()
        # Si no es visible aún, showEvent() lo arrancará.

    def _do_play(self):
        """Crea/enlaza VLC al winId (ya válido) y reproduce la URL pendiente."""
        url = self._pending_url
        if not url:
            return
        try:
            from desktop_app.src.services.playback_service import VLCPlayer
            if self._vlc is None:
                self._vlc = VLCPlayer(config_options=list(_LIVE_VLC_OPTS))
            wid = int(self._surface.winId())
            if os.name == "nt":
                self._vlc.set_hwnd(wid)
            else:
                self._vlc.set_xwindow(wid)
            self._vlc.play_url(url)
            QTimer.singleShot(300, self._apply_fill)
            # El audio hay que fijarlo cuando el output ya existe (tras play).
            QTimer.singleShot(500, self._apply_audio)
        except Exception as e:
            logger.error(f"RtspVideoWidget._do_play: {e}")
            self._surface.setText("Error de vídeo")

    def showEvent(self, event):
        super().showEvent(event)
        # Al hacerse visible el winId ya es válido → enlazar y reproducir lo
        # que quedó pendiente (evita la ventana VLC flotante).
        if self._pending_url and self._vlc is None:
            self._do_play()
        else:
            self._apply_fill()

    def set_url(self, url: str):
        """Cambia la fuente sin recrear el player (evita churn/crash de VLC).

        Si ya hay player visible, swap ASÍNCRONO (player.stop() de libVLC es
        bloqueante y congelaría la UI). Si aún no hay player, usa play() (que
        difiere a showEvent)."""
        if not url or url == self._url:
            return
        self._url = url
        self._pending_url = url
        if self._vlc is not None and self.isVisible():
            try:
                self._vlc.play_url_async(url)
                QTimer.singleShot(800, self._apply_fill)
                QTimer.singleShot(1000, self._apply_audio)
            except Exception as e:
                logger.error(f"RtspVideoWidget.set_url: {e}")
        else:
            self.play(url)

    def show_message(self, text: str):
        """Detiene el vídeo y muestra un texto (placeholder/estado)."""
        self.stop()
        self._url = ""
        self._pending_url = None
        self._surface.setText(text)

    # ------------------------------------------------------------------
    # Audio (client-side) — el botón "Escuchar cámara" lo controla.
    # ------------------------------------------------------------------
    def _apply_audio(self):
        """Aplica mute/volumen al player VLC según el estado deseado."""
        try:
            if self._vlc is None:
                return
            # Durante un swap, audio_set_* se bloquea en el mutex de libVLC
            # (stop en curso) y congela la UI → reintentar luego.
            if self._vlc.is_swapping():
                QTimer.singleShot(400, self._apply_audio)
                return
            p = self._vlc.player
            p.audio_set_mute(not self._audio_enabled)
            p.audio_set_volume(int(self._volume))
        except Exception as e:
            logger.debug(f"RtspVideoWidget._apply_audio: {e}")

    def set_audio_enabled(self, enabled: bool):
        """Activa (desmutea) o desactiva el audio del directo en el cliente."""
        self._audio_enabled = bool(enabled)
        self._apply_audio()

    def set_volume(self, value: int):
        """Ajusta el volumen del audio del directo (0-100)."""
        self._volume = max(0, min(100, int(value)))
        self._apply_audio()

    def has_audio(self) -> bool:
        """True si el stream actual tiene al menos una pista de audio."""
        try:
            return self._vlc is not None and self._vlc.player.audio_get_track_count() > 0
        except Exception:
            return False

    def _apply_fill(self):
        """Estira el vídeo para llenar el panel (sin barras negras)."""
        try:
            if self._vlc is None:
                return
            # Durante un swap, video_set_aspect_ratio se bloquea en el mutex de
            # libVLC (stop en curso) y congela la UI ("No responde") → reintentar.
            if self._vlc.is_swapping():
                QTimer.singleShot(400, self._apply_fill)
                return
            w = max(1, self.width())
            h = max(1, self.height())
            self._vlc.player.video_set_aspect_ratio(f"{w}:{h}".encode("ascii"))
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_fill()

    def stop(self):
        """Detiene VLC y lo desliga de la ventana (seguro, idempotente)."""
        try:
            if self._vlc is None:
                return
            p = self._vlc.player
            try:
                p.stop()
            except Exception:
                pass
            try:
                p.set_hwnd(0) if os.name == "nt" else p.set_xwindow(0)
            except Exception:
                pass
        except Exception:
            pass

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)
