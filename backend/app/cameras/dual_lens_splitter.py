"""
Separador de video para cámaras dual lens (lente dual)
"""
import numpy as np
import logging


class DualLensSplitter:
    """
    Divide frames de cámaras con lente dual (panorámica + detalle)
    o cámaras 180/360 grados.
    """

    def __init__(self, camera_id: int, split_mode: str = "horizontal"):
        """
        Args:
            camera_id: ID de la cámara
            split_mode: "horizontal" (lado a lado - divide ancho) 
                       o "vertical" (arriba/abajo - divide alto)
        """
        self.camera_id = camera_id
        self.split_mode = split_mode
        self._logger = logging.getLogger(__name__)

    def split(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Divide el frame en dos imágenes según el modo configurado.

        Args:
            frame: Frame numpy array (H, W, C)

        Returns:
            tuple: (lens1, lens2) - Dos frames separados
                   lens1 = izquierda (horizontal) o arriba (vertical)
                   lens2 = derecha (horizontal) o abajo (vertical)
        """
        if frame is None or frame.size == 0:
            self._logger.warning(f"Frame vacío recibido en camera {self.camera_id}")
            empty = np.array([])
            return empty, empty

        try:
            height, width = frame.shape[:2]
            
            if self.split_mode == "horizontal":
                # División vertical del frame (izquierda/derecha)
                # Divide el ancho (width) por la mitad
                mid = width // 2
                
                lens1 = frame[:, :mid, :].copy() if len(frame.shape) == 3 else frame[:, :mid].copy()
                lens2 = frame[:, mid:, :].copy() if len(frame.shape) == 3 else frame[:, mid:].copy()
                
                self._logger.debug(f"Split horizontal: {width}x{height} → "
                                  f"Lens1: {mid}x{height}, Lens2: {width-mid}x{height}")

            elif self.split_mode == "vertical":
                # División horizontal del frame (arriba/abajo)
                # Divide el alto (height) por la mitad
                mid = height // 2
                
                lens1 = frame[:mid, :, :].copy() if len(frame.shape) == 3 else frame[:mid, :].copy()
                lens2 = frame[mid:, :, :].copy() if len(frame.shape) == 3 else frame[mid:, :].copy()
                
                self._logger.debug(f"Split vertical: {width}x{height} → "
                                  f"Lens1: {width}x{mid}, Lens2: {width}x{height-mid}")
            else:
                self._logger.error(f"Modo de split inválido: {self.split_mode}")
                return frame.copy(), np.array([])

            return lens1, lens2

        except Exception as e:
            self._logger.error(f"Error dividiendo frame dual lens cámara {self.camera_id}: {e}")
            return frame.copy(), np.array([])

    def get_lens_resolution(self, original_width: int, original_height: int) -> tuple:
        """
        Calcula la resolución resultante para cada lente.

        Returns:
            tuple: (width_lens1, height_lens1, width_lens2, height_lens2)
        """
        if self.split_mode == "horizontal":
            w = original_width // 2
            return (w, original_height, original_width - w, original_height)
        else:  # vertical
            h = original_height // 2
            return (original_width, h, original_width, original_height - h)