# desktop_app/src/ui/views/settings_view.py
"""
Vista de configuración del sistema.
"""
import logging
from typing import Optional

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                               QPushButton, QLineEdit, QCheckBox, QSpinBox,
                               QFormLayout, QGroupBox, QTabWidget, QComboBox,
                               QMessageBox, QFileDialog)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from desktop_app.src.config import config
from desktop_app.src.services.api_client import api_client
from desktop_app.src.ui.components.glass_card import GlassCard

logger = logging.getLogger(__name__)


class SettingsView(QWidget):
    """Vista de configuración general."""
    
    def __init__(self, parent=None):
        super().__init__(parent)

        self._setup_ui()
        # NO cargamos en __init__: requiere JWT y aún no hay sesión.
        # Lo hacemos al mostrar la vista (showEvent).

    def showEvent(self, event):
        """Cargar config y cámaras al mostrar la vista."""
        super().showEvent(event)
        from desktop_app.src.services.api_client import api_client
        if api_client.tokens:
            self._load_settings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        
        # Título + botón ayuda
        title_row = QHBoxLayout()
        title = QLabel("⚙️ Configuración del Sistema")
        title.setStyleSheet(f"""
            color: {config.THEME_TEXT};
            font-size: 24px;
            font-weight: bold;
        """)
        title_row.addWidget(title)

        from desktop_app.src.ui.components.help_button import HelpButton
        title_row.addWidget(HelpButton("settings_view", parent=self))

        title_row.addStretch()
        layout.addLayout(title_row)
        
        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 8px;
                background-color: {config.GLASS_BG};
            }}
            QTabBar::tab {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT_MUTED};
                border: 1px solid {config.GLASS_BORDER};
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 10px 20px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
            }}
            QTabBar::tab:hover {{
                color: {config.THEME_TEXT};
            }}
        """)
        
        # Tab General
        self._setup_general_tab()
        
        # Tab Notificaciones
        self._setup_notifications_tab()
        
        # Tab Almacenamiento
        self._setup_storage_tab()
        
        layout.addWidget(self.tabs)
        
        # Botones de acción
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.btn_reset = QPushButton("Restaurar Defaults")
        self.btn_reset.clicked.connect(self._load_settings)
        btn_layout.addWidget(self.btn_reset)
        
        self.btn_save = QPushButton("💾 Guardar Cambios")
        self.btn_save.setMinimumHeight(40)
        self.btn_save.setStyleSheet(f"""
            QPushButton {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
                border: none;
                border-radius: 6px;
                font-weight: bold;
                padding: 0 30px;
            }}
        """)
        self.btn_save.clicked.connect(self._save_settings)
        btn_layout.addWidget(self.btn_save)
        
        layout.addLayout(btn_layout)
        layout.addStretch()
    
    def _setup_general_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(16)
        
        # Configuración de Video
        video_group = QGroupBox("Configuración de Video")
        video_layout = QFormLayout(video_group)
        
        self.spin_pre_buffer = QSpinBox()
        self.spin_pre_buffer.setRange(1, 60)
        self.spin_pre_buffer.setValue(10)
        self.spin_pre_buffer.setSuffix(" segundos")
        video_layout.addRow("Buffer Pre-Evento:", self.spin_pre_buffer)
        
        self.spin_quality = QSpinBox()
        self.spin_quality.setRange(50, 100)
        self.spin_quality.setValue(75)
        self.spin_quality.setSuffix("%")
        video_layout.addRow("Calidad MJPEG:", self.spin_quality)
        
        layout.addWidget(video_group)
        
        # Configuración de IA
        ai_group = QGroupBox("Inteligencia Artificial")
        ai_layout = QFormLayout(ai_group)
        
        self.cmb_ai_model = QComboBox()
        self.cmb_ai_model.addItems(["YOLOv8n (Nano)", "YOLOv8s (Small)", "YOLOv8m (Medium)"])
        ai_layout.addRow("Modelo:", self.cmb_ai_model)
        
        self.spin_confidence = QSpinBox()
        self.spin_confidence.setRange(30, 90)
        self.spin_confidence.setValue(45)
        self.spin_confidence.setSuffix("%")
        ai_layout.addRow("Confianza Mínima:", self.spin_confidence)
        
        self.chk_save_snapshots = QCheckBox("Guardar snapshots de detecciones")
        self.chk_save_snapshots.setChecked(True)
        ai_layout.addRow(self.chk_save_snapshots)
        
        layout.addWidget(ai_group)
        layout.addStretch()
        
        self.tabs.addTab(widget, "General")
    
    def _setup_notifications_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(16)
        
        # Telegram
        telegram_group = QGroupBox("Notificaciones Telegram")
        telegram_layout = QFormLayout(telegram_group)

        # Banner de estado
        self.lbl_telegram_status = QLabel("⏳ Cargando configuración…")
        self.lbl_telegram_status.setStyleSheet(
            f"background:#1e293b; color:{config.THEME_TEXT_MUTED}; "
            f"padding:8px; border-radius:4px; font-size:11px;"
        )
        self.lbl_telegram_status.setWordWrap(True)
        telegram_layout.addRow(self.lbl_telegram_status)

        self.txt_bot_token = QLineEdit()
        self.txt_bot_token.setPlaceholderText("123456789:AABBccDDeeFFggHHiiJJkkLL...")
        self.txt_bot_token.setEchoMode(QLineEdit.Password)
        # Botón "👁" para mostrar/ocultar el token
        telegram_layout.addRow("Bot Token:", self.txt_bot_token)

        self.chk_show_token = QCheckBox("Mostrar token")
        self.chk_show_token.toggled.connect(
            lambda on: self.txt_bot_token.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password
            )
        )
        telegram_layout.addRow(self.chk_show_token)

        self.txt_chat_id = QLineEdit()
        self.txt_chat_id.setPlaceholderText("123456789  (puedes poner varios separados por coma)")
        telegram_layout.addRow("Chat ID(s):", self.txt_chat_id)

        help_telegram = QLabel(
            "<i>Las credenciales se leen del archivo <code>.env</code> al primer "
            "arranque. Aquí puedes verlas y editarlas — se guardarán en la BD "
            "y sobreescribirán las del .env.</i>"
        )
        help_telegram.setWordWrap(True)
        help_telegram.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 10px;"
        )
        telegram_layout.addRow(help_telegram)

        self.btn_test_telegram = QPushButton("Probar conexión Telegram")
        self.btn_test_telegram.clicked.connect(self._test_telegram)
        telegram_layout.addRow(self.btn_test_telegram)
        
        self.chk_notify_person = QCheckBox("Personas detectadas")
        self.chk_notify_person.setChecked(True)
        telegram_layout.addRow(self.chk_notify_person)
        
        self.chk_notify_vehicle = QCheckBox("Vehículos detectados")
        self.chk_notify_vehicle.setChecked(True)
        telegram_layout.addRow(self.chk_notify_vehicle)
        
        self.chk_notify_motion = QCheckBox("Movimiento (solo sin IA)")
        telegram_layout.addRow(self.chk_notify_motion)

        layout.addWidget(telegram_group)

        # Bloque de pruebas — útil para validar que Telegram funciona
        test_group = QGroupBox("Enviar evento de prueba")
        test_layout = QVBoxLayout(test_group)

        help_lbl = QLabel(
            "<i>Dispara un evento simulado de 'persona' en una cámara activa "
            "y envía a Telegram una foto inmediata + un video de ~20 segundos "
            "(10s pre-evento + 10s post-evento). Útil para verificar que el bot "
            "está bien configurado.</i>"
        )
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 11px;")
        test_layout.addWidget(help_lbl)

        cam_row = QHBoxLayout()
        cam_row.addWidget(QLabel("Cámara:"))
        self.cmb_test_camera = QComboBox()
        self.cmb_test_camera.setMinimumWidth(220)
        cam_row.addWidget(self.cmb_test_camera, 1)
        self.btn_reload_cams = QPushButton("🔄")
        self.btn_reload_cams.setMaximumWidth(40)
        self.btn_reload_cams.clicked.connect(self._reload_test_cameras)
        cam_row.addWidget(self.btn_reload_cams)
        test_layout.addLayout(cam_row)

        self.btn_test_event = QPushButton("📤 Enviar evento de prueba a Telegram")
        self.btn_test_event.setMinimumHeight(40)
        self.btn_test_event.setStyleSheet(f"""
            QPushButton {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
                border: none;
                border-radius: 6px;
                font-weight: bold;
                padding: 8px 16px;
            }}
            QPushButton:hover {{ background-color: #0ea5e9; }}
            QPushButton:disabled {{ background-color: #475569; color: #94a3b8; }}
        """)
        self.btn_test_event.clicked.connect(self._send_test_event)
        test_layout.addWidget(self.btn_test_event)

        self.lbl_test_status = QLabel("")
        self.lbl_test_status.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")
        self.lbl_test_status.setWordWrap(True)
        test_layout.addWidget(self.lbl_test_status)

        layout.addWidget(test_group)
        layout.addStretch()

        self.tabs.addTab(widget, "Notificaciones")
    
    def _setup_storage_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(16)
        
        # Configuración de Almacenamiento
        storage_group = QGroupBox("Gestión de Almacenamiento")
        storage_layout = QFormLayout(storage_group)
        
        self.txt_storage_path = QLineEdit()
        self.txt_storage_path.setReadOnly(True)
        
        btn_browse = QPushButton("Cambiar...")
        btn_browse.clicked.connect(self._browse_storage_path)
        
        path_layout = QHBoxLayout()
        path_layout.addWidget(self.txt_storage_path)
        path_layout.addWidget(btn_browse)
        storage_layout.addRow("Ruta de Grabaciones:", path_layout)
        
        self.spin_max_storage = QSpinBox()
        self.spin_max_storage.setRange(10, 1000)
        self.spin_max_storage.setValue(100)
        self.spin_max_storage.setSuffix(" GB")
        storage_layout.addRow("Límite Máximo:", self.spin_max_storage)
        
        self.spin_cleanup = QSpinBox()
        self.spin_cleanup.setRange(50, 95)
        self.spin_cleanup.setValue(90)
        self.spin_cleanup.setSuffix("%")
        storage_layout.addRow("Iniciar limpieza al:", self.spin_cleanup)
        
        # Info de espacio
        self.lbl_storage_info = QLabel("Espacio usado: Calculando...")
        self.lbl_storage_info.setStyleSheet(f"color: {config.THEME_TEXT_MUTED};")
        storage_layout.addRow(self.lbl_storage_info)
        
        self.btn_cleanup_now = QPushButton("🧹 Limpiar Ahora")
        self.btn_cleanup_now.clicked.connect(self._cleanup_storage)
        storage_layout.addRow(self.btn_cleanup_now)
        
        layout.addWidget(storage_group)
        layout.addStretch()
        
        self.tabs.addTab(widget, "Almacenamiento")
    
    def _load_settings(self):
        """Carga configuración desde el backend (system_config + storage info)."""
        def on_config(response):
            if not response.success:
                logger.warning(f"No se pudo cargar config: {response.error}")
                return
            data = response.data or {}
            # System config en BD es key→value. Lo aceptamos tanto si viene
            # como dict aplanado como si viene como lista de SystemConfig rows.
            if isinstance(data, list):
                data = {item.get("key"): item.get("value") for item in data}

            # Storage
            self.txt_storage_path.setText(
                str(data.get("recordings_path") or "./recordings")
            )
            try:
                self.spin_max_storage.setValue(int(float(data.get("max_storage_gb", 100))))
            except (TypeError, ValueError):
                self.spin_max_storage.setValue(100)

            # Telegram
            token = str(data.get("telegram_bot_token") or "")
            self.txt_bot_token.setText(token)
            # chat_ids puede ser lista separada por coma
            chat_ids = data.get("telegram_chat_ids") or data.get("telegram_chat_id") or ""
            self.txt_chat_id.setText(str(chat_ids))
            enabled = str(data.get("telegram_enabled", "false")).lower() == "true"

            # Banner de estado
            if token and chat_ids and enabled:
                self.lbl_telegram_status.setText(
                    f"✓ Telegram configurado y activo. "
                    f"Chat(s): {chat_ids}. "
                    f"Token: {'•' * 10}{token[-4:] if len(token) >= 4 else ''}"
                )
                self.lbl_telegram_status.setStyleSheet(
                    f"background:#0a3622; color:#22c55e; padding:8px; "
                    f"border-radius:4px; font-size:11px; font-weight:bold;"
                )
            elif token and chat_ids:
                self.lbl_telegram_status.setText(
                    "⚠ Telegram configurado pero DESACTIVADO. "
                    "Guarda los cambios para activarlo."
                )
                self.lbl_telegram_status.setStyleSheet(
                    f"background:#3a2a0a; color:#fbbf24; padding:8px; "
                    f"border-radius:4px; font-size:11px;"
                )
            else:
                self.lbl_telegram_status.setText(
                    "✗ Telegram NO configurado. "
                    "Añade el bot token y chat ID, luego guarda los cambios."
                )
                self.lbl_telegram_status.setStyleSheet(
                    f"background:#3a0a0a; color:#ef4444; padding:8px; "
                    f"border-radius:4px; font-size:11px;"
                )

            # Notification toggles
            self.chk_notify_person.setChecked(
                str(data.get("notify_person", "true")).lower() == "true"
            )
            self.chk_notify_vehicle.setChecked(
                str(data.get("notify_vehicle", "true")).lower() == "true"
            )
            self.chk_notify_motion.setChecked(
                str(data.get("notify_motion", "false")).lower() == "true"
            )

            # AI
            try:
                conf = int(float(data.get("ai_confidence", 45)))
                self.spin_confidence.setValue(conf)
            except (TypeError, ValueError):
                pass
            try:
                pb = int(float(data.get("pre_buffer_seconds", 10)))
                self.spin_pre_buffer.setValue(pb)
            except (TypeError, ValueError):
                pass
            self.chk_save_snapshots.setChecked(
                str(data.get("save_snapshots", "true")).lower() == "true"
            )

            self._load_storage_info()

        api_client.get("system/config", on_config)
        # Cargar cámaras para el selector del test event
        self._reload_test_cameras()
    
    def _load_storage_info(self):
        def on_storage(response):
            if response.success:
                data = response.data
                used = data.get("recordings_used_gb", 0)
                total = data.get("disk_total_gb", 0)
                percent = data.get("percent_used", 0)
                self.lbl_storage_info.setText(
                    f"Espacio usado: {used} GB / {total} GB ({percent}%)"
                )
        
        api_client.get("storage/info", on_storage)
    
    def _save_settings(self):
        """
        Guarda configuración en backend.
        TODO se guarda en SystemConfig (clave→valor) vía PUT /system/config.
        El path de almacenamiento usa POST /storage/config (path real en disco).
        """
        # 1) Storage path → endpoint dedicado (modifica path real en disco)
        new_path = self.txt_storage_path.text().strip()
        if new_path:
            def on_storage_resp(response):
                if not response.success:
                    logger.warning(f"Storage path no actualizado: {response.error}")
            api_client.post("storage/config", on_storage_resp, data={"path": new_path})

        # 2) Resto de settings → SystemConfig (clave→valor, strings)
        settings = {
            # Storage limits
            "max_storage_gb": str(self.spin_max_storage.value()),
            "cleanup_threshold": str(self.spin_cleanup.value()),
            # Telegram
            "telegram_bot_token": self.txt_bot_token.text(),
            "telegram_chat_id": self.txt_chat_id.text(),
            "telegram_chat_ids": self.txt_chat_id.text(),
            # Reactivar Telegram si tiene token+chat
            "telegram_enabled": ("true" if (
                self.txt_bot_token.text().strip() and self.txt_chat_id.text().strip()
            ) else "false"),
            # AI
            "ai_confidence": str(self.spin_confidence.value()),
            "ai_model": str(self.cmb_ai_model.currentIndex()),
            "pre_buffer_seconds": str(self.spin_pre_buffer.value()),
            "save_snapshots": "true" if self.chk_save_snapshots.isChecked() else "false",
            # Notification toggles (formato esperado por TelegramNotifier)
            "notify_person": "true" if self.chk_notify_person.isChecked() else "false",
            "notify_vehicle": "true" if self.chk_notify_vehicle.isChecked() else "false",
            "notify_motion": "true" if self.chk_notify_motion.isChecked() else "false",
        }

        def on_save(response):
            if response.success:
                QMessageBox.information(
                    self, "Éxito",
                    "Configuración guardada correctamente.\n\n"
                    "Si cambiaste el path de almacenamiento, "
                    "reinicia el backend para aplicar."
                )
                self._load_storage_info()
            else:
                QMessageBox.critical(
                    self, "Error",
                    f"No se pudo guardar: {response.error}"
                )

        api_client.put("system/config", on_save, data=settings)
    
    def _test_telegram(self):
        """Prueba la conexión con Telegram."""
        # Aquí iría una llamada al backend para probar el bot
        QMessageBox.information(self, "Prueba", "Mensaje de prueba enviado. Revise su Telegram.")
    
    def _browse_storage_path(self):
        """Abre diálogo para seleccionar carpeta."""
        path = QFileDialog.getExistingDirectory(self, "Seleccionar Carpeta de Grabaciones")
        if path:
            self.txt_storage_path.setText(path)
    
    def _reload_test_cameras(self):
        """Refresca la lista de cámaras del selector del test event."""
        def on_cams(response):
            if not response.success:
                return
            self.cmb_test_camera.clear()
            for c in (response.data or []):
                ws = c.get("worker_status") or {}
                status = ws.get("status") if isinstance(ws, dict) else None
                marker = "🟢 " if status == "running" else "⚫ "
                label = f"{marker}{c.get('name', '')}  (id={c.get('id')})"
                self.cmb_test_camera.addItem(label, c.get("id"))
        api_client.get("cameras/", on_cams)

    def _send_test_event(self):
        """Lanza evento de prueba en la cámara seleccionada."""
        cam_id = self.cmb_test_camera.currentData()
        if not cam_id:
            QMessageBox.warning(
                self, "Sin cámara",
                "Selecciona una cámara activa para enviar el evento de prueba."
            )
            return

        ans = QMessageBox.question(
            self, "Confirmar envío",
            f"Se va a:\n\n"
            f"1. Capturar un snapshot inmediato de la cámara #{cam_id}\n"
            f"2. Grabar 10 segundos de video adicional\n"
            f"3. Enviar foto + video al chat de Telegram\n\n"
            f"Tarda ~25 segundos. ¿Continuar?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return

        self.btn_test_event.setEnabled(False)
        self.btn_test_event.setText("⏳ Enviando... (espera ~25s)")
        self.lbl_test_status.setText(
            "🔄 Capturando snapshot y grabando video, "
            "después se enviará a Telegram…"
        )
        self.lbl_test_status.setStyleSheet(
            f"color: {config.THEME_ACCENT};"
        )

        def on_response(response):
            self.btn_test_event.setEnabled(True)
            self.btn_test_event.setText("📤 Enviar evento de prueba a Telegram")

            if response.success or response.status_code == 202:
                msg = response.data.get("message", "") if response.data else ""
                self.lbl_test_status.setText(
                    f"✓ Solicitud aceptada. {msg}\n"
                    f"Revisa tu Telegram en unos segundos."
                )
                self.lbl_test_status.setStyleSheet("color: #22c55e;")
            else:
                err = response.error or "Error desconocido"
                self.lbl_test_status.setText(f"✗ Error: {err}")
                self.lbl_test_status.setStyleSheet(
                    f"color: {config.THEME_DANGER};"
                )
                QMessageBox.critical(
                    self, "Test Telegram falló",
                    f"No se pudo enviar el evento de prueba:\n\n{err}\n\n"
                    f"Posibles causas:\n"
                    f"• La cámara no está activa (sin frames recientes)\n"
                    f"• Telegram no está configurado en SystemConfig\n"
                    f"• El bot token o chat_id son inválidos"
                )

        api_client.post(
            f"system/test-telegram/{cam_id}",
            on_response,
            data={"post_seconds": 10},
        )

    def _cleanup_storage(self):
        """Inicia limpieza manual."""
        reply = QMessageBox.question(
            self,
            "Confirmar Limpieza",
            "¿Desea eliminar las grabaciones antiguas para liberar espacio?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            def on_cleanup(response):
                if response.success:
                    deleted = response.data.get("deleted_files", 0)
                    QMessageBox.information(self, "Limpieza", f"Se eliminaron {deleted} archivos")
                    self._load_storage_info()
                else:
                    QMessageBox.critical(self, "Error", "No se pudo completar la limpieza")
            
            api_client.post("storage/cleanup", on_cleanup)