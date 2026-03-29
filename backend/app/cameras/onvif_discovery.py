import logging
import socket
import re
import os
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote, urlparse
from dataclasses import dataclass

from wsdiscovery.discovery import ThreadedWSDiscovery as WSDiscovery
from onvif import ONVIFCamera
from zeep.exceptions import Fault

logger = logging.getLogger(__name__)

# --- Configuración ---
# Ruta a los archivos WSDL (ubicación correcta)
# La ruta se construye a partir de la ubicación del paquete 'onvif' (que está en env/Lib/site-packages)
# y luego se busca la carpeta 'wsdl' dentro de ese directorio.
def _get_wsdl_dir() -> str:
    """Devuelve la ruta al directorio wsdl del paquete onvif."""
    import onvif
    # onvif.__file__ apunta a: env\Lib\site-packages\onvif\__init__.py
    package_dir = os.path.dirname(onvif.__file__)  # env\Lib\site-packages\onvif
    # Ahora subimos un nivel para llegar a site-packages, luego entramos a wsdl
    # Pero es más seguro usar la ruta directa que nos diste: env\Lib\site-packages\wsdl
    # Para ser genérico, podemos calcular:
    site_packages = os.path.dirname(package_dir)   # env\Lib\site-packages
    wsdl_dir = os.path.join(site_packages, "wsdl")
    if os.path.isdir(wsdl_dir):
        logger.debug(f"WSDL encontrado en: {wsdl_dir}")
        return wsdl_dir
    else:
        # Fallback: buscar en la misma carpeta del paquete (algunas instalaciones)
        alt_wsdl = os.path.join(package_dir, "wsdl")
        if os.path.isdir(alt_wsdl):
            logger.debug(f"WSDL alternativo en: {alt_wsdl}")
            return alt_wsdl
        logger.error("Directorio WSDL no encontrado")
        return None

WSDL_DIR = _get_wsdl_dir()

# Puertos ONVIF comunes
ONVIF_PORTS = [80, 8000, 8080, 8899]
RTSP_PORT = 554

# Credenciales comunes para prueba
COMMON_CREDENTIALS = [
    ("admin", "admin"),
    ("admin", "12345"),
    ("admin", "123456"),
    ("admin", ""),
    ("admin", "password"),
    ("root", "admin"),
    ("root", ""),
    ("user", "user"),
    ("guest", "guest"),
]

# Patrones RTSP por fabricante (para fallback)
RTSP_PATTERNS = {
    'hikvision': [
        'rtsp://{user}:{password}@{ip}:554/Streaming/Channels/101',
        'rtsp://{user}:{password}@{ip}:554/Streaming/Channels/102',
    ],
    'dahua': [
        'rtsp://{user}:{password}@{ip}:554/cam/realmonitor?channel=1&subtype=0',
        'rtsp://{user}:{password}@{ip}:554/cam/realmonitor?channel=1&subtype=1',
    ],
    'tp-link': [
        'rtsp://{user}:{password}@{ip}:554/stream1',
        'rtsp://{user}:{password}@{ip}:554/stream2',
    ],
    'xiongmai': [
        'rtsp://{user}:{password}@{ip}:554/av0_0',
        'rtsp://{user}:{password}@{ip}:554/av0_1',
    ],
    'generic': [
        'rtsp://{user}:{password}@{ip}:554/onvif1',
        'rtsp://{user}:{password}@{ip}:554/live.sdp',
        'rtsp://{user}:{password}@{ip}:554/live',
        'rtsp://{user}:{password}@{ip}:554/Streaming/Channels/1',
        'rtsp://{user}:{password}@{ip}:554/ch0_0.h264',
        'rtsp://{user}:{password}@{ip}:554/11',
        'rtsp://{user}:{password}@{ip}:554/1',
    ]
}


@dataclass
class ONVIFProfile:
    token: str
    name: str
    encoding: str
    width: int
    height: int
    fps: int
    quality: int  # 0-100, aproximado


