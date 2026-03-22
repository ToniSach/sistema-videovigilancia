"""
Widget de timeline interactivo para grabaciones.
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Callable

from PySide6.QtWidgets import QWidget, QToolTip
from PySide6.QtCore import Qt, QRect, QPoint, Signal, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QMouseEvent, QWheelEvent, QPaintEvent

from desktop_app.src.config import config
from desktop_app.src.models.recording import RecordingSegment

logger = logging.getLogger(__name__)


class TimelineWidget(QWidget):
    """
    Timeline horizontal interactivo para visualizar segmentos de grabación.
    """
    
    # Señales
    segment_clicked = Signal(int, int)  # recording_id, seconds_offset
    position_changed = Signal(int)  # segundos desde medianoche
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.setMinimumHeight(80)
        self.setMaximumHeight(120)
        
        # Datos
        self.segments: List[RecordingSegment] = []
        self.current_time: int = 0  # segundos desde medianoche (0-86399)
        self.duration: int = 86400  # 24 horas en segundos
        
        # Visualización
        self.pixels_per_second: float = self.width() / self.duration
        self.segment_height_ratio: float = 0.6  # 60% de altura
        self.colors = {
            'background': QColor(config.THEME_PRIMARY),
            'segment_continuous': QColor(config.THEME_ACCENT).darker(120),
            'segment_event': QColor(config.THEME_ACCENT),
            'segment_clip': QColor(config.THEME_ACCENT_SECONDARY),
            'current_line': QColor(config.THEME_DANGER),
            'text': QColor(config.THEME_TEXT),
            'grid': QColor(config.THEME_TEXT_MUTED).darker(150)
        }
        
        # Interacción
        self._dragging: bool = False
        self._hover_segment: Optional[RecordingSegment] = None
        self._font = QFont("Inter", 8)
        
        # Timer para tooltip
        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.setSingleShot(True)
        self._tooltip_timer.timeout.connect(self._show_tooltip)
        
        self.setMouseTracking(True)
    
    def set_segments(self, segments: List[RecordingSegment]):
        """Establece segmentos a mostrar."""
        self.segments = sorted(segments, key=lambda x: x.start)
        self.update()
    
    def set_current_time(self, seconds: int):
        """Actualiza línea de tiempo actual."""
        self.current_time = max(0, min(seconds, self.duration))
        self.update()
        self.position_changed.emit(self.current_time)
    
    def paintEvent(self, event: QPaintEvent):
        """Renderiza el timeline."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Fondo
        painter.fillRect(self.rect(), self.colors['background'])
        
        # Calcular escala
        width = self.width()
        height = self.height()
        self.pixels_per_second = width / self.duration
        
        # Dibujar grid de horas
        self._draw_grid(painter, width, height)
        
        # Dibujar segmentos
        self._draw_segments(painter, height)
        
        # Dibujar línea de tiempo actual
        self._draw_current_line(painter, width, height)
        
        painter.end()
    
    def _draw_grid(self, painter: QPainter, width: int, height: int):
        """Dibuja líneas de hora."""
        pen = QPen(self.colors['grid'])
        pen.setWidth(1)
        painter.setPen(pen)
        
        # Línea cada hora
        for hour in range(25):
            x = int(hour * 3600 * self.pixels_per_second)
            if x <= width:
                painter.drawLine(x, 0, x, height)
                
                # Texto de hora
                painter.setPen(self.colors['text'])
                painter.setFont(self._font)
                painter.drawText(QRect(x + 2, 2, 40, 15), Qt.AlignLeft, f"{hour:02d}:00")
                painter.setPen(self.colors['grid'])
    
    def _draw_segments(self, painter: QPainter, height: int):
        """Dibuja los segmentos de grabación."""
        bar_height = int(height * self.segment_height_ratio)
        y_offset = (height - bar_height) // 2
        
        for segment in self.segments:
            # Calcular posición
            start_x = int(segment.start_seconds * self.pixels_per_second)
            end_x = int((segment.start_seconds + segment.duration_seconds) * self.pixels_per_second)
            width = max(end_x - start_x, 3)  # Mínimo 3px para visibilidad
            
            # Color según tipo
            if segment.has_clip:
                color = self.colors['segment_clip']
            else:
                color = self.colors['segment_event'] if segment.duration_seconds < 300 else self.colors['segment_continuous']
            
            # Rectángulo del segmento
            rect = QRect(start_x, y_offset, width, bar_height)
            
            # Hover effect
            if segment == self._hover_segment:
                color = color.lighter(120)
            
            painter.fillRect(rect, color)
            
            # Borde sutil
            pen = QPen(color.darker(110))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawRect(rect)
    
    def _draw_current_line(self, painter: QPainter, width: int, height: int):
        """Dibuja línea indicadora de posición actual."""
        x = int(self.current_time * self.pixels_per_second)
        
        pen = QPen(self.colors['current_line'])
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawLine(x, 0, x, height)
        
        # Punto en la parte superior
        painter.setBrush(self.colors['current_line'])
        painter.drawEllipse(QPoint(x, 5), 4, 4)
    
    def _time_to_seconds(self, time_str: str) -> int:
        """Convierte HH:MM a segundos."""
        try:
            h, m = map(int, time_str.split(':'))
            return h * 3600 + m * 60
        except:
            return 0
    
    def _seconds_to_time(self, seconds: int) -> str:
        """Convierte segundos a HH:MM:SS."""
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    
    def mousePressEvent(self, event: QMouseEvent):
        """Click en timeline."""
        if event.button() == Qt.LeftButton:
            seconds = int(event.pos().x() / self.pixels_per_second)
            seconds = max(0, min(seconds, self.duration))
            
            # Buscar segmento clickeado
            clicked_segment = None
            for seg in self.segments:
                if seg.start_seconds <= seconds <= seg.start_seconds + seg.duration_seconds:
                    clicked_segment = seg
                    break
            
            if clicked_segment:
                offset = seconds - clicked_segment.start_seconds
                self.segment_clicked.emit(clicked_segment.recording_id, offset)
            
            self.set_current_time(seconds)
            self._dragging = True
    
    def mouseMoveEvent(self, event: QMouseEvent):
        """Movimiento de mouse (hover y drag)."""
        x = event.pos().x()
        seconds = int(x / self.pixels_per_second)
        
        # Encontrar segmento bajo cursor
        old_hover = self._hover_segment
        self._hover_segment = None
        
        for seg in self.segments:
            if seg.start_seconds <= seconds <= seg.start_seconds + seg.duration_seconds:
                self._hover_segment = seg
                break
        
        if old_hover != self._hover_segment:
            self.update()
        
        # Tooltip
        if self._hover_segment:
            time_str = self._seconds_to_time(seconds)
            tooltip = f"{time_str} - {self._hover_segment.duration_seconds//60}min"
            QToolTip.showText(event.globalPosition().toPoint(), tooltip, self)
        
        # Drag
        if self._dragging:
            seconds = max(0, min(seconds, self.duration))
            self.set_current_time(seconds)
    
    def mouseReleaseEvent(self, event: QMouseEvent):
        """Fin de drag."""
        self._dragging = False
    
    def wheelEvent(self, event: QWheelEvent):
        """Zoom con rueda del mouse (cambia escala temporal)."""
        # Por ahora, scroll horizontal
        delta = event.angleDelta().y()
        if delta > 0:
            # Zoom in - cambiaría escala si implementamos zoom
            pass
        else:
            # Zoom out
            pass
    
    def _show_tooltip(self):
        """Muestra tooltip de segmento."""
        pass  # Implementado en mouseMove