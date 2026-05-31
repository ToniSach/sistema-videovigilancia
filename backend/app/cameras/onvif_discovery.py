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
from zeep.wsse import UsernameToken

logger = logging.getLogger(__name__)

# El paquete `wsdiscovery` loggea constantemente:
#   "daemon - WARNING - could not find handler for: _handle_probe"
# porque su implementación recibe paquetes de WS-Discovery que no son responses
# (son probes de otros dispositivos en la red). Es ruido benigno; lo silenciamos.
logging.getLogger("daemon").setLevel(logging.ERROR)

# --- Configuración ---
def _get_wsdl_dir() -> str:
    import onvif
    package_dir = os.path.dirname(onvif.__file__)
    site_packages = os.path.dirname(package_dir)
    wsdl_dir = os.path.join(site_packages, "wsdl")
    if os.path.isdir(wsdl_dir):
        logger.debug(f"WSDL encontrado en: {wsdl_dir}")
        return wsdl_dir
    alt_wsdl = os.path.join(package_dir, "wsdl")
    if os.path.isdir(alt_wsdl):
        logger.debug(f"WSDL alternativo en: {alt_wsdl}")
        return alt_wsdl
    logger.error("Directorio WSDL no encontrado")
    return None

WSDL_DIR = _get_wsdl_dir()

ONVIF_PORTS = [80, 8000, 8080, 8899]
RTSP_PORT = 554

# =============================================================================
# Credenciales comunes para cámaras IP / ONVIF
# =============================================================================
# Recopiladas de:
#   - Defaults documentados por fabricante (Hikvision, Dahua, Axis, Sony...)
#   - Bases de datos públicas como cirt.net/passwords y router-network.com
#   - Listas filtradas de Mirai/Hajime (cámaras chinas baratas: XiongMai, TVT, etc.)
#
# Orden: primero las más comunes para fallar rápido.
COMMON_CREDENTIALS = [
    # ---------- Top universales ----------
    ("admin", "admin"),
    ("admin", ""),
    ("admin", "12345"),
    ("admin", "123456"),
    ("admin", "1234"),
    ("admin", "password"),
    ("admin", "Admin123"),
    ("admin", "admin123"),
    ("admin", "admin1234"),
    ("admin", "admin@123"),

    # ---------- Patrones numéricos (cámaras chinas) ----------
    ("admin", "111111"),
    ("admin", "000000"),
    ("admin", "666666"),
    ("admin", "888888"),
    ("admin", "999999"),
    ("admin", "54321"),
    ("admin", "1111"),
    ("admin", "9999"),

    # ---------- root ----------
    ("root", "root"),
    ("root", "admin"),
    ("root", ""),
    ("root", "12345"),
    ("root", "pass"),
    ("root", "password"),
    ("root", "calmonitor"),    # CCTV-Calmonitor
    ("root", "xc3511"),        # Mirai (XiongMai)
    ("root", "vizxv"),         # Mirai (Dahua)
    ("root", "888888"),
    ("root", "666666"),

    # ---------- Usuarios secundarios ----------
    ("user", "user"),
    ("user", ""),
    ("user", "1234"),
    ("guest", "guest"),
    ("guest", ""),
    ("viewer", "viewer"),
    ("viewer", ""),
    ("operator", "operator"),
    ("supervisor", "supervisor"),
    ("service", "service"),
    ("support", "support"),
    ("ubnt", "ubnt"),             # Ubiquiti
    ("supervisor", "supervisor"),

    # ---------- Específicos por fabricante (defaults documentados) ----------
    # Hikvision default → admin/12345 (forzada a cambiar en firmware nuevo)
    # Dahua → admin/admin
    # Foscam → admin/(empty)
    # Axis → root/(viene impreso, suele ser 'pass' o vacío en demos)
    ("Admin", "1111"),            # Anpviz
    ("Admin", "admin"),
    ("Administrator", "admin"),
    ("Administrator", "1234"),
    ("888", "888"),               # XiongMai web admin
    ("666666", "666666"),
    ("ubuntu", "ubuntu"),

    # ---------- Variantes case ----------
    ("ADMIN", "ADMIN"),
    ("Admin", "Admin"),
    ("admin", "ADMIN"),
]

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
    quality: int


