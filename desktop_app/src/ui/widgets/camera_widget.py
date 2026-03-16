from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PySide6.QtGui import QPixmap
# ✅ CORREGIDO: Import relativo
from services.api_client import api_client

class CameraWidget(QWidget):
    def __init__(self, camera_id: int, camera_name: str, parent=None):
        super().__init__(parent)
        self.camera_id = camera_id
        self.camera_name = camera_name
        self._has_signal = False

        self._stream_url = api_client.get_stream_url(camera_id)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Video label
        self.video_label = QLabel()
        self.video_label.setMinimumSize(320, 240)
        self.video_label.setStyleSheet("background-color: #1a1a1a; color: #666;")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setText("Conectando...")
        layout.addWidget(self.video_label)

        # Overlay label
        self.overlay_label = QLabel(f"📷 {camera_name}", self.video_label)
        self.overlay_label.setStyleSheet(
            "background-color: rgba(0,0,0,0.5); "
            "color: white; padding: 5px; border-radius: 3px; font-size: 12px;"
        )
        self.overlay_label.move(10, 10)
        self.overlay_label.adjustSize()

        # Network manager
        self._network_manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._buffer = bytearray()

        # Retry timer
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(5000)
        self._retry_timer.timeout.connect(self._start_stream)

        self._start_stream()

    def _start_stream(self):
        self._retry_timer.stop()
        if self._reply:
            self._reply.abort()
            self._reply = None

        request = QNetworkRequest(QUrl(self._stream_url))
        self._reply = self._network_manager.get(request)
        self._reply.readyRead.connect(self._on_data)
        self._reply.finished.connect(self._on_disconnected)
        self._buffer.clear()
        self.video_label.setText("Cargando stream...")

    def _on_data(self):
        if not self._reply:
            return

        self._buffer.extend(bytes(self._reply.readAll()))

        # Look for JPEG frames (start: 0xFFD8, end: 0xFFD9)
        while True:
            # ✅ CORREGIDO: Bytes literales correctos
            start_idx = self._buffer.find(b'\xff\xd8')
            if start_idx == -1:
                break

            end_idx = self._buffer.find(b'\xff\xd9', start_idx)
            if end_idx == -1:
                break

            # Extract JPEG
            jpeg_bytes = bytes(self._buffer[start_idx:end_idx + 2])
            self._buffer = self._buffer[end_idx + 2:]

            # Display
            pixmap = QPixmap()
            if pixmap.loadFromData(jpeg_bytes, "JPEG"):
                scaled = pixmap.scaled(
                    self.video_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self.video_label.setPixmap(scaled)
                if not self._has_signal:
                    self._has_signal = True
                    self.overlay_label.setText(f"🟢 {self.camera_name}")
                    self.overlay_label.setStyleSheet(
                        "background-color: rgba(0,128,0,0.7); "
                        "color: white; padding: 5px; border-radius: 3px; font-size: 12px;"
                    )
                    self.overlay_label.adjustSize()

    def _on_disconnected(self):
        self._has_signal = False
        self.video_label.clear()
        self.video_label.setText("Sin señal")
        self.overlay_label.setText(f"🔴 {self.camera_name}")
        self.overlay_label.setStyleSheet(
            "background-color: rgba(128,0,0,0.7); "
            "color: white; padding: 5px; border-radius: 3px; font-size: 12px;"
        )
        self.overlay_label.adjustSize()
        self._retry_timer.start()

    def stop_stream(self):
        self._retry_timer.stop()
        if self._reply:
            self._reply.abort()
            self._reply = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep overlay at top-left
        self.overlay_label.move(10, 10)

        # Rescale current pixmap if exists
        if self.video_label.pixmap():
            scaled = self.video_label.pixmap().scaled(
                self.video_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.video_label.setPixmap(scaled)