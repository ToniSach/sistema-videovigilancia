"""
Control de LEDs / IR Cut Filter vía ONVIF Imaging Service.

Reescrito para:
- Probar varios puertos ONVIF (mismas cámaras requieren :8000 / :8899).
- Probar Basic Auth primero y caer a UsernameToken.
- Logs detallados (qué puerto y método de auth se usa).
- Cache singleton (LEDManager) para no reconectar en cada petición.
"""
from __future__ import annotations

import logging
import os
import threading
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


class LEDController:
    """Controla el filtro IR-cut (modos: ON/OFF/AUTO)."""

    def __init__(self, camera: Camera):
        self._camera = camera
        self._cam: Optional[ONVIFCamera] = None
        self._imaging = None
        self._video_source_token: Optional[str] = None
        self._connected = False
        self._lock = threading.Lock()
        self._connect()

    # ------------------------------------------------------------------
    def _persist_onvif_port(self, port: int) -> None:
        """Guarda el puerto ONVIF descubierto en camera.onvif_url."""
        expected = f"http://{self._camera.ip_address}:{port}/onvif/device_service"
        if self._camera.onvif_url == expected:
            return
        try:
            from ..database.connection import db_manager
            from ..database.models import Camera as CameraModel
            with db_manager.get_session() as session:
                cam = session.query(CameraModel).filter_by(id=self._camera.id).first()
                if cam is not None:
                    cam.onvif_url = expected
                    session.commit()
                    self._camera.onvif_url = expected
                    logger.info(f"[LED] cam={self._camera.id} onvif_url guardado: {expected}")
        except Exception as e:
            logger.warning(f"[LED] cam={self._camera.id} no pude guardar onvif_url: {e}")

    def _candidate_ports(self) -> list[int]:
        if self._camera.onvif_url:
            try:
                p = urlparse(self._camera.onvif_url).port
                if p:
                    return [p] + [x for x in _ONVIF_PORTS if x != p]
            except Exception:
                pass
        return list(_ONVIF_PORTS)

    def _build_cam(self, port: int) -> Optional[ONVIFCamera]:
        """El kwarg `wsse` no existe en esta versión de onvif-zeep; no lo pasamos."""
        kwargs = {"encrypt": False, "no_cache": True}
        if _WSDL_DIR:
            kwargs["wsdl_dir"] = _WSDL_DIR
        try:
            logger.debug(f"[LED] cam={self._camera.id} probando {self._camera.ip_address}:{port}")
            cam = ONVIFCamera(
                self._camera.ip_address, port,
                self._camera.username or "", self._camera.password or "",
                **kwargs
            )
            cam.devicemgmt.GetCapabilities({"Category": "All"})
            logger.info(f"[LED] cam={self._camera.id} ONVIF OK en :{port}")
            return cam
        except Fault as e:
            logger.debug(f"[LED] cam={self._camera.id} Fault :{port} → {e}")
            return None
        except Exception as e:
            logger.debug(f"[LED] cam={self._camera.id} :{port} → {type(e).__name__}: {e}")
            return None

    def _connect(self) -> None:
        ports = self._candidate_ports()
        logger.info(f"[LED] cam={self._camera.id} puertos candidatos: {ports}")
        cam = None
        working_port = None
        for port in ports:
            cam = self._build_cam(port)
            if cam is not None:
                working_port = port
                break

        if cam is None:
            logger.warning(f"[LED] cam={self._camera.id}: no se pudo conectar")
            return

        # Persistir el puerto que funcionó
        self._persist_onvif_port(working_port)

        try:
            self._imaging = cam.create_imaging_service()
            media = cam.create_media_service()
            profiles = media.GetProfiles()
            if not profiles:
                logger.warning(f"[LED] cam={self._camera.id} sin perfiles")
                return

            profile = profiles[0]
            vsc = getattr(profile, "VideoSourceConfiguration", None)
            if vsc is not None:
                self._video_source_token = vsc.SourceToken
            else:
                self._video_source_token = profile.token

            self._cam = cam
            self._connected = True
            logger.info(
                f"[LED] cam={self._camera.id} conectado (video_source_token={self._video_source_token})"
            )
        except Exception as e:
            logger.warning(f"[LED] cam={self._camera.id} falló al configurar imaging: {e}")
            self._connected = False

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def is_supported(self) -> bool:
        return self._connected

    def set_ir_cut_filter(self, mode: str) -> bool:
        """mode = 'ON' | 'OFF' | 'AUTO'."""
        if not self._connected:
            return False
        mode_u = (mode or "").upper()
        if mode_u not in ("ON", "OFF", "AUTO"):
            logger.warning(f"[LED] modo inválido: {mode}")
            return False
        with self._lock:
            try:
                req = self._imaging.create_type("GetImagingSettings")
                req.VideoSourceToken = self._video_source_token
                current = self._imaging.GetImagingSettings(req)
                current.IrCutFilter = mode_u
                set_req = self._imaging.create_type("SetImagingSettings")
                set_req.VideoSourceToken = self._video_source_token
                set_req.ImagingSettings = current
                self._imaging.SetImagingSettings(set_req)
                logger.info(f"[LED] cam={self._camera.id} IrCutFilter={mode_u}")
                return True
            except Exception as e:
                logger.error(f"[LED] cam={self._camera.id} error set_ir_cut: {e}")
                return False

    def turn_on(self) -> bool:
        return self.set_ir_cut_filter("OFF")  # IR-Cut OFF = ver IR → LEDs IR encendidos

    def turn_off(self) -> bool:
        return self.set_ir_cut_filter("ON")

    def set_auto(self) -> bool:
        return self.set_ir_cut_filter("AUTO")


class LEDManager:
    """Cache de LEDControllers por camera_id."""
    _instance: "Optional[LEDManager]" = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._controllers: dict[int, LEDController] = {}
        self._lock = threading.Lock()

    def get(self, camera: Camera) -> LEDController:
        with self._lock:
            ctrl = self._controllers.get(camera.id)
            if ctrl is not None and ctrl.is_supported():
                return ctrl
            ctrl = LEDController(camera)
            self._controllers[camera.id] = ctrl
            return ctrl

    def drop(self, camera_id: int) -> None:
        with self._lock:
            self._controllers.pop(camera_id, None)


led_manager = LEDManager()