class RobustONVIFClient:
    """Cliente ONVIF robusto con manejo de errores y selección de perfil óptimo."""

    def __init__(self, ip: str, port: int, user: str, pwd: str):
        self.ip = ip
        self.port = port
        self.user = user
        self.pwd = pwd
        self._cam: Optional[ONVIFCamera] = None
        self._media = None
        self._device = None

    def connect(self) -> bool:
        """Conecta a la cámara mediante ONVIF usando WSDL local."""
        try:
            # ✅ USAR WSDL LOCAL
            if WSDL_DIR:
                self._cam = ONVIFCamera(
                    self.ip, self.port, self.user, self.pwd,
                    wsdl_dir=WSDL_DIR,
                    encrypt=False,
                    no_cache=True
                )
            else:
                # Fallback: sin wsdl_dir (confiar en que la librería lo tiene)
                self._cam = ONVIFCamera(
                    self.ip, self.port, self.user, self.pwd,
                    encrypt=False,
                    no_cache=True
                )

            # Prueba rápida: obtener capacidades
            caps = self._cam.devicemgmt.GetCapabilities({'Category': 'All'})
            if not hasattr(caps, 'Media') or not caps.Media:
                logger.error(f"Cámara {self.ip} no tiene servicio Media")
                return False

            self._media = self._cam.create_media_service()
            self._device = self._cam.create_devicemgmt_service()
            return True

        except Fault as e:
            if 'NotAuthorized' in str(e):
                logger.debug(f"Credenciales inválidas para {self.ip}:{self.port}")
            else:
                logger.debug(f"Fault ONVIF en {self.ip}:{self.port} -> {e}")
            return False
        except Exception as e:
            logger.debug(f"Error conectando a {self.ip}:{self.port} -> {e}")
            return False

    def get_profiles(self) -> List[ONVIFProfile]:
        """Obtiene perfiles de video disponibles."""
        if not self._media:
            return []
        try:
            profiles = self._media.GetProfiles()
            valid = []

            for p in profiles:
                try:
                    if not hasattr(p, 'VideoEncoderConfiguration') or not p.VideoEncoderConfiguration:
                        continue

                    vec = p.VideoEncoderConfiguration
                    encoding = str(getattr(vec, 'Encoding', 'Unknown')).upper()
                    if encoding not in ['H264', 'H265', 'MJPEG']:
                        continue

                    res = vec.Resolution
                    width = getattr(res, 'Width', 0)
                    height = getattr(res, 'Height', 0)
                    if width == 0 or height == 0:
                        continue

                    # FPS
                    fps = 0
                    if hasattr(vec, 'RateControl') and vec.RateControl:
                        fps = getattr(vec.RateControl, 'FrameRateLimit', 0)
                    if fps == 0 and hasattr(vec, 'FrameRate'):
                        fps = vec.FrameRate
                    if fps == 0:
                        fps = 25

                    # Calidad aproximada (bitrate)
                    quality = 50
                    if hasattr(vec, 'RateControl') and vec.RateControl:
                        bitrate = getattr(vec.RateControl, 'BitrateLimit', 0)
                        if bitrate > 0:
                            quality = min(100, bitrate / 1000)

                    valid.append(ONVIFProfile(
                        token=p.token,
                        name=p.Name,
                        encoding=encoding,
                        width=int(width),
                        height=int(height),
                        fps=int(fps),
                        quality=int(quality)
                    ))
                except Exception as e:
                    logger.debug(f"Error parseando perfil: {e}")
                    continue

            return valid

        except Exception as e:
            logger.debug(f"Error en GetProfiles: {e}")
            return []

    def select_optimal_profile(self, profiles: List[ONVIFProfile]) -> Optional[ONVIFProfile]:
        """Selecciona el mejor perfil basado en calidad, resolución y fps."""
        if not profiles:
            return None

        # Priorizar H264
        h264 = [p for p in profiles if p.encoding == 'H264']
        candidates = h264 if h264 else profiles

        # Filtrar HD
        hd = [p for p in candidates if p.height <= 1080]
        if hd:
            candidates = hd

        def score(p: ONVIFProfile) -> int:
            s = 0
            if p.encoding == 'H264':
                s += 1000
            elif p.encoding == 'H265':
                s += 500
            if p.width == 1920 and p.height == 1080:
                s += 100
            elif p.width == 1280 and p.height == 720:
                s += 80
            if 15 <= p.fps <= 30:
                s += 50
            s += p.quality // 10
            return s

        candidates.sort(key=score, reverse=True)
        selected = candidates[0]
        logger.info(f"Perfil seleccionado: {selected.name} ({selected.encoding} "
                   f"{selected.width}x{selected.height} @ {selected.fps}fps, quality={selected.quality})")
        return selected

    def get_stream_url(self, profile_token: str) -> Optional[str]:
        """Obtiene la URL RTSP para un perfil dado."""
        if not self._media:
            return None
        try:
            uri = self._media.GetStreamUri({
                'StreamSetup': {
                    'Stream': 'RTP-Unicast',
                    'Transport': {'Protocol': 'RTSP', 'Tunnel': None}
                },
                'ProfileToken': profile_token
            })
            url = str(uri.Uri)
            if '@' not in url and self.user:
                url = url.replace('rtsp://', f'rtsp://{quote(self.user)}:{quote(self.pwd)}@', 1)
            return url
        except Exception as e:
            logger.debug(f"Error obteniendo stream URI: {e}")
            return None

    def test_profile(self, profile: ONVIFProfile) -> bool:
        """Prueba si el perfil es accesible (conexión TCP)."""
        url = self.get_stream_url(profile.token)
        if not url:
            return False
        try:
            match = re.search(r'rtsp://[^@]+@([^/]+)', url) or re.search(r'rtsp://([^/]+)', url)
            if not match:
                return False
            host_port = match.group(1)
            if ':' in host_port:
                host, port = host_port.split(':')
                port = int(port)
            else:
                host, port = host_port, 554
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def get_device_info(self) -> Tuple[str, str]:
        """Obtiene fabricante y modelo."""
        try:
            info = self._device.GetDeviceInformation()
            return info.Manufacturer, info.Model
        except Exception:
            return "Unknown", "Unknown"

    def has_audio(self) -> bool:
        """Determina si la cámara soporta audio."""
        try:
            profiles = self._media.GetProfiles()
            for p in profiles:
                if hasattr(p, 'AudioEncoderConfiguration') and p.AudioEncoderConfiguration:
                    return True
            return False
        except Exception:
            return False

    def has_ptz(self) -> bool:
        """Determina si la cámara soporta PTZ."""
        try:
            ptz = self._cam.create_ptz_service()
            media = self._cam.create_media_service()
            profiles = media.GetProfiles()
            if profiles:
                ptz.GetStatus({"ProfileToken": profiles[0].token})
            return True
        except Exception:
            return False


