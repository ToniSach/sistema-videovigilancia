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
    
    # FASE 2: Móvil
    safe_register("backend.app.api.routes.mobile", "mobile_bp")

    return blueprints

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

    # Rate Limiter
    limiter = create_limiter(app)
    app.limiter = limiter

    # CORS
    CORS(app, resources={
        r"/api/*": {
            "origins": ["http://localhost", "http://127.0.0.1", "*"],
            "methods": ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
            "allow_headers": ["Authorization", "Content-Type"]
        }
    })

    # ============================================================================
    # BASE DE DATOS
    # ============================================================================

    try:
        db_manager.init_db()
        logger.info("Base de datos inicializada correctamente")
    except Exception as e:
        logger.critical(f"Error inicializando DB: {e}")
        raise

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

        # LiveHLSService (singleton, ya se autoinicia)
        from backend.app.streaming.live_hls_service import live_hls_service
        logger.info("LiveHLSService activo")

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
            metrics_collector.start_stalled_monitor(CameraManager())
            logger.info("Stalled camera monitor iniciado")
        except Exception as e:
            logger.error(f"Error iniciando monitor de cámaras congeladas: {e}")

        app._container_initialized = True
        logger.info("Contenedor inicializado")

    except Exception as e:
        logger.error(f"Error inicializando contenedor: {e}")

    # ============================================================================
    # BLUEPRINTS
    # ============================================================================

    registered = register_blueprints(app)

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
        logger.error(f"Error 500: {error}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500

    @app.errorhandler(429)
    def ratelimit_handler(e):
        return jsonify({
            "success": False, 
            "error": "Demasiadas solicitudes. Intente más tarde.",
            "retry_after": e.description
        }), 429

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

        app.run(
            host=settings.SERVER_HOST,
            port=settings.SERVER_PORT,
            debug=False,
            threaded=True,
            use_reloader=False
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
        
        # Detener LiveHLSService
        try:
            from backend.app.streaming.live_hls_service import live_hls_service
            live_hls_service.shutdown()
            logger.info("LiveHLSService detenido")
        except Exception as e:
            logger.error(f"Error deteniendo LiveHLSService: {e}")

        # ================== NUEVO: Detener ConsistencyChecker ==================
        if hasattr(app, 'consistency_checker'):
            try:
                app.consistency_checker.stop()
                logger.info("ConsistencyChecker detenido")
            except Exception as e:
                logger.error(f"Error deteniendo ConsistencyChecker: {e}")