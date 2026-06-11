"""
Soporte de ejecución EMPAQUETADA (.exe de PyInstaller) para el backend.

En DESARROLLO (proceso no congelado) este módulo no cambia nada: todas las
funciones detectan `sys.frozen` y, si no está, respetan el flujo habitual con
el `.env` del repositorio. Así el arranque en desarrollo queda intacto.

Cuando la app corre como .EXE:
  - Los datos escribibles (grabaciones, base de datos PostgreSQL embebida,
    configuración, logs) viven en %LOCALAPPDATA%\\NVR-VMS, NO dentro del bundle
    de solo lectura.
  - Los binarios incluidos (ffmpeg, ffprobe, go2rtc, postgres) se exponen
    añadiéndolos al PATH del proceso, de modo que el código existente que hace
    `shutil.which("ffmpeg")` los encuentra sin tener que tocar cada call-site.
  - Los secretos (SECRET_KEY / JWT_SECRET_KEY / MEDIA_URL_SECRET) se generan
    UNA vez por instalación y se persisten en %LOCALAPPDATA%\\NVR-VMS\\secrets.env,
    nunca se hardcodean en el ejecutable.
"""
import os
import sys
import shutil
import secrets as _secrets
from pathlib import Path

APP_NAME = "NVR-VMS"


def is_frozen() -> bool:
    """True si corremos dentro de un ejecutable de PyInstaller."""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """
    Carpeta raíz de recursos.
    - Congelado (onedir): sys._MEIPASS (donde PyInstaller deja datas/binaries).
    - Desarrollo: la raíz del repositorio.
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    return Path(__file__).resolve().parent.parent.parent


def app_data_dir() -> Path:
    """
    Directorio ESCRIBIBLE para datos del usuario. Se crea si no existe.
    - Congelado: %LOCALAPPDATA%\\NVR-VMS  (p. ej. C:\\Users\\x\\AppData\\Local\\NVR-VMS)
    - Desarrollo: la raíz del repo (o NVR_DATA_DIR si se define).
    """
    if is_frozen():
        base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
        d = Path(base) / APP_NAME
    else:
        d = Path(os.getenv("NVR_DATA_DIR", str(bundle_dir())))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _augment_path() -> None:
    """
    Expone los binarios incluidos a `shutil.which(...)` anteponiéndolos al PATH
    del proceso. Evita tener que modificar cada lugar que lanza ffmpeg/go2rtc.
    """
    bd = bundle_dir()
    extra = []
    for sub in ("", "bin", "ffmpeg", os.path.join("pgsql", "bin")):
        p = (bd / sub) if sub else bd
        if p.is_dir():
            extra.append(str(p))
    if extra:
        os.environ["PATH"] = os.pathsep.join(extra) + os.pathsep + os.environ.get("PATH", "")


def _ensure_secrets() -> None:
    """
    Genera y persiste secretos únicos por instalación. Se cargan en el entorno
    con `setdefault` para que `config.Settings` los lea como si vinieran del .env.
    """
    sfile = app_data_dir() / "secrets.env"
    vals: dict[str, str] = {}
    if sfile.exists():
        for line in sfile.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()

    changed = False
    for key in ("SECRET_KEY", "JWT_SECRET_KEY", "MEDIA_URL_SECRET", "POSTGRES_PASSWORD"):
        if not vals.get(key):
            vals[key] = _secrets.token_urlsafe(48 if key != "POSTGRES_PASSWORD" else 24)
            changed = True

    if changed:
        sfile.write_text(
            "# Secretos generados automáticamente. NO compartir.\n"
            + "\n".join(f"{k}={v}" for k, v in vals.items()) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(sfile, 0o600)
        except OSError:
            pass

    for k, v in vals.items():
        os.environ.setdefault(k, v)


def _ensure_model() -> None:
    """
    El modelo YOLO viaja en el bundle (solo lectura). Lo copiamos a un directorio
    ESCRIBIBLE la primera vez para que ultralytics pueda cachear exports allí, y
    apuntamos AI_MODEL a esa copia.
    """
    src = bundle_dir() / "yolov8n.pt"
    dst_dir = app_data_dir() / "models"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "yolov8n.pt"
    if src.exists() and not dst.exists():
        try:
            shutil.copy2(src, dst)
        except OSError:
            pass
    if dst.exists():
        os.environ.setdefault("AI_MODEL", str(dst))


def _setup_file_logging() -> None:
    """En el .exe (sin consola visible) vuelca los logs a un fichero rotado."""
    import logging
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    if any(getattr(h, "_nvr_file", False) for h in root.handlers):
        return
    logs = app_data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(
        logs / "backend.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    h._nvr_file = True  # type: ignore[attr-defined]
    h.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    ))
    root.addHandler(h)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)


def _set_embedded_defaults() -> None:
    """Valores por defecto para el modo empaquetado (no pisan lo que ya exista)."""
    data = app_data_dir()
    os.environ.setdefault("RECORDINGS_PATH", str(data / "recordings"))
    os.environ.setdefault("GO2RTC_CONFIG_PATH", str(data / "go2rtc.generated.yaml"))
    # Telemetría = instrumentación de desarrollo: desactivada en producto final.
    os.environ.setdefault("TELEMETRY_ENABLED", "false")
    # Inferencia en PyTorch directo: arranque inmediato y fiable en el .exe
    # (evita exportar OpenVINO/ONNX en el primer arranque del usuario).
    os.environ.setdefault("AI_FORMAT", "pytorch")
    # Exigir secretos reales (ya los generamos arriba).
    os.environ.setdefault("APP_ENV", "production")

    # PostgreSQL embebido en un puerto propio para no chocar con un PostgreSQL
    # del sistema que pudiera existir en la máquina del usuario.
    os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
    os.environ.setdefault("POSTGRES_PORT", os.getenv("NVR_PG_PORT", "5433"))
    os.environ.setdefault("POSTGRES_DB", "nvr_db")
    os.environ.setdefault("POSTGRES_USER", "nvr_user")
    os.environ.setdefault("EMBEDDED_PG", "1")


_DONE = False


def bootstrap() -> None:
    """
    Punto de entrada idempotente. Se llama lo antes posible (desde config.py),
    antes de leer cualquier variable de entorno. En desarrollo es un no-op.
    """
    global _DONE
    if _DONE:
        return
    _DONE = True
    if not is_frozen():
        return
    _augment_path()
    _set_embedded_defaults()
    _ensure_secrets()
    _ensure_model()
    _setup_file_logging()
