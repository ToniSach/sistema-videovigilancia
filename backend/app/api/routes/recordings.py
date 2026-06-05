"""
API Endpoints para grabaciones con timeline, playback streaming y descarga.
"""
import os
import logging
import mimetypes
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, send_file, Response
from flask_jwt_extended import jwt_required, get_jwt_identity

from backend.app.services.permission_service import PermissionService
from backend.app.database.repositories.recording_repository import RecordingRepository
from backend.app.services.signed_url_service import get_signed_url_service
from backend.app.config import settings

logger = logging.getLogger(__name__)
recordings_bp = Blueprint("recordings", __name__, url_prefix="/api/v1/recordings")
permission_service = PermissionService()


def get_recording_repo():
    """Repositorio sin parámetro de db_manager."""
    return RecordingRepository()  # ← CORREGIDO: sin parámetros


# ---------------------------------------------------------------------------
# URLs firmadas de medios (para reproductores que NO envían Authorization)
# ---------------------------------------------------------------------------
# PIPELINE de reproducción segura desde móvil/desktop:
#   Paso 1. El cliente (autenticado por JWT) pide GET /recordings/<id>/playback-url
#           → el servidor valida permiso 'view' y devuelve una URL FIRMADA.
#   Paso 2. El cliente entrega esa URL a ExoPlayer/AVPlayer/VLC.
#   Paso 3. El reproductor pide GET /recordings/<id>/media?token=... (sin JWT).
#   Paso 4. El servidor valida el token (HMAC + caducidad, ligado a ESTE id) y
#           sirve el MP4 con soporte Range (seek). Sin token válido → 403.
def _media_resource(recording_id: int) -> str:
    """Recurso canónico que firma/valida el token de vídeo de una grabación."""
    return f"recording:{recording_id}"


def _thumb_resource(recording_id: int) -> str:
    """Recurso canónico del thumbnail de una grabación."""
    return f"thumb:{recording_id}"


def _signed_playback_url(recording_id: int) -> str:
    svc = get_signed_url_service()
    return f"/api/v1/recordings/{recording_id}/media?{svc.query_param(_media_resource(recording_id))}"


def _signed_thumbnail_url(recording_id: int) -> str:
    svc = get_signed_url_service()
    return f"/api/v1/recordings/{recording_id}/thumbnail?{svc.query_param(_thumb_resource(recording_id))}"


def _thumbnail_path_for(file_path: str) -> str:
    """El thumbnail vive junto al MP4 con extensión .jpg (lo genera RecordingManager)."""
    return os.path.splitext(file_path or "")[0] + ".jpg"


