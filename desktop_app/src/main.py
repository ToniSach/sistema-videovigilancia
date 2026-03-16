import sys
import os
import logging

# Agregar la carpeta src al path si es necesario (para imports absolutos dentro de src)
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QDialog

# Imports relativos (funcionan desde desktop_app/src/)
from ui.login_window import LoginWindow
from ui.cameras_view import CamerasView

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sistema NVR - Videovigilancia")
        self.setMinimumSize(1400, 900)
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.cameras_view = CamerasView()
        layout.addWidget(self.cameras_view)

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("NVR System")
    app.setStyle("Fusion")
    
    login = LoginWindow()
    result = login.exec()
    
    if result == QDialog.DialogCode.Accepted:
        logger.info("Login exitoso, iniciando ventana principal")
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    else:
        logger.info("Login cancelado o fallido")
        sys.exit(0)

if __name__ == "__main__":
    main()