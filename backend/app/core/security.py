"""
Utilidades de seguridad para autenticación y autorización.
Incluye hash de contraseñas, verificación JWT y decoradores de permisos.
"""
import logging
from functools import wraps
from typing import Callable, Any

from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request, get_jwt

logger = logging.getLogger(__name__)


def hash_password(plain_text: str) -> str:
    """
    Genera hash seguro de contraseña usando PBKDF2.
    
    Args:
        plain_text: Contraseña en texto plano
        
    Returns:
        str: Hash de la contraseña (método pbkdf2:sha256)
    """
    try:
        return generate_password_hash(plain_text, method='pbkdf2:sha256', salt_length=16)
    except Exception as error:
        logger.error(f"Error al hashear contraseña: {error}")
        raise ValueError("No se pudo procesar la contraseña") from error


def verify_password(plain_text: str, hashed: str) -> bool:
    """
    Verifica si una contraseña coincide con su hash almacenado.
    
    Args:
        plain_text: Contraseña en texto plano a verificar
        hashed: Hash almacenado en base de datos
        
    Returns:
        bool: True si coincide, False en caso contrario
    """
    try:
        return check_password_hash(hashed, plain_text)
    except Exception as error:
        logger.error(f"Error al verificar contraseña: {error}")
        return False


def get_current_user_id() -> int:
    """
    Obtiene el ID del usuario actual desde el token JWT válido.
    Debe usarse dentro de un contexto donde @jwt_required esté activo.
    
    Returns:
        int: ID numérico del usuario autenticado
        
    Raises:
        RuntimeError: Si no hay token JWT válido en el contexto
    """
    try:
        identity = get_jwt_identity()
        if identity is None:
            raise RuntimeError("No hay identidad JWT en el contexto actual")
        return int(identity)
    except ValueError as error:
        logger.error(f"ID de usuario no es numérico: {error}")
        raise ValueError("Token JWT contiene identidad inválida") from error
    except Exception as error:
        logger.error(f"Error al obtener usuario actual: {error}")
        raise RuntimeError("Error de autenticación") from error


def admin_required(fn: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorador que verifica si el usuario autenticado tiene rol de administrador.
    Debe usarse después de @jwt_required().
    
    Args:
        fn: Función a decorar
        
    Returns:
        Función wrapper que verifica permisos de admin
        
    Example:
        @jwt_required()
        @admin_required
        def delete_user(user_id):
            ...
    """
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        from flask import jsonify
        try:
            # Verificar que hay JWT válido primero
            verify_jwt_in_request()
            
            # Obtener claims del token
            jwt_data = get_jwt()
            user_role = jwt_data.get("role", "viewer")
            
            if user_role != "admin":
                logger.warning(f"Acceso denegado: usuario con rol {user_role} intentó acceder a recurso admin")
                return jsonify({"error": "Admin access required"}), 403
            
            return fn(*args, **kwargs)
            
        except Exception as error:
            logger.error(f"Error en verificación de admin: {error}")
            return jsonify({"error": "Authentication required"}), 401
    
    return wrapper
