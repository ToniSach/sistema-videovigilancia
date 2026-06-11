"""Subpaquete `ai` del Pipeline de IA (#9): expone `AIScheduler` (orquestador worker) y `AIFrameSource` (fuente dedicada de frames desde go2rtc)."""

from .ai_scheduler import AIScheduler
from .ai_frame_source import AIFrameSource

__all__ = ["AIScheduler", "AIFrameSource"]
