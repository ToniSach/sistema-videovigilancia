#!/usr/bin/env python3
"""
Script de Integración - Sistema de Videovigilancia
"""

import sys
import os
import time
import logging
import argparse
import threading
from datetime import datetime
import numpy as np

# FIX UTF-8 para Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    os.environ["PYTHONIOENCODING"] = "utf-8"

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../"))
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f'test_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log', encoding='utf-8')
    ]
)
logger = logging.getLogger("IntegrationTest")

TELEGRAM_CONFIG = {
    "telegram_bot_token": "secreto",
    "telegram_chat_id": "secreto",
    "telegram_enabled": "true",
    "notify_person": "true",
    "notify_vehicle": "true",
    "notify_motion": "true"
}

class StreamMonitor:
    """
    Monitor con detección de frames DUPLICADOS usando frame_id.
    """
    def __init__(self):
        self._frames_received = 0
        self._unique_frames = 0
        self._last_frame_time = 0
        self._last_timestamp = -1
        self._last_hash = None
        self._frame = None
        self._lock = threading.Lock()
        self._frame_ids = set()  # Tracking de IDs únicos
    
    def update(self, frame_data):
        """Procesa frame del callback con detección por ID."""
        with self._lock:
            self._frames_received += 1
            now = time.time()
            
            if frame_data is None or frame_data.frame is None:
                return
            
            # Detectar por frame_id (más confiable que hash)
            frame_id = getattr(frame_data, 'frame_id', 0)
            current_timestamp = getattr(frame_data, 'timestamp', 0)
            
            is_new = False
            
            if frame_id > 0:
                # Usar ID del buffer (método confiable)
                if frame_id not in self._frame_ids:
                    self._frame_ids.add(frame_id)
                    is_new = True
                    if len(self._frame_ids) > 1000:  # Limpiar set si crece mucho
                        self._frame_ids = set(list(self._frame_ids)[-500:])
            else:
                # Fallback a comparación de timestamp + hash
                current_hash = hash(frame_data.frame.tobytes()[:1000])
                if current_hash != self._last_hash or abs(current_timestamp - self._last_timestamp) > 0.5:
                    is_new = True
                    self._last_hash = current_hash
            
            if is_new:
                self._unique_frames += 1
                self._last_timestamp = current_timestamp
                self._last_frame_time = now
                self._frame = np.copy(frame_data.frame)

    def get_status(self):
        with self._lock:
            seconds_stale = time.time() - self._last_frame_time
            duplicates = self._frames_received - self._unique_frames
            
            return {
                'total_callbacks': self._frames_received,
                'unique_frames': self._unique_frames,
                'duplicates': duplicates,
                'seconds_since_new_frame': seconds_stale,  # Compatibilidad con código existente
                'seconds_since_new': seconds_stale,
                'is_frozen': seconds_stale > 2.0 or self._unique_frames == 0,
                'is_duplicate_storm': duplicates > self._unique_frames * 2,  # Detección de storm
                'frame': self._frame,
                'last_timestamp': self._last_timestamp
            }

