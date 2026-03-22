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
        self._load_settings()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        
        # Título
        title = QLabel("⚙️ Configuración del Sistema")
        title.setStyleSheet(f"""
            color: {config.THEME_TEXT};
            font-size: 24px;
            font-weight: bold;
        """)
        layout.addWidget(title)
        
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
        
        self.txt_bot_token = QLineEdit()
        self.txt_bot_token.setEchoMode(QLineEdit.Password)
        telegram_layout.addRow("Bot Token:", self.txt_bot_token)
        
        self.txt_chat_id = QLineEdit()
        telegram_layout.addRow("Chat ID:", self.txt_chat_id)
        
        self.btn_test_telegram = QPushButton("Probar Conexión")
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
        """Carga configuración desde el backend."""
        def on_config(response):
            if response.success:
                data = response.data or {}
                # Aplicar valores
                self.txt_storage_path.setText(data.get("recordings_path", "./recordings"))
                self.spin_max_storage.setValue(data.get("max_storage_gb", 100))
                self.txt_bot_token.setText(data.get("telegram_bot_token", ""))
                self.txt_chat_id.setText(data.get("telegram_chat_id", ""))
                
                # Cargar info de espacio
                self._load_storage_info()
        
        api_client.get("system/config", on_config)
    
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
        """Guarda configuración."""
        settings = {
            "recordings_path": self.txt_storage_path.text(),
            "max_storage_gb": self.spin_max_storage.value(),
            "telegram_bot_token": self.txt_bot_token.text(),
            "telegram_chat_id": self.txt_chat_id.text(),
            "cleanup_threshold": self.spin_cleanup.value(),
            "ai_confidence": self.spin_confidence.value(),
            "ai_model": self.cmb_ai_model.currentIndex(),
            "pre_buffer_seconds": self.spin_pre_buffer.value(),
            "save_snapshots": self.chk_save_snapshots.isChecked(),
            "notify_person": self.chk_notify_person.isChecked(),
            "notify_vehicle": self.chk_notify_vehicle.isChecked(),
            "notify_motion": self.chk_notify_motion.isChecked()
        }
        
        def on_save(response):
            if response.success:
                QMessageBox.information(self, "Éxito", "Configuración guardada correctamente")
            else:
                QMessageBox.critical(self, "Error", f"No se pudo guardar: {response.error}")
        
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