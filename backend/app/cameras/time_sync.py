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
import os
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from onvif import ONVIFCamera
from zeep.exceptions import Fault

from ..database.models import Camera

logger = logging.getLogger(__name__)


def _resolve_wsdl_dir() -> Optional[str]:
    try:
        import onvif
        pkg_dir = os.path.dirname(onvif.__file__)
        site_packages = os.path.dirname(pkg_dir)
        for cand in (os.path.join(site_packages, "wsdl"), os.path.join(pkg_dir, "wsdl")):
            if os.path.isdir(cand):
                return cand
    except Exception:
        pass
    return None


_WSDL_DIR = _resolve_wsdl_dir()
_ONVIF_PORTS = [80, 8000, 8080, 8899]


def _candidate_ports(camera: Camera) -> list[int]:
    """Prioriza el puerto ONVIF ya conocido (camera.onvif_url) si lo hay."""
    if camera.onvif_url:
        try:
            p = urlparse(camera.onvif_url).port
            if p:
                return [p] + [x for x in _ONVIF_PORTS if x != p]
        except Exception:
            pass
    return list(_ONVIF_PORTS)


def _build_cam(camera: Camera, port: int) -> Optional[ONVIFCamera]:
    kwargs = {"encrypt": False, "no_cache": True}
    if _WSDL_DIR:
        kwargs["wsdl_dir"] = _WSDL_DIR
    try:
        cam = ONVIFCamera(
            camera.ip_address, port,
            camera.username or "", camera.password or "",
            **kwargs,
        )
        cam.devicemgmt.GetCapabilities({"Category": "All"})
        return cam
    except Exception:
        return None


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
