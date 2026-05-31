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
    """
    Estado completo del sistema para el SystemHealthView del desktop.

    Devuelve directamente:
      {
        "success": true,
        "data": {
          "system": {"cpu_percent": ..., "memory_percent": ..., "disk_percent": ...},
          "cameras": [{"id":..., "status":..., "fps":..., ...}, ...],
          "version": "...",
          "timestamp": ...,
        }
      }

    Antes envolvía todo en `system_health` y los stats no llegaban al frontend,
    por eso "Estado del sistema" no mostraba nada en la app de escritorio.
    """
    try:
        health_data = metrics_collector.get_health_status()
        camera_manager = CameraManager()
        # health_data ya viene con la forma {system: {...}, cameras: [...]}
        health_data["version"] = "1.0.0"
        health_data["timestamp"] = time.time()
        health_data["cameras_active"] = len(camera_manager._workers)
        health_data["cameras_total"] = (
            len(camera_manager._camera_repo.get_all())
            if hasattr(camera_manager, '_camera_repo') else 0
        )
        return jsonify({"success": True, "data": health_data}), 200
    except Exception as e:
        logger.error(f"Error en health check: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error interno"}), 500


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
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


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
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


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
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@system_bp.route("/hardware", methods=["GET"])
@jwt_required()
def get_hardware_info():
    try:
        from backend.app.core.hardware_detector import hardware_detector
        return jsonify({"success": True, "data": hardware_detector.detect()}), 200
    except Exception as e:
        logger.error(f"Error obteniendo hardware info: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


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
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500
# =============================================================================
# Test event: dispara un evento simulado de "persona" con snapshot + clip de
# ~20s (10 pre-buffer + 10 post) y lo envía a Telegram. Útil para verificar
# la configuración Telegram sin esperar a una detección real.
# =============================================================================
import threading as _threading
import time as _time
from datetime import datetime
from pathlib import Path


def _record_and_send_test_event(camera_id: int, post_seconds: int = 10):
    """
    Worker en background:
      1. Snapshot inmediato del último frame del buffer pre-evento.
      2. Reusa RecordingManager._on_event() para grabar pre+post clip.
      3. Envía snapshot + video a Telegram.
    """
    try:
        from backend.app.container import get_container
        from backend.app.recording.recording_manager import RecordingManager
        from backend.app.notifications.telegram_notifier import telegram_notifier
        from backend.app.events.event_manager import EventData
        from backend.app.config import settings
        from backend.app.database.repositories.camera_repository import CameraRepository
        import cv2

        rec_mgr: RecordingManager = get_container().get("recording_manager")
        cam_repo = CameraRepository()
        camera = cam_repo.get_by_id(camera_id)
        if not camera:
            logger.error(f"[TEST EVENT] cam {camera_id} no existe")
            return

        # 1) Snapshot del frame más reciente
        with rec_mgr._pre_buffer_lock:
            buf = rec_mgr._pre_buffers.get(camera_id)
            latest = buf[-1][0].copy() if (buf and len(buf) > 0) else None

        if latest is None:
            logger.error(
                f"[TEST EVENT] cam {camera_id} sin frames (¿inactiva?)"
            )
            return

        snapshots_dir = Path(settings.RECORDINGS_PATH) / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_path = snapshots_dir / f"test_event_cam{camera_id}_{ts}.jpg"
        cv2.imwrite(str(snapshot_path), latest)
        logger.info(f"[TEST EVENT] snapshot: {snapshot_path}")

        # 2) Grabar clip pre+post usando el código existente
        # RecordingManager._record_event_clip() captura PRE_BUFFER (~10s) +
        # EVENT_RECORDING_DURATION segundos post (default 10s).
        post_seconds = max(5, min(int(post_seconds), 30))
        original_duration = rec_mgr.EVENT_RECORDING_DURATION
        rec_mgr.EVENT_RECORDING_DURATION = post_seconds

        event_data = EventData(
            event_type="person",
            camera_id=camera_id,
            camera_name=camera.name,
            timestamp=_time.time(),
            confidence=0.95,
            frame=latest,
            metadata={
                "test_event": True,
                "snapshot_path": str(snapshot_path),
                "source": "manual_test",
            },
        )

        try:
            rec_mgr._on_event(event_data)
            # Esperar a que termine el clip: pre + post + buffer encoder
            wait_total = (rec_mgr.PRE_BUFFER_SIZE / 15) + post_seconds + 3
            _time.sleep(wait_total)
        finally:
            rec_mgr.EVENT_RECORDING_DURATION = original_duration

        # 3) Buscar el clip generado y enviarlo
        clip_dir = Path(settings.RECORDINGS_PATH) / str(camera_id) / "events"
        clips = sorted(
            clip_dir.glob("event_person_*.mp4"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        clip_path = str(clips[0]) if clips else None
        if clip_path:
            logger.info(f"[TEST EVENT] clip: {clip_path}")
            event_data.metadata["clip_path"] = clip_path
        else:
            logger.warning("[TEST EVENT] no se generó clip")

        caption = (
            f"🧪 *EVENTO DE PRUEBA*\n\n"
            f"📹 Cámara: `{camera.name}` (#{camera_id})\n"
            f"🎯 Tipo: persona (simulado)\n"
            f"📊 Confianza: 95%\n"
            f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n\n"
            f"_Mensaje de prueba enviado desde el panel de administración_"
        )

        success = True
        for chat_id in telegram_notifier._chat_ids:
            ok_photo = telegram_notifier._send_photo(
                chat_id, str(snapshot_path), caption
            )
            success = success and ok_photo
            if clip_path and Path(clip_path).exists():
                telegram_notifier.send_video(
                    chat_id, clip_path,
                    f"🎥 Video del evento (10s pre + {post_seconds}s post)",
                )

        logger.info(
            f"[TEST EVENT] envío Telegram: {'OK' if success else 'FALLÓ'}"
        )

    except Exception as e:
        logger.error(f"[TEST EVENT] error: {e}", exc_info=True)


@system_bp.route("/test-telegram/<int:camera_id>", methods=["POST"])
@jwt_required()
@admin_required
def trigger_test_event(camera_id):
    """
    Dispara un evento de prueba simulado en la cámara indicada y envía
    foto + video clip a Telegram.

    Body opcional:
        {"post_seconds": 10}  # 5-30s, default 10

    Devuelve 202 Accepted inmediatamente; el envío ocurre en background.
    """
    try:
        from backend.app.notifications.telegram_notifier import telegram_notifier
        if not telegram_notifier._enabled or not telegram_notifier._chat_ids:
            return jsonify({
                "success": False,
                "error": "Telegram no está configurado en SystemConfig "
                         "(claves: telegram_enabled, telegram_chat_id)",
            }), 400

        body = request.get_json(silent=True) or {}
        post_seconds = max(5, min(int(body.get("post_seconds", 10)), 30))

        # Antes se creaba un threading.Thread daemon por cada llamada. Con N
        # usuarios disparando test events simultáneos crecía sin límite. El
        # GlobalExecutor está capado a 50 workers y aplica backpressure.
        from backend.app.core.executor import global_executor
        future = global_executor.submit(
            _record_and_send_test_event, camera_id, post_seconds
        )
        if future is None:
            return jsonify({
                "success": False,
                "error": "Sistema saturado, intenta más tarde",
            }), 503

        return jsonify({
            "success": True,
            "message": (
                f"Evento de prueba lanzado en cámara {camera_id}. "
                f"Foto inmediata + video de ~{10 + post_seconds}s "
                f"en Telegram en ~{post_seconds + 15}s."
            ),
            "estimated_seconds": post_seconds + 15,
        }), 202

    except Exception as e:
        logger.error(f"Error disparando test event: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error interno"}), 500
