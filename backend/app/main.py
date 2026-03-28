"""
Main entry point para el backend del Sistema NVR.
Inicializa Flask, base de datos y todos los servicios.
"""
import sys
import os
import logging
from pathlib import Path

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
    
    paths_to_add = [
        str(root_dir),
        str(backend_dir),
        str(current_dir),
    ]
    
    for path in paths_to_add:
        if path not in sys.path:
            sys.path.insert(0, path)
            logger.debug(f"Path añadido: {path}")

setup_paths()

# ============================================================================
# IMPORTS DE FLASK Y EXTENSIONES
# ============================================================================

from flask import Flask, jsonify
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from datetime import timedelta

# ============================================================================
# IMPORTS DEL PROYECTO
# ============================================================================

try:
    from backend.app.config import settings
    from backend.app.database.connection import db_manager
    from backend.app.container import get_container
except ImportError as e:
    logger.error(f"Error crítico importando módulos del backend: {e}")
    sys.exit(1)

# ============================
# NUEVOS IMPORTS (BLUEPRINTS)
# ============================

from backend.app.api.routes.users import users_bp
from backend.app.api.routes.permissions import permissions_bp
from backend.app.api.routes.notifications import notifications_bp
from backend.app.api.routes.devices import devices_bp
from backend.app.api.routes.telegram import telegram_bp
from backend.app.api.routes.qr import qr_bp
from backend.app.api.routes.storage import storage_bp  # ✅ NUEVO

# ============================================================================
# BLUEPRINTS
# ============================================================================

def register_blueprints(app):
    """Registra todos los blueprints de la API."""
    
    blueprints = []
    
    try:
        from backend.app.api.routes.auth import auth_bp
        app.register_blueprint(auth_bp)
        blueprints.append('auth')
    except Exception as e:
        logger.error(f"Error registrando blueprint 'auth': {e}")

    try:
        from backend.app.api.routes.cameras import cameras_bp
        app.register_blueprint(cameras_bp)
        blueprints.append('cameras')
    except Exception as e:
        logger.error(f"Error registrando blueprint 'cameras': {e}")

    try:
        from backend.app.api.routes.events import events_bp
        app.register_blueprint(events_bp)
        blueprints.append('events')
    except Exception as e:
        logger.error(f"Error registrando blueprint 'events': {e}")

    try:
        from backend.app.api.routes.recordings import recordings_bp
        app.register_blueprint(recordings_bp)
        blueprints.append('recordings')
    except Exception as e:
        logger.error(f"Error registrando blueprint 'recordings': {e}")

    try:
        from backend.app.api.routes.system import system_bp
        app.register_blueprint(system_bp)
        blueprints.append('system')
    except Exception as e:
        logger.error(f"Error registrando blueprint 'system': {e}")

    # ============================
    # NUEVOS BLUEPRINTS MULTIUSUARIO
    # ============================

    try:
        app.register_blueprint(users_bp)
        blueprints.append('users')
    except Exception as e:
        logger.error(f"Error registrando blueprint 'users': {e}")

    try:
        app.register_blueprint(permissions_bp)
        blueprints.append('permissions')
    except Exception as e:
        logger.error(f"Error registrando blueprint 'permissions': {e}")

    try:
        app.register_blueprint(notifications_bp)
        blueprints.append('notifications')
    except Exception as e:
        logger.error(f"Error registrando blueprint 'notifications': {e}")

    try:
        app.register_blueprint(devices_bp)
        blueprints.append('devices')
    except Exception as e:
        logger.error(f"Error registrando blueprint 'devices': {e}")

    try:
        app.register_blueprint(telegram_bp)
        blueprints.append('telegram')
    except Exception as e:
        logger.error(f"Error registrando blueprint 'telegram': {e}")

    try:
        app.register_blueprint(qr_bp)
        blueprints.append('qr')
    except Exception as e:
        logger.error(f"Error registrando blueprint 'qr': {e}")

    # ============================
    # NUEVO: STORAGE
    # ============================

    try:
        app.register_blueprint(storage_bp)
        blueprints.append('storage')
    except Exception as e:
        logger.error(f"Error registrando blueprint 'storage': {e}")

    # ============================
    # INTEGRACIÓN EVENTOS → NOTIFICACIONES
    # ============================

    try:
        from backend.app.services.notification_router import notification_router
        from backend.app.events.event_manager import event_manager

        def on_event(event_data):
            try:
                notification_router.handle_event(event_data)
            except Exception as e:
                logger.error(f"Error procesando evento: {e}")

        event_manager.subscribe_all(on_event)
        logger.info("EventManager conectado a NotificationRouter")

    except Exception as e:
        logger.error(f"Error integrando eventos/notificaciones: {e}")

    return blueprints


