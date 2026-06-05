"""
Mobile API Routes - Endpoints optimizados para aplicaciones móviles.
Incluye dashboard agregado, historial de notificaciones, y detección de HLS.
"""
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from backend.app.services.permission_service import PermissionService
from backend.app.infrastructure.metrics.collector import metrics_collector
from backend.app.database.repositories.event_repository import EventRepository
from backend.app.database.repositories.recording_repository import RecordingRepository
from backend.app.cameras.camera_manager import CameraManager

logger = logging.getLogger(__name__)

mobile_bp = Blueprint("mobile", __name__, url_prefix="/api/v1/mobile")


def is_mobile_request() -> bool:
    """
    Detecta si la petición viene de un dispositivo móvil basado en User-Agent.
    """
    user_agent = request.headers.get('User-Agent', '').lower()
    mobile_indicators = [
        'mobile', 'android', 'iphone', 'ipad', 'ipod', 
        'blackberry', 'windows phone', 'webos', 'iemobile'
    ]
    return any(indicator in user_agent for indicator in mobile_indicators)


@mobile_bp.route("/version", methods=["GET"])
def get_api_version():
    """
    Retorna versión de API para negociación de compatibilidad con app móvil.
    """
    return jsonify({
        "success": True,
        "data": {
            "version": "1.2.0",  # Versión actual del backend
            "min_supported_client": "1.0.0",  # Mínima versión de app soportada
            "features": [
                "hls_streaming",
                "adaptive_bitrate",
                "push_notifications",
                "ptz_control",
                "event_history"
            ],
            "server_time": datetime.utcnow().isoformat()
        }
    }), 200


