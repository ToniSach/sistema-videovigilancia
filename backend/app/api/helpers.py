"""
Helpers reutilizables para endpoints REST.

Estos helpers existen para eliminar duplicación de patrones repetidos
~80 veces en routes/ y para hacer cumplir buenas prácticas de seguridad:

- api_error_response: nunca devolver str(e) al cliente (filtra stack traces).
- require_camera_permission: decorator que centraliza el check de permisos.
- get_service: localizador con cache implícito (1 lookup por blueprint).

Compatible con código existente. Adoptar gradualmente en endpoints nuevos
y luego refactorizar los viejos en la Fase 2 de la auditoría.
"""
from functools import wraps
import logging

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

logger = logging.getLogger(__name__)


def api_error_response(exc: Exception, status: int = 500, message: str | None = None):
    """
    Devuelve un JSON de error consistente sin filtrar stack traces.

    El detalle completo (con exc_info) va al log; el cliente recibe sólo un
    mensaje genérico o el provisto explícitamente.
    """
    logger.error(f"API error {status}: {exc}", exc_info=True)
    return jsonify({
        "success": False,
        "error": message or "Error interno del servidor",
    }), status


# Re-exportamos el decorator existente en PermissionService para tener un
# único punto de import. Evita duplicar lógica.
from backend.app.services.permission_service import require_camera_permission  # noqa: F401, E402


def require_admin(view_func):
    """
    Decorator que exige que el JWT tenga claim role=admin.
    """
    from flask_jwt_extended import get_jwt

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        claims = get_jwt() or {}
        if claims.get("role") != "admin":
            return jsonify({
                "success": False,
                "error": "Se requiere rol admin"
            }), 403
        return view_func(*args, **kwargs)
    return wrapper


def get_service(name: str):
    """
    Resuelve un servicio del DI container. Lanza ValueError si no existe
    (es bug del programador, no del usuario).
    """
    from backend.app.container import get_container
    svc = get_container().get(name)
    if svc is None:
        raise ValueError(f"Servicio '{name}' no registrado en el contenedor")
    return svc
