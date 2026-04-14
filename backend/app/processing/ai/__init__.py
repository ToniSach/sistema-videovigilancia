"""
AI processing module.
"""

#from .yolo_detector import YOLODetector, Detection
from .inference_queue import InferenceQueue, InferenceTask
from .ai_scheduler import AIScheduler

__all__ = ["Detection", "InferenceQueue", "InferenceTask", "AIScheduler"]
