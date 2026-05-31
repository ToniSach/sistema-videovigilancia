"""
Main entry point - Fase 0, 1, 2 y 3 integradas.
"""
import sys
import os
import logging
from pathlib import Path

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURACIÓN DE PATHS
# ============================================================================

def setup_paths():
    current_file = Path(__file__).resolve()
    current_dir = current_file.parent
    backend_dir = current_dir.parent
    root_dir = backend_dir.parent
    
    for path in [str(root_dir), str(backend_dir), str(current_dir)]:
        if path not in sys.path:
            sys.path.insert(0, path)

setup_paths()

# ============================================================================
# IMPORTS
# ============================================================================

from flask import Flask, jsonify
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from datetime import timedelta

try:
    from backend.app.config import settings
    from backend.app.database.connection import db_manager
    from backend.app.container import get_container
except ImportError as e:
    logger.critical(f"Error crítico importando módulos: {e}")
    sys.exit(1)

# Rate limiter
from backend.app.api.middleware.rate_limiter import create_limiter

# ============================================================================
# BOOTSTRAP TELEGRAM
# ============================================================================

def _bootstrap_telegram_from_env():
    """
    Si el .env trae TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID, los materializa en
    SystemConfig (clave-valor en BD) y activa el notificador. Esto evita tener
    que configurar Telegram manualmente para entornos de prueba/desarrollo.

    Si SystemConfig ya tiene esos valores, no los sobreescribe.
    """
    bot_token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not bot_token or not chat_id:
        logger.info("Bootstrap Telegram omitido: .env sin token/chat_id")
        return

    from backend.app.database.models import SystemConfig

    defaults = {
        "telegram_bot_token": bot_token,
        "telegram_chat_id": chat_id,
        "telegram_chat_ids": chat_id,
        "telegram_enabled": "true",
        # Filtros: notificar todos los tipos comunes
        "notify_person": "true",
        "notify_vehicle": "true",
        "notify_motion": "true",
        "notify_camera_offline": "true",
        "notify_tampering": "true",
    }

    inserted = 0
    with db_manager.get_session() as session:
        existing = {c.key: c for c in session.query(SystemConfig).all()}
        for key, value in defaults.items():
            if key not in existing:
                session.add(SystemConfig(key=key, value=value))
                inserted += 1
        session.commit()

    if inserted:
        logger.info(f"Bootstrap Telegram: {inserted} claves insertadas en SystemConfig")

    # Recargar config del notificador y suscribirlo a EventManager para que
    # los eventos generados por AI lleguen como notificaciones.
    try:
        from backend.app.notifications.telegram_notifier import telegram_notifier
        from backend.app.events.event_manager import event_manager
        telegram_notifier.reload_config()
        event_manager.subscribe_all(telegram_notifier._on_event)
        logger.info("TelegramNotifier suscrito a EventManager (modo activo)")
    except Exception as e:
        logger.error(f"Error suscribiendo TelegramNotifier: {e}")


# ============================================================================
# BLUEPRINTS
# ============================================================================

def register_blueprints(app):
    """Registra todos los blueprints disponibles."""
    
    blueprints = []

    def safe_register(import_path, name):
        try:
            module = __import__(import_path, fromlist=[name])
            bp = getattr(module, name)
            app.register_blueprint(bp)
            blueprints.append(name.replace("_bp", ""))
        except Exception as e:
            logger.error(f"Error registrando '{name}': {e}")

    # Core
    safe_register("backend.app.api.routes.auth", "auth_bp")
    safe_register("backend.app.api.routes.cameras", "cameras_bp")
    safe_register("backend.app.api.routes.events", "events_bp")
    safe_register("backend.app.api.routes.recordings", "recordings_bp")
    safe_register("backend.app.api.routes.system", "system_bp")

    # Multiusuario
    safe_register("backend.app.api.routes.users", "users_bp")
    safe_register("backend.app.api.routes.permissions", "permissions_bp")
    safe_register("backend.app.api.routes.notifications", "notifications_bp")
    safe_register("backend.app.api.routes.devices", "devices_bp")

    # Telegram
    #safe_register("backend.app.api.routes.telegram", "telegram_bp")
    safe_register("backend.app.api.routes.telegram_link", "telegram_link_bp")

    # Otros
    safe_register("backend.app.api.routes.qr", "qr_bp")
    safe_register("backend.app.api.routes.storage", "storage_bp")

    # IA (activación por cámara y por lente)
    safe_register("backend.app.api.routes.ai", "ai_bp")

    # FASE 2: Móvil
    safe_register("backend.app.api.routes.mobile", "mobile_bp")

    # WebSocket notificaciones push LAN (cliente Android CamLink)
    # ws_bp solo registra el blueprint; el Sock se enlaza por separado
    # en create_app() porque necesita el objeto Flask.
    safe_register("backend.app.api.routes.ws", "ws_bp")

    return blueprints


