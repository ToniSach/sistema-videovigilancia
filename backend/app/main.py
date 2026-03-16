"""
Main entry point para el backend del Sistema NVR.
Inicializa Flask, base de datos y todos los servicios.
"""
import sys
import os
import logging
from pathlib import Path

# Configurar logging ANTES de cualquier otro import
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURACIÓN DE PATHS (Debe ejecutarse antes de imports del proyecto)
# ============================================================================

def setup_paths():
    """Configura los paths para imports absolutos funcionen correctamente."""
    # Obtener directorio de este archivo (backend/app/)
    current_file = Path(__file__).resolve()
    current_dir = current_file.parent
    backend_dir = current_dir.parent
    root_dir = backend_dir.parent
    
    # Añadir a sys.path si no están
    paths_to_add = [
        str(root_dir),           # sistema-videovigilancia/
        str(backend_dir),        # sistema-videovigilancia/backend/
        str(current_dir),        # sistema-videovigilancia/backend/app/
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
from flask_jwt_extended import JWTManager, get_jwt_identity
from flask_cors import CORS
from datetime import timedelta

# ============================================================================
# IMPORTS DEL PROYECTO (Backend únicamente)
# ============================================================================

try:
    from backend.app.config import settings
    from backend.app.database.connection import db_manager
    from backend.app.container import get_container
except ImportError as e:
    logger.error(f"Error crítico importando módulos del backend: {e}")
    logger.error("Verifique la estructura de carpetas y que no haya archivos __pycache__ corruptos")
    sys.exit(1)

# ============================================================================
# BLUEPRINTS (Solo del backend, nunca del desktop_app)
# ============================================================================

def register_blueprints(app):
    """Registra todos los blueprints de la API."""
    
    # Importar blueprints uno por uno con manejo de errores
    blueprints = []
    
    try:
        from backend.app.api.routes.auth import auth_bp
        app.register_blueprint(auth_bp)
        blueprints.append('auth')
        logger.info("Blueprint 'auth' registrado")
    except Exception as e:
        logger.error(f"Error registrando blueprint 'auth': {e}")

    try:
        from backend.app.api.routes.cameras import cameras_bp
        app.register_blueprint(cameras_bp)
        blueprints.append('cameras')
        logger.info("Blueprint 'cameras' registrado")
    except Exception as e:
        logger.error(f"Error registrando blueprint 'cameras': {e}")

    try:
        from backend.app.api.routes.events import events_bp
        app.register_blueprint(events_bp)
        blueprints.append('events')
        logger.info("Blueprint 'events' registrado")
    except Exception as e:
        logger.error(f"Error registrando blueprint 'events': {e}")

    try:
        from backend.app.api.routes.recordings import recordings_bp
        app.register_blueprint(recordings_bp)
        blueprints.append('recordings')
        logger.info("Blueprint 'recordings' registrado")
    except Exception as e:
        logger.error(f"Error registrando blueprint 'recordings': {e}")

    try:
        from backend.app.api.routes.system import system_bp
        app.register_blueprint(system_bp)
        blueprints.append('system')
        logger.info("Blueprint 'system' registrado")
    except Exception as e:
        logger.error(f"Error registrando blueprint 'system': {e}")
    
    # ⚠️  IMPORTANTE: Nunca registrar cameras_control aquí
    # Es un archivo de UI que no debe estar en el backend

    return blueprints

# ============================================================================
# FACTORY APP
# ============================================================================

def create_app(config_name='default'):
    """
    Application factory para crear la app Flask.
    
    Args:
        config_name: Nombre de la configuración a usar
    """
    app = Flask(__name__)
    
    # Configuración desde settings
    app.config['SECRET_KEY'] = settings.SECRET_KEY
    app.config['JWT_SECRET_KEY'] = settings.JWT_SECRET_KEY
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(minutes=settings.JWT_ACCESS_TOKEN_MINUTES)
    app.config['JWT_REFRESH_TOKEN_EXPIRES'] = timedelta(days=settings.JWT_REFRESH_TOKEN_DAYS)
    
    # Inicializar JWT
    jwt = JWTManager(app)
    
    # Configurar CORS para permitir requests del desktop app
    CORS(app, resources={
        r"/api/*": {
            "origins": ["http://localhost", "http://127.0.0.1"],
            "methods": ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
            "allow_headers": ["Authorization", "Content-Type"]
        }
    })
    
    # Manejador de errores JWT
    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload):
        return jsonify({"success": False, "error": "Token expirado"}), 401
    
    @jwt.invalid_token_loader
    def invalid_token_callback(error):
        return jsonify({"success": False, "error": "Token inválido"}), 401
    
    @jwt.unauthorized_loader
    def missing_token_callback(error):
        return jsonify({"success": False, "error": "Autorización requerida"}), 401
    
    # Inicializar base de datos
    try:
        db_manager.init_db()
        logger.info("Base de datos inicializada correctamente")
    except Exception as e:
        logger.error(f"Error inicializando base de datos: {e}")
        raise
    
    # Inicializar contenedor de dependencias (lazy)
    @app.before_request
    def init_container():
        """Inicializa el contenedor en el primer request si es necesario."""
        if not hasattr(app, '_container_initialized'):
            try:
                container = get_container()
                app._container_initialized = True
                logger.debug("Contenedor inicializado en primer request")
            except Exception as e:
                logger.error(f"Error inicializando contenedor: {e}")
    
    # Registrar blueprints
    registered = register_blueprints(app)
    
    if not registered:
        logger.warning("No se registró ningún blueprint!")
    
    # Health check endpoint
    @app.route('/api/v1/health', methods=['GET'])
    def health_check():
        return jsonify({
            "status": "ok",
            "service": "NVR Backend",
            "version": "1.0.0",
            "blueprints": registered
        })
    
    # Error handlers globales
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
        # Crear aplicación
        app = create_app()
        
        # Verificar FFmpeg disponible (warning si no existe)
        try:
            import shutil
            ffmpeg_path = shutil.which("ffmpeg")
            if not ffmpeg_path:
                logger.warning("FFmpeg no encontrado en PATH. Las cámaras no funcionarán hasta instalar FFmpeg.")
                logger.warning("Descargue desde: https://www.gyan.dev/ffmpeg/builds/")
            else:
                logger.info(f"FFmpeg encontrado: {ffmpeg_path}")
        except Exception as e:
            logger.debug(f"No se pudo verificar FFmpeg: {e}")
        
        # Iniciar servidor
        host = settings.SERVER_HOST
        port = settings.SERVER_PORT
        
        logger.info(f"Iniciando servidor en {host}:{port}")
        logger.info(f"API Base URL: http://{host}:{port}/api/v1/")
        
        app.run(
            host=host,
            port=port,
            debug=False,  # Nunca True en producción
            threaded=True,
            use_reloader=False  # Importante: False para evitar doble inicialización
        )
        
    except Exception as e:
        logger.critical(f"Error fatal iniciando aplicación: {e}", exc_info=True)
        sys.exit(1)