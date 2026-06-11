# desktop_app/src/ui/components/camera_control_panel.py
"""
================================================================================
MÓDULO: ui.components.camera_control_panel — Panel de control de cámara
================================================================================

PROPÓSITO
    Panel lateral con TODOS los controles de una cámara seleccionada,
    organizados en pestañas: Movimiento (PTZ + presets), IA/REC (detección +
    grabación manual) y Audio/Luz (audio bidireccional + LEDs/IR). Cada sub-card
    encapsula su propio dominio y habla con el backend por REST (api_client).

RESPONSABILIDAD
    - Componer las sub-cards de control y enrutar la cámara seleccionada a cada
      una vía set_camera().
    - Para PTZ, recibir las señales del PTZJoystick y traducirlas en POST REST
      (este panel es el "pegamento" entre el joystick mudo y el backend).
    - Cada sub-card gestiona su propio estado/feedback y emite señales (started,
      activated, listen_changed…) que la vista puede observar.

PIPELINES
    #8 PTZ (PTZJoystick + presets → /cameras/<id>/ptz/...),
    IA (/ai/<id>/activate|deactivate|status), grabación manual
    (/recordings/manual/...), audio (talk/listen server-side + escucha cliente),
    LEDs (/cameras/<id>/leds/<mode>).

CLASES DE ESTE MÓDULO
    - LEDControlWidget ......... iluminación/IR (auto/on/off).
    - AudioControlWidget ....... audio bidireccional: PTT (talk) + escucha;
                                 emite listen_changed/volume_changed que la vista
                                 cablea al RtspVideoWidget.
    - AIControlWidget .......... activar/desactivar YOLOv8 por lente + estado
                                 global (solo 1 cámara con IA a la vez).
    - RecordingControlWidget ... grabación continua manual (start/stop/status).
    - CameraControlPanel ....... contenedor con pestañas que agrupa lo anterior
                                 + presets PTZ; es la clase pública del módulo.

DEPENDENCIAS
    PySide6 (QTabWidget, QScrollArea, etc.), api_client (REST + JWT, asíncrono),
    config (estilos), GlassCard (base visual de las sub-cards), PTZJoystick.

COMPONENTES RELACIONADOS
    ptz_joystick.py (emite move/stop que aquí se convierten en REST),
    rtsp_video.py (recibe las señales de escucha/volumen del AudioControlWidget),
    glass_card.py (base de las sub-cards).

DÓNDE SE USA
    En la vista de control de cámara / Directo; se muestra al seleccionar una
    cámara y se oculta con clear().
================================================================================
"""
import logging
from PySide6.QtWidgets import QMessageBox
from typing import Optional

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QGroupBox, QSlider, QComboBox,
                               QGridLayout, QSizePolicy, QScrollArea, QFrame,
                               QTabWidget)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QIcon, QFont

from desktop_app.src.config import config
from desktop_app.src.services.api_client import api_client
from desktop_app.src.ui.components.glass_card import GlassCard
from desktop_app.src.ui.components.ptz_joystick import PTZJoystick

logger = logging.getLogger(__name__)


