"""
================================================================================
MÓDULO: main — Punto de entrada y arranque del backend NVR/VMS
================================================================================

PROPÓSITO
    Único punto de entrada del backend. Construye la aplicación Flask
    (`create_app()`), cablea TODA la infraestructura de larga vida (BD, JWT,
    CORS, rate limiter, contenedor de dependencias, cámaras, go2rtc, métricas,
    telemetría, notificaciones) y arranca el servidor HTTP de un SOLO proceso.

RESPONSABILIDAD PRINCIPAL
    Orquestar el *Pipeline de inicio del sistema* (pipeline #1): inicializar
    cada subsistema en el ORDEN correcto de dependencias y dejar los servicios
    de fondo corriendo, sin que un fallo aislado tumbe todo el arranque
    (cada bloque va en su propio try/except → "best-effort").

RESTRICCIÓN ARQUITECTÓNICA CRÍTICA — PROCESO ÚNICO
    Casi todo el estado vivo del sistema (workers, subprocesos FFmpeg/go2rtc,
    buffers, pools de modelos, pool de BD) vive en SINGLETONS en memoria de
    proceso (ver CameraManager, EventManager, DatabaseManager, GlobalExecutor,
    metrics_collector, go2rtc_manager...). Por eso el backend DEBE correr como
    un único proceso: `app.run(threaded=True)` (un proceso, varios hilos).
    NO ponerlo detrás de Gunicorn/uWSGI multi-worker sin rediseñar los
    singletons (moviéndolos a Redis/BD).

DEPENDENCIAS IMPORTANTES (orden de import / arranque)
    config.settings ........... configuración global (lee .env)            [base]
    database.connection ....... db_manager.init_db() crea tablas           [paso 2]
    core.jwt_blocklist ........ revocación de tokens (rehidrata desde BD)
    container.get_container ... DI: construye repos + servicios            [paso 4]
    cameras.camera_manager .... arranca cámaras activas (async, +0.5s)     [paso 5]
    streaming.go2rtc_manager .. capa de medios (directo/grabación/IA)      [paso 13]
    infrastructure.metrics .... salud + monitor de cámaras congeladas
    notifications.telegram_* .. bootstrap y poller del bot de Telegram

PIPELINE DE INICIO (#1) — secuencia que ejecuta create_app()
    ┌─────────────────────────────────────────────────────────────────────┐
    │ 1. setup_paths()  → sys.path (raíz/backend/app)                       │
    │ 2. Flask + SECRET_KEY/JWT + JWTManager (loaders de error y blocklist) │
    │ 3. create_limiter()  → rate limiter; CORS whitelist LAN              │
    │ 4. db_manager.init_db()  → crea tablas; reconcilia esquema legado    │
    │ 5. jwt_blocklist.rehydrate_from_db()  → tokens revocados vigentes    │
    │ 6. _bootstrap_telegram_from_env() + telegram_bot_poller.start()      │
    │ 7. get_container()  → DI (repos + servicios)                          │
    │ 8. (hilo +0.5s) CameraManager().start_all_active()                    │
    │ 9. (hilo +2s) YLOModelPool().warmup()  → precarga YOLO                │
    │10. StorageManager.start()  → rotación/limpieza de grabaciones         │
    │11. metrics_collector (autoinicia) + stream_keepalive.start()         │
    │12. telemetry_recorder.start() + consistency_checker.start()          │
    │13. start_stalled_monitor() + Go2RtcManager().start(cams)             │
    │14. register_blueprints() + WebSocket sock.init_app()                  │
    │15. _exempt_polling_endpoints()  → exime polling del rate limiter     │
    │16. /api/v1/health + error handlers (404/429/500/Exception)           │
    │17. app.run(threaded=True, _LowLatencyHandler con TCP_NODELAY)        │
    └─────────────────────────────────────────────────────────────────────┘

PUNTO DE ENTRADA EN LA ARQUITECTURA
    `python backend/app/main.py` → bloque `if __name__ == "__main__"` →
    create_app() → app.run(). El `finally` del main hace el apagado ordenado
    (telemetría, keepalive, storage, métricas, executor, go2rtc).

QUÉ PROCESOS DEL SISTEMA USAN ESTE MÓDULO
    Ninguno lo importa: es la RAÍZ. Todos los demás módulos son cableados
    aquí. Para verificar qué blueprints/rutas cargaron en producción, consultar
    `GET /api/v1/health` (campo `blueprints`) — el registro es best-effort y un
    fallo de import se loguea como "Error registrando '<name>'" sin abortar.
================================================================================
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

    # Recargar credenciales del notificador (token/chats) tras el bootstrap.
    #
    # NOTA (unificación #13): NO se suscribe el notifier al EventManager. El
    # "modo activo" legacy enviaba Telegram saltándose las NotificationPreference
    # por usuario y usando la config GLOBAL (telegram_chat_ids/notify_*), lo que
    # generaba un SEGUNDO camino de envío que contradecía al router (a veces
    # llegaban alertas que el usuario había desactivado, y con dos cooldowns
    # distintos). Ahora hay UN SOLO camino: evento → NotificationRouter, que lee
    # las preferencias por usuario (canal 'telegram') y entrega vía
    # TelegramNotifier.send_message (modo pasivo). El cooldown vive solo en el
    # router. UserTelegramChat es la única fuente de destinos.
    try:
        from backend.app.notifications.telegram_notifier import telegram_notifier
        telegram_notifier.reload_config()
        logger.info("TelegramNotifier recargado (modo pasivo; lo invoca el router)")
    except Exception as e:
        logger.error(f"Error recargando TelegramNotifier: {e}")


# ============================================================================
# BLUEPRINTS
# ============================================================================

def register_blueprints(app):
    """
    Registra TODOS los blueprints de la API REST (Paso 14 del arranque).

    Cada blueprint se importa y registra vía `safe_register()`: si su import
    falla (dependencia ausente, error de sintaxis) se loguea "Error registrando
    '<name>'" y se continúa con el resto — el registro es BEST-EFFORT. Por eso,
    si una ruta falta en producción, hay que mirar el log de arranque, no asumir
    que la ruta no existe.

    Outputs:
        list[str] con los nombres de blueprints cargados con éxito; se expone
        en `GET /api/v1/health` (campo `blueprints`) para diagnóstico.

    Nota: el blueprint `ws_bp` es solo un placeholder; las rutas WebSocket
    reales se enlazan aparte en create_app() vía `sock.init_app(app)`.
    """

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
    """
    FACTORY de la aplicación — Etapa única del Pipeline de inicio (#1).

    Construye el objeto Flask y ejecuta, EN ORDEN, todo el arranque descrito en
    el docstring del módulo. Cada subsistema de fondo se inicia en su propio
    try/except: un fallo aislado (p.ej. go2rtc ausente) se loguea pero NO impide
    que el servidor HTTP quede operativo — el sistema degrada, no cae.

    Inputs:
        config_name: reservado para perfiles de configuración (no usado hoy;
            la config real proviene del singleton settings que lee .env).

    Outputs:
        Flask app totalmente cableada y con los servicios de fondo ya lanzados
        (hilos daemon de cámaras, métricas, telemetría, keepalive, go2rtc).

    Excepciones:
        Propaga SOLO el fallo de `db_manager.init_db()` (sin BD no hay sistema);
        todo lo demás se captura y degrada.

    Llamado por:
        bloque `if __name__ == "__main__"` (al final de este archivo) y por los
        tests que necesitan una app real.

    Llama a (subsistemas clave, en orden):
        create_limiter() · db_manager.init_db() · jwt_blocklist.rehydrate_from_db()
        · _bootstrap_telegram_from_env() · get_container()
        · CameraManager().start_all_active() (hilo) · StorageManager().start()
        · stream_keepalive.start() · telemetry_recorder.start()
        · consistency_checker.start() · Go2RtcManager().start()
        · register_blueprints() · sock.init_app()
    """
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

    # PostgreSQL EMBEBIDO: en el .exe empaquetado arranca un servidor local
    # incluido en el bundle (no-op en desarrollo, que usa el PostgreSQL del .env).
    try:
        from backend.app.database.pg_embedded import embedded_pg
        if embedded_pg.enabled():
            embedded_pg.start()
    except Exception as e:
        logger.critical(f"Error arrancando PostgreSQL embebido: {e}")
        raise

    try:
        db_manager.init_db()
        logger.info("Base de datos inicializada correctamente")
    except Exception as e:
        logger.critical(f"Error inicializando DB: {e}")
        raise

    # Reconciliación de esquema: FCM/Firebase fue eliminado, pero una BD antigua
    # puede conservar las columnas mobile_devices.fcm_token / fcm_token_updated_at
    # como NOT NULL. create_all() NO altera tablas existentes, así que el INSERT
    # de un móvil nuevo (ya sin fcm_token) violaba la restricción NOT NULL → 500
    # al vincular el celular. Las quitamos si existen (idempotente, PostgreSQL).
    try:
        from sqlalchemy import text as _sql_text
        with db_manager.get_session() as _schema_s:
            _schema_s.execute(_sql_text(
                "ALTER TABLE mobile_devices DROP COLUMN IF EXISTS fcm_token"
            ))
            _schema_s.execute(_sql_text(
                "ALTER TABLE mobile_devices DROP COLUMN IF EXISTS fcm_token_updated_at"
            ))
        logger.info("Esquema reconciliado: columnas FCM legadas eliminadas si existían")
    except Exception as e:
        logger.warning(f"No se pudieron limpiar columnas legadas FCM: {e}")

    # Aplicar overrides de almacenamiento persistidos (ruta/cuota cambiados
    # desde la app de escritorio) para que sobrevivan a reinicios.
    try:
        settings.reload_storage_from_db()
    except Exception as e:
        logger.warning(f"No se pudieron cargar overrides de almacenamiento: {e}")

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

        # Precargar YOLO en segundo plano al ARRANCAR (no en la 1ª activación).
        # La 1ª vez con AI_FORMAT=onnx/openvino exporta el modelo (~30-90s en
        # CPU); hacerlo aquí evita que el primer POST /ai/<id>/activate se quede
        # colgado esperando ese export. Idempotente y best-effort.
        def warmup_ai():
            try:
                time.sleep(2.0)  # dejar que el arranque HTTP termine primero
                from backend.app.processing.ai.model_pool import YLOModelPool
                YLOModelPool().warmup()
            except Exception as e:
                logger.warning(f"Warmup IA al arranque falló (no crítico): {e}")

        threading.Thread(target=warmup_ai, daemon=True, name="AIWarmupBoot").start()

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

        # ================== Keep-alive de streams ==================
        # Modo DINÁMICO: mantiene caliente 1 stream por cámara registrada en la
        # BD (mono → cam_X_medium, dual → cam_X_l1_medium), reconciliando cada
        # 15s → registrar una cámara la mantiene caliente sola. STREAM_KEEPALIVE
        # (env) se conserva como lista EXTRA opcional que se fusiona.
        try:
            if getattr(settings, "GO2RTC_ENABLED", True):
                from backend.app.streaming.stream_keepalive import stream_keepalive
                _port = getattr(settings, "GO2RTC_RTSP_PORT", 8554)
                _ka_extra = (getattr(settings, "STREAM_KEEPALIVE", "") or "").split(",")
                stream_keepalive.start(
                    rtsp_base=f"rtsp://127.0.0.1:{_port}", env_extra=_ka_extra)
                app.stream_keepalive = stream_keepalive
        except Exception as e:
            logger.error(f"Error iniciando keep-alive de streams: {e}")

        # ================== Telemetría integrada ==================
        # Muestrea todos los módulos en uso y guarda a disco fila-a-fila
        # (sobrevive crash/Ctrl-C). Para que la revise después.
        try:
            if getattr(settings, "TELEMETRY_ENABLED", True):
                from backend.app.infrastructure.telemetry import telemetry_recorder
                telemetry_recorder.start(
                    interval=getattr(settings, "TELEMETRY_INTERVAL", 2.0),
                    out_dir=getattr(settings, "TELEMETRY_PATH", "") or "",
                )
                app.telemetry_recorder = telemetry_recorder
        except Exception as e:
            logger.error(f"Error iniciando telemetría: {e}")

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

        # ================== go2rtc (capa de medios) ==================
        # Sirve el directo (WebRTC/RTSP/HLS) directo al cliente. Solo arranca
        # si GO2RTC_ENABLED=true; si está desactivado no hay directo (los
        # clientes mostrarán "sin stream disponible").
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

        # ===== Patch de baja latencia para la API HTTP =====
        # Por defecto los sockets TCP usan el algoritmo Nagle: agrupan chunks
        # pequeños hasta llenar un paquete o esperar ~40ms. Para respuestas
        # JSON pequeñas (la mayoría de la API) eso añade latencia perceptible.
        # Desactivando Nagle (TCP_NODELAY) cada respuesta se envía al instante.
        #
        # NOTA: el vídeo en vivo NO pasa por Flask (lo sirve go2rtc directo),
        # así que aquí NO limitamos SO_SNDBUF — hacerlo frenaría las descargas
        # grandes (grabaciones, snapshots) que sí pasan por este servidor.
        import socket as _socket
        from werkzeug.serving import WSGIRequestHandler

        class _LowLatencyHandler(WSGIRequestHandler):
            """WSGIRequestHandler con TCP_NODELAY para baja latencia de API."""
            def setup(self):
                try:
                    self.connection = self.request
                    self.connection.setsockopt(
                        _socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1
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
        # Telemetría primero: cierra el CSV/JSONL con su última muestra y fsync
        # (aunque el flush por fila ya garantiza que no se pierda nada antes).
        try:
            from backend.app.infrastructure.telemetry import telemetry_recorder
            telemetry_recorder.stop()
        except Exception as e:
            logger.error(f"Error deteniendo telemetría: {e}")

        try:
            from backend.app.streaming.stream_keepalive import stream_keepalive
            stream_keepalive.stop()
        except Exception as e:
            logger.error(f"Error deteniendo keep-alive: {e}")

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

        # Detener PostgreSQL embebido (solo en el .exe empaquetado)
        try:
            from backend.app.database.pg_embedded import embedded_pg
            embedded_pg.stop()
        except Exception as e:
            logger.error(f"Error deteniendo PostgreSQL embebido: {e}")

        # ================== NUEVO: Detener ConsistencyChecker ==================
        if hasattr(app, 'consistency_checker'):
            try:
                app.consistency_checker.stop()
                logger.info("ConsistencyChecker detenido")
            except Exception as e:
                logger.error(f"Error deteniendo ConsistencyChecker: {e}")