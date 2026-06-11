"""
MÓDULO: core.hardware_detector — Detección de capacidades de cómputo (GPU/CPU).

PROPÓSITO
    Inspeccionar en runtime si hay GPU CUDA disponible (vía PyTorch) y cuántos
    núcleos de CPU hay, para RECOMENDAR un backend de inferencia y estimar
    cuántas cámaras pueden correr IA simultáneamente.

RESPONSABILIDAD
    Solo diagnóstico/recomendación: no carga modelos ni configura nada. El pool
    real de YOLO (processing/ai/model_pool.py) decide el device final; este
    módulo aporta la "foto" del hardware para la UI/configuración y para acotar
    expectativas (en CPU solo 1 cámara con IA ≈5 fps; con CUDA hasta 4 ≈30 fps).

DEPENDENCIAS / COMPONENTES RELACIONADOS
    torch (opcional; si falta → solo CPU). Consumido por la capa de sistema/IA
    para informar al usuario y por endpoints de hardware info.

PIPELINE
    Apoya el Pipeline #9 (IA): orienta qué backend usar y cuántas inferencias
    son realistas. Nota: el proyecto suele usar aceleración por hardware en el
    decode (go2rtc QSV), independiente de esta detección de CUDA para YOLO.

PUNTO DE ENTRADA
    Instancia global `hardware_detector` al final del módulo; llamar a
    `.detect()` para obtener el dict de capacidades.
"""
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class HardwareDetector:
    """
    Detecta el hardware de cómputo disponible para IA y vídeo.

    Rol: utilidad de diagnóstico (no es singleton __new__; se usa la instancia
    de módulo `hardware_detector`). Soporta GPU NVIDIA CUDA con fallback a CPU.
    Consumido por la capa de info de sistema/IA para recomendar backend y límites.
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