"""
Rate Limiting Middleware - Protección contra DoS y fuerza bruta.
FIX F1.5: Límites por IP y por usuario.
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask import request, jsonify
import logging

logger = logging.getLogger(__name__)


def get_key_func():
    """
    Función de identificación para rate limiting.
    Combina IP + JWT identity si está autenticado.
    """
    try:
        from flask_jwt_extended import get_jwt_identity
        user_id = get_jwt_identity()
        ip = get_remote_address()
        return f"{ip}:{user_id}" if user_id else ip
    except Exception:
        return get_remote_address()


def create_limiter(app):
    """
    Factory para crear Limiter configurado.
    """
    # Defaults pensados para uso interactivo LAN (NVR con polling de UI).
    # 50/hora era irreal: el frontend hace polling de eventos cada 3-10s y
    # cada usuario fácilmente genera 500+ req/h sólo viendo el panel.
    limiter = Limiter(
        app=app,
        key_func=get_key_func,
        default_limits=["10000 per day", "1000 per hour"],
        storage_uri="memory://",
        strategy="fixed-window",
        on_breach=lambda limit: jsonify({
            "success": False,
            "error": f"Rate limit excedido. Límite: {limit}. Intente más tarde."
        }, 429)
    )

    logger.info("Rate Limiter inicializado")
    return limiter


# Decoradores específicos para endpoints sensibles
def strict_limit(limiter):
    """
    Límite estricto para endpoints de autenticación (login, códigos).
    5 intentos por minuto.
    """
    return limiter.limit("5 per minute")


def medium_limit(limiter):
    """
    Límite medio para operaciones frecuentes (streaming, consultas).
    30 por minuto.
    """
    return limiter.limit("30 per minute")


def ptz_limit(limiter):
    """
    Límite específico para PTZ (evitar spam de movimiento).
    10 por minuto.
    """
    return limiter.limit("10 per minute")