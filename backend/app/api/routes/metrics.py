"""
Prometheus Metrics Endpoint - Exporta métricas para monitoreo.
Requiere instalación: pip install prometheus-client
"""
from flask import Blueprint, Response
from flask_jwt_extended import jwt_required
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Counter, Gauge, Histogram
import time
import psutil

from backend.app.infrastructure.metrics.collector import metrics_collector
from backend.app.cameras.camera_manager import CameraManager

metrics_bp = Blueprint("metrics", __name__, url_prefix="/metrics")

# Definición de métricas Prometheus
CAMERA_FRAMES = Counter('camera_frames_total', 'Total frames processed per camera', ['camera_id'])
CAMERA_FPS = Gauge('camera_fps', 'Current FPS per camera', ['camera_id'])
CAMERA_STATUS = Gauge('camera_status', 'Camera status (1=online, 0=offline)', ['camera_id'])
EVENTS_TOTAL = Counter('events_total', 'Total events by type', ['event_type'])
STORAGE_USED_BYTES = Gauge('storage_used_bytes', 'Total storage used by recordings')
SYSTEM_CPU = Gauge('system_cpu_percent', 'System CPU usage percent')
SYSTEM_MEMORY = Gauge('system_memory_percent', 'System memory usage percent')
REQUEST_DURATION = Histogram('http_request_duration_seconds', 'HTTP request latency', ['endpoint'])


@metrics_bp.route("/", methods=["GET"])
@jwt_required()
def metrics():
    """
    Endpoint protegido que expone métricas en formato Prometheus.
    En producción, se recomienda exponer sin autenticación pero restringir por IP.
    """
    # Actualizar métricas dinámicas antes de exportar
    try:
        # Métricas de cámaras
        camera_manager = CameraManager()
        all_metrics = metrics_collector.get_all_camera_metrics()
        
        for cam_id, cam_metrics in all_metrics.items():
            CAMERA_FPS.labels(camera_id=str(cam_id)).set(cam_metrics.fps)
            # Verificar si cámara está activa
            worker = camera_manager.get_worker(cam_id)
            is_online = 1 if (worker and worker.get_status().get("status") == "running") else 0
            CAMERA_STATUS.labels(camera_id=str(cam_id)).set(is_online)
        
        # Almacenamiento
        from backend.app.database.repositories.recording_repository import RecordingRepository
        repo = RecordingRepository()
        total_bytes = repo.get_total_size_bytes()
        STORAGE_USED_BYTES.set(total_bytes)
        
        # Sistema
        SYSTEM_CPU.set(psutil.cpu_percent(interval=None))
        SYSTEM_MEMORY.set(psutil.virtual_memory().percent)
        
    except Exception as e:
        # No fallar si hay error en métricas
        import logging
        logging.getLogger(__name__).error(f"Error actualizando métricas: {e}")
    
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)