"""
================================================================================
PAQUETE: cameras — Subsistema de cámaras (ciclo de vida, ONVIF, PTZ, audio, LED)
================================================================================

PROPÓSITO
    Agrupa TODO lo relacionado con la gestión de cámaras IP del NVR/VMS:
      - Ciclo de vida (alta/arranque/parada) vía `camera_manager.py`.
      - Descubrimiento en LAN (WS-Discovery + escaneo de subred) en
        `onvif_discovery.py`.
      - Cliente ONVIF SOAP directo (`onvif_soap.py`) + utilidades comunes
        (`onvif_common.py`).
      - Control de cámara vía ONVIF: PTZ (`ptz_controller.py`), audio
        bidireccional (`audio_controller.py`), LED/IR-cut (`led_controller.py`)
        y sincronización de hora (`time_sync.py`).
      - Heurísticas de alta (`camera_heuristics.py`).

PIPELINES EN LOS QUE PARTICIPA EL PAQUETE
    #1  Inicio        — CameraManager.start_all_active() (arranque de cámaras).
    #3  Live          — provee URLs RTSP (GetStreamUri) que consume go2rtc.
    #7  ONVIF         — descubrimiento, capacidades, perfiles, device info.
    #8  PTZ           — control Pan-Tilt-Zoom + presets.
    #11 Grabación     — el CameraManager cablea el RecordingManager por cámara.

PUNTO DE ENTRADA
    Este `__init__` reexporta los controladores de uso frecuente para que las
    rutas (api/routes/cameras.py) puedan importarlos desde `..cameras` sin
    conocer el módulo concreto. NO ejecuta lógica de arranque.
================================================================================
"""

from .ptz_controller import PTZController
from .led_controller import LEDController
from .audio_controller import AudioController
