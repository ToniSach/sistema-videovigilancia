"""
Control PTZ (Pan-Tilt-Zoom) via ONVIF
"""
from onvif import ONVIFCamera
import logging
from typing import Optional
from ..database.models import Camera


class PTZController:
    """Controlador PTZ usando ONVIF Profile S/T"""

    def __init__(self, camera: Camera):
        self._camera = camera
        self._ptz = None
        self._media = None
        self._profile_token = camera.profile_token
        self._connected = False
        self._onvif_cam = None

        self._connect()

    def _connect(self) -> None:
        """Establece conexión ONVIF con la cámara"""
        try:
            self._onvif_cam = ONVIFCamera(
                self._camera.ip_address, 
                80, 
                self._camera.username, 
                self._camera.password
            )
            self._media = self._onvif_cam.create_media_service()
            
            # ✅ AGREGAR: Verificar si el profile tiene PTZ antes de crear el servicio
            profiles = self._media.GetProfiles()
            if not profiles:
                self._connected = False
                return
                
            profile = profiles[0]
            
            # Verificar si el profile tiene configuración PTZ
            if not hasattr(profile, 'PTZConfiguration') or profile.PTZConfiguration is None:
                logging.warning(f"Cámara {self._camera.id} no tiene configuración PTZ en el profile")
                self._connected = False
                return
            
            # Intentar crear servicio PTZ con manejo de error específico
            try:
                self._ptz = self._onvif_cam.create_ptz_service()
                # Verificar que realmente funciona haciendo una llamada de prueba
                status = self._ptz.GetStatus({"ProfileToken": profile.token})
                self._profile_token = profile.token
                self._connected = True
                logging.info(f"PTZ conectado para cámara {self._camera.id}")
            except Exception as ptz_error:
                logging.warning(f"PTZ no disponible para cámara {self._camera.id}: {ptz_error}")
                self._connected = False
                
        except Exception as e:
            logging.warning(f"ONVIF no disponible para cámara {self._camera.id}: {e}")
            self._connected = False

    def move(self, direction: str, speed: float = 0.5) -> bool:
        """
        Mueve la cámara en la dirección especificada.

        Args:
            direction: up, down, left, right, zoom_in, zoom_out
            speed: Velocidad de movimiento (0.0 a 1.0)
        """
        if not self._connected:
            return False

        try:
            # Crear request de velocidad
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
                logging.error(f"Dirección PTZ inválida: {direction}")
                return False

            # Crear request de movimiento continuo
            request = self._ptz.create_type("ContinuousMove")
            request.ProfileToken = self._profile_token
            request.Velocity = velocity

            self._ptz.ContinuousMove(request)
            return True

        except Exception as e:
            logging.error(f"Error moviendo PTZ cámara {self._camera.id}: {e}")
            return False

    def stop(self) -> bool:
        """Detiene el movimiento PTZ"""
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
            logging.error(f"Error deteniendo PTZ cámara {self._camera.id}: {e}")
            return False

    def get_presets(self) -> list[dict]:
        """Obtiene lista de presets PTZ guardados"""
        if not self._connected:
            return []

        try:
            request = self._ptz.create_type("GetPresets")
            request.ProfileToken = self._profile_token

            presets = self._ptz.GetPresets(request)
            return [
                {"token": preset.token, "name": preset.Name}
                for preset in presets
            ]
        except Exception as e:
            logging.error(f"Error obteniendo presets cámara {self._camera.id}: {e}")
            return []

    def go_to_preset(self, preset_token: str) -> bool:
        """Mueve la cámara a un preset específico"""
        if not self._connected:
            return False

        try:
            request = self._ptz.create_type("GotoPreset")
            request.ProfileToken = self._profile_token
            request.PresetToken = preset_token

            # Velocidad de movimiento al preset
            request.Speed = {
                "PanTilt": {"x": 0.5, "y": 0.5},
                "Zoom": {"x": 0.5}
            }

            self._ptz.GotoPreset(request)
            return True
        except Exception as e:
            logging.error(f"Error yendo a preset cámara {self._camera.id}: {e}")
            return False

    def set_preset(self, name: str) -> Optional[str]:
        """Guarda posición actual como preset"""
        if not self._connected:
            return None

        try:
            request = self._ptz.create_type("SetPreset")
            request.ProfileToken = self._profile_token
            request.PresetName = name

            response = self._ptz.SetPreset(request)
            return response.PresetToken
        except Exception as e:
            logging.error(f"Error guardando preset cámara {self._camera.id}: {e}")
            return None

    def is_supported(self) -> bool:
        """Retorna True si PTZ está disponible"""
        return self._connected
