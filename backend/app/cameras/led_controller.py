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
import threading
from typing import Optional

from onvif import ONVIFCamera

from ..database.models import Camera
from .onvif_common import (
    candidate_ports as _common_candidate_ports,
    build_onvif_cam as _common_build_cam,
    persist_onvif_port as _common_persist_port,
)

logger = logging.getLogger(__name__)


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
        _common_persist_port(self._camera, port, "LED")

    def _candidate_ports(self) -> list[int]:
        return _common_candidate_ports(self._camera)

    def _build_cam(self, port: int) -> Optional[ONVIFCamera]:
        return _common_build_cam(self._camera, port, "LED")

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

    def set_white_light(self, on: bool) -> bool:
        """
        BEST-EFFORT: enciende/apaga la LUZ BLANCA de la cámara.

        OJO: la luz blanca NO es estándar ONVIF. Las cámaras XiongMai/iCSee la
        controlan con comandos auxiliares propietarios (o el protocolo binario
        del puerto 34567). Probamos varios tokens de SendAuxiliaryCommand vía
        PTZ y device-mgmt; si la cámara los acepta, la luz responde. Si ninguno
        funciona en TU cámara, hay que mirar el log y, en el peor caso, usar el
        protocolo propietario (tarea aparte). Devuelve True si algún comando se
        envió sin error (no garantiza que la luz físicamente encendiera).
        """
        if not self._connected or self._cam is None:
            return False
        st = "On" if on else "Off"
        candidates = [
            f"tt:WhiteLight|{st}", f"WhiteLight|{st}",
            f"tt:FloodLight|{st}", f"FloodLight|{st}",
            f"tt:IRLamp|{st}", f"LightControl|{st}", f"tt:Wiper|{st}",
        ]
        # 1) PTZ.SendAuxiliaryCommand (necesita ProfileToken)
        try:
            ptz = self._cam.create_ptz_service()
            media = self._cam.create_media_service()
            profs = media.GetProfiles()
            ptoken = profs[0].token if profs else None
            for cmd in candidates:
                try:
                    req = ptz.create_type("SendAuxiliaryCommand")
                    req.ProfileToken = ptoken
                    req.AuxiliaryData = cmd
                    ptz.SendAuxiliaryCommand(req)
                    logger.info(f"[LED] cam={self._camera.id} luz blanca via PTZ aux '{cmd}'")
                    return True
                except Exception:
                    continue
        except Exception:
            pass
        # 2) DeviceMgmt.SendAuxiliaryCommand (algunas cámaras lo exponen aquí)
        try:
            dev = self._cam.create_devicemgmt_service()
            for cmd in candidates:
                try:
                    dev.SendAuxiliaryCommand({"AuxiliaryCommand": cmd})
                    logger.info(f"[LED] cam={self._camera.id} luz blanca via Device aux '{cmd}'")
                    return True
                except Exception:
                    continue
        except Exception:
            pass
        logger.info(
            f"[LED] cam={self._camera.id}: luz blanca no respondió por ONVIF "
            f"(probados {len(candidates)} comandos aux). Puede requerir protocolo propietario."
        )
        return False

    def turn_on(self) -> bool:
        # Comportamiento previo (IR-cut OFF) SE MANTIENE para no perder el modo
        # noche que ya funcionaba, y ADEMÁS se intenta encender la luz blanca.
        ok = self.set_ir_cut_filter("OFF")
        try:
            self.set_white_light(True)
        except Exception:
            pass
        return ok

    def turn_off(self) -> bool:
        try:
            self.set_white_light(False)
        except Exception:
            pass
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
