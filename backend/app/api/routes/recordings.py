import os
from flask import Blueprint, request, jsonify, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity

from ...database.repositories.recording_repository import RecordingRepository
from ...database.connection import db_manager


recordings_bp = Blueprint("recordings", __name__, url_prefix="/api/v1/recordings")


def get_recording_repo():
    return RecordingRepository(db_manager)


@recordings_bp.route("/", methods=["GET"])
@jwt_required()
def get_recordings():
    try:
        camera_id = request.args.get("camera_id", type=int)
        limit = request.args.get("limit", default=100, type=int)

        repo = get_recording_repo()

        if camera_id:
            recordings = repo.get_by_camera(camera_id, limit=limit)
        else:
            recordings = repo.get_oldest(count=limit)

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


@recordings_bp.route("/<int:recording_id>", methods=["GET"])
@jwt_required()
def get_recording(recording_id):
    try:
        repo = get_recording_repo()
        recording = repo.get_by_id(recording_id)

        if not recording:
            return jsonify({
                "success": False,
                "error": "Grabación no encontrada"
            }), 404

        return jsonify({
            "success": True,
            "data": recording.to_dict()
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@recordings_bp.route("/<int:recording_id>/download", methods=["GET"])
@jwt_required()
def download_recording(recording_id):
    try:
        repo = get_recording_repo()
        recording = repo.get_by_id(recording_id)

        if not recording:
            return jsonify({
                "success": False,
                "error": "Grabación no encontrada"
            }), 404

        if not recording.file_path or not os.path.exists(recording.file_path):
            return jsonify({
                "success": False,
                "error": "Archivo no encontrado en disco"
            }), 404

        return send_file(
            recording.file_path,
            mimetype="video/mp4",
            as_attachment=True,
            download_name=os.path.basename(recording.file_path)
        )

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@recordings_bp.route("/<int:recording_id>", methods=["DELETE"])
@jwt_required()
def delete_recording(recording_id):
    try:
        repo = get_recording_repo()
        recording = repo.get_by_id(recording_id)

        if not recording:
            return jsonify({
                "success": False,
                "error": "Grabación no encontrada"
            }), 404

        if recording.file_path and os.path.exists(recording.file_path):
            try:
                os.remove(recording.file_path)
            except Exception as e:
                return jsonify({
                    "success": False,
                    "error": f"No se pudo eliminar archivo: {e}"
                }), 500

        success = repo.delete(recording_id)

        if success:
            return jsonify({
                "success": True,
                "message": "Grabación eliminada"
            }), 204
        else:
            return jsonify({
                "success": False,
                "error": "No se pudo eliminar de la base de datos"
            }), 500

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@recordings_bp.route("/storage/stats", methods=["GET"])
@jwt_required()
def storage_stats():
    try:
        from ...recording.storage_manager import StorageManager
        repo = get_recording_repo()
        manager = StorageManager(repo)

        stats = manager.get_stats()

        return jsonify({
            "success": True,
            "data": stats
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
