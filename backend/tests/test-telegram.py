"""
Script de integración: YOLO + Telegram
Detecta objetos en img.avif y envía notificación real a Telegram.

PREPARACIÓN REQUERIDA:
1. Crear bot con @BotFather en Telegram
2. Copiar el token (ej: 123456789:ABCdefGHIjklMNOpqrSTU)
3. Obtener tu Chat ID (habla con @userinfobot o @getidsbot)
4. Reemplazar las variables abajo
"""

import sys
import os
from pathlib import Path

# Configurar paths
current_dir = Path(__file__).parent.absolute()      # backend/tests/
backend_dir = current_dir.parent.absolute()         # backend/
project_dir = backend_dir.parent.absolute()         # sistema-videovigilancia/

# IMPORTANTE: Agregar la RAÍZ del proyecto
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

# =============================================================================
# CONFIGURACIÓN OBLIGATORIA - REEMPLAZA ESTOS VALORES
# =============================================================================
BOT_TOKEN = "8520537600:AAG2LQoESqyRZJsNr_WY3Y1fGCvvHFlFzvE"  # Tu token actual
CHAT_ID = "1383506337"  # Tu Chat ID de @userinfobot
# =============================================================================

if BOT_TOKEN == "TU_BOT_TOKEN_AQUI" or CHAT_ID == "TU_CHAT_ID_AQUI":
    print("❌ ERROR: Debes configurar BOT_TOKEN y CHAT_ID en el script")
    print("1. Habla con @BotFather en Telegram para crear un bot")
    print("2. Habla con @userinfobot para obtener tu Chat ID")
    print("3. Edita este archivo y reemplaza las variables")
    sys.exit(1)

import cv2
import numpy as np
import logging
from PIL import Image
from datetime import datetime
from collections import defaultdict

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Imports del sistema
from backend.app.database.connection import db_manager
from backend.app.database.models import SystemConfig
from backend.app.events.event_manager import EventData
from backend.app.notifications.telegram_notifier import TelegramNotifier
from backend.app.processing.ai.yolo_detector import YOLODetector

def load_image(image_path: str) -> np.ndarray:
    """Carga imagen (soporta AVIF, JPG, PNG, etc.)"""
    frame = cv2.imread(str(image_path))
    if frame is None:
        try:
            from PIL import Image
            img = Image.open(image_path)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            frame_rgb = np.array(img)
            frame = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        except Exception as e:
            print(f"Error cargando imagen: {e}")
            sys.exit(1)
    return frame

def setup_database():
    """Inicializa DB y configura Telegram"""
    print("🗄️  Inicializando base de datos...")
    
    db_manager.init_db()
    
    config_updates = {
        "telegram_bot_token": BOT_TOKEN,
        "telegram_chat_id": CHAT_ID,
        "telegram_enabled": "true",
        "notify_person": "true",
        "notify_vehicle": "true",
    }
    
    with db_manager.get_session() as session:
        for key, value in config_updates.items():
            existing = session.query(SystemConfig).filter_by(key=key).first()
            if existing:
                existing.value = value
                print(f"   Actualizado: {key}")
            else:
                new_config = SystemConfig(key=key, value=value)
                session.add(new_config)
                print(f"   Creado: {key}")
        session.commit()
    
    print("✅ Configuración guardada en BD")

def test_yolo_detection():
    """Ejecuta YOLO en la imagen"""
    # Buscar imagen en la carpeta tests (img.avif o img2.jpg)
    for img_name in ["img5.jpg", "img.jpg", "test.jpg"]:
        image_path = current_dir / img_name
        if image_path.exists():
            break
    else:
        print(f"❌ No se encontró imagen en: {current_dir}")
        print("   Coloca una imagen llamada img.avif, img2.jpg o similar")
        sys.exit(1)
    
    print(f"🖼️  Cargando: {image_path.name}")
    frame = load_image(str(image_path))
    print(f"   Dimensiones: {frame.shape[1]}x{frame.shape[0]}")
    
    print("🧠 Inicializando YOLO...")
    detector = YOLODetector(model_path="yolov8n.pt", confidence=0.45)
    
    print("🔍 Detectando objetos...")
    detections = detector.detect(frame)
    
    if not detections:
        print("⚠️  No se detectaron personas o vehículos")
        return None, frame, None
    
    print(f"✅ Detectados {len(detections)} objetos:")
    for i, det in enumerate(detections, 1):
        print(f"   {i}. {det.class_name} ({det.confidence:.1%})")
    
    # Dibujar detecciones
    annotated = detector.draw_detections(frame, detections)
    
    return detections, frame, annotated

def send_telegram_notification(detections, annotated_frame):
    """Envía notificación a Telegram"""
    print("\n📱 Preparando notificación...")
    
    # ✅ CORREGIDO: Guardar en backend/recordings/ (ruta que telegram_notifier permite)
    # El notifier valida rutas desde backend/app/notifications/ → sube 2 niveles → backend/recordings/
    snapshot_dir = backend_dir / "recordings" / "snapshots" / "test_temp"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = snapshot_dir / f"detection_{timestamp}.jpg"
    cv2.imwrite(str(snapshot_path), annotated_frame)
    print(f"💾 Snapshot: {snapshot_path}")

    # Agrupar por clase
    grouped = defaultdict(list)
    for d in detections:
        grouped[d.class_name].append(d)
    
    # Enviar una notificación por cada clase detectada
    for class_name, class_detections in grouped.items():
        count = len(class_detections)
        best_conf = max(d.confidence for d in class_detections)
        
        metadata = {
            "count": count,
            "snapshot_path": str(snapshot_path)
        }
        
        event_data = EventData(
            event_type=class_name,
            camera_id=999,
            camera_name="Cámara de Prueba",
            timestamp=datetime.now().timestamp(),
            confidence=best_conf,
            frame=annotated_frame,
            metadata=metadata
        )
        
        print(f"\n📤 Enviando: {class_name} (x{count})...")
        
        notifier = TelegramNotifier()
        notifier.reload_config()
        success = notifier.send_notification(event_data)
        
        if success:
            print(f"✅ Enviado: {class_name}")
        else:
            print(f"❌ Falló: {class_name}")

def main():
    print("=" * 60)
    print("TEST: YOLO + TELEGRAM")
    print("=" * 60)
    
    setup_database()
    
    detections, original_frame, annotated_frame = test_yolo_detection()
    
    if detections is None:
        print("\n⚠️  No hay detecciones para enviar")
        return
    
    send_telegram_notification(detections, annotated_frame)
    
    print("\n" + "=" * 60)
    print("✅ TEST COMPLETADO - Revisa Telegram")
    print("=" * 60)

if __name__ == "__main__":
    main()