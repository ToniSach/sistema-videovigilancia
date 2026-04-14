"""
Hardware Detector - Detección automática de capacidades GPU/CPU.
"""
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class HardwareDetector:
    """
    Detecta hardware disponible para IA y video.
    Soporta NVIDIA CUDA y fallback CPU.
    """
    
    def __init__(self):
        self.cuda_available = False
        self.cuda_device_name: Optional[str] = None
        self.cpu_cores = os.cpu_count() or 4
        
    def detect(self) -> Dict:
        """
        Detecta hardware disponible.
        
        Returns:
            Dict con estado de CUDA, info CPU y recomendación
        """
        self._check_cuda()
        
        return {
            "cuda_available": self.cuda_available,
            "cuda_device": self.cuda_device_name,
            "cpu_cores": self.cpu_cores,
            "recommended_backend": self._recommend_backend(),
            "current_load": self._estimate_load()
        }
    
    def _check_cuda(self) -> None:
        """Verifica disponibilidad de CUDA via PyTorch."""
        try:
            import torch
            if torch.cuda.is_available():
                self.cuda_available = True
                self.cuda_device_name = torch.cuda.get_device_name(0)
                logger.info(f"CUDA detectado: {self.cuda_device_name}")
            else:
                logger.info("CUDA no disponible (torch.cuda.is_available() = False)")
        except ImportError:
            logger.warning("PyTorch no instalado, usando CPU únicamente")
        except Exception as e:
            logger.error(f"Error detectando CUDA: {e}")
    
    def _recommend_backend(self) -> str:
        """Recomienda backend óptimo."""
        if self.cuda_available:
            return "cuda"
        return "cpu"
    
    def _estimate_load(self) -> Dict:
        """Estima capacidad de procesamiento."""
        if self.cuda_available:
            return {
                "max_cameras_ai": 4,
                "inference_fps": 30,
                "cpu_usage_expected": "low"
            }
        else:
            # i5 típico: 4 cores
            return {
                "max_cameras_ai": 1,  # Solo 1 cámara con IA en CPU
                "inference_fps": 5,     # ~200ms por frame
                "cpu_usage_expected": "high"
            }


# Instancia global
hardware_detector = HardwareDetector()