"""
Control PTZ (Pan-Tilt-Zoom) vía ONVIF.

Reescrito para:
- Usar onvif-zeep de forma consistente (no mezclar zeep.Client manuales).
- No cargar WSDL desde la cámara por HTTP (muchas no lo exponen): usa el bundle local.
- Probar Basic Auth primero y caer a UsernameToken (WS-Security) como fallback.
- Reutilizar la conexión: una instancia por cámara, cacheada en el manager.
"""
import logging
import threading
from typing import Optional

from onvif import ONVIFCamera

from ..database.models import Camera
from .onvif_common import (
    WSDL_DIR as _WSDL_DIR,
    candidate_ports as _common_candidate_ports,
    build_onvif_cam as _common_build_cam,
    persist_onvif_port as _common_persist_port,
)

logger = logging.getLogger(__name__)

if _WSDL_DIR:
    logger.info(f"[PTZ] WSDL local detectado en: {_WSDL_DIR}")
else:
    logger.warning("[PTZ] WSDL local NO encontrado, onvif-zeep intentará cargar desde la cámara")

_VALID_DIRECTIONS = {"up", "down", "left", "right",
                     "up_left", "up_right", "down_left", "down_right",
                     "zoom_in", "zoom_out"}


class PTZController:
    """
    Una instancia gestiona la conexión PTZ a una cámara.
    Pensada para vivir mientras la cámara esté activa (cacheada por PTZManager).
    """

    def __init__(self, camera: Camera):
        self._camera = camera
        self._cam: Optional[ONVIFCamera] = None
        self._media = None
        self._ptz = None
        self._profile_token: Optional[str] = camera.profile_token or None
        self._connected = False
        self._lock = threading.Lock()
        self._connect()

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------
    def _persist_onvif_port(self, port: int) -> None:
        _common_persist_port(self._camera, port, "PTZ")

    def _candidate_ports(self) -> list[int]:
        return _common_candidate_ports(self._camera)

    def _build_cam(self, port: int) -> Optional[ONVIFCamera]:
        return _common_build_cam(self._camera, port, "PTZ")

    def _connect(self) -> None:
        ports = self._candidate_ports()
        logger.info(f"[PTZ] cam={self._camera.id} puertos candidatos: {ports}")
        cam = None
        working_port = None
        for port in ports:
            cam = self._build_cam(port)
            if cam is not None:
                working_port = port
                break

        if cam is None:
            logger.warning(
                f"[PTZ] cam={self._camera.id} ({self._camera.ip_address}): "
                f"no se pudo conectar en ninguno de los puertos {ports}"
            )
            return

        # Persistir el puerto que funcionó para la próxima vez
        self._persist_onvif_port(working_port)

        try:
            self._media = cam.create_media_service()
            profiles = self._media.GetProfiles()
            if not profiles:
                logger.warning(f"PTZ: cámara {self._camera.id} sin perfiles ONVIF")
                return

            # 1) si la cámara ya trae un profile_token guardado y tiene PTZ, usarlo
            chosen = None
            if self._profile_token:
                for p in profiles:
                    if p.token == self._profile_token and getattr(p, "PTZConfiguration", None):
                        chosen = p
                        break

            # 2) si no, buscar el primer perfil con PTZConfiguration
            if chosen is None:
                for p in profiles:
                    if getattr(p, "PTZConfiguration", None) is not None:
                        chosen = p
                        break

            if chosen is None:
                logger.info(f"Cámara {self._camera.id} no tiene perfiles con PTZ")
                return

            self._ptz = cam.create_ptz_service()
            # GetStatus valida que el endpoint PTZ responde con este perfil
            self._ptz.GetStatus({"ProfileToken": chosen.token})

            self._profile_token = chosen.token
            self._cam = cam
            self._connected = True
            logger.info(f"PTZ conectado para cámara {self._camera.id} "
                        f"(perfil={chosen.token})")
        except Exception as e:
            logger.warning(f"PTZ no disponible en cámara {self._camera.id}: {e}")
            self._connected = False

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def is_supported(self) -> bool:
        return self._connected

    def move(self, direction: str, speed: float = 0.5) -> bool:
        if not self._connected:
            return False
        if direction not in _VALID_DIRECTIONS:
            logger.warning(f"Dirección PTZ inválida: {direction}")
            return False

        speed = max(-1.0, min(1.0, float(speed)))

        with self._lock:
            try:
                req = self._ptz.create_type("ContinuousMove")
                req.ProfileToken = self._profile_token
                req.Velocity = self._velocity_for(direction, speed)
                self._ptz.ContinuousMove(req)
                return True
            except Exception as e:
                logger.error(f"Error PTZ ContinuousMove({direction}) cam {self._camera.id}: {e}")
                return False

    def _velocity_for(self, direction: str, speed: float) -> dict:
        """Construye el PTZVector según la dirección."""
        pan_tilt = {"x": 0.0, "y": 0.0}
        zoom = {"x": 0.0}

        if direction == "up":
            pan_tilt["y"] = speed
        elif direction == "down":
            pan_tilt["y"] = -speed
        elif direction == "left":
            pan_tilt["x"] = -speed
        elif direction == "right":
            pan_tilt["x"] = speed
        elif direction == "up_left":
            pan_tilt["x"], pan_tilt["y"] = -speed, speed
        elif direction == "up_right":
            pan_tilt["x"], pan_tilt["y"] = speed, speed
        elif direction == "down_left":
            pan_tilt["x"], pan_tilt["y"] = -speed, -speed
        elif direction == "down_right":
            pan_tilt["x"], pan_tilt["y"] = speed, -speed
        elif direction == "zoom_in":
            zoom["x"] = speed
        elif direction == "zoom_out":
            zoom["x"] = -speed

        return {"PanTilt": pan_tilt, "Zoom": zoom}

    def stop(self) -> bool:
        if not self._connected:
            return False
        with self._lock:
            try:
                req = self._ptz.create_type("Stop")
                req.ProfileToken = self._profile_token
                req.PanTilt = True
                req.Zoom = True
                self._ptz.Stop(req)
                return True
            except Exception as e:
                logger.error(f"Error PTZ Stop cam {self._camera.id}: {e}")
                return False

    def get_status(self) -> Optional[dict]:
        if not self._connected:
            return None
        try:
            status = self._ptz.GetStatus({"ProfileToken": self._profile_token})
            pos = getattr(status, "Position", None)
            return {
                "pan_tilt": {
                    "x": float(pos.PanTilt.x) if pos and pos.PanTilt else None,
                    "y": float(pos.PanTilt.y) if pos and pos.PanTilt else None,
                } if pos else None,
                "zoom": float(pos.Zoom.x) if pos and pos.Zoom else None,
                "moving": str(getattr(status, "MoveStatus", "")),
            }
        except Exception as e:
            logger.error(f"Error PTZ GetStatus cam {self._camera.id}: {e}")
            return None

    def get_presets(self) -> list[dict]:
        if not self._connected:
            return []
        try:
            req = self._ptz.create_type("GetPresets")
            req.ProfileToken = self._profile_token
            presets = self._ptz.GetPresets(req) or []
            return [{"token": p.token, "name": getattr(p, "Name", "")} for p in presets]
        except Exception as e:
            logger.error(f"Error obteniendo presets cam {self._camera.id}: {e}")
            return []

    def goto_preset(self, preset_token: str, speed: float = 0.5) -> bool:
        if not self._connected:
            return False
        speed = max(0.0, min(1.0, float(speed)))
        try:
            req = self._ptz.create_type("GotoPreset")
            req.ProfileToken = self._profile_token
            req.PresetToken = preset_token
            req.Speed = {"PanTilt": {"x": speed, "y": speed}, "Zoom": {"x": speed}}
            self._ptz.GotoPreset(req)
            return True
        except Exception as e:
            logger.error(f"Error yendo a preset {preset_token} cam {self._camera.id}: {e}")
            return False

    # Alias por compatibilidad con código previo
    go_to_preset = goto_preset

    def set_preset(self, name: str) -> Optional[str]:
        if not self._connected:
            return None
        try:
            req = self._ptz.create_type("SetPreset")
            req.ProfileToken = self._profile_token
            req.PresetName = name
            resp = self._ptz.SetPreset(req)
            return resp.PresetToken
        except Exception as e:
            logger.error(f"Error guardando preset cam {self._camera.id}: {e}")
            return None

    def remove_preset(self, preset_token: str) -> bool:
        if not self._connected:
            return False
        try:
            req = self._ptz.create_type("RemovePreset")
            req.ProfileToken = self._profile_token
            req.PresetToken = preset_token
            self._ptz.RemovePreset(req)
            return True
        except Exception as e:
            logger.error(f"Error removiendo preset {preset_token} cam {self._camera.id}: {e}")
            return False


class PTZManager:
    """
    Cachea PTZControllers por camera_id para evitar reconectar en cada comando.
    Thread-safe.
    """
    _instance: "Optional[PTZManager]" = None
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
        self._controllers: dict[int, PTZController] = {}
        self._lock = threading.Lock()

    def get(self, camera: Camera) -> PTZController:
        with self._lock:
            ctrl = self._controllers.get(camera.id)
            if ctrl is not None and ctrl.is_supported():
                return ctrl
            ctrl = PTZController(camera)
            self._controllers[camera.id] = ctrl
            return ctrl

    def drop(self, camera_id: int) -> None:
        with self._lock:
            self._controllers.pop(camera_id, None)


ptz_manager = PTZManager()
