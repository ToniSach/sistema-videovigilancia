# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec del BACKEND (NVR-Backend.exe).

Empaqueta el servidor Flask + IA (torch/ultralytics) + ONVIF (zeep/WSDL) +
PostgreSQL embebido + ffmpeg/ffprobe/go2rtc. NO incluye PySide6 (la GUI es otro
exe). Construido en modo onedir (carpeta), no onefile, por tamaño y arranque.

Se ejecuta vía build.bat, que define REPO_DIR y VENDOR_DIR.
"""
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

REPO = Path(os.environ["REPO_DIR"])
VENDOR = Path(os.environ["VENDOR_DIR"])
SITE = Path(os.environ.get("SITE_PACKAGES", ""))

# ---- binarios externos (van a la raíz del bundle, los expone runtime._augment_path) ----
binaries = [
    (str(VENDOR / "ffmpeg.exe"), "."),
    (str(VENDOR / "ffprobe.exe"), "."),
    (str(VENDOR / "go2rtc.exe"), "."),
]

# ---- datos a incluir ----
datas = [
    (str(VENDOR / "yolov8n.pt"), "."),          # modelo (runtime lo copia a %LOCALAPPDATA%)
    (str(VENDOR / "pgsql"), "pgsql"),           # PostgreSQL portátil (bin/lib/share)
]

# WSDL de ONVIF: onvif_discovery._get_wsdl_dir() lo busca en <site>/wsdl → bundle/wsdl
_wsdl = SITE / "wsdl"
if _wsdl.is_dir():
    datas.append((str(_wsdl), "wsdl"))

# ultralytics necesita sus .yaml de configuración (modelos, datasets, trackers).
datas += collect_data_files("ultralytics")
# zeep/onvif: plantillas y XSD declarados como package-data.
datas += collect_data_files("zeep")
datas += collect_data_files("onvif")

# ---- imports ocultos (Flask registra blueprints por import dinámico) ----
hiddenimports = []
hiddenimports += collect_submodules("backend")
hiddenimports += [
    "psycopg2", "sqlalchemy.dialects.postgresql",
    "flask_jwt_extended", "flask_cors", "flask_limiter", "flask_sock",
    "simple_websocket", "wsproto",
    "onvif", "zeep", "wsdiscovery", "ifaddr",
    "cv2", "PIL", "numpy",
]
# torch + ultralytics: submódulos cargados perezosamente.
hiddenimports += collect_submodules("ultralytics")

# DLLs nativas de torch/torchvision/cv2 (los hooks cubren la mayoría; reforzamos).
binaries += collect_dynamic_libs("torch")
binaries += collect_dynamic_libs("torchvision")

# ---- módulos a excluir (no los usa el backend; recortan tamaño) ----
excludes = ["PySide6", "shiboken6", "PyQt5", "tkinter", "vlc"]

block_cipher = None

a = Analysis(
    [str(REPO / "backend" / "app" / "main.py")],
    pathex=[str(REPO)],
    binaries=binaries,
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
    name="NVR-Backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,           # ventana de consola = log del backend (el launcher la oculta)
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name="NVR-Backend",
)
