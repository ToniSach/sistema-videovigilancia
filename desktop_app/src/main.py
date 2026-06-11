"""
================================================================================
MÓDULO: desktop_app.main — Punto de entrada del cliente de escritorio (PySide6)
================================================================================

PROPÓSITO
    Arrancar la aplicación Qt: crear el QApplication, fijar fuente/estilo/tema
    global y mostrar la MainWindow. Es el equivalente desktop de backend/main.py,
    pero del lado del CLIENTE (proceso separado que habla con el backend por
    REST/JWT y consume el directo por RTSP/go2rtc con VLC).

ARQUITECTURA DEL CLIENTE DESKTOP
    Proceso PySide6 independiente. Capas:
      main.py ............ arranque Qt (este archivo)
      ui/main_window.py .. shell: navegación lateral + stack de vistas + login
      ui/views/ .......... pantallas (login, dashboard, live, playback, eventos,
                           gestión de cámaras, usuarios, permisos, ajustes…)
      ui/components/ ..... widgets reutilizables (rtsp_video VLC, ptz_joystick,
                           timeline, toast, glass_card, sparkline…)
      ui/dialogs/ ........ diálogos (onboarding, vinculación QR/Telegram, info)
      ui/theme.py · icons.py · help_texts.py .. estilo QSS, iconos y textos ayuda
      services/api_client.py .. CLIENTE HTTP singleton (JWT + refresh + async)
      services/playback_service.py .. reproducción de grabaciones
      models/ ............ DTOs locales (User, Camera, Recording) — NO son las
                           clases SQLAlchemy del backend; espejan los JSON de la API
      config.py .......... API_BASE_URL, timeouts, constantes del cliente

FLUJO DE DATOS (cómo se comunica con el backend)
    Vista → api_client.get/post/... (async en QThreadPool) → backend REST
          ← APIResponse marshalleada al hilo main (Qt.QueuedConnection) → callback
    Directo en vivo: rtsp_video.py reproduce con VLC el restream RTSP de go2rtc
    (NO pasa por api_client). Notificaciones push: WebSocket /ws/notifications.

PUNTO DE ENTRADA
    `python desktop_app/src/main.py` → main() → QApplication → MainWindow.show()
    → app.exec() (loop de eventos Qt).

DEPENDENCIAS CLAVE
    PySide6 (Qt6), python-vlc/libVLC (directo), requests (vía api_client).
    El singleton `api_client` se crea al importar services/api_client.py; su
    dispatcher Qt se inicializa de forma perezosa para no crear QObjects antes
    de que exista el QApplication.
================================================================================
"""
import sys
import logging
import os

# Configurar logging antes de cualquier import
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Subir solo 2 niveles desde desktop_app/src/ 
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(current_dir, '..', '..')
sys.path.insert(0, project_root)

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase

from desktop_app.src.ui.main_window import MainWindow
from desktop_app.src.ui.theme import apply_theme

def main():
    """Función principal."""
    # FIX Qt6: High DPI es manejado automáticamente en Qt6/PySide6
    # No usar AA_EnableHighDpiScaling ni AA_UseHighDpiPixmaps (deprecados en Qt6)
    # En Qt6, el scaling de alta DPI es el comportamiento por defecto
    
    # Crear aplicación
    app = QApplication(sys.argv)
    app.setApplicationName("Sistema de videovigilancia")
    app.setOrganizationName("NVRSystems")
    app.setStyle('Fusion')

    # Red de seguridad: que un error imprevisto no cierre la app de golpe.
    try:
        from desktop_app.src.ui.error_guard import install_error_guard
        install_error_guard(app)
    except Exception:
        logging.getLogger(__name__).debug("No se pudo instalar error_guard", exc_info=True)
    
    # Cargar fuentes si están disponibles
    font_paths = [
        ":/fonts/Inter-Regular.ttf",
        "/usr/share/fonts/truetype/inter/Inter-Regular.ttf",
        "C:\\Windows\\Fonts\\Inter.ttf"
    ]
    
    for fp in font_paths:
        if os.path.exists(fp):
            QFontDatabase.addApplicationFont(fp)
            break
    
    # Fuente por defecto
    font = QFont("Inter", 10)
    if not QFontDatabase.hasFamily("Inter"):
        font = QFont("Segoe UI", 10)
        if sys.platform == "darwin":
            font = QFont(".AppleSystemUIFont", 10)
        elif sys.platform == "linux":
            font = QFont("Ubuntu", 10)
    
    app.setFont(font)

    # Tema global (QSS): da aspecto "pro" coherente a TODOS los widgets
    # (scrollbars, desplegables, tablas, menús, inputs, diálogos…). Aditivo:
    # las hojas inline de cada vista siguen teniendo prioridad.
    apply_theme(app)

    # Crear y mostrar ventana principal
    window = MainWindow()
    window.show()
    
    # Ejecutar loop
    sys.exit(app.exec())

if __name__ == "__main__":
    main()