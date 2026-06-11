"""
Cliente ONVIF SOAP directo (sin onvif-zeep).

Implementa el estándar ONVIF Core: SOAP 1.2 sobre HTTP, con autenticación
WS-Security (UsernameToken). No depende de WSDL local — construye los
envelopes manualmente, lo que lo hace:

- Más portable (cero dependencias además de `requests`).
- Mucho más rápido (no carga el árbol WSDL completo en memoria).
- Más fácil de depurar (errores HTTP / SOAP Fault claros).
- Más tolerante con cámaras baratas que no implementan el estándar al pie de
  la letra (XiongMai, etc. — que aceptan cualquier servicio en el endpoint
  /onvif/device_service).

Soporta autenticación con `PasswordText` (lo que esperan la mayoría de
cámaras chinas) y `PasswordDigest` (estándar ONVIF estricto).

Endpoints típicos:
    /onvif/device_service          → device management
    /onvif/media_service            → media (perfiles, stream URI)
    /onvif/Media                    → idem (variante)
    /onvif/ptz_service              → PTZ
    /onvif/PTZ                      → idem
    /onvif/imaging_service          → imaging (IR-Cut, brillo, etc.)

Puertos comunes: 80, 8000, 8080, 8899.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


# Puertos ONVIF típicos por orden de probabilidad
ONVIF_PORTS = [80, 8000, 8080, 8899]

# Endpoints ONVIF estándar (probamos varios por compatibilidad)
DEVICE_PATHS = ["/onvif/device_service"]
MEDIA_PATHS = ["/onvif/media_service", "/onvif/Media", "/onvif/device_service"]
PTZ_PATHS = ["/onvif/ptz_service", "/onvif/PTZ", "/onvif/device_service"]
IMAGING_PATHS = ["/onvif/imaging_service", "/onvif/Imaging", "/onvif/device_service"]

# Namespaces ONVIF
NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "wsse": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
    "wsu": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd",
    "tt": "http://www.onvif.org/ver10/schema",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
    "timg": "http://www.onvif.org/ver20/imaging/wsdl",
}

PASSWORD_TYPE_DIGEST = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
)

# =============================================================================
# Errores
# =============================================================================
@dataclass
class ONVIFError:
    """Error legible de una llamada ONVIF."""
    kind: str          # "network" | "http" | "soap_fault" | "parse" | "auth"
    message: str
    http_status: Optional[int] = None
    fault_code: Optional[str] = None
    fault_reason: Optional[str] = None
    raw_response: Optional[str] = None  # truncado

    def __str__(self) -> str:
        parts = [f"[{self.kind}]"]
        if self.http_status:
            parts.append(f"HTTP {self.http_status}")
        if self.fault_code:
            parts.append(f"SOAP {self.fault_code}")
        parts.append(self.message)
        return " ".join(parts)


# =============================================================================
# Resultados parseados
# =============================================================================
@dataclass
class DeviceInformation:
    manufacturer: str = ""
    model: str = ""
    firmware_version: str = ""
    serial_number: str = ""
    hardware_id: str = ""


@dataclass
class Profile:
    token: str
    name: str = ""
    encoding: str = ""
    width: int = 0
    height: int = 0
    fps: int = 0
    has_ptz: bool = False
    has_audio: bool = False


@dataclass
class Capabilities:
    has_ptz: bool = False
    has_imaging: bool = False
    has_media: bool = False
    has_audio: bool = False
    device_xaddr: str = ""
    media_xaddr: str = ""
    ptz_xaddr: str = ""
    imaging_xaddr: str = ""


@dataclass
class ProbeResult:
    """Resumen de un probe ONVIF contra una IP+puerto."""
    ip: str
    port: int
    endpoint_path: str = ""
    reachable: bool = False
    onvif_ok: bool = False
    auth_ok: bool = False
    auth_method: str = ""  # "PasswordText" | "PasswordDigest" | ""
    device_info: Optional[DeviceInformation] = None
    capabilities: Optional[Capabilities] = None
    profiles: list[Profile] = field(default_factory=list)
    stream_uri: str = ""
    errors: list[ONVIFError] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)  # bitácora detallada

    def short_summary(self) -> str:
        if not self.reachable:
            return f"{self.ip}:{self.port} no responde"
        if not self.onvif_ok:
            return f"{self.ip}:{self.port} responde pero no es ONVIF"
        if not self.auth_ok:
            return f"{self.ip}:{self.port} ONVIF OK pero credenciales inválidas"
        man = self.device_info.manufacturer if self.device_info else ""
        mod = self.device_info.model if self.device_info else ""
        return f"{self.ip}:{self.port} ONVIF OK — {man} {mod} ({len(self.profiles)} perfiles)"


# =============================================================================
# WS-Security UsernameToken
# =============================================================================
def _password_digest_header(user: str, password: str) -> str:
    """
    Genera un header WS-Security con PasswordDigest, conforme al estándar.
    Algunos modelos lo exigen; otros aceptan PasswordText.
    """
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    sha = hashlib.sha1()
    sha.update(nonce + created.encode("utf-8") + password.encode("utf-8"))
    digest = base64.b64encode(sha.digest()).decode("ascii")
    nonce_b64 = base64.b64encode(nonce).decode("ascii")
    return f"""<wsse:Security s:mustUnderstand="1">
  <wsse:UsernameToken>
    <wsse:Username>{_xml_escape(user)}</wsse:Username>
    <wsse:Password Type="{PASSWORD_TYPE_DIGEST}">{digest}</wsse:Password>
    <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</wsse:Nonce>
    <wsu:Created>{created}</wsu:Created>
  </wsse:UsernameToken>
