"""
Go2RtcManager — Capa de medios (go2rtc) como proceso sidecar.

QUÉ ES go2rtc
-------------
Un servidor de medios escrito en Go (binario único, muy ligero) que:
  - habla RTSP con cada cámara UNA sola vez,
  - reexpone ese flujo H.264 como WebRTC / RTSP / MSE / HLS SIN transcodificar
    (`-c copy`), por lo que su coste de CPU es ≈ 0.

POR QUÉ EN ESTE PROYECTO
------------------------
Hoy cada consumidor (preview, grabación, móvil, IA) abre su propia conexión
RTSP a la cámara; las cámaras IP suelen aceptar solo 1-4 sesiones simultáneas y
se saturan. go2rtc centraliza esa conexión y deja a Python decodificar píxeles
SOLO cuando la IA lo necesita.

RESILIENCIA DE RED (requisito explícito)
----------------------------------------
- SIN internet: por defecto NO se usan STUN/TURN; solo candidatos ICE de host
  (la IP LAN). Todo funciona en una red aislada.
- Red inestable: go2rtc reconecta el RTSP de la cámara por su cuenta; ADEMÁS
  este manager supervisa el proceso y lo reinicia con backoff exponencial si
  muere. La grabación y el preview se recuperan solos.
- Con internet/acceso remoto: se pueden añadir candidatos (STUN/TURN) vía
  `GO2RTC_WEBRTC_CANDIDATES` sin tocar código.

PIPELINE DE ARRANQUE
--------------------
  Paso 1. `start()` comprueba el flag GO2RTC_ENABLED y que el binario exista.
  Paso 2. `reload(cameras)` genera el go2rtc.yaml desde la BD (solo cámaras activas).
  Paso 3. Lanza el subproceso go2rtc apuntando a ese yaml.
  Paso 4. Un hilo supervisor vigila el proceso y lo reinicia con backoff.
  Paso 5. Los clientes obtienen su URL con `rtsp_restream_url()` / `webrtc_api_base()`.

Este componente es ADITIVO: si GO2RTC_ENABLED=false (default), no hace nada y el
sistema sigue exactamente igual que antes.
"""
from __future__ import annotations

import logging
import shutil
import socket
import subprocess
import threading
import time
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


