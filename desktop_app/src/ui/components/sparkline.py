"""
================================================================================
MÓDULO: ui.components.sparkline — Mini-gráfica de línea para métricas
================================================================================

PROPÓSITO
    Sparkline: mini-gráfica de línea para series temporales (CPU, RAM, FPS,
    latencia, etc.). Ligera, dibujada con QPainter, sin dependencias externas
    (no usa matplotlib/pyqtgraph).

RESPONSABILIDAD
    Mantener una ventana deslizante (deque) de los últimos `capacity` valores en
    rango 0-100 y pintarlos como una línea con relleno degradado y un punto en
    el valor más reciente. Pensada para incrustar en una tarjeta de estadísticas.

DEPENDENCIAS
    PySide6 (QWidget, QPainter y primitivas de pintado), config (color de
    acento por defecto). collections.deque para la ventana deslizante.

COMPONENTES RELACIONADOS
    glass_card.py (suele alojar la sparkline), dashboards/vistas de métricas que
    alimentan add_value() con datos del backend (p.ej. /system/metrics).

DÓNDE SE USA
    En tarjetas de estadísticas del dashboard y paneles de estado del sistema.
================================================================================
"""
from collections import deque

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QLinearGradient, QPainterPath, QBrush
from PySide6.QtWidgets import QWidget

from desktop_app.src.config import config


class Sparkline(QWidget):
    """Gráfica de línea de los últimos `capacity` valores (rango 0-100).

    Rol: indicador visual ligero de tendencia de una métrica.
    Quién la instancia/consume: tarjetas de estadísticas/dashboards, que llaman
        add_value() periódicamente con datos del backend.
    Señales Qt: ninguna (es solo presentación; ignora el ratón).
    Dependencias: config (color por defecto), QPainter.

    Parámetros:
        capacity: nº de muestras de la ventana deslizante (ancho del historial).
        color: color de la línea (hex); si None usa config.THEME_ACCENT.
    """

    def __init__(self, capacity: int = 60, color: str = None, parent=None):
        super().__init__(parent)
        self._data = deque(maxlen=capacity)
        self._color = QColor(color or config.THEME_ACCENT)
        self.setMinimumHeight(48)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def add_value(self, value: float):
        """Añade una muestra a la ventana deslizante y repinta.

        Inputs: value (numérico; se clampa a 0-100; valores no convertibles → 0).
        Outputs: ninguno (al superar `capacity`, el deque descarta la más antigua).
        Llamado por: el código que alimenta la métrica (timer/callback de datos).
        """
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
