"""
Modelos de datos para grabaciones.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class RecordingSegment:
    """Segmento de grabación para timeline."""
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
    """Datos de un día completo para timeline."""
    date: str  # YYYY-MM-DD
    camera_id: int
    segments: list
    total_duration_seconds: float = 0.0