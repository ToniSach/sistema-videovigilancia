from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from ...container import get_container
from ...cameras.camera_manager import CameraManager
from ...database.repositories.camera_repository import CameraRepository
import logging
import time
import psutil
import weakref


system_bp = Blueprint("system", __name__, url_prefix="/api/v1/system")
logger = logging.getLogger(__name__)


# ==============================
# HEALTH MONITOR
# ==============================
class HealthMonitor:
    def __init__(self):
        self._checks = {}
        self._running = False

    def register_camera_check(self, camera_id, worker):
        self._checks[camera_id] = {
            'last_frame': time.time(),
            'worker': weakref.ref(worker)
        }

    def update_frame(self, camera_id):
        """Actualizar timestamp cuando llega un frame."""
        if camera_id in self._checks:
            self._checks[camera_id]['last_frame'] = time.time()

    def _calculate_fps(self, camera_id):
        # Placeholder básico (puedes mejorarlo luego)
        return 0

    def get_system_health(self):
        return {
            'cameras': [
                {
                    'id': cid,
                    'status': 'healthy' if time.time() - info['last_frame'] < 5 else 'stalled',
                    'fps': self._calculate_fps(cid)
                }
                for cid, info in self._checks.items()
            ],
            'memory': psutil.virtual_memory().percent,
            'disk': psutil.disk_usage('/').percent
        }


# Instancia global (simple singleton)
health_monitor = HealthMonitor()


# ==============================
# ROUTES
# ==============================

@system_bp.route("/health", methods=["GET"])
@jwt_required()
def health_check():
    """Health check del sistema."""
    try:
        camera_manager = CameraManager()

        health_data = health_monitor.get_system_health()

        return jsonify({
            "status": "ok",
            "timestamp": time.time(),
            "cameras_active": len(camera_manager._workers),
            "cameras_total": len(camera_manager._camera_repo.get_all()) if hasattr(camera_manager, '_camera_repo') else 0,
            "system_health": health_data,
            "version": "1.0.0"
        })

    except Exception as e:
        logger.error(f"Error en health check: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@system_bp.route("/stats", methods=["GET"])
@jwt_required()
def get_stats():
    """Estadísticas generales del sistema."""
    try:
        camera_manager = CameraManager()

        # Contar cámaras por estado
        all_status = camera_manager.get_all_status()
        active_count = sum(1 for s in all_status.values() if s.get("status") == "running")
        error_count = sum(1 for s in all_status.values() if s.get("status") == "error")

        return jsonify({
            "success": True,
            "data": {
                "cameras": {
                    "total": len(all_status),
                    "active_streaming": active_count,
                    "error": error_count,
                    "details": all_status
                },
                "system": {
                    "version": "1.0.0",
                    "uptime_seconds": None  # TODO: implementar tracking de uptime
                }
            }
        })

    except Exception as e:
        logger.error(f"Error al obtener estadísticas: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@system_bp.route("/config", methods=["GET"])
@jwt_required()
def get_config():
    """Obtener configuración del sistema."""
    try:
        from ...database.connection import db_manager
        from ...database.models import SystemConfig

        session = db_manager.get_session()
        configs = session.query(SystemConfig).all()

        config_dict = {}
        for config in configs:
            config_dict[config.key] = config.value

        return jsonify({
            "success": True,
            "data": config_dict
        })

    except Exception as e:
        logger.error(f"Error al obtener configuración: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@system_bp.route("/config", methods=["PUT"])
@jwt_required()
def update_config():
    """Actualizar configuración del sistema."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No se proporcionaron datos"}), 400

        from ...database.connection import db_manager
        from ...database.models import SystemConfig

        session = db_manager.get_session()

        for key, value in data.items():
            config = session.query(SystemConfig).filter_by(key=key).first()
            if config:
                config.value = str(value)
            else:
                new_config = SystemConfig(key=key, value=str(value), description="")
                session.add(new_config)

        session.commit()

        return jsonify({
            "success": True,
            "message": "Configuración actualizada"
        })

    except Exception as e:
        logger.error(f"Error al actualizar configuración: {e}")
        return jsonify({"success": False, "error": str(e)}), 500