"""
Helper central de ICONOS para el cliente de escritorio (reemplaza emojis).

Usa **qtawesome** (Material Design Icons) si está instalado:
    pip install qtawesome
Si no está, cae a iconos estándar de Qt (QStyle) y, en último caso, a un
QIcon vacío — así la app funciona igual aunque qtawesome no esté presente.

Uso:
    from desktop_app.src.ui.icons import icon
    btn.setIcon(icon("refresh"))
"""
from __future__ import annotations

import logging
from functools import lru_cache

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyle

logger = logging.getLogger(__name__)

try:
    import qtawesome as qta  # type: ignore
    _HAS_QTA = True
except Exception:
    qta = None
    _HAS_QTA = False
    logger.info("qtawesome no instalado; iconos via QStyle (pip install qtawesome para iconos completos)")

# nombre semántico -> (icono Material Design de qtawesome, fallback QStyle.SP_*)
_MAP = {
    "logo":          ("mdi.cctv", "SP_ComputerIcon"),
    "live":          ("mdi.cctv", "SP_ComputerIcon"),
    "events":        ("mdi.bell", "SP_MessageBoxInformation"),
    "playback":      ("mdi.play-circle", "SP_MediaPlay"),
    "cameras":       ("mdi.camera", "SP_ComputerIcon"),
    "notifications": ("mdi.bell-ring", "SP_MessageBoxInformation"),
    "system":        ("mdi.chart-box", "SP_FileDialogDetailedView"),
    "users":         ("mdi.account-group", "SP_DirHomeIcon"),
    "permissions":   ("mdi.lock", "SP_VistaShield"),
    "settings":      ("mdi.cog", "SP_FileDialogDetailedView"),
    "tutorial":      ("mdi.school", "SP_DialogHelpButton"),
    "link_mobile":   ("mdi.cellphone-link", "SP_DriveNetIcon"),
    "logout":        ("mdi.logout", "SP_DialogCloseButton"),
    "user":          ("mdi.account", "SP_DirHomeIcon"),
    "refresh":       ("mdi.refresh", "SP_BrowserReload"),
    "add":           ("mdi.plus", "SP_FileDialogNewFolder"),
    "edit":          ("mdi.pencil", "SP_FileDialogDetailedView"),
    "delete":        ("mdi.delete", "SP_TrashIcon"),
    "help":          ("mdi.help-circle", "SP_DialogHelpButton"),
    "back":          ("mdi.arrow-left", "SP_ArrowBack"),
    "next":          ("mdi.chevron-right", "SP_ArrowForward"),
    "prev":          ("mdi.chevron-left", "SP_ArrowBack"),
    "telegram":      ("mdi.send", "SP_DialogYesButton"),
    "link":          ("mdi.link-variant", "SP_DialogYesButton"),
    "unlink":        ("mdi.link-off", "SP_DialogCancelButton"),
    "copy":          ("mdi.content-copy", "SP_FileDialogDetailedView"),
    "ok":            ("mdi.check-circle", "SP_DialogApplyButton"),
    "warn":          ("mdi.alert", "SP_MessageBoxWarning"),
    "error":         ("mdi.close-circle", "SP_MessageBoxCritical"),
    "record":        ("mdi.record-rec", "SP_MediaVolume"),
    "mic":           ("mdi.microphone", "SP_MediaVolume"),
    "night":         ("mdi.weather-night", "SP_DialogYesButton"),
    "fullscreen":    ("mdi.fullscreen", "SP_TitleBarMaxButton"),
    "snapshot":      ("mdi.camera", "SP_ComputerIcon"),
    "person":        ("mdi.account", "SP_DirHomeIcon"),
    "vehicle":       ("mdi.car", "SP_ComputerIcon"),
    "motion":        ("mdi.motion-sensor", "SP_ComputerIcon"),
    "offline":       ("mdi.lan-disconnect", "SP_MessageBoxWarning"),
    "tamper":        ("mdi.shield-alert", "SP_MessageBoxCritical"),
    "push":          ("mdi.cellphone", "SP_DriveNetIcon"),
    "email":         ("mdi.email", "SP_DialogYesButton"),
    "clock":         ("mdi.clock-outline", "SP_DialogHelpButton"),
    "list":          ("mdi.format-list-bulleted", "SP_FileDialogDetailedView"),
    "robot":         ("mdi.robot", "SP_ComputerIcon"),
    "ptz":           ("mdi.cursor-move", "SP_ComputerIcon"),
    "ai":            ("mdi.brain", "SP_ComputerIcon"),
    "led":           ("mdi.lightbulb", "SP_ComputerIcon"),
    "audio":         ("mdi.volume-high", "SP_MediaVolume"),
    "play":          ("mdi.play", "SP_MediaPlay"),
    "pause":         ("mdi.pause", "SP_MediaPause"),
    "stop":          ("mdi.stop", "SP_MediaStop"),
    "export":        ("mdi.download", "SP_DialogSaveButton"),
    "ptz_ctrl":      ("mdi.gamepad-variant", "SP_ComputerIcon"),
    "view":          ("mdi.eye", "SP_FileDialogContentsView"),
    "download":      ("mdi.download", "SP_DialogSaveButton"),
    "grant":         ("mdi.account-plus", "SP_FileDialogNewFolder"),
    "search":        ("mdi.magnify", "SP_FileDialogContentsView"),
    "power":         ("mdi.power", "SP_BrowserReload"),
    "save":          ("mdi.content-save", "SP_DialogSaveButton"),
    "send":          ("mdi.send", "SP_DialogYesButton"),
    "cleanup":       ("mdi.broom", "SP_TrashIcon"),
    "speaker":       ("mdi.volume-high", "SP_MediaVolume"),
    "speaker_off":   ("mdi.volume-off", "SP_MediaVolume"),
    "preset":        ("mdi.map-marker", "SP_DirHomeIcon"),
    "zoom_in":       ("mdi.magnify-plus", "SP_FileDialogContentsView"),
    "zoom_out":      ("mdi.magnify-minus", "SP_FileDialogContentsView"),
}

_DEFAULT_COLOR = "#cbd5e1"  # gris claro, combina con el tema oscuro


@lru_cache(maxsize=512)
def icon(name: str, color: str = _DEFAULT_COLOR) -> QIcon:
    """Devuelve un QIcon para el nombre semántico (cacheado)."""
    spec = _MAP.get(name)
    if _HAS_QTA and spec and spec[0]:
        try:
            return qta.icon(spec[0], color=color)
        except Exception:
            pass
    app = QApplication.instance()
    if app is not None and spec and spec[1]:
        sp = getattr(QStyle.StandardPixmap, spec[1], None)
        if sp is not None:
            try:
                return app.style().standardIcon(sp)
            except Exception:
                pass
    return QIcon()


def has_real_icons() -> bool:
    """True si qtawesome está disponible (iconos completos)."""
    return _HAS_QTA
