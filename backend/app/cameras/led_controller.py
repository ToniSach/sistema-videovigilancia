"""Control de LEDs/IR Cut Filter via ONVIF Imaging Service con UsernameToken."""
from onvif import ONVIFCamera
from zeep.wsse import UsernameToken   # ✅ AÑADIDO
import logging
from ..database.models import Camera


class LEDController:
    def __init__(self, camera: Camera):
        self._camera = camera
        self._imaging = None
        self._video_source_token = None
        self._connected = False
        self._onvif_cam = None
        self._connect()

    def _connect(self) -> None:
        try:
            wsse = UsernameToken(self._camera.username, self._camera.password)
            self._onvif_cam = ONVIFCamera(
                self._camera.ip_address,
                80,
                self._camera.username,
                self._camera.password,
                wsse=wsse
            )
            self._imaging = self._onvif_cam.create_imaging_service()
            media = self._onvif_cam.create_media_service()
            profiles = media.GetProfiles()
            if profiles and len(profiles) > 0:
                profile = profiles[0]
                if hasattr(profile, "VideoSourceConfiguration") and profile.VideoSourceConfiguration:
                    self._video_source_token = profile.VideoSourceConfiguration.SourceToken
                else:
                    self._video_source_token = profile.token
            if self._video_source_token:
                self._connected = True
                logging.info(f"LED Control conectado para cámara {self._camera.id}")
            else:
                logging.warning(f"No se encontró video source token cámara {self._camera.id}")
        except Exception as e:
            logging.warning(f"LED control no disponible: {e}")
            self._connected = False

    def set_ir_cut_filter(self, mode: str) -> bool:
        if not self._connected:
            return False
        try:
            request = self._imaging.create_type("GetImagingSettings")
            request.VideoSourceToken = self._video_source_token
            settings = self._imaging.GetImagingSettings(request)
            settings.IrCutFilter = mode
            set_request = self._imaging.create_type("SetImagingSettings")
            set_request.VideoSourceToken = self._video_source_token
            set_request.ImagingSettings = settings
            self._imaging.SetImagingSettings(set_request)
            logging.info(f"IR Cut filter set to {mode} for camera {self._camera.id}")
            return True
        except Exception as e:
            logging.error(f"Error configurando IR filter: {e}")
            return False

    def turn_on(self) -> bool:
        return self.set_ir_cut_filter("OFF")

    def turn_off(self) -> bool:
        return self.set_ir_cut_filter("ON")

    def set_auto(self) -> bool:
        return self.set_ir_cut_filter("AUTO")

    def is_supported(self) -> bool:
        return self._connected