def _exempt_polling_endpoints(app):
    """Marca endpoints de polling de UI como exentos del rate limiter."""
    limiter = getattr(app, "limiter", None)
    if limiter is None:
        return

    polling_endpoints = (
        "events.get_events",
        "ai.get_camera_status",
        "ai.get_status",
        "system.get_stats",
        "system.health_check",
        "system.get_hardware_info",
        "storage.get_storage_info",
        "cameras.get_cameras",
    )
    for ep in polling_endpoints:
        view = app.view_functions.get(ep)
        if view is not None:
            try:
                limiter.exempt(view)
            except Exception as e:
                logger.warning(f"No se pudo eximir {ep} del rate limiter: {e}")

# ============================================================================
# FACTORY
# ============================================================================

def create_app(config_name='default'):
    app = Flask(__name__)

    # Configuración básica
    app.config['SECRET_KEY'] = settings.SECRET_KEY
    app.config['JWT_SECRET_KEY'] = settings.JWT_SECRET_KEY
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(minutes=settings.JWT_ACCESS_TOKEN_MINUTES)
    app.config['JWT_REFRESH_TOKEN_EXPIRES'] = timedelta(days=settings.JWT_REFRESH_TOKEN_DAYS)

    # JWT
    jwt = JWTManager(app)

    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload):
        return jsonify({"success": False, "error": "Token expirado"}), 401

    @jwt.invalid_token_loader
    def invalid_token_callback(error):
        return jsonify({"success": False, "error": "Token inválido"}), 401

    @jwt.unauthorized_loader
    def missing_token_callback(error):
        return jsonify({"success": False, "error": "Autorización requerida"}), 401

    # Blocklist en memoria: token revocados en logout vuelven 401.
    # Se pierde al reiniciar el backend (los tokens válidos por exp natural
    # vuelven a aceptarse). Aceptable en LAN; ver core/jwt_blocklist.py.
    from backend.app.core.jwt_blocklist import jwt_blocklist

    @jwt.token_in_blocklist_loader
    def check_token_revoked(_jwt_header, jwt_payload):
        return jwt_blocklist.is_revoked(jwt_payload.get("jti", ""))

    @jwt.revoked_token_loader
    def revoked_token_callback(_jwt_header, _jwt_payload):
        return jsonify({"success": False, "error": "Sesión cerrada"}), 401

    # Rate Limiter
    limiter = create_limiter(app)
    app.limiter = limiter

    # CORS — whitelist explícita por defecto.
    # En LAN normalmente queremos: localhost (desktop_app local) + IPs del
    # rango privado. NUNCA "*" en respuesta a credentials, eso permite CSRF
    # desde cualquier sitio que se cargue en el navegador del usuario.
    # Configurable vía CORS_ORIGINS="http://192.168.1.5,http://10.0.0.7"
    import os as _os
    _cors_env = _os.getenv("CORS_ORIGINS", "").strip()
    if _cors_env:
        cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
    else:
        cors_origins = [
            "http://localhost",
            "http://127.0.0.1",
            # Rango LAN privada típico (regex de Flask-CORS)
            r"http://192\.168\.\d+\.\d+(:\d+)?",
            r"http://10\.\d+\.\d+\.\d+(:\d+)?",
            r"http://172\.(1[6-9]|2[0-9]|3[01])\.\d+\.\d+(:\d+)?",
        ]
    CORS(app, resources={
        r"/api/*": {
            "origins": cors_origins,
            "methods": ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
            "allow_headers": ["Authorization", "Content-Type"],
        }
    })
    logger.info(f"CORS configurado con {len(cors_origins)} origen(es) permitido(s)")

    # ============================================================================
    # BASE DE DATOS
    # ============================================================================

    try:
        db_manager.init_db()
        logger.info("Base de datos inicializada correctamente")
    except Exception as e:
        logger.critical(f"Error inicializando DB: {e}")
        raise

    # Rehidratar JWT blocklist desde BD (si la tabla existe). Si no existe,
    # se ignora silenciosamente y la blocklist queda sólo en memoria.
    try:
        from backend.app.core.jwt_blocklist import jwt_blocklist
        jwt_blocklist.rehydrate_from_db()
    except Exception as e:
        logger.warning(f"No se pudo rehidratar blocklist JWT: {e}")

    # ============================================================================
    # BOOTSTRAP TELEGRAM (lee .env y materializa en SystemConfig si falta)
    # ============================================================================
    try:
        _bootstrap_telegram_from_env()
    except Exception as e:
        logger.warning(f"No se pudo bootstrap Telegram desde .env: {e}")

    # Iniciar el poller del bot (lee mensajes que llegan al bot para
    # detectar comandos /vincular). Si no hay bot_token configurado en
    # SystemConfig el poller no arranca (es OK, sólo significa que aún
    # no se han añadido las credenciales).
    try:
        from backend.app.notifications.telegram_bot_poller import telegram_bot_poller
        telegram_bot_poller.start()
    except Exception as e:
        logger.warning(f"No se pudo iniciar TelegramBotPoller: {e}")

    # ============================================================================
    # CONTENEDOR + CÁMARAS + STORAGE MANAGER + METRICS + HLS + CONSISTENCY + STALLED MONITOR
    # ============================================================================

    try:
        container = get_container()

        import threading
        import time

        def delayed_camera_startup():
            time.sleep(0.5)
            try:
                from backend.app.cameras.camera_manager import CameraManager
                CameraManager().start_all_active()
                logger.info("Cámaras iniciadas correctamente")
            except Exception as e:
                logger.error(f"Error iniciando cámaras: {e}")

        threading.Thread(target=delayed_camera_startup, daemon=True).start()

        # StorageManager
        try:
            from backend.app.recording.storage_manager import StorageManager
            from backend.app.database.repositories.recording_repository import RecordingRepository
            
            recording_repo = RecordingRepository()
            storage_manager = StorageManager(recording_repo=recording_repo)
            storage_manager.start()
            app.storage_manager = storage_manager
            logger.info("StorageManager iniciado")
        except Exception as e:
            logger.error(f"Error iniciando StorageManager: {e}")

        # MetricsCollector (singleton, ya se autoinicia)
        from backend.app.infrastructure.metrics.collector import metrics_collector
        logger.info("MetricsCollector activo")

        # (LiveHLSService eliminado: el directo se sirve por go2rtc/WebRTC.)

        # ================== NUEVO: Consistency Checker ==================
        try:
            from backend.app.storage.consistency_checker import consistency_checker
            consistency_checker.start()
            app.consistency_checker = consistency_checker
            logger.info("ConsistencyChecker iniciado")
        except Exception as e:
            logger.error(f"Error iniciando ConsistencyChecker: {e}")

        # ================== NUEVO: Stalled camera monitor ==================
        try:
            from backend.app.cameras.camera_manager import CameraManager
            # Threshold 25s: balance entre detectar cuelgues reales y NO
            # interferir con el reconnect normal del worker. Antes era 12s,
            # pero el reconnect de XiongMai tarda ~9s (1s backoff + 5s
            # arranque FFmpeg + 3-5s GOP), justo en el borde. A 12s el
            # monitor disparaba en mitad del reconnect y duplicaba el restart.
            # 25s da margen sobrado: el worker ya tiene su propio watchdog
            # interno (WATCHDOG_TIMEOUT=30s) que es la primera línea de defensa.
            metrics_collector.start_stalled_monitor(
                CameraManager(), interval=10, stalled_threshold=25
            )
            logger.info("Stalled camera monitor iniciado (threshold=25s)")
        except Exception as e:
            logger.error(f"Error iniciando monitor de cámaras congeladas: {e}")

        # ================== go2rtc (capa de medios, OPCIONAL) ==================
        # Solo arranca si GO2RTC_ENABLED=true. Aditivo: si está desactivado
        # (default) no hace nada y el sistema sigue con MJPEG/HLS como hoy.
        try:
            from backend.app.streaming.go2rtc_manager import Go2RtcManager
            from backend.app.database.repositories.camera_repository import CameraRepository

            go2rtc = Go2RtcManager()
            if go2rtc.is_enabled():
                cams = CameraRepository().get_all()
                if go2rtc.start(cams):
                    app.go2rtc = go2rtc
                    logger.info("go2rtc iniciado (capa de medios)")
                else:
                    logger.warning("go2rtc habilitado pero no se pudo arrancar")
            else:
                logger.info("go2rtc desactivado (GO2RTC_ENABLED=false)")
        except Exception as e:
            logger.error(f"Error iniciando go2rtc: {e}")

        app._container_initialized = True
        logger.info("Contenedor inicializado")

    except Exception as e:
        logger.error(f"Error inicializando contenedor: {e}")

    # ============================================================================
    # BLUEPRINTS
    # ============================================================================

    registered = register_blueprints(app)

    # Enlazar el WebSocket de notificaciones (flask-sock). Se hace después
    # de register_blueprints porque sock.route() escanea las rutas declaradas
    # en backend/app/api/routes/ws.py. El blueprint ws_bp es solo placeholder
    # — las rutas WebSocket reales viven en el `sock` global.
    try:
        from backend.app.api.routes.ws import sock as _ws_sock
        _ws_sock.init_app(app)
        logger.info("WebSocket /ws/notifications enlazado al Flask app")
    except Exception as e:
        logger.error(f"No pude enlazar el WebSocket: {e}")

    # Eximir endpoints de polling continuo del rate limiter.
    # El frontend hace polling cada 3-20s sobre estos endpoints; con el
    # default global (1000/h) varios usuarios + varias pestañas se pasan.
    # Estos endpoints son de sólo lectura para UI, así que es seguro.
    _exempt_polling_endpoints(app)

    # ============================================================================
    # HEALTH CHECK
    # ============================================================================

    @app.route('/api/v1/health', methods=['GET'])
    def health_check():
        from backend.app.infrastructure.metrics.collector import metrics_collector
        health_data = metrics_collector.get_health_status()
        
        return jsonify({
            "status": "ok",
            "service": "NVR Backend",
            "version": "1.3.0",
            "blueprints": registered,
            "system_health": health_data
        }), 200

    # ============================================================================
    # ERROR HANDLERS
    # ============================================================================

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"success": False, "error": "Endpoint no encontrado"}), 404

    @app.errorhandler(500)
    def internal_error(error):
        # Detalle completo al log con exc_info; al cliente sólo mensaje genérico
        # para no filtrar trazas (paths, nombres de columnas, schemas).
        logger.error(f"Error 500: {error}", exc_info=True)
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500

    @app.errorhandler(429)
    def ratelimit_handler(e):
        return jsonify({
            "success": False,
            "error": "Demasiadas solicitudes. Intente más tarde.",
            "retry_after": e.description
        }), 429

    # Handler genérico para cualquier Exception no atrapada en endpoints que
    # devuelven str(e) o que olvidan try/except. Solo se aplica a errores
    # NO-HTTPException (las HTTPException tienen handlers propios arriba).
    from werkzeug.exceptions import HTTPException

    @app.errorhandler(Exception)
    def handle_unexpected_exception(e):
        if isinstance(e, HTTPException):
            # Dejar que Flask maneje 404/405/etc con sus handlers
            return e
        logger.error(
            f"Excepción no manejada en {request.method} {request.path}: {e}",
            exc_info=True
        )
        return jsonify({
            "success": False,
            "error": "Error interno del servidor"
        }), 500

    return app

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    try:
        app = create_app()

        # Verificar FFmpeg
        try:
            import shutil
            ffmpeg_path = shutil.which("ffmpeg")
            if ffmpeg_path:
                logger.info(f"FFmpeg encontrado: {ffmpeg_path}")
            else:
                logger.warning("FFmpeg no encontrado en PATH")
        except Exception:
            pass

        # Estadísticas del executor
        from backend.app.core.executor import global_executor
        logger.info(f"GlobalExecutor stats: {global_executor.get_stats()}")

        logger.info(f"Iniciando servidor en {settings.SERVER_HOST}:{settings.SERVER_PORT}")

        # ===== Patch crítico para baja latencia en streaming MJPEG =====
        # Por defecto los sockets TCP usan el algoritmo Nagle: agrupan chunks
        # pequeños hasta llenar un paquete o esperar 40ms. En MJPEG cada
        # boundary frame es relativamente pequeño y la espera de Nagle
        # añadía ~40ms de latencia por frame. Desactivando Nagle (TCP_NODELAY)
        # cada chunk se envía inmediatamente.
        #
        # Sin esto, ningún otro fix de baja latencia sirve completamente.
        import socket as _socket
        from werkzeug.serving import WSGIRequestHandler

        class _LowLatencyHandler(WSGIRequestHandler):
            """WSGIRequestHandler optimizado para streaming MJPEG.

            Aplica dos opciones críticas en cada conexión:
              - TCP_NODELAY: desactiva Nagle → cada chunk va al instante.
              - SO_SNDBUF=64 KB: limita el send buffer del kernel para que
                la backpressure por TCP window se active rápido cuando el
                cliente lee lento. Sin esto, Windows auto-tunea SO_SNDBUF
                hasta varios MB y la pila acumula 5-10 s de JPEGs antes
                de que la app sienta presión (cola maxsize=1 con
                drop-oldest no protege porque el JPEG ya está en el socket).
            """
            def setup(self):
                try:
                    self.connection = self.request
                    self.connection.setsockopt(
                        _socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1
                    )
                    self.connection.setsockopt(
                        _socket.SOL_SOCKET, _socket.SO_SNDBUF, 64 * 1024
                    )
                except Exception:
                    pass
                super().setup()

        app.run(
            host=settings.SERVER_HOST,
            port=settings.SERVER_PORT,
            debug=False,
            threaded=True,
            use_reloader=False,
            request_handler=_LowLatencyHandler,
        )

    except Exception as e:
        logger.critical(f"Error fatal iniciando aplicación: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Graceful shutdown
        if hasattr(app, 'storage_manager'):
            try:
                app.storage_manager.stop()
                logger.info("StorageManager detenido")
            except Exception as e:
                logger.error(f"Error deteniendo StorageManager: {e}")
        
        # Detener MetricsCollector (incluye el monitor de cámaras congeladas)
        try:
            from backend.app.infrastructure.metrics.collector import metrics_collector
            metrics_collector.shutdown()
            logger.info("MetricsCollector detenido")
        except Exception as e:
            logger.error(f"Error deteniendo MetricsCollector: {e}")
            
        # Detener Executor
        try:
            from backend.app.core.executor import global_executor
            global_executor.shutdown()
            logger.info("GlobalExecutor detenido")
        except Exception as e:
            logger.error(f"Error deteniendo GlobalExecutor: {e}")
        
        # Detener go2rtc (si estaba activo)
        try:
            from backend.app.streaming.go2rtc_manager import Go2RtcManager
            Go2RtcManager().stop()
            logger.info("go2rtc detenido")
        except Exception as e:
            logger.error(f"Error deteniendo go2rtc: {e}")

        # ================== NUEVO: Detener ConsistencyChecker ==================
        if hasattr(app, 'consistency_checker'):
            try:
                app.consistency_checker.stop()
                logger.info("ConsistencyChecker detenido")
            except Exception as e:
                logger.error(f"Error deteniendo ConsistencyChecker: {e}")