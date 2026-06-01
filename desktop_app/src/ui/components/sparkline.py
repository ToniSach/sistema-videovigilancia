"""
Sparkline — mini-gráfica de línea para series temporales (CPU, RAM, etc.).

Ligera: se dibuja con QPainter, sin dependencias (matplotlib/pyqtgraph). Mantiene
una ventana deslizante de los últimos N valores (0-100) y los pinta como una línea
con relleno suave. Pensada para incrustar dentro de una tarjeta de estadísticas.
"""
from collections import deque

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QLinearGradient, QPainterPath, QBrush
from PySide6.QtWidgets import QWidget

from desktop_app.src.config import config


class Sparkline(QWidget):
    """Gráfica de línea de los últimos `capacity` valores (rango 0-100)."""

    def __init__(self, capacity: int = 60, color: str = None, parent=None):
        super().__init__(parent)
        self._data = deque(maxlen=capacity)
        self._color = QColor(color or config.THEME_ACCENT)
        self.setMinimumHeight(48)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def add_value(self, value: float):
        """Añade un valor (se clampa a 0-100) y repinta."""
        try:
            v = max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            v = 0.0
        self._data.append(v)
        self.update()

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def clear(self):
        self._data.clear()
        self.update()

    def paintEvent(self, event):
        if len(self._data) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        w = self.width()
        h = self.height()
        pad = 3.0
        usable_h = h - 2 * pad

        n = len(self._data)
        step = w / (n - 1) if n > 1 else w

        # Puntos (y invertida: 0% abajo, 100% arriba)
        pts = []
        for i, v in enumerate(self._data):
            x = i * step
            y = pad + usable_h * (1.0 - v / 100.0)
            pts.append(QPointF(x, y))

        # Relleno bajo la curva (degradado)
        fill = QPainterPath()
        fill.moveTo(QPointF(pts[0].x(), h))
        for pt in pts:
            fill.lineTo(pt)
        fill.lineTo(QPointF(pts[-1].x(), h))
        fill.closeSubpath()

        grad = QLinearGradient(0, 0, 0, h)
        c = QColor(self._color)
        c.setAlpha(90)
        grad.setColorAt(0.0, c)
        c2 = QColor(self._color)
        c2.setAlpha(0)
        grad.setColorAt(1.0, c2)
        p.fillPath(fill, QBrush(grad))

        # Línea
        line = QPainterPath()
        line.moveTo(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        pen = QPen(self._color, 2)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawPath(line)

        # Punto final
        p.setBrush(QBrush(self._color))
        p.setPen(Qt.NoPen)
        p.drawEllipse(pts[-1], 2.5, 2.5)
        p.end()
