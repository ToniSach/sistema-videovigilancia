"""
================================================================================
MÓDULO: ui.views.live_view — Rejilla de directo en vivo (CRÍTICO · Pipeline #3)
================================================================================

PROPÓSITO
    Pantalla de monitoreo en vivo tipo NVR comercial: una rejilla paginada de
    cámaras donde cada celda reproduce el directo de baja latencia. Es la pieza
    más sensible de rendimiento del cliente: gestiona N reproductores VLC vivos
    a la vez sin congelar la UI ni fugar memoria de GPU.

    Diseño de la rejilla:
      - Selector de layout 1×1 / 2×2 / 3×3 (slots por página = columnas²).
      - Paginación ◀ Pág X/Y ▶ entre grupos de slots; atajos PageUp/PageDown y
        Ctrl+←/→.
      - "Modo TV": rota automáticamente entre páginas cada 10s (vigilancia
        desatendida).
      - Doble clic = maximizar una cámara a todo el grid (y restaurar).
      - Dual-lens: cada cámara dual ocupa 2 slots independientes (L1, L2).

ARQUITECTURA DEL DIRECTO (cómo se pinta el vídeo)
    El directo NO pasa por api_client. Cada `CameraWidget` reproduce con VLC
    (libVLC) el restream RTSP que publica go2rtc en el backend; VLC pinta
    directamente sobre el HWND/xwindow nativo del QLabel de vídeo. go2rtc abre
    UNA sola conexión a la cámara y la multiplexa, así que tener el mismo stream
    en varias páginas/vistas no penaliza a la cámara.

    Latencia mínima (no VOD): VLC se arranca con --network-caching=150,
    --rtsp-tcp y --drop-late-frames (resync por descarte → la latencia queda
    ACOTADA; desactivar el resync con --clock-jitter=0 hacía CRECER el retardo
    sin parar). El cuello de botella real suele ser el GOP de la cámara.

    Cambio de calidad sin pantalla negra: `set_live_url` carga la nueva calidad
    en un reproductor + superficie SECUNDARIOS por detrás y hace el relevo solo
    cuando la nueva entrega su primer frame (con watchdog de 12s para descartarla
    si nunca llega).

CACHE Y CICLO DE VIDA (clave para no fugar VRAM)
    Los widgets viven en `_widget_cache` indexados por (camera_id, stream_type);
    cambiar de página/layout solo hace hide/show, no destruye streams (volver
    atrás es instantáneo). Solo se destruyen al cargar una lista totalmente
    distinta (`set_cameras`) o al hacer logout (`_destroy_all_widgets`, invocado
    por MainWindow._logout). `release_resources` para VLC y libera el QPixmap
    ANTES de deleteLater para que VLC no pinte sobre una ventana liberada.

DEPENDENCIAS
    - services/playback_service.VLCPlayer : reproductor libVLC (import diferido).
    - services/api_client.py : NO para el vídeo; solo para badges de estado:
        * GET /cameras/ (cada 15s) → indicador ●REC de las cámaras que graban.
    - models/camera.Camera , ui/icons.icon , ui/components/toast (snapshot),
      ui/dialogs/info_dialog + ui/help_texts (ayuda contextual).

COMPONENTES RELACIONADOS
    `CameraWidget` (definido aquí) es la celda de la rejilla. La vista de control
    individual (camera_control_view) y el preview de gestión usan el componente
    RtspVideoWidget en su lugar; aquí se usa VLC directo por densidad/rendimiento.

PUNTO DE ENTRADA
    La instancia MainWindow._create_main_view (índice VIEW_LIVE=0). MainWindow
    le pasa cámaras + token con `set_cameras`, la reinicia con `restart_streams`
    al entrar a la vista, y llama a `_destroy_all_widgets` en el logout.

SEÑALES emitidas hacia MainWindow:
    - ptz_requested(Camera)        → abrir control PTZ de esa cámara.
    - camera_config_requested(int) → abrir la vista de control de la cámara.
    - camera_selected(Camera)      → selección simple (informativa).

PIPELINE(S)
    #3 Live (reproducción del restream go2rtc con VLC, sin tocar el backend de
    captura/IA).
================================================================================
"""
import logging
import math
from typing import List, Dict, Optional, Tuple

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QGridLayout, QFrame, QMenu, QPushButton,
    QComboBox, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer, Slot, QThread, QCoreApplication
from PySide6.QtGui import (
    QPixmap, QMouseEvent, QContextMenuEvent, QShortcut, QKeySequence,
)

from desktop_app.src.models.camera import Camera
from desktop_app.src.ui.icons import icon

logger = logging.getLogger(__name__)


# Tipo: (camera_id, label, stream_type, camera_obj)
SlotTuple = Tuple[int, str, str, Camera]


