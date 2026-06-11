"""
================================================================================
MÓDULO: desktop_app.models.recording — DTOs de grabaciones para el timeline
================================================================================

PROPÓSITO
    Definir los DTOs del cliente que alimentan la vista de playback histórico
    (pipeline #14): `RecordingSegment` (un tramo grabado en el timeline) y
    `TimelineDay` (todos los segmentos de un día para una cámara). Espejan el
    JSON de /recordings; NO son las clases SQLAlchemy del backend.

RESPONSABILIDAD
    - RecordingSegment: metadatos de un tramo (id, cámara, inicio/fin, duración,
      tamaño, si tiene clip de evento) + helpers para posicionarlo en el
      timeline (start_seconds = segundos desde medianoche; duration).
    - TimelineDay: agregado por día (fecha, cámara, lista de segmentos y total).

DEPENDENCIAS
    - dataclasses, datetime/timedelta, typing (sin Qt ni backend).

COMPONENTES RELACIONADOS
    - ui/views/playback_view y el componente timeline los construyen a partir de
      la respuesta de api_client (GET /recordings). El id de un segmento se pasa
      luego a playback_service.play_recording para descargar y reproducir.

PUNTO DE ENTRADA
    `from desktop_app.src.models.recording import RecordingSegment, TimelineDay`.

SINCRONIZACIÓN
    Mantener en línea con backend/app/database/models.py:Recording (campos y
    semántica de duración/tamaño/clip).
================================================================================
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class RecordingSegment:
    """
    NIVEL 2 — DTO de un tramo de grabación (unidad del timeline).

    Rol: representar un segmento grabado y ubicarlo en la barra de tiempo. `end`
    puede ser None si la grabación sigue en curso (se usa duration_seconds como
    respaldo). `has_clip` marca si lleva asociado un clip de evento. Los helpers
    start_seconds y duration los consume el widget de timeline para dibujarlo.
    """
    recording_id: int
    camera_id: int
    start: datetime
    end: Optional[datetime]
    duration_seconds: float
    file_size_mb: float
    has_clip: bool = False
    
    @property
    def start_seconds(self) -> int:
        """Segundos desde medianoche."""
        return self.start.hour * 3600 + self.start.minute * 60 + self.start.second
    
    @property
    def duration(self) -> timedelta:
        if self.end:
            return self.end - self.start
        return timedelta(seconds=self.duration_seconds)


@dataclass
class TimelineDay:
    """
    NIVEL 2 — DTO agregado de un día completo de grabaciones de una cámara.

    Rol: agrupar todos los RecordingSegment de una fecha (YYYY-MM-DD) para una
    cámara, con la duración total acumulada. Lo consume el selector de día /
    timeline de la vista de playback para pintar la jornada de un vistazo.
    """
    date: str  # YYYY-MM-DD
    camera_id: int
    segments: list
    total_duration_seconds: float = 0.0