class RobustONVIFClient:
    def __init__(self, ip: str, port: int, user: str, pwd: str):
        self.ip = ip
        self.port = port
        self.user = user
        self.pwd = pwd
        self._cam: Optional[ONVIFCamera] = None
        self._media = None
        self._device = None

    def connect(self) -> bool:
        """
        Conecta a la cámara mediante ONVIF.
        Primero intenta con HTTP Basic Auth (sin wsse). Si falla por autenticación,
        reintenta con UsernameToken (WS-Security) como fallback.
        """
        # Intento 1: HTTP Basic Auth (estándar)
        if self._try_connect_with_auth(use_wsse=False):
            return True
        # Intento 2: UsernameToken (fallback)
        logger.debug(f"Fallback con UsernameToken para {self.ip}:{self.port}")
        return self._try_connect_with_auth(use_wsse=True)

    def _try_connect_with_auth(self, use_wsse: bool) -> bool:
        try:
            kwargs = {
                'encrypt': False,
                'no_cache': True
            }
            if WSDL_DIR:
                kwargs['wsdl_dir'] = WSDL_DIR

            if use_wsse:
                wsse = UsernameToken(self.user, self.pwd)
                kwargs['wsse'] = wsse
                # No pasar user/pwd como parámetros si usamos wsse? En onvif-zeep se puede pasar también.
                # Mejor pasar user/pwd igualmente.
                self._cam = ONVIFCamera(self.ip, self.port, self.user, self.pwd, **kwargs)
            else:
                # HTTP Basic Auth: no pasar wsse
                self._cam = ONVIFCamera(self.ip, self.port, self.user, self.pwd, **kwargs)

            caps = self._cam.devicemgmt.GetCapabilities({'Category': 'All'})
            if not hasattr(caps, 'Media') or not caps.Media:
                logger.error(f"Cámara {self.ip} no tiene servicio Media")
                return False

            self._media = self._cam.create_media_service()
            self._device = self._cam.create_devicemgmt_service()
            return True

        except Fault as e:
            if 'NotAuthorized' in str(e) or 'Authentication' in str(e):
                logger.debug(f"Credenciales inválidas para {self.ip}:{self.port}")
            else:
                logger.debug(f"Fault ONVIF en {self.ip}:{self.port} -> {e}")
            return False
        except Exception as e:
            logger.debug(f"Error conectando a {self.ip}:{self.port} -> {e}")
            return False

    def get_profiles(self) -> List[ONVIFProfile]:
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
                    fps = 0
                    if hasattr(vec, 'RateControl') and vec.RateControl:
                        fps = getattr(vec.RateControl, 'FrameRateLimit', 0)
                    if fps == 0 and hasattr(vec, 'FrameRate'):
                        fps = vec.FrameRate
                    if fps == 0:
                        fps = 25
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
        if not profiles:
            return None
        h264 = [p for p in profiles if p.encoding == 'H264']
        candidates = h264 if h264 else profiles
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
        try:
            info = self._device.GetDeviceInformation()
            return info.Manufacturer, info.Model
        except Exception:
            return "Unknown", "Unknown"

    def has_audio(self) -> bool:
        try:
            profiles = self._media.GetProfiles()
            for p in profiles:
                if hasattr(p, 'AudioEncoderConfiguration') and p.AudioEncoderConfiguration:
                    return True
            return False
        except Exception:
            return False

    def has_ptz(self) -> bool:
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
    def __init__(self):
        self._wsd = WSDiscovery()

    @staticmethod
    def _is_reachable(ip: str, ports: Optional[list] = None,
                      timeout: float = 1.0) -> bool:
        """
        Pre-check rápido de alcanzabilidad TCP. Útil para descartar IPs
        encontradas vía WS-Discovery (multicast L2) que en realidad están en
        una subred distinta y dan timeout largo al intentar ONVIF.

        Ejemplo real: cámara con IP estática 192.168.1.19 conectada a un router
        que asigna 10.99.130.x. El multicast la encuentra pero el TCP a su IP
        es no-enrutable desde el host → timeout 5-30s × 4 puertos × N creds.

        Devuelve True si AL MENOS un puerto responde en `timeout` segundos.
        """
        if not ip or ':' in ip:
            return False
        ports = ports or (ONVIF_PORTS + [RTSP_PORT])
        for port in ports:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                rc = sock.connect_ex((ip, port))
                sock.close()
                if rc == 0:
                    return True
            except Exception:
                continue
        return False

    def discover(self, timeout: int = 120, subnet_scan: bool = True) -> List[Dict]:
        """
        Lanza descubrimiento WS-Discovery + sondeo ONVIF.

        Args:
            timeout: segundos máximos que WS-Discovery escucha respuestas
                     (la librería bloquea TODO ese tiempo, sin early-exit).
                     Para arranque rápido: 10-15s. Para escaneo "completo": 60-120s.
            subnet_scan: si True y WS-Discovery devuelve 0 cámaras, escanea
                     el subnet local /24. Pone esto en False si solo necesitas
                     una pasada rápida (p. ej. para un popup de bienvenida).

        Cuando WS-Discovery no devuelve nada (común en Windows con firewall
        bloqueando multicast 239.255.255.250:3702) y `subnet_scan=True`,
        cae a un escaneo del subnet local probando ONVIF en :80/:8000/:8080/:8899.
        """
        import time as _t
        t_start = _t.time()
        # Tiempo total máximo del discover. Si lo excedemos, devolvemos lo que
        # tengamos hasta el momento en vez de seguir gastando tiempo.
        # 90s deja margen al cliente desktop (HTTP timeout = 180s ahora).
        MAX_TOTAL = 90

        logger.info(
            f"[DISCOVERY] Iniciando descubrimiento ONVIF (timeout WS-Disc={timeout}s, "
            f"cap total={MAX_TOTAL}s)"
        )
        devices = []
        unreachable: list[dict] = []  # IPs encontradas pero no alcanzables
        seen_ips: set[str] = set()
        wsd_count = 0

        # ----- 1) WS-Discovery (multicast) ----------------------------------
        try:
            logger.info("[DISCOVERY] Paso 1: lanzando WS-Discovery (multicast 239.255.255.250:3702)")
            self._wsd.start()
            services = self._wsd.searchServices(timeout=timeout)
            wsd_count = len(services) if services else 0
            logger.info(f"[DISCOVERY] WS-Discovery encontró {wsd_count} servicio(s)")

            for idx, service in enumerate(services or []):
                if _t.time() - t_start > MAX_TOTAL:
                    logger.warning(
                        f"[DISCOVERY] Cap de {MAX_TOTAL}s alcanzado en WS-Disc loop; "
                        f"abortando y devolviendo {len(devices)} cámara(s)"
                    )
                    break
                try:
                    types = service.getTypes()
                    xaddrs = service.getXAddrs()
                    logger.info(
                        f"[DISCOVERY] svc[{idx}] types={[str(t) for t in types]} xaddrs={xaddrs}"
                    )
                    if not any("onvif" in str(t).lower() for t in types):
                        logger.info(f"[DISCOVERY] svc[{idx}] descartado: no es ONVIF")
                        continue
                    if not xaddrs:
                        continue
                    ip = self._extract_ip(xaddrs[0])
                    if not ip or ip.startswith('[') or ':' in ip:
                        logger.info(f"[DISCOVERY] svc[{idx}] IP descartada: {ip!r}")
                        continue
                    if ip in seen_ips:
                        continue
                    seen_ips.add(ip)

                    # Pre-check de alcanzabilidad: la cámara puede anunciar una
                    # IP estática que está en otra subred (típico cuando cambias
                    # de router). Sin este filtro, probaríamos ONVIF y RTSP
                    # contra una IP no-enrutable y tardaríamos minutos.
                    if not self._is_reachable(ip, timeout=1.0):
                        logger.warning(
                            f"[DISCOVERY] svc[{idx}] IP {ip} anunciada por WS-Discovery "
                            f"pero NO ALCANZABLE desde este host (¿IP estática de otra "
                            f"subred?). Saltando; configura la cámara en DHCP o cámbiale "
                            f"la IP a la subred actual."
                        )
                        unreachable.append({
                            "ip_address": ip,
                            "name": f"Cámara no alcanzable ({ip})",
                            "manufacturer": "Desconocido",
                            "model": "Desconocido",
                            "connection_type": "unreachable",
                            "reason": (
                                f"IP {ip} anunciada por la cámara vía WS-Discovery, "
                                f"pero no hay conectividad TCP. Probablemente tiene "
                                f"IP estática de otra subred. Cámbiale la IP desde "
                                f"su panel web (conéctate por cable directo con un PC "
                                f"que tenga IP en {ip.rsplit('.', 1)[0]}.x)."
                            ),
                        })
                        continue

                    logger.info(f"[DISCOVERY] svc[{idx}] → IP {ip}, probando ONVIF...")
                    device = self._try_onvif_robust(ip)
                    if device:
                        devices.append(device)
                        logger.info(f"[DISCOVERY] ✓ {ip}: ONVIF OK ({device.get('manufacturer')}/{device.get('model')})")
                        continue
                    logger.info(f"[DISCOVERY] {ip}: ONVIF falló, probando RTSP fallback...")
                    device = self._try_rtsp_fallback(ip)
                    if device:
                        devices.append(device)
                        logger.info(f"[DISCOVERY] ✓ {ip}: RTSP fallback OK")
                except Exception as e:
                    logger.warning(f"[DISCOVERY] Error procesando svc[{idx}]: {e}")
        except Exception as e:
            logger.error(f"[DISCOVERY] Error en WS-Discovery: {e}")
        finally:
            try:
                self._wsd.stop()
            except Exception:
                pass

        # ----- 2) Fallback: subnet scan ------------------------------------
        # Si WS-Discovery encontró 0 cámaras Y el caller pidió subnet_scan,
        # escanear el subnet local /24. Muy útil cuando el firewall bloquea
        # multicast, pero es LENTO (~30-60s) — por eso es opt-in.
        elapsed = _t.time() - t_start
        if not devices and subnet_scan and elapsed < MAX_TOTAL:
            remaining = MAX_TOTAL - elapsed
            logger.warning(
                f"[DISCOVERY] WS-Discovery no encontró cámaras alcanzables. "
                f"Cayendo a subnet scan (cap={remaining:.0f}s)..."
            )
            scanned = self._subnet_scan(seen_ips=seen_ips, deadline=t_start + MAX_TOTAL)
            devices.extend(scanned)
        elif not devices:
            logger.info(
                f"[DISCOVERY] WS-Discovery sin resultados "
                f"(subnet_scan={subnet_scan}, elapsed={elapsed:.0f}s/{MAX_TOTAL}s)"
            )

        # Adjuntar IPs no alcanzables al resultado para que el cliente las
        # muestre con la advertencia (no son cámaras agregables, solo informativo).
        if unreachable:
            logger.info(
                f"[DISCOVERY] Reportando {len(unreachable)} cámara(s) no alcanzable(s) "
                f"al cliente para que el usuario las vea"
            )
            devices.extend(unreachable)

        logger.info(
            f"[DISCOVERY] ✅ Finalizado en {_t.time() - t_start:.1f}s: "
            f"{len(devices) - len(unreachable)} cámara(s) alcanzable(s), "
            f"{len(unreachable)} no alcanzable(s)"
        )
        return devices

    def _local_ipv4_subnets(self) -> List[str]:
        """Devuelve los prefijos /24 de las interfaces locales (sin loopback)."""
        prefixes: list[str] = []
        try:
            import ifaddr  # dependencia indirecta de wsdiscovery
            for adapter in ifaddr.get_adapters():
                for ip in adapter.ips:
                    if not ip.is_IPv4:
                        continue
                    addr = ip.ip
                    if not isinstance(addr, str):
                        continue
                    if addr.startswith("127.") or addr.startswith("169.254."):
                        continue
                    parts = addr.split(".")
                    if len(parts) == 4:
                        prefixes.append(".".join(parts[:3]))
        except Exception as e:
            logger.warning(f"[DISCOVERY] No se pudo enumerar interfaces: {e}")

        # Deduplicar y aportar fallbacks comunes si no encontró nada
        seen = set()
        result = []
        for p in prefixes:
            if p not in seen:
                seen.add(p)
                result.append(p)
        if not result:
            result = ["192.168.1", "192.168.0"]
            logger.info("[DISCOVERY] Sin interfaces detectadas, probando con 192.168.0/24 y 192.168.1/24")
        return result

    def _local_ipv4_addresses(self) -> set[str]:
        """Devuelve TODAS las IPv4 de las interfaces locales (para excluir del scan)."""
        addrs: set[str] = set()
        try:
            import ifaddr
            for adapter in ifaddr.get_adapters():
                for ip in adapter.ips:
                    if not ip.is_IPv4:
                        continue
                    addr = ip.ip
                    if isinstance(addr, str):
                        addrs.add(addr)
        except Exception:
            pass
        return addrs

    def _subnet_scan(self, seen_ips: set[str],
                     deadline: Optional[float] = None) -> List[Dict]:
        """
        Escanea cada IP del subnet /24 buscando un puerto ONVIF abierto.
        Si lo encuentra, intenta ONVIF; si falla, RTSP fallback.

        Heurística importante: en subnet scan probamos solo un subset reducido
        de credenciales comunes (top ~6) — iterar las 50+ de COMMON_CREDENTIALS
        contra cada IP del subnet puede tardar minutos. Si el usuario quiere
        forzar la cámara, la agregará luego desde el diálogo con sus credenciales.
        Tampoco caemos al SLOW PATH onvif-zeep.

        Args:
            deadline: timestamp absoluto (time.time()) tras el cual abortar.
        """
        import concurrent.futures
        import time as _t

        prefixes = self._local_ipv4_subnets()
        # Excluir IPs de las interfaces locales (incluida la del propio host).
        # Antes, probar 50 credenciales × 4 puertos contra nuestra propia IP
        # consumía decenas de segundos sin sentido.
        local_addrs = self._local_ipv4_addresses()
        candidates: list[str] = []
        for prefix in prefixes:
            for octet in range(1, 255):
                ip = f"{prefix}.{octet}"
                if ip in seen_ips or ip in local_addrs:
                    continue
                candidates.append(ip)

        logger.info(
            f"[DISCOVERY] Subnet scan: {len(candidates)} IPs en {prefixes} "
            f"(excluidas locales: {sorted(local_addrs)})"
        )

        # Incluimos RTSP 554 en el pre-filtro porque muchas cámaras baratas SOLO
        # exponen RTSP, sin ONVIF accesible. Sin esto, una cámara solo-RTSP no
        # aparecía nunca en el scan aunque estuviera viva en la red.
        SCAN_PORTS = ONVIF_PORTS + [RTSP_PORT]

        def open_ports(ip: str) -> list[int]:
            """Devuelve la lista de puertos ONVIF/RTSP abiertos en `ip`."""
            opened: list[int] = []
            for port in SCAN_PORTS:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(0.25)
                    if sock.connect_ex((ip, port)) == 0:
                        opened.append(port)
                    sock.close()
                except Exception:
                    pass
            return opened

        # Pre-filtro paralelo agresivo: 64 workers en paralelo, timeout corto.
        # 254 IPs × 5 puertos / 64 workers ≈ ~5-7 segundos total.
        with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
            port_results = list(ex.map(open_ports, candidates))

        # Map IP → puertos abiertos, filtrando IPs sin nada abierto
        port_map: dict[str, list[int]] = {
            ip: ports for ip, ports in zip(candidates, port_results) if ports
        }
        logger.info(
            f"[DISCOVERY] Subnet scan: {len(port_map)} IPs responden "
            f"a algún puerto ONVIF/RTSP"
        )
        for ip, ports in port_map.items():
            logger.info(f"[DISCOVERY]   {ip} → puertos abiertos: {ports}")

        devices: list[Dict] = []
        for ip, ports in port_map.items():
            if deadline is not None and _t.time() > deadline:
                logger.warning(
                    f"[DISCOVERY] Subnet scan deadline alcanzado; abortando "
                    f"({len(devices)} encontrada(s) de {len(port_map)} probadas)"
                )
                break
            if ip in seen_ips:
                continue
            seen_ips.add(ip)

            has_onvif_port = any(p in ONVIF_PORTS for p in ports)
            has_rtsp_port = RTSP_PORT in ports

            # Si SOLO está abierto el 554 (sin ningún puerto ONVIF típico), no
            # gastar 30+s probando SOAP que va a fallar. Vamos directo a RTSP.
            if not has_onvif_port and has_rtsp_port:
                logger.info(
                    f"[DISCOVERY] {ip}: solo RTSP (sin ONVIF). "
                    f"Saltando a RTSP fallback directo."
                )
                d = self._try_rtsp_fallback(ip, quick=True)
                if d:
                    devices.append(d)
                continue

            logger.info(f"[DISCOVERY] Probando ONVIF en {ip} (modo quick)...")
            d = self._try_onvif_robust(ip, quick=True)
            if d:
                devices.append(d)
                continue
            # Si tiene RTSP abierto pero ONVIF no respondió, igual probar
            # RTSP fallback — la cámara puede ser solo-RTSP en otro puerto.
            if has_rtsp_port:
                d = self._try_rtsp_fallback(ip, quick=True)
                if d:
                    devices.append(d)
        return devices

    def probe_single_ip(self, ip: str, username: str = None, password: str = None) -> Optional[Dict]:
        """
        Prueba una IP específica. Si se proporcionan username/password, los usa primero.
        """
        # Si hay credenciales específicas, probarlas primero
        if username and password:
            device = self._try_onvif_robust_with_creds(ip, username, password)
            if device:
                return device
        # Si no, probar con credenciales comunes
        device = self._try_onvif_robust(ip)
        if device:
            return device
        return self._try_rtsp_fallback(ip)

    def _try_onvif_robust_with_creds(self, ip: str, user: str, pwd: str,
                                     allow_slow_path: bool = False) -> Optional[Dict]:
        """
        Prueba ONVIF con credenciales específicas (sin iterar sobre COMMON_CREDENTIALS).

        Args:
            ip: dirección IPv4 de la cámara.
            user, pwd: credenciales explícitas.
            allow_slow_path: si False (default), no cae al cliente onvif-zeep
                (que puede tardar minutos por WSDL + 4 puertos). El FAST PATH
                cubre el 95% de cámaras IP comerciales y baratas; si falla, casi
                seguro la cámara no es ONVIF accesible y el SLOW PATH solo
                hace perder tiempo del request HTTP.
        """
        if not ip or ':' in ip:
            return None

        # FAST PATH SOAP directo (sin WSDL)
        from .onvif_soap import probe_ip_all_ports, probe_to_camera_dict
        logger.info(f"[DISCOVERY/{ip}] FAST PATH con creds user={user!r}")
        result = probe_ip_all_ports(ip, user, pwd, timeout=4)
        if result and result.auth_ok and result.profiles and result.stream_uri:
            cam = probe_to_camera_dict(result, user, pwd)
            if cam:
                logger.info(f"[DISCOVERY/{ip}] ✓ SOAP OK :{result.port}")
                return cam

        if not allow_slow_path:
            logger.info(
                f"[DISCOVERY/{ip}] FAST PATH falló con creds explícitas; "
                f"SLOW PATH deshabilitado (allow_slow_path=False)"
            )
            return None

        # SLOW PATH: onvif-zeep. Solo si el caller lo pidió explícitamente.
        logger.info(f"[DISCOVERY/{ip}] SLOW PATH onvif-zeep con creds user={user!r}")
        for port in ONVIF_PORTS:
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

    def _try_onvif_robust(self, ip: str, quick: bool = False) -> Optional[Dict]:
        """
        Args:
            quick: si True, prueba solo las ~6 credenciales más comunes y
                deshabilita el SLOW PATH onvif-zeep. Usado en subnet_scan
                para no gastar minutos por IP no-cámara. Si la cámara real
                tiene credenciales no estándar, el usuario la agregará desde
                el diálogo con sus credenciales explícitas.
        """
        if not ip or ':' in ip:
            return None

        # Top credenciales (cubren el 90% de cámaras con defaults). En modo
        # quick se usa este subset; en modo completo se usan todas.
        creds_to_try = COMMON_CREDENTIALS[:6] if quick else COMMON_CREDENTIALS

        # ---- FAST PATH: cliente SOAP directo (sin WSDL) -------------------
        # Antes de cargar onvif-zeep (lento y propenso a fallar con WSDL),
        # probamos un probe SOAP minimalista. Esto cubre el 95% de las cámaras
        # incluidas las baratas (XiongMai, etc.) que aceptan cualquier request
        # en /onvif/device_service.
        from .onvif_soap import probe_ip_all_ports, probe_to_camera_dict
        logger.info(
            f"[DISCOVERY/{ip}] FAST PATH: probando SOAP directo con "
            f"{len(creds_to_try)} credenciales{' (quick)' if quick else ''}"
        )
        for user, pwd in creds_to_try:
            # timeout corto (2s) — si la cámara no responde tan rápido,
            # probablemente no es ONVIF; mejor pasar a la siguiente credencial.
            result = probe_ip_all_ports(ip, user, pwd, timeout=2)
            if result is None:
                continue
            if result.auth_ok and result.profiles and result.stream_uri:
                logger.info(
                    f"[DISCOVERY/{ip}] ✓ SOAP OK :{result.port} user={user!r} "
                    f"perfiles={len(result.profiles)} auth={result.auth_method}"
                )
                cam = probe_to_camera_dict(result, user, pwd)
                if cam:
                    return cam
            # Si fue reachable pero falló por auth, no probamos más puertos
            # con esta credencial; saltamos a la siguiente credencial.
            if result.reachable and not result.auth_ok:
                logger.info(
                    f"[DISCOVERY/{ip}] :{result.port} respondió pero user={user!r} "
                    f"falló: {[str(e) for e in result.errors][:2]}"
                )

        if quick:
            logger.info(
                f"[DISCOVERY/{ip}] FAST PATH agotado (quick); SLOW PATH "
                f"deshabilitado para no bloquear el discover"
            )
            return None

        # ---- SLOW PATH: caer al cliente onvif-zeep (con WSDL) -------------
        logger.info(f"[DISCOVERY/{ip}] SLOW PATH: cayendo a onvif-zeep")
        for port in ONVIF_PORTS:
            for user, pwd in creds_to_try:
                logger.debug(f"[DISCOVERY/{ip}] :{port} user={user!r}")
                client = RobustONVIFClient(ip, port, user, pwd)
                if not client.connect():
                    continue
                logger.info(f"[DISCOVERY/{ip}] ✓ conectado :{port} con user={user!r}")
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

    def _try_rtsp_fallback(self, ip: str, quick: bool = False) -> Optional[Dict]:
        if not ip or ':' in ip:
            return None
        logger.info(f"⚠ Intentando RTSP fallback: {ip}{' (quick)' if quick else ''}")
        manufacturer = self._guess_manufacturer(ip)
        patterns = RTSP_PATTERNS.get(manufacturer, RTSP_PATTERNS['generic'])
        # En quick: solo top 6 creds y solo los 2 primeros patrones.
        creds_to_try = COMMON_CREDENTIALS[:6] if quick else COMMON_CREDENTIALS
        patterns_to_try = patterns[:2] if quick else patterns
        for user, pwd in creds_to_try:
            for pattern in patterns_to_try:
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
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            if sock.connect_ex((ip, 34567)) == 0:
                return 'xiongmai'
        except:
            pass
        return 'generic'

    def _test_rtsp_url(self, url: str) -> bool:
        """
        Verifica que el puerto RTSP de la URL responda a TCP.

        BUG FIX: la regex anterior `r'rtsp://([^/]+)'` capturaba también la
        parte `user:pass@` cuando la URL incluía credenciales (típico). Eso
        rompía el `host_port.split(':')` (3 partes en vez de 2) y disparaba
        ValueError → siempre devolvía False → el RTSP fallback nunca pasaba
        contra cámaras solo-RTSP. Nueva regex ignora explícitamente el segmento
        de credenciales.
        """
        try:
            # `(?:[^@/]+@)?` consume opcionalmente "user:pass@", `([^/]+)`
            # captura "host[:port]".
            match = re.search(r'rtsp://(?:[^@/]+@)?([^/]+)', url)
            if not match:
                return False
            host_port = match.group(1)
            if ':' in host_port:
                host, port_s = host_port.rsplit(':', 1)
                try:
                    port = int(port_s)
                except ValueError:
                    return False
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
        try:
            return urlparse(xaddr).hostname
        except:
            return None