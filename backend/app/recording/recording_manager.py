import threading
import subprocess
import os
import time
import logging
import collections
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np

from ..events.event_manager import EventManager, EventData, event_manager
from ..database.repositories.recording_repository import RecordingRepository
from ..database.repositories.event_repository import EventRepository
from ..database.models import Recording
from ..config import settings


class RecordingManager:
    PRE_BUFFER_SIZE = 150  # ~5 segundos a 30fps
    EVENT_RECORDING_DURATION = 10  # segundos post-evento
    CONTINUOUS_SEGMENT_DURATION = 120  # segundos (2 minutos)

    def __init__(self, recording_repo: RecordingRepository, event_repo: Optional[EventRepository] = None):
        self._recording_repo = recording_repo
        self._event_repo = event_repo
        self._recordings_dir = settings.RECORDINGS_PATH
        os.makedirs(self._recordings_dir, exist_ok=True)

        # Pre-event buffers: camera_id → deque de (frame np.ndarray, timestamp float)
        self._pre_buffers: Dict[int, collections.deque] = {}
        self._pre_buffer_lock = threading.Lock()

        # Estado de grabaciones de evento activas
        self._event_recordings: Dict[int, Dict] = {}
        self._event_recordings_lock = threading.Lock()

        # Grabaciones continuas activas
        self._continuous_threads: Dict[int, threading.Thread] = {}
        self._continuous_running: Dict[int, bool] = {}
        self._continuous_lock = threading.Lock()

        # Suscribirse a eventos
        event_manager.subscribe("motion", self._on_event)
        event_manager.subscribe("person", self._on_event)
        event_manager.subscribe("vehicle", self._on_event)

        logging.info("RecordingManager inicializado")

    def register_camera_buffer(self, camera_id: int, frame_distributor) -> None:
        with self._pre_buffer_lock:
            self._pre_buffers[camera_id] = collections.deque(maxlen=self.PRE_BUFFER_SIZE)

        consumer_name = f"recording_prebuffer_{camera_id}"
        frame_distributor.register_consumer(
            consumer_name,
            lambda fd: self._update_pre_buffer(camera_id, fd)
        )
        logging.info(f"Buffer pre-evento registrado para cámara {camera_id}")

    def _update_pre_buffer(self, camera_id: int, frame_data) -> None:
        with self._pre_buffer_lock:
            if camera_id in self._pre_buffers:
                self._pre_buffers[camera_id].append(
                    (frame_data.frame.copy(), frame_data.timestamp)
                )

    def _on_event(self, event_data: EventData) -> None:
        camera_id = event_data.camera_id

        with self._event_recordings_lock:
            if camera_id in self._event_recordings:
                logging.debug(f"Ya existe grabación activa para cámara {camera_id}")
                return

        thread = threading.Thread(
            target=self._record_event_clip,
            args=(camera_id, event_data),
            daemon=True
        )
        thread.start()

        with self._event_recordings_lock:
            self._event_recordings[camera_id] = {
                "thread": thread,
                "start_time": time.time(),
                "event_type": event_data.event_type
            }

    def _record_event_clip(self, camera_id: int, event_data: EventData) -> None:
        try:
            logging.info(f"Iniciando grabación de evento {event_data.event_type} para cámara {camera_id}")

            with self._pre_buffer_lock:
                if camera_id not in self._pre_buffers:
                    pre_frames = []
                else:
                    pre_frames = list(self._pre_buffers[camera_id])

            all_frames = [f[0] for f in pre_frames]
            start_time = time.time()
            end_time = start_time + self.EVENT_RECORDING_DURATION

            post_frames = []
            last_buffer_size = len(pre_frames)

            while time.time() < end_time:
                with self._pre_buffer_lock:
                    if camera_id in self._pre_buffers:
                        current_buffer = list(self._pre_buffers[camera_id])
                        if len(current_buffer) > last_buffer_size:
                            new_frames = current_buffer[last_buffer_size:]
                            post_frames.extend([f[0] for f in new_frames])
                            last_buffer_size = len(current_buffer)

                time.sleep(0.033)

            all_frames.extend(post_frames)

            if not all_frames:
                logging.warning(f"No hay frames para grabar en cámara {camera_id}")
                return

            cam_dir = os.path.join(self._recordings_dir, str(camera_id), "events")
            os.makedirs(cam_dir, exist_ok=True)

            timestamp_str = datetime.fromtimestamp(event_data.timestamp).strftime("%Y%m%d_%H%M%S")
            filename = f"event_{event_data.event_type}_{timestamp_str}.mp4"
            output_path = os.path.join(cam_dir, filename)

            success = self._frames_to_mp4(all_frames, output_path, fps=15)

            if success and os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                duration = len(all_frames) / 15.0

                recording = Recording(
                    camera_id=camera_id,
                    start_time=datetime.fromtimestamp(event_data.timestamp - 5),
                    end_time=datetime.now(),
                    file_path=output_path,
                    file_size_bytes=file_size,
                    duration_seconds=duration
                )
                saved = self._recording_repo.create(recording)

                logging.info(f"Clip de evento guardado: {output_path} ({file_size} bytes)")

                if self._event_repo:
                    recent_events = self._event_repo.get_by_camera(camera_id, limit=1)
                    if recent_events:
                        recent_events[0].clip_path = output_path
            else:
                logging.error(f"Fallo al crear clip MP4: {output_path}")

        except Exception as e:
            logging.error(f"Error en grabación de evento: {e}", exc_info=True)
        finally:
            with self._event_recordings_lock:
                if camera_id in self._event_recordings:
                    del self._event_recordings[camera_id]

    def _frames_to_mp4(self, frames: List[np.ndarray], output_path: str, fps: int = 15) -> bool:
        if not frames:
            return False

        try:
            height, width = frames[0].shape[:2]

            cmd = [
                "ffmpeg", "-y",
                "-f", "rawvideo",
                "-vcodec", "rawvideo",
                "-s", f"{width}x{height}",
                "-pix_fmt", "bgr24",
                "-r", str(fps),
                "-i", "pipe:0",
                "-vcodec", "libx264",
                "-pix_fmt", "yuv420p",
                "-preset", "ultrafast",
                output_path
            ]

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            for frame in frames:
                process.stdin.write(frame.tobytes())

            process.stdin.close()
            process.wait()

            return process.returncode == 0 and os.path.exists(output_path)

        except Exception as e:
            logging.error(f"Error en FFmpeg: {e}")
            return False

    def start_continuous_recording(self, camera_id: int) -> None:
        with self._continuous_lock:
            if camera_id in self._continuous_threads and self._continuous_threads[camera_id].is_alive():
                logging.warning(f"Ya existe grabación continua para cámara {camera_id}")
                return

            self._continuous_running[camera_id] = True
            thread = threading.Thread(
                target=self._continuous_loop,
                args=(camera_id,),
                daemon=True
            )
            self._continuous_threads[camera_id] = thread
            thread.start()
            logging.info(f"Grabación continua iniciada para cámara {camera_id}")

    def _continuous_loop(self, camera_id: int) -> None:
        while self._continuous_running.get(camera_id, False):
            try:
                start_time = datetime.utcnow()
                cam_dir = os.path.join(self._recordings_dir, str(camera_id), "continuous")
                os.makedirs(cam_dir, exist_ok=True)

                filename = f"{camera_id}_{start_time.strftime('%Y%m%d_%H%M%S')}.mp4"
                output_path = os.path.join(cam_dir, filename)

                segment_frames = []
                segment_start = time.time()

                while (time.time() - segment_start) < self.CONTINUOUS_SEGMENT_DURATION:
                    if not self._continuous_running.get(camera_id, False):
                        break

                    with self._pre_buffer_lock:
                        if camera_id in self._pre_buffers:
                            if self._pre_buffers[camera_id]:
                                last_frame = self._pre_buffers[camera_id][-1][0]
                                segment_frames.append(last_frame.copy())

                    time.sleep(0.033)

                if segment_frames:
                    success = self._frames_to_mp4(segment_frames, output_path, fps=15)

                    if success and os.path.exists(output_path):
                        file_size = os.path.getsize(output_path)
                        duration = len(segment_frames) / 15.0

                        recording = Recording(
                            camera_id=camera_id,
                            start_time=start_time,
                            end_time=datetime.utcnow(),
                            file_path=output_path,
                            file_size_bytes=file_size,
                            duration_seconds=duration
                        )
                        self._recording_repo.create(recording)
                        logging.info(f"Segmento continuo guardado: {filename}")

            except Exception as e:
                logging.error(f"Error en grabación continua cámara {camera_id}: {e}")
                time.sleep(1)

    def stop_continuous_recording(self, camera_id: int) -> None:
        with self._continuous_lock:
            self._continuous_running[camera_id] = False
            logging.info(f"Grabación continua detenida para cámara {camera_id}")

    def is_recording_continuous(self, camera_id: int) -> bool:
        return self._continuous_running.get(camera_id, False)