class LEDControlWidget(GlassCard):
    """Control de iluminación/IR de la cámara (modos auto / on / off).

    Rol: sub-card de la pestaña "Audio/Luz". Llama a
    POST /cameras/<id>/leds/<mode> al cambiar de modo.

    Quién la instancia/consume: CameraControlPanel (pestaña Audio/Luz).
    Señales Qt: no emite señales propias.
    Dependencias: api_client (REST), config, GlassCard (base visual).
    """
    
    def __init__(self, parent=None):
        super().__init__(parent, border_radius=8)
        
        self.camera_id: Optional[int] = None
        
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # Título
        lbl_title = QLabel("Control de Iluminación")
        lbl_title.setStyleSheet(f"""
            color: {config.THEME_ACCENT};
            font-weight: bold;
            font-size: 14px;
        """)
        layout.addWidget(lbl_title)
        
        # Botones de modo
        btn_layout = QHBoxLayout()
        
        self.btn_auto = QPushButton("Auto")
        self.btn_auto.setCheckable(True)
        self.btn_auto.setChecked(True)
        self.btn_auto.clicked.connect(lambda: self._set_mode("auto"))
        
        self.btn_on = QPushButton("Encendido")
        self.btn_on.setCheckable(True)
        self.btn_on.clicked.connect(lambda: self._set_mode("on"))

        self.btn_off = QPushButton("Apagado")
        self.btn_off.setCheckable(True)
        self.btn_off.clicked.connect(lambda: self._set_mode("off"))
        
        self.mode_buttons = [self.btn_auto, self.btn_on, self.btn_off]
        
        for btn in self.mode_buttons:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {config.THEME_SECONDARY};
                    color: {config.THEME_TEXT};
                    border: 1px solid {config.GLASS_BORDER};
                    border-radius: 6px;
                    padding: 8px 16px;
                }}
                QPushButton:checked {{
                    background-color: {config.THEME_ACCENT};
                    color: {config.THEME_PRIMARY};
                }}
            """)
            btn_layout.addWidget(btn)
        
        layout.addLayout(btn_layout)
        
        # Estado
        self.lbl_status = QLabel("Modo: Automático")
        self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(self.lbl_status)
    
    def set_camera(self, camera_id: int):
        """Fija la cámara objetivo de los comandos LED. Llamado por
        CameraControlPanel.set_camera()."""
        self.camera_id = camera_id

    def _set_mode(self, mode: str):
        """Cambia el modo de iluminación y lo envía al backend.

        Inputs: mode ('auto' | 'on' | 'off').
        Outputs: ninguno (actualiza estado visual y hace POST).
        Llamado por: el clicked de los botones Auto/ON/OFF.
        Llama a: POST /cameras/<id>/leds/<mode>.
        """
        # Desmarcar otros botones
        for btn in self.mode_buttons:
            btn.setChecked(False)
        
        # Marcar el seleccionado
        if mode == "auto":
            self.btn_auto.setChecked(True)
            self.lbl_status.setText("Modo: Automático")
        elif mode == "on":
            self.btn_on.setChecked(True)
            self.lbl_status.setText("Modo: Forzado ON")
        else:
            self.btn_off.setChecked(True)
            self.lbl_status.setText("Modo: Forzado OFF")
        
        # Enviar a API
        if self.camera_id:
            def on_response(response):
                if not response.success:
                    logger.error(f"Error cambiando modo LED: {response.error}")
            
            api_client.post(f"cameras/{self.camera_id}/leds/{mode}", on_response)


class AudioControlWidget(GlassCard):
    """Control de audio bidireccional (talk + listen) con selector de micrófono.

    Rol: sub-card de la pestaña "Audio/Luz".
      - Talk (PTT): captura el micrófono del operador y lo manda a la cámara
        (server-side, /cameras/<id>/audio/talk|stop).
      - Listen: reproduce el audio de la cámara. Por defecto server-side (ffplay,
        /cameras/<id>/audio/listen/start|stop) porque el directo de go2rtc se
        reexpone solo-vídeo; aun así emite listen_changed/volume_changed para que
        la vista pueda dirigir la escucha al RtspVideoWidget cuando aplique.

    Quién la instancia/consume: CameraControlPanel (pestaña Audio/Luz); la vista
        conecta listen_changed → RtspVideoWidget.set_audio_enabled y
        volume_changed → set_volume.

    SEÑALES Qt que EMITE:
      - talk_started() / talk_ended(): inicio/fin de transmisión PTT.
      - listen_changed(bool): petición de escuchar/silenciar (consumo cliente).
      - volume_changed(int): volumen de escucha cliente (0-100).
    SEÑALES que RECIBE: ninguna.

    Dependencias: api_client (REST), config, GlassCard.
    """

    talk_started = Signal()
    talk_ended = Signal()
    # Escucha CLIENT-SIDE: el audio de la cámara se reproduce desmuteando el
    # player VLC del directo (por los auriculares del usuario, esté donde esté
    # el servidor). La vista conecta estas señales al RtspVideoWidget.
    listen_changed = Signal(bool)
    volume_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent, border_radius=8)

        self.camera_id: Optional[int] = None
        self._talking = False
        self._listening = False
        self._mics_loaded = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Título
        lbl_title = QLabel("Audio Bidireccional")
        lbl_title.setStyleSheet(f"""
            color: {config.THEME_ACCENT};
            font-weight: bold;
            font-size: 14px;
        """)
        layout.addWidget(lbl_title)

        # Selector de micrófono
        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("Mic:"))
        self.cmb_mic = QComboBox()
        self.cmb_mic.addItem("(default)")
        self.cmb_mic.setStyleSheet(f"""
            QComboBox {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 4px;
            }}
        """)
        mic_row.addWidget(self.cmb_mic, 1)
        self.btn_reload_mics = QPushButton("")
        self.btn_reload_mics.setMaximumWidth(36)
        self.btn_reload_mics.setToolTip("Detectar micrófonos disponibles")
        self.btn_reload_mics.clicked.connect(self._load_mics)
        mic_row.addWidget(self.btn_reload_mics)
        layout.addLayout(mic_row)

        # Botón PTT (Push to Talk)
        self.btn_ptt = QPushButton("MANTENER PRESIONADO PARA HABLAR")
        self.btn_ptt.setMinimumHeight(50)
        self.btn_ptt.setStyleSheet(f"""
            QPushButton {{
                background-color: {config.THEME_DANGER};
                color: white;
                border: none;
                border-radius: 8px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:pressed {{
                background-color: #dc2626;
            }}
        """)
        self.btn_ptt.pressed.connect(self._start_talk)
        self.btn_ptt.released.connect(self._stop_talk)
        layout.addWidget(self.btn_ptt)

        # Botón Listen (toggle)
        self.btn_listen = QPushButton("Escuchar cámara")
        self.btn_listen.setMinimumHeight(40)
        self.btn_listen.setCheckable(True)
        self.btn_listen.toggled.connect(self._toggle_listen)
        self.btn_listen.setStyleSheet(f"""
            QPushButton {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 8px;
                padding: 8px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:checked {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
            }}
        """)
        layout.addWidget(self.btn_listen)

        # Volumen de recepción
        vol_layout = QHBoxLayout()
        vol_layout.addWidget(QLabel("Volumen:"))

        self.slider_volume = QSlider(Qt.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(80)
        self.slider_volume.valueChanged.connect(self._set_volume)

        vol_layout.addWidget(self.slider_volume)
        layout.addLayout(vol_layout)

        # Estado
        self.lbl_status = QLabel("Listo")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")
        layout.addWidget(self.lbl_status)

    def set_camera(self, camera_id: int):
        """Apunta el panel de audio a una cámara y refresca micrófonos.

        Inputs: camera_id. Outputs: ninguno.
        Efectos: apaga la escucha (silencio) para no soltar audio de golpe y
            recarga la lista de micrófonos (asíncrono).
        Llamado por: CameraControlPanel.set_camera().
        """
        self.camera_id = camera_id
        # Al cambiar de cámara, dejar la escucha en OFF (silencio) para no
        # reproducir audio de golpe de la nueva cámara.
        if self.btn_listen.isChecked():
            self.btn_listen.setChecked(False)  # dispara _toggle_listen(False)
        # Refrescar la lista de micrófonos cada vez que se abre el panel, para
        # detectar dispositivos conectados después de arrancar (p.ej. un headset).
        # Es asíncrono (api_client.get), no bloquea la UI.
        self._mics_loaded = True
        self._load_mics()

    def _load_mics(self):
        """Carga la lista de micrófonos disponibles desde el backend."""
        def on_response(response):
            self.cmb_mic.clear()
            self.cmb_mic.addItem("(default)")
            if response.success and response.data:
                for d in response.data.get("devices", []):
                    self.cmb_mic.addItem(d)

        api_client.get("cameras/audio/devices", on_response)
    
    def _start_talk(self):
        """Inicia la transmisión PTT (micrófono del operador → cámara).

        Outputs: ninguno. Señales: talk_started() si el backend acepta.
        Llamado por: pressed del botón "MANTENER PRESIONADO PARA HABLAR".
        Llama a: POST /cameras/<id>/audio/talk (incluye mic_device si no es default).
        """
        if not self.camera_id or self._talking:
            return

        self._talking = True
        self.lbl_status.setText("Transmitiendo...")
        self.lbl_status.setStyleSheet(f"color: {config.THEME_DANGER}; font-weight: bold;")

        # Incluir el mic seleccionado (o nada si está en "default")
        body = {}
        mic = self.cmb_mic.currentText()
        if mic and mic != "(default)":
            body["mic_device"] = mic

        def on_response(response):
            if response.success:
                self.talk_started.emit()
            else:
                self._talking = False
                err = response.error or "Error al iniciar"
                self.lbl_status.setText(f"Error: {err[:60]}")
                self.lbl_status.setStyleSheet(f"color: {config.THEME_DANGER};")

        api_client.post(f"cameras/{self.camera_id}/audio/talk", on_response, data=body)

    def _stop_talk(self):
        """Detiene la transmisión PTT.

        Outputs: ninguno. Señales: talk_ended().
        Llamado por: released del botón PTT.
        Llama a: POST /cameras/<id>/audio/stop.
        """
        if not self._talking:
            return

        self._talking = False
        self.lbl_status.setText("Listo")
        self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")

        def on_response(response):
            self.talk_ended.emit()

        api_client.post(f"cameras/{self.camera_id}/audio/stop", on_response)

    def _toggle_listen(self, checked: bool):
        """
        Escucha el audio de la cámara reproduciéndolo en el SERVIDOR (ffplay).

        Por qué server-side y no en el cliente: go2rtc reexpone los lentes como
        streams SOLO-vídeo (transcodifica `#video=h264`), así que el directo del
        cliente NO tiene pista de audio que desmutear. El audio de la cámara sí
        está disponible en su RTSP, y el backend lo reproduce con ffplay. En la
        instalación típica (servidor y operador en el MISMO PC) el audio sale por
        los altavoces/auriculares del operador.
        """
        if not self.camera_id:
            self.btn_listen.setChecked(False)
            return

        if checked:
            self.btn_listen.setText("Detener escucha")

            def on_started(response):
                if response.success:
                    self._listening = True
                    self.lbl_status.setText("Escuchando audio de la cámara")
                    self.lbl_status.setStyleSheet(
                        f"color: {config.THEME_ACCENT}; font-weight: bold;"
                    )
                else:
                    self.btn_listen.setChecked(False)
                    self.btn_listen.setText("Escuchar cámara")
                    err = response.error or "Error"
                    self.lbl_status.setText(f"No se pudo escuchar: {err[:50]}")
                    self.lbl_status.setStyleSheet(f"color: {config.THEME_DANGER};")

            api_client.post(f"cameras/{self.camera_id}/audio/listen/start", on_started)
        else:
            self.btn_listen.setText("Escuchar cámara")

            def on_stopped(response):
                self._listening = False
                self.lbl_status.setText("Escucha detenida")
                self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")

            api_client.post(f"cameras/{self.camera_id}/audio/listen/stop", on_stopped)

    def _set_volume(self, value):
        """El volumen de la escucha server-side lo controla el sistema operativo
        (ffplay). Mantenemos el slider por familiaridad pero no actúa sobre él."""
        pass


class AIControlWidget(GlassCard):
    """
    Control de IA (YOLOv8) por cámara y por lente (main, o l1/l2 si dual-lens).

    Rol: sub-card de la pestaña "IA/REC". Activa/desactiva la detección y
    refleja el estado, incluyendo el aviso de que SOLO UNA cámara puede tener IA
    a la vez (si está en otra, activarla aquí la moverá).

    Llama a los endpoints REST:
        POST /api/v1/ai/<id>/activate    body: {lens, mode}
        POST /api/v1/ai/<id>/deactivate  body: {lens}
        GET  /api/v1/ai/<id>             → estado por lente
        GET  /api/v1/ai/status           → cámaras con IA activa (aviso global)

    Quién la instancia/consume: CameraControlPanel (pestaña IA/REC).
    SEÑALES Qt que EMITE: activated() / deactivated() al cambiar el estado.
    Dependencias: api_client (REST), config, GlassCard.
    """

    activated = Signal()
    deactivated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, border_radius=8)

        self.camera_id: Optional[int] = None
        self._is_dual_lens = False
        self._active_lenses: set[str] = set()

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        lbl_title = QLabel("Detección de objetos")
        lbl_title.setStyleSheet(f"""
            color: {config.THEME_ACCENT};
            font-weight: bold;
            font-size: 14px;
        """)
        layout.addWidget(lbl_title)

        # Aviso: solo una cámara puede tener la detección activa a la vez.
        # Si está en OTRA cámara, lo indicamos aquí (se actualiza en _refresh_status).
        self.lbl_global_ai = QLabel("")
        self.lbl_global_ai.setWordWrap(True)
        self.lbl_global_ai.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 11px;"
        )
        layout.addWidget(self.lbl_global_ai)

        # Selector de lente
        lens_layout = QHBoxLayout()
        lens_layout.addWidget(QLabel("Lente:"))
        self.cmb_lens = QComboBox()
        self.cmb_lens.addItem("main")
        self.cmb_lens.setStyleSheet(self._combo_style())
        lens_layout.addWidget(self.cmb_lens, 1)
        layout.addLayout(lens_layout)

        # Selector de modo
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Modo:"))
        # Etiquetas comerciales; el valor real (low_cpu/high_quality) va como data.
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItem("Bajo consumo (rápido)", "low_cpu")
        self.cmb_mode.addItem("Alta precisión", "high_quality")
        self.cmb_mode.setStyleSheet(self._combo_style())
        mode_layout.addWidget(self.cmb_mode, 1)
        layout.addLayout(mode_layout)

        # Botones
        btn_layout = QHBoxLayout()
        self.btn_activate = QPushButton("▶ Activar IA")
        self.btn_activate.clicked.connect(self._activate)
        self.btn_deactivate = QPushButton("Desactivar")
        self.btn_deactivate.clicked.connect(self._deactivate)
        for b in (self.btn_activate, self.btn_deactivate):
            b.setStyleSheet(self._button_style())
        btn_layout.addWidget(self.btn_activate)
        btn_layout.addWidget(self.btn_deactivate)
        layout.addLayout(btn_layout)

        # Estado
        self.lbl_status = QLabel("IA: desactivada")
        self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(self.lbl_status)

    def _combo_style(self) -> str:
        return f"""
            QComboBox {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 6px;
            }}
        """

    def _button_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 8px 12px;
            }}
            QPushButton:hover {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
            }}
        """

    def set_camera(self, camera_id: int, is_dual_lens: bool = False):
        """Apunta el control de IA a una cámara y rellena el selector de lente.

        Inputs: camera_id; is_dual_lens (True → lentes l1/l2; False → 'main').
        Outputs: ninguno. Llamado por: CameraControlPanel.set_camera().
        Llama a: _refresh_status (consulta estado por lente y aviso global).
        """
        self.camera_id = camera_id
        self._is_dual_lens = is_dual_lens
        self.cmb_lens.clear()
        if is_dual_lens:
            self.cmb_lens.addItems(["l1", "l2"])
        else:
            self.cmb_lens.addItem("main")
        self._refresh_status()

    def _activate(self):
        """Activa la detección YOLOv8 en la lente y modo seleccionados.

        Outputs: ninguno. Señales: activated() si el backend acepta.
        Llamado por: clicked de "▶ Activar IA".
        Llama a: POST /ai/<id>/activate {lens, mode}.
        """
        if not self.camera_id:
            return
        lens = self.cmb_lens.currentText()
        mode = self.cmb_mode.currentData() or "low_cpu"
        mode_label = self.cmb_mode.currentText()

        def on_response(response):
            if response.success:
                self._active_lenses.add(lens)
                self.lbl_status.setText(f"Detección activa en {lens} · {mode_label}")
                self.lbl_status.setStyleSheet(
                    f"color: {config.THEME_ACCENT}; font-weight: bold;"
                )
                self.activated.emit()
            else:
                err = response.error or "Error desconocido"
                self.lbl_status.setText(f"Error: {err[:60]}")
                self.lbl_status.setStyleSheet(f"color: {config.THEME_DANGER};")

        api_client.post(
            f"ai/{self.camera_id}/activate", on_response,
            data={"lens": lens, "mode": mode},
        )

    def _deactivate(self):
        """Desactiva la detección en la lente seleccionada.

        Outputs: ninguno. Señales: deactivated() si el backend acepta.
        Llamado por: clicked de "Desactivar".
        Llama a: POST /ai/<id>/deactivate {lens}.
        """
        if not self.camera_id:
            return
        lens = self.cmb_lens.currentText()

        def on_response(response):
            if response.success:
                self._active_lenses.discard(lens)
                self.lbl_status.setText("IA: desactivada")
                self.lbl_status.setStyleSheet(
                    f"color: {config.THEME_TEXT_MUTED}; font-size: 12px;"
                )
                self.deactivated.emit()
            else:
                err = response.error or "Error desconocido"
                self.lbl_status.setText(f"Error: {err[:60]}")

        api_client.post(
            f"ai/{self.camera_id}/deactivate", on_response,
            data={"lens": lens},
        )

    def _refresh_status(self):
        if not self.camera_id:
            return

        def on_response(response):
            if response.success and response.data:
                active = []
                for lens in ("main", "l1", "l2"):
                    if response.data.get(lens):
                        active.append(lens)
                        self._active_lenses.add(lens)
                if active:
                    self.lbl_status.setText(f"Detección activa en: {', '.join(active)}")
                    self.lbl_status.setStyleSheet(
                        f"color: {config.THEME_ACCENT}; font-weight: bold;"
                    )
                else:
                    self.lbl_status.setText("Detección: desactivada")
                    self.lbl_status.setStyleSheet(
                        f"color: {config.THEME_TEXT_MUTED}; font-size: 12px;"
                    )

        api_client.get(f"ai/{self.camera_id}", on_response)
        self._refresh_global_ai()

    def _refresh_global_ai(self):
        """Indica si la detección está activa en OTRA cámara (solo 1 a la vez)."""
        def on_global(response):
            if not response.success:
                self.lbl_global_ai.setText("")
                return
            active = (response.data or {}).get("active") or []
            others = [a for a in active if a.get("camera_id") != self.camera_id]
            if others:
                cams = ", ".join(f"cámara {a['camera_id']}" for a in others)
                self.lbl_global_ai.setText(
                    f"⚠ La detección está activa en {cams}. Solo una cámara puede "
                    f"tenerla a la vez; activarla aquí la moverá."
                )
                self.lbl_global_ai.setStyleSheet(
                    "color: #fbbf24; font-size: 11px;"
                )
            else:
                self.lbl_global_ai.setText("")
        api_client.get("ai/status", on_global)


class RecordingControlWidget(GlassCard):
    """
    Control de grabación continua MANUAL (independiente de la programada/evento).

    Rol: sub-card de la pestaña "IA/REC". Inicia/detiene una grabación continua
    a demanda y refleja el estado actual.

    Llama a los endpoints REST:
        POST /api/v1/recordings/manual/start/<id>
        POST /api/v1/recordings/manual/stop/<id>
        GET  /api/v1/recordings/manual/status/<id>

    Quién la instancia/consume: CameraControlPanel (pestaña IA/REC).
    SEÑALES Qt que EMITE: started() / stopped() al cambiar el estado.
    Dependencias: api_client (REST), config, GlassCard.
    """

    started = Signal()
    stopped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, border_radius=8)

        self.camera_id: Optional[int] = None
        self._recording = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        lbl_title = QLabel("Grabación manual")
        lbl_title.setStyleSheet(f"""
            color: {config.THEME_ACCENT};
            font-weight: bold;
            font-size: 14px;
        """)
        layout.addWidget(lbl_title)

        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("Iniciar")
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QPushButton("Detener")
        self.btn_stop.clicked.connect(self._stop)
        for b in (self.btn_start, self.btn_stop):
            b.setStyleSheet(f"""
                QPushButton {{
                    background-color: {config.THEME_SECONDARY};
                    color: {config.THEME_TEXT};
                    border: 1px solid {config.GLASS_BORDER};
                    border-radius: 6px;
                    padding: 8px 12px;
                }}
                QPushButton:hover {{
                    background-color: {config.THEME_ACCENT};
                    color: {config.THEME_PRIMARY};
                }}
            """)
        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)
        layout.addLayout(btn_layout)

        self.lbl_status = QLabel("Sin grabar")
        self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(self.lbl_status)

    def set_camera(self, camera_id: int):
        """Apunta el control a una cámara y consulta si ya está grabando.
        Llamado por: CameraControlPanel.set_camera()."""
        self.camera_id = camera_id
        self._refresh_status()

    def _start(self):
        """Inicia la grabación continua manual.

        Outputs: ninguno. Señales: started() si el backend acepta.
        Llamado por: clicked de "Iniciar". Llama a:
        POST /recordings/manual/start/<id>.
        """
        if not self.camera_id:
            return

        def on_response(response):
            if response.success:
                self._recording = True
                self.lbl_status.setText("Grabando")
                self.lbl_status.setStyleSheet(
                    f"color: {config.THEME_DANGER}; font-weight: bold;"
                )
                self.started.emit()
            else:
                err = response.error or "Error"
                self.lbl_status.setText(f"Error: {err[:80]}")

        api_client.post(f"recordings/manual/start/{self.camera_id}", on_response)

    def _stop(self):
        """Detiene la grabación continua manual.

        Outputs: ninguno. Señales: stopped().
        Llamado por: clicked de "Detener". Llama a:
        POST /recordings/manual/stop/<id>.
        """
        if not self.camera_id:
            return

        def on_response(response):
            self._recording = False
            self.lbl_status.setText("Sin grabar")
            self.lbl_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")
            self.stopped.emit()

        api_client.post(f"recordings/manual/stop/{self.camera_id}", on_response)

    def _refresh_status(self):
        if not self.camera_id:
            return

        def on_response(response):
            if response.success and response.data:
                self._recording = response.data.get("recording", False)
                if self._recording:
                    self.lbl_status.setText("Grabando")
                    self.lbl_status.setStyleSheet(
                        f"color: {config.THEME_DANGER}; font-weight: bold;"
                    )
                else:
                    self.lbl_status.setText("Sin grabar")
                    self.lbl_status.setStyleSheet(
                        f"color: {config.THEME_TEXT_MUTED};"
                    )

        api_client.get(f"recordings/manual/status/{self.camera_id}", on_response)


