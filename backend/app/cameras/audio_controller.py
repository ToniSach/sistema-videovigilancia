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

from onvif import ONVIFCamera

from ..database.models import Camera
from .onvif_common import (
    candidate_ports as _common_candidate_ports,
    build_onvif_cam as _common_build_cam,
    persist_onvif_port as _common_persist_port,
)

logger = logging.getLogger(__name__)


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
                # ffmpeg emite el listado en UTF-8. Si dejamos que Python lo
                # decodifique con la cp local (cp1252 en Windows ES), nombres
                # con caracteres como "®" salen corruptos ("Â®") y luego NO
                # coinciden con el device dshow → "Could not find audio device".
                capture_output=True, encoding="utf-8", errors="replace", timeout=10,
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
        self._audio_supported = False      # mic de la cámara (para ESCUCHAR)
        self._audio_out_supported = False  # altavoz de la cámara (para HABLAR)
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        # IMPORTANTE: proceso de escucha como atributo de INSTANCIA (no de clase).
        # Antes era atributo de clase y se compartía entre controladores → al
        # recrear el controlador, stop_listen no encontraba el ffplay correcto y
        # el audio "seguía escuchándose para siempre".
        self._listen_process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._detect_audio_support()

    # ------------------------------------------------------------------
    def _candidate_ports(self) -> list[int]:
        return _common_candidate_ports(self._camera)

    def _build_cam(self, port: int) -> Optional[ONVIFCamera]:
        return _common_build_cam(self._camera, port, "AUDIO")

    def _persist_onvif_port(self, port: int) -> None:
        _common_persist_port(self._camera, port, "AUDIO")

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

            has_audio_in = False   # encoder = micro de la cámara (escuchar)
            has_audio_out = False  # output  = altavoz de la cámara (hablar)
            for p in profiles:
                if getattr(p, "AudioEncoderConfiguration", None):
                    has_audio_in = True
                if getattr(p, "AudioOutputConfiguration", None):
                    has_audio_out = True
            self._audio_supported = has_audio_in
            self._audio_out_supported = has_audio_out
            logger.info(
                f"[AUDIO] cam={self._camera.id}: micro(escuchar)={has_audio_in} "
                f"altavoz(hablar)={has_audio_out}"
            )

            self._cam = cam
            self._connected = True
        except Exception as e:
            logger.warning(f"[AUDIO] cam={self._camera.id} error detectando audio: {e}")
            self._connected = False

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def is_supported(self) -> bool:
        """Soporte para ESCUCHAR (la cámara tiene micro/encoder de audio)."""
        return self._connected and self._audio_supported

    def is_talk_supported(self) -> bool:
        """
        Soporte para HABLAR. Es best-effort: muchas cámaras NO anuncian su
        AudioOutputConfiguration por ONVIF aunque tengan altavoz y acepten el
        backchannel. Por eso basta con que ONVIF responda (self._connected);
        si además anuncia salida, mejor. Bloquear por la ausencia del config
        impediría hablar en cámaras que sí funcionan.
        """
        return self._connected

    def is_active(self) -> bool:
        return self._running and self._process is not None and self._process.poll() is None

    def start_talk(self, mic_device: Optional[str] = None) -> bool:
        """
        Empieza a transmitir el micrófono local hacia la cámara vía RTP.
        mic_device: nombre del dispositivo (Windows: 'Microphone'; Linux: 'default').
        """
        if not self.is_talk_supported():
            logger.warning(
                f"[AUDIO] cam={self._camera.id}: talk no disponible "
                f"(ONVIF no respondió; no se puede ubicar la cámara)"
            )
            return False
        if not self._audio_out_supported:
            # No bloqueamos, pero avisamos: es probable que la cámara no tenga
            # altavoz o no acepte backchannel ONVIF estándar.
            logger.info(
                f"[AUDIO] cam={self._camera.id}: la cámara no anuncia salida de "
                f"audio; intento talk de todas formas (best-effort)."
            )
        with self._lock:
            if self._running and self._process and self._process.poll() is None:
                logger.info(f"[AUDIO] cam={self._camera.id}: ya está hablando")
                return True

            # Resolver el micrófono: si el cliente no especificó uno (o mandó el
            # genérico), auto-detectamos el PRIMER dispositivo real del sistema.
            # En Windows NO existe un device llamado "Microphone" por defecto —
            # hay que usar el nombre EXACTO que reporta dshow (p.ej. "Micrófono
            # (Realtek...)"), o ffmpeg falla con "Could not find audio device".
            if not mic_device or mic_device.strip().lower() in ("", "default", "microphone"):
                try:
                    devices = list_input_audio_devices()
                except Exception:
                    devices = []
                # En Windows descartamos "default" (no es un device dshow válido).
                real = [d for d in devices if d and d.lower() != "default"]
                if real:
                    mic_device = real[0]
                    logger.info(f"[AUDIO] cam={self._camera.id}: micro auto = '{mic_device}'")

            system = platform.system()
            if system == "Windows":
                if not mic_device or mic_device.strip().lower() in ("", "default", "microphone"):
                    logger.error(
                        f"[AUDIO] cam={self._camera.id}: no se detectó ningún "
                        f"micrófono en el sistema (dshow). Conecta uno o elígelo "
                        f"en el selector 'Mic'."
                    )
                    return False
                in_args = ["-f", "dshow", "-i", f"audio={mic_device}"]
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
            proc = self._listen_process
            pid = proc.pid
            try:
                logger.info(f"[AUDIO] cam={self._camera.id} LISTEN STOP (pid={pid})")
                proc.kill()  # forzado (en Windows = TerminateProcess)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                # Garantía extra en Windows: matar el árbol por si quedó vivo.
                if platform.system() == "Windows" and proc.poll() is None:
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            capture_output=True, timeout=5,
                        )
                    except Exception:
                        pass
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
            # Reusar SIEMPRE el controlador que esté EN USO (escuchando/hablando)
            # para que stop_listen/stop_talk actúen sobre el MISMO proceso que se
            # inició. Antes, recrearlo dejaba el ffplay/ffmpeg anterior huérfano
            # ("seguía escuchando para siempre").
            if ctrl is not None and (
                ctrl.is_supported() or ctrl.is_listening() or ctrl.is_active()
            ):
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

    def stop_all(self) -> None:
        """Detiene escucha y talk de TODAS las cámaras. Se llama al apagar el
        backend (atexit) para no dejar procesos ffplay/ffmpeg huérfanos que
        sigan sonando tras cerrar/reiniciar el servidor."""
        with self._lock:
            ctrls = list(self._controllers.values())
        for ctrl in ctrls:
            try:
                ctrl.stop_listen()
            except Exception:
                pass
            try:
                ctrl.stop_talk()
            except Exception:
                pass


audio_manager = AudioManager()

# Al cerrar el proceso (Ctrl+C / exit), matar cualquier ffplay/ffmpeg de audio
# vivo para que no quede sonando en background (orígenes de "sigo escuchando
# después de parar"). atexit no corre en kill -9, pero sí en Ctrl+C normal.
import atexit as _atexit
_atexit.register(audio_manager.stop_all)
