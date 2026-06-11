"""Subpaquete `motion` del Pipeline de IA (#9): expone `MotionDetector`/`MotionResult`, la compuerta de movimiento que decide cuándo ejecutar YOLO."""

from .motion_detector import MotionDetector, MotionResult

__all__ = ["MotionDetector", "MotionResult"]
