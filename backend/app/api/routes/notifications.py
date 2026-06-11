"""
================================================================================
MÓDULO: api.routes.notifications — Preferencias de notificación por usuario
================================================================================

PROPÓSITO
    Blueprint REST para que cada usuario administre SUS reglas de notificación:
    por qué tipo de evento (person/vehicle/motion/camera_offline),
    para qué cámara (o todas), por qué canales (push/telegram/...), en qué
    ventana horaria y qué días de la semana quiere ser avisado.

RESPONSABILIDAD
    SOLO contrato HTTP: validar el body, PARSEAR los horarios "HH:MM" → time
    (crítico: sin esto se guardaba un string en una columna Time y la ventana
    horaria nunca se evaluaba bien) y exigir ownership de la preferencia antes
    de editar/borrar. La persistencia vive en NotificationPreferenceService.

DEPENDENCIAS
    services.notification_preference_service.NotificationPreferenceService
    flask_jwt_extended ... identidad + @jwt_required
    datetime ............. parseo de horarios "HH:MM" → time

COMPONENTES RELACIONADOS
    NotificationRouter ... consume estas preferencias al enrutar un EventData
                           (decide a quién/por qué canal notificar) vía la fuente
                           ÚNICA notifications/preference_eval.wanted_channels.
    Canales de salida ...  TelegramNotifier (canal 'telegram') y WSNotification
                           Broker (canal 'app' por WebSocket en la LAN; sin FCM,
                           que fue eliminado del proyecto).
    routes/telegram_link.py, routes/devices.py  alta de destinos (chat / dispositivo).
    database.models.NotificationPreference (+ channels, days)  modelo ORM.

PUNTO DE ENTRADA
    Registrado en main.create_app() vía safe_register(notifications_bp).
    Prefijo: /api/v1/notifications. Best-effort.

PIPELINE(S)
    Pipeline #13 (Notificaciones) — etapa de CONFIGURACIÓN de preferencias.
    El disparo real (Evento → router → canal) lo ejecutan EventManager y los
    notifiers; aquí solo se definen las reglas que ese pipeline consulta.

ENDPOINTS
    GET    /preferences            → get_preferences()    lista las del usuario
    POST   /preferences            → create_preference()  crea una regla
    PUT    /preferences/<pref_id>  → update_preference()  edita (con ownership)
    DELETE /preferences/<pref_id>  → delete_preference()  borra (con ownership)
================================================================================
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt

from backend.app.services.notification_preference_service import NotificationPreferenceService

notifications_bp = Blueprint("notifications", __name__, url_prefix="/api/v1/notifications")
pref_service = NotificationPreferenceService()


def _device_id_from_jwt():
    """
    device_id del JWT (las preferencias in-app/Telegram son POR DISPOSITIVO).
    El token móvil lo lleva; el de escritorio no → None (alcance "de cuenta").
    """
    dev = get_jwt().get("device_id")
    return int(dev) if dev is not None else None


def _parse_hhmm(value):
    """
    Parsea un horario "HH:MM" → datetime.time, tolerando hora sin cero a la
    izquierda ("9:00" → 09:00). Devuelve None si value es None/"" (sin ventana).
    Lanza ValueError con mensaje claro si el formato es inválido (el llamador lo
    traduce a HTTP 400 en vez del 500 críptico que daba antes).
    """
    from datetime import datetime
    if value is None or value == "":
        return None
    try:
        return datetime.strptime(str(value).strip(), "%H:%M").time()
    except ValueError:
        raise ValueError(
            f"Horario inválido: '{value}'. Usa el formato HH:MM (00:00–23:59)."
        )


@notifications_bp.route("/preferences", methods=["GET"])
@jwt_required()
def get_preferences():
    """
    Lista las preferencias de notificación del usuario autenticado.

    Método+Ruta: GET /api/v1/notifications/preferences
    Permiso: JWT válido (cualquier rol). Solo devuelve las del propio usuario.
    Inputs: ninguno.
    Outputs:
        200 {success:true, data:[{id, event_type, camera_id, enabled, channels[],
             schedule:{start,end ISO}, schedule_start/"HH:MM", schedule_end/"HH:MM",
             days[]}, ...]}
             (schedule se entrega DOS veces: anidado ISO y plano "HH:MM" para
             clientes móviles que lo consumen directo.)
        500 ante fallo interno.
    Llama a: NotificationPreferenceService.get_user_preferences(user_id).
    """
    try:
        user_id = int(get_jwt_identity())
        device_id = _device_id_from_jwt()
        # Solo las preferencias del alcance de ESTE cliente (dispositivo o cuenta).
        prefs = pref_service.get_user_preferences(user_id, device_id, _scope_device=True)

        result = []
        for p in prefs:
            channels = [c.channel for c in p.channels]
            days = [d.day_of_week for d in p.days]

            result.append({
                "id": p.id,
                "event_type": p.event_type,
                "camera_id": p.camera_id,
                "device_id": p.device_id,
                "enabled": p.enabled,
                "channels": channels,
                "schedule": {
                    "start": p.schedule_start.isoformat() if p.schedule_start else None,
                    "end": p.schedule_end.isoformat() if p.schedule_end else None
                },
                # Campos planos "HH:MM" para clientes (móvil) que los consumen directos.
                "schedule_start": p.schedule_start.strftime("%H:%M") if p.schedule_start else None,
                "schedule_end": p.schedule_end.strftime("%H:%M") if p.schedule_end else None,
                "days": days
            })
        
        return jsonify({"success": True, "data": result}), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@notifications_bp.route("/preferences", methods=["POST"])
@jwt_required()
def create_preference():
    """
    Crea una regla de notificación para el usuario autenticado.

    Método+Ruta: POST /api/v1/notifications/preferences
    Permiso: JWT válido (cualquier rol). user_id sale del token.
    Inputs (body JSON):
        event_type (str, REQUERIDO; person|vehicle|motion|camera_offline|...),
        camera_id (int|null; null = todas las cámaras),
        enabled (bool, default True),
        channels (list[str], default ["push"]),
        schedule_start / schedule_end ("HH:MM" o null; se parsean a time),
        days_of_week (list[int 0-6], default todos los días).
    Outputs:
        201 {success:true, data:{id}}
        400 {success:false, error} si falta event_type.
        500 ante fallo interno (incluye "HH:MM" malformado).
    Llama a: NotificationPreferenceService.create_preference(...).
    """
    try:
        user_id = int(get_jwt_identity())
        data = request.get_json()
        
        if not data or "event_type" not in data:
            return jsonify({"success": False, "error": "event_type requerido"}), 400

        # Parsear/validar horarios (400 claro si el formato es inválido).
        try:
            schedule_start = _parse_hhmm(data.get("schedule_start"))
            schedule_end = _parse_hhmm(data.get("schedule_end"))
        except ValueError as ve:
            return jsonify({"success": False, "error": str(ve)}), 400

        pref = pref_service.create_preference(
            user_id=user_id,
            event_type=data["event_type"],
            camera_id=data.get("camera_id"),
            enabled=data.get("enabled", True),
            channels=data.get("channels", ["app"]),
            schedule_start=schedule_start,
            schedule_end=schedule_end,
            days_of_week=data.get("days_of_week", [0, 1, 2, 3, 4, 5, 6]),
            device_id=_device_id_from_jwt(),
        )
        
        return jsonify({"success": True, "data": {"id": pref.id}}), 201
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@notifications_bp.route("/preferences/<int:pref_id>", methods=["PUT"])
@jwt_required()
def update_preference(pref_id):
    """
    Actualiza una preferencia existente (solo si pertenece al usuario).

    Método+Ruta: PUT /api/v1/notifications/preferences/<pref_id>
    Permiso: JWT válido + OWNERSHIP (la pref debe estar entre las del usuario;
        si no, 403).
    Inputs:
        Path: pref_id (int).
        Body JSON: campos a modificar (**data al servicio). schedule_start/end,
        si vienen como "HH:MM", se parsean a time ANTES de pasar al servicio
        (ver comentario abajo: evita guardar un string en columna Time).
    Outputs:
        200 {success:true, data:{id}}
        403 {success:false, error:"No autorizado"} si la pref no es del usuario.
        404 {success:false, error} si no existe.
        500 ante fallo interno.
    Llama a: get_user_preferences() (ownership) + update_preference(pref_id, **data).
    """
    try:
        user_id = int(get_jwt_identity())
        data = request.get_json()
        
        # Verificar ownership ACOTADO al alcance del cliente (dispositivo o cuenta):
        # un teléfono solo edita SUS preferencias, no las de otro dispositivo.
        prefs = pref_service.get_user_preferences(
            user_id, _device_id_from_jwt(), _scope_device=True
        )
        if not any(p.id == pref_id for p in prefs):
            return jsonify({"success": False, "error": "No autorizado"}), 403

        # Parsear/validar horarios "HH:MM" → time (igual que en POST). Sin esto,
        # el service hacía setattr(schedule_start, "08:00") guardando un string
        # en una columna Time → la ventana horaria nunca se evaluaba bien.
        try:
            if "schedule_start" in data:
                data["schedule_start"] = _parse_hhmm(data["schedule_start"])
            if "schedule_end" in data:
                data["schedule_end"] = _parse_hhmm(data["schedule_end"])
        except ValueError as ve:
            return jsonify({"success": False, "error": str(ve)}), 400

        pref = pref_service.update_preference(pref_id, **data)
        if not pref:
            return jsonify({"success": False, "error": "No encontrado"}), 404
        
        return jsonify({"success": True, "data": {"id": pref.id}}), 200
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500


@notifications_bp.route("/preferences/<int:pref_id>", methods=["DELETE"])
@jwt_required()
def delete_preference(pref_id):
    """
    Elimina una preferencia (solo si pertenece al usuario).

    Método+Ruta: DELETE /api/v1/notifications/preferences/<pref_id>
    Permiso: JWT válido + OWNERSHIP (si la pref no es del usuario → 403).
    Inputs: Path pref_id (int).
    Outputs:
        200 {success:true}
        403 {success:false, error:"No autorizado"}
        404 {success:false, error} si no existe.
        500 ante fallo interno.
    Llama a: get_user_preferences() (ownership) + delete_preference(pref_id) → bool.
    """
    try:
        user_id = int(get_jwt_identity())
        prefs = pref_service.get_user_preferences(
            user_id, _device_id_from_jwt(), _scope_device=True
        )
        if not any(p.id == pref_id for p in prefs):
            return jsonify({"success": False, "error": "No autorizado"}), 403

        if pref_service.delete_preference(pref_id):
            return jsonify({"success": True}), 200
        return jsonify({"success": False, "error": "No encontrado"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500