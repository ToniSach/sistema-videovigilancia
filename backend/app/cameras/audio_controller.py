"""
Audio bidireccional vía ONVIF + FFmpeg RTP.

Reescrito para:
- Multi-puerto ONVIF (80, 8000, 8080, 8899) + fallback WSSE.
- Logs detallados (qué dispositivo de micrófono usa, qué codec, qué URL RTP).
- AudioManager singleton para cachear controladores.
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading
import time
from typing import Optional
from urllib.parse import urlparse

from onvif import ONVIFCamera
from zeep.exceptions import Fault

from ..database.models import Camera

logger = logging.getLogger(__name__)


def _resolve_wsdl_dir() -> Optional[str]:
    try:
        import onvif
        pkg_dir = os.path.dirname(onvif.__file__)
        site_packages = os.path.dirname(pkg_dir)
        for cand in (os.path.join(site_packages, "wsdl"), os.path.join(pkg_dir, "wsdl")):
            if os.path.isdir(cand):
                return cand
    except Exception:
        pass
    return None


_WSDL_DIR = _resolve_wsdl_dir()
_ONVIF_PORTS = [80, 8000, 8080, 8899]


def list_input_audio_devices() -> list[str]:
    """
    Enumera dispositivos de audio de entrada (micrófonos) disponibles.

    Plataforma-específico:
      - Windows: ffmpeg -list_devices true -f dshow -i dummy
      - Linux:   busca en /proc/asound (ALSA)
      - macOS:   ffmpeg -f avfoundation -list_devices true -i ""
    """
    import platform
    import re
    system = platform.system()

    if system == "Windows":
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-list_devices", "true",
                 "-f", "dshow", "-i", "dummy"],
                capture_output=True, text=True, timeout=10,
            )
            stderr = result.stderr or ""
            # Formato: [dshow @ 0x...] "Nombre del Mic" (audio)
            devices = []
            for line in stderr.splitlines():
                m = re.search(r'"([^"]+)"\s+\(audio\)', line)
                if m and m.group(1) not in devices:
                    devices.append(m.group(1))
            return devices
        except Exception as e:
            logger.warning(f"No se pudo enumerar audio devices (Windows): {e}")
            return []

    if system == "Darwin":
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-f", "avfoundation",
                 "-list_devices", "true", "-i", ""],
                capture_output=True, text=True, timeout=10,
            )
            stderr = result.stderr or ""
            devices = []
            in_audio = False
            for line in stderr.splitlines():
                if "AVFoundation audio devices" in line:
                    in_audio = True
                    continue
                if in_audio:
                    m = re.search(r'\[(\d+)\]\s+(.+)', line)
                    if m:
                        devices.append(m.group(2).strip())
            return devices
        except Exception as e:
            logger.warning(f"No se pudo enumerar audio devices (macOS): {e}")
            return []

    # Linux / otros
    try:
        import os
        cards = []
        for fname in ("/proc/asound/cards", "/proc/asound/pcm"):
            if os.path.exists(fname):
                with open(fname) as f:
                    cards.append(f.read())
        return ["default"] + [c.strip().split("\n")[0] for c in cards if c.strip()]
    except Exception:
        return ["default"]


class AudioController:
    """Stream de audio del micrófono local hacia la cámara (talk-back)."""

    def __init__(self, camera: Camera):
        self._camera = camera
        self._cam: Optional[ONVIFCamera] = None
        self._connected = False
        self._audio_supported = False
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._lock = threading.Lock()
        self._detect_audio_support()

    # ------------------------------------------------------------------
    def _candidate_ports(self) -> list[int]:
        if self._camera.onvif_url:
            try:
                p = urlparse(self._camera.onvif_url).port
                if p:
                    return [p] + [x for x in _ONVIF_PORTS if x != p]
            except Exception:
                pass
        return list(_ONVIF_PORTS)

    def _build_cam(self, port: int) -> Optional[ONVIFCamera]:
        """El kwarg `wsse` no existe en esta versión; no lo pasamos."""
        kwargs = {"encrypt": False, "no_cache": True}
        if _WSDL_DIR:
            kwargs["wsdl_dir"] = _WSDL_DIR
        try:
            logger.debug(f"[AUDIO] cam={self._camera.id} probando {self._camera.ip_address}:{port}")
            cam = ONVIFCamera(
                self._camera.ip_address, port,
                self._camera.username or "", self._camera.password or "",
                **kwargs
            )
            cam.devicemgmt.GetCapabilities({"Category": "All"})
            logger.info(f"[AUDIO] cam={self._camera.id} ONVIF OK en :{port}")
            return cam
        except Fault as e:
            logger.debug(f"[AUDIO] cam={self._camera.id} Fault :{port} → {e}")
            return None
        except Exception as e:
            logger.debug(f"[AUDIO] cam={self._camera.id} :{port} → {type(e).__name__}: {e}")
            return None

    def _persist_onvif_port(self, port: int) -> None:
        expected = f"http://{self._camera.ip_address}:{port}/onvif/device_service"
        if self._camera.onvif_url == expected:
            return
        try:
            from ..database.connection import db_manager
            from ..database.models import Camera as CameraModel
            with db_manager.get_session() as session:
                cam = session.query(CameraModel).filter_by(id=self._camera.id).first()
                if cam is not None:
                    cam.onvif_url = expected
                    session.commit()
                    self._camera.onvif_url = expected
                    logger.info(f"[AUDIO] cam={self._camera.id} onvif_url guardado: {expected}")
        except Exception as e:
            logger.warning(f"[AUDIO] cam={self._camera.id} no pude guardar onvif_url: {e}")

    def _detect_audio_support(self) -> None:
        ports = self._candidate_ports()
        logger.info(f"[AUDIO] cam={self._camera.id} puertos candidatos: {ports}")
        cam = None
        working_port = None
        for port in ports:
            cam = self._build_cam(port)
            if cam is not None:
                working_port = port
                break
        if cam is None:
            logger.warning(f"[AUDIO] cam={self._camera.id}: ONVIF no respondió")
            return

        self._persist_onvif_port(working_port)

        try:
            media = cam.create_media_service()
            profiles = media.GetProfiles()
            if not profiles:
                logger.warning(f"[AUDIO] cam={self._camera.id} sin perfiles")
                return

            has_audio = False
            for p in profiles:
                if getattr(p, "AudioEncoderConfiguration", None):
                    has_audio = True
                    break
            self._audio_supported = has_audio
            if not has_audio:
                logger.info(f"[AUDIO] cam={self._camera.id}: la cámara no expone audio encoder")
            else:
                logger.info(f"[AUDIO] cam={self._camera.id}: audio soportado ✓")

            self._cam = cam
            self._connected = True
        except Exception as e:
            logger.warning(f"[AUDIO] cam={self._camera.id} error detectando audio: {e}")
            self._connected = False

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def is_supported(self) -> bool:
        return self._connected and self._audio_supported

    def is_active(self) -> bool:
        return self._running and self._process is not None and self._process.poll() is None

    def start_talk(self, mic_device: Optional[str] = None) -> bool:
        """
        Empieza a transmitir el micrófono local hacia la cámara vía RTP.
        mic_device: nombre del dispositivo (Windows: 'Microphone'; Linux: 'default').
        """
        if not self.is_supported():
            logger.warning(f"[AUDIO] cam={self._camera.id}: talk no soportado")
            return False
        with self._lock:
            if self._running and self._process and self._process.poll() is None:
                logger.info(f"[AUDIO] cam={self._camera.id}: ya está hablando")
                return True

            system = platform.system()
            if system == "Windows":
                device = mic_device or "Microphone"
                in_args = ["-f", "dshow", "-i", f"audio={device}"]
            elif system == "Linux":
                device = mic_device or "default"
                in_args = ["-f", "alsa", "-i", device, "-thread_queue_size", "4096"]
            elif system == "Darwin":
                device = mic_device or ":0"
                in_args = ["-f", "avfoundation", "-i", device]
            else:
                in_args = ["-f", "alsa", "-i", "default"]

            rtp_url = f"rtp://{self._camera.ip_address}:5004"
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                   *in_args,
                   "-acodec", "pcm_alaw", "-ar", "8000", "-ac", "1",
                   "-f", "rtp", rtp_url]

            logger.info(f"[AUDIO] cam={self._camera.id} TALK START: {' '.join(cmd)}")
            try:
                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except FileNotFoundError:
                logger.error("[AUDIO] FFmpeg no está en PATH")
                return False

            time.sleep(0.5)
            if self._process.poll() is not None:
                stderr = self._process.stderr.read().decode(errors="replace")
                logger.error(f"[AUDIO] cam={self._camera.id}: FFmpeg salió: {stderr[:500]}")
                self._process = None
                return False

            self._running = True
            threading.Thread(target=self._monitor, daemon=True).start()
            return True

    def _monitor(self):
        p = self._process
        if p is None:
            return
        p.wait()
        self._running = False
        logger.info(f"[AUDIO] cam={self._camera.id}: proceso FFmpeg terminado")

    # ------------------------------------------------------------------
    # Listen: recibir el audio que la cámara captura (audio FROM cam)
    # ------------------------------------------------------------------
    _listen_process: Optional[subprocess.Popen] = None

    def start_listen(self, rtsp_url: str) -> bool:
        """
        Reproduce el audio del RTSP en los altavoces locales.
        Usa ffplay (viene con FFmpeg) con `-nodisp` para audio-only.
        """
        with self._lock:
            if self._listen_process and self._listen_process.poll() is None:
                logger.info(f"[AUDIO] cam={self._camera.id} listen ya activo")
                return True

            # ffplay viene con FFmpeg estándar.
            ffplay = "ffplay"
            cmd = [
                ffplay, "-hide_banner", "-loglevel", "warning",
                "-nodisp",                    # sin ventana
                "-autoexit",
                "-rtsp_transport", "tcp",
                "-fflags", "nobuffer",
                "-flags", "low_delay",
                "-i", rtsp_url,
                "-vn",                        # sin video
            ]
            logger.info(f"[AUDIO] cam={self._camera.id} LISTEN START: ffplay -nodisp -i {rtsp_url[:60]}...")
            try:
                self._listen_process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except FileNotFoundError:
                logger.error("[AUDIO] ffplay no está en PATH (suele venir con FFmpeg)")
                self._listen_process = None
                return False

            time.sleep(0.3)
            if self._listen_process.poll() is not None:
                err = self._listen_process.stderr.read().decode(errors="replace")
                logger.error(f"[AUDIO] cam={self._camera.id} listen falló: {err[:300]}")
                self._listen_process = None
                return False
            return True

    def stop_listen(self) -> bool:
        with self._lock:
            if not self._listen_process:
                return False
            try:
                logger.info(f"[AUDIO] cam={self._camera.id} LISTEN STOP")
                self._listen_process.terminate()
                try:
                    self._listen_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._listen_process.kill()
                    self._listen_process.wait()
            finally:
                self._listen_process = None
            return True

    def is_listening(self) -> bool:
        return (self._listen_process is not None
                and self._listen_process.poll() is None)

    def stop_talk(self) -> bool:
        with self._lock:
            if not self._process:
                return False
            try:
                logger.info(f"[AUDIO] cam={self._camera.id} TALK STOP")
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
            finally:
                self._process = None
                self._running = False
            return True


class AudioManager:
    """Cache de AudioControllers por camera_id."""
    _instance: "Optional[AudioManager]" = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._controllers: dict[int, AudioController] = {}
        self._lock = threading.Lock()

    def get(self, camera: Camera) -> AudioController:
        with self._lock:
            ctrl = self._controllers.get(camera.id)
            if ctrl is not None and ctrl.is_supported():
                return ctrl
            ctrl = AudioController(camera)
            self._controllers[camera.id] = ctrl
            return ctrl

    def drop(self, camera_id: int) -> None:
        with self._lock:
            ctrl = self._controllers.pop(camera_id, None)
            if ctrl is not None:
                try:
                    ctrl.stop_talk()
                except Exception:
                    pass


audio_manager = AudioManager()