class CameraWidget(QFrame):
    """
    Celda de la rejilla: una cámara (o una lente de una cámara dual-lens).

    RESPONSABILIDAD / ROL
        Encapsula un reproductor VLC que muestra el restream go2rtc de su
        (camera_id, stream_type), más la barra inferior (nombre, reloj, ●REC,
        estado, snapshot, ⚙). Se identifica por la pareja (camera_id,
        stream_type) — "main" | "l1" | "l2".

    QUIÉN LA INSTANCIA
        LiveView._load_page, una por slot visible; quedan cacheadas en
        LiveView._widget_cache.

    SEÑALES QT (las conecta LiveView)
        - clicked(int) / double_clicked(int): selección / maximizar.
        - ptz_requested(int): menú contextual → PTZ.
        - snapshot_requested(int): captura del fotograma actual.
        - config_requested(int): botón ⚙ → vista de control.

    DETALLES DE RENDIMIENTO
        - `start_live` arranca VLC una sola vez (idempotente).
        - `set_live_url` cambia de calidad SIN corte (reproductor de relevo).
        - El reloj se pausa en `hideEvent` (no malgastar ticks en widgets
          ocultos del cache de páginas).
        - `release_resources`/`stop_video` desligan el HWND antes de destruir
          para evitar crashes nativos de VLC y fugas de QPixmap en GPU.
    """

    clicked = Signal(int)
    double_clicked = Signal(int)
    ptz_requested = Signal(int)
    snapshot_requested = Signal(int)
    config_requested = Signal(int)

    def __init__(self, camera_id: int, camera_name: str,
                 stream_type: str = "main", stream_url: str = "", parent=None):
        super().__init__(parent)

        self.camera_id = camera_id
        self.camera_name = camera_name
        self.stream_type = stream_type  # "main" | "l1" | "l2"
        self.stream_url = stream_url or ""
        # El directo se reproduce SIEMPRE con go2rtc + VLC (RTSP de baja
        # latencia, una sola conexión a la cámara). MJPEG fue eliminado.
        self._vlc = None
        self._rtsp_started = False
        self.is_maximized = False
        # Relevo de calidad sin pantalla negra: reproductor + superficie
        # secundarios que cargan la nueva calidad por detrás de la actual.
        self._pending_player = None
        self._pending_surface = None
        self._pending_watchdog = None
        # Watchdog de "primer frame": si VLC no recibe vídeo (transcoder go2rtc
        # frío/saturado), reintenta sin congelar la UI hasta que llegue imagen.
        self._got_frame = False
        self._live_gen = 0
        self._live_retries = 0
        self._live_max_retries = 6

        self.setMinimumSize(280, 200)
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self.lbl_video = QLabel("Esperando video...")
        self.lbl_video.setAlignment(Qt.AlignCenter)
        self.lbl_video.setStyleSheet("""
            background-color: #000000;
            color: #888888;
            border-radius: 4px;
            font-size: 12px;
        """)
        self.lbl_video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.lbl_video.setMinimumHeight(160)
        layout.addWidget(self.lbl_video, 1)

        info_layout = QHBoxLayout()
        self.lbl_name = QLabel(camera_name)
        self.lbl_name.setStyleSheet("color: white; font-weight: bold; font-size: 11px;")
        info_layout.addWidget(self.lbl_name)

        # Reloj en vivo (hora actual) sobre la barra de la cámara.
        self.lbl_clock = QLabel("")
        self.lbl_clock.setStyleSheet("color: #94a3b8; font-size: 10px;")
        info_layout.addWidget(self.lbl_clock)

        # Indicador REC (rojo) — visible solo si la cámara está grabando.
        self.lbl_rec = QLabel("●REC")
        self.lbl_rec.setStyleSheet("color: #ef4444; font-size: 10px; font-weight: bold;")
        self.lbl_rec.setVisible(False)
        info_layout.addWidget(self.lbl_rec)

        info_layout.addStretch()

        self.lbl_status = QLabel("●")
        self.lbl_status.setStyleSheet("color: #22c55e; font-size: 10px;")
        info_layout.addWidget(self.lbl_status)

        # Botón snapshot (captura rápida del fotograma actual).
        self.btn_snapshot = QPushButton()
        self.btn_snapshot.setIcon(icon("snapshot", "#888888"))
        self.btn_snapshot.setMaximumWidth(30)
        self.btn_snapshot.setToolTip("Capturar imagen")
        self.btn_snapshot.setStyleSheet("""
            QPushButton { background-color: transparent; color: #888; border: none; font-size: 14px; }
            QPushButton:hover { color: #38bdf8; }
        """)
        self.btn_snapshot.clicked.connect(
            lambda: self.snapshot_requested.emit(self.camera_id)
        )
        info_layout.addWidget(self.btn_snapshot)

        self.btn_config = QPushButton()
        self.btn_config.setIcon(icon("settings"))
        self.btn_config.setMaximumWidth(30)
        self.btn_config.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888888;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover { color: #38bdf8; }
        """)
        self.btn_config.clicked.connect(
            lambda: self.config_requested.emit(self.camera_id)
        )
        info_layout.addWidget(self.btn_config)

        layout.addLayout(info_layout)

        # Reloj que actualiza la hora cada segundo (barato).
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(1000)
        self._tick_clock()

    def _tick_clock(self):
        from datetime import datetime as _dt
        try:
            self.lbl_clock.setText(_dt.now().strftime("%H:%M:%S"))
        except Exception:
            pass

    def set_recording(self, on: bool):
        """Muestra/oculta el indicador ●REC."""
        if hasattr(self, "lbl_rec"):
            self.lbl_rec.setVisible(bool(on))

    def start_live(self):
        """
        Arranca el directo (go2rtc + VLC) una sola vez. Si la cámara no tiene
        stream_url (go2rtc aún no publicó su feed), muestra un aviso claro en
        lugar de quedarse en "Esperando video..." indefinidamente.
        """
        if self._rtsp_started:
            return
        if not self.stream_url:
            self.lbl_video.setText("Sin stream disponible\n(go2rtc no configurado)")
            self.lbl_status.setStyleSheet("color: #f59e0b;")
            return
        self._rtsp_started = True
        self.lbl_video.setText("Conectando…")
        # Arrancar VLC tras tener winId nativo (QTimer 0ms).
        QTimer.singleShot(0, self._start_rtsp)

    def _start_rtsp(self):
        """
        Reproduce el restream RTSP de go2rtc con VLC, tuneado a LATENCIA MÍNIMA
        para directo (no VOD). VLC pinta directamente sobre el HWND/xwindow del
        QLabel de vídeo.
        """
        # El widget puede haber sido destruido entre el QTimer diferido y aquí
        # (cambio de página/calidad rápido) → su QLabel C++ ya no existe.
        try:
            import shiboken6
            if not shiboken6.isValid(self.lbl_video):
                return
        except Exception:
            pass
        try:
            from desktop_app.src.services.playback_service import VLCPlayer
            import os as _os
            # NO usar --clock-jitter=0/--clock-synchro=0: desactivan el resync de
            # VLC y la latencia CRECE sin parar (vídeo cada vez más retrasado aun
            # con FPS correctos). Con resync activo + drop-late, VLC descarta
            # frames tardíos y la latencia queda ACOTADA.
            opts = [
                "--quiet", "--no-video-title-show",
                "--network-caching=150",   # colchón mínimo (ms)
                "--rtsp-tcp",              # RTSP sobre TCP (robusto en WiFi)
                "--drop-late-frames",      # resync por descarte → no acumula
                "--no-audio-time-stretch",
            ]
            self._vlc = VLCPlayer(config_options=opts)
            wid = int(self.lbl_video.winId())
            if _os.name == "nt":
                self._vlc.set_hwnd(wid)
            else:
                self._vlc.set_xwindow(wid)
            self._vlc.error.connect(
                lambda m: logger.error(f"VLC live cam {self.camera_id}: {m}")
            )
            try:
                self._vlc.first_frame.connect(self._on_live_frame)
            except Exception:
                pass
            self._vlc.play_url(self.stream_url)
            # Rellenar el panel completo (sin barras negras). Se aplica tras un
            # instante (cuando el vídeo ya tiene tamaño) y en cada resize.
            QTimer.singleShot(300, self._apply_fill)
            self.lbl_status.setStyleSheet("color: #22c55e;")
            # Armar el watchdog de primer frame (reintenta si el transcoder de
            # go2rtc tarda en entregar; cancela en cuanto llega imagen).
            self._arm_live_watchdog()
        except RuntimeError:
            # QLabel/objeto C++ borrado a mitad → abortar silenciosamente.
            return
        except Exception as e:
            logger.error(f"No se pudo iniciar RTSP live cam {self.camera_id}: {e}")
            try:
                self.lbl_video.setText("Error RTSP")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Watchdog de primer frame (reintento sin congelar la UI)
    # ------------------------------------------------------------------
    def _on_live_frame(self):
        """Llega el primer frame → el directo va; cancelar reintentos."""
        self._got_frame = True

    def _arm_live_watchdog(self, reset: bool = True):
        """Programa una comprobación del directo. Con reset=True (arranque nuevo)
        reinicia contador y bandera de primer frame."""
        if reset:
            self._got_frame = False
            self._live_retries = 0
            self._live_gen += 1
        gen = self._live_gen
        # Primer chequeo GENEROSO: un transcoder dual-lens de go2rtc puede tardar
        # varios segundos en entregar el primer frame en frío.
        QTimer.singleShot(9000, lambda: self._check_live_stream(gen))

    def _check_live_stream(self, gen: int):
        """Si tras el intervalo no llegó imagen, reintenta la reproducción en
        hilo de fondo (stop+play NO bloquea la UI)."""
        if gen != self._live_gen:
            return  # superado por otro arranque (cambio de calidad/página)
        if self._got_frame or self._vlc is None or not self.stream_url:
            return
        if not self.isVisible():
            return  # oculto en cache de páginas → no insistir
        if self._live_retries >= self._live_max_retries:
            try:
                import shiboken6
                if shiboken6.isValid(self.lbl_video):
                    self.lbl_video.setText("Sin señal de vídeo")
            except Exception:
                pass
            return
        self._live_retries += 1
        logger.info(
            f"Live cam {self.camera_id} ({self.stream_type}): sin primer frame, "
            f"reintentando ({self._live_retries}/{self._live_max_retries})"
        )
        try:
            # Reconecta al MISMO path de go2rtc (su transcoder sigue caliente);
            # en hilo de fondo para no bloquear la UI con el stop() de libVLC.
            self._vlc.play_url_async(self.stream_url)
        except Exception as e:
            logger.debug(f"_check_live_stream retry cam {self.camera_id}: {e}")
        QTimer.singleShot(7000, lambda: self._check_live_stream(gen))

    def _apply_fill(self):
        """
        Hace que el vídeo RELLENE todo el panel (sin barras negras). Fuerza la
        relación de aspecto al tamaño del contenedor → VLC estira para llenar.
        Se llama al iniciar y en cada resize.
        """
        try:
            if self._vlc is None:
                return
            # NO tocar el player mientras hay un swap (stop+play) en curso: el
            # método bloquearía esperando el mutex interno y congelaría la UI
            # ("No responde") al cambiar de calidad. Reintentar luego.
            if self._vlc.is_swapping():
                QTimer.singleShot(400, self._apply_fill)
                return
            import shiboken6
            if not shiboken6.isValid(self.lbl_video):
                return
            w = max(1, self.lbl_video.width())
            h = max(1, self.lbl_video.height())
            self._vlc.player.video_set_aspect_ratio(f"{w}:{h}".encode("ascii"))
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._vlc is not None:
            self._apply_fill()
        # Mantener la superficie de relevo alineada con el vídeo.
        if self._pending_surface is not None:
            try:
                self._pending_surface.setGeometry(self.lbl_video.geometry())
            except Exception:
                pass

    def hideEvent(self, event):
        # Pausar el reloj cuando el widget no se ve (queda en cache de páginas):
        # evita ~1 tick/seg × N widgets ocultos corriendo en background.
        if hasattr(self, "_clock_timer") and self._clock_timer.isActive():
            self._clock_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        if hasattr(self, "_clock_timer") and not self._clock_timer.isActive():
            self._clock_timer.start(1000)
            self._tick_clock()
        super().showEvent(event)

    def set_live_url(self, url: str):
        """
        Cambia la calidad del directo SIN cortar lo que se ve. La calidad actual
        (p. ej. media) sigue mostrándose mientras la nueva (p. ej. alta) carga en
        un reproductor secundario por DETRÁS. Solo cuando la nueva entrega su
        primer frame se hace el relevo (sin pantalla negra). Si la nueva no llega
        en ~12s, se descarta y se mantiene la actual (arregla el "tarda/nunca").
        """
        if self._vlc is None or not url:
            return
        if url == self.stream_url and self._pending_player is None:
            return
        # Cancelar un relevo previo en curso (cambios rápidos de calidad).
        self._cleanup_pending()
        self.stream_url = url
        try:
            from desktop_app.src.services.playback_service import VLCPlayer
            import os as _os
            # Superficie secundaria: misma geometría que lbl_video pero DETRÁS,
            # así la calidad actual se sigue viendo encima mientras la nueva carga.
            surf = QLabel(self.lbl_video.parentWidget())
            surf.setStyleSheet("background-color:#000;")
            surf.setGeometry(self.lbl_video.geometry())
            surf.show()
            surf.lower()
            self.lbl_video.raise_()
            self._pending_surface = surf

            player = VLCPlayer(config_options=[
                "--quiet", "--no-video-title-show", "--network-caching=150",
                "--rtsp-tcp", "--drop-late-frames", "--no-audio-time-stretch",
            ])
            wid = int(surf.winId())
            if _os.name == "nt":
                player.set_hwnd(wid)
            else:
                player.set_xwindow(wid)
            player.first_frame.connect(self._on_pending_ready)
            self._pending_player = player
            player.play_url_async(url)

            # Watchdog: si la nueva calidad no entrega frame en 12s, descartarla
            # (la actual nunca se tocó, así que sigue viéndose).
            self._pending_watchdog = QTimer(self)
            self._pending_watchdog.setSingleShot(True)
            self._pending_watchdog.timeout.connect(
                lambda: self._cleanup_pending(log="nueva calidad no disponible (timeout 12s)")
            )
            self._pending_watchdog.start(12000)
        except Exception as e:
            logger.error(f"set_live_url cam {self.camera_id}: {e}")
            self._cleanup_pending()

    def _on_pending_ready(self):
        """La nueva calidad ya entrega imagen: relevo sin pantalla negra."""
        if self._pending_player is None:
            return
        try:
            if self._pending_watchdog is not None:
                self._pending_watchdog.stop()
            # La superficie nueva (ya con imagen) al frente para tapar el cambio.
            if self._pending_surface is not None:
                self._pending_surface.raise_()
            # Pasar el reproductor PRINCIPAL a la nueva URL (ya está CALIENTE, su
            # arranque es casi inmediato); la superficie nueva cubre el micro-corte.
            try:
                self._vlc.first_frame.connect(self._finish_swap)
            except Exception:
                pass
            self._vlc.play_url_async(self.stream_url)
            # Fallback: aunque el principal no notifique vout, cerrar el relevo.
            QTimer.singleShot(4000, self._finish_swap)
        except Exception as e:
            logger.error(f"_on_pending_ready cam {self.camera_id}: {e}")
            self._cleanup_pending()

    def _finish_swap(self):
        """El principal ya muestra la nueva calidad: soltar el reproductor de relevo."""
        try:
            self._vlc.first_frame.disconnect(self._finish_swap)
        except Exception:
            pass
        self._cleanup_pending()
        QTimer.singleShot(200, self._apply_fill)

    def _cleanup_pending(self, log: str = ""):
        """Detiene y libera el reproductor/superficie de relevo (idempotente)."""
        if log:
            logger.info(f"Live cam {self.camera_id}: {log}; se mantiene la calidad actual")
        p = self._pending_player
        self._pending_player = None
        if self._pending_watchdog is not None:
            try:
                self._pending_watchdog.stop()
            except Exception:
                pass
            self._pending_watchdog = None
        if p is not None:
            # Desligar la ventana AQUÍ (operación rápida) para poder borrar la
            # superficie sin que VLC pinte sobre una ventana liberada, y hacer el
            # stop() —BLOQUEANTE en libVLC— en un HILO DE FONDO para NO congelar
            # la UI. Llamar stop() en el hilo de UI era la causa de los "trabados"
            # al cambiar de calidad.
            try:
                import os as _os
                if _os.name == "nt":
                    p.player.set_hwnd(0)
                else:
                    p.player.set_xwindow(0)
            except Exception:
                pass
            import threading as _th

            def _stop_async(pl):
                try:
                    pl.player.stop()
                except Exception:
                    pass

            _th.Thread(target=_stop_async, args=(p,),
                       name="VLCPendingStop", daemon=True).start()
        surf = self._pending_surface
        self._pending_surface = None
        if surf is not None:
            try:
                surf.hide()
                surf.deleteLater()
            except Exception:
                pass

    def stop_video(self):
        """
        Detiene VLC y lo DESLIGA de la ventana (idempotente). Desligar el HWND
        antes de que el QLabel se destruya evita que VLC pinte sobre una ventana
        liberada (causa típica de crash nativo al cerrar/cambiar de vista).
        """
        try:
            self._cleanup_pending()
        except Exception:
            pass
        # Cancelar cualquier reintento de watchdog pendiente.
        self._live_gen += 1
        self._got_frame = False
        try:
            if self._vlc is None:
                return
            import os as _os
            p = self._vlc.player
            # Desligar la ventana PRIMERO (operación rápida) para que el stop()
            # en segundo plano no pinte sobre un HWND ya liberado.
            try:
                if _os.name == "nt":
                    p.set_hwnd(0)
                else:
                    p.set_xwindow(0)
            except Exception:
                pass
            # player.stop() es BLOQUEANTE en libVLC 3 (espera a que el decoder
            # RTSP/TCP termine, puede tardar segundos). Llamarlo en el hilo de UI
            # era la CAUSA de los congelamientos al cerrar/cambiar de vista o al
            # cerrar sesión con varias cámaras. Lo hacemos en un hilo de fondo.
            import threading as _th

            def _stop_async(pl):
                try:
                    pl.stop()
                except Exception:
                    pass

            _th.Thread(target=_stop_async, args=(p,),
                       name="VLCLiveStop", daemon=True).start()
        except Exception:
            pass

    def is_live(self) -> bool:
        """True si VLC está reproduciendo el directo en este momento."""
        try:
            if self._vlc is None:
                return False
            # Durante un swap (stop+play) NO consultar is_playing(): bloquearía
            # el hilo UI esperando el mutex del player. Asumir "vivo".
            if self._vlc.is_swapping():
                return True
            return bool(self._vlc.player.is_playing())
        except Exception:
            return False

    def release_resources(self):
        """
        Detiene VLC y libera el pixmap ANTES de deleteLater(). Sin esto, VLC
        podía seguir pintando sobre una ventana liberada (crash nativo) y los
        QPixmap quedaban en memoria GPU (memory creep al recrear layouts).
        """
        try:
            self.stop_video()
        except Exception:
            pass
        self.lbl_video.setPixmap(QPixmap())  # libera el pixmap anterior

    def set_offline(self):
        """Marca como offline."""
        self.lbl_video.setText("Cámara offline")
        self.lbl_status.setStyleSheet("color: #ef4444;")

    def set_online_pending(self):
        """Estado intermedio: aún sin frame, pero conectando."""
        self.lbl_status.setStyleSheet("color: #f59e0b;")

    def closeEvent(self, event):
        self.release_resources()
        super().closeEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.camera_id)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self.double_clicked.emit(self.camera_id)

    def contextMenuEvent(self, event: QContextMenuEvent):
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
    """
    Vista de directo en vivo: rejilla paginada de CameraWidget.

    RESPONSABILIDAD / ROL
        Orquestar el conjunto de celdas: expandir cámaras (dual-lens → 2 slots),
        paginarlas según el layout 1×1/2×2/3×3, mantener el cache de widgets
        vivo entre páginas, refrescar estado/grabación y gestionar la limpieza
        de recursos. Núcleo de rendimiento del cliente (Pipeline #3).

    QUIÉN LA INSTANCIA
        MainWindow._create_main_view (índice VIEW_LIVE=0).

    SEÑALES QT
        EMITE (las escucha MainWindow):
          - camera_selected(Camera): selección simple (informativa).
          - ptz_requested(Camera): abrir control PTZ.
          - camera_config_requested(int): abrir la vista de control de la cámara.
        No escucha señales externas; recibe órdenes por métodos públicos
        (`set_cameras`, `restart_streams`, `_destroy_all_widgets`).

    ESTADO CLAVE
        - `_widget_cache`: {(camera_id, stream_type): CameraWidget} — todos los
          widgets vivos (no solo los visibles).
        - `cameras`: subconjunto actualmente colocado en el grid.
        - `_all_slots`: lista expandida de slots (con dual-lens desdoblado).
        - `_grid_cols` / `_current_page`: layout y página activa.

    TIMERS
        - `_status_timer` (5s): marca en ámbar las celdas cuyo VLC dejó de
          reproducir (RTSP caído).
        - `_rec_timer` (15s): consulta GET /cameras/ para el badge ●REC.
        - `_tv_timer` (10s): rotación automática del "Modo TV".

    DEPENDENCIAS
        CameraWidget (celdas + VLC/go2rtc), api_client (solo badges ●REC),
        toast/info_dialog (UI auxiliar).
    """

    camera_selected = Signal(Camera)
    ptz_requested = Signal(Camera)
    camera_config_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Cache: widgets vivos indexados por (camera_id, stream_type).
        # No se destruyen al cambiar de página — sólo hide/show.
        # Sólo se destruyen al cargar una lista de cámaras totalmente
        # diferente o al hacer logout.
        self._widget_cache: Dict[Tuple[int, str], CameraWidget] = {}

        # Slots actualmente en el grid (visibles)
        self.cameras: Dict[Tuple[int, str], CameraWidget] = {}

        # Estado
        self._camera_list: List[Camera] = []
        self._all_slots: List[SlotTuple] = []
        self._current_api_token: str = ""
        self._grid_cols = 2
        self._current_page = 0
        self._restart_timer: Optional[QTimer] = None
        # Calidad del directo: "auto" | "high" | "medium" | "low".
        # Por defecto "medium" (480p): aligera CPU/red en cámaras dual-lens con
        # IA activa (evita transcodes/decodes a resolución plena). El usuario
        # puede subir a Alta/Auto desde el selector.
        self._quality = "medium"

        self._setup_ui()
        self._setup_shortcuts()

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._check_cameras_status)
        self._status_timer.start(5000)
        # El estado de grabación (●REC) cambia raramente; consultar /cameras/
        # cada 5s solo para eso era derrochador. Timer aparte cada 15s.
        self._rec_timer = QTimer(self)
        self._rec_timer.timeout.connect(self._refresh_recording_badges)
        self._rec_timer.start(15000)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header: título, layout selector, paginación
        header = QHBoxLayout()

        self.lbl_title = QLabel("Cámaras en Vivo")
        self.lbl_title.setStyleSheet(
            "color: #f1f5f9; font-size: 20px; font-weight: bold;"
        )
        header.addWidget(self.lbl_title)

        # Botón de ayuda contextual
        self.btn_help = QPushButton()
        self.btn_help.setIcon(icon("help"))
        self.btn_help.setToolTip("Ayuda — Cámaras en vivo")
        self.btn_help.setMaximumWidth(36)
        self.btn_help.setStyleSheet("""
            QPushButton {
                background-color: transparent; color: #94a3b8;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 6px; padding: 4px 8px; font-size: 14px;
            }
            QPushButton:hover { color: #38bdf8; border-color: #38bdf8; }
        """)
        self.btn_help.clicked.connect(self._show_help)
        header.addWidget(self.btn_help)

        header.addStretch()

        # Selector de layout
        header.addWidget(QLabel("Vista:"))
        self.cmb_layout = QComboBox()
        for text, cols in [
            ("1×1 (cámara grande)", 1),
            ("2×2 (4 cámaras)", 2),
            ("3×3 (9 cámaras)", 3),
        ]:
            self.cmb_layout.addItem(text, cols)
        self.cmb_layout.setCurrentIndex(1)
        self.cmb_layout.currentIndexChanged.connect(self._on_layout_change)
        self.cmb_layout.setStyleSheet("""
            QComboBox {
                background-color: #1e293b;
                color: #f1f5f9;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 6px 12px;
                min-width: 180px;
            }
        """)
        header.addWidget(self.cmb_layout)

        # La calidad del directo es SIEMPRE "media" (transcode 480p ligero en
        # go2rtc). Se quitó el selector Auto/Alta/Baja: la app entera ve en media
        # para una experiencia uniforme y un único stream que mantener caliente.

        # Separador
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: rgba(255,255,255,0.15);")
        header.addWidget(sep)

        # Paginación
        btn_style = """
            QPushButton {
                background-color: #1e293b;
                color: #f1f5f9;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 6px;
                padding: 6px 12px;
                min-width: 90px;
            }
            QPushButton:hover:!disabled { background-color: #334155; }
            QPushButton:disabled { color: #475569; }
        """
        self.btn_prev = QPushButton("  Anterior")
        self.btn_prev.setIcon(icon("prev"))
        self.btn_prev.setStyleSheet(btn_style)
        self.btn_prev.setToolTip("Página anterior (Page Up)")
        self.btn_prev.clicked.connect(self._on_prev_page)
        header.addWidget(self.btn_prev)

        self.lbl_page = QLabel("Pág 1/1")
        self.lbl_page.setAlignment(Qt.AlignCenter)
        self.lbl_page.setMinimumWidth(80)
        self.lbl_page.setStyleSheet(
            "color: #f1f5f9; font-size: 13px; font-weight: 600;"
        )
        header.addWidget(self.lbl_page)

        self.btn_next = QPushButton("Siguiente  ")
        self.btn_next.setIcon(icon("next"))
        self.btn_next.setStyleSheet(btn_style)
        self.btn_next.setToolTip("Página siguiente (Page Down)")
        self.btn_next.clicked.connect(self._on_next_page)
        header.addWidget(self.btn_next)

        # Modo TV: rota automáticamente entre páginas cada N segundos (ideal
        # para una pantalla de vigilancia desatendida). Botón conmutable.
        self.btn_tv = QPushButton("  Modo TV")
        self.btn_tv.setIcon(icon("live"))
        self.btn_tv.setCheckable(True)
        self.btn_tv.setStyleSheet(btn_style + """
            QPushButton:checked {
                background-color: #38bdf8; color: #0f172a; font-weight: bold;
            }
        """)
        self.btn_tv.setToolTip("Rota automáticamente entre páginas cada 10 s")
        self.btn_tv.toggled.connect(self._on_tv_toggle)
        header.addWidget(self.btn_tv)

        # Timer de rotación del modo TV (10 s por página).
        self._tv_timer = QTimer(self)
        self._tv_timer.setInterval(10000)
        self._tv_timer.timeout.connect(self._tv_advance)

        layout.addLayout(header)

        # Grid
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setSpacing(8)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.grid_widget, 1)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key_PageDown), self, activated=self._on_next_page)
        QShortcut(QKeySequence(Qt.Key_PageUp), self, activated=self._on_prev_page)
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=self._on_next_page)
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=self._on_prev_page)

    def _show_help(self):
        from desktop_app.src.ui.dialogs.info_dialog import InfoDialog
        from desktop_app.src.ui.help_texts import get_help
        title, sections = get_help("live_view")
        InfoDialog(title=title, sections=sections, parent=self).exec()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def set_cameras(self, cameras: List[Camera], api_token: str):
        """Define el conjunto de cámaras a mostrar y repuebla la página actual.

        Propósito: expandir cámaras (dual-lens → slots L1/L2), descartar del
        cache los widgets de cámaras que ya no existen (con release_resources) y
        recolocar la página actual. Reutiliza el cache para las que siguen.
        Inputs: `cameras` (DTOs Camera) y `api_token` (token de stream go2rtc).
        Llamado por: MainWindow._load_cameras y `restart_streams`. Llama a:
        `_load_page`."""
        self._camera_list = cameras
        self._current_api_token = api_token

        # Expandir cámaras dual-lens en slots independientes
        new_slots: List[SlotTuple] = []
        for cam in cameras:
            if getattr(cam, "is_dual_lens", False):
                new_slots.append((cam.id, f"{cam.name} · L1", "l1", cam))
                new_slots.append((cam.id, f"{cam.name} · L2", "l2", cam))
            else:
                new_slots.append((cam.id, cam.name, "main", cam))

        # Limpiar widgets que ya no existen en la nueva lista
        new_keys = {(s[0], s[2]) for s in new_slots}
        stale = [k for k in self._widget_cache.keys() if k not in new_keys]
        for key in stale:
            widget = self._widget_cache.pop(key)
            widget.release_resources()
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()

        self._all_slots = new_slots
        # Si el current_page se queda fuera de rango, ajustar
        max_pages = max(1, self._get_page_count())
        if self._current_page >= max_pages:
            self._current_page = 0

        self._load_page(self._current_page)

    def restart_streams(self, api_token: str):
        """Reinicia TODOS los streams con un token fresco (al entrar a la vista).

        Propósito: destruir el cache completo y recrearlo (diferido 500ms) con
        el token nuevo de go2rtc, para que las URLs lleven credenciales válidas
        tras un cambio de sesión. Inputs: `api_token`. Llamado por:
        MainWindow._switch_view cuando se navega a VIEW_LIVE. Llama a:
        `_destroy_all_widgets` y, tras el timer, `set_cameras`."""
        logger.debug("Reiniciando streams de video con token nuevo...")

        if self._restart_timer:
            self._restart_timer.stop()
            self._restart_timer.deleteLater()
            self._restart_timer = None

        # Forzar reinicio total: destruir todo el cache para que se vuelvan
        # a crear los streams con el token nuevo.
        self._destroy_all_widgets()

        self._restart_timer = QTimer(self)
        self._restart_timer.setSingleShot(True)
        self._restart_timer.timeout.connect(
            lambda: self.set_cameras(self._camera_list, api_token)
        )
        self._restart_timer.start(500)

    def cleanup(self):
        """Limpieza al ocultar la vista o cerrar la app."""
        if QThread.currentThread() != self.thread():
            QTimer.singleShot(0, self._do_cleanup)
            return
        self._do_cleanup()

    # ------------------------------------------------------------------
    # Paginación
    # ------------------------------------------------------------------
    def _slots_per_page(self) -> int:
        return self._grid_cols * self._grid_cols

    def _get_page_count(self) -> int:
        if not self._all_slots:
            return 1
        return math.ceil(len(self._all_slots) / self._slots_per_page())

    def _get_page_slots(self, page: int) -> List[SlotTuple]:
        per = self._slots_per_page()
        start = page * per
        return self._all_slots[start:start + per]

    def _load_page(self, page: int):
        """Pinta los widgets de la página solicitada usando el cache."""
        # Al cambiar de página se sale del modo maximizado (evita estado colgado).
        if getattr(self, "_maximized_key", None) is not None and not getattr(self, "_restoring", False):
            self._maximized_key = None
        if page < 0:
            page = 0
        max_pages = max(1, self._get_page_count())
        if page >= max_pages:
            page = max_pages - 1
        self._current_page = page

        slots = self._get_page_slots(page)

        # Quitar TODOS los widgets del grid (sin destruir — quedan en cache)
        for key, widget in list(self.cameras.items()):
            self.grid.removeWidget(widget)
            widget.hide()
        self.cameras.clear()

        # Limpiar stretches viejos (importante al pasar de 3x3 → 1x1)
        for i in range(self.grid.rowCount()):
            self.grid.setRowStretch(i, 0)
        for i in range(self.grid.columnCount()):
            self.grid.setColumnStretch(i, 0)

        # Colocar widgets de esta página (creándolos si es la primera vez)
        cols = self._grid_cols
        for i, (cam_id, label, stream_type, _cam) in enumerate(slots):
            key = (cam_id, stream_type)
            widget = self._widget_cache.get(key)
            if widget is None:
                stream_url = self._pick_stream_url(
                    _cam, stream_type, self._effective_quality()
                )
                widget = CameraWidget(
                    cam_id, label, stream_type=stream_type, stream_url=stream_url
                )
                widget.clicked.connect(self._on_camera_click)
                widget.double_clicked.connect(self._on_camera_double_click)
                widget.ptz_requested.connect(self._on_ptz_request)
                widget.snapshot_requested.connect(self._on_snapshot)
                widget.config_requested.connect(self._on_config_request)
                self._widget_cache[key] = widget

                # Arranque escalonado del directo (go2rtc + VLC). En dual-lens
                # cada lente/calidad es un TRANSCODE H264 propio en go2rtc; si los
                # 4 arrancan a la vez saturan el encoder (qsv/CPU) y varios se
                # quedan en "Conectando…". Espaciar ~1s deja que cada transcoder
                # caliente antes del siguiente (la watchdog de cada tile reintenta
                # si aun así no llega frame).
                QTimer.singleShot(i * 1000, widget.start_live)
            else:
                # Reutilizado del cache: actualizar label por si cambió el name
                widget.lbl_name.setText(label)
                # Asegurar que el directo esté vivo (idempotente).
                widget.start_live()

            row = i // cols
            col = i % cols
            self.grid.addWidget(widget, row, col)
            widget.show()
            self.cameras[key] = widget

        # Aplicar stretch sólo a las filas/cols usadas
        for r in range(cols):
            self.grid.setRowStretch(r, 1)
            self.grid.setColumnStretch(r, 1)

        # Actualizar UI de paginación
        self._update_pagination_ui()
        QCoreApplication.processEvents()

    def _update_pagination_ui(self):
        total = self._get_page_count()
        self.lbl_page.setText(f"Pág {self._current_page + 1}/{total}")
        self.btn_prev.setEnabled(self._current_page > 0)
        self.btn_next.setEnabled(self._current_page < total - 1)

    @Slot()
    def _on_prev_page(self):
        if self._current_page > 0:
            self._load_page(self._current_page - 1)

    @Slot()
    def _on_next_page(self):
        if self._current_page < self._get_page_count() - 1:
            self._load_page(self._current_page + 1)

    # ------------------------------------------------------------------
    # Modo TV (rotación automática de páginas)
    # ------------------------------------------------------------------
    def _on_tv_toggle(self, on: bool):
        if on:
            # Solo tiene sentido si hay más de una página.
            if self._get_page_count() <= 1:
                self.btn_tv.setChecked(False)
                return
            self._tv_timer.start()
        else:
            self._tv_timer.stop()

    def _tv_advance(self):
        """Avanza a la siguiente página, volviendo a la primera al final (loop)."""
        total = self._get_page_count()
        if total <= 1:
            self._tv_timer.stop()
            self.btn_tv.setChecked(False)
            return
        nxt = (self._current_page + 1) % total
        self._load_page(nxt)

    # ------------------------------------------------------------------
    # Cambio de layout (1×1 / 2×2 / 3×3)
    # ------------------------------------------------------------------
    def _on_layout_change(self, idx: int):
        new_cols = self.cmb_layout.itemData(idx)
        if new_cols == self._grid_cols:
            return
        self._grid_cols = new_cols
        self._current_page = 0
        # No destruye nada — sólo recoloca desde el cache
        self._load_page(0)

    # ------------------------------------------------------------------
    # Calidad del directo
    # ------------------------------------------------------------------
    def _effective_quality(self) -> str:
        """
        Resuelve la calidad concreta. "auto" se decide UNA vez por nº de núcleos
        (coste cero, sin monitoreo continuo): <4 → baja, 4-7 → media, ≥8 → alta.
        """
        if self._quality != "auto":
            return self._quality
        import os
        cores = os.cpu_count() or 4
        if cores < 4:
            return "low"
        if cores < 8:
            return "medium"
        return "high"

    def _pick_stream_url(self, cam, stream_type: str, quality: str) -> str:
        """URL de go2rtc para (cam, lente, calidad), con fallbacks."""
        urls = getattr(cam, "stream_urls", None) or {}
        key = stream_type if stream_type in ("l1", "l2") else "main"
        by_q = urls.get(key) or {}
        legacy = {
            "l1": getattr(cam, "stream_url_l1", None),
            "l2": getattr(cam, "stream_url_l2", None),
        }.get(stream_type)
        return (
            by_q.get(quality)
            or by_q.get("high")
            or legacy
            or (getattr(cam, "stream_url", "") or "")
        )

    # ------------------------------------------------------------------
    # Click handlers
    # ------------------------------------------------------------------
    def _on_camera_click(self, camera_id: int):
        logger.debug(f"Cámara seleccionada: {camera_id}")

    def _on_camera_double_click(self, camera_id: int):
        """Doble-clic = maximizar esa cámara a todo el grid (y restaurar)."""
        # ¿Ya hay una maximizada? Si es la misma, restaurar; si no, cambiar.
        maxed = getattr(self, "_maximized_key", None)
        target_key = None
        for (cid, stype), _w in self.cameras.items():
            if cid == camera_id:
                target_key = (cid, stype)
                break
        if target_key is None:
            return
        if maxed == target_key:
            self._restore_grid()
        else:
            self._maximize_camera(target_key)

    def _maximize_camera(self, key):
        """Oculta las demás y expande la cámara `key` a todo el grid."""
        self._maximized_key = key
        for k, widget in self.cameras.items():
            if k == key:
                self.grid.removeWidget(widget)
                self.grid.addWidget(widget, 0, 0,
                                    max(1, self._grid_cols), max(1, self._grid_cols))
                widget.show()
            else:
                widget.hide()

    def _restore_grid(self):
        """Vuelve a la rejilla normal de la página actual."""
        self._maximized_key = None
        self._load_page(self._current_page)

    def _on_ptz_request(self, camera_id: int):
        logger.info(f"PTZ solicitado para cámara {camera_id}")
        self.ptz_requested.emit(Camera(id=camera_id, name=""))

    def _on_snapshot(self, camera_id: int):
        widget = None
        for (cid, _stype), w in self.cameras.items():
            if cid == camera_id:
                widget = w
                break
        if not widget:
            return
        from PySide6.QtCore import QStandardPaths
        import time, os
        pictures_path = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        full_path = os.path.join(pictures_path, f"snapshot_{camera_id}_{int(time.time())}.png")

        ok = False
        # Modo RTSP (VLC): usar video_take_snapshot del player.
        vlc = getattr(widget, "_vlc", None)
        if vlc is not None:
            try:
                w = max(0, widget.lbl_video.width())
                vlc.player.video_take_snapshot(0, full_path, w, 0)
                ok = True  # VLC escribe el archivo de forma asíncrona
            except Exception as e:
                logger.error(f"snapshot VLC cam {camera_id}: {e}")
        # Fallback: si por lo que sea hay un pixmap pintado, guardarlo.
        elif widget.lbl_video.pixmap() and not widget.lbl_video.pixmap().isNull():
            ok = widget.lbl_video.pixmap().save(full_path)

        if ok:
            logger.info(f"Snapshot guardado: {full_path}")
            self._toast(f"Imagen guardada en {pictures_path}")
        else:
            self._toast("No se pudo capturar la imagen")

    def _toast(self, message: str):
        """Notificación breve no intrusiva (si la ventana la soporta)."""
        try:
            from desktop_app.src.ui.components.toast import show_toast
            show_toast(self, message)
        except Exception:
            logger.info(message)

    def _on_config_request(self, camera_id: int):
        logger.info(f"Configuración solicitada para cámara {camera_id}")
        self.camera_config_requested.emit(camera_id)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def _check_cameras_status(self):
        # El directo lo gestiona VLC; si dejó de reproducir (RTSP caído,
        # reconexión) lo marcamos en ámbar. Sólo aplica a widgets que ya
        # intentaron arrancar y tienen stream_url.
        for (cam_id, stream_type), widget in self.cameras.items():
            if widget._rtsp_started and widget.stream_url and not widget.is_live():
                widget.set_online_pending()

    def _refresh_recording_badges(self):
        """Actualiza el indicador ●REC de cada celda según quién está grabando.

        Propósito: pintar el badge rojo solo en las cámaras con grabación activa.
        Async (callback en hilo UI). Llamado por: `_rec_timer` (15s) y
        `showEvent`. Llama a: GET /cameras/ (lee `worker_status.recording` o
        `is_recording`)."""
        if not self.cameras:
            return
        # Import diferido (mismo patrón que `config` en este archivo): api_client
        # no está importado a nivel de módulo.
        from desktop_app.src.services.api_client import api_client

        def on_cams(response):
            if not response.success:
                return
            rec_by_cam = {}
            for c in (response.data or []):
                ws = c.get("worker_status") or {}
                # 'recording' puede venir en worker_status o como flag de la cámara.
                rec = False
                if isinstance(ws, dict):
                    rec = bool(ws.get("recording") or ws.get("is_recording"))
                rec = rec or bool(c.get("is_recording"))
                rec_by_cam[c.get("id")] = rec
            for (cam_id, _stype), widget in self.cameras.items():
                widget.set_recording(rec_by_cam.get(cam_id, False))
        api_client.get("cameras/", on_cams)

    # ------------------------------------------------------------------
    # Limpieza
    # ------------------------------------------------------------------
    def _destroy_all_widgets(self):
        """
        Destruye TODO el cache de widgets (logout, cambio total de lista).
        Garantiza release_resources antes de deleteLater para evitar leaks
        de QPixmap en GPU.
        """
        for key, widget in list(self._widget_cache.items()):
            try:
                widget.stop_video()  # cierra VLC si estaba en modo RTSP/go2rtc
            except Exception:
                pass
            widget.release_resources()
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        self._widget_cache.clear()
        self.cameras.clear()

        # Limpiar grid layout
        while self.grid.count():
            item = self.grid.takeAt(0)
            if w := item.widget():
                w.setParent(None)

    def _do_cleanup(self):
        """
        Limpieza al ocultar la vista. NO destruye widgets — sólo para que
        cuando vuelvas a la vista los streams ya estén vivos.
        Cuando se hace logout real, se llama a _destroy_all_widgets vía
        restart_streams o explícitamente desde MainWindow._logout.
        """
        if self._status_timer.isActive():
            self._status_timer.stop()
        if hasattr(self, "_rec_timer") and self._rec_timer.isActive():
            self._rec_timer.stop()
        # Streams se mantienen vivos en background — el usuario eligió
        # "siempre vivos" en la configuración del modo paginación.

    def hideEvent(self, event):
        # Detener la rotación TV al salir de la vista (no rotar en background).
        if hasattr(self, "_tv_timer"):
            self._tv_timer.stop()
        if hasattr(self, "btn_tv"):
            self.btn_tv.setChecked(False)
        self.cleanup()
        super().hideEvent(event)

    def showEvent(self, event):
        if not self._status_timer.isActive():
            self._status_timer.start(5000)
        if hasattr(self, "_rec_timer") and not self._rec_timer.isActive():
            self._rec_timer.start(15000)
            self._refresh_recording_badges()  # actualización inmediata al entrar
        super().showEvent(event)

    def closeEvent(self, event):
        self._destroy_all_widgets()
        if self._status_timer.isActive():
            self._status_timer.stop()
        super().closeEvent(event)
