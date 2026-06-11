"""
================================================================================
MÓDULO: camera_heuristics — Heurísticas para el alta de cámaras
================================================================================

PROPÓSITO
    Acelerar el ALTA de cámaras con sugerencias automáticas (NO decisiones):
      - suggest_dual_lens(): adivinar si una cámara es de doble lente a partir
        de su resolución/modelo (las dual-lens combinan dos sensores en un solo
        frame con aspect ratio atípico ~3:2).
      - build_default_urls(): autocompletar las URLs RTSP y ONVIF a partir de la
        IP, en el formato típico de las cámaras del proyecto (XiongMai/iCSee).

RESPONSABILIDAD
    Son SUGERENCIAS puras (funciones sin estado ni I/O): el usuario SIEMPRE
    puede corregirlas en el formulario de alta. No hablan ONVIF ni tocan BD.

DEPENDENCIAS: ninguna (solo stdlib).
QUIÉN LO CONSUME: la capa de servicio/rutas de alta de cámaras, para
    prerellenar el formulario antes de que el usuario confirme.
PIPELINE: #7 ONVIF (apoyo al alta; el resultado final entra en #1/#3 vía
    CameraManager + go2rtc).
================================================================================
"""
from __future__ import annotations

# Tokens en modelo/nombre que delatan una cámara de doble lente.
_DUAL_TOKENS = (
    "dual", "duallens", "dual-lens", "dual lens", "binocular",
    "2lens", "two lens", "doble lente", "twin",
)


def suggest_dual_lens(width: int = 0, height: int = 0,
                      model: str = "", name: str = "") -> bool:
    """
    Devuelve True si la cámara PROBABLEMENTE es dual-lens.

    Señales:
      1) El modelo/nombre contiene una palabra clave de doble lente.
      2) La resolución tiene una relación de aspecto atípica (~3:2) y alta
         resolución vertical: las dual-lens combinan dos sensores en un solo
         frame, dando relaciones distintas al 16:9 (1.78) o 4:3 (1.33) de las
         mono. La cámara del proyecto reporta 3072x2048 (relación 1.5).
    """
    text = f"{model} {name}".lower()
    if any(tok in text for tok in _DUAL_TOKENS):
        return True
    try:
        if width and height:
            ratio = width / height
            if 1.45 <= ratio <= 1.6 and height >= 1440:
                return True
    except Exception:
        pass
    return False


def build_default_urls(ip: str, username: str = "", password: str = "",
                       rtsp_port: int = 554, onvif_port: int = 8899) -> dict:
    """
    Sugiere las URLs RTSP y ONVIF a partir de la IP (formato XiongMai/iCSee,
    el de las cámaras de este proyecto). El usuario puede editarlas.
    """
    ip = (ip or "").strip()
    cred = ""
    if username:
        # Codificar @ y : en credenciales sería ideal; para el caso común
        # (admin/sin símbolos raros) basta con concatenar.
        cred = f"{username}:{password}@" if password else f"{username}@"
    return {
        "rtsp_url": f"rtsp://{cred}{ip}:{rtsp_port}/cam/realmonitor?channel=1&subtype=0",
        "onvif_url": f"http://{ip}:{onvif_port}/onvif/device_service",
    }