@mobile_bp.route("/dashboard", methods=["GET"])
@jwt_required()
def get_dashboard():
    """
    Dashboard agregado para pantalla principal de app móvil.
    Retorna resumen de todo en una sola petición (minimiza latencia móvil).
    """
    try:
        user_id = int(get_jwt_identity())
        permission_service = PermissionService()
        
        # Cámaras accesibles
        accessible_cameras = permission_service.get_accessible_cameras(user_id)
        camera_manager = CameraManager()
        
        cameras_data = []
        for cam_id in accessible_cameras[:10]:  # Limitar a 10 para performance
            worker = camera_manager.get_worker(cam_id)
            metrics = metrics_collector.get_camera_metrics(cam_id)
            
            # El directo se sirve por go2rtc (HLS/WebRTC). Aquí devolvemos el
            # endpoint HLS como referencia; el cliente usa la stream_url real
            # que entrega /cameras. (MJPEG eliminado.)
            is_mobile = is_mobile_request()
            stream_url = f"/api/v1/cameras/{cam_id}/stream.m3u8"  # HLS
            
            cameras_data.append({
                "id": cam_id,
                "status": "online" if worker and worker.get_status().get("status") == "running" else "offline",
                "fps": round(metrics.fps, 1) if metrics else 0,
                "stream_url": stream_url,
                "is_mobile_optimized": is_mobile,
                "thumbnail_url": f"/api/v1/mobile/cameras/{cam_id}/thumbnail"  # FIX F2.3: Thumbnail endpoint
            })
        
        # Eventos recientes (últimas 24h)
        event_repo = EventRepository()
        recent_events = event_repo.get_recent(hours=24, limit=5)
        
        # Estadísticas de almacenamiento
        recording_repo = RecordingRepository()
        total_storage = recording_repo.get_total_size_bytes() / (1024**3)  # GB
        
        # Notificaciones no leídas (placeholder para futura implementación)
        unread_count = 0  # TODO: Implementar cuando haya sistema de "read" en notificaciones
        
        return jsonify({
            "success": True,
            "data": {
                "user": {
                    "id": user_id,
                    "accessible_cameras_count": len(accessible_cameras)
                },
                "cameras": cameras_data,
                "today_events_summary": {
                    "total": len(recent_events),
                    "unacknowledged": len([e for e in recent_events if not e.acknowledged]),
                    "latest": [e.to_dict() for e in recent_events[:3]]
                },
                "storage": {
                    "used_gb": round(total_storage, 2),
                    "warning": total_storage > (settings.MAX_STORAGE_GB * 0.8) if hasattr(settings, 'MAX_STORAGE_GB') else False
                },
                "system_status": {
                    "healthy": True,
                    "timestamp": time.time()
                }
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Error en dashboard móvil: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error interno"}), 500


@mobile_bp.route("/notifications/history", methods=["GET"])
@jwt_required()
def get_notifications_history():
    """
    Historial de notificaciones para app móvil (infinite scroll).
    Soporta paginación por cursor (timestamp) para eficiencia.
    """
    try:
        user_id = int(get_jwt_identity())
        permission_service = PermissionService()
        accessible_cameras = permission_service.get_accessible_cameras(user_id)
        
        # Parámetros de paginación
        cursor = request.args.get("cursor", type=float)  # Timestamp del último item
        limit = min(request.args.get("limit", 20, type=int), 50)  # Max 50 por request
        
        event_repo = EventRepository()
        
        # FIX F2.2: Obtener eventos de cámaras accesibles
        if not accessible_cameras:
            return jsonify({
                "success": True,
                "data": [],
                "pagination": {"has_more": False}
            }), 200
        
        # Construir query base
        base_time = datetime.fromtimestamp(cursor) if cursor else datetime.utcnow()
        hours_back = 168  # 7 días de historial máximo
        
        from datetime import timedelta
        since = base_time - timedelta(hours=hours_back)
        
        # Obtener eventos recientes (usando método existente y filtrando)
        all_recent = event_repo.get_recent(hours=hours_back, limit=200)
        
        # Filtrar por cámaras accesibles y tiempo cursor
        events = [
            e for e in all_recent 
            if e.camera_id in accessible_cameras and e.created_at < base_time
        ]
        
        # Ordenar por fecha descendente y limitar
        events = sorted(events, key=lambda x: x.created_at, reverse=True)[:limit]
        
        # Preparar respuesta con URLs de thumbnails
        results = []
        for event in events:
            data = event.to_dict()
            # Agregar URL de thumbnail si existe snapshot
            if event.snapshot_path:
                data['thumbnail_url'] = f"/api/v1/events/{event.id}/snapshot"
            results.append(data)
        
        # Preparar cursor para siguiente página
        next_cursor = None
        if events and len(events) == limit:
            next_cursor = events[-1].created_at.timestamp()
        
        return jsonify({
            "success": True,
            "data": results,
            "pagination": {
                "has_more": next_cursor is not None,
                "next_cursor": next_cursor,
                "count": len(results)
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Error en historial de notificaciones: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@mobile_bp.route("/cameras/<int:camera_id>/thumbnail", methods=["GET"])
@jwt_required()
def get_camera_thumbnail(camera_id: int):
    """
    Retorna thumbnail actual de la cámara (para lista de cámaras en móvil).
    Generado on-the-fly desde el último frame disponible.
    """
    try:
        user_id = int(get_jwt_identity())
        permission_service = PermissionService()
        
        if not permission_service.check_permission(user_id, camera_id, 'view'):
            return jsonify({"success": False, "error": "Permiso denegado"}), 403
        
        # FIX F2.3: Obtener último frame y generar thumbnail
        from backend.app.streaming.frame_buffer import CircularFrameBuffer
        from backend.app.cameras.camera_manager import CameraManager
        
        # Intentar obtener frame del buffer
        camera_manager = CameraManager()
        distributor = camera_manager.get_distributor(camera_id)
        
        if not distributor:
            return jsonify({"success": False, "error": "Cámara no disponible"}), 404
        
        # Obtener último frame (esto requiere que FrameBuffer tenga método get_latest_snapshot)
        # Por simplicidad, usaremos el evento más reciente si existe
        event_repo = EventRepository()
        recent_events = event_repo.get_by_camera(camera_id, limit=1)
        
        if recent_events and recent_events[0].snapshot_path:
            from flask import send_file
            import os
            
            snapshot_path = recent_events[0].snapshot_path
            
            # FIX F2.3: Verificar path seguro (reutilizar lógica de events.py)
            from pathlib import Path
            from backend.app.config import settings
            
            recordings_path = Path(settings.RECORDINGS_PATH).resolve()
            requested = Path(snapshot_path).resolve()
            
            if not str(requested).startswith(str(recordings_path)):
                return jsonify({"error": "Invalid path"}), 403
            
            if not requested.exists():
                return jsonify({"success": False, "error": "No hay imagen disponible"}), 404
            
            # En Fase 2 avanzada, aquí redimensionaríamos con Pillow
            # Por ahora servimos el original (Fase 3 puede agregar resize dinámico)
            return send_file(
                str(requested),
                mimetype="image/jpeg",
                as_attachment=False
            )
        else:
            return jsonify({"success": False, "error": "No hay snapshot reciente"}), 404
            
    except Exception as e:
        logger.error(f"Error generando thumbnail: {e}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


# (Endpoints HLS-live eliminados: el directo móvil usa RTSP/go2rtc vía ExoPlayer.)


# ============================================================================
# RECORDINGS — endpoints específicos para móvil
# ============================================================================

@mobile_bp.route("/cameras/<int:camera_id>/recordings", methods=["GET"])
@jwt_required()
def list_camera_recordings(camera_id: int):
    """
    Lista grabaciones de una cámara con filtros por rango.

    Query params:
      from   ISO datetime (ej: 2026-05-23T00:00:00)
      to     ISO datetime
      limit  máximo de resultados (default 50, max 200)
      offset paginación
    """
    try:
        user_id = int(get_jwt_identity())
        permission_service = PermissionService()
        if not permission_service.check_permission(user_id, camera_id, 'view'):
            return jsonify({"success": False, "error": "Permiso denegado"}), 403

        # Parseo de filtros
        from_str = request.args.get("from")
        to_str = request.args.get("to")
        try:
            from_dt = datetime.fromisoformat(from_str) if from_str else None
            to_dt = datetime.fromisoformat(to_str) if to_str else None
        except ValueError:
            return jsonify({
                "success": False,
                "error": "from/to inválido (usar ISO 8601)"
            }), 400

        limit = min(max(int(request.args.get("limit", 50)), 1), 200)
        offset = max(int(request.args.get("offset", 0)), 0)

        repo = RecordingRepository()
        if from_dt and to_dt:
            recordings = repo.get_by_date_range(camera_id, from_dt, to_dt)
            # Aplicar paginación manualmente (el repo no la soporta)
            recordings = recordings[offset:offset + limit]
        else:
            # Sin rango → últimos N por cámara
            recordings = repo.get_by_camera(camera_id, limit=limit + offset)
            recordings = recordings[offset:offset + limit]

        # Serializar como dicts ligeros (sin path absoluto)
        data = []
        for r in recordings:
            data.append({
                "id": r.id,
                "camera_id": r.camera_id,
                "start_time": r.start_time.isoformat() if r.start_time else None,
                "end_time": r.end_time.isoformat() if r.end_time else None,
                "duration_seconds": r.duration_seconds,
                "file_size_bytes": r.file_size_bytes,
            })

        return jsonify({
            "success": True,
            "data": data,
            "count": len(data),
            "limit": limit,
            "offset": offset,
        }), 200

    except Exception as e:
        logger.error(f"Error listando grabaciones móvil: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error interno"}), 500


@mobile_bp.route("/recordings/<int:recording_id>/download", methods=["GET"])
@jwt_required()
def download_recording(recording_id: int):
    """
    Descarga una grabación. Valida que el usuario tenga permiso 'view' o
    'download' sobre la cámara correspondiente.

    Path traversal cerrado: el file_path se resuelve y se verifica que esté
    bajo RECORDINGS_PATH.
    """
    try:
        from pathlib import Path
        from flask import send_file
        from backend.app.config import settings

        user_id = int(get_jwt_identity())
        repo = RecordingRepository()
        recording = repo.get_by_id(recording_id) if hasattr(repo, "get_by_id") else None
        if not recording:
            return jsonify({"success": False, "error": "Grabación no encontrada"}), 404

        permission_service = PermissionService()
        if not (
            permission_service.check_permission(user_id, recording.camera_id, 'download')
            or permission_service.check_permission(user_id, recording.camera_id, 'view')
        ):
            return jsonify({"success": False, "error": "Permiso denegado"}), 403

        recordings_root = Path(settings.RECORDINGS_PATH).resolve()
        try:
            file_path = Path(recording.file_path).resolve(strict=True)
            file_path.relative_to(recordings_root)
        except (ValueError, FileNotFoundError):
            logger.error(
                f"Path traversal o archivo no existe: rec={recording_id} "
                f"path={recording.file_path}"
            )
            return jsonify({"success": False, "error": "Archivo no disponible"}), 404

        return send_file(
            str(file_path),
            mimetype="video/mp4",
            as_attachment=True,
            download_name=f"recording_{recording_id}.mp4",
        )

    except Exception as e:
        logger.error(f"Error descargando grabación móvil: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Error interno"}), 500