</wsse:Security>"""


def _password_text_header(user: str, password: str) -> str:
    """Header WS-Security con PasswordText (texto plano)."""
    return f"""<wsse:Security>
  <wsse:UsernameToken>
    <wsse:Username>{_xml_escape(user)}</wsse:Username>
    <wsse:Password>{_xml_escape(password)}</wsse:Password>
  </wsse:UsernameToken>
</wsse:Security>"""


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\"", "&quot;").replace("'", "&apos;"))


# =============================================================================
# Cliente
# =============================================================================
class ONVIFSoapClient:
    """
    Cliente SOAP ONVIF mínimo — el FAST PATH del descubrimiento. (Pipeline #7.)

    ROL
        Habla ONVIF construyendo los envelopes SOAP a mano (sin WSDL), lo que lo
        hace rápido y tolerante con cámaras baratas que no cumplen el estándar.
        Cada operación prueba PasswordText primero (compat con cámaras chinas) y
        cae a PasswordDigest (ONVIF estricto) solo si el fallo fue de auth.

    QUIÉN LO CONSUME
        onvif_discovery.py (FAST PATH) vía los helpers probe_ip_all_ports() y
        probe_to_camera_dict(). Es el camino preferido antes de caer a
        onvif-zeep (SLOW PATH).

    SERVICIOS ONVIF QUE TOCA
        device_service (GetDeviceInformation / GetCapabilities), media
        (GetProfiles / GetStreamUri). El endpoint media se autodescubre vía los
        XAddr de GetCapabilities y, si no, se prueban rutas conocidas.

    Uso típico:
        client = ONVIFSoapClient("192.168.1.8", 8899, "admin", "admin")
        result = client.probe()
        if result.auth_ok:
            print(result.device_info)
            print(result.profiles)
    """

    def __init__(self, ip: str, port: int, username: str, password: str,
                 timeout: int = 5):
        self.ip = ip
        self.port = port
        self.username = username or ""
        self.password = password or ""
        self.timeout = timeout
        self._device_url = f"http://{ip}:{port}/onvif/device_service"
        self._media_url: Optional[str] = None
        self._ptz_url: Optional[str] = None
        self._imaging_url: Optional[str] = None
        # Bitácora para depuración
        self._log: list[str] = []

    # ------------------------------------------------------------------
    # Construcción de envelopes
    # ------------------------------------------------------------------
    def _envelope(self, body_xml: str, use_digest: bool) -> bytes:
        security = (
            _password_digest_header(self.username, self.password)
            if use_digest
            else _password_text_header(self.username, self.password)
        )
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<s:Envelope '
            'xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
            'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" '
            'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
            f'<s:Header>{security}</s:Header>'
            f'<s:Body>{body_xml}</s:Body>'
            '</s:Envelope>'
        )
        return envelope.encode("utf-8")

    def _do_request(self, url: str, body_xml: str, action: str,
                    use_digest: bool) -> tuple[Optional[ET.Element], Optional[ONVIFError]]:
        """
        Lanza un request SOAP y devuelve (xml_root, error).
        """
        envelope = self._envelope(body_xml, use_digest=use_digest)
        headers = {
            "Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"',
            "User-Agent": "NVR-ONVIF-Probe/1.0",
        }
        self._log.append(f"POST {url}  action={action}  auth={'digest' if use_digest else 'text'}")
        try:
            r = requests.post(url, data=envelope, headers=headers, timeout=self.timeout)
        except requests.exceptions.ConnectTimeout:
            return None, ONVIFError("network", f"Timeout conectando a {url}")
        except requests.exceptions.ReadTimeout:
            return None, ONVIFError("network", f"Timeout leyendo de {url}")
        except requests.exceptions.ConnectionError as e:
            return None, ONVIFError("network", f"No se pudo conectar: {e}")
        except Exception as e:
            return None, ONVIFError("network", f"Error de red: {type(e).__name__}: {e}")

        text = r.text
        # Parsear XML siempre que sea posible (SOAP Fault también es XML 200/500)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            return None, ONVIFError(
                "parse",
                f"Respuesta no es XML válido: {e}",
                http_status=r.status_code,
                raw_response=text[:500],
            )

        # Detectar SOAP Fault (puede venir en 200 o 4xx/5xx)
        fault = root.find(".//{http://www.w3.org/2003/05/soap-envelope}Fault")
        if fault is not None:
            code_el = fault.find(".//{http://www.w3.org/2003/05/soap-envelope}Subcode/"
                                  "{http://www.w3.org/2003/05/soap-envelope}Value")
            if code_el is None:
                code_el = fault.find(".//{http://www.w3.org/2003/05/soap-envelope}Code/"
                                      "{http://www.w3.org/2003/05/soap-envelope}Value")
            reason_el = fault.find(".//{http://www.w3.org/2003/05/soap-envelope}Reason/"
                                    "{http://www.w3.org/2003/05/soap-envelope}Text")
            code = code_el.text if code_el is not None else ""
            reason = reason_el.text if reason_el is not None else ""
            kind = "auth" if any(k in (code + reason).lower()
                                  for k in ("notauthorized", "authentication", "unauthorized")) else "soap_fault"
            return None, ONVIFError(
                kind, f"SOAP Fault: {reason or code}",
                http_status=r.status_code,
                fault_code=code,
                fault_reason=reason,
                raw_response=text[:500],
            )

        if r.status_code != 200:
            return None, ONVIFError(
                "http", f"HTTP {r.status_code}",
                http_status=r.status_code,
                raw_response=text[:500],
            )

        return root, None

    def _try_both_auth(self, url: str, body: str, action: str
                       ) -> tuple[Optional[ET.Element], Optional[ONVIFError], str]:
        """Prueba PasswordText primero (compat) y PasswordDigest después."""
        root, err = self._do_request(url, body, action, use_digest=False)
        if root is not None:
            return root, None, "PasswordText"
        # Solo cae a digest si fue un error de auth
        if err and err.kind == "auth":
            root2, err2 = self._do_request(url, body, action, use_digest=True)
            if root2 is not None:
                return root2, None, "PasswordDigest"
            return None, err2 or err, ""
        return None, err, ""

    # ------------------------------------------------------------------
    # Operaciones ONVIF
    # ------------------------------------------------------------------
    def get_device_information(self) -> tuple[Optional[DeviceInformation],
                                                Optional[ONVIFError], str]:
        """
        Lee fabricante/modelo/firmware/serie de la cámara. (Pipeline #7 ONVIF.)

        Es la PRUEBA DE VIDA ONVIF + validación de credenciales: si responde
        OK, la cámara es ONVIF y las credenciales son válidas.

        Solicitud SOAP: GetDeviceInformation (sin parámetros).
        Respuesta esperada: GetDeviceInformationResponse con Manufacturer,
            Model, FirmwareVersion, SerialNumber, HardwareId.
        Servicio ONVIF: device_service.
        Outputs: (DeviceInformation, error, auth_method) — auth_method indica si
            funcionó con "PasswordText" o "PasswordDigest".
        Llamado por: probe().
        """
        body = '<tds:GetDeviceInformation xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/>'
        action = "http://www.onvif.org/ver10/device/wsdl/GetDeviceInformation"
        root, err, auth = self._try_both_auth(self._device_url, body, action)
        if root is None:
            return None, err, auth
        try:
            resp = root.find(".//{http://www.onvif.org/ver10/device/wsdl}GetDeviceInformationResponse")
            if resp is None:
                return None, ONVIFError("parse", "GetDeviceInformationResponse no encontrado"), auth
            info = DeviceInformation()
            for child in resp:
                tag = child.tag.split("}")[-1]
                val = (child.text or "").strip()
                if tag == "Manufacturer":
                    info.manufacturer = val
                elif tag == "Model":
                    info.model = val
                elif tag == "FirmwareVersion":
                    info.firmware_version = val
                elif tag == "SerialNumber":
                    info.serial_number = val
                elif tag == "HardwareId":
                    info.hardware_id = val
            return info, None, auth
        except Exception as e:
            return None, ONVIFError("parse", f"Error parseando device info: {e}"), auth

    def get_capabilities(self) -> tuple[Optional[Capabilities], Optional[ONVIFError]]:
        """
        Descubre qué servicios soporta la cámara y sus URLs. (Pipeline #7.)

        Solicitud SOAP: GetCapabilities con Category=All.
        Respuesta esperada: Capabilities con secciones Media/PTZ/Imaging/Device,
            cada una con su XAddr (URL del servicio). Esas URLs se cachean
            (self._media_url/_ptz_url/_imaging_url) para las siguientes llamadas.
        Servicio ONVIF: device_service.
        Tolerancia: en cámaras baratas puede fallar sin ser fatal — probe() lo
            trata como no crítico y sigue con GetProfiles.
        Outputs: (Capabilities, error).
        """
        body = (
            '<tds:GetCapabilities xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
            '<tds:Category>All</tds:Category>'
            '</tds:GetCapabilities>'
        )
        action = "http://www.onvif.org/ver10/device/wsdl/GetCapabilities"
        root, err, _ = self._try_both_auth(self._device_url, body, action)
        if root is None:
            return None, err
        try:
            caps = Capabilities()
            # Media
            media = root.find(".//{http://www.onvif.org/ver10/schema}Media")
            if media is not None:
                caps.has_media = True
                xaddr = media.find(".//{http://www.onvif.org/ver10/schema}XAddr")
                if xaddr is not None and xaddr.text:
                    caps.media_xaddr = xaddr.text
                    self._media_url = xaddr.text
            # PTZ
            ptz = root.find(".//{http://www.onvif.org/ver10/schema}PTZ")
            if ptz is not None:
                caps.has_ptz = True
                xaddr = ptz.find(".//{http://www.onvif.org/ver10/schema}XAddr")
                if xaddr is not None and xaddr.text:
                    caps.ptz_xaddr = xaddr.text
                    self._ptz_url = xaddr.text
            # Imaging
            imaging = root.find(".//{http://www.onvif.org/ver10/schema}Imaging")
            if imaging is not None:
                caps.has_imaging = True
                xaddr = imaging.find(".//{http://www.onvif.org/ver10/schema}XAddr")
                if xaddr is not None and xaddr.text:
                    caps.imaging_xaddr = xaddr.text
                    self._imaging_url = xaddr.text
            # Device XAddr
            device = root.find(".//{http://www.onvif.org/ver10/schema}Device")
            if device is not None:
                xaddr = device.find(".//{http://www.onvif.org/ver10/schema}XAddr")
                if xaddr is not None and xaddr.text:
                    caps.device_xaddr = xaddr.text
            return caps, None
        except Exception as e:
            return None, ONVIFError("parse", f"Error parseando capabilities: {e}")

    def get_profiles(self) -> tuple[list[Profile], Optional[ONVIFError]]:
        """
        Obtiene los perfiles de medios (resolución/codec/PTZ/audio). (Pipeline #7.)

        Solicitud SOAP: GetProfiles (sin parámetros).
        Respuesta esperada: lista de <Profiles> con VideoEncoderConfiguration y,
            si los hay, PTZConfiguration / AudioEncoderConfiguration.
        Servicio ONVIF: media.
        Compatibilidad: prueba el XAddr media de GetCapabilities y, si no, las
            rutas MEDIA_PATHS conocidas; también acepta el tag <Profiles> tanto
            en el namespace media/wsdl como en el schema (variantes de cámaras).
        Outputs: (lista de Profile, error).
        """
        body = '<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>'
        action = "http://www.onvif.org/ver10/media/wsdl/GetProfiles"
        # Probar primero el endpoint Media declarado por GetCapabilities, luego variantes
        endpoints = []
        if self._media_url:
            endpoints.append(self._media_url)
        for path in MEDIA_PATHS:
            url = f"http://{self.ip}:{self.port}{path}"
            if url not in endpoints:
                endpoints.append(url)

        last_err = None
        for url in endpoints:
            self._log.append(f"GetProfiles → probando endpoint {url}")
            root, err, _ = self._try_both_auth(url, body, action)
            if root is None:
                last_err = err
                continue
            try:
                profiles = []
                for p in root.iter("{http://www.onvif.org/ver10/media/wsdl}Profiles"):
                    profiles.append(self._parse_profile(p))
                # Algunos servidores devuelven Profile (singular) en vez de Profiles
                if not profiles:
                    for p in root.iter("{http://www.onvif.org/ver10/schema}Profiles"):
                        profiles.append(self._parse_profile(p))
                if profiles:
                    return profiles, None
                last_err = ONVIFError("parse", "Sin perfiles en la respuesta")
            except Exception as e:
                last_err = ONVIFError("parse", f"Error parseando perfiles: {e}")
        return [], last_err

    def _parse_profile(self, p_elem: ET.Element) -> Profile:
        token = p_elem.attrib.get("token", "")
        name_el = p_elem.find("{http://www.onvif.org/ver10/schema}Name")
        name = name_el.text if name_el is not None else ""
        # Video encoder
        vec = p_elem.find("{http://www.onvif.org/ver10/schema}VideoEncoderConfiguration")
        encoding = ""
        width = height = fps = 0
        if vec is not None:
            enc_el = vec.find("{http://www.onvif.org/ver10/schema}Encoding")
            if enc_el is not None and enc_el.text:
                encoding = enc_el.text.upper()
            res_el = vec.find("{http://www.onvif.org/ver10/schema}Resolution")
            if res_el is not None:
                w_el = res_el.find("{http://www.onvif.org/ver10/schema}Width")
                h_el = res_el.find("{http://www.onvif.org/ver10/schema}Height")
                if w_el is not None and w_el.text:
                    width = int(w_el.text)
                if h_el is not None and h_el.text:
                    height = int(h_el.text)
            rc_el = vec.find("{http://www.onvif.org/ver10/schema}RateControl")
            if rc_el is not None:
                fps_el = rc_el.find("{http://www.onvif.org/ver10/schema}FrameRateLimit")
                if fps_el is not None and fps_el.text:
                    fps = int(float(fps_el.text))
        has_ptz = p_elem.find("{http://www.onvif.org/ver10/schema}PTZConfiguration") is not None
        has_audio = p_elem.find("{http://www.onvif.org/ver10/schema}AudioEncoderConfiguration") is not None
        return Profile(
            token=token, name=name or token, encoding=encoding,
            width=width, height=height, fps=fps,
            has_ptz=has_ptz, has_audio=has_audio,
        )

    def get_stream_uri(self, profile_token: str) -> tuple[str, Optional[ONVIFError]]:
        """
        Resuelve la URL RTSP de un perfil. (Provee la URL al pipeline #3 Live.)

        Solicitud SOAP: GetStreamUri con StreamSetup={Stream: RTP-Unicast,
            Transport.Protocol: RTSP} y el ProfileToken indicado.
        Respuesta esperada: <Uri> con la URL rtsp://...
        Servicio ONVIF: media.
        Outputs: (uri, error). En probe() se inyectan las credenciales en la URL
            si la cámara no las incluye.
        """
        body = (
            '<trt:GetStreamUri xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
            'xmlns:tt="http://www.onvif.org/ver10/schema">'
            '<trt:StreamSetup>'
            '<tt:Stream>RTP-Unicast</tt:Stream>'
            '<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>'
            '</trt:StreamSetup>'
            f'<trt:ProfileToken>{_xml_escape(profile_token)}</trt:ProfileToken>'
            '</trt:GetStreamUri>'
        )
        action = "http://www.onvif.org/ver10/media/wsdl/GetStreamUri"
        endpoints = []
        if self._media_url:
            endpoints.append(self._media_url)
        for path in MEDIA_PATHS:
            url = f"http://{self.ip}:{self.port}{path}"
            if url not in endpoints:
                endpoints.append(url)

        last_err = None
        for url in endpoints:
            self._log.append(f"GetStreamUri[{profile_token}] → {url}")
            root, err, _ = self._try_both_auth(url, body, action)
            if root is None:
                last_err = err
                continue
            try:
                uri_el = root.find(".//{http://www.onvif.org/ver10/schema}Uri")
                if uri_el is not None and uri_el.text:
                    return uri_el.text.strip(), None
            except Exception as e:
                last_err = ONVIFError("parse", f"Error parseando StreamUri: {e}")
        return "", last_err or ONVIFError("parse", "Sin URI en respuesta")

    # ------------------------------------------------------------------
    # Probe completo
    # ------------------------------------------------------------------
    def probe(self) -> ProbeResult:
        """
        Probe completo de la cámara: TCP reachable → GetDeviceInformation →
        GetCapabilities → GetProfiles → GetStreamUri.

        Devuelve un ProbeResult con TODA la información, incluyendo errores
        intermedios. Útil para diagnóstico.
        """
        result = ProbeResult(ip=self.ip, port=self.port,
                             endpoint_path="/onvif/device_service")

        # 1) ¿TCP abierto?
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            res = sock.connect_ex((self.ip, self.port))
            sock.close()
            if res != 0:
                result.errors.append(ONVIFError("network", f"TCP cerrado ({res})"))
                result.log_lines = list(self._log)
                return result
            result.reachable = True
        except Exception as e:
            result.errors.append(ONVIFError("network", f"Error TCP: {e}"))
            return result

        # 2) GetDeviceInformation (prueba de vida ONVIF + credenciales)
        info, err, auth = self.get_device_information()
        if info is None:
            # Si el error es de red → no es ONVIF
            if err and err.kind == "network":
                result.errors.append(err)
                result.log_lines = list(self._log)
                return result
            # Si es 404/HTTP error genérico, tampoco es ONVIF
            if err and err.kind == "http" and err.http_status in (404, 405, 501):
                result.errors.append(err)
                result.log_lines = list(self._log)
                return result
            # Si el error es de parseo (respuesta no-SOAP, p. ej. HTML del web admin
            # en puerto 80), NO es ONVIF — no marcar onvif_ok.
            if err and err.kind == "parse":
                result.errors.append(err)
                result.log_lines = list(self._log)
                return result
            # Si llegó hasta aquí, hubo respuesta ONVIF pero falló (probablemente auth)
            result.onvif_ok = True
            result.errors.append(err or ONVIFError("auth", "GetDeviceInformation falló"))
            result.log_lines = list(self._log)
            return result

        result.onvif_ok = True
        result.auth_ok = True
        result.device_info = info
        result.auth_method = auth
        self._log.append(f"DeviceInfo: {info.manufacturer} / {info.model}")

        # 3) GetCapabilities (no es fatal si falla en cámaras baratas)
        caps, err = self.get_capabilities()
        if caps is not None:
            result.capabilities = caps
        else:
            result.errors.append(err or ONVIFError("soap_fault", "GetCapabilities falló"))

        # 4) GetProfiles
        profiles, err = self.get_profiles()
        if profiles:
            result.profiles = profiles
        else:
            result.errors.append(err or ONVIFError("soap_fault", "GetProfiles falló"))

        # 5) GetStreamUri del primer perfil
        if result.profiles:
            uri, err = self.get_stream_uri(result.profiles[0].token)
            if uri:
                # Asegurar credenciales en la URL (algunas cámaras no las incluyen)
                if self.username and "@" not in uri:
                    from urllib.parse import quote
                    u = quote(self.username, safe="")
                    p = quote(self.password, safe="")
                    uri = uri.replace("rtsp://", f"rtsp://{u}:{p}@", 1)
                result.stream_uri = uri
            else:
                result.errors.append(err or ONVIFError("soap_fault", "GetStreamUri falló"))

        result.log_lines = list(self._log)
        return result


# =============================================================================
# Helpers de alto nivel
# =============================================================================
def probe_ip_all_ports(ip: str, username: str, password: str,
                       timeout: int = 5, ports: Optional[list[int]] = None
                       ) -> ProbeResult:
    """
    Prueba una IP en todos los puertos ONVIF típicos y devuelve el primer
    resultado válido (auth_ok=True). Si ninguno funciona, devuelve el último
    intentado para que el caller vea los errores.
    """
    ports = ports or ONVIF_PORTS
    last = None
    for port in ports:
        client = ONVIFSoapClient(ip, port, username, password, timeout=timeout)
        result = client.probe()
        last = result
        if result.auth_ok and result.profiles:
            logger.info(f"[ONVIF-SOAP] {ip}:{port} OK ({result.short_summary()})")
            return result
        # Solo loggear en INFO si la cámara DICE ser ONVIF pero falló auth o capabilities;
        # los demás casos (puerto cerrado, no es ONVIF) son ruido en DEBUG.
        if result.onvif_ok:
            logger.info(
                f"[ONVIF-SOAP] {ip}:{port} respondió ONVIF pero auth/perfiles fallaron: "
                f"{[str(e) for e in result.errors][:2]}"
            )
        else:
            logger.debug(
                f"[ONVIF-SOAP] {ip}:{port} → reachable={result.reachable} "
                f"errs={[str(e) for e in result.errors][:1]}"
            )
    return last  # type: ignore[return-value]


def probe_to_camera_dict(result: ProbeResult, username: str, password: str
                         ) -> Optional[dict]:
    """Convierte un ProbeResult exitoso al formato dict que usa CameraService.add_camera."""
    if not (result.auth_ok and result.profiles and result.stream_uri):
        return None
    # Elegir el perfil con mejor encoding/resolución
    profile = _best_profile(result.profiles)
    has_ptz = profile.has_ptz or (result.capabilities.has_ptz if result.capabilities else False)
    has_audio = profile.has_audio
    return {
        "name": (result.device_info.model if result.device_info else "ONVIF Camera"),
        "ip_address": result.ip,
        "rtsp_url": result.stream_uri,
        "onvif_url": f"http://{result.ip}:{result.port}/onvif/device_service",
        "username": username,
        "password": password,
        "profile_token": profile.token,
        "manufacturer": result.device_info.manufacturer if result.device_info else "Unknown",
        "model": result.device_info.model if result.device_info else "Unknown",
        "has_ptz": has_ptz,
        "has_audio": has_audio,
        "has_leds": result.capabilities.has_imaging if result.capabilities else False,
        "fps": profile.fps or 15,
        "resolution_width": profile.width or 1920,
        "resolution_height": profile.height or 1080,
        "connection_type": "onvif",
    }


def _best_profile(profiles: list[Profile]) -> Profile:
    """Selecciona el perfil con mejor calidad: H264 > H265 > resto."""
    def score(p: Profile) -> int:
        s = 0
        if p.encoding == "H264":
            s += 1000
        elif p.encoding == "H265":
            s += 500
        if p.has_ptz:
            s += 50
        s += min(p.width // 10, 200)
        return s
    return sorted(profiles, key=score, reverse=True)[0]
