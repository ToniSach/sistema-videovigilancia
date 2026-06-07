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
    # PRE_BUFFER_SIZE: solo se usa cuando NO hay grabación continua activa
    # (fallback). Con continuous activo, los 10s pre se extraen del .mp4
    # de continuous con `ffmpeg -c copy` (sin RAM, sin re-encode).
    # 75 frames ≈ 5s a 15fps; suficiente como fallback de emergencia.
    PRE_BUFFER_SIZE = 75
    EVENT_RECORDING_DURATION = 10  # segundos post-evento
    CONTINUOUS_SEGMENT_DURATION = 120  # 2 minutos por archivo
    # Pre-context para el SPLICE (usado solo si hay continuous activo):
    # cuánto pre-evento extraer del archivo continuous.
    SPLICE_PRE_SECONDS = 10
    SPLICE_POST_SECONDS = 10  # = EVENT_RECORDING_DURATION para consistencia

    @property
    def _recordings_dir(self) -> str:
        """Carpeta de grabaciones, leída EN CALIENTE desde settings: si el admin
        cambia la ruta desde la app de escritorio, las grabaciones NUEVAS van a
        la ruta nueva sin reiniciar el backend."""
        return settings.RECORDINGS_PATH

    def __init__(self, recording_repo: RecordingRepository, event_repo: Optional[EventRepository] = None):
        self._recording_repo = recording_repo
        self._event_repo = event_repo
        os.makedirs(settings.RECORDINGS_PATH, exist_ok=True)

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
        # needs_copy=False: _update_pre_buffer ya hace .copy() internamente
        # antes de meter el frame en la deque (necesario porque retiene la
        # referencia mucho tiempo). Tener needs_copy=True aquí causaba
        # DOBLE memcpy por frame (~5.5 MB en dual-lens). En cámara a 15 fps
        # eso eran 82 MB/s de memcpy desperdiciado en ancho de banda de memoria.
        frame_distributor.register_consumer(
            consumer_name,
            lambda fd: self._update_pre_buffer(camera_id, fd),
            needs_copy=False
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

        # Cooldown reentrante: si ya hay grabación de evento en curso para
        # esta cámara, no arrancar otra (el lock protege el dict).
        with self._event_recordings_lock:
            if camera_id in self._event_recordings:
                logging.debug(f"Ya existe grabación de evento activa para cámara {camera_id}")
                return
            self._event_recordings[camera_id] = {
                "start_time": time.time(),
                "event_type": event_data.event_type,
            }

        # Estrategia: si la cámara tiene grabación CONTINUA activa, extraemos
        # los 10s pre + 10s post del archivo continuous con `ffmpeg -c copy`
        # (sin re-encodear → casi instantáneo, ~200ms vs ~2s del método
        # antiguo de re-encoding desde pre-buffer RAM). Si no hay continuous,
        # fallback al método antiguo usando pre-buffer en RAM.
        from backend.app.core.executor import global_executor
        # Clips de evento SIEMPRE por SPLICE de la grabación continua (-c copy):
        # sin re-encode y sin pre-buffer en RAM. El fallback antiguo por pre-buffer
        # (_record_event_clip) queda en desuso; la optimización exige continua activa.
        if not self.is_recording_continuous(camera_id):
            logging.warning(
                f"Cámara {camera_id}: evento sin grabación continua activa → no se "
                f"puede generar clip por splice. Activa la grabación continua "
                f"(AUTO_START_RECORDING). Clip omitido."
            )
            with self._event_recordings_lock:
                self._event_recordings.pop(camera_id, None)
            return

        future = global_executor.submit(self._record_event_splice, camera_id, event_data)
        if future is None:
            # Executor lleno → liberar slot porque el clip no se va a grabar
            with self._event_recordings_lock:
                self._event_recordings.pop(camera_id, None)
            logging.warning(
                f"Executor saturado, grabación de evento descartada cam {camera_id}"
            )

    def _get_frame_dimensions(self, camera_id: int) -> Tuple[int, int]:
        """Obtiene dimensiones de frame para FFmpeg."""
        if camera_id in self._frame_dimensions:
            return self._frame_dimensions[camera_id]
        return (720, 1280)

    def _probe_duration(self, mp4_path: str) -> float:
        """Devuelve duración real del MP4 en segundos usando ffprobe. 0.0 si falla."""
        import shutil as _shutil
        ffprobe = _shutil.which("ffprobe")
        if not ffprobe:
            return 0.0
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", mp4_path],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return float(result.stdout.strip() or 0.0)
        except Exception:
            pass
        return 0.0

    def _remux_fragmented_to_regular(self, frag_path: str) -> str:
        """
        Convierte fMP4 (frag_keyframe+empty_moov) a MP4 regular con faststart.
        Devuelve el path del archivo final (sobrescribe el original).

        Se usa al CERRAR un segmento continuo:
          - Durante la escritura, el formato fMP4 permite que el SPLICE lea
            el archivo mientras crece (moov al inicio + moof por fragmento).
          - Al cerrar, lo re-muxeamos a MP4 estándar para que la reproducción
            en el cliente (VLC/QMediaPlayer) y los reproductores externos
            (Telegram preview) funcionen sin problemas de seek.

        Usa `-c copy` → NO recodifica, solo reorganiza átomos. Tarda
        ~100-300ms para un segmento de 2 min @ 15fps.

        Si re-mux falla, devuelve el path original (mejor algo que nada).
        """
        import shutil as _shutil
        ffmpeg = _shutil.which("ffmpeg")
        if not ffmpeg:
            return frag_path

        tmp_path = frag_path + ".remux.mp4"
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", frag_path,
            "-c", "copy",
            "-movflags", "+faststart",
            tmp_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode != 0 or not os.path.exists(tmp_path) \
                    or os.path.getsize(tmp_path) < 1024:
                logging.warning(
                    f"[REMUX] ffmpeg falló, dejando fMP4 original: "
                    f"{result.stderr.decode('utf-8', errors='ignore')[:200]}"
                )
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                return frag_path

            # Reemplazar original con el re-muxeado
            try:
                os.replace(tmp_path, frag_path)
                logging.debug(f"[REMUX] {os.path.basename(frag_path)} fMP4 → MP4 regular")
            except Exception as e:
                logging.warning(f"[REMUX] no se pudo reemplazar: {e}")
                return tmp_path
            return frag_path
        except subprocess.TimeoutExpired:
            logging.warning(f"[REMUX] timeout en {frag_path}")
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return frag_path
        except Exception as e:
            logging.warning(f"[REMUX] error: {e}")
            return frag_path

    # ==================================================================
    # NUEVO: Grabación de evento por SPLICE del archivo continuous
    # ==================================================================
    #
    # Antes (método _record_event_clip):
    #   1. Tomamos 150 frames del pre-buffer EN RAM (466MB por cám dual).
    #   2. Lanzamos ffmpeg libx264 que re-encodea desde raw BGR24.
    #   3. Esperamos 10s post grabando frames adicionales.
    #   4. Cerramos ffmpeg → MP4 final.
    #   Coste: ~2-3s de encoding + 466MB RAM constantes.
    #
    # Ahora (método _record_event_splice):
    #   1. La cámara ya graba continuamente segmentos de 2 min en
    #      recordings/<cam>/continuous/<cam>_<ts>.mp4.
    #   2. Esperamos los 10s post del evento (inevitable).
    #   3. Buscamos el/los archivo(s) continuous que cubren
    #      [event_ts - 10, event_ts + 10].
    #   4. ffmpeg -ss N -t M -c copy extrae sin re-encodear (~200ms).
    #   5. Si cruza boundary: ffmpeg concat de 2 segmentos.
    #   Coste: ~200ms de splice + 0 RAM extra.
    #
    # Ventajas: -2.5s en delay total de video, libera 466MB RAM por cám dual,
    # menor uso de CPU (sin re-encoding).

    def _record_event_splice(self, camera_id: int, event_data: EventData) -> None:
        """
        Extrae los 20s del evento (10 pre + 10 post) del archivo continuous
        usando `ffmpeg -c copy` (sin re-encodear).
        """
        try:
            event_ts = event_data.timestamp
            pre_dur = self.SPLICE_PRE_SECONDS
            post_dur = self.SPLICE_POST_SECONDS
            pre_start_ts = event_ts - pre_dur
            post_end_ts = event_ts + post_dur

            logging.info(
                f"[SPLICE] Evento {event_data.event_type} cam {camera_id}: "
                f"esperando {post_dur:.0f}s post-evento…"
            )

            # 1. Esperar los 10s post-evento (inevitable)
            wait_until = post_end_ts + 0.5  # +0.5s margen para que ffmpeg flushee
            while True:
                now = time.time()
                if now >= wait_until:
                    break
                time.sleep(min(0.5, wait_until - now))

            # 2. Buscar segmentos continuous que cubren [pre_start_ts, post_end_ts]
            segments = self._find_continuous_segments(camera_id, pre_start_ts, post_end_ts)
            if not segments:
                logging.warning(
                    f"[SPLICE] cam {camera_id}: no se encontraron segmentos "
                    f"continuous para [{pre_start_ts}, {post_end_ts}]. "
                    f"Cayendo al método antiguo con pre-buffer."
                )
                self._record_event_clip(camera_id, event_data)
                return

            # 3. Preparar output path
            cam_dir = os.path.join(self._recordings_dir, str(camera_id), "events")
            os.makedirs(cam_dir, exist_ok=True)
            timestamp_str = datetime.fromtimestamp(event_ts).strftime("%Y%m%d_%H%M%S")
            filename = f"event_{event_data.event_type}_{timestamp_str}.mp4"
            output_path = os.path.join(cam_dir, filename)

            # 4. Splice según número de segmentos
            ok = False
            if len(segments) == 1:
                ok = self._splice_single(
                    segments[0], pre_start_ts, post_end_ts, output_path
                )
            else:
                ok = self._splice_concat(
                    segments, pre_start_ts, post_end_ts, output_path
                )

            if not ok or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                logging.warning(
                    f"[SPLICE] cam {camera_id}: splice falló, cayendo al método antiguo"
                )
                self._record_event_clip(camera_id, event_data)
                return

            file_size = os.path.getsize(output_path)
            duration = post_end_ts - pre_start_ts
            recording = Recording(
                camera_id=camera_id,
                start_time=datetime.fromtimestamp(pre_start_ts),
                end_time=datetime.fromtimestamp(post_end_ts),
                file_path=output_path,
                file_size_bytes=file_size,
                duration_seconds=duration,
            )
            self._recording_repo.create(recording)
            logging.info(
                f"✅ [SPLICE] Clip extraído: {output_path} "
                f"({file_size} bytes, {duration:.0f}s, "
                f"{len(segments)} segmento(s))"
            )

            # 5. Enviar a Telegram
            self._send_clip_to_telegram(camera_id, output_path, event_data)

        except Exception as e:
            logging.error(f"Error en _record_event_splice: {e}", exc_info=True)
        finally:
            with self._event_recordings_lock:
                if camera_id in self._event_recordings:
                    del self._event_recordings[camera_id]

    def _find_continuous_segments(self, camera_id: int,
                                    start_ts: float, end_ts: float) -> list:
        """
        Devuelve lista de tuplas (file_path, segment_start_ts, segment_end_ts)
        de los archivos continuous que se solapan con [start_ts, end_ts],
        ordenados por tiempo. Consulta directamente al filesystem (más rápido
        que BD para esta operación corta).

        Patrón de nombre: <cam>_<YYYYMMDD>_<HHMMSS>.mp4
        El timestamp del nombre es start_time del segmento. Como cada segmento
        dura CONTINUOUS_SEGMENT_DURATION (120s), end_ts = start_ts + 120.
        """
        cont_dir = os.path.join(self._recordings_dir, str(camera_id), "continuous")
        if not os.path.isdir(cont_dir):
            return []

        # CRÍTICO: los nombres se generan con datetime.utcnow().strftime(...)
        # (UTC). Si los parseamos con datetime.strptime().timestamp(), Python
        # interpreta el datetime como LOCAL → desfase de zona horaria → ningún
        # segmento coincide con el rango del evento (que es UTC).
        # Solución: parsear como UTC explícito.
        from datetime import timezone
        results = []
        for fname in sorted(os.listdir(cont_dir)):
            if not fname.endswith(".mp4"):
                continue
            # Patrón: <cam>_<YYYYMMDD>_<HHMMSS>.mp4
            parts = fname.replace(".mp4", "").split("_")
            if len(parts) < 3:
                continue
            try:
                dt_utc = datetime.strptime(
                    f"{parts[-2]}_{parts[-1]}", "%Y%m%d_%H%M%S"
                ).replace(tzinfo=timezone.utc)
                seg_start = dt_utc.timestamp()  # POSIX timestamp UTC correcto
            except Exception:
                continue
            seg_end = seg_start + self.CONTINUOUS_SEGMENT_DURATION
            # ¿se solapa con [start_ts, end_ts]?
            if seg_end < start_ts or seg_start > end_ts:
                continue
            results.append((os.path.join(cont_dir, fname), seg_start, seg_end))

        return results

    def _splice_single(self, segment: tuple, start_ts: float, end_ts: float,
                        output_path: str) -> bool:
        """Extrae [start_ts, end_ts] de UN segmento con `ffmpeg -c copy`."""
        seg_path, seg_start, seg_end = segment
        # Offset dentro del segmento donde empezar
        offset = max(0.0, start_ts - seg_start)
        # Duración pedida (cap al final del segmento si rebasa)
        duration = min(end_ts, seg_end) - max(start_ts, seg_start)
        if duration <= 0:
            return False

        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{offset:.3f}",     # antes del -i = seek rápido
            "-i", seg_path,
            "-t", f"{duration:.3f}",
            "-c", "copy",                # sin re-encoding
            "-movflags", "+faststart",
            output_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=15)
            if result.returncode != 0:
                logging.error(
                    f"[SPLICE single] ffmpeg falló: "
                    f"{result.stderr.decode('utf-8', errors='ignore')[:300]}"
                )
                return False
            return True
        except Exception as e:
            logging.error(f"[SPLICE single] excepción: {e}")
            return False

    def _splice_concat(self, segments: list, start_ts: float, end_ts: float,
                        output_path: str) -> bool:
        """
        El evento cruza un boundary de segmento. Extraemos la cola del
        primer segmento + segmentos intermedios + cabeza del último,
        luego concatenamos con el demuxer concat de ffmpeg.
        """
        import tempfile
        tmp_files = []
        try:
            for i, (seg_path, seg_start, seg_end) in enumerate(segments):
                offset = max(0.0, start_ts - seg_start)
                duration = min(end_ts, seg_end) - max(start_ts, seg_start)
                if duration <= 0:
                    continue
                tmp = tempfile.NamedTemporaryFile(
                    suffix=f"_part{i}.mp4", delete=False
                )
                tmp.close()
                tmp_files.append(tmp.name)
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{offset:.3f}",
                    "-i", seg_path,
                    "-t", f"{duration:.3f}",
                    "-c", "copy",
                    tmp.name,
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=15)
                if result.returncode != 0:
                    logging.error(
                        f"[SPLICE concat] parte {i} falló: "
                        f"{result.stderr.decode('utf-8', errors='ignore')[:300]}"
                    )
                    return False

            if not tmp_files:
                return False

            # Concat demuxer: necesita un fichero de lista
            list_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            )
            try:
                for tf in tmp_files:
                    list_file.write(f"file '{tf}'\n")
                list_file.close()
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0",
                    "-i", list_file.name,
                    "-c", "copy",
                    "-movflags", "+faststart",
                    output_path,
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=20)
                if result.returncode != 0:
                    logging.error(
                        f"[SPLICE concat] join falló: "
                        f"{result.stderr.decode('utf-8', errors='ignore')[:300]}"
                    )
                    return False
                return True
            finally:
                try:
                    os.remove(list_file.name)
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"[SPLICE concat] excepción: {e}")
            return False
        finally:
            for tf in tmp_files:
                try:
                    os.remove(tf)
                except Exception:
                    pass

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
            
            # Comando crítico para que el MP4 tenga duración correcta:
            #   -framerate 15 antes del -i  → mete PTS válidos al leer el raw
            #   -fps_mode cfr               → constant frame rate (reemplaza
            #                                 al deprecated -vsync cfr en
            #                                 ffmpeg 6+)
            #   -movflags +faststart        → moov al inicio del MP4
            #   -an                          → sin audio (input es raw video)
            #
            # En ffmpeg 8 (que es lo que tienes), el "-vsync cfr" produce
            # warning y NO siempre se aplica. Eso causaba MP4 con duration=0.
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-f", "rawvideo",
                "-vcodec", "rawvideo",
                "-s", f"{width}x{height}",
                "-pix_fmt", "bgr24",
                "-framerate", "15",            # framerate de INPUT (genera PTS)
                "-i", "pipe:0",
                "-an",                         # sin audio
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-preset", "ultrafast",
                "-crf", "28",
                "-fps_mode", "cfr",            # CFR moderno
                "-r", "15",                    # fps SALIDA explícito
                "-movflags", "+faststart",
                output_path
            ]

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # 1. Snapshot del pre-buffer: UNA copia bajo lock, luego suelto.
            # Antes se mantenía el lock durante todo el write a FFmpeg, lo
            # que bloqueaba al productor (frame_distributor) y causaba drops
            # de frames en el liveview cada vez que había un evento.
            with self._pre_buffer_lock:
                pre_frames = (
                    list(self._pre_buffers[camera_id])
                    if camera_id in self._pre_buffers
                    else []
                )
            last_ts = pre_frames[-1][1] if pre_frames else 0.0

            frames_written = 0
            for frame, _ in pre_frames:
                try:
                    process.stdin.write(frame.tobytes())
                    frames_written += 1
                except (BrokenPipeError, OSError):
                    break

            # 2. Escribir frames en tiempo real a 15 fps CFR. Misma lógica
            # que el continuous loop: tomar el latest frame del pre-buffer
            # cada 66 ms y escribirlo (con duplicación si la cámara va
            # lenta). Esto garantiza que la duración del MP4 coincida
            # con la duración wall clock pedida (10 s post-evento).
            #
            # Antes filtrábamos por timestamp `> last_ts` y solo escribíamos
            # frames "nuevos", produciendo videos más cortos que el wall
            # clock cuando la cámara entrega <15 fps reales → Telegram
            # mostraba "0 s" porque la metadata MP4 no alineaba con la
            # cantidad de frames.
            start_time = time.time()

            while time.time() - start_time < self.EVENT_RECORDING_DURATION:
                last_frame = None
                with self._pre_buffer_lock:
                    if camera_id in self._pre_buffers and self._pre_buffers[camera_id]:
                        last_frame, _ = self._pre_buffers[camera_id][-1]

                if last_frame is not None:
                    try:
                        process.stdin.write(last_frame.tobytes())
                        frames_written += 1
                    except (BrokenPipeError, OSError):
                        break
                    except Exception as e:
                        logging.error(f"Error escribiendo frame evento: {e}")
                        break

                time.sleep(0.066)  # 15 fps consistentes
            
            # 3. Cerrar FFmpeg
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

            # Si ffmpeg salió con error, loguear stderr para diagnóstico.
            # Antes el stderr se descartaba en silencio: si el MP4 salía
            # con duration=0 no había forma de saber por qué.
            if process.returncode != 0 and process.stderr:
                try:
                    err_output = process.stderr.read().decode("utf-8", errors="ignore")
                    if err_output.strip():
                        logging.error(
                            f"ffmpeg event-clip cam {camera_id} salió con "
                            f"código {process.returncode}: {err_output[:1000]}"
                        )
                except Exception:
                    pass

            # 4. Registrar en BD
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                file_size = os.path.getsize(output_path)
                # IMPORTANTE: medir la duración REAL del MP4 con ffprobe, no
                # asumirla a partir del PRE_BUFFER_SIZE. Si el ffmpeg falló
                # y produjo un MP4 con 0 duración, queremos saberlo aquí
                # antes de subirlo a Telegram.
                real_duration = self._probe_duration(output_path)
                duration = real_duration if real_duration > 0 else (
                    self.EVENT_RECORDING_DURATION + (self.PRE_BUFFER_SIZE / 15)
                )
                if real_duration <= 0:
                    logging.error(
                        f"⚠ Clip {output_path} tiene duración=0 según ffprobe. "
                        f"El MP4 probablemente está corrupto. Revisa los flags "
                        f"de ffmpeg arriba (¿se aplicó -fps_mode cfr?)."
                    )

                recording = Recording(
                    camera_id=camera_id,
                    start_time=datetime.fromtimestamp(event_data.timestamp - (self.PRE_BUFFER_SIZE / 15)),
                    end_time=datetime.utcnow(),
                    file_path=output_path,
                    file_size_bytes=file_size,
                    duration_seconds=duration
                )
                self._recording_repo.create(recording)
                logging.info(
                    f"✅ Clip guardado: {output_path} "
                    f"({file_size} bytes, {duration:.1f}s real, "
                    f"{frames_written} frames escritos)"
                )

                # 5. Notificar el VIDEO por Telegram al terminar la grabación.
                # Antes esto no se hacía: el clip quedaba solo en disco/BD.
                # El usuario espera: foto inmediata + video con pre+post después.
                self._send_clip_to_telegram(camera_id, output_path, event_data)
        
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

    def _send_clip_to_telegram(self, camera_id: int, clip_path: str,
                                event_data: EventData) -> None:
        """
        Envía el clip MP4 a los chats Telegram (best-effort).

        Se ejecuta en el mismo thread del executor que grabó el clip
        (no es un hilo nuevo). Es síncrono respecto a la grabación, pero
        las llamadas HTTP a api.telegram.org tienen sus propios timeouts.

        Decisiones:
          - Por simplicidad, lo enviamos a TODOS los chats configurados en
            SystemConfig (telegram_chat_ids). El ruteo per-user del
            NotificationRouter envía la FOTO; aquí complementamos con el
            VIDEO al chat global. Esto evita doble persistencia/lookup BD.
          - Si Telegram no está configurado o el archivo no existe, no hace
            nada (sin error).
          - Caption corto con clase + cámara + duración (la foto ya tenía
            el caption largo con confianza/hora).
        """
        try:
            from backend.app.notifications.telegram_notifier import telegram_notifier
            if not telegram_notifier._enabled or not telegram_notifier._chat_ids:
                return
            if not os.path.exists(clip_path):
                return

            duration = self.EVENT_RECORDING_DURATION + (self.PRE_BUFFER_SIZE / 15)
            event_type_es = {
                "person": "persona",
                "vehicle": "vehículo",
                "motion": "movimiento",
                "car": "auto",
                "truck": "camión",
                "bus": "autobús",
                "motorcycle": "moto",
            }.get(event_data.event_type, event_data.event_type)
            caption = (
                f"🎥 Video del evento: *{event_type_es}*\n"
                f"📹 Cámara: `{event_data.camera_name or camera_id}`\n"
                f"⏱ Duración: ~{int(duration)}s "
                f"({int(self.PRE_BUFFER_SIZE / 15)}s antes + "
                f"{int(self.EVENT_RECORDING_DURATION)}s después)"
            )

            for chat_id in list(telegram_notifier._chat_ids):
                try:
                    telegram_notifier.send_video(chat_id, clip_path, caption)
                except Exception as e:
                    logging.error(f"Error enviando video Telegram a {chat_id}: {e}")
        except Exception as e:
            logging.error(f"Error en _send_clip_to_telegram: {e}", exc_info=True)

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

    def _use_recording_copy_mode(self) -> bool:
        """
        Paso 0 (decisión). ¿Grabar por remux (`-c copy`) en vez de recodificar?

        Requiere RECORDING_COPY_MODE=true Y go2rtc activo, porque el modo copia
        lee el restream RTSP estable de go2rtc (no el pipe de frames raw). Si
        cualquiera de las dos condiciones falla, se usa el camino clásico
        (libx264) sin cambios → comportamiento por defecto idéntico al actual.
        """
        try:
            return bool(settings.RECORDING_COPY_MODE and settings.GO2RTC_ENABLED)
        except Exception:
            return False

    def _continuous_loop(self, camera_id: int, stop_event: threading.Event) -> None:
        """
        FIX CRÍTICO: Streaming directo sin acumular frames en RAM.
        Cada frame se escribe inmediatamente al stdin de FFmpeg.
        """
        # Desvío al modo -c copy si está habilitado (CPU ≈ 0, sin recodificar).
        if self._use_recording_copy_mode():
            return self._continuous_loop_copy(camera_id, stop_event)

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
                
                # MP4 fragmentado para que SPLICE pueda leer el archivo
                # mientras se está escribiendo.
                #
                # Cambios respecto a la versión anterior:
                #   - frag_duration 500ms (antes 2s): SPLICE puede acceder
                #     a un segmento recién abierto en <1 s. Antes los
                #     primeros 2 s la cabecera no estaba en disco → SPLICE
                #     fallaba con "moov atom not found" en eventos al
                #     inicio de un segmento.
                #   - keyint 15 (1 s @ 15fps): un keyframe cada segundo
                #     → fragmentos más densos, mejor seek.
                #   - flush_packets 1: fuerza al muxer a escribir cada
                #     paquete al disco inmediatamente (no buffer interno).
                #
                # Al cerrar el segmento, _remux_fragmented_to_regular()
                # convierte a MP4 estándar para reproducción óptima.
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                    "-flush_packets", "1",
                    "-f", "rawvideo",
                    "-vcodec", "rawvideo",
                    "-s", f"{width}x{height}",
                    "-pix_fmt", "bgr24",
                    "-framerate", "15",
                    "-i", "pipe:0",
                    "-an",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-preset", "ultrafast",
                    "-crf", "28",
                    "-g", "15",                    # keyframe cada 1s @ 15fps
                    "-fps_mode", "cfr",
                    "-r", "15",
                    "-movflags",
                    "+frag_keyframe+empty_moov+default_base_moof",
                    "-frag_duration", "500000",    # 500ms por fragmento (µs)
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
                
                # CFR a 15 fps con duplicación si la cámara va más lenta.
                #
                # CRÍTICO: NO saltar duplicados. Le decimos a FFmpeg
                # `-framerate 15 -fps_mode cfr -r 15`, lo que significa que
                # asume que cada frame de input representa 1/15 s de video.
                # Si saltáramos duplicados cuando la cámara va a 4 fps:
                #   - 120 s wall clock × 4 fps = 480 frames escritos
                #   - FFmpeg los interpreta como 480/15 = 32 s de video
                #   - El SPLICE pide offset N segundos en el video pero
                #     el video es 4× más corto que el wall clock → falla
                #     o devuelve clip de 0 s (~30 KB).
                # Escribir el ÚLTIMO frame disponible cada 66 ms garantiza:
                #   - 15 fps efectivos al pipe de FFmpeg
                #   - Duración del MP4 = duración wall clock
                #   - SPLICE mapea offsets correctamente
                # libx264 ultrafast codifica los frames duplicados como
                # P-frames diminutos (~50-100 B), el coste de bytes es bajo.
                while (time.time() - segment_start_time) < self.CONTINUOUS_SEGMENT_DURATION:
                    if stop_event.is_set():
                        break

                    last_frame = None
                    with self._pre_buffer_lock:
                        if camera_id in self._pre_buffers and self._pre_buffers[camera_id]:
                            last_frame, _ = self._pre_buffers[camera_id][-1]

                    if last_frame is not None:
                        try:
                            process.stdin.write(last_frame.tobytes())
                            segment_frames += 1
                        except (BrokenPipeError, OSError):
                            break
                        except Exception as e:
                            logging.error(f"Error escribiendo frame continuo: {e}")
                            break

                    time.sleep(0.066)  # 15 FPS
                
                # Cerrar segmento
                if process:
                    try:
                        process.stdin.close()
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

                # RE-MUX: el archivo recién cerrado está en formato fMP4
                # (frag_keyframe+empty_moov+default_base_moof) para que el
                # SPLICE pueda leerlo MIENTRAS se escribe. Eso resuelve el
                # problema de "moov atom not found", pero algunos reproductores
                # (QMediaPlayer en Windows, VLC < 3.0, Telegram preview)
                # tienen problemas para buscar/seek en fMP4. Aquí lo
                # convertimos a MP4 regular con `-c copy -movflags +faststart`.
                # No re-encodea (es rápido, ~100-300ms por segmento de 2 min).
                if os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
                    output_path = self._remux_fragmented_to_regular(output_path)

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

    def _continuous_loop_copy(self, camera_id: int, stop_event: threading.Event) -> None:
        """
        PIPELINE de grabación continua en modo `-c copy` (sin recodificar).

        Coste de CPU ≈ 0: copia el H.264 que ya emite la cámara, vía el restream
        de go2rtc, directamente a disco. No toca píxeles, no usa el pipe de
        frames raw ni libx264.

          Paso 1. Resolver la URL RTSP del restream de go2rtc para la cámara.
          Paso 2. Por cada segmento, lanzar ffmpeg que LEE ese RTSP y COPIA el
                  vídeo a un MP4 durante CONTINUOUS_SEGMENT_DURATION segundos
                  (ffmpeg se autodetiene con `-t`).
          Paso 3. Al cerrar el segmento: registrarlo en BD + generar thumbnail.
          Paso 4. Repetir hasta stop_event. Si el segmento sale vacío (fuente
                  caída / red inestable), backoff y reintento — resiliencia.

        Nota: el MP4 se escribe fragmentado (+frag_keyframe+empty_moov) para que
        el SPLICE de eventos pueda leerlo mientras se graba, igual que el modo
        clásico.
        """
        from backend.app.streaming.go2rtc_manager import Go2RtcManager

        rtsp_url = Go2RtcManager().rtsp_restream_url(camera_id)
        segment_count = 0

        # H265/HEVC en MP4 requiere la etiqueta 'hvc1' para que los
        # reproductores (VLC/QuickTime/navegador) lo abran; con 'hev1' muchos
        # muestran pantalla negra. Detectamos el códec UNA vez y, si es HEVC,
        # añadimos -tag:v hvc1. Para H264 no se añade nada (lo rompería).
        vcodec = self._probe_video_codec(rtsp_url)
        tag_args = ["-tag:v", "hvc1"] if vcodec == "hevc" else []
        logging.info(
            f"Grabación continua (-c copy) cámara {camera_id} desde {rtsp_url} "
            f"(códec vídeo={vcodec or 'desconocido'}{' → tag hvc1' if tag_args else ''})"
        )

        while not stop_event.is_set():
            segment_start = datetime.utcnow()
            process = None
            try:
                cam_dir = os.path.join(self._recordings_dir, str(camera_id), "continuous")
                os.makedirs(cam_dir, exist_ok=True)
                filename = f"{camera_id}_{segment_start.strftime('%Y%m%d_%H%M%S')}.mp4"
                output_path = os.path.join(cam_dir, filename)

                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                    # go2rtc sirve RTSP solo por TCP (UDP → 461). Este loop
                    # siempre lee del restream de go2rtc, así que forzamos tcp.
                    "-rtsp_transport", "tcp",
                    "-i", rtsp_url,
                    "-t", str(self.CONTINUOUS_SEGMENT_DURATION),
                    "-an",
                    "-c:v", "copy",
                    *tag_args,
                    "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
                    "-frag_duration", "500000",
                    output_path,
                ]
                t0 = time.time()
                process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )

                # Esperar a que ffmpeg termine el segmento (-t) O a que pidan parar.
                while process.poll() is None:
                    if stop_event.is_set():
                        try:
                            process.terminate()
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        break
                    time.sleep(0.5)

                if os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
                    output_path = self._remux_fragmented_to_regular(output_path)
                    file_size = os.path.getsize(output_path)
                    recording = Recording(
                        camera_id=camera_id,
                        start_time=segment_start,
                        end_time=datetime.utcnow(),
                        file_path=output_path,
                        file_size_bytes=file_size,
                        duration_seconds=max(0.0, time.time() - t0),
                    )
                    self._recording_repo.create(recording)
                    self._generate_thumbnail(output_path)
                    segment_count += 1
                elif not stop_event.is_set():
                    # Segmento vacío → fuente probablemente caída: backoff.
                    logging.warning(
                        f"Segmento -c copy vacío cámara {camera_id}; "
                        f"¿go2rtc/cámara accesibles? Reintentando en 2s"
                    )
                    time.sleep(2)
            except Exception as e:
                logging.error(f"Error en segmento -c copy cámara {camera_id}: {e}")
                time.sleep(2)

        logging.info(
            f"⏹️ Grabación continua (-c copy) finalizada cámara {camera_id} "
            f"({segment_count} segmentos)"
        )

    def _probe_video_codec(self, url: str) -> "str | None":
        """
        Detecta el códec de vídeo (p.ej. 'hevc', 'h264') de una fuente RTSP con
        ffprobe. Best-effort: si falla devuelve None. Se usa una sola vez al
        arrancar la grabación para decidir el etiquetado del MP4.
        """
        try:
            out = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-rtsp_transport", "tcp",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=codec_name",
                    "-of", "default=nokey=1:noprint_wrappers=1",
                    url,
                ],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
            )
            codec = out.stdout.decode(errors="ignore").strip().lower()
            return codec or None
        except Exception as e:
            logging.warning(f"ffprobe no pudo detectar códec de {url}: {e}")
            return None

    def _generate_thumbnail(self, video_path: str) -> "str | None":
        """
        Genera un thumbnail JPEG (~320px de ancho) del primer segundo del vídeo
        para los listados de grabaciones en móvil/desktop.

        Best-effort: barato, no recodifica el vídeo y NUNCA interrumpe la
        grabación si falla (devuelve None).
        """
        try:
            thumb_path = os.path.splitext(video_path)[0] + ".jpg"
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", "1", "-i", video_path, "-frames:v", "1",
                "-vf", "scale=320:-2", thumb_path,
            ]
            subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15
            )
            return thumb_path if os.path.exists(thumb_path) else None
        except Exception as e:
            logging.warning(f"No se pudo generar thumbnail de {video_path}: {e}")
            return None

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