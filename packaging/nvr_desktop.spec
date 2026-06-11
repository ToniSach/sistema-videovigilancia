# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec del FRONTEND / LAUNCHER (NVR-VMS.exe).

Empaqueta la GUI PySide6 + el launcher (que arranca el backend y espera a que
cargue) + el runtime de VLC para la reproducción. NO incluye torch/cv2/
ultralytics (eso vive en NVR-Backend.exe). Onedir, ventana sin consola.

Se ejecuta vía build.bat, que define REPO_DIR y VENDOR_DIR.
"""
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

REPO = Path(os.environ["REPO_DIR"])
VENDOR = Path(os.environ["VENDOR_DIR"])

datas = [
    (str(VENDOR / "vlc"), "vlc"),   # libvlc.dll + libvlccore.dll + plugins + lua
]

hiddenimports = []
hiddenimports += collect_submodules("desktop_app")
hiddenimports += ["vlc", "requests"]

# La GUI no necesita el stack de IA ni OpenCV → fuera (recorta cientos de MB).
excludes = ["torch", "torchvision", "ultralytics", "cv2", "openvino", "onnxruntime",
            "matplotlib", "scipy", "pandas", "tkinter"]

block_cipher = None

a = Analysis(
    [str(REPO / "desktop_app" / "src" / "launcher.py")],
    pathex=[str(REPO)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="NVR-VMS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # app de ventana (sin consola)
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name="NVR-VMS",
)
