"""
SignedUrlService — Firma y verificación de URLs de medios (HMAC-SHA256).

PROBLEMA QUE RESUELVE
---------------------
Los reproductores nativos (ExoPlayer en Android, AVPlayer en iOS, libVLC en el
desktop, o un <video> en el navegador) NO envían el header `Authorization:
Bearer <JWT>` cuando descargan los segmentos `.ts` de un HLS, un `.m3u8` o un
MP4 por rangos. Si protegiéramos esos endpoints solo con JWT, el reproductor
recibiría 401. Si los dejáramos abiertos, cualquiera con la URL vería la
grabación. La solución estándar (la usan Cloudflare, AWS CloudFront, etc.) es
firmar la URL con un token corto y caducable que el reproductor arrastra en el
query string (`?token=...`) sin necesidad de cabeceras.

DISEÑO
------
- Núcleo SIN dependencias del proyecto (solo stdlib): se puede testear con un
  secreto de prueba sin levantar Flask ni la BD.
- El token LIGA el recurso concreto (p.ej. `recording:123`) con una caducidad.
  Un token emitido para una grabación NO sirve para otra (evita IDOR).
- Comparación en tiempo constante (`hmac.compare_digest`) → sin timing attacks.

PIPELINE DE EMISIÓN (servidor, al construir una URL de medios)
--------------------------------------------------------------
  Paso 1. El endpoint autenticado (JWT + permisos) decide qué recurso exponer.
  Paso 2. Llama a `sign(resource, ttl)` → obtiene un token opaco.
  Paso 3. Devuelve al cliente la URL del medio con `?token=<token>`.

PIPELINE DE VALIDACIÓN (servidor, al servir el medio)
-----------------------------------------------------
  Paso 4. El reproductor pide `GET .../media?token=<token>` (sin JWT).
  Paso 5. El endpoint llama a `verify(resource, token)`.
  Paso 6. Si es válido y no ha caducado → sirve el archivo; si no → 403.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Callable, Optional

# Versión del formato de token. Permite rotar el esquema sin romper tokens
# antiguos de forma silenciosa (un token v1 no valida contra un verificador v2).
_TOKEN_VERSION = "v1"


def _b64url(raw: bytes) -> str:
    """Codifica en base64 url-safe SIN padding (apto para query strings)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class SignedUrlService:
    """
    Firma/verifica tokens HMAC ligados a un recurso con caducidad.

    El servicio es puro: recibe el secreto por constructor (inyección de
    dependencias), por lo que es trivialmente testeable. El cableado con
    `settings` ocurre en `get_signed_url_service()` (fábrica de runtime).
    """

    def __init__(
        self,
        secret: str | bytes,
        default_ttl_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """
        Paso 0 (init). Normaliza el secreto a bytes y guarda el TTL y el reloj.

        Args:
            secret: clave HMAC (string o bytes). Nunca debe quedar vacía.
            default_ttl_seconds: caducidad por defecto si `sign()` no recibe ttl.
            clock: función que devuelve epoch en segundos (inyectable en tests
                   para simular el paso del tiempo sin `sleep`).
        """
        if not secret:
            raise ValueError("SignedUrlService requiere un secreto no vacío")
        self._secret: bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
        self._default_ttl = int(default_ttl_seconds)
        self._clock = clock

    # ───────────────────────────────────────────────────────────────────────
    # Núcleo criptográfico
    # ───────────────────────────────────────────────────────────────────────
    def _compute_sig(self, resource: str, exp: int) -> str:
        """
        Paso interno. Calcula HMAC-SHA256 sobre "<version>:<resource>:<exp>".

        Incluir el recurso y la caducidad DENTRO del material firmado impide:
          - reusar el token en otro recurso (cambia `resource` → firma distinta),
          - extender la caducidad manualmente (cambia `exp` → firma distinta).
        """
        msg = f"{_TOKEN_VERSION}:{resource}:{exp}".encode("utf-8")
        digest = hmac.new(self._secret, msg, hashlib.sha256).digest()
        return _b64url(digest)

    # ───────────────────────────────────────────────────────────────────────
    # Emisión (pasos 1-3 del pipeline)
    # ───────────────────────────────────────────────────────────────────────
    def sign(self, resource: str, ttl_seconds: Optional[int] = None) -> str:
        """
        Paso 2 (emisión). Genera un token opaco para `resource`.

        Formato: "<version>.<exp>.<sig>" — todo url-safe, sin caracteres que
        haya que escapar en un query string.

        Args:
            resource: identificador estable del recurso, p.ej. "recording:123"
                      o "hls:42/index.m3u8". DEBE ser el mismo string que se
                      pase luego a `verify()`.
            ttl_seconds: caducidad concreta; si es None usa el default.

        Returns:
            Token string para colocar en `?token=<token>`.
        """
        if not resource:
            raise ValueError("resource no puede estar vacío")
        ttl = self._default_ttl if ttl_seconds is None else int(ttl_seconds)
        exp = int(self._clock()) + ttl
        sig = self._compute_sig(resource, exp)
        return f"{_TOKEN_VERSION}.{exp}.{sig}"

    # ───────────────────────────────────────────────────────────────────────
    # Validación (pasos 5-6 del pipeline)
    # ───────────────────────────────────────────────────────────────────────
    def verify(self, resource: str, token: Optional[str]) -> bool:
        """
        Paso 5 (validación). Devuelve True solo si el token:
          (a) tiene el formato y la versión esperados,
          (b) fue firmado por este mismo secreto para ESTE `resource`,
          (c) no ha caducado (exp >= ahora).

        No lanza excepciones ante tokens malformados: devuelve False (un
        atacante no debe poder provocar un 500 con basura en el query string).
        """
        if not token or not resource:
            return False
        try:
            version, exp_str, sig = token.split(".", 2)
        except ValueError:
            return False
        if version != _TOKEN_VERSION:
            return False
        if not exp_str.isdigit():
            return False
        exp = int(exp_str)
        # (c) caducidad
        if exp < int(self._clock()):
            return False
        # (b) integridad/autenticidad en tiempo constante
        expected = self._compute_sig(resource, exp)
        return hmac.compare_digest(expected, sig)

    def query_param(self, resource: str, ttl_seconds: Optional[int] = None) -> str:
        """
        Paso 3 (azúcar). Devuelve directamente "token=<token>" para concatenar
        a una URL. Atajo cómodo para los endpoints.
        """
        return f"token={self.sign(resource, ttl_seconds)}"


# ───────────────────────────────────────────────────────────────────────────
# Fábrica / singleton de runtime
# ───────────────────────────────────────────────────────────────────────────
_instance: Optional[SignedUrlService] = None


def get_signed_url_service() -> SignedUrlService:
    """
    Devuelve la instancia singleton cableada con la configuración del sistema.

    Importa `settings` de forma perezosa y relativa para funcionar tanto en
    runtime (`backend.app...`) como bajo el runner de tests (`app...`).
    """
    global _instance
    if _instance is None:
        from ..config import settings  # import relativo: robusto en ambos paths
        _instance = SignedUrlService(
            secret=settings.MEDIA_URL_SECRET,
            default_ttl_seconds=settings.MEDIA_URL_TTL_SECONDS,
        )
    return _instance
