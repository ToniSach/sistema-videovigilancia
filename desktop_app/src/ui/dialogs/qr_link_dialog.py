"""
================================================================================
MÓDULO: desktop_app.ui.dialogs.qr_link_dialog — Vinculación de móvil por QR
================================================================================

PROPÓSITO
    Diálogo modal «Vincular móvil»: pide un QR al backend y lo muestra para que
    la app del celular lo escanee y se registre contra el NVR sin teclear nada.
    Forma parte del Pipeline #13 (vinculación de dispositivos para
    notificaciones / acceso desde el móvil).

RESPONSABILIDAD
    - Descargar el PNG del QR en un hilo aparte (no bloquear el UI).
    - Mostrarlo escalado, con cuenta atrás de validez y botón «Regenerar».
    - Avisar cuando expira o falla.

FLUJO DE VINCULACIÓN (extremo a extremo)
  1. Este diálogo hace POST /api/v1/qr/generate.
  2. El backend crea un link_token (1 solo uso, 5 min) y devuelve un PNG.
  3. El usuario escanea el QR desde la app móvil.
  4. La app móvil hace POST /api/v1/devices/register con el link_token + datos.
  5. El backend devuelve JWT real al móvil → consume TODA la API existente.

    Como el link_token caduca en 5 min, hay un timer que muestra cuánto queda y
    un botón para regenerar sin cerrar el diálogo.

DEPENDENCIAS
    - services/api_client.py (singleton) — petición autenticada con refresh JWT.
    - config — no se usa directamente aquí (importado por consistencia).
    - PySide6 (QThread para la descarga, QTimer para la cuenta atrás).

COMPONENTES RELACIONADOS
    - main_window.py — botón «Vincular móvil» del menú lateral
      (`_open_qr_link_dialog`).
    - Backend: routes de `qr/` y `devices/` que cierran el ciclo.

QUIÉN LO ABRE
    MainWindow, desde el botón «Vincular móvil» del sidebar.
================================================================================
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QByteArray
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QMessageBox, QSizePolicy,
)

from desktop_app.src.services.api_client import api_client
from desktop_app.src.config import config

logger = logging.getLogger(__name__)

# El link_token expira en 5 minutos en el backend
QR_TTL_SECONDS = 300


class _QRFetchWorker(QThread):
    """Worker que descarga el PNG del QR sin bloquear el UI.

    Va a través de api_client._make_request (no requests crudo) para heredar
    el refresco-y-reintento automático ante 401: el access_token JWT vive
    ~15 min y antes el QR fallaba con "Token expirado" si el diálogo se abría
    pasado ese tiempo (la petición cruda no refrescaba el token).
    """
    fetched = Signal(bytes)
    failed = Signal(str)

    def run(self):
        try:
            # stream=True → _make_request devuelve el objeto Response crudo en
            # .data (el QR es un PNG binario, no JSON). El 401 se maneja dentro
            # de _make_request: refresca con el refresh_token y reintenta.
            resp = api_client._make_request("POST", "qr/generate", stream=True)
            if not resp.success:
                self.failed.emit(resp.error or "Sesión expirada")
                return
            http = resp.data  # requests.Response
            if http is None or http.status_code != 200:
                code = getattr(http, "status_code", "?")
                text = getattr(http, "text", "")[:200] if http is not None else ""
                self.failed.emit(f"HTTP {code}: {text}")
                return
            self.fetched.emit(http.content)
        except Exception as e:
            self.failed.emit(str(e))


class QRLinkDialog(QDialog):
    """Diálogo modal que muestra el QR de vinculación de móvil.

    ROL
        Orquestar: lanzar `_QRFetchWorker`, pintar el QR, llevar la cuenta atrás
        de validez y permitir regenerar. La descarga ocurre en el worker; este
        diálogo solo coordina UI + timers.

    QUIÉN LO INSTANCIA
        MainWindow (`_open_qr_link_dialog`).

    RESULTADO
        Informativo; se cierra con accept(). La vinculación real la confirma el
        móvil contra el backend, no este diálogo.

    DEPENDENCIAS
        api_client (vía el worker), QTimer (cuenta atrás), QThread (descarga).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vincular dispositivo móvil")
        self.setMinimumSize(420, 580)
        self.setModal(True)

        self._worker: Optional[_QRFetchWorker] = None
        self._remaining_s = 0
        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick)

        self._setup_ui()
        self._fetch_qr()

    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        # Título
        title = QLabel("Vincular nuevo dispositivo")
        title.setStyleSheet(
            "color: #f1f5f9; font-size: 18px; font-weight: bold;"
        )
        layout.addWidget(title)

        # Instrucciones
        info = QLabel(
            "1. Abre la app móvil del NVR en tu celular.\n"
            "2. Toca «Vincular con NVR».\n"
            "3. Apunta la cámara al código QR de abajo.\n"
            "4. El móvil queda autenticado y podrá ver streams, "
            "recibir notificaciones, ver grabaciones y controlar las cámaras."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #cbd5e1; font-size: 12px; line-height: 1.4;")
        layout.addWidget(info)

        # Frame del QR
        self.qr_frame = QFrame()
        self.qr_frame.setStyleSheet(
            "background-color: white; border-radius: 8px; padding: 12px;"
        )
        self.qr_frame.setMinimumSize(320, 320)
        self.qr_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        qr_layout = QVBoxLayout(self.qr_frame)
        qr_layout.setContentsMargins(8, 8, 8, 8)

        self.lbl_qr = QLabel("Generando código QR…")
        self.lbl_qr.setAlignment(Qt.AlignCenter)
        self.lbl_qr.setStyleSheet("color: #1e293b; font-size: 13px;")
        self.lbl_qr.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        qr_layout.addWidget(self.lbl_qr)

        layout.addWidget(self.qr_frame, 1)

        # Estado / countdown
        self.lbl_status = QLabel("Esperando…")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet("color: #94a3b8; font-size: 11px;")
        layout.addWidget(self.lbl_status)

        # Botones
        btn_row = QHBoxLayout()
        btn_style = """
            QPushButton {
                background-color: #1e293b; color: #f1f5f9;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 6px; padding: 8px 16px;
            }
            QPushButton:hover:!disabled { background-color: #334155; }
            QPushButton:disabled { color: #475569; }
        """

        self.btn_refresh = QPushButton("Regenerar QR")
        self.btn_refresh.setStyleSheet(btn_style)
        self.btn_refresh.clicked.connect(self._fetch_qr)
        btn_row.addWidget(self.btn_refresh)

        btn_row.addStretch()

        self.btn_close = QPushButton("Cerrar")
        self.btn_close.setStyleSheet(btn_style)
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close)

        layout.addLayout(btn_row)

        # Estilo del diálogo
        self.setStyleSheet(
            "QDialog { background-color: #0f172a; }"
        )

    # ------------------------------------------------------------------
    def _fetch_qr(self):
        """Solicita un nuevo QR al backend en un thread aparte.

        Inputs: ninguno (usa api_client.tokens para validar sesión).
        Outputs: lanza `_QRFetchWorker`; el resultado llega por señal a
            `_on_qr_fetched` / `_on_qr_failed`.
        Llamado por: __init__ (carga inicial) y el botón «Regenerar QR».
        Llama a (backend): POST /api/v1/qr/generate (dentro del worker).
        """
        # AuthTokens es un dataclass con atributos access_token/refresh_token,
        # NO un dict. Antes accedíamos con .get(...) y [...] como si fuera
        # un dict → AttributeError 'AuthTokens' object has no attribute 'get'.
        if not api_client.tokens or not api_client.tokens.access_token:
            self._show_error("No has iniciado sesión.")
            return

        self.btn_refresh.setEnabled(False)
        self.lbl_qr.setText("Generando código QR…")
        self.lbl_qr.setPixmap(QPixmap())  # libera el anterior
        self.lbl_status.setText("Conectando al servidor…")

        self._worker = _QRFetchWorker(parent=self)
        self._worker.fetched.connect(self._on_qr_fetched, type=Qt.QueuedConnection)
        self._worker.failed.connect(self._on_qr_failed, type=Qt.QueuedConnection)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_qr_fetched(self, png_bytes: bytes):
        # Slot de la señal `fetched`: decodifica el PNG recibido, lo escala al
        # frame manteniendo aspecto y arranca la cuenta atrás de validez.
        # Llamado por: _QRFetchWorker.fetched (Qt.QueuedConnection → hilo UI).
        try:
            image = QImage.fromData(QByteArray(png_bytes), "PNG")
            if image.isNull():
                self._show_error("Respuesta del servidor no es un PNG válido.")
                return

            # Escalar al tamaño del frame manteniendo aspecto
            target = min(self.qr_frame.width(), self.qr_frame.height()) - 32
            pixmap = QPixmap.fromImage(image).scaled(
                target, target,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation  # QR sí queremos antialiasing
            )
            self.lbl_qr.setPixmap(pixmap)
            self.lbl_qr.setText("")

            self._remaining_s = QR_TTL_SECONDS
            self._countdown.start()
            self._tick()
            self.btn_refresh.setEnabled(True)

        except Exception as e:
            self._show_error(f"Error procesando QR: {e}")

    def _on_qr_failed(self, msg: str):
        logger.error(f"Error obteniendo QR: {msg}")
        self._show_error(f"No se pudo generar el QR: {msg}")

    def _show_error(self, msg: str):
        self.lbl_qr.setText("\n\n" + msg)
        self.lbl_status.setText("")
        self.btn_refresh.setEnabled(True)
        self._countdown.stop()

    def _tick(self):
        # Slot del QTimer de 1s: actualiza «Válido por M:SS» y, al llegar a 0,
        # marca el QR como expirado (hay que pulsar «Regenerar QR»).
        if self._remaining_s <= 0:
            self._countdown.stop()
            self.lbl_status.setText("Código expirado. Pulsa «Regenerar QR».")
            self.lbl_status.setStyleSheet("color: #ef4444; font-size: 11px;")
            return
        m, s = divmod(self._remaining_s, 60)
        self.lbl_status.setText(f"Válido por {m:01d}:{s:02d}")
        self.lbl_status.setStyleSheet("color: #94a3b8; font-size: 11px;")
        self._remaining_s -= 1

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        # Limpieza al cerrar: detiene la cuenta atrás y, si el worker sigue
        # descargando, lo interrumpe y espera (máx. 1s) para no dejar hilos
        # huérfanos accediendo a widgets ya destruidos.
        try:
            self._countdown.stop()
        except Exception:
            pass
        # El worker (objeto C++) puede haber sido ya destruido por Qt al cerrar
        # rápido; acceder a .isRunning() lanzaría RuntimeError. Lo envolvemos.
        try:
            if self._worker is not None and self._worker.isRunning():
                self._worker.requestInterruption()
                self._worker.wait(1000)
        except RuntimeError:
            pass
        super().closeEvent(event)
