#!/usr/bin/env python3
"""
Script de prueba integral para el sistema NVR.
Prueba: conexión RTSP/ONVIF, detección YOLO, notificaciones Telegram,
guardado de snapshots y visualización en tiempo real.
"""

import sys
import os
import logging
import time
import cv2
import signal
from pathlib import Path

# Añadir el directorio raíz al path (sube tres niveles hasta la raíz del proyecto)
root_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(root_dir))

# Configurar logging detallado (incluyendo DEBUG para ver todo)
logging.basicConfig(
    level=logging.DEBUG,  # Cambiar a DEBUG para más detalles
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# IMPORTS DEL BACKEND
# ============================================================================
from backend.app.cameras.camera_manager import CameraManager
from backend.app.cameras.onvif_discovery import ONVIFDiscovery
from backend.app.database.models import Camera, SystemConfig
from backend.app.database.connection import db_manager
from backend.app.services.camera_service import CameraService
from backend.app.services.ai_service import AIService
from backend.app.services.event_service import EventService
from backend.app.database.repositories.event_repository import EventRepository
from backend.app.events.event_manager import event_manager
from backend.app.config import settings

# Importamos el notificador para poder recargar configuración después
from backend.app.notifications.telegram_notifier import telegram_notifier

# ============================================================================
# CONFIGURACIÓN DE PRUEBA
# ============================================================================
CAMERA_IP = "192.168.1.8"          # Cambia por la IP de tu cámara
CAMERA_USER = "admin"               # Cambia si es necesario
CAMERA_PASS = "admin"               # Cambia si es necesario
TEST_DURATION = 6000                  # Segundos de prueba
SAVE_SNAPSHOTS = True               # Guardar snapshots en disco
SEND_TELEGRAM = True                # Enviar alertas por Telegram
SHOW_WINDOW = True                  # Mostrar ventana de video

# Telegram config (directo)
TELEGRAM_BOT_TOKEN = "8520537600:AAG2LQoESqyRZJsNr_WY3Y1fGCvvHFlFzvE"
TELEGRAM_CHAT_ID = "1383506337"

# ============================================================================
# VARIABLES GLOBALES
# ============================================================================
running = True
camera_id = None
camera_service = None
ai_service = None
event_service = None

def signal_handler(sig, frame):
    """Maneja Ctrl+C para detener limpiamente."""
    global running
    logger.info("Deteniendo prueba...")
    running = False

def configure_telegram_in_db():
    """Guarda la configuración de Telegram directamente en la base de datos."""
    try:
        with db_manager.get_session() as session:
            # Claves que vamos a insertar/actualizar
            keys_to_clear = [
                "telegram_enabled", "telegram_bot_token", "telegram_chat_id",
                "notify_person", "notify_vehicle", "notify_motion",
                "notify_offline", "notify_tampering"
            ]
            # Eliminar registros existentes con esas claves
            session.query(SystemConfig).filter(SystemConfig.key.in_(keys_to_clear)).delete()
            # Insertar nuevas configuraciones
            configs = [
                SystemConfig(key="telegram_enabled", value="true"),
                SystemConfig(key="telegram_bot_token", value=TELEGRAM_BOT_TOKEN),
                SystemConfig(key="telegram_chat_id", value=TELEGRAM_CHAT_ID),
                SystemConfig(key="notify_person", value="true"),
                SystemConfig(key="notify_vehicle", value="true"),
                SystemConfig(key="notify_motion", value="false"),
                SystemConfig(key="notify_offline", value="true"),
                SystemConfig(key="notify_tampering", value="true"),
            ]
            session.add_all(configs)
            session.commit()
            logger.info("✅ Configuración de Telegram guardada en base de datos")
    except Exception as e:
        logger.error(f"Error guardando configuración Telegram: {e}")

def setup_database():
    """Inicializa la base de datos y crea tablas si no existen."""
    try:
        db_manager.init_db()
        logger.info("✅ Base de datos inicializada")
    except Exception as e:
        logger.error(f"Error inicializando BD: {e}")

def find_or_create_camera():
    """Busca la cámara por IP o la crea en BD si no existe."""
    from backend.app.database.repositories.camera_repository import CameraRepository
    repo = CameraRepository()
    camera = repo.get_by_ip(CAMERA_IP)
    if camera:
        logger.info(f"📷 Cámara existente encontrada: ID {camera.id} - {camera.name}")
        return camera

    # Intentar descubrir ONVIF
    logger.info(f"🔍 Descubriendo cámara en {CAMERA_IP}...")
    discovery = ONVIFDiscovery()
    device_info = discovery.probe_single_ip(CAMERA_IP)
    if not device_info:
        # Fallback manual
        logger.warning("ONVIF no detectado, usando configuración manual")
        device_info = {
            "name": "Cámara China",
            "ip_address": CAMERA_IP,
            "rtsp_url": f"rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554/Streaming/Channels/101",
            "username": CAMERA_USER,
            "password": CAMERA_PASS,
            "manufacturer": "Generic",
            "model": "RTSP",
            "resolution_width": 1920,
            "resolution_height": 1080,
            "fps": 15,
            "connection_type": "manual"
        }

    # Crear objeto Camera
    camera = Camera(
        name=device_info.get("name", "Test Camera"),
        ip_address=device_info["ip_address"],
        rtsp_url=device_info["rtsp_url"],
        onvif_url=device_info.get("onvif_url", ""),
        username=device_info["username"],
        password=device_info["password"],
        profile_token=device_info.get("profile_token", ""),
        is_active=True,
        has_ai=True,
        has_ptz=device_info.get("has_ptz", False),
        resolution_width=device_info.get("resolution_width", 1920),
        resolution_height=device_info.get("resolution_height", 1080),
        fps=device_info.get("fps", 15),
        connection_type=device_info.get("connection_type", "onvif")
    )

    # Guardar en BD
    repo = CameraRepository()
    created = repo.create(camera)
    logger.info(f"✅ Cámara creada: ID {created.id}")
    return created

def on_detection(camera_id, event_type, class_name, confidence, frame, metadata=None):
    """Callback llamado cuando hay una detección.
       Solo loguea; el envío de Telegram lo maneja el notificador suscrito a EventManager."""
    logger.info(f"🔔 DETECCIÓN: {class_name} con {confidence:.2%} (cámara {camera_id})")
    if metadata and metadata.get("count", 1) > 1:
        logger.info(f"   ↳ {metadata['count']} objetos detectados")
        
def test_event_callback(event_data):
    """Callback de prueba para verificar que los eventos se publican correctamente."""
    logger.info(f"📢 EVENTO RECIBIDO EN TEST: {event_data.event_type} - cámara {event_data.camera_id}")

def view_stream(camera_manager, camera_id, duration=60):
    """Muestra el stream en tiempo real con detecciones dibujadas."""
    buffer = camera_manager.get_buffer(camera_id)
    if not buffer:
        logger.error("No se pudo obtener buffer de la cámara")
        return

    cv2.namedWindow("NVR Test - Presiona 'q' para salir", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("NVR Test - Presiona 'q' para salir", 960, 540)

    start_time = time.time()
    frame_count = 0

    while running and time.time() - start_time < duration:
        frame_data = buffer.get_latest()
        if frame_data and frame_data.frame is not None:
            frame = frame_data.frame.copy()
            frame_count += 1

            # Añadir info en pantalla
            cv2.putText(frame, f"Frame: {frame_count}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame, f"Time: {time.strftime('%H:%M:%S')}", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            # Mostrar ventana
            cv2.imshow("NVR Test - Presiona 'q' para salir", frame)

        # Esperar 1ms para eventos de teclado
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            logger.info("Usuario detuvo la prueba")
            break

    cv2.destroyAllWindows()
    logger.info(f"Ventana cerrada. Frames mostrados: {frame_count}")

def main():
    global camera_id, camera_service, ai_service, event_service, running

    # Configurar señal para Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)

    print("=" * 70)
    print("PRUEBA COMPLETA DEL SISTEMA NVR")
    print("=" * 70)

    # 1. Inicializar base de datos
    setup_database()

    # 2. Guardar configuración de Telegram en BD (para que el notificador la cargue)
    configure_telegram_in_db()

    # 3. Recargar configuración del notificador (para que use los nuevos valores)
    telegram_notifier.reload_config()

    # Verificar estado del notificador después de recargar
    logger.info(f"Telegram notifier enabled: {telegram_notifier._enabled}")
    logger.info(f"Telegram notify_types: {telegram_notifier._notify_types}")

    # Enviar mensaje de prueba para confirmar que funciona
    if telegram_notifier._enabled:
        logger.info("Enviando mensaje de prueba a Telegram...")
        telegram_notifier.test_connection()
    else:
        logger.warning("Telegram no está habilitado después de recargar configuración")

    # 4. Registrar un callback de prueba para ver si los eventos llegan
    event_manager.subscribe_all(test_event_callback)
    logger.info("Callback de prueba registrado en EventManager")

    # 5. Obtener o crear cámara
    camera = find_or_create_camera()
    camera_id = camera.id

    # 6. Inicializar CameraManager
    camera_manager = CameraManager()
    # Detener cualquier instancia previa de esta cámara (por si quedó corriendo)
    camera_manager.stop_camera(camera_id)

    # 7. Iniciar cámara (con MJPEG automático)
    if not camera_manager.start_camera(camera, register_mjpeg=True):
        logger.error("No se pudo iniciar la cámara")
        return

    logger.info(f"✅ Cámara {camera_id} iniciada")

    # 8. Obtener el distribuidor y registrar consumidor para IA
    distributor = camera_manager.get_distributor(camera_id)
    if not distributor:
        logger.error("No se pudo obtener el distribuidor")
        return

    # 9. Inicializar servicios necesarios
    from backend.app.container import get_container
    container = get_container()
    camera_service = container.get("camera_service")
    if not camera_service:
        from backend.app.database.repositories.camera_repository import CameraRepository
        from backend.app.cameras.onvif_discovery import ONVIFDiscovery
        camera_service = CameraService(CameraRepository(), camera_manager, ONVIFDiscovery())
        container.register("camera_service", camera_service)

    # 10. Inicializar AIService (con YOLO)
    ai_service = AIService(camera_manager)
    # Configurar callback para eventos (solo logging, el envío lo hace el notificador)
    ai_service.set_event_callback(on_detection)

    # 11. Activar IA en la cámara
    if not ai_service.activate_ai(camera_id, mode="low_cpu"):
        logger.error("No se pudo activar IA en la cámara")
    else:
        logger.info("✅ IA activada en modo low_cpu")

    # 12. Inicializar EventService (para guardar snapshots y notificaciones)
    event_repo = EventRepository()
    event_service = EventService(event_repo)

    # Esperar un poco para que los workers se estabilicen
    time.sleep(2)

    # 13. Mostrar ventana en tiempo real
    if SHOW_WINDOW:
        view_stream(camera_manager, camera_id, duration=TEST_DURATION)
    else:
        logger.info(f"Esperando {TEST_DURATION} segundos sin ventana...")
        time.sleep(TEST_DURATION)

    # 14. Resultados finales
    print("\n" + "=" * 70)
    print("RESUMEN DE PRUEBA")
    print("=" * 70)
    ai_status = ai_service.get_ai_status() if ai_service else {}
    try:
        if hasattr(event_service, 'get_stats'):
            stats = event_service.get_stats()
        else:
            raise AttributeError("get_stats not found")
    except Exception as e:
        logger.warning(f"Fallo al obtener estadísticas con EventService: {e}")
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        try:
            recent = event_repo.get_recent(now - timedelta(hours=24))
        except TypeError:
            recent = event_repo.get_all()
        total = len(recent)
        by_type = {}
        for ev in recent:
            by_type[ev.event_type] = by_type.get(ev.event_type, 0) + 1
        stats = {"total_24h": total, "by_type": by_type}
    
    print(f"IA activa en cámaras: {ai_status.get('active_count', 0)}")
    print(f"Eventos en últimas 24h: {stats.get('total_24h', 0)}")
    print(f" - Personas: {stats.get('by_type', {}).get('person', 0)}")
    print(f" - Vehículos: {stats.get('by_type', {}).get('vehicle', 0)}")
    print("=" * 70)

    # 15. Limpieza
    logger.info("Deteniendo servicios...")
    if ai_service:
        ai_service.deactivate_ai(camera_id)
    if camera_manager:
        camera_manager.stop_camera(camera_id)
    event_manager.shutdown()
    cv2.destroyAllWindows()
    logger.info("Prueba completada.")

if __name__ == "__main__":
    main()