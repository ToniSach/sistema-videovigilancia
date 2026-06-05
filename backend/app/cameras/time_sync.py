"""
Sincronización de la hora de la cámara con la del servidor vía ONVIF
(SetSystemDateAndTime).

POR QUÉ
-------
Muchas cámaras baratas (XiongMai y similares) arrancan con la fecha/hora mal
(p.ej. mostrando el día siguiente y +14h). Eso ensucia el OSD del vídeo y las
marcas de tiempo de los eventos/grabaciones. Como el servidor SÍ tiene la hora
correcta, le empujamos esa hora a la cámara por ONVIF.

NOTA: NO afecta a la reproducción de los MP4 (eso depende del códec/tag), solo
al reloj que la cámara estampa en la imagen y reporta en eventos.

PIPELINE
--------
  Paso 1. Conectar al device service ONVIF de la cámara (probando puertos).
  Paso 2. Construir UTCDateTime = ahora (UTC) + TimeZone POSIX del servidor.
  Paso 3. Enviar SetSystemDateAndTime (DateTimeType=Manual). Best-effort.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from onvif import ONVIFCamera
from zeep.exceptions import Fault

from ..database.models import Camera
# Reutilizamos las utilidades ONVIF comunes (puertos candidatos + build) en vez
# de duplicarlas aquí (estaban repetidas idénticas en ptz/led/audio).
from .onvif_common import (
    candidate_ports as _candidate_ports,
    build_onvif_cam as _build_cam_common,
)

logger = logging.getLogger(__name__)


def _build_cam(camera: Camera, port: int) -> Optional[ONVIFCamera]:
    return _build_cam_common(camera, port, "TIME")


def sync_camera_time(camera: Camera) -> bool:
    """
    Empuja la hora del servidor a la cámara. Devuelve True si la cámara aceptó
    el cambio; False si no soporta ONVIF o lo rechazó (best-effort, no lanza).
    """
    cam = None
    for port in _candidate_ports(camera):
        cam = _build_cam(camera, port)
        if cam is not None:
            break
    if cam is None:
        logger.warning(f"[TIME] cam={camera.id}: sin ONVIF accesible, no se sincroniza la hora")
        return False

    try:
        dev = cam.devicemgmt
        # Estrategia robusta para que el OSD muestre la HORA LOCAL correcta:
        # enviar la hora LOCAL del servidor en el campo UTCDateTime + TZ=GMT0
        # (sin desfase). Funciona en cámaras que RESPETAN la zona horaria
        # (GMT0 = offset 0 → muestran lo enviado) y en las que la IGNORAN
        # (XiongMai y similares, que pintan UTCDateTime tal cual). En ambos
        # casos el OSD queda en hora local.
        now = datetime.now()  # hora local del servidor (naive)
        req = dev.create_type("SetSystemDateAndTime")
        req.DateTimeType = "Manual"
        req.DaylightSavings = False
        req.TimeZone = {"TZ": "GMT0"}
        req.UTCDateTime = {
            "Date": {"Year": now.year, "Month": now.month, "Day": now.day},
            "Time": {"Hour": now.hour, "Minute": now.minute, "Second": now.second},
        }
        dev.SetSystemDateAndTime(req)
        logger.info(
            f"[TIME] cam={camera.id}: hora local enviada "
            f"{now:%Y-%m-%d %H:%M:%S} (TZ GMT0)"
        )
        return True
    except Fault as e:
        logger.warning(f"[TIME] cam={camera.id}: la cámara rechazó SetSystemDateAndTime: {e}")
        return False
    except Exception as e:
        logger.warning(f"[TIME] cam={camera.id}: error sincronizando hora: {e}")
        return False