def detect_lan_ip() -> str:
    """
    Paso auxiliar. Detecta la IP LAN del servidor SIN necesidad de internet.

    Truco estándar: se abre un socket UDP "hacia" una dirección privada y se lee
    el extremo local elegido por el SO. No se envía tráfico real ni se requiere
    que el destino exista — solo fuerza al SO a elegir la interfaz de salida.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def stream_name(camera_id: int) -> str:
    """Nombre canónico del stream en go2rtc para una cámara."""
    return f"cam_{camera_id}"


def lens_stream_name(camera_id: int, lens: str) -> str:
    """Nombre del sub-stream de un lente (dual-lens), p.ej. 'cam_3_l1'."""
    return f"cam_{camera_id}_{lens}"


# Geometría de recorte para dual-lens. DEBE coincidir con la config del
# DualLensSplitter en camera_manager (split vertical = arriba/abajo, swap →
# l1 = mitad inferior, l2 = mitad superior). Si allí cambia, cambiar aquí.
#   crop=w:h:x:y  → l1 (abajo): crop=iw:ih/2:0:ih/2 ; l2 (arriba): crop=iw:ih/2:0:0
_DUAL_LENS_CROP = {
    "l1": "crop=iw:ih/2:0:ih/2",  # mitad inferior
    "l2": "crop=iw:ih/2:0:0",     # mitad superior
}

# Niveles de calidad para el DIRECTO. "high" no lleva escala (nativo / recorte
# pleno). "medium"/"low" añaden un escalado → go2rtc los transcodifica SOLO si
# alguien los está viendo (perezoso), así que su costo es como mucho 1 transcode
# por panel abierto. La grabación y la IA NO usan estos niveles (van al nativo).
_QUALITY_SCALE = {
    "medium": "scale=-2:480",
    "low": "scale=-2:360",
}


def build_go2rtc_config(
    cameras: Iterable[Any],
    *,
    api_host: str,
    api_port: int,
    rtsp_port: int,
    webrtc_port: int,
    public_host: str,
    extra_candidates: str = "",
    include_inactive: bool = False,
    hwaccel: str = "",
) -> dict:
    """
    Paso 2 (núcleo PURO y testeable). Construye el diccionario de config go2rtc
    a partir de una lista de cámaras.

    Reglas de negocio:
      - Solo se incluyen cámaras con `is_active=True` (salvo include_inactive),
        coherente con "las cámaras inactivas no aparecen en el en vivo".
      - Se omiten cámaras sin `rtsp_url`.
      - Cada cámara -> un stream `cam_<id>` con su rtsp_url como fuente.

    No realiza I/O: recibe objetos cámara (o mocks con .id/.rtsp_url/.is_active)
    y devuelve un dict. La serialización a YAML y el arranque del proceso son
    pasos separados, lo que permite testear la generación de forma aislada.
    """
    streams: dict[str, str] = {}
    for cam in cameras:
        if not include_inactive and not getattr(cam, "is_active", False):
            continue
        rtsp = getattr(cam, "rtsp_url", None)
        cam_id = getattr(cam, "id", None)
        if not rtsp or cam_id is None:
            continue
        streams[stream_name(cam_id)] = rtsp

        # Dual-lens: dos sub-streams recortados (transcode H264 + crop) que
        # PARTEN del stream ya ingerido (ffmpeg:cam_X), NO reabren la cámara
        # → respeta el límite de 1 conexión RTSP. Además H264 hace que el
        # WebRTC funcione en navegadores (H265 tiene soporte limitado).
        src = stream_name(cam_id)

        def _xcode(filters: str) -> str:
            # IMPORTANTE: el modificador `#hardware=qsv` de go2rtc NO se aplica
            # cuando se usa `#raw=` (go2rtc cae a libx264 software → ~500% CPU,
            # confirmado en telemetría). Para usar la GPU de verdad emitimos un
            # comando `exec:` EXPLÍCITO con `-hwaccel qsv` (decode por iGPU) +
            # `h264_qsv` (encode por iGPU). El crop/scale van por software en
            # memoria de sistema (barato). `-g 30 -bf 0` recorta el arranque.
            if hwaccel == "qsv":
                rtsp_in = f"rtsp://127.0.0.1:{rtsp_port}/{src}"
                # `-c:v hevc_qsv` (decoder QSV EXPLÍCITO) en vez de `-hwaccel qsv`:
                # con -hwaccel los frames salen en formato GPU (qsv) y el `scale`
                # por software no puede convertirlos → "Impossible to convert ...
                # src: qsv" (falla cam_X_*_low/_medium). Con -c:v hevc_qsv los
                # frames salen en memoria de sistema → crop+scale por software OK
                # → encode h264_qsv por GPU. (Asume cámara HEVC; si fuera H264
                # usar h264_qsv como decoder o GO2RTC_HWACCEL="".)
                # Flags de FLUIDEZ para el directo por WiFi/WebRTC:
                #   -g 15  → keyframe cada ~1s (cámara ~12-15fps). Con -g 30 el
                #            keyframe llegaba cada ~2.5-3s: ante CUALQUIER pérdida
                #            de paquete (WiFi) el vídeo se congelaba hasta el
                #            siguiente keyframe y "saltaba" ~3s. GOP corto = el
                #            salto se reduce a ~1s y recupera mucho antes.
                #   -async_depth 1 → QSV entrega cada frame al codificarlo en vez
                #            de agruparlos (su default ~4) → ritmo de frames
                #            estable, sin ráfagas que disparan el jitter buffer.
                #   -bf 0  → sin B-frames (menor latencia, ya estaba).
                return (
                    "exec:ffmpeg -hide_banner -loglevel error "
                    f"-c:v hevc_qsv -rtsp_transport tcp -i {rtsp_in} "
                    f"-vf {filters} -c:v h264_qsv -g 15 -bf 0 -async_depth 1 -an "
                    "-rtsp_transport tcp -f rtsp {output}"
                )
            # Sin hwaccel: transcode software (libx264 por defecto de go2rtc).
            return f"ffmpeg:{src}#video=h264#raw=-vf {filters}"

        if getattr(cam, "is_dual_lens", False):
            # Por lente: high = recorte pleno; medium/low = recorte + escala.
            for lens, crop in _DUAL_LENS_CROP.items():
                base = lens_stream_name(cam_id, lens)
                streams[base] = _xcode(crop)
                for q, sc in _QUALITY_SCALE.items():
                    streams[f"{base}_{q}"] = _xcode(f"{crop},{sc}")
        else:
            # Mono: high = nativo (cam_X, ya añadido, -c copy); medium/low = escala.
            for q, sc in _QUALITY_SCALE.items():
                streams[f"{src}_{q}"] = _xcode(sc)

    candidates: list[str] = [f"{public_host}:{webrtc_port}"]
    for extra in (extra_candidates or "").split(","):
        extra = extra.strip()
        if extra:
            candidates.append(extra)

    return {
        "api": {"listen": f"{api_host}:{api_port}"},
        "rtsp": {"listen": f":{rtsp_port}"},
        "webrtc": {"listen": f":{webrtc_port}", "candidates": candidates},
        "streams": streams,
        # info: muestra cuándo cada stream conecta/falla con la cámara
        # (clave para diagnosticar cámaras de 1 sola conexión RTSP).
        "log": {"level": "info"},
    }


def _dump_yaml(data: dict) -> str:
    """
    Serializa el config a YAML. Usa PyYAML si está disponible; si no, emite un
    YAML mínimo a mano (la estructura es plana y conocida). Esto evita una
    dependencia dura solo para escribir 15 líneas.
    """
    try:
        import yaml  # type: ignore

        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    except Exception:
        lines: list[str] = []

        def emit(obj, indent=0):
            pad = "  " * indent
            for key, val in obj.items():
                if isinstance(val, dict):
                    lines.append(f"{pad}{key}:")
                    emit(val, indent + 1)
                elif isinstance(val, list):
                    lines.append(f"{pad}{key}:")
                    for item in val:
                        lines.append(f"{pad}  - {_yaml_scalar(item)}")
                else:
                    lines.append(f"{pad}{key}: {_yaml_scalar(val)}")

        emit(data)
        return "\n".join(lines) + "\n"


def _yaml_scalar(v: Any) -> str:
    """Cita un escalar YAML si contiene caracteres conflictivos (p.ej. URLs con ':')."""
    s = str(v)
    if s == "":
        return '""'
    if any(c in s for c in ':#@{}[],&*?|<>=!%\\"\'') or s.startswith(" ") or s.endswith(" "):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


class Go2RtcManager:
    """
    Singleton que gestiona el ciclo de vida del proceso go2rtc.

    Es seguro instanciarlo aunque go2rtc esté desactivado: en ese caso `start()`
    es un no-op y las funciones de URL siguen devolviendo valores coherentes
    (útiles para construir respuestas de API sin ramificar en cada endpoint).
    """

    _instance: Optional["Go2RtcManager"] = None
    _lock = threading.Lock()

    # Backoff de reinicio del supervisor (segundos).
    _RESTART_BACKOFF = [1, 2, 5, 10, 20, 30]

    def __new__(cls) -> "Go2RtcManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_once()
            return cls._instance

    def _init_once(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._supervisor: Optional[threading.Thread] = None
        self._reconciler: Optional[threading.Thread] = None
        self._running = False
        self._public_host = ""
        self._cfg = None  # config del sistema (lazy)
        self._last_sig = None  # firma del último set de streams escrito

    # ───────────────────────────────────────────────────────────────────────
    # Configuración (lazy, robusta en runtime y en tests)
    # ───────────────────────────────────────────────────────────────────────
    @property
    def cfg(self):
        if self._cfg is None:
            from ..config import settings
            self._cfg = settings
        return self._cfg

    @property
    def public_host(self) -> str:
        """Host anunciado a clientes: el de config si se fijó, o la IP LAN."""
        if not self._public_host:
            self._public_host = self.cfg.GO2RTC_PUBLIC_HOST or detect_lan_ip()
        return self._public_host

    # ───────────────────────────────────────────────────────────────────────
    # URLs que consumen los clientes (pasos 5)
    # ───────────────────────────────────────────────────────────────────────
    def rtsp_restream_url(
        self, camera_id: int, lens: Optional[str] = None, quality: str = "high"
    ) -> str:
        """
        URL RTSP del restream.
          - Sin `lens` → stream combinado (cam_X). Con `lens` → sub-stream del
            lente dual (cam_X_l1/l2).
          - `quality`: "high" (nativo/recorte pleno), "medium" (480p), "low"
            (360p). Solo aplica al DIRECTO; grabación/IA usan "high"/nativo.
        """
        base = lens_stream_name(camera_id, lens) if lens else stream_name(camera_id)
        name = base if quality == "high" else f"{base}_{quality}"
        return f"rtsp://{self.public_host}:{self.cfg.GO2RTC_RTSP_PORT}/{name}"

    def hls_url(
        self, camera_id: int, lens: Optional[str] = None, quality: str = "high"
    ) -> str:
        """
        URL HLS del restream (la consume la app MÓVIL con ExoPlayer, que es muy
        fiable con HLS y flojo con RTSP). go2rtc genera el HLS bajo demanda desde
        el stream ya ingerido (sin reabrir la cámara). Usa public_host (IP LAN)
        + el puerto de la API, que ahora escucha en 0.0.0.0.
        """
        base = lens_stream_name(camera_id, lens) if lens else stream_name(camera_id)
        name = base if quality == "high" else f"{base}_{quality}"
        return (
            f"http://{self.public_host}:{self.cfg.GO2RTC_API_PORT}"
            f"/api/stream.m3u8?src={name}"
        )

    def webrtc_api_base(self) -> str:
        """Base de la API de go2rtc para el proxy de signaling WHEP del backend.

        Siempre loopback: es el backend hablando con el go2rtc LOCAL. (GO2RTC_API_HOST
        puede ser 0.0.0.0 para exponer HLS a la LAN, pero conectarse a 0.0.0.0 como
        destino falla en Windows; 127.0.0.1 siempre funciona y la API escucha ahí.)
        """
        return f"http://127.0.0.1:{self.cfg.GO2RTC_API_PORT}"

    def is_enabled(self) -> bool:
        return bool(self.cfg.GO2RTC_ENABLED)

    def is_running(self) -> bool:
        return self._running and self._proc is not None and self._proc.poll() is None

    # ───────────────────────────────────────────────────────────────────────
    # Generación y escritura de config (paso 2)
    # ───────────────────────────────────────────────────────────────────────
    def write_config(self, cameras: Iterable[Any]) -> str:
        """Genera el go2rtc.yaml desde las cámaras y lo escribe en disco."""
        cfg = self.cfg
        data = build_go2rtc_config(
            cameras,
            api_host=cfg.GO2RTC_API_HOST,
            api_port=cfg.GO2RTC_API_PORT,
            rtsp_port=cfg.GO2RTC_RTSP_PORT,
            webrtc_port=cfg.GO2RTC_WEBRTC_PORT,
            public_host=self.public_host,
            extra_candidates=cfg.GO2RTC_WEBRTC_CANDIDATES,
            hwaccel=cfg.GO2RTC_HWACCEL,
        )
        import os

        path = os.path.abspath(cfg.GO2RTC_CONFIG_PATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_dump_yaml(data))
        logger.info("go2rtc.yaml generado con %d stream(s) -> %s", len(data["streams"]), path)
        return path

    # ───────────────────────────────────────────────────────────────────────
    # Reconciliador: mantiene el config de go2rtc sincronizado con la BD
    # ───────────────────────────────────────────────────────────────────────
    # PROBLEMA que resuelve: el config de go2rtc se generaba UNA vez al arrancar
    # con las cámaras activas de ese momento. Si arrancabas con 0 activas y luego
    # añadías/activabas una, go2rtc no tenía su stream → el worker leía
    # rtsp://.../cam_X y recibía 404. Ahora un hilo revisa la BD cada pocos
    # segundos y, si el conjunto de cámaras cambió, regenera el YAML y reinicia
    # go2rtc. Incluye TODAS las cámaras (activas o no): go2rtc conecta a la cámara
    # de forma PEREZOSA (solo cuando alguien consume el stream), así que tener
    # streams de cámaras inactivas no cuesta nada hasta que se activan.
    def _load_all_cameras(self) -> list:
        """Carga TODAS las cámaras de la BD como snapshots ligeros.

        Solo SELECT de las 4 columnas necesarias (no onvif_url/password/etc.),
        ya que esto corre periódicamente en el reconciliador.
        """
        from types import SimpleNamespace
        try:
            from ..database.connection import db_manager
            from ..database.models import Camera
            cams = []
            with db_manager.get_session() as session:
                rows = session.query(
                    Camera.id, Camera.rtsp_url, Camera.is_active, Camera.is_dual_lens
                ).all()
                for cid, rtsp, active, dual in rows:
                    cams.append(SimpleNamespace(
                        id=cid,
                        rtsp_url=rtsp,
                        is_active=bool(active),
                        is_dual_lens=bool(dual),
                    ))
            return cams
        except Exception as e:
            logger.warning("go2rtc reconcile: no se pudo leer cámaras de BD: %s", e)
            return []

    def reconcile(self) -> None:
        """Regenera el config desde la BD y reinicia go2rtc SOLO si cambió."""
        if not self.is_enabled():
            return
        cfg = self.cfg
        cams = self._load_all_cameras()
        data = build_go2rtc_config(
            cams,
            api_host=cfg.GO2RTC_API_HOST,
            api_port=cfg.GO2RTC_API_PORT,
            rtsp_port=cfg.GO2RTC_RTSP_PORT,
            webrtc_port=cfg.GO2RTC_WEBRTC_PORT,
            public_host=self.public_host,
            extra_candidates=cfg.GO2RTC_WEBRTC_CANDIDATES,
            hwaccel=cfg.GO2RTC_HWACCEL,
            include_inactive=True,   # todas las cámaras; go2rtc conecta perezoso
        )
        # Guardia anti-churn: si la lectura de cámaras vino VACÍA pero antes
        # teníamos streams, es casi seguro un fallo transitorio de BD (no que
        # borraran todas las cámaras). Reiniciar go2rtc con config vacío cortaría
        # TODOS los streams (el celular ve un micro-corte) y al siguiente ciclo
        # volvería a cambiar → churn. Conservamos el config actual.
        if not data["streams"] and self._last_sig:
            logger.warning(
                "go2rtc reconcile: lectura de cámaras vacía; conservo el config "
                "actual (probable fallo transitorio de BD)."
            )
            return

        sig = frozenset(data["streams"].items())
        if sig == self._last_sig and self.is_running():
            return  # nada cambió
        self._last_sig = sig

        import os
        path = os.path.abspath(cfg.GO2RTC_CONFIG_PATH)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_dump_yaml(data))
            logger.info(
                "go2rtc reconcile: %d stream(s) (reinicio para aplicar)",
                len(data["streams"]),
            )
        except Exception as e:
            logger.warning("go2rtc reconcile: no se pudo escribir config: %s", e)
            return

        # IMPORTANTE: NO respawneamos aquí. Solo matamos el proceso y dejamos
        # que el hilo SUPERVISOR (único que hace _spawn) lo levante con el nuevo
        # config. Si reconciliador y supervisor spawnearan a la vez habría DOS
        # go2rtc peleando por el puerto 8554. Así el supervisor es el único
        # spawner y no hay carrera.
        if self.is_running():
            self._terminate_proc()

    def _reconcile_loop(self) -> None:
        """Revisa la BD periódicamente y aplica cambios de cámaras a go2rtc."""
        while self._running:
            try:
                self.reconcile()
            except Exception as e:
                logger.warning("go2rtc reconcile loop error: %s", e)
            # 15s: el worker FFmpeg tarda ~60s en agotar sus reintentos, así que
            # 15s da margen de sobra para que una cámara recién añadida tenga su
            # stream, y reduce 3× la lectura periódica de la BD frente a 5s.
            for _ in range(15):
                if not self._running:
                    break
                time.sleep(1)

    # ───────────────────────────────────────────────────────────────────────
    # Ciclo de vida del proceso (pasos 1, 3, 4)
    # ───────────────────────────────────────────────────────────────────────
    def start(self, cameras: Iterable[Any]) -> bool:
        """
        Paso 1+3. Arranca go2rtc si está habilitado y el binario existe.
        Devuelve True si quedó corriendo, False si se omitió o falló.
        """
        if not self.is_enabled():
            logger.info("go2rtc desactivado (GO2RTC_ENABLED=false). No se arranca.")
            return False
        binary = shutil.which(self.cfg.GO2RTC_BINARY) or (
            self.cfg.GO2RTC_BINARY if _looks_like_path(self.cfg.GO2RTC_BINARY) else None
        )
        if not binary:
            logger.error(
                "go2rtc habilitado pero el binario '%s' no está en PATH. "
                "Instálalo o define GO2RTC_BINARY con la ruta absoluta.",
                self.cfg.GO2RTC_BINARY,
            )
            return False

        config_path = self.write_config(cameras)
        self._binary = binary
        self._config_path = config_path
        self._running = True
        # Abrir los puertos en el Firewall de Windows automáticamente para que
        # el usuario NO tenga que tocar nada (la app móvil necesita alcanzar
        # HLS:1984, RTSP:8554 y WebRTC:8555 desde la LAN).
        self._ensure_firewall_rules()
        self._spawn()
        self._supervisor = threading.Thread(
            target=self._supervise_loop, name="Go2RtcSupervisor", daemon=True
        )
        self._supervisor.start()
        # Reconciliador: sincroniza el config con la BD (cámaras añadidas/
        # activadas/borradas en caliente) sin que nadie tenga que llamarlo.
        self._reconciler = threading.Thread(
            target=self._reconcile_loop, name="Go2RtcReconciler", daemon=True
        )
        self._reconciler.start()
        return True

    def _ensure_firewall_rules(self) -> None:
        """
        Best-effort: abre los puertos de go2rtc en el Firewall de Windows para
        que la app móvil pueda conectarse sin que el usuario configure nada.

        - Solo en Windows. En otros SO no hace nada.
        - Idempotente: usa un nombre de regla fijo por puerto; si ya existe, no
          la duplica (primero consulta con `show rule`).
        - Requiere privilegios de administrador. Si el backend NO corre elevado,
          netsh devuelve error y solo lo registramos (sin romper el arranque):
          en ese caso el usuario tendría que permitir el puerto una vez.
        """
        import sys

        if not sys.platform.startswith("win"):
            return

        ports = [
            ("CamLink go2rtc API", self.cfg.GO2RTC_API_PORT, "TCP"),
            ("CamLink go2rtc RTSP", self.cfg.GO2RTC_RTSP_PORT, "TCP"),
            ("CamLink go2rtc WebRTC TCP", self.cfg.GO2RTC_WEBRTC_PORT, "TCP"),
            ("CamLink go2rtc WebRTC UDP", self.cfg.GO2RTC_WEBRTC_PORT, "UDP"),
        ]
        # Flask del backend (lo usa la móvil para la API REST/WS).
        try:
            backend_port = int(getattr(self.cfg, "PORT", 5000) or 5000)
            ports.append(("CamLink backend API", backend_port, "TCP"))
        except Exception:
            pass

        missing: list[tuple[str, int, str]] = []
        needs_elevation = False
        for name, port, proto in ports:
            try:
                # ¿Existe ya la regla? (evita duplicados en cada arranque)
                check = subprocess.run(
                    ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                    capture_output=True, text=True, timeout=10,
                )
                if check.returncode == 0 and "No rules match" not in check.stdout:
                    continue
                add = subprocess.run(
                    [
                        "netsh", "advfirewall", "firewall", "add", "rule",
                        f"name={name}", "dir=in", "action=allow",
                        f"protocol={proto}", f"localport={port}",
                        "profile=any",
                    ],
                    capture_output=True, text=True, timeout=10,
                )
                if add.returncode == 0:
                    logger.info("Firewall: regla añadida '%s' (%s/%s)", name, port, proto)
                else:
                    detail = (add.stderr or add.stdout).strip()
                    missing.append((name, port, proto))
                    if "elevaci" in detail.lower() or "elevation" in detail.lower():
                        needs_elevation = True
            except Exception as e:
                logger.warning("Firewall: error configurando '%s': %s", name, e)

        # Sin permisos → intentar UNA sola vez con elevación (UAC). Las reglas
        # persisten, así que el usuario solo verá el aviso de UAC la primera vez.
        if missing and needs_elevation and not getattr(self, "_fw_elevation_tried", False):
            self._fw_elevation_tried = True
            self._add_firewall_rules_elevated(missing)

    def _add_firewall_rules_elevated(self, rules: "list[tuple[str, int, str]]") -> None:
        """Lanza UN proceso elevado (UAC) que crea todas las reglas que faltan."""
        try:
            cmds = " & ".join(
                f'netsh advfirewall firewall add rule name="{n}" dir=in '
                f"action=allow protocol={p} localport={port} profile=any"
                for n, port, p in rules
            )
            # Start-Process -Verb RunAs dispara el diálogo de UAC una vez.
            ps = (
                f"Start-Process -FilePath cmd.exe "
                f"-ArgumentList '/c {cmds}' -Verb RunAs -WindowStyle Hidden"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=30,
            )
            logger.info(
                "Firewall: solicitada elevación (UAC) para abrir %d puerto(s) "
                "de go2rtc/backend. Si aceptaste, ya quedaron abiertos.",
                len(rules),
            )
        except Exception as e:
            logger.warning(
                "Firewall: no se pudo elevar para abrir puertos (%s). "
                "Abre manualmente 1984/8554/8555 o ejecuta el backend como admin.",
                e,
            )

    def _spawn(self) -> None:
        """Lanza el subproceso go2rtc, volcando su salida a un archivo de log."""
        import os

        try:
            log_path = os.path.join(os.path.dirname(self._config_path), "go2rtc.log")
            # 'a' para conservar entre reinicios; go2rtc rota poco. Útil para
            # diagnosticar por qué un stream no conecta a la cámara.
            self._log_fh = open(log_path, "a", encoding="utf-8", buffering=1)
            self._proc = subprocess.Popen(
                [self._binary, "-config", self._config_path],
                stdout=self._log_fh,
                stderr=subprocess.STDOUT,
            )
            logger.info("go2rtc lanzado (pid=%s) — log en %s", self._proc.pid, log_path)
        except Exception as e:  # pragma: no cover - depende del entorno
            logger.error("No se pudo lanzar go2rtc: %s", e)
            self._proc = None

    def _supervise_loop(self) -> None:
        """
        Paso 4. Vigila el proceso; si muere y seguimos en 'running', lo reinicia
        con backoff exponencial (resiliencia ante caídas/red inestable).
        """
        attempt = 0
        while self._running:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                delay = self._RESTART_BACKOFF[min(attempt, len(self._RESTART_BACKOFF) - 1)]
                if self._running:
                    logger.warning("go2rtc no está vivo; reintentando en %ss", delay)
                    time.sleep(delay)
                    if not self._running:
                        break
                    self._spawn()
                    attempt += 1
            else:
                attempt = 0  # estable → resetear backoff
                time.sleep(2)

    def reload(self, cameras: Iterable[Any]) -> None:
        """
        Regenera el config (p.ej. al añadir/quitar/activar una cámara) y reinicia
        go2rtc para aplicarlo. Si está desactivado, solo reescribe el archivo.
        """
        if not self.is_enabled():
            return
        self.write_config(cameras)
        if self.is_running():
            logger.info("Recargando go2rtc (reinicio para aplicar config)")
            self._terminate_proc()
            self._spawn()

    def stop(self) -> None:
        """Detiene el supervisor y el proceso."""
        self._running = False
        self._terminate_proc()

    def _terminate_proc(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None


def _looks_like_path(value: str) -> bool:
    return ("/" in value) or ("\\" in value)
