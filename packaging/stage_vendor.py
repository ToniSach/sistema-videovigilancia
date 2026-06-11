"""
Reúne en una carpeta `vendor/` todos los binarios externos que el .exe necesita
incluir: ffmpeg/ffprobe, go2rtc, PostgreSQL portátil, el modelo YOLO y el runtime
de VLC. PyInstaller luego los empaqueta dentro del bundle (ver los .spec).

Uso:
    python packaging/stage_vendor.py
Variables de entorno opcionales para apuntar a otras rutas de origen:
    VENDOR_DIR, FFMPEG_EXE, FFPROBE_EXE, GO2RTC_EXE, PG_DIR, VLC_DIR, YOLO_PT
"""
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENDOR = Path(os.getenv("VENDOR_DIR", r"C:\NVR-VMS-build\vendor"))


def _first_existing(*candidates) -> Path | None:
    for c in candidates:
        if c:
            p = Path(c)
            if p.exists():
                return p
    return None


def _glob_first(root: str, pattern: str) -> Path | None:
    r = Path(root)
    if not r.exists():
        return None
    for m in r.glob(pattern):
        return m
    return None


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"  + {dst.relative_to(VENDOR)}  <-  {src}")


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        print(f"  = {dst.relative_to(VENDOR)} (ya existe, se omite)")
        return
    print(f"  + {dst.relative_to(VENDOR)}/  <-  {src} (copiando…)")
    shutil.copytree(src, dst)


def stage_ffmpeg() -> None:
    ffmpeg = _first_existing(
        os.getenv("FFMPEG_EXE"),
        _glob_first(r"C:\ProgramData\chocolatey\lib", "ffmpeg*/tools/**/ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        shutil.which("ffmpeg"),
    )
    ffprobe = _first_existing(
        os.getenv("FFPROBE_EXE"),
        (ffmpeg.parent / "ffprobe.exe") if ffmpeg else None,
        _glob_first(r"C:\ProgramData\chocolatey\lib", "ffmpeg*/tools/**/ffprobe.exe"),
        shutil.which("ffprobe"),
    )
    if not ffmpeg or not ffprobe:
        sys.exit("ERROR: no encuentro ffmpeg/ffprobe. Define FFMPEG_EXE/FFPROBE_EXE.")
    copy_file(ffmpeg, VENDOR / "ffmpeg.exe")
    copy_file(ffprobe, VENDOR / "ffprobe.exe")


def stage_go2rtc() -> None:
    go2rtc = _first_existing(os.getenv("GO2RTC_EXE"), REPO / "go2rtc.exe", shutil.which("go2rtc"))
    if not go2rtc:
        sys.exit("ERROR: no encuentro go2rtc.exe. Define GO2RTC_EXE.")
    copy_file(go2rtc, VENDOR / "go2rtc.exe")


def stage_model() -> None:
    pt = _first_existing(os.getenv("YOLO_PT"), REPO / "yolov8n.pt")
    if not pt:
        sys.exit("ERROR: no encuentro yolov8n.pt. Define YOLO_PT.")
    copy_file(pt, VENDOR / "yolov8n.pt")


def stage_postgres() -> None:
    pg = _first_existing(
        os.getenv("PG_DIR"),
        r"C:\Program Files\PostgreSQL\17",
        r"C:\Program Files\PostgreSQL\16",
        r"C:\Program Files\PostgreSQL\15",
    )
    if not pg:
        sys.exit("ERROR: no encuentro PostgreSQL. Instálalo o define PG_DIR.")
    if not (pg / "bin" / "initdb.exe").exists():
        sys.exit(f"ERROR: {pg}\\bin\\initdb.exe no existe.")
    for sub in ("bin", "lib", "share"):
        copy_tree(pg / sub, VENDOR / "pgsql" / sub)


def stage_vlc() -> None:
    vlc = _first_existing(
        os.getenv("VLC_DIR"),
        r"C:\Program Files\VideoLAN\VLC",
        r"C:\Program Files (x86)\VideoLAN\VLC",
    )
    if not vlc:
        sys.exit("ERROR: no encuentro VLC. Instálalo o define VLC_DIR.")
    dst = VENDOR / "vlc"
    for name in ("libvlc.dll", "libvlccore.dll"):
        copy_file(vlc / name, dst / name)
    for folder in ("plugins", "lua"):
        if (vlc / folder).is_dir():
            copy_tree(vlc / folder, dst / folder)


def main() -> None:
    print(f"Staging binarios en: {VENDOR}")
    VENDOR.mkdir(parents=True, exist_ok=True)
    stage_ffmpeg()
    stage_go2rtc()
    stage_model()
    stage_postgres()
    stage_vlc()
    print("OK: vendor listo.")


if __name__ == "__main__":
    main()
