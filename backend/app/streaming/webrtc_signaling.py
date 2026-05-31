"""
WebRTCSignalingService — Proxy de signaling WebRTC (WHEP) hacia go2rtc.

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
    """Servicio de signaling. Singleton ligero; sin estado propio relevante."""

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

        Lanza WebRTCSignalingError si go2rtc no está accesible o responde mal,
        para que el endpoint lo traduzca a un 502/503 claro.
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
