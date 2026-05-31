"""
Script de prueba integral del backend NVR.
Ejecuta: python todo.py

Funciones:
1. Inicia backend Flask en background
2. Detecta cámaras ONVIF automáticamente
3. Muestra ventana OpenCV con stream en vivo
4. Control PTZ interactivo (WASD + QE)
5. Activa IA (YOLO) por 5 minutos
6. Envía alertas Telegram (configuración automática)
"""
import sys
import os
import threading
import time
import cv2
import logging
import shutil
from datetime import datetime, timedelta

# CRITICAL: Forzar modo desarrollo ANTES de cualquier import de backend
os.environ['FLASK_ENV'] = 'development'

# Setup paths
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.join(current_dir, '..', 'app')
project_root = os.path.join(current_dir, '..', '..')
sys.path.insert(0, os.path.abspath(backend_dir))
sys.path.insert(0, os.path.abspath(project_root))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def reset_database():
    """Elimina la base de datos si existe para evitar errores de schema."""
    db_path = os.path.join(project_root, 'data', 'surveillance.db')
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
            print(f"🗑️  Base de datos anterior eliminada: {db_path}")
        except Exception as e:
            print(f"⚠️  No se pudo eliminar BD: {e}")

# Resetear BD antes de importar modelos (evita "no such column: owner_id")
reset_database()

# Imports del sistema (después del reset)
from backend.app.cameras.onvif_discovery import ONVIFDiscovery
from backend.app.cameras.camera_manager import CameraManager
from backend.app.cameras.ptz_controller import PTZController
from backend.app.services.ai_service import AIService
from backend.app.events.event_manager import EventManager, EventData, event_manager
from backend.app.database.connection import db_manager
from backend.app.database.models import Camera, SystemConfig
from backend.app.container import get_container
from backend.app.config import settings
from backend.app.main import create_app

# Flask app
flask_app = None

def start_flask():
    """Inicia Flask en thread background"""
    global flask_app
    flask_app = create_app()
    flask_app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

def setup_telegram():
    """Configura Telegram automáticamente (SIN pedir input)"""
    print("\n" + "="*60)
    print("CONFIGURACIÓN TELEGRAM (AUTO)")
    print("="*60)
    
    # Valores pre-configurados (cámbialos si es necesario)
    token = "8520537600:AAG2LQoESqyRZJsNr_WY3Y1fGCvvHFlFzvE"
    chat_id = "1383506337"
    
    # Guardar en DB
    with db_manager.get_session() as session:
        configs = {
            "telegram_bot_token": token,
            "telegram_chat_id": chat_id,
            "telegram_enabled": "true",
            "notify_person": "true",
            "notify_vehicle": "true",
            "notify_motion": "true"
        }
        
        for key, value in configs.items():
            config = session.query(SystemConfig).filter_by(key=key).first()
            if config:
                config.value = value
            else:
                config = SystemConfig(key=key, value=value)
                session.add(config)
        
        session.commit()
    
    print("✅ Configuración Telegram aplicada automáticamente")
    return token, chat_id

def discover_and_setup_camera():
    """Detecta cámara ONVIF o usa IP manual"""
    print("\n🔍 Buscando cámaras ONVIF en la red...")
    discovery = ONVIFDiscovery()
    devices = discovery.discover(timeout=10)
    
    if devices:
        print(f"✅ Encontradas {len(devices)} cámaras:")
        for i, dev in enumerate(devices, 1):
            print(f"  {i}. {dev['name']} ({dev['ip_address']})")
        
        choice = input("\nSelecciona número (o ENTER para la primera): ").strip()
        if not choice:
            choice = "1"
        
        try:
            idx = int(choice) - 1
            selected = devices[idx]
        except:
            selected = devices[0]
    else:
        print("❌ No se encontraron cámaras automáticamente")
        use_manual = input("¿Deseas agregar manualmente? (s/n): ").lower()
        if use_manual != 's':
            return None
            
        ip = input("IP de la cámara: ").strip()
        rtsp = input("URL RTSP (ej: rtsp://admin:pass@ip:554/stream1): ").strip()
        user = input("Usuario ONVIF: ").strip()
        pwd = input("Contraseña: ").strip()
        
        selected = {
            "name": f"Cámara Manual {ip}",
            "ip_address": ip,
            "rtsp_url": rtsp,
            "onvif_url": f"http://{ip}:80/onvif/device_service",
            "username": user,
            "password": pwd,
            "profile_token": "",  # ✅ AGREGAR ESTO
            "has_ptz": True,
            "has_audio": False,
            "has_leds": False,
            "is_dual_lens": False,
            "resolution_width": 1920,
            "resolution_height": 1080,
            "fps": 15
        }
    
    # Guardar en BD
    with db_manager.get_session() as session:
        # Verificar si ya existe por IP
        existing = session.query(Camera).filter_by(ip_address=selected['ip_address']).first()
        
        if existing:
            print(f"⚠️  Cámara ya existe en BD (ID: {existing.id})")
            camera = existing
            # Actualizar datos
            camera.rtsp_url = selected['rtsp_url']
            camera.username = selected.get('username')
            camera.password = selected.get('password')
            camera.has_ptz = selected.get('has_ptz', False)
        else:
            camera = Camera(
                name=selected['name'],
                ip_address=selected['ip_address'],
                rtsp_url=selected['rtsp_url'],
                onvif_url=selected.get('onvif_url'),
                username=selected.get('username'),
                password=selected.get('password'),
                is_active=True,
                has_ptz=selected.get('has_ptz', False),
                has_ai=True,  # Habilitar IA por defecto
                has_leds=selected.get('has_leds', False),
                has_audio=selected.get('has_audio', False),
                is_dual_lens=selected.get('is_dual_lens', False),
                resolution_width=selected.get('resolution_width', 1920),
                resolution_height=selected.get('resolution_height', 1080),
                fps=selected.get('fps', 15)
            )
            session.add(camera)
            session.flush()
            print(f"✅ Cámara creada en BD (ID: {camera.id})")
        
        session.expunge(camera)
        return camera

