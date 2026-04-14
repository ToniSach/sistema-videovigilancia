import subprocess
import threading
import os
import time
import logging
import collections
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np

from backend.app.events.event_manager import EventManager, EventData, event_manager
from backend.app.database.repositories.recording_repository import RecordingRepository
from backend.app.database.repositories.event_repository import EventRepository
from backend.app.database.models import Recording
from backend.app.config import settings


class RecordingManager:
    PRE_BUFFER_SIZE = 150  # ~10 segundos a 15 FPS
    EVENT_RECORDING_DURATION = 10  # segundos post-evento
    CONTINUOUS_SEGMENT_DURATION = 120  # 2 minutos por archivo

    def __init__(self, recording_repo: RecordingRepository, event_repo: Optional[EventRepository] = None):
        self._recording_repo = recording_repo
        self._event_repo = event_repo
        self._recordings_dir = settings.RECORDINGS_PATH
        os.makedirs(self._recordings_dir, exist_ok=True)

        self._pre_buffers: Dict[int, collections.deque] = {}
        self._pre_buffer_lock = threading.Lock()

        self._event_recordings: Dict[int, Dict] = {}
        self._event_recordings_lock = threading.Lock()

        self._continuous_threads: Dict[int, threading.Thread] = {}
        self._continuous_stop_events: Dict[int, threading.Event] = {}  # FIX: Event thread-safe
        self._continuous_lock = threading.Lock()
        
        # Cache de dimensiones por cámara
        self._frame_dimensions: Dict[int, Tuple[int, int]] = {}

        # Suscripción a eventos de detección
        event_manager.subscribe("motion", self._on_event)
        event_manager.subscribe("person", self._on_event)
        event_manager.subscribe("vehicle", self._on_event)

        logging.info("RecordingManager inicializado")

    def register_camera_buffer(self, camera_id: int, frame_distributor) -> None:
        """Registra buffer circular pre-evento para una cámara."""
        with self._pre_buffer_lock:
            self._pre_buffers[camera_id] = collections.deque(maxlen=self.PRE_BUFFER_SIZE)

        consumer_name = f"recording_prebuffer_{camera_id}"
        frame_distributor.register_consumer(
            consumer_name,
            lambda fd: self._update_pre_buffer(camera_id, fd),
            needs_copy=True
        )
        logging.info(f"Buffer pre-evento registrado para cámara {camera_id}")

    def _update_pre_buffer(self, camera_id: int, frame_data) -> None:
        """Callback que alimenta el pre-buffer circular."""
        with self._pre_buffer_lock:
            if camera_id in self._pre_buffers:
                self._pre_buffers[camera_id].append(
                    (frame_data.frame.copy(), frame_data.timestamp)
                )
                # Actualizar dimensiones conocidas
                h, w = frame_data.frame.shape[:2]
                if h > 0 and w > 0:
                    self._frame_dimensions[camera_id] = (h, w)

    def _on_event(self, event_data: EventData) -> None:
        """Handler de eventos que inicia grabación de clip."""
        camera_id = event_data.camera_id

        with self._event_recordings_lock:
            if camera_id in self._event_recordings:
                logging.debug(f"Ya existe grabación de evento activa para cámara {camera_id}")
                return

        thread = threading.Thread(
            target=self._record_event_clip,
            args=(camera_id, event_data),
            daemon=True,
            name=f"EventRecorder-{camera_id}"
        )
        thread.start()

        with self._event_recordings_lock:
            self._event_recordings[camera_id] = {
                "thread": thread,
                "start_time": time.time(),
                "event_type": event_data.event_type
            }

    def _get_frame_dimensions(self, camera_id: int) -> Tuple[int, int]:
        """Obtiene dimensiones de frame para FFmpeg."""
        if camera_id in self._frame_dimensions:
            return self._frame_dimensions[camera_id]
        return (720, 1280)

    def _record_event_clip(self, camera_id: int, event_data: EventData) -> None:
        """
        Grabación de evento con pre-buffer incluido.
        Patrón: Streaming directo a FFmpeg sin acumular en RAM.
        """
        process = None
        try:
            logging.info(f"Iniciando grabación de evento {event_data.event_type} para cámara {camera_id}")
            
            cam_dir = os.path.join(self._recordings_dir, str(camera_id), "events")
            os.makedirs(cam_dir, exist_ok=True)
            
            timestamp_str = datetime.fromtimestamp(event_data.timestamp).strftime("%Y%m%d_%H%M%S")
            filename = f"event_{event_data.event_type}_{timestamp_str}.mp4"
            output_path = os.path.join(cam_dir, filename)
            
            height, width = self._get_frame_dimensions(camera_id)
            if height == 0 or width == 0:
                logging.error(f"No se pudieron determinar dimensiones para cámara {camera_id}")
                return
            
            cmd = [
                "ffmpeg", "-y",
                "-f", "rawvideo",
                "-vcodec", "rawvideo",
                "-s", f"{width}x{height}",
                "-pix_fmt", "bgr24",
                "-r", "15",
                "-i", "pipe:0",
                "-vcodec", "libx264",
                "-pix_fmt", "yuv420p",
                "-preset", "ultrafast",
                "-crf", "28",
                output_path
            ]
            
            process = subprocess.Popen(
                cmd, 
                stdin=subprocess.PIPE, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE
            )
            
            # 1. Escribir pre-buffer
            pre_frames = []
            with self._pre_buffer_lock:
                if camera_id in self._pre_buffers:
                    pre_frames = list(self._pre_buffers[camera_id])
            
            frames_written = 0
            for frame, _ in pre_frames:
                try:
                    process.stdin.write(frame.tobytes())
                    frames_written += 1
                except (BrokenPipeError, OSError):
                    break

            # 2. Escribir frames en tiempo real
            start_time = time.time()
            last_buffer_idx = len(pre_frames)
            
            while time.time() - start_time < self.EVENT_RECORDING_DURATION:
                try:
                    with self._pre_buffer_lock:
                        if camera_id in self._pre_buffers:
                            current_buffer = list(self._pre_buffers[camera_id])
                            if len(current_buffer) > last_buffer_idx:
                                new_frames = current_buffer[last_buffer_idx:]
                                for frame, _ in new_frames:
                                    process.stdin.write(frame.tobytes())
                                    frames_written += 1
                                last_buffer_idx = len(current_buffer)
                    time.sleep(0.066)
                except (BrokenPipeError, OSError):
                    break
            
            # 3. Cerrar FFmpeg
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            
            # 4. Registrar en BD
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                file_size = os.path.getsize(output_path)
                duration = self.EVENT_RECORDING_DURATION + (self.PRE_BUFFER_SIZE / 15)
                
                recording = Recording(
                    camera_id=camera_id,
                    start_time=datetime.fromtimestamp(event_data.timestamp - (self.PRE_BUFFER_SIZE / 15)),
                    end_time=datetime.utcnow(),
                    file_path=output_path,
                    file_size_bytes=file_size,
                    duration_seconds=duration
                )
                self._recording_repo.create(recording)
                logging.info(f"✅ Clip guardado: {output_path} ({file_size} bytes)")
        
        except Exception as e:
            logging.error(f"Error en grabación de evento: {e}", exc_info=True)
        finally:
            if process:
                try:
                    if process.stdin:
                        process.stdin.close()
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=2)
                except:
                    pass
            with self._event_recordings_lock:
                if camera_id in self._event_recordings:
                    del self._event_recordings[camera_id]

    def start_continuous_recording(self, camera_id: int) -> bool:
        """Inicia grabación continua cíclica."""
        with self._continuous_lock:
            if camera_id in self._continuous_threads and self._continuous_threads[camera_id].is_alive():
                logging.warning(f"Ya existe grabación continua para cámara {camera_id}")
                return False

            stop_event = threading.Event()
            self._continuous_stop_events[camera_id] = stop_event
            
            thread = threading.Thread(
                target=self._continuous_loop,
                args=(camera_id, stop_event),
                daemon=True,
                name=f"ContinuousRecorder-{camera_id}"
            )
            self._continuous_threads[camera_id] = thread
            thread.start()
            logging.info(f"✅ Grabación continua iniciada para cámara {camera_id}")
            return True

    def _continuous_loop(self, camera_id: int, stop_event: threading.Event) -> None:
        """
        FIX CRÍTICO: Streaming directo sin acumular frames en RAM.
        Cada frame se escribe inmediatamente al stdin de FFmpeg.
        """
        segment_count = 0
        
        while not stop_event.is_set():
            process = None
            segment_start = datetime.utcnow()
            
            try:
                cam_dir = os.path.join(self._recordings_dir, str(camera_id), "continuous")
                os.makedirs(cam_dir, exist_ok=True)
                
                filename = f"{camera_id}_{segment_start.strftime('%Y%m%d_%H%M%S')}.mp4"
                output_path = os.path.join(cam_dir, filename)
                
                height, width = self._get_frame_dimensions(camera_id)
                
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "rawvideo",
                    "-vcodec", "rawvideo",
                    "-s", f"{width}x{height}",
                    "-pix_fmt", "bgr24",
                    "-r", "15",
                    "-i", "pipe:0",
                    "-vcodec", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-preset", "ultrafast",
                    "-crf", "28",
                    "-movflags", "+faststart",
                    output_path
                ]
                
                process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                segment_frames = 0
                segment_start_time = time.time()
                
                # FIX: Escribir frames directamente, sin almacenar en lista
                while (time.time() - segment_start_time) < self.CONTINUOUS_SEGMENT_DURATION:
                    if stop_event.is_set():
                        break
                    
                    try:
                        with self._pre_buffer_lock:
                            if camera_id in self._pre_buffers and self._pre_buffers[camera_id]:
                                last_frame, _ = self._pre_buffers[camera_id][-1]
                                process.stdin.write(last_frame.tobytes())
                                segment_frames += 1
                        
                        time.sleep(0.066)  # 15 FPS
                        
                    except (BrokenPipeError, OSError):
                        break
                    except Exception as e:
                        logging.error(f"Error escribiendo frame continuo: {e}")
                        break
                
                # Cerrar segmento
                if process:
                    try:
                        process.stdin.close()
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                
                # Registrar en BD
                if os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
                    file_size = os.path.getsize(output_path)
                    actual_duration = segment_frames / 15.0
                    
                    recording = Recording(
                        camera_id=camera_id,
                        start_time=segment_start,
                        end_time=datetime.utcnow(),
                        file_path=output_path,
                        file_size_bytes=file_size,
                        duration_seconds=actual_duration
                    )
                    self._recording_repo.create(recording)
                    segment_count += 1
                        
            except Exception as e:
                logging.error(f"Error en segmento continuo cámara {camera_id}: {e}")
                time.sleep(1)
        
        logging.info(f"⏹️ Grabación continua finalizada cámara {camera_id} ({segment_count} segmentos)")

    def stop_continuous_recording(self, camera_id: int) -> bool:
        """Detiene grabación continua de forma segura."""
        with self._continuous_lock:
            if camera_id not in self._continuous_threads:
                return False
            
            if camera_id in self._continuous_stop_events:
                self._continuous_stop_events[camera_id].set()
            
            thread = self._continuous_threads[camera_id]
            if thread.is_alive():
                thread.join(timeout=5)
            
            del self._continuous_threads[camera_id]
            if camera_id in self._continuous_stop_events:
                del self._continuous_stop_events[camera_id]
            
            logging.info(f"⏹️ Grabación continua detenida para cámara {camera_id}")
            return True

    def is_recording_continuous(self, camera_id: int) -> bool:
        """Verifica si hay grabación continua activa."""
        with self._continuous_lock:
            if camera_id not in self._continuous_threads:
                return False
            return self._continuous_threads[camera_id].is_alive()