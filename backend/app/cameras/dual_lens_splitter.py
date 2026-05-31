"""
Separador de video para cámaras dual lens (lente dual)
"""
import numpy as np
import logging
import cv2


class DualLensSplitter:
    """
    Divide frames de cámaras con lente dual (panorámica + detalle)
    o cámaras 180/360 grados.
    
    NOTA: Por defecto intercambia las mitades (swap=True) porque la mayoría
    de cámaras dual-lens entregan el frame con las imágenes espejadas
    (la lente física izquierda aparece en la mitad derecha del frame).
    """

    def __init__(self, camera_id: int, split_mode: str = "horizontal", 
                 swap_lenses: bool = True, flip_horizontal: bool = False):
        """
        Args:
            camera_id: ID de la cámara
            split_mode: "horizontal" (lado a lado - divide ancho) 
                       o "vertical" (arriba/abajo - divide alto)
            swap_lenses: Si True, intercambia left/right (corrección para cámaras 
                        que entregan el frame espejado). Default True.
            flip_horizontal: Si True, aplica espejo horizontal a cada lente después 
                          de dividir. Default False.
        """
        self.camera_id = camera_id
        self.split_mode = split_mode
        self.swap_lenses = swap_lenses
        self.flip_horizontal = flip_horizontal
        self._logger = logging.getLogger(__name__)

    def split(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Divide el frame en dos imágenes según el modo configurado.

        Args:
            frame: Frame numpy array (H, W, C)

        Returns:
            tuple: (lens1, lens2) - Dos frames separados
                   Si swap_lenses=False:
                     lens1 = izquierda (horizontal) o arriba (vertical)
                     lens2 = derecha (horizontal) o abajo (vertical)
                   Si swap_lenses=True (default):
                     lens1 = derecha del frame (lente izquierdo físico)
                     lens2 = izquierda del frame (lente derecho físico)
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

                # Views (sin copy): el frame entrante ya viene como una copia
                # del distributor (needs_copy=True), así que podemos crear
                # views de sus mitades sin copiar de nuevo. Esto ahorra ~5MB
                # de memcpy por frame en cámaras de alta resolución.
                if len(frame.shape) == 3:
                    left_half = frame[:, :mid, :]
                    right_half = frame[:, mid:, :]
                else:
                    left_half = frame[:, :mid]
                    right_half = frame[:, mid:]
                
                # CORRECCIÓN: Por defecto intercambiamos porque las cámaras dual-lens
                # típicamente entregan el frame espejado
                if self.swap_lenses:
                    lens1 = right_half  # Mitad derecha del frame = Lente izquierdo físico
                    lens2 = left_half   # Mitad izquierda del frame = Lente derecho físico
                    self._logger.debug(f"Split horizontal (SWAPPED): {width}x{height} → "
                                      f"Lens1(right_half): {mid}x{height}, Lens2(left_half): {width-mid}x{height}")
                else:
                    lens1 = left_half   # Mitad izquierda
                    lens2 = right_half  # Mitad derecha
                    self._logger.debug(f"Split horizontal (normal): {width}x{height} → "
                                      f"Lens1: {mid}x{height}, Lens2: {width-mid}x{height}")

                # Aplicar flip horizontal si se solicita (corrección adicional de mirror)
                if self.flip_horizontal:
                    lens1 = cv2.flip(lens1, 1)
                    lens2 = cv2.flip(lens2, 1)

            elif self.split_mode == "vertical":
                # División horizontal del frame (arriba/abajo)
                # Divide el alto (height) por la mitad
                mid = height // 2

                # Views sin copy (ver explicación en la rama horizontal)
                if len(frame.shape) == 3:
                    top_half = frame[:mid, :, :]
                    bottom_half = frame[mid:, :, :]
                else:
                    top_half = frame[:mid, :]
                    bottom_half = frame[mid:, :]
                
                if self.swap_lenses:
                    # En modo vertical, "swap" intercambia arriba/abajo
                    lens1 = bottom_half  # Abajo = Lente "1" físico
                    lens2 = top_half     # Arriba = Lente "2" físico
                    self._logger.debug(f"Split vertical (SWAPPED): {width}x{height} → "
                                      f"Lens1(bottom): {width}x{mid}, Lens2(top): {width}x{height-mid}")
                else:
                    lens1 = top_half
                    lens2 = bottom_half
                    self._logger.debug(f"Split vertical (normal): {width}x{height} → "
                                      f"Lens1: {width}x{mid}, Lens2: {width}x{height-mid}")

                if self.flip_horizontal:
                    lens1 = cv2.flip(lens1, 1)
                    lens2 = cv2.flip(lens2, 1)
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
                   Nota: Ten en cuenta que si swap_lenses=True, lens1 corresponde
                   a la mitad derecha (horizontal) o inferior (vertical) del frame original
        """
        if self.split_mode == "horizontal":
            w = original_width // 2
            return (w, original_height, original_width - w, original_height)
        else:  # vertical
            h = original_height // 2
            return (original_width, h, original_width, original_height - h)