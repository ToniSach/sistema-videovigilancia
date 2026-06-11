"""
================================================================================
MÓDULO: ui.components.timeline_widget — Línea de tiempo de grabaciones (#14)
================================================================================

PROPÓSITO
    Línea de tiempo horizontal de 24 h, dibujada con QPainter, que visualiza los
    segmentos de grabación de una cámara y permite hacer seek (click/arrastre) y
    ver marcas de eventos. Es el control principal de navegación temporal de la
    vista de Reproducción.

RESPONSABILIDAD
    - Pintar la rejilla horaria (00:00–24:00), los segmentos de grabación
      coloreados por tipo (continuo / evento / clip), las marcas de eventos
      (triángulos de color por tipo) y la línea roja de posición actual.
    - Convertir píxeles ↔ segundos desde medianoche y emitir señales de
      navegación cuando el usuario hace click o arrastra.

PIPELINE
    #14 Reproducción. La timeline NO reproduce: emite señales que la vista
    traduce en seek sobre playback_service (que controla el VLC de video_player).
    Flujo: click/drag → segment_clicked / position_changed → vista de
    Reproducción → playback_service (carga grabación + seek) → VLC.

MODELO DE DATOS
    Trabaja sobre RecordingSegment (models/recording.py): start_seconds,
    duration_seconds, recording_id, has_clip. Las marcas de evento son tuplas
    (segundos_desde_medianoche, tipo_evento).

DEPENDENCIAS
    PySide6 (QWidget, QPainter, eventos de ratón/rueda, QToolTip),
    models.recording.RecordingSegment, config (colores del tema).

COMPONENTES RELACIONADOS
    services/playback_service.py (ejecuta el seek que esta barra solicita),
    video_player.py (superficie donde se ve el resultado), la vista de
    Reproducción que cablea ambas.

DÓNDE SE USA
    En la vista de Reproducción (ui/views/playback_view), bajo el VideoPlayerWidget.
================================================================================
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
    Timeline horizontal interactivo para visualizar segmentos de grabación y
    navegar por ellos (seek por click/arrastre).

    Rol: control de navegación temporal de la vista de Reproducción.

    Quién la instancia/consume:
        La vista de Reproducción la crea, le pasa los datos con
        set_segments()/set_event_markers() y conecta sus señales para hacer
        seek sobre playback_service.

    SEÑALES Qt que EMITE:
      - segment_clicked(recording_id: int, seconds_offset: int): al hacer click
        DENTRO de un segmento; indica qué grabación abrir y en qué segundo.
      - position_changed(seconds: int): al mover la posición actual (click o
        arrastre); segundos desde medianoche (0-86399).
    SEÑALES que RECIBE: ninguna (los datos entran por los setters públicos).

    Dependencias: RecordingSegment (modelo), QPainter (render), config (colores).
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
        # Marcas de evento: lista de (segundos_desde_medianoche, tipo_evento).
        self.event_markers: List[tuple] = []
        
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
        """Establece los segmentos de grabación a mostrar (los ordena por inicio).

        Inputs: segments (lista de RecordingSegment del día seleccionado).
        Outputs: ninguno (repinta).
        Llamado por: la vista de Reproducción al cargar las grabaciones del día.
        """
        self.segments = sorted(segments, key=lambda x: x.start)
        self.update()

    def set_current_time(self, seconds: int):
        """Mueve la línea de posición actual y notifica el cambio.

        Inputs: seconds (segundos desde medianoche; se clampa a 0-duration).
        Outputs: ninguno (repinta).
        Señales: emite position_changed(seconds).
        Llamado por: click/arrastre del usuario y por la vista al sincronizar
            la barra con el avance de la reproducción.
        """
        self.current_time = max(0, min(seconds, self.duration))
        self.update()
        self.position_changed.emit(self.current_time)

    def set_event_markers(self, markers: List[tuple]):
        """Fija las marcas de evento a dibujar sobre la barra.

        Inputs: markers (lista de (segundos_desde_medianoche, tipo_evento)).
        Outputs: ninguno (repinta).
        Llamado por: la vista de Reproducción al cargar los eventos del día.
        """
        self.event_markers = markers or []
        self.update()
    
    def paintEvent(self, event: QPaintEvent):
        """Renderiza el timeline."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Fondo
        painter.fillRect(self.rect(), self.colors['background'])
        
        # Calcular escala (evitar división por cero si aún no hay tamaño o la
        # duración no es válida: el widget puede pintarse antes de tener ancho).
        width = self.width()
        height = self.height()
        if self.duration and self.duration > 0 and width > 0:
            self.pixels_per_second = width / self.duration
        else:
            self.pixels_per_second = 0.0
        
        # Dibujar grid de horas
        self._draw_grid(painter, width, height)
        
        # Dibujar segmentos
        self._draw_segments(painter, height)

        # Dibujar marcas de evento (puntos de color arriba)
        self._draw_event_markers(painter, height)

        # Dibujar línea de tiempo actual
        self._draw_current_line(painter, width, height)

        painter.end()

    # Colores por tipo de evento (coherente con events_view).
    _EVENT_COLORS = {
        "person": QColor("#ef4444"),
        "vehicle": QColor("#f59e0b"),
        "motion": QColor("#38bdf8"),
        "camera_offline": QColor("#fb7185"),
    }

    def _draw_event_markers(self, painter: QPainter, height: int):
        """Dibuja un triangulito de color por cada evento, sobre la barra."""
        if not self.event_markers:
            return
        for secs, etype in self.event_markers:
            x = int(secs * self.pixels_per_second)
            color = self._EVENT_COLORS.get(etype, QColor(config.THEME_ACCENT))
            painter.setBrush(color)
            painter.setPen(QPen(color.darker(140), 1))
            # Triángulo apuntando hacia abajo, cerca del borde superior.
            from PySide6.QtGui import QPolygon
            tri = QPolygon([
                QPoint(x - 4, 14), QPoint(x + 4, 14), QPoint(x, 22),
            ])
            painter.drawPolygon(tri)
    
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
        """Click en la timeline: traduce X→segundo, busca segmento e inicia drag.

        Si el click cae dentro de un segmento, emite segment_clicked(recording_id,
        offset) para que la vista abra ESA grabación en ese punto; en cualquier
        caso mueve la posición actual y arranca el modo arrastre.

        Inputs: event (QMouseEvent; usa botón izquierdo y posición X).
        Señales: emite segment_clicked (si hay segmento) y position_changed.
        Llamado por: Qt al pulsar el ratón sobre el widget.
        Llama a: set_current_time.
        """
        if event.button() == Qt.LeftButton:
            # Si aún no se ha calculado la escala (widget sin pintar), ignorar
            # el clic en vez de dividir por cero.
            if not self.pixels_per_second or self.pixels_per_second <= 0:
                return
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
        """Movimiento del ratón: resalta el segmento bajo el cursor, muestra
        tooltip de hora/duración y, si se está arrastrando, hace scrubbing.

        Inputs: event (QMouseEvent; posición X y global para el tooltip).
        Señales: position_changed (indirecta, vía set_current_time durante drag).
        Llamado por: Qt al mover el ratón (mouseTracking activado).
        Llama a: set_current_time (solo en drag), QToolTip.showText.
        """
        if not self.pixels_per_second or self.pixels_per_second <= 0:
            return
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