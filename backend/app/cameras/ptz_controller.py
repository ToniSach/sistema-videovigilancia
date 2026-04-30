"""Control PTZ (Pan-Tilt-Zoom) via ONVIF con autenticación UsernameToken (método compatible)."""
from onvif import ONVIFCamera
from zeep import Client
from zeep.wsse import UsernameToken
from zeep.transports import Transport
import requests
import logging
from urllib.parse import urlparse
from typing import Optional
from ..database.models import Camera
import logging
logging.basicConfig(level=logging.DEBUG)


class PTZController:
    def __init__(self, camera: Camera):
        self._camera = camera
        self._ptz = None
        self._media = None
        self._profile_token = camera.profile_token
        self._connected = False
        self._onvif_cam = None
        self._connect()

    def _get_onvif_base_url(self):
        if self._camera.onvif_url:
            parsed = urlparse(self._camera.onvif_url)
            return f"{parsed.scheme}://{parsed.netloc}"
        return f"http://{self._camera.ip_address}:80"

    def _create_zeep_client(self, wsdl_url):
        """Crea un cliente zeep con autenticación UsernameToken."""
        session = requests.Session()
        transport = Transport(session=session, timeout=10)
        client = Client(wsdl_url, transport=transport, wsse=UsernameToken(self._camera.username, self._camera.password))
        return client

    def _connect(self) -> None:
        try:
            base_url = self._get_onvif_base_url()
            # URLs WSDL
            device_wsdl = f"{base_url}/onvif/device_service?wsdl"
            media_wsdl = f"{base_url}/onvif/media_service?wsdl"
            ptz_wsdl = f"{base_url}/onvif/ptz_service?wsdl"

            # Crear cliente ONVIF sin wsse (no lo soporta)
            self._onvif_cam = ONVIFCamera(
                self._camera.ip_address,
                urlparse(base_url).port or 80,
                self._camera.username,
                self._camera.password,
                no_cache=True
            )
            
            # Reemplazar el transporte de los servicios con uno que tenga wsse
            # Método: crear servicios manualmente
            from onvif import ONVIFService
            
            # Servicio Media
            media_client = self._create_zeep_client(media_wsdl)
            self._media = ONVIFService(media_client, 'Media', 'http://www.onvif.org/ver10/media/wsdl')
            
            # Obtener perfiles
            profiles = self._media.GetProfiles()
            if not profiles:
                self._connected = False
                return
            
            profile = profiles[0]
            if not hasattr(profile, 'PTZConfiguration') or profile.PTZConfiguration is None:
                logging.warning(f"Cámara {self._camera.id} no tiene configuración PTZ")
                self._connected = False
                return
            
            # Servicio PTZ
            ptz_client = self._create_zeep_client(ptz_wsdl)
            self._ptz = ONVIFService(ptz_client, 'PTZ', 'http://www.onvif.org/ver20/ptz/wsdl')
            
            # Probar conexión
            self._ptz.GetStatus({"ProfileToken": profile.token})
            self._profile_token = profile.token
            self._connected = True
            logging.info(f"PTZ conectado para cámara {self._camera.id} (puerto {urlparse(base_url).port})")
            
        except Exception as e:
            logging.warning(f"ONVIF no disponible: {e}")
            self._connected = False

    def move(self, direction: str, speed: float = 0.5) -> bool:
        if not self._connected:
            return False
        try:
            velocity = self._ptz.create_type("PTZVector")
            if direction == "up":
                velocity.PanTilt = {"x": 0.0, "y": speed}
            elif direction == "down":
                velocity.PanTilt = {"x": 0.0, "y": -speed}
            elif direction == "left":
                velocity.PanTilt = {"x": -speed, "y": 0.0}
            elif direction == "right":
                velocity.PanTilt = {"x": speed, "y": 0.0}
            elif direction == "zoom_in":
                velocity.Zoom = {"x": speed}
            elif direction == "zoom_out":
                velocity.Zoom = {"x": -speed}
            else:
                return False
            request = self._ptz.create_type("ContinuousMove")
            request.ProfileToken = self._profile_token
            request.Velocity = velocity
            self._ptz.ContinuousMove(request)
            return True
        except Exception as e:
            logging.error(f"Error moviendo PTZ: {e}")
            return False

    def stop(self) -> bool:
        if not self._connected:
            return False
        try:
            request = self._ptz.create_type("Stop")
            request.ProfileToken = self._profile_token
            request.PanTilt = True
            request.Zoom = True
            self._ptz.Stop(request)
            return True
        except Exception as e:
            logging.error(f"Error deteniendo PTZ: {e}")
            return False

    def get_presets(self) -> list[dict]:
        if not self._connected:
            return []
        try:
            request = self._ptz.create_type("GetPresets")
            request.ProfileToken = self._profile_token
            presets = self._ptz.GetPresets(request)
            return [{"token": preset.token, "name": preset.Name} for preset in presets]
        except Exception as e:
            logging.error(f"Error obteniendo presets: {e}")
            return []

    def go_to_preset(self, preset_token: str) -> bool:
        if not self._connected:
            return False
        try:
            request = self._ptz.create_type("GotoPreset")
            request.ProfileToken = self._profile_token
            request.PresetToken = preset_token
            request.Speed = {"PanTilt": {"x": 0.5, "y": 0.5}, "Zoom": {"x": 0.5}}
            self._ptz.GotoPreset(request)
            return True
        except Exception as e:
            logging.error(f"Error yendo a preset: {e}")
            return False

    def set_preset(self, name: str) -> Optional[str]:
        if not self._connected:
            return None
        try:
            request = self._ptz.create_type("SetPreset")
            request.ProfileToken = self._profile_token
            request.PresetName = name
            response = self._ptz.SetPreset(request)
            return response.PresetToken
        except Exception as e:
            logging.error(f"Error guardando preset: {e}")
            return None

    def is_supported(self) -> bool:
        return self._connected