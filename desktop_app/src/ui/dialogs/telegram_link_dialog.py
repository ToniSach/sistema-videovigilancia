"""
================================================================================
MÓDULO: desktop_app.ui.dialogs.telegram_link_dialog — Vinculación de Telegram
================================================================================

PROPÓSITO
    Diálogo modal «Vincular Telegram»: pide un código de vinculación al backend,
    lo muestra en grande con instrucciones y hace polling hasta que el usuario
    lo envía al bot y el backend confirma. Parte del Pipeline #13
    (notificaciones: alertas de cámara entregadas por Telegram).

RESPONSABILIDAD
    - Generar el código (POST /telegram/generate-code) en un hilo aparte.
    - Mostrar código + deep link + botones «Abrir Telegram» / «Copiar».
    - Sondear el estado (GET /telegram/link-status) cada 2s y cerrar al vincular.
    - Llevar la cuenta atrás de validez y permitir regenerar al expirar.

FLUJO
  1. Al abrir → POST /telegram/generate-code → {code, bot_username,
     telegram_deep_link, expires_in_seconds, bot_configured}.
  2. Muestra el código en grande, los pasos y un botón «Abrir Telegram» que usa
     el deep link t.me/<bot>?start=<code>.
  3. Cada 2s hace GET /telegram/link-status?code=XXX.
  4. Cuando `linked=true` → estado de éxito + cierre automático (~2.5s).
  5. Si el código expira → permite regenerar con «Nuevo código».

DEPENDENCIAS
    - services/api_client.py — solo para leer el access_token (las llamadas se
      hacen con `requests` directo dentro de `_APIWorker`, no vía api_client).
    - config.API_BASE_URL — base de la URL del backend.
    - PySide6 (QThread para REST, varios QTimer: polling, cuenta atrás, cierre).

COMPONENTES RELACIONADOS
    - views/notifications_view.py — lo abre desde su botón de vincular Telegram.
    - Backend: rutas `telegram/generate-code` y `telegram/link-status`.

QUIÉN LO ABRE
    notifications_view.py, desde la sección de canales de notificación.
================================================================================
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QClipboard
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QMessageBox, QSizePolicy, QApplication,
)

import requests

from desktop_app.src.services.api_client import api_client
from desktop_app.src.config import config

logger = logging.getLogger(__name__)


class _APIWorker(QThread):
    """Worker genérico que hace una llamada REST y emite el JSON resultante.

    Reutilizable: lo usan tanto la generación del código como cada ronda de
    polling. Lee el access_token del singleton api_client pero hace la petición
    con `requests` directo (no pasa por api_client._make_request).

    Señales:
        ok(dict)     — JSON de respuesta en éxito (<400).
        failed(str)  — mensaje de error (HTTP>=400 o excepción).
    """
    ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, method: str, path: str, params: Optional[dict] = None,
                 body: Optional[dict] = None, parent=None):
        super().__init__(parent)
        self._method = method
        self._path = path
        self._params = params or {}
        self._body = body

    def run(self):
        try:
            # api_client.tokens es un objeto AuthTokens (no un dict): hay que
            # leer .access_token, no .get(...). Antes esto lanzaba
            # "'AuthTokens' object has no attribute 'get'" al generar el código.
            _tok = getattr(api_client, "tokens", None)
            token = getattr(_tok, "access_token", "") if _tok else ""
            url = f"{config.API_BASE_URL.rstrip('/')}{self._path}"
            headers = {"Authorization": f"Bearer {token}"}
            r = requests.request(
                self._method, url,
                params=self._params, json=self._body,
                headers=headers, timeout=10,
            )
            if r.status_code >= 400:
                try:
                    data = r.json()
                    msg = data.get("error") or f"HTTP {r.status_code}"
                except Exception:
                    msg = f"HTTP {r.status_code}"
                self.failed.emit(msg)
                return
            self.ok.emit(r.json())
        except Exception as e:
            self.failed.emit(str(e))


class TelegramLinkDialog(QDialog):
    """Diálogo modal de vinculación de Telegram.

    ROL
        Coordinar: generar código, mostrarlo, sondear estado y cerrarse solo al
        confirmarse la vinculación. Gestiona tres QTimer (polling, cuenta atrás
        de validez, cierre diferido) y delega cada llamada REST en `_APIWorker`.

    QUIÉN LO INSTANCIA
        notifications_view.py.

    RESULTADO
        Se cierra con accept() al vincular (o al pulsar «Cerrar»). El efecto real
        (chat de Telegram asociado al usuario) lo persiste el backend; este
        diálogo solo lo refleja.

    DEPENDENCIAS
        api_client (token), requests (vía _APIWorker), QTimer, QDesktopServices
        (abrir el deep link), QClipboard (copiar el comando).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vincular Telegram")
        self.setMinimumSize(540, 620)
        self.setModal(True)
        self.setStyleSheet(
            "QDialog { background-color: #0f172a; }"
        )

        self._code: str = ""
        self._bot_username: str = ""
        self._deep_link: str = ""
        self._remaining_s = 0

        self._worker: Optional[_APIWorker] = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._poll_status)

        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick_countdown)

        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self.accept)

        self._setup_ui()
        self._fetch_code()

    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(16)

        # Título
        title = QLabel("Vincular Telegram")
        title.setStyleSheet(
            "color: #f1f5f9; font-size: 20px; font-weight: bold;"
        )
        layout.addWidget(title)

        # Subtítulo / explicación corta
        subtitle = QLabel(
            "Recibe en Telegram alertas con foto y video cuando tus cámaras "
            "detecten algo. Sigue los pasos:"
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #cbd5e1; font-size: 12px;")
        layout.addWidget(subtitle)

        # Pasos numerados
        steps = QFrame()
        steps.setStyleSheet(
            "QFrame { background-color: #1e293b; border-radius: 8px; padding: 12px; }"
        )
        steps_layout = QVBoxLayout(steps)
        steps_layout.setSpacing(8)

        self.lbl_step1 = self._step_label(
            "1. Abre Telegram y busca el bot del NVR."
        )
        self.lbl_step2 = self._step_label(
            "2. Envíale el código que ves abajo."
        )
        self.lbl_step3 = self._step_label(
            "3. Espera la confirmación (recibirás un mensaje del bot)."
        )
        steps_layout.addWidget(self.lbl_step1)
        steps_layout.addWidget(self.lbl_step2)
        steps_layout.addWidget(self.lbl_step3)
        layout.addWidget(steps)

        # Caja del código
        self.code_frame = QFrame()
        self.code_frame.setStyleSheet(
            "QFrame { background-color: #1e293b; border: 2px solid #38bdf8; "
            "border-radius: 10px; padding: 18px; }"
        )
        code_layout = QVBoxLayout(self.code_frame)
        code_layout.setSpacing(6)

        code_caption = QLabel("Tu código de vinculación")
        code_caption.setAlignment(Qt.AlignCenter)
        code_caption.setStyleSheet("color: #94a3b8; font-size: 11px;")
        code_layout.addWidget(code_caption)

        self.lbl_code = QLabel("------")
        self.lbl_code.setAlignment(Qt.AlignCenter)
        self.lbl_code.setStyleSheet(
            "color: #38bdf8; font-size: 42px; font-weight: bold; "
            "letter-spacing: 6px; font-family: 'Consolas', 'Courier New', monospace;"
        )
        self.lbl_code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        code_layout.addWidget(self.lbl_code)

        self.lbl_command = QLabel("…")
        self.lbl_command.setAlignment(Qt.AlignCenter)
        self.lbl_command.setStyleSheet(
            "color: #cbd5e1; font-size: 12px; "
            "font-family: 'Consolas', 'Courier New', monospace;"
        )
        self.lbl_command.setTextInteractionFlags(Qt.TextSelectableByMouse)
        code_layout.addWidget(self.lbl_command)

        layout.addWidget(self.code_frame)

        # Botón abrir Telegram + copiar
        actions = QHBoxLayout()
        actions.setSpacing(8)

        self.btn_open_telegram = QPushButton("Abrir Telegram")
        self.btn_open_telegram.setStyleSheet(self._primary_button_style())
        self.btn_open_telegram.setMinimumHeight(40)
        self.btn_open_telegram.clicked.connect(self._open_telegram)
        self.btn_open_telegram.setEnabled(False)
        actions.addWidget(self.btn_open_telegram, 2)

        self.btn_copy = QPushButton("Copiar")
        self.btn_copy.setStyleSheet(self._secondary_button_style())
        self.btn_copy.setMinimumHeight(40)
        self.btn_copy.clicked.connect(self._copy_code)
        self.btn_copy.setEnabled(False)
        actions.addWidget(self.btn_copy, 1)

        layout.addLayout(actions)

        # Estado y countdown
        self.lbl_status = QLabel("Generando código…")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet(
            "color: #94a3b8; font-size: 12px; padding: 6px;"
        )
        layout.addWidget(self.lbl_status)

        layout.addStretch()

        # Botones inferiores
        footer = QHBoxLayout()

        self.btn_refresh = QPushButton("Nuevo código")
        self.btn_refresh.setStyleSheet(self._secondary_button_style())
        self.btn_refresh.clicked.connect(self._fetch_code)
        footer.addWidget(self.btn_refresh)

        footer.addStretch()

        self.btn_close = QPushButton("Cerrar")
        self.btn_close.setStyleSheet(self._secondary_button_style())
        self.btn_close.clicked.connect(self.accept)
        footer.addWidget(self.btn_close)

        layout.addLayout(footer)

    def _step_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #e2e8f0; font-size: 12px;")
        return lbl

    def _primary_button_style(self) -> str:
        return """
            QPushButton {
                background-color: #38bdf8; color: #0f172a;
                border: none; border-radius: 8px;
                padding: 10px 16px; font-weight: bold;
            }
            QPushButton:hover:!disabled { background-color: #7dd3fc; }
            QPushButton:disabled { background-color: #475569; color: #94a3b8; }
        """

    def _secondary_button_style(self) -> str:
        return """
            QPushButton {
                background-color: #1e293b; color: #f1f5f9;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 8px; padding: 8px 14px;
            }
            QPushButton:hover:!disabled { background-color: #334155; }
            QPushButton:disabled { color: #475569; }
        """

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def _fetch_code(self):
        """Pide un nuevo código de vinculación y reinicia el estado del diálogo.

        Inputs: ninguno (lee api_client.tokens para validar sesión).
        Outputs: lanza `_APIWorker`; el resultado llega a `_on_code_received` /
            `_on_code_failed`. Detiene polling/cuenta atrás previos.
        Llamado por: __init__ y el botón «Nuevo código».
        Llama a (backend): POST /telegram/generate-code.
        """
        if not api_client.tokens:
            self._show_error("Sesión expirada. Vuelve a iniciar sesión.")
            return

        self._poll_timer.stop()
        self._countdown.stop()
        self.btn_refresh.setEnabled(False)
        self.btn_open_telegram.setEnabled(False)
        self.btn_copy.setEnabled(False)
        self.lbl_code.setText("……")
        self.lbl_command.setText("Generando…")
        self.lbl_status.setText("Generando código…")
        self.lbl_status.setStyleSheet("color: #94a3b8; font-size: 12px;")

        self._worker = _APIWorker("POST", "/telegram/generate-code", parent=self)
        self._worker.ok.connect(self._on_code_received, type=Qt.QueuedConnection)
        self._worker.failed.connect(self._on_code_failed, type=Qt.QueuedConnection)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_code_received(self, data: dict):
        # Slot de éxito de la generación: vuelca código/deep-link a la UI,
        # habilita botones y arranca cuenta atrás + polling. Si el bot no está
        # configurado en el servidor, avisa y no inicia el sondeo.
        # Llamado por: _APIWorker.ok (Qt.QueuedConnection → hilo UI).
        payload = data.get("data") or {}
        self._code = payload.get("code", "")
        self._bot_username = payload.get("bot_username", "")
        self._deep_link = payload.get("telegram_deep_link") or ""
        bot_configured = payload.get("bot_configured", False)

        self.lbl_code.setText(self._code)
        self.lbl_command.setText(f"/vincular {self._code}")

        if not bot_configured:
            self.lbl_status.setText(
                "El bot de Telegram no está configurado en el servidor. "
                "Pídele al administrador que lo configure en Sistema → Telegram."
            )
            self.lbl_status.setStyleSheet("color: #f59e0b; font-size: 12px;")
            self.btn_refresh.setEnabled(True)
            return

        if self._bot_username:
            self.lbl_step1.setText(
                f"1. Abre Telegram y busca el bot <b>@{self._bot_username}</b>."
            )
            self.lbl_step1.setTextFormat(Qt.RichText)

        self.btn_open_telegram.setEnabled(bool(self._deep_link))
        self.btn_copy.setEnabled(True)
        self.btn_refresh.setEnabled(True)

        expires_in = int(payload.get("expires_in_seconds", 300))
        self._remaining_s = expires_in
        self._tick_countdown()
        self._countdown.start()
        self._poll_timer.start()

    def _on_code_failed(self, msg: str):
        self._show_error(f"No se pudo generar el código: {msg}")

    # ------------------------------------------------------------------
    # Polling de status
    # ------------------------------------------------------------------
    def _poll_status(self):
        """Sondea si el código ya fue vinculado (una llamada por cada tick).

        Inputs: usa self._code.
        Outputs: lanza un `_APIWorker` efímero; el resultado va a `_on_status`.
            Los fallos de red se ignoran (la siguiente ronda reintenta).
        Llamado por: el QTimer `_poll_timer` cada 2s.
        Llama a (backend): GET /telegram/link-status?code=<code>.
        """
        if not self._code:
            return
        worker = _APIWorker("GET", "/telegram/link-status",
                            params={"code": self._code}, parent=self)
        worker.ok.connect(self._on_status, type=Qt.QueuedConnection)
        # Errores de polling se ignoran (la próxima ronda lo reintenta)
        worker.failed.connect(lambda _msg: None)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_status(self, data: dict):
        # Slot del resultado del polling: si `linked` → muestra éxito y programa
        # el cierre automático; si `expired` → para timers e invita a regenerar.
        # Llamado por: _APIWorker.ok del worker de polling.
        payload = data.get("data") or {}
        if payload.get("linked"):
            self._poll_timer.stop()
            self._countdown.stop()
            self.btn_open_telegram.setEnabled(False)
            self.btn_refresh.setEnabled(False)
            chat = payload.get("chat") or {}
            who = chat.get("telegram_username") or "tu cuenta de Telegram"
            self.lbl_status.setText(
                f"¡Vinculado correctamente con {who}! El diálogo se cerrará…"
            )
            self.lbl_status.setStyleSheet(
                "color: #22c55e; font-size: 14px; font-weight: bold;"
            )
            self._close_timer.start(2500)
            return

        if payload.get("expired"):
            self._poll_timer.stop()
            self._countdown.stop()
            self.lbl_status.setText("Código expirado. Pulsa «Nuevo código».")
            self.lbl_status.setStyleSheet("color: #ef4444; font-size: 12px;")

    # ------------------------------------------------------------------
    def _tick_countdown(self):
        if self._remaining_s <= 0:
            self._countdown.stop()
            self.lbl_status.setText("Código expirado. Pulsa «Nuevo código».")
            self.lbl_status.setStyleSheet("color: #ef4444; font-size: 12px;")
            return
        m, s = divmod(self._remaining_s, 60)
        self.lbl_status.setText(
            f"Esperando confirmación del bot…  válido {m:01d}:{s:02d}"
        )
        self.lbl_status.setStyleSheet("color: #94a3b8; font-size: 12px;")
        self._remaining_s -= 1

    def _open_telegram(self):
        # Abre el deep link t.me/<bot>?start=<code> en la app/cliente de
        # Telegram del sistema. Llamado por: botón «Abrir Telegram».
        if self._deep_link:
            QDesktopServices.openUrl(QUrl(self._deep_link))

    def _copy_code(self):
        # Copia el comando «/vincular <code>» al portapapeles para pegarlo en el
        # chat del bot. Llamado por: botón «Copiar».
        if not self._code:
            return
        QApplication.clipboard().setText(f"/vincular {self._code}")
        self.lbl_status.setText("Comando copiado. Pégalo en el chat del bot.")
        self.lbl_status.setStyleSheet("color: #22c55e; font-size: 12px;")

    def _show_error(self, msg: str):
        self.lbl_code.setText("")
        self.lbl_command.setText("")
        self.lbl_status.setText(msg)
        self.lbl_status.setStyleSheet("color: #ef4444; font-size: 12px;")
        self.btn_refresh.setEnabled(True)
        self.btn_open_telegram.setEnabled(False)
        self.btn_copy.setEnabled(False)

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        # Limpieza al cerrar: detiene los tres timers (polling, cuenta atrás,
        # cierre diferido) e interrumpe el worker en curso (máx. 0.5s) para no
        # dejar hilos accediendo a widgets destruidos.
        self._poll_timer.stop()
        self._countdown.stop()
        self._close_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(500)
        super().closeEvent(event)