def _serve_file_with_range(file_path: str, mime_type: str):
    """
    Sirve un archivo con soporte de Range Requests (seek). Reutilizado por los
    endpoints de medios firmados. Devuelve 206 si hay Range, 200 si no.
    """
    file_size = os.path.getsize(file_path)
    range_header = request.headers.get("Range", None)
    if range_header:
        try:
            byte_range = range_header.replace("bytes=", "").split("-")
            start = int(byte_range[0]) if byte_range[0] else 0
            end = int(byte_range[1]) if byte_range[1] else file_size - 1
        except Exception:
            start, end = 0, file_size - 1
        length = end - start + 1

        def generate(initial_length=length):
            remaining = initial_length
            with open(file_path, "rb") as f:
                f.seek(start)
                while remaining > 0:
                    data = f.read(min(8192, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        response = Response(generate(), 206, mimetype=mime_type, direct_passthrough=True)
        response.headers.add("Content-Range", f"bytes {start}-{end}/{file_size}")
        response.headers.add("Accept-Ranges", "bytes")
        response.headers.add("Content-Length", str(length))
        return response
    return send_file(file_path, mimetype=mime_type, as_attachment=False, conditional=True)


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

        # Enriquecer cada grabación con URLs FIRMADAS listas para reproductores
        # nativos (no necesitan cabecera Authorization) + thumbnail si existe.
        data = []
        for r in recordings:
            d = r.to_dict()
            rid = d.get("id")
            if rid is not None:
                d["playback_url"] = _signed_playback_url(rid)
                if r.file_path and os.path.exists(_thumbnail_path_for(r.file_path)):
                    d["thumbnail_url"] = _signed_thumbnail_url(rid)
            data.append(d)

        return jsonify({
            "success": True,
            "data": data,
            "count": len(recordings)
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@recordings_bp.route("/<int:recording_id>", methods=["GET"])
@jwt_required()
def get_recording_detail(recording_id):
    """
    Detalle de UNA grabación con URLs firmadas (la app móvil lo usa antes de
    reproducir: PlaybackFragment necesita playback_url). Antes NO existía esta
    ruta GET → getRecording(id) daba 404 y la reproducción móvil fallaba.
    """
    try:
        user_id = int(get_jwt_identity())
        repo = get_recording_repo()
        recording = repo.get_by_id(recording_id)
        if not recording:
            return jsonify({"success": False, "error": "Grabación no encontrada"}), 404
        if not permission_service.check_permission(user_id, recording.camera_id, "view"):
            return jsonify({"success": False, "error": "Permiso denegado"}), 403

        d = recording.to_dict()
        d["playback_url"] = _signed_playback_url(recording_id)
        if recording.file_path and os.path.exists(_thumbnail_path_for(recording.file_path)):
            d["thumbnail_url"] = _signed_thumbnail_url(recording_id)
        return jsonify({"success": True, "data": d}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@recordings_bp.route("/<int:recording_id>/playback-url", methods=["GET"])
@jwt_required()
def get_playback_url(recording_id):
    """
    Paso 1 del pipeline de reproducción segura. Valida permiso 'view' y devuelve
    una URL FIRMADA (con caducidad) que el cliente entrega a su reproductor.
    """
    try:
        user_id = int(get_jwt_identity())
        repo = get_recording_repo()
        recording = repo.get_by_id(recording_id)
        if not recording:
            return jsonify({"success": False, "error": "Grabación no encontrada"}), 404
        if not permission_service.check_permission(user_id, recording.camera_id, "view"):
            return jsonify({"success": False, "error": "Permiso denegado"}), 403

        resp = {
            "success": True,
            "data": {
                "playback_url": _signed_playback_url(recording_id),
                "expires_in": settings.MEDIA_URL_TTL_SECONDS,
            },
        }
        if recording.file_path and os.path.exists(_thumbnail_path_for(recording.file_path)):
            resp["data"]["thumbnail_url"] = _signed_thumbnail_url(recording_id)
        return jsonify(resp), 200
    except Exception:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@recordings_bp.route("/<int:recording_id>/media", methods=["GET"])
def get_signed_media(recording_id):
    """
    Pasos 3-4. Endpoint SIN @jwt_required: la autorización va en el token firmado
    del query string (los reproductores nativos no envían Authorization). Valida
    HMAC + caducidad ligados a ESTE recording_id y sirve el MP4 con Range.
    """
    try:
        token = request.args.get("token")
        if not get_signed_url_service().verify(_media_resource(recording_id), token):
            return jsonify({"success": False, "error": "Token de medios inválido o caducado"}), 403

        repo = get_recording_repo()
        recording = repo.get_by_id(recording_id)
        if not recording:
            return jsonify({"success": False, "error": "Grabación no encontrada"}), 404

        file_path = recording.file_path or ""
        if file_path and not os.path.isabs(file_path):
            file_path = os.path.abspath(file_path)
        if not file_path or not os.path.exists(file_path) or os.path.getsize(file_path) < 1024:
            return jsonify({"success": False, "error": "Archivo no disponible"}), 404

        mime_type, _ = mimetypes.guess_type(file_path)
        return _serve_file_with_range(file_path, mime_type or "video/mp4")
    except Exception:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@recordings_bp.route("/<int:recording_id>/thumbnail", methods=["GET"])
def get_signed_thumbnail(recording_id):
    """
    Sirve el thumbnail JPEG de una grabación. Acepta token firmado (query) para
    reproductores/listas, O JWT en cabecera para clientes que lo manden.
    """
    try:
        token = request.args.get("token")
        authorized = get_signed_url_service().verify(_thumb_resource(recording_id), token)
        if not authorized:
            # Fallback a JWT + permiso (clientes que sí mandan cabecera).
            from flask_jwt_extended import verify_jwt_in_request
            try:
                verify_jwt_in_request()
                user_id = int(get_jwt_identity())
                rec = get_recording_repo().get_by_id(recording_id)
                authorized = bool(
                    rec and permission_service.check_permission(user_id, rec.camera_id, "view")
                )
            except Exception:
                authorized = False
        if not authorized:
            return jsonify({"success": False, "error": "No autorizado"}), 403

        recording = get_recording_repo().get_by_id(recording_id)
        if not recording or not recording.file_path:
            return jsonify({"success": False, "error": "No encontrado"}), 404
        thumb = _thumbnail_path_for(recording.file_path)
        if not os.path.exists(thumb):
            return jsonify({"success": False, "error": "Sin miniatura"}), 404
        return send_file(thumb, mimetype="image/jpeg", as_attachment=False)
    except Exception:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


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
        
        # Formatear como segmentos. `has_clip` indica si es grabación de
        # evento (carpeta /events/) vs continua (carpeta /continuous/);
        # antes leía rec.clip_path que NO existe en Recording → AttributeError
        # → 500 → la vista de Reproducción aparecía vacía.
        segments = []
        for rec in recordings:
            file_path_lower = (rec.file_path or "").lower()
            is_event_clip = "events" in file_path_lower.replace("\\", "/").split("/")
            segments.append({
                "recording_id": rec.id,
                "start": rec.start_time.isoformat() if rec.start_time else None,
                "end": rec.end_time.isoformat() if rec.end_time else (
                    rec.start_time.isoformat() if rec.start_time else None
                ),
                # duration_seconds es Float en BD, pero la app móvil lo espera
                # como entero (Gson revienta al parsear un decimal en un Int →
                # "No se pudo cargar el timeline"). Lo devolvemos como int.
                "duration_seconds": int(rec.duration_seconds or 0),
                "file_size_mb": round((rec.file_size_bytes or 0) / (1024*1024), 2),
                "has_clip": True,  # todas las grabaciones tienen archivo
                "type": "event" if is_event_clip else "continuous",
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
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


def _lens_cropped_path(src_path: str, recording_id: int, lens: str) -> "str | None":
    """
    Devuelve la ruta de un MP4 que contiene SOLO el lente pedido, recortado del
    vídeo combinado (dual-lens apilado arriba/abajo) y transcodificado+escalado
    a 720p (rápido y ligero). Se CACHEA por (recording_id, lens) para que las
    re-reproducciones sean instantáneas.
      - l1 = mitad inferior (crop=iw:ih/2:0:ih/2)
      - l2 = mitad superior (crop=iw:ih/2:0:0)
    NO se rota (el lente inferior sale boca abajo, decisión del usuario).
    Devuelve None si falla (el caller sirve el combinado).
    """
    if lens not in ("l1", "l2"):
        return None
    import tempfile
    import subprocess
    cache_dir = os.path.join(tempfile.gettempdir(), "nvr_lens_cache")
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError:
        return None
    out = os.path.join(cache_dir, f"rec{recording_id}_{lens}.mp4")
    # Cache válido si existe, no está vacío y es más nuevo que el origen.
    try:
        if (os.path.exists(out) and os.path.getsize(out) > 1024
                and os.path.getmtime(out) >= os.path.getmtime(src_path)):
            return out
    except OSError:
        pass
    crop = "crop=iw:ih/2:0:ih/2" if lens == "l1" else "crop=iw:ih/2:0:0"
    vf = f"{crop},scale=-2:720"  # recorte + escala a 720p para encode veloz
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", src_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
        "-an", "-movflags", "+faststart",
        out,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180)
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 1024:
            _prune_lens_cache(cache_dir, max_files=40)
            return out
        logger.warning(
            f"[PLAY] recorte lente {lens} rec={recording_id} falló: "
            f"{(r.stderr or b'')[:200]!r}"
        )
    except Exception as e:
        logger.warning(f"[PLAY] recorte lente {lens} rec={recording_id} error: {e}")
    return None


def _prune_lens_cache(cache_dir: str, max_files: int = 40) -> None:
    """Mantiene el caché de lentes recortados acotado: deja como mucho
    `max_files` (los más recientes) y borra los más antiguos. Evita que el
    tempdir crezca sin límite si se reproducen muchos segmentos×lentes."""
    try:
        files = [
            os.path.join(cache_dir, f) for f in os.listdir(cache_dir)
            if f.endswith(".mp4")
        ]
        if len(files) <= max_files:
            return
        files.sort(key=lambda p: os.path.getmtime(p))  # más antiguo primero
        for p in files[:len(files) - max_files]:
            try:
                os.remove(p)
            except OSError:
                pass
    except Exception:
        pass


@recordings_bp.route("/play/<int:recording_id>", methods=["GET"])
@jwt_required()
def play_recording(recording_id):
    """
    Streaming de video con soporte Range Requests (seek).

    Query opcional `?lens=l1|l2`: para cámaras dual-lens, sirve SOLO ese lente
    recortado del combinado (transcodificado y cacheado). Sin `lens` (o full)
    sirve el archivo combinado tal cual.
    """
    try:
        user_id = int(get_jwt_identity())
        repo = get_recording_repo()
        recording = repo.get_by_id(recording_id)

        if not recording:
            logger.warning(f"[PLAY] recording_id={recording_id} no existe en BD")
            return jsonify({"success": False, "error": "Grabación no encontrada"}), 404

        # Verificar permisos
        if not permission_service.check_permission(user_id, recording.camera_id, 'view'):
            logger.warning(
                f"[PLAY] user={user_id} sin permiso 'view' en cam {recording.camera_id}"
            )
            return jsonify({"success": False, "error": "Permiso denegado"}), 403

        # Resolver path: si es relativo, hacerlo absoluto desde el CWD del backend.
        file_path = recording.file_path or ""
        if file_path and not os.path.isabs(file_path):
            file_path = os.path.abspath(file_path)

        if not file_path or not os.path.exists(file_path):
            logger.error(
                f"[PLAY] archivo no existe en disco: recording_id={recording_id} "
                f"file_path={recording.file_path!r} resolved={file_path!r} "
                f"cwd={os.getcwd()!r}"
            )
            return jsonify({
                "success": False,
                "error": "Archivo no encontrado en disco",
                "file_path_db": recording.file_path,
            }), 404

        # Recorte de lente server-side (opción B): si se pide ?lens=l1|l2,
        # servir SOLO ese lente recortado del combinado. El desktop solo lo
        # pide para cámaras dual-lens. Cacheado para re-reproducciones.
        lens = (request.args.get("lens") or "").strip().lower()
        if lens in ("l1", "l2"):
            cropped = _lens_cropped_path(file_path, recording_id, lens)
            if cropped:
                file_path = cropped
                mimetypes.add_type("video/mp4", ".mp4")

        file_size = os.path.getsize(file_path)
        if file_size < 1024:
            logger.error(
                f"[PLAY] archivo demasiado pequeño ({file_size}B), probablemente "
                f"corrupto: {file_path}"
            )
            return jsonify({
                "success": False,
                "error": f"Archivo corrupto o vacío ({file_size} bytes)"
            }), 422

        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "video/mp4"

        logger.info(
            f"[PLAY] rec={recording_id} cam={recording.camera_id} "
            f"size={file_size} mime={mime_type} range={request.headers.get('Range', 'none')}"
        )
        
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

            # BUG FIX: usar variable local distinta dentro del generador.
            # Antes `length -= len(data)` hacía a `length` local de generate()
            # y el primer `while length > 0` lanzaba UnboundLocalError →
            # cliente recibía 500 al intentar reproducir con Range header.
            def generate(initial_length=length):
                remaining = initial_length
                with open(file_path, 'rb') as f:
                    f.seek(start)
                    while remaining > 0:
                        chunk_size = min(8192, remaining)
                        data = f.read(chunk_size)
                        if not data:
                            break
                        remaining -= len(data)
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
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


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
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


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
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500
    
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
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


# ---------------------------------------------------------------------------
# Control manual de grabación continua (start/stop por cámara)
# ---------------------------------------------------------------------------
def _get_recording_manager():
    from backend.app.container import get_container
    mgr = get_container().get("recording_manager")
    if mgr is None:
        raise RuntimeError("RecordingManager no disponible en el contenedor")
    return mgr


@recordings_bp.route("/manual/start/<int:camera_id>", methods=["POST"])
@jwt_required()
def start_manual_recording(camera_id: int):
    """Inicia grabación continua de la cámara hasta que se detenga."""
    try:
        user_id = int(get_jwt_identity())
        if not permission_service.check_permission(user_id, camera_id, 'view'):
            return jsonify({"success": False, "error": "Permiso denegado"}), 403

        mgr = _get_recording_manager()
        ok = mgr.start_continuous_recording(camera_id)
        if not ok:
            return jsonify({
                "success": False,
                "error": "Ya hay una grabación continua activa para esta cámara"
            }), 409
        return jsonify({"success": True, "data": {"camera_id": camera_id, "recording": True}}), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@recordings_bp.route("/manual/stop/<int:camera_id>", methods=["POST"])
@jwt_required()
def stop_manual_recording(camera_id: int):
    """Detiene la grabación continua de la cámara."""
    try:
        user_id = int(get_jwt_identity())
        if not permission_service.check_permission(user_id, camera_id, 'view'):
            return jsonify({"success": False, "error": "Permiso denegado"}), 403

        mgr = _get_recording_manager()
        ok = mgr.stop_continuous_recording(camera_id)
        if not ok:
            return jsonify({
                "success": False,
                "error": "No había grabación activa"
            }), 404
        return jsonify({"success": True, "data": {"camera_id": camera_id, "recording": False}}), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@recordings_bp.route("/manual/status/<int:camera_id>", methods=["GET"])
@jwt_required()
def manual_recording_status(camera_id: int):
    try:
        user_id = int(get_jwt_identity())
        if not permission_service.check_permission(user_id, camera_id, 'view'):
            return jsonify({"success": False, "error": "Permiso denegado"}), 403

        mgr = _get_recording_manager()
        return jsonify({
            "success": True,
            "data": {
                "camera_id": camera_id,
                "recording": mgr.is_recording_continuous(camera_id),
            }
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


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
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500