class CameraControlPanel(QWidget):
    """Panel completo de control para la cámara seleccionada (clase pública).

    Rol: contenedor con pestañas (Movimiento / IA-REC / Audio-Luz) que agrupa
    las sub-cards de este módulo + los presets PTZ. Es el "pegamento" entre el
    PTZJoystick (mudo) y las llamadas REST de PTZ.

    Quién la instancia/consume: la vista de control de cámara / Directo; le pasa
        la cámara con set_camera(camera_id, camera_data) y la oculta con clear().

    SEÑALES Qt: no emite señales propias; reexpone las sub-cards (ai_widget,
        rec_widget, audio_widget…) para que la vista observe SUS señales.
        RECIBE las señales move/stop del PTZJoystick interno y las convierte en
        POST /cameras/<id>/ptz/...

    Dependencias: PTZJoystick, las sub-cards (LED/Audio/AI/Recording), api_client,
        GlassCard, config.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.current_camera_id: Optional[int] = None
        
        self._setup_ui()
        self.hide()  # Inicialmente oculto
    
    def _setup_ui(self):
        """
        Layout NUEVO: pestañas en lugar de scroll vertical de 6 widgets.
        Antes todo estaba apilado en un QScrollArea, y aunque cabía, el
        usuario tenía que hacer scroll constantemente para llegar a IA,
        Grabación, etc. Con pestañas todo cabe sin scroll en una pantalla
        razonable y la navegación es más rápida.
        """
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header
        header = QLabel("Controles de Cámara")
        header.setStyleSheet(f"""
            color: {config.THEME_TEXT};
            font-size: 18px;
            font-weight: bold;
            padding: 8px 12px;
            border-bottom: 1px solid {config.GLASS_BORDER};
            background-color: {config.THEME_PRIMARY};
        """)
        outer.addWidget(header)

        # ----- Pestañas -----
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {config.GLASS_BORDER};
                background-color: {config.THEME_PRIMARY};
                border-top: none;
            }}
            QTabBar::tab {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT_MUTED};
                padding: 8px 14px;
                border: 1px solid {config.GLASS_BORDER};
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
                font-size: 11px;
            }}
            QTabBar::tab:selected {{
                background-color: {config.THEME_PRIMARY};
                color: {config.THEME_ACCENT};
                font-weight: bold;
            }}
            QTabBar::tab:hover:!selected {{
                color: {config.THEME_TEXT};
            }}
        """)
        outer.addWidget(self.tabs, 1)

        # ============= PESTAÑA 1: MOVIMIENTO (PTZ + presets) =============
        self.tabs.addTab(self._build_movement_tab(), "Movimiento")

        # ============= PESTAÑA 2: IA + Grabación =============
        self.tabs.addTab(self._build_ai_recording_tab(), "IA / REC")

        # ============= PESTAÑA 3: Audio + LEDs =============
        self.tabs.addTab(self._build_audio_leds_tab(), "Audio / Luz")

    def _scroll_wrap(self, content: QWidget) -> QScrollArea:
        """Envuelve un widget en QScrollArea (fallback si contenido es alto)."""
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setStyleSheet(f"""
            QScrollArea {{ background-color: transparent; }}
            QScrollBar:vertical {{
                background-color: {config.THEME_SECONDARY};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background-color: {config.THEME_ACCENT};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        sa.setWidget(content)
        return sa

    def _build_movement_tab(self) -> QWidget:
        """Tab Movimiento: PTZ joystick + presets."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # PTZ joystick (PTZ 3x3 + zoom + velocidad)
        self.ptz_widget = PTZJoystick()
        self.ptz_widget.move.connect(self._on_ptz_move)
        self.ptz_widget.stop.connect(self._on_ptz_stop)
        layout.addWidget(self.ptz_widget)

        # Card de Presets (separada visualmente)
        preset_card = GlassCard(border_radius=8)
        preset_layout = QVBoxLayout(preset_card)
        preset_layout.setContentsMargins(12, 12, 12, 12)
        preset_layout.setSpacing(8)

        preset_title = QLabel("Presets PTZ")
        preset_title.setStyleSheet(
            f"color: {config.THEME_ACCENT}; font-weight: bold; font-size: 13px;"
        )
        preset_layout.addWidget(preset_title)

        # Row 1: combo de presets ocupa todo el ancho
        self.cmb_presets = QComboBox()
        self.cmb_presets.setPlaceholderText("Ir a preset…")
        self.cmb_presets.setStyleSheet(f"""
            QComboBox {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 6px;
                padding: 6px;
            }}
        """)
        preset_layout.addWidget(self.cmb_presets)

        # Row 2: dos botones a ancho igual
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_go_preset = QPushButton("▶  Ir")
        self.btn_go_preset.setMinimumHeight(34)
        self.btn_go_preset.clicked.connect(self._go_to_preset)
        self.btn_save_preset = QPushButton("Guardar actual")
        self.btn_save_preset.setMinimumHeight(34)
        self.btn_save_preset.clicked.connect(self._save_preset)
        for b in (self.btn_go_preset, self.btn_save_preset):
            b.setStyleSheet(f"""
                QPushButton {{
                    background-color: {config.THEME_SECONDARY};
                    color: {config.THEME_TEXT};
                    border: 1px solid {config.GLASS_BORDER};
                    border-radius: 6px;
                    padding: 6px;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background-color: {config.THEME_ACCENT};
                    color: {config.THEME_PRIMARY};
                }}
            """)
        btn_row.addWidget(self.btn_go_preset, 1)
        btn_row.addWidget(self.btn_save_preset, 1)
        preset_layout.addLayout(btn_row)

        layout.addWidget(preset_card)
        layout.addStretch()
        return self._scroll_wrap(content)

    def _build_ai_recording_tab(self) -> QWidget:
        """Tab IA + Grabación manual."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        self.ai_widget = AIControlWidget()
        layout.addWidget(self.ai_widget)

        self.rec_widget = RecordingControlWidget()
        layout.addWidget(self.rec_widget)

        layout.addStretch()
        return self._scroll_wrap(content)

    def _build_audio_leds_tab(self) -> QWidget:
        """Tab Audio + LEDs."""
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        self.audio_widget = AudioControlWidget()
        layout.addWidget(self.audio_widget)

        self.led_widget = LEDControlWidget()
        layout.addWidget(self.led_widget)

        layout.addStretch()
        return self._scroll_wrap(content)

    def set_camera(self, camera_id: int, camera_data: dict):
        """Configura el panel para una cámara específica.

        IMPORTANTE: NO deshabilitamos secciones aunque el modelo diga que la
        cámara no tiene la capability. Razón: el modelo se rellena al hacer
        ONVIF probe pero algunas cámaras no reportan correctamente sus
        capabilities (Hikvision/Dahua/XiongMai cada una en distinto formato).
        Mejor mostrar todo y dejar que el endpoint backend responda con error
        si la cámara realmente no lo soporta. El usuario al menos puede
        intentar y verá feedback claro.
        """
        self.current_camera_id = camera_id

        is_dual_lens = camera_data.get("is_dual_lens", False)

        # TODOS los widgets disponibles (NO deshabilitar)
        self.led_widget.set_camera(camera_id)
        self.audio_widget.set_camera(camera_id)
        self.ai_widget.set_camera(camera_id, is_dual_lens=is_dual_lens)
        self.rec_widget.set_camera(camera_id)

        # PTZ: solo cargar presets si el modelo dice que es PTZ-capable
        if camera_data.get("has_ptz", False):
            self._load_presets()

        self.show()
    
    def clear(self):
        """Limpia el panel."""
        self.current_camera_id = None
        self.hide()
    
    def _on_ptz_move(self, direction: str, speed: float):
        """Traduce la señal move del joystick en un comando PTZ al backend.

        Inputs: direction (clave de dirección/zoom), speed (ignorada aquí; el
            backend usa su propia velocidad por defecto).
        Señales: slot conectado a PTZJoystick.move.
        Llamado por: el PTZJoystick interno al presionar un botón.
        Llama a: POST /cameras/<id>/ptz/<direction>.
        """
        if not self.current_camera_id:
            return

        def on_response(response):
            if not response.success:
                logger.warning(f"Error PTZ: {response.error}")
        
        api_client.post(f"cameras/{self.current_camera_id}/ptz/{direction}", on_response)
    
    def _on_ptz_stop(self):
        """Traduce la señal stop del joystick en POST /cameras/<id>/ptz/stop.

        Señales: slot conectado a PTZJoystick.stop.
        Llamado por: el PTZJoystick interno al soltar un botón.
        """
        if not self.current_camera_id:
            return

        def on_response(response):
            pass

        api_client.post(f"cameras/{self.current_camera_id}/ptz/stop", on_response)
    
    def _load_presets(self):
        # Cargar presets disponibles
        if not self.current_camera_id:
            return
        
        def on_response(response):
            if response.success:
                self.cmb_presets.clear()
                presets = response.data.get("presets", [])
                for preset in presets:
                    self.cmb_presets.addItem(preset.get("name", "Sin nombre"), preset.get("token"))
        
        api_client.get(f"cameras/{self.current_camera_id}/ptz/presets", on_response)
    
    def _go_to_preset(self):
        if not self.current_camera_id or self.cmb_presets.currentIndex() < 0:
            return
        
        preset_token = self.cmb_presets.currentData()
        
        def on_response(response):
            if not response.success:
                QMessageBox.warning(self, "PTZ", "No se pudo mover al preset")
        
        api_client.post(f"cameras/{self.current_camera_id}/ptz/goto/{preset_token}", on_response)
    
    def _save_preset(self):
        # Diálogo simple para nombre del preset
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Guardar Preset", "Nombre de la posición:")
        if ok and name:
            def on_response(response):
                if response.success:
                    self._load_presets()
            
            api_client.post(f"cameras/{self.current_camera_id}/ptz/preset", on_response, data={"name": name})