# ============================================================================
# FACTORY APP
# ============================================================================

def create_app(config_name='default'):
    app = Flask(__name__)
    
    app.config['SECRET_KEY'] = settings.SECRET_KEY
    app.config['JWT_SECRET_KEY'] = settings.JWT_SECRET_KEY
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(minutes=settings.JWT_ACCESS_TOKEN_MINUTES)
    app.config['JWT_REFRESH_TOKEN_EXPIRES'] = timedelta(days=settings.JWT_REFRESH_TOKEN_DAYS)
    
    jwt = JWTManager(app)
    
    CORS(app, resources={
        r"/api/*": {
            "origins": ["http://localhost", "http://127.0.0.1"],
            "methods": ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
            "allow_headers": ["Authorization", "Content-Type"]
        }
    })
    
    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload):
        return jsonify({"success": False, "error": "Token expirado"}), 401
    
    @jwt.invalid_token_loader
    def invalid_token_callback(error):
        return jsonify({"success": False, "error": "Token inválido"}), 401
    
    @jwt.unauthorized_loader
    def missing_token_callback(error):
        return jsonify({"success": False, "error": "Autorización requerida"}), 401
    
    try:
        db_manager.init_db()
        logger.info("Base de datos inicializada correctamente")
    except Exception as e:
        logger.error(f"Error inicializando base de datos: {e}")
        raise
    
    # OPTIMIZACIÓN: Inicialización del contenedor con manejo de errores mejorado
    # y carga lazy de servicios pesados
    @app.before_request
    def init_container():
        if not hasattr(app, '_container_initialized'):
            try:
                container = get_container()
                
                # OPTIMIZACIÓN: Iniciar cámaras activas solo después de que 
                # el contenedor esté completamente listo y en un thread separado
                # para no bloquear el arranque del servidor
                import threading
                
                def delayed_camera_startup():
                    """Inicia cámaras con delay para no bloquear el arranque de Flask."""
                    import time
                    time.sleep(0.5)  # Esperar 500ms a que Flask esté listo
                    
                    try:
                        from backend.app.cameras.camera_manager import CameraManager
                        cm = CameraManager()
                        
                        # OPTIMIZACIÓN: start_all_active ahora usa register_mjpeg=True por defecto
                        cm.start_all_active()
                        logger.info("Cámaras activas iniciadas automáticamente")
                    except Exception as e:
                        logger.error(f"Error iniciando cámaras: {e}")
                
                # Iniciar cámaras en thread separado para no bloquear el arranque
                startup_thread = threading.Thread(target=delayed_camera_startup, daemon=True)
                startup_thread.start()
                
                app._container_initialized = True
                logger.info("Contenedor inicializado y cámaras en proceso de arranque")
                
            except Exception as e:
                logger.error(f"Error inicializando contenedor: {e}")
    
    registered = register_blueprints(app)
    
    @app.route('/api/v1/health', methods=['GET'])
    def health_check():
        return jsonify({
            "status": "ok",
            "service": "NVR Backend",
            "version": "1.0.0",
            "blueprints": registered
        })
    
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"success": False, "error": "Endpoint no encontrado"}), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        logger.error(f"Error 500: {error}")
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500
    
    return app


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    try:
        app = create_app()
        
        try:
            import shutil
            ffmpeg_path = shutil.which("ffmpeg")
            if not ffmpeg_path:
                logger.warning("FFmpeg no encontrado en PATH.")
            else:
                logger.info(f"FFmpeg encontrado: {ffmpeg_path}")
        except Exception:
            pass
        
        host = settings.SERVER_HOST
        port = settings.SERVER_PORT
        
        logger.info(f"Iniciando servidor en {host}:{port}")
        
        app.run(
            host=host,
            port=port,
            debug=False,
            threaded=True,
            use_reloader=False
        )
        
    except Exception as e:
        logger.critical(f"Error fatal iniciando aplicación: {e}", exc_info=True)
        sys.exit(1)