"""
Image Optimizer - Redimensionamiento y compresión de imágenes.
Utilizado para reducir tamaño de snapshots antes de enviar a Telegram o servir como thumbnails.
"""
import io
import logging
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image
import cv2
import numpy as np

logger = logging.getLogger(__name__)


class ImageOptimizer:
    """
    Optimiza imágenes para reducir ancho de banda y almacenamiento.
    - Redimensiona manteniendo aspect ratio
    - Convierte a JPEG con calidad ajustable
    """
    
    DEFAULT_MAX_WIDTH = 800
    DEFAULT_QUALITY = 85
    
    @staticmethod
    def resize_image(image: np.ndarray, max_width: int = DEFAULT_MAX_WIDTH) -> np.ndarray:
        """
        Redimensiona una imagen (numpy array BGR) manteniendo aspect ratio.
        """
        height, width = image.shape[:2]
        if width <= max_width:
            return image
        
        new_width = max_width
        new_height = int(height * (new_width / width))
        resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)
        return resized
    
    @staticmethod
    def optimize_image_file(input_path: str, output_path: Optional[str] = None,
                           max_width: int = DEFAULT_MAX_WIDTH,
                           quality: int = DEFAULT_QUALITY) -> Optional[str]:
        """
        Lee una imagen del disco, la redimensiona y la guarda (opcionalmente sobrescribe).
        Retorna la ruta del archivo optimizado.
        """
        try:
            img = cv2.imread(input_path)
            if img is None:
                logger.error(f"No se pudo leer imagen: {input_path}")
                return None
            
            resized = ImageOptimizer.resize_image(img, max_width)
            
            if output_path is None:
                output_path = input_path
            
            # Guardar con calidad JPEG
            cv2.imwrite(output_path, resized, [cv2.IMWRITE_JPEG_QUALITY, quality])
            logger.debug(f"Imagen optimizada: {input_path} -> {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Error optimizando imagen {input_path}: {e}")
            return None
    
    @staticmethod
    def optimize_image_bytes(image_bytes: bytes, max_width: int = DEFAULT_MAX_WIDTH,
                            quality: int = DEFAULT_QUALITY) -> bytes:
        """
        Optimiza una imagen en memoria (bytes) y retorna bytes optimizados.
        Útil para envío a Telegram sin tocar disco.
        """
        try:
            # Convertir bytes a numpy array
            np_arr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if img is None:
                return image_bytes
            
            resized = ImageOptimizer.resize_image(img, max_width)
            _, buffer = cv2.imencode('.jpg', resized, [cv2.IMWRITE_JPEG_QUALITY, quality])
            return buffer.tobytes()
        except Exception as e:
            logger.error(f"Error optimizando imagen en bytes: {e}")
            return image_bytes


# Instancia global
image_optimizer = ImageOptimizer()