"""
================================================================================
MÓDULO: api.helpers — Utilidades transversales para los endpoints REST
================================================================================

PROPÓSITO
    Colección de helpers reutilizables que comparten TODAS las rutas de la API
    (transversal a todos los pipelines que pasan por HTTP). Centraliza tres
    patrones que de otro modo se repetirían ~80 veces en `routes/`:
      - construir respuestas de error JSON sin filtrar trazas,
      - exigir permisos (admin / permiso de cámara),
      - resolver servicios del contenedor de inyección de dependencias (DI).

RESPONSABILIDAD PRINCIPAL
    Imponer un contrato uniforme de respuesta y seguridad en la capa HTTP:
      - api_error_response: NUNCA devolver str(e) al cliente (evita filtrar
        stack traces, rutas, nombres de columnas) — el detalle va sólo al log.
      - require_camera_permission / require_admin: punto único de control de
        acceso, en vez de repartir checks de rol por cada endpoint.
      - get_service: localizador del contenedor DI, único punto de acceso.

DEPENDENCIAS
    flask ........................... jsonify / request
    flask_jwt_extended .............. identidad y claims del JWT (rol)
    services.permission_service ..... origen real de require_camera_permission
                                      (aquí sólo se re-exporta)
    container.get_container ......... contenedor DI (repos + servicios)

COMPONENTES RELACIONADOS
    backend/app/api/middleware/audit.py ......... auditoría de acciones
    backend/app/api/middleware/rate_limiter.py .. límites de tasa
    backend/app/services/permission_service.py .. lógica de permisos por cámara

PUNTO DE ENTRADA / CÓMO LO USAN LAS RUTAS
    No tiene arranque propio: es una librería de funciones. Cada archivo en
    `backend/app/api/routes/` importa de aquí lo que necesita y lo aplica como
    decorador (require_admin, require_camera_permission) o lo llama dentro del
    handler (api_error_response en el except, get_service al inicio). Adoptarlo
    es gradual: endpoints nuevos lo usan; los viejos se migran cuando se tocan.
================================================================================
"""
from functools import wraps
import logging

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

logger = logging.getLogger(__name__)


def api_error_response(exc: Exception, status: int = 500, message: str | None = None):
    """
    Devuelve un JSON de error consistente sin filtrar stack traces.

    Propósito:
        Unificar el cuerpo de error de la API ({"success": False, "error": ...})
        garantizando que el cliente NUNCA reciba str(exc) crudo (que puede
        revelar paths, esquema de BD o trazas).

    Inputs:
        exc: excepción capturada (sólo se loguea, no se devuelve al cliente).
        status: código HTTP a devolver (500 por defecto).
        message: mensaje opcional ya saneado para el cliente; si es None se usa
            "Error interno del servidor".

    Outputs:
        tupla (Response JSON, status) lista para `return` desde un endpoint.

    Excepciones:
        No lanza; el detalle completo (con exc_info) va al log.

    Llamado por:
        El bloque `except` de los handlers de `routes/` que quieren responder un
        error controlado sin filtrar la excepción.
    Llama a:
        logger.error (con exc_info) y flask.jsonify.
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

    Propósito:
        Restringir un endpoint a administradores. Centraliza el check de rol
        para no repetir `if claims.get("role") != "admin"` en cada handler.

    Inputs:
        view_func: la función-vista a proteger (se aplica como @require_admin).

    Outputs:
        El wrapper: ejecuta la vista si el rol es admin; si no, devuelve
        403 + {"success": False, "error": "Se requiere rol admin"}.

    Excepciones:
        No lanza; asume que ya corre dentro de un contexto con JWT validado
        (normalmente combinado con @jwt_required()).

    Llamado por:
        Endpoints de administración en `routes/` (p. ej. gestión de usuarios).
    Llama a:
        flask_jwt_extended.get_jwt (lee los claims del token).
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
    Resuelve un servicio del contenedor de inyección de dependencias.

    Propósito:
        Punto único para obtener servicios/repos cableados en el contenedor DI,
        en lugar de importarlos e instanciarlos sueltos en cada ruta.

    Inputs:
        name: clave con la que el servicio está registrado en el contenedor.

    Outputs:
        La instancia del servicio (singleton gestionado por el contenedor).

    Excepciones:
        ValueError si la clave no está registrada. Es un bug del programador
        (no del usuario): un nombre mal escrito o un servicio no cableado.

    Llamado por:
        Handlers de `routes/` al inicio, para obtener su servicio de negocio.
    Llama a:
        container.get_container().get(name).
    """
    from backend.app.container import get_container
    svc = get_container().get(name)
    if svc is None:
        raise ValueError(f"Servicio '{name}' no registrado en el contenedor")
    return svc
