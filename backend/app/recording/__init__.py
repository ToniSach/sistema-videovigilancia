"""
Paquete `recording` — Subsistema de grabación del NVR (pipelines #11 y #12).

Reexporta los dos componentes del subsistema:
  - RecordingManager: grabación continua por segmentos (#11) + generación de
    clips de evento por SPLICE del continuo (#12). Construido por
    DependencyContainer; cableado por cámara por CameraManager.
  - StorageManager: hilo de rotación/limpieza por cuota (borrado LRU de las
    grabaciones más antiguas). Arrancado por main.py (paso 10).

La reconciliación FS↔BD vive en el paquete hermano `storage`
(consistency_checker), complementario a la limpieza por cuota de aquí.
"""
from .recording_manager import RecordingManager
from .storage_manager import StorageManager

__all__ = ["RecordingManager", "StorageManager"]
