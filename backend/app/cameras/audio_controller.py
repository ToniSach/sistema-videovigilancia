"""
Audio bidireccional via ONVIF Profile S/T y FFmpeg RTP
"""
import platform
import subprocess
import logging
import threading
import time
from typing import Optional
from onvif import ONVIFCamera
from ..database.models import Camera


class AudioController:
    """Controlador de audio bidireccional usando ONVIF y FFmpeg"""

    def __init__(self, camera: Camera):
        self._camera = camera
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._onvif_cam = None
        self._audio_output_token = None
        self._connected = False
        self._audio_url = None

        self._detect_audio_support()

    def _detect_audio_support(self) -> None:
        """Detecta soporte de audio bidireccional via ONVIF"""
        try:
            self._onvif_cam = ONVIFCamera(
                self._camera.ip_address,
                80,
                self._camera.username,
                self._camera.password
            )

            media = self._onvif_cam.create_media_service()
            profiles = media.GetProfiles()

            if not profiles:
                logging.warning(f"No profiles found for camera {self._camera.id}")
                return

            profile = profiles[0]

            # Verificar si tiene configuración de audio
            has_audio = hasattr(profile, "AudioEncoderConfiguration") and profile.AudioEncoderConfiguration

            if not has_audio:
                logging.warning(f"Cámara {self._camera.id} no tiene audio encoder configuration")
                return

            # Intentar obtener AudioOutputs via DeviceIO service primero
            try:
                deviceio = self._onvif_cam.create_devicemgmt_service()
                # Algunas cámaras exponen audio outputs via device management
                self._audio_output_token = "AudioOutput_1"  # Token por defecto
            except:
                pass

            # Intentar con media service GetAudioOutputs si está disponible
            try:
                audio_outputs = media.GetAudioOutputs()
                if audio_outputs and len(audio_outputs) > 0:
                    self._audio_output_token = audio_outputs[0].token
            except Exception:
                # Fallback: usar token genérico
                self._audio_output_token = f"camera_{self._camera.id}_audio"

            # Obtener URL del stream de audio (para recibir audio)
            try:
                stream_setup = {
                    "Stream": "RTP-Unicast",
                    "Transport": {"Protocol": "RTSP", "Tunnel": None}
                }
                uri_response = media.GetStreamUri({
                    "StreamSetup": stream_setup,
                    "ProfileToken": profile.token
                })
                self._audio_url = uri_response.Uri
            except Exception as e:
                logging.warning(f"No se pudo obtener URI de audio: {e}")
                self._audio_url = f"rtsp://{self._camera.ip_address}/audio"

            self._connected = True
            logging.info(f"Audio bidireccional disponible para cámara {self._camera.id}")

        except Exception as e:
            logging.warning(f"Audio bidireccional no disponible para cámara {self._camera.id}: {e}")
            self._connected = False

    def start_talk(self) -> bool:
        """
        Inicia transmisión de audio del PC hacia la cámara (speak).
        Captura micrófono del sistema y envía vía RTP.
        """
        if not self._connected:
            return False

        if self._running:
            return True

        try:
            # Construir comando FFmpeg según el sistema operativo
            system = platform.system()

            if system == "Windows":
                # DirectShow en Windows
                # En Windows, detectar dispositivo de audio automáticamente
                # Usar lista de dispositivos: ffmpeg -list_devices true -f dshow -i dummy
                input_device = '-f dshow -i audio="Microphone"'  # Nombre genérico
                # Alternativa usando default (puede requerir ajuste según sistema)
            elif system == "Linux":
                # ALSA en Linux
                input_device = "-f alsa -i default -thread_queue_size 4096"
            elif system == "Darwin":  # macOS
                input_device = '-f avfoundation -i ":0"'
            else:
                input_device = "-f alsa -i default"

            # Construir URL RTP para enviar audio a la cámara
            # La cámara debe exponer un endpoint RTP para recibir audio
            rtp_url = f"rtp://{self._camera.ip_address}:5004"

            # Comando FFmpeg completo
            cmd = [
                "ffmpeg",
                "-y",  # Sobrescribir sin preguntar
                "-hide_banner",
                "-loglevel", "error",
                *input_device.split(),
                "-acodec", "pcm_alaw",  # G.711 A-law (común en ONVIF)
                "-ar", "8000",  # 8kHz
                "-ac", "1",  # Mono
                "-f", "rtp",
                rtp_url
            ]

            logging.info(f"Iniciando audio stream hacia cámara {self._camera.id}")

            # Ejecutar FFmpeg
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE
            )

            # Verificar que no haya terminado inmediatamente
            time.sleep(0.5)
            if self._process.poll() is not None:
                stderr = self._process.stderr.read().decode()
                logging.error(f"FFmpeg terminó inmediatamente: {stderr}")
                return False

            self._running = True

            # Iniciar thread de monitoreo
            monitor_thread = threading.Thread(target=self._monitor_process, daemon=True)
            monitor_thread.start()

            return True

        except Exception as e:
            logging.error(f"Error iniciando audio talk cámara {self._camera.id}: {e}")
            return False

    def _monitor_process(self):
        """Monitorea el proceso FFmpeg"""
        if self._process:
            self._process.wait()
            self._running = False
            logging.info(f"Proceso de audio terminado para cámara {self._camera.id}")

    def stop_talk(self) -> bool:
        """Detiene la transmisión de audio"""
        try:
            if self._process:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()

                self._process = None

            self._running = False
            logging.info(f"Audio detenido para cámara {self._camera.id}")
            return True

        except Exception as e:
            logging.error(f"Error deteniendo audio cámara {self._camera.id}: {e}")
            return False

    def is_supported(self) -> bool:
        """Retorna True si audio bidireccional está disponible"""
        return self._connected

    def is_active(self) -> bool:
        """Retorna True si hay transmisión de audio activa"""
        return self._running and self._process is not None
