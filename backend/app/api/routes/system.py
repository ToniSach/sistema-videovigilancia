from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from ...cameras.camera_manager import CameraManager
import logging
import time
import psutil
import weakref
import threading
from typing import Dict

from backend.app.core.security import admin_required
from backend.app.infrastructure.metrics.collector import metrics_collector

system_bp = Blueprint("system", __name__, url_prefix="/api/v1/system")
logger = logging.getLogger(__name__)


class HealthMonitor:
    def __init__(self):
        self._logger = logging.getLogger(__name__)
        self._logger.info("HealthMonitor inicializado (wrapper de MetricsCollector)")
    
    def get_system_health(self):
        return metrics_collector.get_health_status()

health_monitor = HealthMonitor()


@system_bp.route("/health", methods=["GET"])
@jwt_required()
def health_check():
    try:
        health_data = metrics_collector.get_health_status()
        camera_manager = CameraManager()
        return jsonify({
            "status": "ok",
            "timestamp": time.time(),
            "version": "1.0.0",
            "cameras_active": len(camera_manager._workers),
            "cameras_total": len(camera_manager._camera_repo.get_all()) if hasattr(camera_manager, '_camera_repo') else 0,
            "system_health": health_data
        })
    except Exception as e:
        logger.error(f"Error en health check: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@system_bp.route("/stats", methods=["GET"])
@jwt_required()
def get_stats():
    try:
        camera_manager = CameraManager()
        all_status = camera_manager.get_all_status()
        active_count = sum(1 for s in all_status.values() if s.get("status") == "running")
        error_count = sum(1 for s in all_status.values() if s.get("status") == "error")
        camera_metrics = metrics_collector.get_all_camera_metrics()
        fps_info = {cid: {"fps": m.fps, "frames": m.frame_count} for cid, m in camera_metrics.items()}
        return jsonify({
            "success": True,
            "data": {
                "cameras": {
                    "total": len(all_status),
                    "active_streaming": active_count,
                    "error": error_count,
                    "fps_metrics": fps_info,
                    "details": all_status
                },
                "system": {"version": "1.0.0", "uptime_seconds": None}
            }
        })
    except Exception as e:
        logger.error(f"Error al obtener estadísticas: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@system_bp.route("/config", methods=["GET"])
@jwt_required()
def get_config():
    try:
        from ...database.connection import db_manager
        from ...database.models import SystemConfig
        # ✅ CORREGIDO: usar context manager
        with db_manager.get_session() as session:
            configs = session.query(SystemConfig).all()
            return jsonify({
                "success": True,
                "data": {c.key: c.value for c in configs}
            })
    except Exception as e:
        logger.error(f"Error al obtener configuración: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@system_bp.route("/config", methods=["PUT"])
@jwt_required()
@admin_required
def update_config():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No se proporcionaron datos"}), 400

        from ...database.connection import db_manager
        from ...database.models import SystemConfig
        # ✅ CORREGIDO: usar context manager
        with db_manager.get_session() as session:
            for key, value in data.items():
                config = session.query(SystemConfig).filter_by(key=key).first()
                if config:
                    config.value = str(value)
                else:
                    session.add(SystemConfig(key=key, value=str(value)))
            session.commit()
        return jsonify({"success": True, "message": "Configuración actualizada"})
    except Exception as e:
        logger.error(f"Error al actualizar configuración: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@system_bp.route("/hardware", methods=["GET"])
@jwt_required()
def get_hardware_info():
    try:
        from backend.app.core.hardware_detector import hardware_detector
        return jsonify({"success": True, "data": hardware_detector.detect()}), 200
    except Exception as e:
        logger.error(f"Error obteniendo hardware info: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@system_bp.route("/config/hardware", methods=["PUT"])
@jwt_required()
def update_hardware_config():
    try:
        from flask_jwt_extended import get_jwt_identity
        from backend.app.services.user_service import UserService
        from backend.app.database.connection import db_manager
        from backend.app.database.models import SystemConfig

        user_id = int(get_jwt_identity())
        if not UserService().is_admin(user_id):
            return jsonify({"success": False, "error": "Admin requerido"}), 403

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No se proporcionaron datos"}), 400

        use_gpu = data.get("use_gpu_ai")
        backend = data.get("ai_backend")
        if use_gpu and use_gpu not in ["auto", "true", "false"]:
            return jsonify({"success": False, "error": "use_gpu_ai inválido"}), 400
        if backend and backend not in ["auto", "cuda", "cpu"]:
            return jsonify({"success": False, "error": "ai_backend inválido"}), 400

        with db_manager.get_session() as session:
            if use_gpu:
                session.merge(SystemConfig(key="use_gpu_ai", value=use_gpu))
            if backend:
                session.merge(SystemConfig(key="ai_backend", value=backend))
            session.commit()

        return jsonify({
            "success": True,
            "message": "Configuración actualizada. Reinicie cámaras.",
            "changes": {"use_gpu_ai": use_gpu, "ai_backend": backend}
        }), 200
    except Exception as e:
        logger.error(f"Error actualizando config hardware: {e}")
        return jsonify({"success": False, "error": str(e)}), 500