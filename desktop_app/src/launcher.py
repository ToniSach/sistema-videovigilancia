"""
Launcher unificado del producto empaquetado (NVR-VMS.exe).

Flujo al ejecutar el .exe:
  1. Arranca el backend (NVR-Backend.exe incluido en la misma carpeta).
  2. Muestra una pantalla de carga (splash) mientras el backend inicializa
     PostgreSQL embebido, modelos de IA, cámaras y go2rtc.
  3. SOLO cuando `GET /api/v1/health` responde 200 (backend completamente
     cargado) abre la interfaz de escritorio.
  4. Al cerrar la ventana, detiene el backend (y con él PostgreSQL/go2rtc).

En desarrollo no se usa: se sigue ejecutando `desktop_app/src/main.py` y el
backend a mano. Para probar el flujo completo en desarrollo:
    set NVR_LAUNCH_BACKEND=1 && python -m desktop_app.src.launcher
"""
import os
import sys
import time
import logging
import subprocess
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("launcher")

# Asegura que el paquete del proyecto sea importable también en desarrollo.
_here = Path(__file__).resolve()
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(_here.parent.parent.parent))

import requests  # noqa: E402
from PySide6.QtWidgets import QApplication, QSplashScreen, QMessageBox  # noqa: E402
from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QPixmap, QPainter, QColor, QFont  # noqa: E402

from desktop_app.src.config import config  # noqa: E402

HEALTH_URL = config.API_BASE_URL.rstrip("/") + "/health"
BACKEND_BOOT_TIMEOUT = int(os.getenv("NVR_BACKEND_TIMEOUT", "180"))  # s
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_backend_proc: subprocess.Popen | None = None


# ------------------------------------------------------------------------- vlc
def _setup_vlc() -> None:
    """
    En el .exe empaquetado, python-vlc (ctypes) debe encontrar libvlc.dll y la
    carpeta de plugins incluidas en el bundle. Debe ejecutarse ANTES de importar
    el módulo `vlc` (lo hace la GUI al cargar el reproductor).
    """
    if not getattr(sys, "frozen", False):
        return
    vlc_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "vlc"
    if not vlc_dir.is_dir():
        return
    os.environ.setdefault("PYTHON_VLC_LIB_PATH", str(vlc_dir / "libvlc.dll"))
    os.environ.setdefault("VLC_PLUGIN_PATH", str(vlc_dir / "plugins"))
    os.environ["PATH"] = str(vlc_dir) + os.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(str(vlc_dir))
    except (OSError, AttributeError):
        pass


