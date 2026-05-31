"""
Live HLS Streaming Service - Transcodificación en tiempo real para móviles.
Genera segmentos HLS continuos desde streams RTSP con múltiples perfiles de calidad.
"""
import os
import time
import shutil
import threading
import subprocess
import logging
from typing import Dict, Optional, List
from dataclasses import dataclass
from pathlib import Path

from backend.app.config import settings
from backend.app.core.executor import global_executor

logger = logging.getLogger(__name__)


@dataclass
class HLSProfile:
    """Perfil de calidad para HLS."""
    name: str
    width: int
    height: int
    video_bitrate: str  # ej: "800k"
    audio_bitrate: str  # ej: "64k"
    maxrate: str        # ej: "900k"
    bufsize: str        # ej: "1200k"


# Perfiles estándar para Adaptive Bitrate (móvil)
HLS_PROFILES = [
    HLSProfile("480p", 854, 480, "800k", "64k", "900k", "1200k"),
    HLSProfile("720p", 1280, 720, "1500k", "128k", "1600k", "2400k"),
]


class LiveHLSService:
    """
    Servicio de streaming HLS live para cámaras IP.
    Crea pipelines FFmpeg independientes por cámara y perfil.
    """
    
    SEGMENT_DURATION = 2  # segundos (baja latencia para móvil)
    MAX_SEGMENTS = 10     # mantener últimos N segmentos (DVR en vivo)
    CLEANUP_INTERVAL = 60  # segundos
    
    def __init__(self):
        self._base_path = Path(settings.RECORDINGS_PATH) / "hls_live"
        self._base_path.mkdir(parents=True, exist_ok=True)
        
        self._active_streams: Dict[int, Dict] = {}  # camera_id -> {process, profiles, last_access}
        self._lock = threading.RLock()
        # Event en vez de bool: shutdown() despierta el cleanup_loop al
        # instante en lugar de esperar hasta el próximo sleep(60).
        self._shutdown_event = threading.Event()
        
        self._ffmpeg_path = shutil.which("ffmpeg")
        if not self._ffmpeg_path:
            logger.error("FFmpeg no encontrado. HLS Live no estará disponible.")
        
        # Thread de limpieza de streams inactivos
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()
        
        logger.info(f"LiveHLSService inicializado en {self._base_path}")
    
    def _get_camera_path(self, camera_id: int) -> Path:
        """Retorna directorio base para una cámara."""
        return self._base_path / str(camera_id)
    
    def _get_profile_path(self, camera_id: int, profile_name: str) -> Path:
        """Retorna directorio para un perfil específico."""
        return self._get_camera_path(camera_id) / profile_name
    
    def start_stream(self, camera_id: int, rtsp_url: str) -> bool:
        """
        Inicia streaming HLS para una cámara con múltiples perfiles.
        """
        if not self._ffmpeg_path:
            return False
        
        with self._lock:
            if camera_id in self._active_streams:
                # Actualizar timestamp de acceso (reutilizar)
                self._active_streams[camera_id]['last_access'] = time.time()
                logger.info(f"Reutilizando stream HLS existente para cámara {camera_id}")
                return True
            
            logger.info(f"Iniciando HLS live para cámara {camera_id}")
            
            camera_path = self._get_camera_path(camera_id)
            camera_path.mkdir(parents=True, exist_ok=True)
            
            profiles_info = {}
            
            for profile in HLS_PROFILES:
                profile_path = self._get_profile_path(camera_id, profile.name)
                profile_path.mkdir(exist_ok=True)
                
                process = self._start_ffmpeg_profile(
                    camera_id, rtsp_url, profile, profile_path
                )
                
                if process:
                    profiles_info[profile.name] = {
                        'process': process,
                        'path': profile_path,
                        'playlist': profile_path / "playlist.m3u8"
                    }
            
            if not profiles_info:
                logger.error(f"No se pudo iniciar ningún perfil HLS para cámara {camera_id}")
                return False
            
            self._active_streams[camera_id] = {
                'rtsp_url': rtsp_url,
                'profiles': profiles_info,
                'start_time': time.time(),
                'last_access': time.time(),
                'master_playlist': camera_path / "master.m3u8"
            }
            
            # Generar master playlist (manifesto ABR)
            self._generate_master_playlist(camera_id)
            
            return True
    
    def _start_ffmpeg_profile(self, camera_id: int, rtsp_url: str, 
                              profile: HLSProfile, output_path: Path) -> Optional[subprocess.Popen]:
        """
        Inicia proceso FFmpeg para un perfil específico.
        """
        playlist_path = output_path / "playlist.m3u8"
        segment_pattern = output_path / "segment_%03d.ts"
        
        cmd = [
            self._ffmpeg_path,
            "-hide_banner",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-timeout", "5000000",
            "-i", rtsp_url,
            "-vf", f"scale={profile.width}:{profile.height},fps=15",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "zerolatency",
            "-b:v", profile.video_bitrate,
            "-maxrate", profile.maxrate,
            "-bufsize", profile.bufsize,
            "-g", "30",  # GOP: 2 segundos a 15fps
            "-c:a", "aac",
            "-b:a", profile.audio_bitrate,
            "-ar", "44100",
            "-f", "hls",
            "-hls_time", str(self.SEGMENT_DURATION),
            "-hls_list_size", str(self.MAX_SEGMENTS),
            "-hls_segment_type", "mpegts",
            "-hls_flags", "delete_segments+omit_endlist",  # DVR en vivo
            "-hls_segment_filename", str(segment_pattern),
            str(playlist_path)
        ]
        
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL
            )
            
            # Verificar que no termine inmediatamente
            time.sleep(1)
            if process.poll() is not None:
                stderr = process.stderr.read().decode('utf-8', errors='ignore')
                logger.error(f"FFmpeg terminó inmediatamente para perfil {profile.name}: {stderr[:200]}")
                return None
            
            logger.info(f"FFmpeg iniciado para cámara {camera_id} perfil {profile.name} (PID: {process.pid})")
            return process
            
        except Exception as e:
            logger.error(
                f"Error iniciando FFmpeg para perfil {profile.name}: {e}",
                exc_info=True,
            )
            return None
    
    def _generate_master_playlist(self, camera_id: int):
        """
        Genera master.m3u8 con todos los perfiles para ABR (Adaptive Bitrate).
        El player móvil selecciona automáticamente según ancho de banda.
        """
        stream_info = self._active_streams[camera_id]
        camera_path = self._get_camera_path(camera_id)
        master_path = camera_path / "master.m3u8"
        
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3"
        ]
        
        for profile in HLS_PROFILES:
            if profile.name in stream_info['profiles']:
                playlist_relative = f"{profile.name}/playlist.m3u8"
                bandwidth = int(profile.video_bitrate.replace('k', '000'))
                resolution = f"{profile.width}x{profile.height}"
                
                lines.append(f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={resolution}")
                lines.append(playlist_relative)
        
        master_path.write_text('\n'.join(lines))
        logger.info(f"Master playlist generado para cámara {camera_id}")
    
    def get_manifest(self, camera_id: int) -> Optional[str]:
        """
        Retorna path al master.m3u8 si existe y está activo.
        """
        with self._lock:
            if camera_id not in self._active_streams:
                return None
            
            stream_info = self._active_streams[camera_id]
            master_path = stream_info['master_playlist']
            
            # Verificar que el archivo existe y se está actualizando
            if master_path.exists():
                # Actualizar timestamp de acceso (para cleanup)
                self._active_streams[camera_id]['last_access'] = time.time()
                return str(master_path)
            
            return None
    
    def get_segment(self, camera_id: int, profile_name: str, segment_name: str) -> Optional[str]:
        """
        Retorna path a un segmento específico (.ts).
        Validación de seguridad contra path traversal.
        """
        # FIX F2.1: Validación de path traversal
        if ".." in segment_name or "/" in segment_name or not segment_name.endswith(".ts"):
            logger.warning(f"Intento de path traversal en segmento: {segment_name}")
            return None
        
        if profile_name not in [p.name for p in HLS_PROFILES]:
            return None
        
        with self._lock:
            if camera_id not in self._active_streams:
                return None
            
            segment_path = self._get_profile_path(camera_id, profile_name) / segment_name
            
            if segment_path.exists():
                # Actualizar timestamp de acceso
                self._active_streams[camera_id]['last_access'] = time.time()
                return str(segment_path)
            
            return None
    
    def stop_stream(self, camera_id: int) -> bool:
        """
        Detiene streaming HLS para una cámara y limpia recursos.

        El borrado de archivos sucede DESPUÉS de confirmar que todos los
        procesos FFmpeg murieron (con kill como fallback). Antes se hacía
        rmtree mientras FFmpeg todavía estaba escribiendo segmentos, lo
        que dejaba archivos corruptos o errores intermitentes en el log.
        """
        with self._lock:
            if camera_id not in self._active_streams:
                return False

            logger.info(f"Deteniendo HLS live para cámara {camera_id}")
            stream_info = self._active_streams.pop(camera_id)
            processes = [
                p.get('process') for p in stream_info['profiles'].values()
                if p.get('process')
            ]

        # Fuera del lock: terminar procesos (puede tardar segundos)
        for process in processes:
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    logger.warning(f"FFmpeg HLS cam {camera_id} no respondió a terminate, kill")
                    try:
                        process.kill()
                        process.wait(timeout=2)
                    except Exception as e:
                        logger.error(f"Error matando FFmpeg HLS cam {camera_id}: {e}")
                except Exception as e:
                    logger.error(f"Error deteniendo FFmpeg HLS cam {camera_id}: {e}")

        # Confirmar que ninguno sigue vivo antes de borrar archivos
        for process in processes:
            if process.poll() is None:
                logger.error(
                    f"FFmpeg HLS cam {camera_id} sigue vivo tras kill; "
                    f"NO se borra carpeta para no corromper segmentos en uso"
                )
                return True

        # Ahora sí, borrar archivos
        try:
            camera_path = self._get_camera_path(camera_id)
            if camera_path.exists():
                shutil.rmtree(camera_path, ignore_errors=True)
        except Exception as e:
            logger.error(f"Error limpiando archivos HLS cam {camera_id}: {e}")

        return True
    
    def _cleanup_loop(self):
        """
        Limpia streams que no han sido accedidos en los últimos 60 segundos.
        """
        while not self._shutdown_event.is_set():
            # wait() vuelve True si shutdown_event se setea → salimos al instante.
            if self._shutdown_event.wait(timeout=self.CLEANUP_INTERVAL):
                break

            try:
                # Recolectar ids bajo lock; stop_stream se ejecuta fuera del
                # lock (adentro de stop_stream se vuelve a adquirir) para no
                # bloquear start_stream/get_segment durante terminate() de FFmpeg.
                with self._lock:
                    now = time.time()
                    to_remove = [
                        cam_id for cam_id, info in self._active_streams.items()
                        if now - info['last_access'] > 60
                    ]

                for camera_id in to_remove:
                    logger.info(f"Stream HLS cámara {camera_id} inactivo, limpiando...")
                    self.stop_stream(camera_id)

            except Exception as e:
                logger.error(f"Error en cleanup loop HLS: {e}", exc_info=True)
    
    def get_stats(self) -> dict:
        """Estadísticas del servicio HLS."""
        with self._lock:
            return {
                "active_streams": len(self._active_streams),
                "cameras": list(self._active_streams.keys()),
                "profiles_per_camera": len(HLS_PROFILES)
            }
    
    def shutdown(self):
        """Detiene todos los streams activos."""
        self._shutdown_event.set()

        # Recolectar IDs bajo lock, pero llamar stop_stream fuera (ya gestiona
        # su propio lock y FFmpeg terminate puede tardar segundos).
        with self._lock:
            camera_ids = list(self._active_streams.keys())
        for camera_id in camera_ids:
            self.stop_stream(camera_id)

        # Esperar a que el cleanup_thread salga limpiamente
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=2)

        logger.info("LiveHLSService detenido")


# Instancia global
live_hls_service = LiveHLSService()