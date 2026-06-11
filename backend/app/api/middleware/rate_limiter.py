"""
================================================================================
MÓDULO: api.middleware.rate_limiter — Limitación de tasa (anti-DoS / fuerza bruta)
================================================================================

PROPÓSITO
    Configura Flask-Limiter para todo el backend: límites globales por
    IP+usuario y decoradores con límites estrictos para endpoints sensibles
    (login, PTZ). Protege contra DoS y fuerza bruta de credenciales (FIX F1.5).

RESPONSABILIDAD PRINCIPAL
    - Definir la función de clave (get_key_func): IP, o IP:usuario si hay JWT.
    - Construir el Limiter global (create_limiter) con almacenamiento en memoria.
    - Ofrecer decoradores reutilizables (strict/medium/ptz) para sobrescribir el
      límite global en endpoints concretos.

DEPENDENCIAS
    flask_limiter ............... Limiter + get_remote_address
    flask ....................... request / jsonify
    flask_jwt_extended .......... get_jwt_identity (clave por usuario; lazy)

COMPONENTES RELACIONADOS
    backend/app/main.py:create_app() ... llama create_limiter(app) en el paso 3
        y luego exime los endpoints de polling de UI vía
        _exempt_polling_endpoints() (de lo contrario el polling cada 3-20s
        agotaría el límite global).
    backend/app/api/helpers.py ......... otros helpers transversales de la API.

PUNTO DE ENTRADA / CÓMO LO USAN LAS RUTAS
    create_limiter(app) se invoca UNA vez en el arranque; el Limiter resultante
    se guarda en `app.limiter`. Las rutas que quieren un límite distinto al
    global importan strict_limit/medium_limit/ptz_limit y los aplican como
    decorador, pasándoles ese mismo Limiter.

NOTA SOBRE ALMACENAMIENTO
    storage_uri="memory://" → los contadores viven en memoria del proceso y se
    reinician al reiniciar el backend. Coherente con el diseño de proceso único
    (no hay Redis); suficiente para una appliance LAN.
================================================================================
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask import request, jsonify
import logging

logger = logging.getLogger(__name__)


def get_key_func():
    """
    Función de identificación (clave) que Flask-Limiter usa para contar.

    Propósito:
        Aislar el cupo por usuario autenticado y, si no lo hay, por IP. Así un
        usuario tras NAT no consume el cupo de otro que comparta IP pública.

    Inputs:
        Ninguno explícito; lee el contexto de petición (JWT + IP remota).

    Outputs:
        str con la clave de rate limiting: "ip:user_id" si hay JWT válido, o
        sólo "ip" en caso contrario.

    Excepciones:
        No propaga: si no hay contexto JWT (endpoint público o token ausente)
        cae al except y devuelve sólo la IP.

    Llamado por:
        El Limiter (internamente, en cada request) — se pasa como key_func.
    Llama a:
        get_jwt_identity() y get_remote_address().
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
    Factory que crea y enlaza el Limiter global de la aplicación.

    Propósito:
        Construir el Limiter con la clave IP+usuario, los límites por defecto y
        el manejador de exceso, y asociarlo a la app Flask.

    Inputs:
        app: la instancia Flask a la que se enlaza el limitador.

    Outputs:
        El objeto Limiter (el llamador lo guarda en app.limiter para poder
        eximir endpoints después).

    Excepciones:
        No lanza explícitamente.

    Llamado por:
        backend/app/main.py:create_app() en el paso 3 del arranque.
    Llama a:
        Limiter(...) (Flask-Limiter), con get_key_func como key_func.
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


# ============================================================================
# DECORADORES POR ENDPOINT
# Sobrescriben el límite global en endpoints concretos. Reciben el Limiter ya
# creado (app.limiter) y devuelven el decorador limiter.limit(...) a aplicar.
# ============================================================================
def strict_limit(limiter):
    """
    Límite estricto para autenticación (login, códigos): 5/minuto.
    Frena la fuerza bruta de credenciales.

    Inputs: limiter (el Limiter global). Outputs: decorador limiter.limit("5/min").
    Llamado por: endpoints de auth en routes/.
    """
    return limiter.limit("5 per minute")


def medium_limit(limiter):
    """
    Límite medio para operaciones frecuentes (consultas, streaming): 30/minuto.

    Inputs: limiter. Outputs: decorador limiter.limit("30/min").
    Llamado por: endpoints de lectura/operación frecuente en routes/.
    """
    return limiter.limit("30 per minute")


def ptz_limit(limiter):
    """
    Límite para comandos PTZ: 10/minuto. Evita el spam de movimiento de cámara.

    Inputs: limiter. Outputs: decorador limiter.limit("10/min").
    Llamado por: endpoints de control PTZ en routes/cameras.py.
    """
    return limiter.limit("10 per minute")