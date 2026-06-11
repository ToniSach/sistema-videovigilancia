"""
================================================================================
MÓDULO: onvif_common — Utilidades ONVIF compartidas (onvif-zeep / WSDL)
================================================================================

PROPÓSITO
    Centraliza la conexión ONVIF basada en onvif-zeep (carga WSDL) que usan los
    controladores de CONTROL de cámara: PTZ, LED/IR-cut, audio y sync de hora.
    Resuelve el directorio WSDL una sola vez, decide qué puertos ONVIF probar y
    persiste en BD el puerto que funcionó.

POR QUÉ EXISTE
    `_resolve_wsdl_dir`, la lista de puertos, `candidate_ports`, `build_onvif_cam`
    y `persist_onvif_port` estaban DUPLICADOS (idénticos) en ptz_controller.py,
    led_controller.py y audio_controller.py (~250 líneas repetidas). Aquí viven
    una sola vez; cada controlador las usa pasando su `log_prefix`
    ("PTZ"/"LED"/"AUDIO"/"TIME").

DIFERENCIA CON onvif_soap
    onvif_soap = FAST PATH para DESCUBRIMIENTO (SOAP a mano, sin WSDL).
    onvif_common = onvif-zeep (con WSDL) para el CONTROL de cámara ya dada de
    alta, donde se necesita la API completa (ContinuousMove, SetImagingSettings,
    SetSystemDateAndTime...). El kwarg `wsse` no existe en esta versión de
    onvif-zeep, así que se usa auth PasswordText por defecto.

DEPENDENCIAS: onvif (onvif-zeep), zeep, database (Camera + persistencia).
COMPONENTES RELACIONADOS: ptz_controller, led_controller, audio_controller,
    time_sync (todos importan estas helpers).
PIPELINES: #7 ONVIF (conexión), #8 PTZ (control).

PUERTOS ONVIF
    Muchas cámaras (XiongMai, TP-Link, etc.) NO exponen ONVIF en :80 sino en
    :8000 / :8899. Se prueban en orden; el primero que valide GetCapabilities
    se persiste en camera.onvif_url para no reiterar en la próxima conexión.
================================================================================
"""
from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.parse import urlparse

from onvif import ONVIFCamera
from zeep.exceptions import Fault

from ..database.models import Camera

logger = logging.getLogger(__name__)


def resolve_wsdl_dir() -> Optional[str]:
    """Localiza el directorio WSDL del paquete onvif-zeep instalado."""
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


# WSDL local resuelto UNA vez para todo el proceso (lo comparten los 3 controladores).
WSDL_DIR = resolve_wsdl_dir()

# Muchas cámaras (XiongMai, TP-Link, etc.) no exponen ONVIF en :80 sino en
# :8000 / :8899. Se prueban en orden.
ONVIF_PORTS = [80, 8000, 8080, 8899]


def candidate_ports(camera: Camera) -> list[int]:
    """
    Lista de puertos ONVIF a probar. Si la cámara trae onvif_url con puerto, ese
    va primero (y el resto detrás por si el guardado es incorrecto).
    """
    if camera.onvif_url:
        try:
            p = urlparse(camera.onvif_url).port
            if p:
                return [p] + [x for x in ONVIF_PORTS if x != p]
        except Exception:
            pass
    return list(ONVIF_PORTS)


def build_onvif_cam(camera: Camera, port: int, log_prefix: str) -> Optional[ONVIFCamera]:
    """
    Construye y valida un cliente onvif-zeep contra ip:port. (Pipeline #7 ONVIF.)

    Validación: tras construir el ONVIFCamera, llama GetCapabilities(Category=All)
    como prueba de vida; si responde sin Fault, la conexión sirve.

    Solicitud SOAP: GetCapabilities (Category=All).
    Respuesta esperada: capacidades del dispositivo (no se parsean aquí, solo
        se usa el éxito de la llamada como validación).
    Servicio ONVIF: device_service.
    Inputs:  camera (IP/credenciales), port, log_prefix ("PTZ"/"LED"/...).
    Outputs: ONVIFCamera conectada o None si falla (Fault/red/timeout).
    Llamado por: los _build_cam() de ptz/led/audio/time_sync.
    """
    kwargs = {"encrypt": False, "no_cache": True}
    if WSDL_DIR:
        kwargs["wsdl_dir"] = WSDL_DIR
    try:
        logger.debug(
            f"[{log_prefix}] cam={camera.id} probando {camera.ip_address}:{port} "
            f"user={camera.username!r}"
        )
        cam = ONVIFCamera(
            camera.ip_address, port,
            camera.username or "", camera.password or "",
            **kwargs
        )
        cam.devicemgmt.GetCapabilities({"Category": "All"})
        logger.info(f"[{log_prefix}] cam={camera.id} ONVIF OK en {camera.ip_address}:{port}")
        return cam
    except Fault as e:
        logger.debug(f"[{log_prefix}] cam={camera.id} Fault :{port} → {e}")
        return None
    except Exception as e:
        logger.debug(f"[{log_prefix}] cam={camera.id} :{port} → {type(e).__name__}: {e}")
        return None


def persist_onvif_port(camera: Camera, port: int, log_prefix: str) -> None:
    """
    Guarda onvif_url=http://ip:port/onvif/device_service en BD para que la próxima
    conexión vaya directa al puerto correcto (sin iterar los 4 puertos).
    """
    expected = f"http://{camera.ip_address}:{port}/onvif/device_service"
    if camera.onvif_url == expected:
        return
    try:
        from ..database.connection import db_manager
        from ..database.models import Camera as CameraModel
        with db_manager.get_session() as session:
            cam = session.query(CameraModel).filter_by(id=camera.id).first()
            if cam is not None:
                cam.onvif_url = expected
                session.commit()
                camera.onvif_url = expected
                logger.info(f"[{log_prefix}] cam={camera.id} onvif_url guardado: {expected}")
    except Exception as e:
        logger.warning(f"[{log_prefix}] cam={camera.id} no pude guardar onvif_url: {e}")
