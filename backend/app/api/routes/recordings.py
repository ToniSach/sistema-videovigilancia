"""
API Endpoints para grabaciones con timeline, playback streaming y descarga.
"""
import os
import mimetypes
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, send_file, Response
from flask_jwt_extended import jwt_required, get_jwt_identity

from backend.app.services.permission_service import PermissionService
from backend.app.database.repositories.recording_repository import RecordingRepository
from backend.app.config import settings

recordings_bp = Blueprint("recordings", __name__, url_prefix="/api/v1/recordings")
permission_service = PermissionService()


def get_recording_repo():
    """Repositorio sin parámetro de db_manager."""
    return RecordingRepository()  # ← CORREGIDO: sin parámetros


@recordings_bp.route("/", methods=["GET"])
@jwt_required()
def get_recordings():
    """Obtiene lista de grabaciones con filtros."""
    try:
        user_id = int(get_jwt_identity())
        camera_id = request.args.get("camera_id", type=int)
        date_str = request.args.get("date")  # YYYY-MM-DD
        limit = request.args.get("limit", default=100, type=int)

        # Verificar permisos
        if camera_id and not permission_service.check_permission(user_id, camera_id, 'view'):
            return jsonify({"success": False, "error": "Permiso denegado"}), 403
        
        accessible_cameras = permission_service.get_accessible_cameras(user_id)
        
        repo = get_recording_repo()
        
        if camera_id:
            if camera_id not in accessible_cameras:
                return jsonify({"success": False, "error": "Cámara no accesible"}), 403
            recordings = repo.get_by_camera(camera_id, limit=limit)
        else:
            # Obtener de todas las cámaras accesibles
            recordings = []
            for cam_id in accessible_cameras[:4]:  # Limitar para performance
                recs = repo.get_by_camera(cam_id, limit=limit//4)
                recordings.extend(recs)
            recordings = sorted(recordings, key=lambda x: x.start_time, reverse=True)[:limit]

        return jsonify({
            "success": True,
            "data": [r.to_dict() for r in recordings],
            "count": len(recordings)
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@recordings_bp.route("/timeline", methods=["GET"])
@jwt_required()
def get_timeline():
    """
    Obtiene segmentos de grabación para timeline.
    
    Query params:
    - camera_id: int (requerido)
    - date: str (YYYY-MM-DD, requerido)
    """
    try:
        user_id = int(get_jwt_identity())
        camera_id = request.args.get("camera_id", type=int)
        date_str = request.args.get("date")
        
        if not camera_id or not date_str:
            return jsonify({"success": False, "error": "camera_id y date requeridos"}), 400
        
        if not permission_service.check_permission(user_id, camera_id, 'view'):
            return jsonify({"success": False, "error": "Permiso denegado"}), 403
        
        # Parsear fecha
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"success": False, "error": "Formato de fecha inválido (YYYY-MM-DD)"}), 400
        
        start_dt = datetime.combine(target_date, datetime.min.time())
        end_dt = start_dt + timedelta(days=1)
        
        # Obtener grabaciones del día
        repo = get_recording_repo()
        recordings = repo.get_by_date_range(camera_id, start_dt, end_dt)
        
        # Formatear como segmentos
        segments = []
        for rec in recordings:
            segments.append({
                "recording_id": rec.id,
                "start": rec.start_time.isoformat(),
                "end": rec.end_time.isoformat() if rec.end_time else rec.start_time.isoformat(),
                "duration_seconds": rec.duration_seconds,
                "file_size_mb": rec.file_size_bytes / (1024*1024),
                "has_clip": rec.clip_path is not None
            })
        
        return jsonify({
            "success": True,
            "data": {
                "date": date_str,
                "camera_id": camera_id,
                "segments": segments,
                "total_segments": len(segments)
            }
        }), 200
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@recordings_bp.route("/play/<int:recording_id>", methods=["GET"])
@jwt_required()
def play_recording(recording_id):
    """
    Streaming de video con soporte Range Requests (seek).
    """
    try:
        user_id = int(get_jwt_identity())
        repo = get_recording_repo()
        recording = repo.get_by_id(recording_id)
        
        if not recording:
            return jsonify({"success": False, "error": "Grabación no encontrada"}), 404
        
        # Verificar permisos
        if not permission_service.check_permission(user_id, recording.camera_id, 'view'):
            return jsonify({"success": False, "error": "Permiso denegado"}), 403
        
        if not recording.file_path or not os.path.exists(recording.file_path):
            return jsonify({"success": False, "error": "Archivo no encontrado en disco"}), 404
        
        file_path = recording.file_path
        file_size = os.path.getsize(file_path)
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "video/mp4"
        
        # Manejar Range Requests (para seek)
        range_header = request.headers.get('Range', None)
        
        if range_header:
            # Parsear Range: bytes=start-end
            try:
                byte_range = range_header.replace('bytes=', '').split('-')
                start = int(byte_range[0]) if byte_range[0] else 0
                end = int(byte_range[1]) if byte_range[1] else file_size - 1
            except:
                start = 0
                end = file_size - 1
            
            length = end - start + 1
            
            def generate():
                with open(file_path, 'rb') as f:
                    f.seek(start)
                    while length > 0:
                        chunk_size = min(8192, length)
                        data = f.read(chunk_size)
                        if not data:
                            break
                        length -= len(data)
                        yield data
            
            response = Response(
                generate(),
                206,  # Partial Content
                mimetype=mime_type,
                direct_passthrough=True
            )
            response.headers.add('Content-Range', f'bytes {start}-{end}/{file_size}')
            response.headers.add('Accept-Ranges', 'bytes')
            response.headers.add('Content-Length', str(length))
            return response
        
        else:
            # Full file
            return send_file(
                file_path,
                mimetype=mime_type,
                as_attachment=False,
                conditional=True
            )
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@recordings_bp.route("/download/<int:recording_id>", methods=["GET"])
@jwt_required()
def download_recording(recording_id):
    """Descarga de grabación."""
    try:
        user_id = int(get_jwt_identity())
        repo = get_recording_repo()
        recording = repo.get_by_id(recording_id)
        
        if not recording:
            return jsonify({"success": False, "error": "Grabación no encontrada"}), 404
        
        # Verificar permiso de descarga
        if not permission_service.check_permission(user_id, recording.camera_id, 'download'):
            return jsonify({"success": False, "error": "Permiso de descarga denegado"}), 403
        
        if not recording.file_path or not os.path.exists(recording.file_path):
            return jsonify({"success": False, "error": "Archivo no encontrado"}), 404
        
        return send_file(
            recording.file_path,
            mimetype="video/mp4",
            as_attachment=True,
            download_name=os.path.basename(recording.file_path)
        )
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@recordings_bp.route("/<int:recording_id>", methods=["DELETE"])
@jwt_required()
def delete_recording(recording_id):
    """Elimina grabación (solo admin o owner)."""
    try:
        from backend.app.services.user_service import UserService
        user_id = int(get_jwt_identity())
        
        if not UserService().is_admin(user_id):
            return jsonify({"success": False, "error": "Admin requerido"}), 403
        
        repo = get_recording_repo()
        recording = repo.get_by_id(recording_id)
        
        if not recording:
            return jsonify({"success": False, "error": "No encontrado"}), 404
        
        # Eliminar archivo físico
        if recording.file_path and os.path.exists(recording.file_path):
            try:
                os.remove(recording.file_path)
            except Exception as e:
                return jsonify({"success": False, "error": f"No se pudo eliminar archivo: {e}"}), 500
        
        repo.delete(recording_id)
        return jsonify({"success": True}), 200
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    
from backend.app.streaming.hls_service import hls_service


@recordings_bp.route("/<int:recording_id>/hls/index.m3u8", methods=["GET"])
@jwt_required()
def get_hls_manifest(recording_id):
    """Manifest HLS para streaming adaptativo."""
    try:
        user_id = int(get_jwt_identity())
        repo = get_recording_repo()
        recording = repo.get_by_id(recording_id)
        
        if not recording:
            return jsonify({"success": False, "error": "Grabación no encontrada"}), 404
        
        if not permission_service.check_permission(user_id, recording.camera_id, 'view'):
            return jsonify({"success": False, "error": "Permiso denegado"}), 403
        
        if not recording.file_path or not os.path.exists(recording.file_path):
            return jsonify({"success": False, "error": "Archivo no encontrado"}), 404
        
        manifest_path = hls_service.get_hls_manifest(recording_id, recording.file_path)
        
        if not manifest_path:
            # 202 = Accepted, procesando en background
            return jsonify({
                "success": False, 
                "error": "Generando stream HLS, intente en unos segundos"
            }), 202
        
        return send_file(
            manifest_path,
            mimetype="application/vnd.apple.mpegurl",
            as_attachment=False
        )
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@recordings_bp.route("/<int:recording_id>/hls/<path:filename>", methods=["GET"])
@jwt_required()
def get_hls_segment(recording_id, filename):
    """Segmentos TS del stream HLS."""
    try:
        user_id = int(get_jwt_identity())
        repo = get_recording_repo()
        recording = repo.get_by_id(recording_id)
        
        if not recording:
            return jsonify({"success": False, "error": "Grabación no encontrada"}), 404
        
        if not permission_service.check_permission(user_id, recording.camera_id, 'view'):
            return jsonify({"success": False, "error": "Permiso denegado"}), 403
        
        segment_path = hls_service.get_segment(recording_id, filename)
        
        if not segment_path:
            return jsonify({"success": False, "error": "Segmento no encontrado"}), 404
        
        return send_file(
            segment_path,
            mimetype="video/MP2T",
            as_attachment=False
        )
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500