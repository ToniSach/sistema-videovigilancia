import logging
import socket
import requests
from typing import List, Dict

from wsdiscovery.discovery import ThreadedWSDiscovery as WSDiscovery
from onvif import ONVIFCamera

logging.basicConfig(level=logging.INFO)


COMMON_PORTS = [80, 8000, 8080, 8899]
COMMON_CREDENTIALS = [
    ("admin", "admin"),
    ("admin", "12345"),
    ("admin", "123456"),
]


class ONVIFDiscovery:

    def __init__(self):
        self._wsd = WSDiscovery()

    # ===============================
    # PUBLIC
    # ===============================
    def discover(self, timeout: int = 5) -> List[Dict]:
        logging.info("🔍 Discovery profesional iniciado...")

        devices = []

        try:
            self._wsd.start()
            services = self._wsd.searchServices(timeout=timeout)

            for service in services:
                try:
                    xaddr = service.getXAddrs()[0]
                    ip = self._extract_ip(xaddr)

                    logging.info(f"➡ Detectado: {ip}")

                    # 1. Intentar ONVIF real
                    device = self._try_onvif(ip)

                    # 2. Fallback RTSP si falla ONVIF
                    if not device:
                        device = self._try_rtsp(ip)

                    if device:
                        devices.append(device)

                except Exception as e:
                    logging.warning(f"Error procesando servicio: {e}")

        finally:
            self._wsd.stop()

        logging.info(f"✅ Discovery finalizado: {len(devices)} dispositivos válidos")
        return devices

    # ===============================
    # ONVIF
    # ===============================
    def _try_onvif(self, ip: str) -> Dict | None:
        for port in COMMON_PORTS:
            for user, pwd in COMMON_CREDENTIALS:
                try:
                    cam = ONVIFCamera(ip, port, user, pwd)

                    dev = cam.create_devicemgmt_service()
                    info = dev.GetDeviceInformation()

                    media = cam.create_media_service()
                    profiles = media.GetProfiles()

                    if not profiles:
                        continue

                    profile = profiles[0]

                    uri = media.GetStreamUri({
                        'StreamSetup': {
                            'Stream': 'RTP-Unicast',
                            'Transport': {'Protocol': 'RTSP'}
                        },
                        'ProfileToken': profile.token
                    })

                    logging.info(f"✅ ONVIF válido: {ip}:{port}")

                    return {
                        "name": info.Model or "ONVIF Camera",
                        "ip_address": ip,
                        "rtsp_url": uri.Uri,
                        "onvif_url": f"http://{ip}:{port}/onvif/device_service",
                        "username": user,
                        "password": pwd,
                        "profile_token": profile.token,  # ✅ AGREGAR ESTA LÍNEA
                        "manufacturer": info.Manufacturer,
                        "model": info.Model,
                        "has_ptz": self._has_ptz(cam),
                        "has_audio": False,
                        "has_leds": False,
                        "fps": 15,
                        "resolution_width": 1920,
                        "resolution_height": 1080
                    }

                except Exception:
                    continue

        return None

    # ===============================
    # RTSP FALLBACK (NVR STYLE)
    # ===============================
    def _try_rtsp(self, ip: str) -> Dict | None:
        logging.info(f"⚠ Intentando RTSP fallback: {ip}")

        rtsp_patterns = [
            f"rtsp://admin:admin@{ip}:554/Streaming/Channels/101",  # Hikvision
            f"rtsp://admin:admin@{ip}:554/cam/realmonitor?channel=1&subtype=0",  # Dahua
            f"rtsp://admin:admin@{ip}:554/live",
            f"rtsp://{ip}:554/live.sdp",
        ]

        for url in rtsp_patterns:
            if self._check_rtsp(ip):
                logging.info(f"🎥 RTSP detectado: {ip}")
                return {
                    "name": "RTSP Camera",
                    "ip_address": ip,
                    "rtsp_url": url,
                    "onvif_url": "",
                    "username": "admin",
                    "password": "admin",
                    "manufacturer": "Unknown",
                    "model": "RTSP",
                    "has_ptz": False,
                    "has_audio": False,
                    "has_leds": False,
                    "fps": 15,
                    "resolution_width": 1920,
                    "resolution_height": 1080
                }

        return None

    # ===============================
    # HELPERS
    # ===============================
    def _extract_ip(self, xaddr: str) -> str:
        try:
            return xaddr.split("//")[1].split("/")[0].split(":")[0]
        except Exception:
            return "unknown"

    def _check_rtsp(self, ip: str) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((ip, 554))
            sock.close()
            return result == 0
        except Exception:
            return False

    def _has_ptz(self, cam: ONVIFCamera) -> bool:
        """Verifica PTZ de forma más robusta"""
        try:
            ptz = cam.create_ptz_service()
            # Hacer una llamada real para verificar que funciona
            media = cam.create_media_service()
            profiles = media.GetProfiles()
            if profiles:
                # Intentar obtener status (fallará si no hay PTZ real)
                ptz.GetStatus({"ProfileToken": profiles[0].token})
            return True
        except Exception as e:
            logging.debug(f"PTZ check failed: {e}")
            return False