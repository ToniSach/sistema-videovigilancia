from wsdiscovery.discovery import ThreadedWSDiscovery as WSDiscovery
from onvif import ONVIFCamera
import socket
import logging
import ipaddress
from urllib.parse import urlparse


class ONVIFDiscovery:
    """Descubridor de cámaras ONVIF mediante WS-Discovery y consulta de capacidades."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def discover(self, timeout: int = 5) -> list[dict]:
        """
        Descubre cámaras ONVIF en la red local.

        Args:
            timeout: Segundos de espera para respuestas WS-Discovery

        Returns:
            Lista de diccionarios con información de cada cámara
        """
        discovered_cameras = []
        wsd = None

        try:
            wsd = WSDiscovery()
            wsd.start()

            # Buscar servicios del tipo NetworkVideoTransmitter
            services = wsd.searchServices(serviceTypes=["http://www.onvif.org/ver10/network/wsdl/NetworkVideoTransmitter"], timeout=timeout)

            for service in services:
                try:
                    xaddrs = service.getXAddrs()
                    if not xaddrs:
                        continue

                    # Extraer IP del primer XAddr
                    xaddr = xaddrs[0]
                    parsed = urlparse(xaddr)
                    ip = parsed.hostname

                    if not ip or ipaddress.ip_address(ip).is_loopback:
                        continue

                    # Intentar conectar con credenciales por defecto
                    onvif_cam = ONVIFCamera(ip, 80, "admin", "admin", wsdl_dir=None)
                    camera_info = self._get_camera_info(onvif_cam, ip)
                    discovered_cameras.append(camera_info)

                except Exception as e:
                    self.logger.warning(f"Error al procesar servicio {service}: {e}")
                    continue

        except Exception as e:
            self.logger.error(f"Error en descubrimiento WS-Discovery: {e}")

        finally:
            if wsd:
                try:
                    wsd.stop()
                except:
                    pass

        return discovered_cameras

    def _get_camera_info(self, cam: ONVIFCamera, ip: str) -> dict:
        """
        Extrae información detallada de la cámara ONVIF.

        Args:
            cam: Instancia ONVIFCamera conectada
            ip: Dirección IP de la cámara

        Returns:
            Diccionario con información de la cámara
        """
        info = {
            "ip": ip,
            "onvif_url": f"http://{ip}:80/onvif/device_service",
            "manufacturer": "",
            "model": "",
            "firmware_version": "",
            "profiles": [],
            "rtsp_url": None,
            "profile_token": None,
            "has_ptz": False,
            "has_leds": False,
            "has_audio": False
        }

        try:
            # Obtener información del dispositivo
            devicemgmt = cam.create_devicemgmt_service()
            device_info = devicemgmt.GetDeviceInformation()
            info["manufacturer"] = device_info.Manufacturer
            info["model"] = device_info.Model
            info["firmware_version"] = device_info.FirmwareVersion
        except Exception as e:
            self.logger.warning(f"No se pudo obtener información del dispositivo {ip}: {e}")

        try:
            # Obtener perfiles de video
            media_service = cam.create_media_service()
            profiles = media_service.GetProfiles()

            for profile in profiles:
                profile_data = {
                    "token": profile.token,
                    "name": profile.Name,
                    "width": 0,
                    "height": 0,
                    "fps": 0
                }

                # Extraer resolución y FPS si están disponibles
                if profile.VideoEncoderConfiguration:
                    vec = profile.VideoEncoderConfiguration
                    if vec.Resolution:
                        profile_data["width"] = vec.Resolution.Width
                        profile_data["height"] = vec.Resolution.Height
                    if vec.RateControl:
                        profile_data["fps"] = vec.RateControl.FrameRateLimit

                info["profiles"].append(profile_data)

            # Obtener URI de stream para el primer perfil
            if profiles:
                first_profile = profiles[0]
                info["profile_token"] = first_profile.token

                stream_setup = {
                    'Stream': 'RTP_unicast',
                    'Transport': {'Protocol': 'RTSP', 'Tunnel': None}
                }
                uri = media_service.GetStreamUri({
                    'StreamSetup': stream_setup,
                    'ProfileToken': first_profile.token
                })
                info["rtsp_url"] = uri.Uri

        except Exception as e:
            self.logger.warning(f"No se pudo obtener perfiles de media {ip}: {e}")

        try:
            # Verificar soporte PTZ
            ptz_service = cam.create_ptz_service()
            ptz_status = ptz_service.GetStatus({'ProfileToken': info["profile_token"]}) if info["profile_token"] else None
            if ptz_status:
                info["has_ptz"] = True
        except:
            info["has_ptz"] = False

        try:
            # Verificar soporte de Imaging (LEDs/IR)
            imaging_service = cam.create_imaging_service()
            # Intentar obtener opciones de imaging para verificar soporte
            if info["profiles"]:
                imaging_opts = imaging_service.GetOptions({'VideoSourceToken': info["profile_token"]})
                if imaging_opts:
                    info["has_leds"] = True
        except:
            info["has_leds"] = False

        try:
            # Verificar soporte de audio via media profiles
            audio_present = False
            for profile in info["profiles"]:
                # Verificar si hay configuración de audio en el perfil original
                media_service = cam.create_media_service()
                full_profile = media_service.GetProfile({'ProfileToken': profile["token"]})
                if hasattr(full_profile, 'AudioEncoderConfiguration') and full_profile.AudioEncoderConfiguration:
                    audio_present = True
                    break
            info["has_audio"] = audio_present
        except:
            info["has_audio"] = False

        return info