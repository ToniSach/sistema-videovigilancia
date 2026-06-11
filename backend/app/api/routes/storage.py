"""
MÓDULO: api.routes.storage — Gestión del almacenamiento de grabaciones (HTTP).

PROPÓSITO
    Endpoints para consultar el uso de disco/grabaciones, cambiar la ruta de
    almacenamiento (persistida y aplicada en caliente) y disparar una limpieza
    manual por cuota. Soporta el Pipeline #11 (Grabación) desde el lado de
    administración del espacio.

RESPONSABILIDAD
    Validar admin donde corresponde, blindar contra path-traversal al cambiar la
    ruta (lista de rutas de sistema prohibidas multiplataforma) y delegar la
    limpieza en StorageManager.

DEPENDENCIAS
    config.settings (RECORDINGS_PATH) · services.user_service (is_admin) ·
    recording.storage_manager (run_cleanup) · database.models.SystemConfig
    (persistir la ruta nueva) · recording_repository.

PUNTO DE ENTRADA / ENDPOINTS
    Blueprint `storage_bp` (url_prefix=/api/v1/storage), registrado en main.
      GET  /info     uso de disco + grabaciones
      POST /config   cambia y persiste RECORDINGS_PATH (solo admin)
      POST /cleanup  ejecuta rotación por cuota manualmente (solo admin)
"""
import os
from pathlib import Path
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required

from backend.app.config import settings
from backend.app.services.user_service import UserService

storage_bp = Blueprint("storage", __name__, url_prefix="/api/v1/storage")


@storage_bp.route("/info", methods=["GET"])
@jwt_required()
def get_storage_info():
    """Obtiene información de almacenamiento."""
    try:
        import shutil
        path = settings.RECORDINGS_PATH
        
        # Espacio total y libre del disco
        total, used, free = shutil.disk_usage(path)
        
        # Espacio usado específicamente por grabaciones
        recordings_size = 0
        if os.path.exists(path):
            for dirpath, dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if os.path.exists(fp) and not os.path.islink(fp):
                        recordings_size += os.path.getsize(fp)
        
        return jsonify({
            "success": True,
            "data": {
                "path": path,
                "disk_total_gb": round(total / (1024**3), 2),
                "disk_free_gb": round(free / (1024**3), 2),
                "disk_used_gb": round(used / (1024**3), 2),
                "recordings_used_gb": round(recordings_size / (1024**3), 2),
                "percent_used": round((used / total) * 100, 1) if total > 0 else 0
            }
        }), 200
        
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@storage_bp.route("/config", methods=["POST"])
@jwt_required()
def update_storage_config():
    """Actualiza ruta de almacenamiento (solo admin)."""
    try:
        from flask_jwt_extended import get_jwt_identity
        user_id = int(get_jwt_identity())
        
        if not UserService().is_admin(user_id):
            return jsonify({"success": False, "error": "Admin requerido"}), 403
        
        data = request.get_json()
        new_path = data.get("path")
        
        if not new_path:
            return jsonify({"success": False, "error": "Path requerido"}), 400
        
        # VALIDACIÓN PATH TRAVERSAL MULTIPLATAFORMA
        try:
            requested_path = Path(new_path).resolve()
            
            # Rutas absolutamente prohibidas independiente del SO
            forbidden = [
                Path(os.environ.get("SystemRoot", "C:/Windows")),  # Windows
                Path("/etc"), Path("/usr"), Path("/bin"),           # Linux/Mac
                Path("/sys"), Path("/proc"), Path("/boot"),
                Path("/sbin"), Path("/dev"),
            ]
            
            if any(str(requested_path).startswith(str(f)) for f in forbidden):
                return jsonify({"success": False, "error": "Path no permitido"}), 400
            
            # Verificar que se puede crear el directorio
            os.makedirs(requested_path, exist_ok=True)

            # PERSISTIR en BD (SystemConfig) para que sobreviva a reinicios, y
            # aplicar EN CALIENTE (StorageManager/RecordingManager leen settings
            # de forma dinámica, así que la ruta nueva tiene efecto al momento
            # para grabaciones nuevas).
            from backend.app.database.connection import db_manager
            from backend.app.database.models import SystemConfig
            with db_manager.get_session() as session:
                session.merge(SystemConfig(key="recordings_path", value=str(requested_path)))
            settings.RECORDINGS_PATH = str(requested_path)

        except Exception as e:
            return jsonify({"success": False, "error": f"Path inválido: {e}"}), 400

        return jsonify({
            "success": True,
            "message": "Ruta de almacenamiento actualizada y aplicada."
        }), 200
        
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@storage_bp.route("/cleanup", methods=["POST"])
@jwt_required()
def cleanup_storage():
    """Limpia archivos temporales o viejos (solo admin)."""
    try:
        from flask_jwt_extended import get_jwt_identity
        user_id = int(get_jwt_identity())
        
        if not UserService().is_admin(user_id):
            return jsonify({"success": False, "error": "Admin requerido"}), 403
        
        from backend.app.recording.storage_manager import StorageManager
        from backend.app.database.repositories.recording_repository import RecordingRepository
        
        repo = RecordingRepository()
        manager = StorageManager(repo)
        
        deleted_count = manager.run_cleanup()
        
        return jsonify({
            "success": True,
            "deleted_files": deleted_count
        }), 200
        
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500