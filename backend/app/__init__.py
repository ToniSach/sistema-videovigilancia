"""
Paquete raíz del backend NVR/VMS (Flask, proceso único).

Agrupa todos los subsistemas del servidor de videovigilancia:
    api/ ........... capa HTTP (blueprints REST + WebSocket + middleware)
    services/ ...... lógica de negocio (entre rutas y repositorios)
    database/ ...... modelos SQLAlchemy + repositorios + conexión
    cameras/ ....... ciclo de vida de cámaras, ONVIF, PTZ, audio, LED
    streaming/ ..... go2rtc (capa de medios), WebRTC, keepalive
    processing/ .... IA (YOLOv8) y detección de movimiento
    recording/ ..... grabación continua y clips de evento
    storage/ ....... consistencia de almacenamiento
    events/ ........ bus pub/sub de eventos (EventManager)
    notifications/ . Telegram + WebSocket push
    core/ .......... utilidades base (seguridad, JWT, executor, hardware)
    infrastructure/  métricas y telemetría
    container.py ... contenedor de inyección de dependencias (DI)
    config.py ...... configuración global (lee .env)
    main.py ........ punto de entrada (create_app + arranque)

El arranque y el flujo completo están documentados en backend/app/main.py.
"""
