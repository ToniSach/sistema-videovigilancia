# desktop_app/src/ui/views/settings_view.py
"""
Vista de configuración del sistema.
"""
import logging
from typing import Optional

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QLineEdit, QCheckBox, QSpinBox,
                               QFormLayout, QGroupBox, QTabWidget, QComboBox,
                               QMessageBox, QFileDialog, QFrame)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

from desktop_app.src.config import config
from desktop_app.src.services.api_client import api_client
from desktop_app.src.ui.components.glass_card import GlassCard
from desktop_app.src.ui.icons import icon

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
        title = QLabel("Configuración del Sistema")
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
        
        layout.addWidget(self.tabs, 1)

        # ── Barra de acción PEGAJOSA (siempre abajo, siempre visible) ──────
        bar = QFrame()
        bar.setStyleSheet(
            "QFrame { background-color: #111c30; border-top: 1px solid "
            "rgba(255,255,255,0.08); border-radius: 0; }"
        )
        btn_layout = QHBoxLayout(bar)
        btn_layout.setContentsMargins(12, 8, 12, 8)

        # Indicador de cambios sin guardar (oculto hasta que algo cambie).
        self.lbl_dirty = QLabel("")
        self.lbl_dirty.setStyleSheet("color: #f59e0b; font-size: 12px; font-weight: bold;")
        btn_layout.addWidget(self.lbl_dirty)
        btn_layout.addStretch()

        self.btn_reset = QPushButton("Descartar cambios")
        self.btn_reset.clicked.connect(self._discard_changes)
        btn_layout.addWidget(self.btn_reset)

        self.btn_save = QPushButton("Guardar Cambios")
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
            QPushButton:disabled {{ background-color: #334155; color: #64748b; }}
        """)
        self.btn_save.clicked.connect(self._save_settings)
        btn_layout.addWidget(self.btn_save)

        layout.addWidget(bar)

        # Estado de "cambios sin guardar".
        self._dirty = False
        self.btn_save.setEnabled(False)
        # Conectar señales de cambio de TODOS los controles editables tras cargar.
        QTimer.singleShot(600, self._wire_dirty_tracking)
    
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
        
        # Perfiles comerciales (sin exponer "YOLO"). El índice se mapea al
        # modelo real en el backend (0=nano, 1=small, 2=medium).
        self.cmb_ai_model = QComboBox()
        self.cmb_ai_model.addItems([
            "Bajo consumo (rápido)",
            "Equilibrado",
            "Alta precisión",
        ])
        ai_layout.addRow("Detección de objetos:", self.cmb_ai_model)
        
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
        
        # Telegram — SIN credenciales manuales (token/chat_id). La vinculación
        # se hace desde "Notificaciones" con el flujo por código (el usuario
        # escribe al bot y queda vinculado solo). Aquí solo el estado + atajo.
        telegram_group = QGroupBox("Telegram")
        telegram_layout = QVBoxLayout(telegram_group)

        self.lbl_telegram_status = QLabel("Cargando configuración…")
        self.lbl_telegram_status.setStyleSheet(
            f"background:#1e293b; color:{config.THEME_TEXT_MUTED}; "
            f"padding:8px; border-radius:4px; font-size:11px;"
        )
        self.lbl_telegram_status.setWordWrap(True)
        telegram_layout.addWidget(self.lbl_telegram_status)

        help_telegram = QLabel(
            "<i>La vinculación de Telegram se gestiona en la pestaña "
            "<b>Notificaciones</b>: cada usuario vincula su propio chat con un "
            "código seguro (no hace falta copiar tokens ni IDs).</i>"
        )
        help_telegram.setWordWrap(True)
        help_telegram.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 11px;"
        )
        telegram_layout.addWidget(help_telegram)

        self.btn_goto_notifications = QPushButton("  Ir a Notificaciones")
        try:
            self.btn_goto_notifications.setIcon(icon("telegram"))
        except Exception:
            pass
        self.btn_goto_notifications.clicked.connect(self._goto_notifications)
        telegram_layout.addWidget(self.btn_goto_notifications)

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
        self.btn_reload_cams = QPushButton("")
        self.btn_reload_cams.setMaximumWidth(40)
        self.btn_reload_cams.clicked.connect(self._reload_test_cameras)
        cam_row.addWidget(self.btn_reload_cams)
        test_layout.addLayout(cam_row)

        self.btn_test_event = QPushButton("Enviar evento de prueba a Telegram")
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
        
        self.btn_cleanup_now = QPushButton("Limpiar Ahora")
        self.btn_cleanup_now.clicked.connect(self._cleanup_storage)
        storage_layout.addRow(self.btn_cleanup_now)
        
        layout.addWidget(storage_group)
        layout.addStretch()
        
        self.tabs.addTab(widget, "Almacenamiento")
    
    def _load_settings(self):
        """Carga configuración desde el backend (system_config + storage info)."""
        def on_config(response):
            self._loading = True
            try:
                self._apply_config(response)
            finally:
                self._loading = False
                self._clear_dirty()

        api_client.get("system/config", on_config)
        self._reload_test_cameras()

    def _apply_config(self, response):
        if True:
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

            # Telegram: solo estado (el bot lo configura el servidor; la
            # vinculación es por código desde Notificaciones).
            self._update_telegram_status_banner()

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
    
    def _wire_dirty_tracking(self):
        """Conecta las señales de cambio de todos los controles editables."""
        for w in self.findChildren(QSpinBox):
            w.valueChanged.connect(self._mark_dirty)
        for w in self.findChildren(QCheckBox):
            w.toggled.connect(self._mark_dirty)
        for w in self.findChildren(QComboBox):
            w.currentIndexChanged.connect(self._mark_dirty)
        for w in self.findChildren(QLineEdit):
            w.textEdited.connect(self._mark_dirty)

    def _mark_dirty(self, *args):
        # Ignorar cambios provocados por la propia carga de settings.
        if getattr(self, "_loading", False):
            return
        self._dirty = True
        self.lbl_dirty.setText("● Cambios sin guardar")
        self.btn_save.setEnabled(True)

    def _clear_dirty(self):
        self._dirty = False
        self.lbl_dirty.setText("")
        self.btn_save.setEnabled(False)

    def _discard_changes(self):
        """Recarga la configuración desde el servidor (descarta cambios locales)."""
        self._load_settings()
        self._clear_dirty()

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
        # NOTA: las credenciales de Telegram (token/chat_id) ya NO se editan
        # aquí — la vinculación es por código desde Notificaciones.
        settings = {
            # Storage limits
            "max_storage_gb": str(self.spin_max_storage.value()),
            "cleanup_threshold": str(self.spin_cleanup.value()),
            # AI
            "ai_confidence": str(self.spin_confidence.value()),
            "ai_model": str(self.cmb_ai_model.currentIndex()),
            "pre_buffer_seconds": str(self.spin_pre_buffer.value()),
            "save_snapshots": "true" if self.chk_save_snapshots.isChecked() else "false",
        }

        def on_save(response):
            if response.success:
                self._clear_dirty()
                try:
                    from desktop_app.src.ui.components.toast import show_toast
                    show_toast(self, "Configuración guardada ✓", level="success")
                except Exception:
                    QMessageBox.information(self, "Éxito", "Configuración guardada.")
                self._load_storage_info()
            else:
                QMessageBox.critical(
                    self, "Error",
                    f"No se pudo guardar: {response.error}"
                )

        api_client.put("system/config", on_save, data=settings)
    
    def _update_telegram_status_banner(self):
        """Consulta el estado del bot (sin exponer token) y pinta el banner."""
        def on_info(response):
            ok = response.success and (response.data or {}).get("configured")
            uname = (response.data or {}).get("username") if response.success else None
            if ok and uname:
                self.lbl_telegram_status.setText(f"Bot conectado: @{uname}")
                color_bg, color_fg = "#0a3622", "#22c55e"
            elif ok:
                self.lbl_telegram_status.setText("Bot configurado en el servidor.")
                color_bg, color_fg = "#0a3622", "#22c55e"
            else:
                self.lbl_telegram_status.setText(
                    "Telegram aún no está configurado en el servidor. "
                    "Configúralo desde Notificaciones (solo administrador)."
                )
                color_bg, color_fg = "#3a2a0a", "#fbbf24"
            self.lbl_telegram_status.setStyleSheet(
                f"background:{color_bg}; color:{color_fg}; padding:8px; "
                f"border-radius:4px; font-size:11px;"
            )
        try:
            api_client.get("telegram/bot-info", on_info)
        except Exception:
            pass

    def _goto_notifications(self):
        """Pide a la ventana principal que cambie a la pestaña Notificaciones."""
        w = self.window()
        for attr in ("show_notifications", "open_notifications", "go_to_notifications"):
            fn = getattr(w, attr, None)
            if callable(fn):
                fn()
                return
        QMessageBox.information(
            self, "Notificaciones",
            "Abre la pestaña «Notificaciones» en el menú lateral para vincular Telegram."
        )
    
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
                marker = "" if status == "running" else ""
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
        self.btn_test_event.setText("Enviando... (espera ~25s)")
        self.lbl_test_status.setText(
            "Capturando snapshot y grabando video, "
            "después se enviará a Telegram…"
        )
        self.lbl_test_status.setStyleSheet(
            f"color: {config.THEME_ACCENT};"
        )

        def on_response(response):
            self.btn_test_event.setEnabled(True)
            self.btn_test_event.setText("Enviar evento de prueba a Telegram")

            if response.success or response.status_code == 202:
                msg = response.data.get("message", "") if response.data else ""
                self.lbl_test_status.setText(
                    f"Solicitud aceptada. {msg}\n"
                    f"Revisa tu Telegram en unos segundos."
                )
                self.lbl_test_status.setStyleSheet("color: #22c55e;")
            else:
                err = response.error or "Error desconocido"
                self.lbl_test_status.setText(f"Error: {err}")
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