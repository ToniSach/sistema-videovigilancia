"""
================================================================================
MÓDULO: ui.components.rtsp_video — Superficie de vídeo en VIVO (DIRECTO, #3)
================================================================================

PROPÓSITO
    Widget reutilizable de vídeo en VIVO por RTSP/go2rtc con VLC y flags de baja
    latencia. Es la ÚNICA vía de directo en las vistas (control de cámara,
    preview de gestión, mosaico en vivo, etc.).

RESPONSABILIDAD
    Encapsula todo el ciclo de vida del directo de UNA cámara/lente:
      - creación perezosa de un VLCPlayer con flags de baja latencia,
      - bind del HWND (Windows) / xwindow (X11) a su superficie nativa,
      - relleno del panel sin barras negras (aspect ratio = tamaño del widget),
      - cambio de fuente sin recrear el player (set_url) y parada segura
        (stop() que desliga el HWND, idempotente).

PIPELINE
    #3 Live. El vídeo NO pasa por api_client: VLC abre directamente el restream
    RTSP de go2rtc (por TCP). El backend solo orquesta go2rtc; el flujo de
    píxeles va cámara → go2rtc → VLC → esta superficie.

DISTINCIÓN vs. video_player.py
    rtsp_video.py = DIRECTO, tiene SU PROPIO VLCPlayer con flags de baja latencia.
    video_player.py = REPRODUCCIÓN/VOD, comparte el VLC singleton de
    playback_service. No confundir: cada uno usa una instancia distinta.

NOTA SOBRE EL AUDIO (escucha de cámara)
    El directo de go2rtc se reexpone SOLO-vídeo (transcodifica a `#video=h264`),
    así que normalmente el stream no trae pista de audio que desmutear; la
    "escucha de cámara" real se hace server-side (ffplay) desde AudioControlWidget.
    Los métodos de audio de este widget (set_audio_enabled/set_volume) actúan
    sobre el player VLC cuando SÍ hay pista de audio disponible.

DEPENDENCIAS
    PySide6 (QFrame/QLabel/QTimer), playback_service.VLCPlayer (libVLC),
    config indirecto. Import diferido de VLCPlayer para no exigir libVLC hasta
    que se reproduce de verdad.

COMPONENTES RELACIONADOS
    services/playback_service.py (clase VLCPlayer compartida), camera_control_panel
    (AudioControlWidget conecta sus señales de escucha/volumen aquí),
    video_player.py (homólogo VOD).

DÓNDE SE USA
    Lo instancian la vista de Directo/Live, la vista de control de cámara y los
    previews de la gestión de cámaras.

FLAGS DE BAJA LATENCIA — POR QUÉ NO --clock-jitter=0
    Ver el bloque _LIVE_VLC_OPTS más abajo: desactivar la resincronización de
    reloj hace que la latencia CREZCA sin parar; con el reloj activo VLC descarta
    frames tardíos y la latencia queda ACOTADA (lo correcto en vigilancia).
================================================================================
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
    """
    Superficie de vídeo en VIVO con VLC embebido. API pública:
    play(url) / set_url(url) / stop() y los métodos de audio.

    Rol: única superficie de directo del cliente (Pipeline #3).

    Quién la instancia/consume:
        Vista de Directo, vista de control de cámara y previews de gestión la
        crean. AudioControlWidget (camera_control_panel) conecta sus señales de
        escucha/volumen a set_audio_enabled()/set_volume() de este widget.

    Señales Qt: no emite señales propias. RECIBE acciones por llamada directa
        a slots (no por conexión de señal): la vista llama play/set_url/stop y
        reenvía aquí las señales listen_changed/volume_changed del panel de audio.

    Dependencias: VLCPlayer (libVLC, import diferido), QLabel nativo como
        superficie de pintado, QTimer para diferir fill/audio tras play().
    """

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
        # Watchdog de arranque: una cámara recién añadida tarda unos segundos en
        # estar lista en go2rtc; si VLC se conecta antes de tiempo se quedaría en
        # negro sin reintentar. Reintentamos la reproducción hasta que llegue el
        # primer frame (o se agoten los reintentos). El "generation" evita que un
        # reintento viejo pise una reproducción nueva (cambio de cámara/lente).
        self._play_gen = 0
        self._got_frame = False
        self._retry_count = 0
        self._max_retries = 5
        self._retry_interval_ms = 4000
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

        Inputs: url (restream RTSP de go2rtc, p.ej. rtsp://host:8554/cam_X).
        Outputs: ninguno. Si url es vacía, muestra "Sin stream disponible".
        Llamado por: la vista de Directo/control al seleccionar una cámara.
        Llama a: _do_play (ahora o diferido a showEvent).
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
                # Conectar UNA sola vez: el primer frame confirma que el stream
                # ya va; cancela los reintentos del watchdog.
                try:
                    self._vlc.first_frame.connect(self._on_first_frame)
                except Exception:
                    pass
            wid = int(self._surface.winId())
            if os.name == "nt":
                self._vlc.set_hwnd(wid)
            else:
                self._vlc.set_xwindow(wid)
            self._vlc.play_url(url)
            QTimer.singleShot(300, self._apply_fill)
            # El audio hay que fijarlo cuando el output ya existe (tras play).
            QTimer.singleShot(500, self._apply_audio)
            # Arrancar el watchdog de "primer frame" (reintenta si no llega).
            self._arm_watchdog()
        except Exception as e:
            logger.error(f"RtspVideoWidget._do_play: {e}")
            self._surface.setText("Error de vídeo")

    # ------------------------------------------------------------------
    # Watchdog de arranque (reintento hasta el primer frame)
    # ------------------------------------------------------------------
    def _arm_watchdog(self, reset: bool = True):
        """Programa una comprobación del stream. Con reset=True (reproducción
        nueva) reinicia el contador y la bandera de primer frame."""
        if reset:
            self._got_frame = False
            self._retry_count = 0
            self._play_gen += 1
        gen = self._play_gen
        QTimer.singleShot(self._retry_interval_ms, lambda: self._check_stream(gen))

    def _on_first_frame(self):
        """Llega el primer frame de vídeo → el stream va; parar reintentos."""
        self._got_frame = True

    def _check_stream(self, gen: int):
        """Si tras el intervalo no hubo primer frame, reintenta la reproducción
        (la cámara recién añadida puede no estar lista aún en go2rtc)."""
        # Reproducción superada por otra más nueva (cambio de cámara) → ignorar.
        if gen != self._play_gen:
            return
        if self._got_frame or not self._url:
            return
        # Si el widget no está visible, no insistir (showEvent reanudará).
        if not self.isVisible():
            return
        if self._retry_count >= self._max_retries:
            self._surface.setText(
                "Sin señal de vídeo. Revisa que la cámara esté en línea."
            )
            return
        self._retry_count += 1
        logger.info(
            f"RtspVideoWidget: sin primer frame, reintentando "
            f"({self._retry_count}/{self._max_retries}) {self._url}"
        )
        try:
            if self._vlc is not None:
                # play_url_async: stop()+play() en hilo de fondo (no congela UI).
                self._vlc.play_url_async(self._url)
            else:
                self._do_play()
        except Exception as e:
            logger.debug(f"RtspVideoWidget._check_stream retry: {e}")
        # Reprogramar el siguiente intento sin reiniciar el contador.
        self._arm_watchdog(reset=False)

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
        difiere a showEvent).

        Inputs: url (nuevo restream RTSP). Si es igual a la actual, no hace nada.
        Outputs: ninguno.
        Llamado por: la vista al CAMBIAR de cámara sin destruir el widget.
        Llama a: VLCPlayer.play_url_async (swap) o play() (primera vez).
        """
        if not url or url == self._url:
            return
        self._url = url
        self._pending_url = url
        if self._vlc is not None and self.isVisible():
            try:
                self._vlc.play_url_async(url)
                QTimer.singleShot(800, self._apply_fill)
                QTimer.singleShot(1000, self._apply_audio)
                # Nueva fuente → reiniciar el watchdog de primer frame.
                self._arm_watchdog()
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
        """Activa (desmutea) o desactiva el audio del directo en el cliente.

        Inputs: enabled (True = oír por los altavoces del operador).
        Outputs: ninguno.
        Señales: slot conectado normalmente a AudioControlWidget.listen_changed.
        Llamado por: la vista, al pulsar "Escuchar cámara" en el panel de audio.
        Llama a: _apply_audio.
        """
        self._audio_enabled = bool(enabled)
        self._apply_audio()

    def set_volume(self, value: int):
        """Ajusta el volumen del audio del directo (0-100), clampado al rango.

        Inputs: value (0-100).
        Outputs: ninguno.
        Señales: slot conectado normalmente a AudioControlWidget.volume_changed.
        Llamado por: la vista, al mover el slider de volumen del panel de audio.
        Llama a: _apply_audio.
        """
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
        # Invalida cualquier reintento del watchdog pendiente (un singleShot ya
        # programado verá que su 'gen' caducó y no hará nada).
        self._play_gen += 1
        self._got_frame = False
        self._retry_count = 0
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