# --------------------------------------------------------------------- backend
def _backend_command() -> list[str] | None:
    """
    Resuelve cómo lanzar el backend.
    - Empaquetado: <carpeta del .exe>/backend/NVR-Backend.exe
    - Desarrollo (NVR_LAUNCH_BACKEND=1): python backend/app/main.py
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).parent / "backend" / "NVR-Backend.exe"
        return [str(exe)] if exe.exists() else None
    if os.getenv("NVR_LAUNCH_BACKEND") == "1":
        root = _here.parent.parent.parent
        return [sys.executable, str(root / "backend" / "app" / "main.py")]
    return None


def _start_backend() -> None:
    global _backend_proc
    cmd = _backend_command()
    if not cmd:
        logger.info("No se lanza backend (se asume ya en ejecución en %s)", HEALTH_URL)
        return
    logger.info("Arrancando backend: %s", cmd[0])
    creationflags = _NO_WINDOW
    if os.name == "nt":
        creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
    _backend_proc = subprocess.Popen(
        cmd, cwd=str(Path(cmd[0]).parent), creationflags=creationflags
    )


def _stop_backend() -> None:
    global _backend_proc
    if not _backend_proc:
        return
    logger.info("Deteniendo backend (pid=%s) y sus procesos hijos", _backend_proc.pid)
    try:
        if os.name == "nt":
            # /T derriba el árbol completo (PostgreSQL embebido + go2rtc + ffmpeg).
            subprocess.run(
                ["taskkill", "/PID", str(_backend_proc.pid), "/T", "/F"],
                capture_output=True, creationflags=_NO_WINDOW,
            )
        else:
            _backend_proc.terminate()
        _backend_proc.wait(timeout=20)
    except Exception as e:
        logger.error("Error deteniendo backend: %s", e)
    finally:
        _backend_proc = None


def _backend_alive() -> bool:
    return _backend_proc is None or (_backend_proc.poll() is None)


# ---------------------------------------------------------------------- splash
def _make_splash() -> QSplashScreen:
    pix = QPixmap(520, 300)
    pix.fill(QColor("#0f172a"))
    p = QPainter(pix)
    p.setPen(QColor("#38bdf8"))
    p.setFont(QFont("Segoe UI", 24, QFont.Bold))
    p.drawText(pix.rect().adjusted(0, -30, 0, -30),
               Qt.AlignCenter | Qt.TextWordWrap, "Sistema de\nVideovigilancia")
    p.setPen(QColor("#94a3b8"))
    p.setFont(QFont("Segoe UI", 11))
    p.drawText(pix.rect().adjusted(0, 60, 0, 60), Qt.AlignCenter,
               "Para red local (LAN)")
    p.end()
    splash = QSplashScreen(pix, Qt.WindowStaysOnTopHint)
    return splash


def _msg(splash: QSplashScreen, text: str) -> None:
    splash.showMessage(
        text, Qt.AlignBottom | Qt.AlignHCenter, QColor("#e2e8f0")
    )
    QApplication.processEvents()


# ------------------------------------------------------------------------ main
def main() -> int:
    _setup_vlc()
    app = QApplication(sys.argv)
    app.setApplicationName("Sistema de videovigilancia")
    app.setOrganizationName("NVRSystems")
    app.setStyle("Fusion")
    app.aboutToQuit.connect(_stop_backend)

    # Red de seguridad: que un error imprevisto no cierre la app de golpe.
    try:
        from desktop_app.src.ui.error_guard import install_error_guard
        install_error_guard(app)
    except Exception:
        pass

    splash = _make_splash()
    splash.show()
    _msg(splash, "Iniciando servidor…")

    _start_backend()

    # Esperar a que el backend cargue del todo (health 200).
    deadline = time.time() + BACKEND_BOOT_TIMEOUT
    ready = False
    dots = 0
    while time.time() < deadline:
        if not _backend_alive():
            QMessageBox.critical(
                None, "Sistema de videovigilancia",
                "El servidor se cerró inesperadamente durante el arranque.\n"
                "Revisa el log en %LOCALAPPDATA%\\NVR-VMS\\logs.",
            )
            return 1
        try:
            r = requests.get(HEALTH_URL, timeout=2)
            if r.status_code == 200:
                ready = True
                break
        except requests.RequestException:
            pass
        dots = (dots % 3) + 1
        _msg(splash, "Iniciando servidor" + "." * dots)
        time.sleep(1.0)

    if not ready:
        QMessageBox.critical(
            None, "Sistema de videovigilancia",
            "El servidor no respondió a tiempo.\n"
            "Cierra la aplicación e inténtalo de nuevo.",
        )
        _stop_backend()
        return 1

    _msg(splash, "Cargando interfaz…")

    # Importar la GUI solo ahora (acelera el arranque del splash).
    from desktop_app.src.ui.main_window import MainWindow
    from desktop_app.src.ui.theme import apply_theme
    from PySide6.QtGui import QFont as _QFont, QFontDatabase

    f = _QFont("Inter", 10)
    if not QFontDatabase.hasFamily("Inter"):
        f = _QFont("Segoe UI", 10)
    app.setFont(f)
    apply_theme(app)

    window = MainWindow()
    window.show()
    splash.finish(window)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
