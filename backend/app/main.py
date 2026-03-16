"""
Punto de entrada principal de la aplicación Flask.
Implementa Factory Pattern para creación de app y registro de blueprints.
"""
import os
import logging
import sys
from datetime import timedelta

from flask import Flask, jsonify
from flask_jwt_extended import JWTManager
from flask_cors import CORS

# Agregar directorio raíz al path para imports absolutos
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
project_root = os.path.dirname(backend_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backend.app.config import settings
from backend.app.database.connection import db_manager


def create_app() -> Flask:
    """
    Factory function que crea y configura la aplicación Flask.
    
    Returns:
        Flask: Aplicación configurada con blueprints registrados
    """
    app = Flask(__name__)
    
    # Configuración básica
    app.config["SECRET_KEY"] = settings.SECRET_KEY
    app.config["JWT_SECRET_KEY"] = settings.JWT_SECRET_KEY
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(
        minutes=settings.JWT_ACCESS_TOKEN_MINUTES
    )
    app.config["JWT_REFRESH_TOKEN_EXPIRES"] = timedelta(
        days=settings.JWT_REFRESH_TOKEN_DAYS
    )
    app.config["JSON_SORT_KEYS"] = False  # Mantener orden de keys en JSON
    
    # Configurar CORS (permitir todas las origenes en desarrollo)
    CORS(app, resources={
        r"/api/*": {
            "origins": "*",
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Authorization", "Content-Type"]
        }
    })
    
    # Inicializar JWTManager
    JWTManager(app)
    
    # Inicializar base de datos (crear tablas si no existen)
    try:
        db_manager.init_db()
        logging.info("Base de datos inicializada correctamente")
    except Exception as error:
        logging.error(f"Error al inicializar base de datos: {error}")
        # Continuar anyway, podría ser error temporal de conexión
    
    # Registrar blueprints con manejo de errores individual
    # Esto permite que la app arranque aunque algunos módulos no estén listos
    
    # Blueprint de autenticación
    try:
        from backend.app.api.routes.auth import auth_bp
        app.register_blueprint(auth_bp)
        logging.info("Blueprint 'auth' registrado")
    except ImportError as error:
        logging.warning(f"Blueprint 'auth' no disponible: {error}")
    
    # Blueprint de cámaras (Parte 4)
    try:
        from backend.app.api.routes.cameras import cameras_bp
        app.register_blueprint(cameras_bp)
        logging.info("Blueprint 'cameras' registrado")
    except ImportError as error:
        logging.warning(f"Blueprint 'cameras' no disponible aún: {error}")
    
    # Blueprint de eventos (Parte 4)
    try:
        from backend.app.api.routes.events import events_bp
        app.register_blueprint(events_bp)
        logging.info("Blueprint 'events' registrado")
    except ImportError as error:
        logging.warning(f"Blueprint 'events' no disponible aún: {error}")
    
    # Blueprint de grabaciones (Parte 4)
    try:
        from backend.app.api.routes.recordings import recordings_bp
        app.register_blueprint(recordings_bp)
        logging.info("Blueprint 'recordings' registrado")
    except ImportError as error:
        logging.warning(f"Blueprint 'recordings' no disponible aún: {error}")
    
    # Blueprint de sistema/configuración (Parte 4)
    try:
        from backend.app.api.routes.system import system_bp
        app.register_blueprint(system_bp)
        logging.info("Blueprint 'system' registrado")
    except ImportError as error:
        logging.warning(f"Blueprint 'system' no disponible aún: {error}")
    
    # Blueprint de control de cámaras PTZ (Parte 5)
    try:
        from backend.app.api.routes.cameras_control import cameras_control_bp
        app.register_blueprint(cameras_control_bp)
        logging.info("Blueprint 'cameras_control' registrado")
    except ImportError as error:
        logging.warning(f"Blueprint 'cameras_control' no disponible aún: {error}")
    
    # Manejador de errores global
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"error": "Recurso no encontrado"}), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({"error": "Error interno del servidor"}), 500
    
    @app.route("/health")
    def health_check():
        """Endpoint de health check para monitoreo."""
        return jsonify({
            "status": "ok",
            "version": "1.0.0",
            "timestamp": datetime.utcnow().isoformat()
        })
    
    @app.route("/")
    def index():
        """Endpoint raíz con información básica."""
        return jsonify({
            "name": "Sistema de Videovigilancia API",
            "version": "1.0.0",
            "endpoints": {
                "auth": "/api/v1/auth",
                "health": "/health"
            }
        })
    
    return app


if __name__ == "__main__":
    # Configurar logging antes de iniciar
    log_level = getattr(logging, settings.LOG_LEVEL, logging.INFO)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Crear aplicación
    app = create_app()
    
    # Iniciar servidor
    logging.info(
        f"Iniciando servidor en {settings.SERVER_HOST}:{settings.SERVER_PORT}"
    )
    
    app.run(
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        debug=False,  # Nunca True en producción
        threaded=True,  # Permitir múltiples requests concurrentes
        use_reloader=False  # Evitar doble inicialización
    )