def setup_telegram_config():
    from backend.app.database.connection import db_manager
    from backend.app.database.models import SystemConfig
    
    with db_manager.get_session() as session:
        session.query(SystemConfig).filter(
            SystemConfig.key.in_(TELEGRAM_CONFIG.keys())
        ).delete(synchronize_session=False)
        
        for key, value in TELEGRAM_CONFIG.items():
            session.add(SystemConfig(key=key, value=value))
        session.commit()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--show-local', action='store_true')
    args = parser.parse_args()

    print("=" * 70)
    print("SISTEMA DE VIDEOVIGILANCIA - TEST DE INTEGRACION")
    print("=" * 70)
    
    # Inicialización
    logger.info("PASO 1: Base de datos...")
    from backend.app.database.connection import db_manager
    from backend.app.database.repositories.camera_repository import CameraRepository
    from backend.app.database.models import Camera
    setup_telegram_config()
    
    # Telegram
    logger.info("PASO 2: Telegram...")
    from backend.app.notifications.telegram_notifier import telegram_notifier
    telegram_notifier.reload_config()
    telegram_notifier.test_connection()
    logger.info("[OK] Telegram activo - Chat ID: " + TELEGRAM_CONFIG['telegram_chat_id'])

    # ONVIF
    logger.info("PASO 3: ONVIF Discovery...")
    from backend.app.cameras.onvif_discovery import ONVIFDiscovery
    discovery = ONVIFDiscovery()
    devices = discovery.discover(timeout=10)
    if not devices:
        logger.error("No se encontraron camaras")
        return 1
    
    cam_data = devices[0]
    logger.info(f"[OK] Camara detectada: {cam_data['name']}")

    # BD
    repo = CameraRepository()
    existing = repo.get_by_ip(cam_data['ip_address'])
    if existing:
        camera = existing
        logger.info(f"Usando existente ID: {camera.id}")
    else:
        camera = Camera(
            name=cam_data.get('name', f"Cam-{cam_data['ip_address']}"),
            ip_address=cam_data['ip_address'],
            rtsp_url=cam_data['rtsp_url'],
            username=cam_data.get('username', 'admin'),
            password=cam_data.get('password', ''),
            is_active=True,
            has_ptz=cam_data.get('has_ptz', False),
            resolution_width=1920,
            resolution_height=1080,
            fps=15
        )
        camera = repo.create(camera)

    cam_id = camera.id

    # Streaming
    logger.info("PASO 4: Iniciando FFmpeg...")
    from backend.app.cameras.camera_manager import CameraManager
    cam_mgr = CameraManager()
    
    if not cam_mgr.start_camera(camera):
        logger.error("Fallo al iniciar camara")
        return 1
    
    time.sleep(3)
    worker = cam_mgr.get_worker(cam_id)
    if not worker or worker.get_status()["status"] != "running":
        logger.error("FFmpeg no corriendo")
        return 1
    
    logger.info("[OK] Stream iniciado")

    # Monitor y callback
    stop_event = threading.Event()
    monitor = StreamMonitor()
    
    if args.show_local:
        def on_frame(frame_data):
            if frame_data:
                monitor.update(frame_data)
            return not stop_event.is_set()
        
        distributor = cam_mgr.get_distributor(cam_id)
        distributor.register_consumer("local_viewer", on_frame)
        logger.info("[OK] Visualizador registrado")

    # IA y Telegram
    logger.info("PASO 5: Activando IA (YOLOv8) y notificaciones...")
    from backend.app.services.ai_service import AIService
    
    ai_svc = AIService(cam_mgr)
    success = ai_svc.activate_ai(cam_id, mode="low_cpu")
    
    if success:
        logger.info("[OK] IA activada - detectando personas y vehiculos")
        logger.info("[OK] Sistema listo para enviar alertas a Telegram")
    else:
        logger.error("[ERROR] No se pudo activar IA")

    # Loop principal
    if args.show_local:
        try:
            import cv2
            
            window_name = f"Cam {cam_id}: {camera.name}"
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(window_name, 1280, 720)
            
            last_status_log = time.time()
            alert_sent = False
            
            logger.info("=" * 70)
            logger.info("VENTANA ABIERTA - Presiona Q o ESC para detener")
            logger.info("Si la imagen esta congelada: verifica duplicados en el overlay")
            logger.info("=" * 70)
            
            while not stop_event.is_set():
                status = monitor.get_status()
                frame = status['frame']
                
                # Crear display
                if frame is not None:
                    display = frame.copy()
                    h, w = display.shape[:2]
                    
                    # Color segun estado
                    if status['is_duplicate_storm']:
                        # Alerta: Muchos duplicados - PROBLEMA DE PIPELINE
                        color = (0, 0, 255)  # Rojo
                        bg_color = (0, 0, 100)
                        estado = "DUPLICADOS!"
                    elif status['is_frozen']:
                        # Congelado
                        color = (0, 0, 255)
                        bg_color = (50, 50, 50)
                        estado = "CONGELADO"
                    else:
                        # OK
                        color = (0, 255, 0)
                        bg_color = (0, 100, 0)
                        estado = "ACTIVO"
                    
                    # Barra de estado
                    cv2.rectangle(display, (0,0), (w,80), bg_color, -1)
                    
                    # Info principal
                    line1 = f"{estado} | {camera.name} | Unicos: {status['unique_frames']} | Total: {status['total_callbacks']}"
                    cv2.putText(display, line1, (10,30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                    
                    # Info secundaria
                    dup_rate = (status['duplicates'] / max(status['total_callbacks'], 1)) * 100
                    line2 = f"Dup: {dup_rate:.1f}% | Stale: {status['seconds_since_new_frame']:.1f}s | TS: {status['last_timestamp']:.3f}"
                    cv2.putText(display, line2, (10,65), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 1)
                    
                    # Alerta visual de duplicados
                    if status['is_duplicate_storm']:
                        cv2.putText(display, "ALERTA: Frames duplicados detectados!", (50, h//2),
                                   cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0,0,255), 3)
                        cv2.putText(display, "El callback recibe el mismo frame repetidamente", (50, h//2 + 40),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200,200,200), 2)
                        if not alert_sent:
                            logger.error("ALERTA: Storm de frames duplicados detectado!")
                            alert_sent = True
                else:
                    display = np.zeros((720, 1280, 3), dtype=np.uint8)
                    cv2.putText(display, "Esperando primer frame...", (400, 360),
                               cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
                
                cv2.imshow(window_name, display)
                
                # Logging cada 3 segundos
                now = time.time()
                if now - last_status_log >= 3:
                    last_status_log = now
                    worker_status = worker.get_status()
                    ai_status = ai_svc.get_ai_status()
                    scheduler = ai_status.get('schedulers', {}).get(cam_id, {})
                    
                    # Logging corregido según solicitado
                    logger.info(f"PIPELINE: Unicos={status['unique_frames']} | "
                               f"TotalCB={status['total_callbacks']} | "
                               f"Dup={status['duplicates']}")
                    
                    # Si hay muchos duplicados, diagnosticar
                    if status['is_duplicate_storm']:
                        logger.error("DIAGNOSTICO: El distributor entrega frames duplicados!")
                        logger.error("Posible causa: FrameBuffer.get_latest() siempre retorna el mismo frame")
                
                # Teclas
                key = cv2.waitKey(30) & 0xFF
                if key in [ord('q'), ord('Q'), 27]:
                    stop_event.set()
                    break
                    
        except ImportError:
            logger.error("OpenCV no instalado")
        except Exception as e:
            logger.error(f"Error ventana: {e}", exc_info=True)
        finally:
            try:
                import cv2
                cv2.destroyAllWindows()
            except:
                pass
    else:
        # Modo consola con IA activa
        logger.info("Modo consola - IA activa, esperando detecciones...")
        try:
            while True:
                time.sleep(5)
                ai_status = ai_svc.get_ai_status()
                scheduler = ai_status.get('schedulers', {}).get(cam_id, {})
                detections = scheduler.get('detections', 0)
                worker_status = worker.get_status()
                
                logger.info(f"Status: {worker_status.get('status')} | Detecciones IA: {detections}")
                if detections > 0:
                    logger.info(f"[ALERTA] Se detectaron objetos! Revisar Telegram.")
        except KeyboardInterrupt:
            pass

    # Limpieza
    logger.info("Deteniendo sistema...")
    stop_event.set()
    
    if args.show_local:
        try:
            distributor = cam_mgr.get_distributor(cam_id)
            distributor.unregister_consumer("local_viewer")
        except:
            pass
    
    ai_svc.deactivate_ai(cam_id)
    cam_mgr.stop_camera(cam_id)
    telegram_notifier._enabled = False
    
    logger.info("Sistema detenido correctamente")
    return 0

if __name__ == "__main__":
    sys.exit(main())