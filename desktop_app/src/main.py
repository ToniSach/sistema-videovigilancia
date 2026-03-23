"""
Punto de entrada de la aplicación desktop - FIX Qt6.
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

def main():
    """Función principal."""
    # FIX Qt6: High DPI es manejado automáticamente en Qt6/PySide6
    # No usar AA_EnableHighDpiScaling ni AA_UseHighDpiPixmaps (deprecados en Qt6)
    # En Qt6, el scaling de alta DPI es el comportamiento por defecto
    
    # Crear aplicación
    app = QApplication(sys.argv)
    app.setApplicationName("NVR VMS")
    app.setOrganizationName("NVRSystems")
    app.setStyle('Fusion')
    
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
    
    # Crear y mostrar ventana principal
    window = MainWindow()
    window.show()
    
    # Ejecutar loop
    sys.exit(app.exec())

if __name__ == "__main__":
    main()