"""
================================================================================
MÓDULO: api.middleware.audit — Auditoría de acciones sensibles → AuditLog
================================================================================

PROPÓSITO
    Registrar en la tabla `audit_logs` (modelo AuditLog) las acciones sensibles
    que pasan por la API: quién (user_id del JWT), qué acción, sobre qué recurso,
    detalles, IP y user-agent. Soporte forense / de cumplimiento.

RESPONSABILIDAD PRINCIPAL
    - audit_action: decorador que envuelve un endpoint y persiste una entrada de
      auditoría tanto si tiene éxito como si lanza (registra en finally).
    - log_action_sync: variante imperativa para registrar desde código que no es
      un endpoint (servicios, tareas de fondo).
    - get_client_ip: resuelve la IP real del cliente considerando proxies.

    El registro de auditoría es BEST-EFFORT: si la escritura falla se loguea el
    error pero NUNCA se rompe la petición ni se enmascara la excepción original
    del endpoint (que se re-lanza intacta).

DEPENDENCIAS
    database.connection.db_manager .... sesión de BD para persistir el log
    database.models.AuditLog .......... modelo ORM de la bitácora (audit_logs)
    flask ............................. request / g (contexto de petición)
    flask_jwt_extended ................ identidad del usuario (opcional)

COMPONENTES RELACIONADOS
    backend/app/database/models.py:AuditLog ... tabla destino (inmutable).
    backend/app/api/helpers.py ................ otros helpers de la capa HTTP.
    backend/app/api/middleware/rate_limiter.py  middleware hermano.

PUNTO DE ENTRADA / CÓMO LO USAN LAS RUTAS
    No tiene arranque. Las rutas decoran sus handlers con @audit_action(...)
    (encima de @jwt_required()) para auditar la acción; el código de servicio
    que no es endpoint llama a log_action_sync(...). Es transversal a varios
    pipelines, en especial #2 (Autenticación) y la administración.
================================================================================
"""
import logging
from functools import wraps
from flask import request, g
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
from typing import Callable, Any, Optional

from backend.app.database.connection import db_manager
from backend.app.database.models import AuditLog

logger = logging.getLogger(__name__)


def get_client_ip() -> str:
    """
    Obtiene la IP real del cliente, considerando proxies.

    Propósito:
        Que la auditoría registre la IP de origen real y no la del proxy.

    Inputs:
        Ninguno explícito; lee la request actual (cabecera X-Forwarded-For y
        request.remote_addr).

    Outputs:
        str con la primera IP de X-Forwarded-For si existe; si no, remote_addr;
        'unknown' como último recurso.

    Llamado por:
        audit_action y log_action_sync al construir el AuditLog.
    """
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or 'unknown'


def audit_action(action: str, resource_type: Optional[str] = None, 
                 resource_id: Optional[str] = None, 
                 get_details: Optional[Callable[[], dict]] = None):
    """
    Decorador que audita la ejecución de un endpoint (éxito o fallo).

    Propósito:
        Persistir una entrada en audit_logs cada vez que se invoca el endpoint
        decorado, capturando también el caso en que el handler lanza excepción.

    Inputs:
        action: nombre de la acción (p. ej. "delete_recording", "login").
        resource_type: tipo de recurso afectado (p. ej. "recording", "camera").
        resource_id: identificador del recurso (str u opcional).
        get_details: callable opcional sin argumentos que devuelve un dict con
            detalles extra; se evalúa DENTRO del handler para poder leer
            variables locales/kwargs (closures sobre el endpoint).

    Outputs:
        El decorador; al aplicarse no cambia el valor de retorno del endpoint
        (devuelve su respuesta original tal cual).

    Excepciones:
        Re-lanza intacta cualquier excepción del endpoint tras registrar la
        auditoría (la auditoría va en finally). Los fallos de la propia
        escritura de auditoría se capturan y sólo se loguean (best-effort).

    Llamado por:
        Handlers de routes/ que necesitan dejar rastro de la acción.

    Uso:
        @audit_action("delete_recording", "recording", lambda: {"recording_id": recording_id})
        @jwt_required()
        def delete_recording(recording_id):
            ...
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Ejecutar el endpoint primero, registrando si tuvo éxito o no.
            # La auditoría se escribe en el finally para cubrir ambos casos.
            try:
                response = f(*args, **kwargs)
                success = True
                error_msg = None
            except Exception as e:
                success = False
                error_msg = str(e)
                raise  # Re-lanzar después de registrar
            
            finally:
                # Registrar auditoría (en finally para capturar también excepciones)
                try:
                    user_id = None
                    try:
                        verify_jwt_in_request(optional=True)
                        identity = get_jwt_identity()
                        user_id = int(identity) if identity else None
                    except Exception:
                        pass
                    
                    details = {}
                    if get_details:
                        try:
                            details = get_details()
                        except Exception:
                            pass
                    details['success'] = success
                    if error_msg:
                        details['error'] = error_msg
                    
                    log = AuditLog(
                        user_id=user_id,
                        action=action,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        details=str(details) if details else None,
                        ip_address=get_client_ip(),
                        user_agent=request.headers.get('User-Agent', '')[:500]
                    )
                    with db_manager.get_session() as session:
                        session.add(log)
                    logger.info(f"Audit: {action} by user {user_id} on {resource_type}/{resource_id} - success={success}")
                except Exception as e:
                    # La auditoría es best-effort: si falla la escritura, se
                    # loguea pero no se rompe la petición ni se oculta el error
                    # original del endpoint (que ya se re-lanzó arriba).
                    logger.error(f"Error registrando auditoría: {e}")

            return response
        return decorated_function
    return decorator


def log_action_sync(action: str, user_id: Optional[int] = None,
                    resource_type: Optional[str] = None,
                    resource_id: Optional[str] = None,
                    details: Optional[dict] = None,
                    ip_address: Optional[str] = None,
                    user_agent: Optional[str] = None):
    """
    Registra una acción de auditoría de forma imperativa (no como decorador).

    Propósito:
        Permitir auditar desde código que no es un endpoint HTTP (servicios,
        tareas de fondo, hooks) o cuando ya se tienen todos los datos a mano.

    Inputs:
        action: nombre de la acción (obligatorio).
        user_id, resource_type, resource_id, details: metadatos opcionales.
        ip_address: IP a registrar; si es None, se intenta get_client_ip()
            (sólo válido si hay contexto de petición).
        user_agent: cadena de user-agent; si es None se toma de la request
            actual cuando exista.

    Outputs:
        None — efecto secundario: una fila en audit_logs.

    Excepciones:
        No propaga: cualquier fallo de escritura se captura y se loguea
        (best-effort, igual que audit_action).

    Llamado por:
        Servicios y código de fondo que necesitan dejar rastro forense fuera
        del flujo de un endpoint decorado.
    Llama a:
        db_manager.get_session() y get_client_ip() (si no se pasó ip_address).
    """
    try:
        with db_manager.get_session() as session:
            log = AuditLog(
                user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=str(details) if details else None,
                ip_address=ip_address or get_client_ip(),
                user_agent=user_agent or request.headers.get('User-Agent', '')[:500] if request else None
            )
            session.add(log)
        logger.info(f"Audit (sync): {action} by user {user_id}")
    except Exception as e:
        logger.error(f"Error registrando auditoría síncrona: {e}")