def test_ptz_control(camera):
    """Prueba PTZ interactivo"""
    if not camera.has_ptz:
        print("⚠️  La cámara no tiene PTZ habilitado")
        return None
    
    print("🎮 Probando control PTZ...")
    try:
        ptz = PTZController(camera)
        if ptz.is_supported():
            print("✅ PTZ conectado correctamente")
            return ptz
        else:
            print("❌ PTZ no disponible (verificar credenciales ONVIF)")
            return None
    except Exception as e:
        print(f"❌ Error PTZ: {e}")
        return None

def main():
    print("="*60)
    print("SISTEMA NVR - MODO PRUEBA INTEGRAL")
    print("="*60)
    print("Este script probará:")
    print("  1. Backend Flask (iniciado automáticamente)")
    print("  2. Descubrimiento ONVIF de cámaras")
    print("  3. Stream de video en ventana OpenCV")
    print("  4. Control PTZ (WASD: movimiento, Q/E: zoom)")
    print("  5. IA (YOLO) activa por 5 minutos")
    print("  6. Alertas Telegram al detectar personas/vehículos")
    print("="*60)
    
    try:
        # 1. Setup Telegram
        setup_telegram()
        
        # 2. Iniciar Flask en background
        print("\n🚀 Iniciando servidor Flask en http://localhost:5000 ...")
        flask_thread = threading.Thread(target=start_flask, daemon=True)
        flask_thread.start()
        time.sleep(3)  # Esperar que inicie
        
        # 3. La BD ya se inicializó automáticamente al importar db_manager
        # (gracias a FLASK_ENV=development y el reset inicial)
        
        # 4. Descubrir cámara
        camera = discover_and_setup_camera()
        if not camera:
            print("❌ No se pudo configurar cámara. Saliendo.")
            return
        
        # 5. Iniciar CameraManager
        print("\n📹 Iniciando captura de video...")
        camera_manager = CameraManager()
        
        # Obtener cámara fresca de BD
        with db_manager.get_session() as session:
            camera_fresh = session.get(Camera, camera.id)
            if not camera_fresh:
                print("❌ Error: No se pudo recuperar cámara de BD")
                return
            camera_manager.start_camera(camera_fresh)
        
        # Esperar que inicie FFmpeg
        time.sleep(2)
        
        distributor = camera_manager.get_distributor(camera.id)
        buffer = camera_manager.get_buffer(camera.id)
        
        if not distributor or not buffer:
            print("❌ Error iniciando stream")
            return
        
        # 6. Configurar PTZ
        ptz_controller = test_ptz_control(camera)
        
        # 7. Iniciar IA por 5 minutos
        print("\n🤖 Iniciando IA (YOLO) - Será activa por 5 minutos...")
        ai_service = AIService(camera_manager)
        
        # Contador de detecciones para Telegram
        last_alert_time = {}
        alert_cooldown = 30  # segundos entre alertas del mismo tipo
        
        def on_detection(cam_id, event_type, class_name, confidence, metadata):
            """Callback cuando YOLO detecta algo"""
            print(f"🎯 Detección: {class_name} ({confidence:.0%})")
            
            # Enviar Telegram con cooldown
            current_time = time.time()
            key = f"{class_name}"
            
            if key not in last_alert_time or (current_time - last_alert_time[key]) > alert_cooldown:
                try:
                    from backend.app.notifications.telegram_notifier import telegram_notifier
                    
                    # Forzar reload de config
                    telegram_notifier.reload_config()
                    
                    if telegram_notifier._enabled:
                        msg = (f"🚨 Alerta NVR\n\n"
                               f"Cámara: {cam_id}\n"
                               f"Detectado: {class_name}\n"
                               f"Confianza: {confidence:.0%}\n"
                               f"Hora: {datetime.now().strftime('%H:%M:%S')}")
                        telegram_notifier._send_message(msg)
                        print(f"📤 Alerta Telegram enviada: {class_name}")
                        last_alert_time[key] = current_time
                except Exception as e:
                    print(f"⚠️  Error enviando Telegram: {e}")
        
        ai_service.set_event_callback(on_detection)
        ai_service.activate_ai(camera.id, mode="high_quality")
        
        ia_start_time = time.time()
        ia_duration = 300  # 5 minutos
        
        # 8. Loop principal OpenCV
        print("\n" + "="*60)
        print("VENTANA DE VIDEO INICIADA")
        print("="*60)
        print("Controles:")
        print("  W/A/S/D - Mover cámara (PTZ)")
        print("  Q/E     - Zoom out/in")
        print("  ESPACIO - Detener movimiento")
        print("  R       - Recargar configuración Telegram")
        print("  ESC     - Salir")
        print("="*60)
        
        window_name = f"NVR Test - {camera.name}"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, 800, 600)
        
        ptz_moving = False
        
        # 🔧 FIX: Inicializar remaining antes del loop para evitar UnboundLocalError
        remaining = ia_duration
        
        try:
            while True:
                # Obtener frame más reciente
                frame_data = buffer.get_latest()
                
                if frame_data:
                    # 🔧 FIX: Crear copia mutable del frame para poder dibujar sobre él
                    frame = frame_data.frame.copy()
                    
                    # Calcular tiempo restante de IA
                    elapsed = time.time() - ia_start_time
                    remaining = max(0, ia_duration - elapsed)
                    
                    # Info en pantalla
                    status_text = [
                        f"Cámara: {camera.name}",
                        f"IA: {'ACTIVA' if remaining > 0 else 'INACTIVA'} ({int(remaining)}s)",
                        f"Detectando: Personas, Vehiculos",
                        "PTZ: WASD+Q/E | ESC para salir"
                    ]
                    
                    y_offset = 30
                    for text in status_text:
                        cv2.putText(frame, text, (10, y_offset), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                        y_offset += 30
                    
                    # Mostrar frame
                    cv2.imshow(window_name, frame)
                
                # Capturar tecla (espera 33ms = ~30fps)
                key = cv2.waitKey(33) & 0xFF
                
                # Manejo PTZ
                if ptz_controller and ptz_controller.is_supported():
                    if key == ord('w'):
                        ptz_controller.move("up", speed=0.5)
                        ptz_moving = True
                    elif key == ord('s'):
                        ptz_controller.move("down", speed=0.5)
                        ptz_moving = True
                    elif key == ord('a'):
                        ptz_controller.move("left", speed=0.5)
                        ptz_moving = True
                    elif key == ord('d'):
                        ptz_controller.move("right", speed=0.5)
                        ptz_moving = True
                    elif key == ord('q'):
                        ptz_controller.move("zoom_out", speed=0.3)
                        ptz_moving = True
                    elif key == ord('e'):
                        ptz_controller.move("zoom_in", speed=0.3)
                        ptz_moving = True
                    elif key == ord(' '):  # Espacio = stop
                        ptz_controller.stop()
                        ptz_moving = False
                
                # Recargar Telegram
                if key == ord('r'):
                    print("🔄 Recargando configuración Telegram...")
                    try:
                        from backend.app.notifications.telegram_notifier import telegram_notifier
                        telegram_notifier.reload_config()
                        print("✅ Config recargada")
                    except Exception as e:
                        print(f"⚠️  Error recargando: {e}")
                
                # Salir
                if key == 27:  # ESC
                    break
                
                # Verificar si terminó IA
                if remaining <= 0 and camera.id in ai_service._schedulers:
                    print("⏰ Tiempo de IA finalizado. Desactivando...")
                    ai_service.deactivate_ai(camera.id)
        
        except KeyboardInterrupt:
            print("\n⚠️ Interrupción manual")
        
        finally:
            # Limpieza
            print("\n🧹 Limpiando recursos...")
            cv2.destroyAllWindows()
            
            if ptz_controller and ptz_moving:
                ptz_controller.stop()
            
            if camera.id in ai_service._schedulers:
                ai_service.deactivate_ai(camera.id)
            
            camera_manager.stop_camera(camera.id)
            
            print("✅ Prueba finalizada correctamente")
            
    except Exception as e:
        print(f"\n❌ Error fatal: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        print("\nPresiona ENTER para salir...")
        input()

if __name__ == "__main__":
    main()