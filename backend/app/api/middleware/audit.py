"""
Audit Middleware - Registra acciones sensibles en la base de datos.
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
    """Obtiene la IP real del cliente considerando proxies."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or 'unknown'


def audit_action(action: str, resource_type: Optional[str] = None, 
                 resource_id: Optional[str] = None, 
                 get_details: Optional[Callable[[], dict]] = None):
    """
    Decorador para registrar acciones de auditoría en endpoints.
    
    Uso:
        @audit_action("delete_recording", "recording", lambda: {"recording_id": recording_id})
        @jwt_required()
        def delete_recording(recording_id):
            ...
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Ejecutar el endpoint primero (capturar respuesta o excepción)
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
    Función síncrona para registrar acciones desde código (no desde endpoint).
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