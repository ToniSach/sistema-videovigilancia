"""
================================================================================
MÓDULO: core.security — Utilidades de autenticación y autorización
================================================================================

PROPÓSITO
    Funciones transversales de seguridad: hash/verificación de contraseñas,
    lectura de la identidad del usuario desde el JWT y un decorador de
    autorización por rol (admin). Es el "kit" de auth que usan rutas y servicios.

RESPONSABILIDAD PRINCIPAL
    - hash_password / verify_password: derivación segura de contraseñas
      (PBKDF2-SHA256 vía werkzeug) — no se guardan contraseñas en claro.
    - get_current_user_id: extraer el ID del usuario del token JWT del request.
    - admin_required: cerrar rutas a usuarios sin rol admin.

NOTA SOBRE EL NOMBRE
    El docstring histórico menciona "bcrypt", pero el hashing real lo provee
    werkzeug con el esquema pbkdf2:sha256 (ver generate_password_hash). El
    resultado es autodescriptivo (lleva el método embebido), así que verify
    sigue funcionando aunque cambie el algoritmo por defecto.

DEPENDENCIAS
    werkzeug.security ......... generate_password_hash / check_password_hash.
    flask_jwt_extended ........ get_jwt_identity / verify_jwt_in_request / get_jwt.

COMPONENTES RELACIONADOS
    services.auth_service ..... emite y refresca los tokens; usa estos hashes.
    core.jwt_blocklist ........ revocación de los tokens que aquí se leen.
    api.routes.auth / rutas admin ... consumen get_current_user_id y
                                @admin_required.

PUNTO DE ENTRADA EN LA ARQUITECTURA
    Módulo de funciones libres (sin estado/singleton). Se importa donde haga
    falta validar credenciales o permisos.

PIPELINE(S)
    Pipeline #2 (Autenticación): hash/verify en login y cambio de contraseña;
    get_current_user_id y admin_required en CADA request protegido (autorización).
================================================================================
"""
import logging
from functools import wraps
from typing import Callable, Any

from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request, get_jwt

logger = logging.getLogger(__name__)


def hash_password(plain_text: str) -> str:
    """
    Genera el hash de una contraseña (pipeline #2: registro / cambio de clave).

    Usa PBKDF2-SHA256 con salt aleatorio de 16 bytes; el string resultante
    incluye método y salt, de modo que verify_password no necesita parámetros
    extra. Nunca se almacena la contraseña en claro.

    Inputs: plain_text — contraseña en texto plano.
    Outputs: str — hash con formato "pbkdf2:sha256:...".
    Excepciones: ValueError si werkzeug no puede procesar la entrada.
    Llamado por: AuthService (alta de usuario y cambio de contraseña).
    """
    try:
        return generate_password_hash(plain_text, method='pbkdf2:sha256', salt_length=16)
    except Exception as error:
        logger.error(f"Error al hashear contraseña: {error}")
        raise ValueError("No se pudo procesar la contraseña") from error


def verify_password(plain_text: str, hashed: str) -> bool:
    """
    Verifica una contraseña contra su hash almacenado (pipeline #2: login).

    Inputs: plain_text (clave introducida), hashed (hash guardado en BD).
    Outputs: bool — True si coincide. Devuelve False (no lanza) ante cualquier
        error de verificación, para no filtrar detalles del fallo al cliente.
    Llamado por: AuthService durante el login y al validar la clave actual en
        un cambio de contraseña.
    """
    try:
        return check_password_hash(hashed, plain_text)
    except Exception as error:
        logger.error(f"Error al verificar contraseña: {error}")
        return False


def get_current_user_id() -> int:
    """
    Devuelve el ID del usuario autenticado a partir del JWT del request actual
    (pipeline #2: autorización en cada endpoint protegido).

    Precondición: debe llamarse dentro de un contexto con @jwt_required() activo
    (el token ya validado por flask_jwt_extended).

    Outputs: int — ID del usuario (la identidad del token, convertida a entero).
    Excepciones:
        RuntimeError — si no hay identidad JWT en el contexto.
        ValueError — si la identidad del token no es numérica.
    Llamado por: prácticamente todas las rutas protegidas para saber "quién pide"
        (filtrar cámaras/eventos por dueño, aplicar permisos, etc.).
    Llama a: flask_jwt_extended.get_jwt_identity.
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
    Decorador de autorización por rol: exige rol "admin" (pipeline #2).

    Verifica el JWT (verify_jwt_in_request) y lee el claim "role"; si no es
    "admin" corta con 403. Si falta/expira el token, responde 401. Debe ir
    DESPUÉS de @jwt_required() en la pila de decoradores.

    Inputs: fn — la vista a proteger.
    Outputs: wrapper que ejecuta fn solo si el usuario es admin; en caso
        contrario un jsonify con 403 (rol insuficiente) o 401 (sin auth).
    Llamado por: rutas de administración (gestión de usuarios, config del
        sistema, etc.).
    Llama a: flask_jwt_extended.verify_jwt_in_request / get_jwt.

    Ejemplo:
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