class ONVIFDiscovery:
    """Descubrimiento de cámaras ONVIF en la red local."""

    def __init__(self):
        self._wsd = WSDiscovery()

    def discover(self, timeout: int = 5) -> List[Dict]:
        """Descubre cámaras ONVIF en la red."""
        logger.info("🔍 Discovery profesional iniciado...")
        devices = []
        seen_ips = set()

        try:
            self._wsd.start()
            services = self._wsd.searchServices(timeout=timeout)

            for service in services:
                try:
                    # Filtrar por tipos ONVIF
                    types = service.getTypes()
                    if not any("onvif" in str(t).lower() for t in types):
                        continue

                    xaddr = service.getXAddrs()[0]
                    ip = self._extract_ip(xaddr)

                    # Ignorar IPv6 o IPs vacías
                    if not ip or ip.startswith('[') or ':' in ip:
                        continue

                    if ip in seen_ips:
                        continue
                    seen_ips.add(ip)

                    logger.info(f"➡ Detectado: {ip}")

                    device = self._try_onvif_robust(ip)
                    if device:
                        devices.append(device)
                        continue

                    # Fallback a RTSP
                    device = self._try_rtsp_fallback(ip)
                    if device:
                        devices.append(device)

                except Exception as e:
                    logger.warning(f"Error procesando servicio: {e}")

        finally:
            self._wsd.stop()

        logger.info(f"✅ Discovery finalizado: {len(devices)} dispositivos válidos")
        return devices

    def probe_single_ip(self, ip: str) -> Optional[Dict]:
        """Prueba una IP específica sin WS-Discovery."""
        # Intentar ONVIF primero
        device = self._try_onvif_robust(ip)
        if device:
            return device
        # Fallback RTSP
        return self._try_rtsp_fallback(ip)

    def _try_onvif_robust(self, ip: str) -> Optional[Dict]:
        """Intenta conexión ONVIF con todas las credenciales y puertos."""
        if not ip or ':' in ip:  # IPv6 no soportado en esta rutina
            return None

        for port in ONVIF_PORTS:
            for user, pwd in COMMON_CREDENTIALS:
                client = RobustONVIFClient(ip, port, user, pwd)
                if not client.connect():
                    continue
                profiles = client.get_profiles()
                if not profiles:
                    continue
                best = client.select_optimal_profile(profiles)
                if not best:
                    continue
                if not client.test_profile(best):
                    continue
                stream_url = client.get_stream_url(best.token)
                if not stream_url:
                    continue

                manufacturer, model = client.get_device_info()
                has_ptz = client.has_ptz()
                has_audio = client.has_audio()

                return {
                    "name": model or "ONVIF Camera",
                    "ip_address": ip,
                    "rtsp_url": stream_url,
                    "onvif_url": f"http://{ip}:{port}/onvif/device_service",
                    "username": user,
                    "password": pwd,
                    "profile_token": best.token,
                    "manufacturer": manufacturer,
                    "model": model,
                    "has_ptz": has_ptz,
                    "has_audio": has_audio,
                    "has_leds": False,
                    "fps": best.fps,
                    "resolution_width": best.width,
                    "resolution_height": best.height,
                    "connection_type": "onvif"
                }
        return None

    def _try_rtsp_fallback(self, ip: str) -> Optional[Dict]:
        """Intenta conexión RTSP directa (sin ONVIF)."""
        if not ip or ':' in ip:
            return None
        logger.info(f"⚠ Intentando RTSP fallback: {ip}")
        manufacturer = self._guess_manufacturer(ip)
        patterns = RTSP_PATTERNS.get(manufacturer, RTSP_PATTERNS['generic'])

        for user, pwd in COMMON_CREDENTIALS:
            for pattern in patterns:
                fmt_dict = {
                    'user': quote(user, safe=''),
                    'password': quote(pwd, safe=''),
                    'ip': ip
                }
                url = pattern.format(**fmt_dict)
                if self._test_rtsp_url(url):
                    logger.info(f"🎥 RTSP detectado: {url}")
                    return {
                        "name": f"RTSP Camera ({manufacturer})",
                        "ip_address": ip,
                        "rtsp_url": url,
                        "onvif_url": "",
                        "username": user,
                        "password": pwd,
                        "manufacturer": manufacturer,
                        "model": "RTSP",
                        "has_ptz": False,
                        "has_audio": False,
                        "has_leds": False,
                        "fps": 15,
                        "resolution_width": 1920,
                        "resolution_height": 1080,
                        "connection_type": "rtsp_fallback"
                    }
        return None

    def _guess_manufacturer(self, ip: str) -> str:
        """Intenta identificar el fabricante mediante HTTP GET."""
        try:
            import requests
            response = requests.get(f"http://{ip}", timeout=2, allow_redirects=True)
            data = response.text
            if 'Hikvision' in data:
                return 'hikvision'
            if 'Dahua' in data:
                return 'dahua'
            if 'TP-LINK' in data:
                return 'tp-link'
            if 'XiongMai' in data or 'XWebPlay' in data:
                return 'xiongmai'
        except:
            pass
        # Fallback por puerto Xiongmai
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            if sock.connect_ex((ip, 34567)) == 0:
                return 'xiongmai'
        except:
            pass
        return 'generic'

    def _test_rtsp_url(self, url: str) -> bool:
        """Prueba si una URL RTSP es accesible (conexión TCP al puerto)."""
        try:
            match = re.search(r'rtsp://([^/]+)', url)
            if not match:
                return False
            host_port = match.group(1)
            if ':' in host_port:
                host, port = host_port.split(':')
                port = int(port)
            else:
                host, port = host_port, 554
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def _extract_ip(self, xaddr: str) -> Optional[str]:
        """Extrae la dirección IP de una URL XAddr (http://ip:port/...)."""
        try:
            return urlparse(xaddr).hostname
        except:
            return None