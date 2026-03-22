"""
Test de visualización de cámara - Muestra el stream en vivo usando OpenCV
Soporta RTSP directo o URL del servidor MJPEG si está corriendo
"""

import sys
import os
from pathlib import Path
import json
import cv2
import logging

# Configurar path
current_dir = Path(__file__).parent.absolute()
backend_dir = current_dir.parent.absolute()
project_dir = backend_dir.parent.absolute()
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

logging.basicConfig(level=logging.INFO)

def load_discovered_camera():
    """Carga primera cámara del archivo de descubrimiento si existe"""
    json_file = current_dir / "discovered_cameras.json"
    if json_file.exists():
        with open(json_file, "r") as f:
            cameras = json.load(f)
            if cameras:
                return cameras[0]
    return None

def main():
    print("=" * 60)
    print("📺 TEST: VISUALIZACIÓN DE STREAM")
    print("=" * 60)
    
    # Intentar cargar cámara descubierta
    discovered = load_discovered_camera()
    
    if discovered:
        print(f"Cámara encontrada en caché: {discovered.get('name', 'Desconocida')}")
        use_discovered = input("¿Usar esta cámara? (s/n): ").lower().strip() == 's'
        
        if use_discovered:
            rtsp_url = discovered.get('rtsp_url')
            if not rtsp_url:
                print("❌ La cámara no tiene URL RTSP disponible")
                return
        else:
            rtsp_url = input("Ingresa URL RTSP (ej: rtsp://192.168.1.100:554/stream1): ")
    else:
        print("No hay cámaras descubiertas. Ingresa manualmente:")
        rtsp_url = input("URL RTSP: ")
    
    if not rtsp_url:
        print("❌ URL requerida")
        return
    
    print(f"\nConectando a: {rtsp_url}")
    print("Presiona 'Q' para salir, 'S' para guardar snapshot\n")
    
    # Abrir stream
    cap = cv2.VideoCapture(rtsp_url)
    
    if not cap.isOpened():
        print("❌ No se pudo abrir el stream")
        print("   Verifica:")
        print("   • La URL es correcta")
        print("   • Usuario/contraseña (si requiere auth)")
        print("   • La cámara está online")
        return
    
    # Obtener info del stream
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"✅ Stream abierto: {width}x{height} @ {fps}fps\n")
    
    frame_count = 0
    
    try:
        while True:
            ret, frame = cap.read()
            
            if not ret:
                print("⚠️  Pérdida de conexión, reintentando...")
                # Reintentar conexión
                cap.release()
                cap = cv2.VideoCapture(rtsp_url)
                continue
            
            frame_count += 1
            
            # Info en pantalla
            cv2.putText(frame, f"Frames: {frame_count}", (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame, "Q:Salir  S:Foto", (10, 60),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            
            cv2.imshow("Stream de Cámara - Test", frame)
            
            key = cv2.waitKey(1) & 0xFF
            
            if key == ord('q'):
                break
            elif key == ord('s'):
                # Guardar snapshot
                snapshot_dir = current_dir / "snapshots"
                snapshot_dir.mkdir(exist_ok=True)
                filename = f"snapshot_{frame_count}.jpg"
                filepath = snapshot_dir / filename
                cv2.imwrite(str(filepath), frame)
                print(f"💾 Foto guardada: {filepath}")
    
    except KeyboardInterrupt:
        print("\n⛔ Interrumpido por usuario")
    
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print(f"\n✅ Stream cerrado. Total frames recibidos: {frame_count}")

if __name__ == "__main__":
    main()