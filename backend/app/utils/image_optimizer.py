"""
================================================================================
MÓDULO: utils.image_optimizer — Redimensionado y recompresión de imágenes
================================================================================

PROPÓSITO
    Utilidad SIN ESTADO para reducir el tamaño de snapshots/imágenes (redimensiona
    a un ancho máximo manteniendo aspect ratio y recomprime a JPEG con calidad
    ajustable) antes de enviarlas o servirlas.

RESPONSABILIDAD PRINCIPAL
    Bajar ancho de banda y almacenamiento de las imágenes de evento/notificación
    sin lógica de negocio: entra una imagen (array, fichero o bytes), sale otra
    más ligera. No persiste estado ni conoce cámaras/eventos.

DEPENDENCIAS
    PIL (Pillow), cv2 (OpenCV) y numpy — operaciones de imagen puras.

COMPONENTES RELACIONADOS
    Consumidores típicos: snapshots de eventos de IA y notificaciones (Telegram),
    y generación de thumbnails. Se ofrece como helper compartido; el envío real
    lo hacen los notificadores (Pipeline #13).

PUNTO DE ENTRADA
    Singleton ligero `image_optimizer` al final del módulo (o métodos estáticos
    de la clase directamente, ya que no guardan estado).

PIPELINE(S) + ETAPA
    - Pipeline #10/#13 (Eventos → Notificaciones): optimizar el snapshot del
      evento antes de adjuntarlo a la alerta.
    - Transversal: cualquier punto que necesite un JPEG más pequeño.
================================================================================
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
    Optimizador de imágenes sin estado (métodos estáticos + singleton trivial).

    ROL: helper reutilizable para redimensionar (preservando aspect ratio) y
    recomprimir a JPEG. Como no guarda estado, sus métodos son `@staticmethod`;
    el `image_optimizer` global es solo conveniencia, no un singleton con recursos.

    QUIÉN LO USA: notificadores/servicios que adjuntan o sirven snapshots
    (Telegram, thumbnails). DEPENDENCIAS: cv2/numpy/PIL.
    """

    # Ancho máximo y calidad JPEG por defecto: equilibrio nitidez/peso para
    # alertas (la imagen viaja por red móvil y se ve en pantalla pequeña).
    DEFAULT_MAX_WIDTH = 800
    DEFAULT_QUALITY = 85
    
    @staticmethod
    def resize_image(image: np.ndarray, max_width: int = DEFAULT_MAX_WIDTH) -> np.ndarray:
        """
        Propósito: reducir un frame a `max_width` preservando proporción (núcleo
            del optimizador). Si ya es <= max_width, lo devuelve tal cual (no amplía).
        Inputs: image (numpy BGR, p.ej. un frame OpenCV); max_width en px.
        Outputs: numpy BGR redimensionado (interpolación INTER_AREA, ideal al reducir).
        Excepciones: ninguna propia (errores de cv2 suben al caller).
        Llamado por: optimize_image_file y optimize_image_bytes.
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
        Propósito: optimizar una imagen EN DISCO (lee, redimensiona, recomprime JPEG).
        Inputs: input_path; output_path (None → sobrescribe el origen); max_width; quality.
        Outputs: ruta del fichero optimizado, o None si no se pudo leer/procesar.
        Excepciones: capturadas → loguea y devuelve None (no propaga).
        Llamado por: flujos que ya tienen el snapshot guardado en disco.
        Llama a: resize_image, cv2.imread/imwrite.
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
        Propósito: optimizar una imagen EN MEMORIA (bytes JPEG→bytes JPEG), sin
            tocar disco. Etapa: Pipeline #13 (adjuntar snapshot a una alerta Telegram).
        Inputs: image_bytes (JPEG/PNG codificado); max_width; quality.
        Outputs: bytes JPEG optimizados; si falla decodificar/procesar devuelve
            los bytes originales intactos (degradación segura, nunca rompe el envío).
        Excepciones: capturadas → loguea y devuelve la entrada sin tocar.
        Llamado por: notificadores que envían el snapshot directo desde memoria.
        Llama a: resize_image, cv2.imdecode/imencode.
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