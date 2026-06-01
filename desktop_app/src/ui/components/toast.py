"""
Toast — notificación breve no intrusiva (estilo Android/Material).

Aparece flotando en la esquina inferior de una ventana, se desvanece solo tras
unos segundos y no bloquea (a diferencia de QMessageBox). Útil para confirmar
acciones ("Guardado ✓", "Imagen capturada") sin interrumpir al usuario.

Uso:
    from desktop_app.src.ui.components.toast import show_toast
    show_toast(self, "Guardado correctamente")
    show_toast(self, "Error al conectar", level="error")
"""
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QPoint
from PySide6.QtWidgets import QLabel, QGraphicsOpacityEffect

_LEVEL_COLORS = {
    "info":    ("#1e293b", "#f1f5f9", "#38bdf8"),
    "success": ("#0a3622", "#dcfce7", "#22c55e"),
    "error":   ("#3a0a0a", "#fee2e2", "#ef4444"),
    "warning": ("#3a2a0a", "#fef9c3", "#f59e0b"),
}


def show_toast(parent, message: str, level: str = "info", msec: int = 2800):
    """Muestra un toast sobre la ventana de `parent`. No bloquea."""
    # Anclar a la ventana de nivel superior para que flote sobre todo.
    window = parent.window() if parent is not None else None
    if window is None:
        return None

    bg, fg, border = _LEVEL_COLORS.get(level, _LEVEL_COLORS["info"])

    toast = QLabel(message, window)
    toast.setWordWrap(True)
    toast.setAlignment(Qt.AlignCenter)
    toast.setStyleSheet(
        f"background-color: {bg}; color: {fg}; "
        f"border: 1px solid {border}; border-radius: 8px; "
        f"padding: 10px 16px; font-size: 12px;"
    )
    toast.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    toast.adjustSize()
    # Limitar ancho.
    max_w = min(420, window.width() - 40)
    if toast.width() > max_w:
        toast.setFixedWidth(max_w)
        toast.adjustSize()

    # Posición: centrado horizontal, cerca del borde inferior.
    x = (window.width() - toast.width()) // 2
    y = window.height() - toast.height() - 40
    toast.move(max(20, x), max(20, y))
    toast.show()
    toast.raise_()

    # Fade-in / fade-out con efecto de opacidad.
    effect = QGraphicsOpacityEffect(toast)
    toast.setGraphicsEffect(effect)
    fade_in = QPropertyAnimation(effect, b"opacity", toast)
    fade_in.setDuration(180)
    fade_in.setStartValue(0.0)
    fade_in.setEndValue(1.0)
    fade_in.start()
    toast._fade_in = fade_in  # evitar GC

    def _fade_out():
        anim = QPropertyAnimation(effect, b"opacity", toast)
        anim.setDuration(300)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.finished.connect(toast.deleteLater)
        anim.start()
        toast._fade_out = anim  # evitar GC

    QTimer.singleShot(msec, _fade_out)
    return toast
