# backend/app/streaming/hls_service.py
"""
================================================================================
MÓDULO: hls_service — Conversión MP4 → HLS de GRABACIONES (LEGACY / EN DESUSO)
================================================================================

ESTADO: LEGACY / EN DESUSO
    Servicio que transcodeaba grabaciones MP4 a HLS (segmentos .ts + manifiesto
    .m3u8) para reproducirlas en navegador/móvil. Hoy está EN DESUSO:
      - El DIRECTO se sirve por go2rtc (WebRTC/RTSP/HLS), no por aquí
        (ver go2rtc_manager.hls_url para el HLS de directo).
      - La REPRODUCCIÓN de grabaciones (pipeline #14) usa los MP4 directamente.
    No construir nuevas rutas sobre este módulo; se conserva como respaldo.

PROPÓSITO (cuando estaba activo)
    Dado un recording_id + ruta MP4, generar (de forma asíncrona y cacheada) un
    HLS reproducible por HTTP, sirviendo manifiesto y segmentos bajo demanda.

RESPONSABILIDAD
    - Cachear conversiones en RECORDINGS_PATH/hls_cache/<recording_id>/.
    - Limitar la concurrencia de conversiones (semáforo) y la repetición
      (no reconvertir si el manifiesto está fresco).
    - Limpiar el caché periódicamente (TTL).

PIPELINES (histórico)
    #12 Clips / #14 Reproducción — entrega HLS de grabaciones, hoy reemplazado
    por la reproducción directa de MP4.

DEPENDENCIAS
    config.settings.RECORDINGS_PATH — raíz del caché HLS.
    core.executor.global_executor — encola la conversión fuera del hilo HTTP.
    ffmpeg (PATH) — realiza el remux MP4→HLS (-c copy, sin recodificar).
================================================================================
"""
import os
import time
import shutil
import threading
import subprocess
import logging
from typing import Optional

from ..config import settings
from ..core.executor import global_executor

logger = logging.getLogger(__name__)


class HLSService:
    """Servicio de conversión MP4→HLS de grabaciones (LEGACY — ver módulo).

    ROL (histórico): cachea y sirve HLS de grabaciones bajo demanda. Singleton
    de conveniencia (`hls_service` al final). Mantiene un hilo de limpieza del
    caché y un semáforo que acota las conversiones ffmpeg concurrentes.

    Constantes:
      SEGMENT_DURATION ........... duración de cada segmento .ts (s).
      MAX_CONCURRENT_CONVERSIONS . tope de conversiones ffmpeg simultáneas.
      CACHE_TTL_SECONDS .......... frescura del manifiesto antes de regenerar.
    """

    SEGMENT_DURATION = 4
    MAX_CONCURRENT_CONVERSIONS = 2
    CACHE_TTL_SECONDS = 3600

    def __init__(self):
        self._cache_dir = os.path.join(settings.RECORDINGS_PATH, "hls_cache")
        os.makedirs(self._cache_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._active_conversions: dict[int, bool] = {}
        self._semaphore = threading.Semaphore(self.MAX_CONCURRENT_CONVERSIONS)
        self._ffmpeg_path = shutil.which("ffmpeg")
        if not self._ffmpeg_path:
            logger.error("FFmpeg no encontrado. HLS no estará disponible.")
        self._cleanup_running = True
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def get_hls_manifest(self, recording_id: int, mp4_path: str) -> Optional[str]:
        """Devuelve la ruta del manifiesto HLS si está listo, o None si se encola.

        Si el manifiesto cacheado está fresco, lo devuelve de inmediato. Si no,
        encola la conversión en el executor global y devuelve None (el cliente
        reintenta) — patrón no bloqueante. None también si falta ffmpeg/MP4.
        Inputs: recording_id, mp4_path. Llama a: _convert_to_hls (async).
        """
        if not self._ffmpeg_path or not os.path.exists(mp4_path):
            return None

        hls_dir = os.path.join(self._cache_dir, str(recording_id))
        manifest_path = os.path.join(hls_dir, "index.m3u8")

        if os.path.exists(manifest_path):
            age = time.time() - os.path.getmtime(manifest_path)
            if age < self.CACHE_TTL_SECONDS:
                return manifest_path

        with self._lock:
            if recording_id in self._active_conversions:
                return None
            self._active_conversions[recording_id] = True

        try:
            global_executor.submit(self._convert_to_hls, recording_id, mp4_path, hls_dir)
        except Exception as e:
            with self._lock:
                self._active_conversions.pop(recording_id, None)
            logger.error(f"Error encolando conversión HLS: {e}")
            return None

        return None

    def _convert_to_hls(self, recording_id: int, mp4_path: str, output_dir: str) -> None:
        with self._semaphore:
            try:
                os.makedirs(output_dir, exist_ok=True)
                cmd = [
                    self._ffmpeg_path,
                    "-i", mp4_path,
                    "-c:v", "copy",
                    "-c:a", "copy",
                    "-f", "hls",
                    "-hls_time", str(self.SEGMENT_DURATION),
                    "-hls_list_size", "0",
                    "-hls_segment_filename", os.path.join(output_dir, "segment_%03d.ts"),
                    os.path.join(output_dir, "index.m3u8")
                ]
                logger.info(f"Iniciando HLS para recording {recording_id}")
                subprocess.run(cmd, check=True, capture_output=True, timeout=300)
                logger.info(f"HLS generado exitosamente para {recording_id}")
            except Exception as e:
                logger.error(f"Error generando HLS para {recording_id}: {e}")
                if os.path.exists(output_dir):
                    try:
                        shutil.rmtree(output_dir)
                    except:
                        pass
            finally:
                with self._lock:
                    self._active_conversions.pop(recording_id, None)

    def get_segment(self, recording_id: int, segment_name: str) -> Optional[str]:
        """Devuelve la ruta de un segmento .ts del caché, o None si no es válido.

        Valida el nombre contra path traversal (rechaza "..", "/" y extensiones
        que no sean .ts) antes de resolver la ruta. None si el segmento no existe.
        """
        if ".." in segment_name or "/" in segment_name or not segment_name.endswith(".ts"):
            return None
        segment_path = os.path.join(self._cache_dir, str(recording_id), segment_name)
        if os.path.exists(segment_path):
            return segment_path
        return None

    def _cleanup_loop(self):
        while self._cleanup_running:
            time.sleep(3600)
            try:
                now = time.time()
                for folder in os.listdir(self._cache_dir):
                    folder_path = os.path.join(self._cache_dir, folder)
                    if os.path.isdir(folder_path):
                        age = now - os.path.getmtime(folder_path)
                        if age > self.CACHE_TTL_SECONDS * 2:
                            shutil.rmtree(folder_path, ignore_errors=True)
                            logger.info(f"Limpiado caché HLS antiguo: {folder}")
            except Exception as e:
                logger.error(f"Error limpiando caché HLS: {e}")

    def shutdown(self):
        self._cleanup_running = False


hls_service = HLSService()