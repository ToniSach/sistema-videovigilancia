"""
================================================================================
MÓDULO: webrtc_signaling — Proxy de signaling WebRTC (WHEP) hacia go2rtc
================================================================================

PROPÓSITO
    Reenviar SOLO el "saludo" inicial de WebRTC (intercambio SDP offer/answer)
    entre el cliente y go2rtc, tras validar la autorización en el backend. El
    vídeo NUNCA pasa por aquí: viaja peer-a-peer entre cliente y go2rtc.

RESPONSABILIDAD PRINCIPAL
    Ser el punto de control de acceso del directo WebRTC: el endpoint autentica
    (JWT) y comprueba permiso ANTES de delegar en este servicio, que se limita a
    hacer de proxy del SDP. Así el backend decide QUIÉN puede abrir el stream
    sin pagar coste de CPU por el medio (que va directo P2P).

PIPELINES EN LOS QUE PARTICIPA
    #6  WebRTC ... ESTE módulo ejecuta el signaling del pipeline WebRTC.
    #3  Live ..... es el camino del directo de baja latencia (desktop/móvil).
    #5  go2rtc ... destino del proxy (usa Go2RtcManager.webrtc_api_base()).

DEPENDENCIAS
    go2rtc_manager .... webrtc_api_base() (base API) + stream_name() (nombre).
    urllib (stdlib) ... POST del SDP a go2rtc (sin dependencia de `requests`).

COMPONENTES RELACIONADOS
    Endpoint /cameras/<id>/webrtc — autentica/autoriza y llama a exchange().
    Go2RtcManager — provee la URL de la API WebRTC (puerto 1984).

PUNTO DE ENTRADA
    Singleton `webrtc_signaling`. Método público: exchange(camera_id, offer).

QUÉ HACE
--------
WebRTC necesita un "saludo" inicial (signaling) donde cliente y servidor
intercambian descripciones de sesión (SDP). El medio (vídeo) viaja luego
peer-a-peer, NO por el backend. Este servicio es solo ese saludo:

  cliente --(SDP offer)--> BACKEND (valida JWT + permiso) --(offer)--> go2rtc
  cliente <--(SDP answer)-- BACKEND <----------------(answer)-------- go2rtc

Así el backend nunca toca el vídeo (CPU ≈ 0) pero SÍ controla quién puede
abrir el stream (autorización antes de reenviar el offer a go2rtc).

PIPELINE
--------
  Paso 1. El endpoint /cameras/<id>/webrtc recibe el SDP offer (ya autenticado).
  Paso 2. `exchange(camera_id, offer)` construye la URL WebRTC de go2rtc.
  Paso 3. Reenvía el offer a go2rtc y espera el SDP answer.
  Paso 4. Devuelve el answer al cliente, que establece la conexión P2P.

RESILIENCIA
-----------
- Timeout corto: si go2rtc no responde (caído), se devuelve error claro, no
  cuelga la petición.
- Sin dependencias externas: usa urllib (stdlib), no requiere `requests`.
"""
from __future__ import annotations

import logging
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)


class WebRTCSignalingError(Exception):
    """Error de signaling (go2rtc inaccesible, respuesta inválida, etc.)."""


class WebRTCSignalingService:
    """Servicio de signaling WebRTC. Singleton ligero; sin estado propio.

    ROL: hace de intermediario del SDP entre cliente y go2rtc (pipeline #6).
    QUIÉN LO USA: el endpoint /cameras/<id>/webrtc (ya autenticado/autorizado)
    llama a exchange(); este servicio NO valida permisos (eso es del endpoint).
    SINGLETON: sin estado mutable relevante (solo cachea su instancia); el
    patrón existe por consistencia con el resto del sistema, no por estado vivo.
    Se expone como `webrtc_signaling` al final del módulo.
    """

    _instance: Optional["WebRTCSignalingService"] = None

    def __new__(cls) -> "WebRTCSignalingService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def webrtc_endpoint(self, camera_id: int) -> str:
        """
        Paso 2 (puro, testeable). Construye la URL de la API WebRTC de go2rtc
        para el stream de esta cámara: `<base>/api/webrtc?src=cam_<id>`.
        """
        from .go2rtc_manager import Go2RtcManager, stream_name

        base = Go2RtcManager().webrtc_api_base()
        return f"{base}/api/webrtc?src={stream_name(camera_id)}"

    def exchange(self, camera_id: int, offer_sdp: str, timeout: float = 10.0) -> str:
        """
        Paso 3. Reenvía el SDP offer a go2rtc y devuelve el SDP answer.

        Etapa central del signaling (#6): POST del offer a la API WebRTC de
        go2rtc (puerto 1984) y espera de la answer. Tras esto, el vídeo va P2P
        entre cliente y go2rtc (8555) — este método NO vuelve a intervenir.

        Inputs:
          camera_id ... id de la cámara (resuelve el stream cam_<id>).
          offer_sdp ... SDP offer del cliente (texto). Se rechaza si está vacío.
          timeout ..... segundos máx. a esperar a go2rtc (corto: no cuelga la
                        petición si go2rtc está caído).
        Outputs:
          str con el SDP answer de go2rtc (validado: debe contener "v=0").
        Excepciones:
          WebRTCSignalingError — offer vacío, go2rtc inaccesible o answer
          inválida; el endpoint la traduce a un 502/503 claro.
        Llamado por: endpoint /cameras/<id>/webrtc.
        Llama a:     webrtc_endpoint (construye la URL destino en go2rtc).
        """
        if not offer_sdp or not offer_sdp.strip():
            raise WebRTCSignalingError("SDP offer vacío")

        url = self.webrtc_endpoint(camera_id)
        req = urllib.request.Request(
            url,
            data=offer_sdp.encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/sdp"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                answer = resp.read().decode("utf-8")
        except Exception as e:  # urllib lanza URLError/HTTPError/socket.timeout
            logger.error("Fallo de signaling WebRTC con go2rtc (%s): %s", url, e)
            raise WebRTCSignalingError(f"go2rtc no respondió: {e}") from e

        if not answer or "v=0" not in answer:
            raise WebRTCSignalingError("Respuesta SDP inválida de go2rtc")
        return answer


# Singleton de conveniencia
webrtc_signaling = WebRTCSignalingService()
