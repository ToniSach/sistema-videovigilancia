"""
Script de prueba para YOLO Detector
Prueba de detección de objetos en img.avif
"""

import sys
import os
from pathlib import Path
import cv2
import numpy as np
import logging

# Configurar logging para ver output de YOLO
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Añadir el directorio 'backend' al path para poder importar desde app
# Esto asume que el script está en backend/tests/
current_dir = Path(__file__).parent.absolute()
backend_dir = current_dir.parent.absolute()
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Importar el detector
from app.processing.ai.yolo_detector import YOLODetector, Detection

def load_avif_image(image_path: str) -> np.ndarray:
    """
    Carga imagen AVIF. 
    Si OpenCV no soporta AVIF nativamente, usa PIL como fallback.
    """
    # Intentar con OpenCV primero
    frame = cv2.imread(image_path)
    
    if frame is None:
        print("OpenCV no pudo leer AVIF, usando PIL...")
        try:
            from PIL import Image
            img = Image.open(image_path)
            # Convertir a RGB si es necesario
            if img.mode != 'RGB':
                img = img.convert('RGB')
            # Convertir a numpy array (RGB)
            frame_rgb = np.array(img)
            # Convertir a BGR (formato de OpenCV)
            frame = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        except ImportError:
            print("ERROR: Instala Pillow para soportar AVIF: pip install Pillow")
            sys.exit(1)
        except Exception as e:
            print(f"ERROR al cargar imagen: {e}")
            sys.exit(1)
    
    return frame

def main():
    print("=" * 60)
    print("PRUEBA DE YOLO DETECTOR")
    print("=" * 60)
    
    # 1. Verificar que existe la imagen
    image_path = Path(__file__).parent / "img2.jpg"
    if not image_path.exists():
        print(f"ERROR: No se encontró la imagen en: {image_path}")
        print(f"Directorio actual: {os.getcwd()}")
        print(f"Contenido del directorio: {list(Path(__file__).parent.iterdir())}")
        return
    
    print(f"Imagen encontrada: {image_path}")
    
    # 2. Cargar imagen
    print("\nCargando imagen...")
    frame = load_avif_image(str(image_path))
    print(f"Dimensiones: {frame.shape}")
    print(f"Tipo de dato: {frame.dtype}")
    
    # 3. Inicializar detector
    print("\nInicializando YOLO (descargará yolov8n.pt si no existe)...")
    print("Esto puede tardar unos segundos la primera vez...")
    try:
        detector = YOLODetector(model_path="yolov8n.pt", confidence=0.45)
        print("✓ Detector inicializado correctamente")
    except Exception as e:
        print(f"✗ ERROR al inicializar YOLO: {e}")
        print("¿Tienes instalado ultralytics? (pip install ultralytics)")
        return
    
    # 4. Realizar detección
    print("\nEjecutando detección...")
    detections = detector.detect(frame)
    
    # 5. Mostrar resultados
    print(f"\n{'='*60}")
    print(f"RESULTADOS: {len(detections)} objetos detectados")
    print(f"{'='*60}")
    
    if not detections:
        print("No se detectaron objetos de interés en la imagen.")
    else:
        for i, det in enumerate(detections, 1):
            print(f"{i}. {det.class_name.upper()}")
            print(f"   Confianza: {det.confidence:.2%}")
            print(f"   Coordenadas: ({det.x1}, {det.y1}) - ({det.x2}, {det.y2})")
            print()
    
    # 6. Dibujar detecciones y guardar
    print("Generando imagen de salida...")
    annotated_frame = detector.draw_detections(frame, detections)
    
    # Guardar resultado
    output_path = Path(__file__).parent / "img_detected.jpg"
    cv2.imwrite(str(output_path), annotated_frame)
    print(f"✓ Imagen anotada guardada en: {output_path}")
    
    # 7. Mostrar preview (opcional, si tienes GUI)
    try:
        # Redimensionar para que quepa en pantalla si es muy grande
        display_frame = annotated_frame.copy()
        max_width = 1280
        if display_frame.shape[1] > max_width:
            scale = max_width / display_frame.shape[1]
            new_width = int(display_frame.shape[1] * scale)
            new_height = int(display_frame.shape[0] * scale)
            display_frame = cv2.resize(display_frame, (new_width, new_height))
        
        cv2.imshow("Detecciones YOLO", display_frame)
        print("\nPresiona cualquier tecla en la ventana de imagen para cerrar...")
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    except Exception as e:
        print(f"No se pudo mostrar ventana (modo headless?): {e}")
        print("La imagen de resultado se guardó en disco de todos modos.")
    
    print(f"\n{'='*60}")
    print("PRUEBA COMPLETADA")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()