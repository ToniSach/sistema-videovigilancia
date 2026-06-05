import threading
import logging

from ..database.models import Camera
from ..database.repositories.camera_repository import CameraRepository
from ..streaming.frame_buffer import CircularFrameBuffer, FrameData
from ..streaming.frame_distributor import FrameDistributor
from ..workers.ffmpeg_worker import FFmpegWorker, WorkerStatus


class CameraManager:
    """
    Singleton que gestiona el ciclo de vida de todas las cámaras:
    - Inicia/detiene workers FFmpeg
    - Crea buffers y distribuidores por cámara
    - Proporciona acceso centralizado a componentes de streaming
    - SOPORTE DUAL LENS: Divide cámaras side-by-side en dos streams independientes
      identificados por stream_id ('l1' / 'l2'), que go2rtc publica por separado.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return

        self._workers: dict[int, FFmpegWorker] = {}
        self._buffers: dict = {}
        self._distributors: dict = {}
        # Para cámaras dual-lens: buffers/distributors por lente
        # Clave: (camera_id, "l1" | "l2")
        self._lens_buffers: dict[tuple, CircularFrameBuffer] = {}
        self._lens_distributors: dict[tuple, FrameDistributor] = {}
        # RLock (reentrante) en vez de Lock porque restart_camera adquiere el
        # lock y dentro llama a stop_camera que también lo adquiere. Con Lock
        # plano eso era un deadlock garantizado: PUT /cameras/{id} se quedaba
        # esperando hasta el read-timeout de 60s del cliente.
        self._lifecycle_lock = threading.RLock()
        self._lock = threading.Lock()
        self._camera_repo = CameraRepository()
        self._logger = logging.getLogger(__name__)
        # IDs de cámaras auto-desactivadas tras agotar reintentos. Sirve para
        # que el stalled monitor las ignore aunque sigan apareciendo en métricas.
        self._auto_disabled: set[int] = set()
        self._initialized = True

    def _on_permanent_failure(self, camera_id: int, error_code: str) -> None:
        """
        Callback que se dispara cuando un worker FFmpeg agota MAX_RECONNECT o
        recibe un error fatal no-retryable (auth, codec, etc.).

        Marca la cámara como inactiva en BD, libera recursos y publica un
        evento `camera_offline`. El usuario puede reactivar la cámara desde
        la UI tras editar la IP/credenciales.
        """
        self._logger.warning(
            f"[AUTO-DISABLE] Cámara {camera_id} agotó reintentos "
            f"(code={error_code}). Marcando inactiva en BD y deteniendo worker."
        )
        # Marcar inmediatamente para que el stalled monitor no la reanime
        # mientras estamos limpiando.
        self._auto_disabled.add(camera_id)

        # 1) Persistir is_active=False + last_error_code en BD
        try:
            from ..database.connection import db_manager
            from ..database.models import Camera as CameraModel
            with db_manager.get_session() as session:
                cam = session.query(CameraModel).filter_by(id=camera_id).first()
                if cam is not None:
                    cam.is_active = False
                    cam.last_error_code = error_code
                    session.commit()
                    self._logger.info(
                        f"[AUTO-DISABLE] Cámara {camera_id} marcada is_active=False en BD"
                    )
        except Exception as e:
            self._logger.error(
                f"[AUTO-DISABLE] No pude marcar is_active=False para cam {camera_id}: {e}"
            )

        # 2) Desregistrar de métricas para que check_stalled_cameras no la vea
        try:
            from ..infrastructure.metrics.collector import metrics_collector
            metrics_collector.unregister_camera(camera_id)
        except Exception as e:
            self._logger.debug(f"[AUTO-DISABLE] unregister metrics: {e}")

        # 3) Parar worker + buffers + distributors en thread aparte para no
        #    bloquear el thread del worker que está terminando.
        def _cleanup():
            # Apagar IA en runtime PRIMERO (no toca has_ai en BD; el usuario
            # podrá reactivarla al corregir la IP de la cámara). Sin esto, el
            # endpoint GET /api/v1/ai/<id> seguiría reportando active=True para
            # una cámara muerta y el frontend muestra "IA habilitada" engañoso.
            try:
                from ..container import get_container
                ai_svc = get_container().get("ai_service")
                if ai_svc is not None:
                    for lens in ("main", "l1", "l2"):
                        try:
                            if hasattr(ai_svc, "is_active") and \
                               ai_svc.is_active(camera_id, lens):
                                ai_svc.deactivate_ai(camera_id, lens=lens)
                        except Exception:
                            pass
            except Exception as e:
                self._logger.debug(f"[AUTO-DISABLE] pre-stop ai deactivate: {e}")

            try:
                self.stop_camera(camera_id)
            except Exception as e:
                self._logger.error(f"[AUTO-DISABLE] stop_camera({camera_id}): {e}")
            # Emitir evento camera_offline para que TelegramNotifier / NotificationRouter
            # avisen al usuario.
            try:
                from ..events.event_manager import event_manager, EventData
                import time as _t
                event_manager.emit(EventData(
                    event_type="camera_offline",
                    camera_id=camera_id,
                    camera_name=f"Camara_{camera_id}",
                    timestamp=_t.time(),
                    confidence=1.0,
                    frame=None,
                    metadata={"reason": f"auto_disabled_{error_code}"},
                ))
            except Exception as e:
                self._logger.debug(f"[AUTO-DISABLE] emit evento: {e}")
        threading.Thread(
            target=_cleanup, daemon=True, name=f"AutoDisable-Cam{camera_id}"
        ).start()

    def is_auto_disabled(self, camera_id: int) -> bool:
        """True si la cámara fue auto-desactivada por fallo permanente."""
        return camera_id in self._auto_disabled

    def clear_auto_disabled(self, camera_id: int) -> None:
        """Reset del flag de auto-desactivación (llamar al re-activar manualmente)."""
        self._auto_disabled.discard(camera_id)

    def _go2rtc_source_url(self, camera_id: int):
        """
        Devuelve la URL del restream de go2rtc para alimentar al FFmpegWorker,
        o None para que use la cámara directa. Solo aplica si GO2RTC_ENABLED y
        GO2RTC_AS_SOURCE están activos (cámaras de 1 sola conexión RTSP).
        """
        try:
            from backend.app.config import settings as _s
            if getattr(_s, "GO2RTC_ENABLED", False) and getattr(_s, "GO2RTC_AS_SOURCE", False):
                from backend.app.streaming.go2rtc_manager import Go2RtcManager
                url = Go2RtcManager().rtsp_restream_url(camera_id)
                self._logger.info(
                    f"[GO2RTC_AS_SOURCE] Cam {camera_id}: el worker leerá del "
                    f"restream de go2rtc → {url} (NO directo a la cámara)"
                )
                return url
            else:
                self._logger.info(
                    f"Cam {camera_id}: worker lee DIRECTO de la cámara "
                    f"(GO2RTC_AS_SOURCE={getattr(_s, 'GO2RTC_AS_SOURCE', False)}, "
                    f"GO2RTC_ENABLED={getattr(_s, 'GO2RTC_ENABLED', False)})"
                )
        except Exception as e:
            self._logger.warning(f"No se pudo resolver source go2rtc cam {camera_id}: {e}")
        return None

    def _maybe_sync_time(self, camera) -> None:
        """
        Sincroniza la hora de la cámara con la del servidor (ONVIF), en un hilo
        aparte y best-effort, si CAMERA_SYNC_TIME_ON_START está activo. No
        bloquea el arranque ni falla si la cámara no soporta ONVIF.
        """
        try:
            from backend.app.config import settings as _s
            if not getattr(_s, "CAMERA_SYNC_TIME_ON_START", True):
                return

            def _do():
                try:
                    from backend.app.cameras.time_sync import sync_camera_time
                    sync_camera_time(camera)
                except Exception as e:
                    self._logger.debug(f"sync hora cam {getattr(camera, 'id', '?')}: {e}")

            threading.Thread(
                target=_do, name=f"TimeSync-{getattr(camera, 'id', '?')}", daemon=True
            ).start()
        except Exception:
            pass

    def start_camera(self, camera: Camera) -> bool:
        if camera.is_dual_lens:
            return self.start_dual_lens_camera(camera)

        # Reset del flag de auto-desactivación: si la estamos re-arrancando es
        # porque el usuario corrigió la config.
        self.clear_auto_disabled(camera.id)

        with self._lifecycle_lock:
            if camera.id in self._workers:
                self._logger.warning(f"Cámara {camera.id} ya está activa")
                return False

            try:
                # maxsize=2 → mínima latencia. get_latest() siempre devuelve el
                # más reciente, así que un buffer mayor no aporta nada (los
                # frames intermedios se descartan igualmente). Antes era 3,
                # ahora bajamos a 2 = 1 leyendo + 1 escribiendo en pico.
                buffer = CircularFrameBuffer(camera_id=camera.id, maxsize=2)
                distributor = FrameDistributor(camera_id=camera.id)
                # Import local: el modelo de settings se inyecta solo en este
                # método (no estaba a nivel de módulo y rompía cámaras mono).
                from backend.app.config import settings as _settings
                # Cámaras de 1 sola conexión RTSP: leer del restream de go2rtc
                # (si GO2RTC_AS_SOURCE) para que go2rtc sea el único consumidor
                # de la cámara y no haya contención con la grabación/otros.
                _src = self._go2rtc_source_url(camera.id)
                # El restream de go2rtc SOLO acepta RTSP sobre TCP (UDP →
                # "461 Unsupported transport"). RTSP_TRANSPORT del .env aplica
                # a la cámara; cuando leemos de go2rtc forzamos tcp.
                _transport = "tcp" if _src else getattr(_settings, "RTSP_TRANSPORT", "tcp")
                worker = FFmpegWorker(
                    camera=camera,
                    frame_buffer=buffer,
                    rtsp_transport=_transport,
                    source_url=_src,
                )
                worker.set_permanent_failure_callback(self._on_permanent_failure)

                distributor.start(buffer)
                worker.start()

                self._workers[camera.id] = worker
                self._buffers[camera.id] = buffer
                self._distributors[camera.id] = distributor

                # El directo se sirve por go2rtc (WebRTC/RTSP/HLS) directo al
                # cliente; el backend solo procesa frames para IA y grabación.
                self._wire_recording_manager(camera.id, distributor)
                self._maybe_sync_time(camera)

                self._logger.info(f"Cámara {camera.id} ({camera.name}) iniciada correctamente")
                return True

            except Exception as e:
                self._logger.error(f"Error al iniciar cámara {camera.id}: {e}")
                return False

    def start_dual_lens_camera(self, parent_camera: Camera) -> bool:
        from ..cameras.dual_lens_splitter import DualLensSplitter
        from backend.app.config import settings

        # Reset del flag de auto-desactivación: si la estamos re-arrancando es
        # porque el usuario corrigió la config.
        self.clear_auto_disabled(parent_camera.id)

        with self._lifecycle_lock:
            if parent_camera.id in self._workers:
                self._logger.warning(f"Cámara dual {parent_camera.id} ya está activa")
                return False

            try:
                self._logger.info(f"Iniciando cámara DUAL LENS {parent_camera.id}")

                # FIX: Buffer raw con resolución COMPLETA (no la default 720p)
                # Buffer raw del stream completo dual-lens (antes del split).
                # maxsize=2 = mínima latencia; el splitter es el único consumer
                # y procesa síncronamente, no necesita colchón.
                raw_buffer = CircularFrameBuffer(camera_id=parent_camera.id, maxsize=2)
                raw_distributor = FrameDistributor(camera_id=parent_camera.id)

                # FIX: Pasar target_resolution forzando las dimensiones duales
                dual_width = settings.FFMPEG_DUAL_LENS_WIDTH   # 1280
                dual_height = settings.FFMPEG_DUAL_LENS_HEIGHT # 1440

                _dual_src = self._go2rtc_source_url(parent_camera.id)
                _dual_transport = "tcp" if _dual_src else settings.RTSP_TRANSPORT
                worker = FFmpegWorker(
                    camera=parent_camera,
                    frame_buffer=raw_buffer,
                    target_resolution=(dual_width, dual_height),
                    rtsp_transport=_dual_transport,
                    source_url=_dual_src,
                )
                worker.set_permanent_failure_callback(self._on_permanent_failure)

                raw_distributor.start(raw_buffer)
                worker.start()

                self._workers[parent_camera.id] = worker
                self._buffers[parent_camera.id] = raw_buffer
                self._distributors[parent_camera.id] = raw_distributor

                # Configurar el splitter
                splitter = DualLensSplitter(parent_camera.id, split_mode="vertical")  # o "horizontal"

                # Buffers y distributors por lente para que AI/recording puedan
                # suscribirse a un solo lente (l1 o l2) en lugar del frame raw.
                lens_buf_l1 = CircularFrameBuffer(camera_id=parent_camera.id, maxsize=2)
                lens_buf_l2 = CircularFrameBuffer(camera_id=parent_camera.id, maxsize=2)
                lens_dist_l1 = FrameDistributor(camera_id=parent_camera.id)
                lens_dist_l2 = FrameDistributor(camera_id=parent_camera.id)
                lens_dist_l1.start(lens_buf_l1)
                lens_dist_l2.start(lens_buf_l2)

                self._lens_buffers[(parent_camera.id, "l1")] = lens_buf_l1
                self._lens_buffers[(parent_camera.id, "l2")] = lens_buf_l2
                self._lens_distributors[(parent_camera.id, "l1")] = lens_dist_l1
                self._lens_distributors[(parent_camera.id, "l2")] = lens_dist_l2

                # El directo por lente se sirve por go2rtc (cam_X_l1/l2). Los
                # distribuidores de lente quedan únicamente para la IA.

                def split_and_distribute(frame_data: FrameData):
                    # splitter.split() retorna VIEWS (no copia) del frame.
                    # Es seguro porque FFmpegWorker crea un ndarray fresco
                    # por cada read del pipe → los views permanecen válidos
                    # mientras alguien tenga referencia (Python GC).
                    left, right = splitter.split(frame_data.frame)

                    if left is not None and left.size > 0:
                        lens_buf_l1.put(left)
                    if right is not None and right.size > 0:
                        lens_buf_l2.put(right)

                # needs_copy=False: splitter solo lee (slices) y los views
                # quedan en lens_buf. Antes con needs_copy=True se hacía un
                # memcpy de ~5.5 MB del frame combinado por cada cuadro,
                # malgastando ancho de banda de memoria.
                raw_distributor.register_consumer(
                    "dual_lens_splitter",
                    split_and_distribute,
                    needs_copy=False
                )

                # En dual-lens registramos el pre-buffer de grabación contra l1
                # (asumimos l1 como lente "principal" del par).
                self._wire_recording_manager(parent_camera.id, lens_dist_l1)
                self._maybe_sync_time(parent_camera)

                self._logger.info(f"✅ Cámara dual {parent_camera.id} iniciada: "
                                f"{dual_width}x{dual_height} → l1/l2")
                return True

            except Exception as e:
                self._logger.error(f"Error iniciando cámara dual {parent_camera.id}: {e}", exc_info=True)
                return False
        
    def stop_camera(self, camera_id: int) -> bool:
        with self._lifecycle_lock:
            if camera_id in self._workers:
                # Parar grabación continua ANTES de matar el worker para que
                # cierre el último segmento limpiamente.
                try:
                    from ..container import get_container
                    rec_mgr = get_container().get("recording_manager")
                    if rec_mgr and rec_mgr.is_recording_continuous(camera_id):
                        rec_mgr.stop_continuous_recording(camera_id)
                except Exception as e:
                    self._logger.debug(f"stop_continuous_recording: {e}")

                try:
                    worker = self._workers.pop(camera_id)
                    worker.stop()
                except Exception as e:
                    self._logger.error(f"Error deteniendo worker {camera_id}: {e}")

                # Eliminar buffer y distributor del stream raw
                if camera_id in self._buffers:
                    try:
                        self._buffers[camera_id].clear()
                        del self._buffers[camera_id]
                    except Exception as e:
                        self._logger.error(f"Error limpiando buffer raw {camera_id}: {e}")

                if camera_id in self._distributors:
                    try:
                        self._distributors[camera_id].stop()
                        del self._distributors[camera_id]
                    except Exception as e:
                        self._logger.error(f"Error deteniendo distributor raw {camera_id}: {e}")

                # Detener distributors/buffers por lente si existen
                for lens in ("l1", "l2"):
                    key = (camera_id, lens)
                    if key in self._lens_distributors:
                        try:
                            self._lens_distributors[key].stop()
                        except Exception as e:
                            self._logger.error(f"Error deteniendo distributor {lens} {camera_id}: {e}")
                        finally:
                            del self._lens_distributors[key]
                    if key in self._lens_buffers:
                        try:
                            self._lens_buffers[key].clear()
                        except Exception:
                            pass
                        finally:
                            del self._lens_buffers[key]

                # Soltar el cache PTZ por si la cámara cambia de IP/credenciales
                try:
                    from .ptz_controller import ptz_manager
                    ptz_manager.drop(camera_id)
                except Exception:
                    pass

            else:
                self._logger.warning(f"No se encontró cámara activa con ID {camera_id}")

            self._logger.info(f"Cámara {camera_id} detenida")
            return True

    def restart_camera(self, camera_id: int) -> bool:
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            self._logger.error(f"No se encontró cámara {camera_id} para reiniciar")
            return False

        with self._lifecycle_lock:
            self.stop_camera(camera_id)
            import time
            time.sleep(0.5)
            return self.start_camera(camera)

    def get_distributor(self, camera_id, stream_id: str = "main"):
        """
        Devuelve el distributor del stream solicitado.
        - stream_id="main": stream principal (cámara mono o el raw de la dual).
        - stream_id="l1"/"l2": distributors por lente (solo dual-lens).
        """
        if stream_id in ("l1", "l2"):
            return self._lens_distributors.get((camera_id, stream_id))
        # Compat: si pasaron un str tipo "1_l1" como en versiones viejas
        if isinstance(camera_id, str) and "_l" in camera_id:
            try:
                cid_str, lens = camera_id.split("_", 1)
                return self._lens_distributors.get((int(cid_str), lens))
            except Exception:
                return None
        return self._distributors.get(camera_id)

    def get_buffer(self, camera_id, stream_id: str = "main"):
        if stream_id in ("l1", "l2"):
            return self._lens_buffers.get((camera_id, stream_id))
        if isinstance(camera_id, str) and "_l" in camera_id:
            return None
        return self._buffers.get(camera_id)

    def get_worker(self, camera_id: int) -> FFmpegWorker | None:
        return self._workers.get(camera_id)

    def _wire_recording_manager(self, camera_id: int, distributor) -> None:
        """
        Conecta el RecordingManager al distributor de una cámara recién iniciada:
        - Registra el buffer pre-evento (para clips automáticos en eventos)
        - Si AUTO_START_RECORDING=true, arranca también la grabación continua

        Para no bloquear el arranque de la cámara (FFmpeg tarda ~5s en producir
        el primer frame), la grabación continua se programa con un pequeño delay.
        """
        try:
            from ..container import get_container
            from ..config import settings
            container = get_container()
            rec_mgr = container.get("recording_manager")
            if rec_mgr is None:
                return

            # 1) Registrar buffer pre-evento (instantáneo)
            rec_mgr.register_camera_buffer(camera_id, distributor)

            # 2) Auto-start de grabación continua si está habilitado
            if getattr(settings, "AUTO_START_RECORDING", True):
                import threading
                def _delayed_start():
                    import time
                    # Esperar 8s para que FFmpeg estabilice frames y el pre-buffer
                    # se llene; sin esto los primeros segundos del clip salen vacíos.
                    time.sleep(8)
                    try:
                        ok = rec_mgr.start_continuous_recording(camera_id)
                        if ok:
                            self._logger.info(
                                f"[AUTO-REC] Grabación continua iniciada para cámara {camera_id}"
                            )
                    except Exception as e:
                        self._logger.warning(
                            f"[AUTO-REC] No se pudo iniciar grabación continua "
                            f"cam={camera_id}: {e}"
                        )
                threading.Thread(target=_delayed_start, daemon=True,
                                 name=f"AutoRec-Cam{camera_id}").start()

            # 3) Auto-activar IA si la cámara tiene has_ai=True en BD.
            # Esto persiste el estado de IA entre reinicios del backend.
            self._auto_start_ai_if_needed(camera_id)
        except Exception as e:
            self._logger.warning(f"No se pudo registrar pre-buffer en RecordingManager: {e}")

    def _auto_start_ai_if_needed(self, camera_id: int) -> None:
        """
        Si la cámara tiene has_ai=True en BD, activa IA automáticamente cuando
        arranca el worker. Para dual-lens se activa en l1 por default.
        """
        try:
            camera = self._camera_repo.get_by_id(camera_id)
            if not camera or not getattr(camera, "has_ai", False):
                return

            import threading, time
            def _delayed_ai_start():
                # Esperar 10s a que FFmpeg estabilice y produzca frames
                time.sleep(10)
                try:
                    from ..container import get_container
                    ai_svc = get_container().get("ai_service")
                    if ai_svc is None:
                        self._logger.warning(f"[AUTO-AI] AIService no disponible cam={camera_id}")
                        return
                    lens = "l1" if camera.is_dual_lens else "main"
                    ok = ai_svc.activate_ai(camera_id, lens=lens, mode="low_cpu")
                    if ok:
                        self._logger.info(
                            f"[AUTO-AI] ✓ IA activada cam={camera_id} lens={lens} (has_ai=True en BD)"
                        )
                    else:
                        self._logger.warning(
                            f"[AUTO-AI] ✗ No se pudo activar IA cam={camera_id} lens={lens}"
                        )
                except Exception as e:
                    self._logger.error(f"[AUTO-AI] cam={camera_id} excepción: {e}")

            threading.Thread(target=_delayed_ai_start, daemon=True,
                             name=f"AutoAI-Cam{camera_id}").start()
        except Exception as e:
            self._logger.warning(f"[AUTO-AI] cam={camera_id} skipped: {e}")

    def get_camera_by_id(self, camera_id: int) -> Camera | None:
        """Obtiene la entidad Camera por su id (lectura desde repositorio)."""
        try:
            return self._camera_repo.get_by_id(camera_id)
        except Exception as e:
            self._logger.error(f"Error obteniendo cámara {camera_id}: {e}")
            return None

    def get_dual_lens_ids(self, parent_id: int) -> list:
        return [f"{parent_id}_l1", f"{parent_id}_l2"]

    def start_all_active(self) -> None:
        active_cameras = self._camera_repo.get_active_cameras()
        self._logger.info(f"Iniciando {len(active_cameras)} cámaras activas...")
        for camera in active_cameras:
            if not camera.rtsp_url:
                self._logger.warning(f"Cámara {camera.id} no tiene URL RTSP, omitiendo")
                continue
            self.start_camera(camera)

    def stop_all(self) -> None:
        camera_ids = list(self._workers.keys())
        self._logger.info(f"Deteniendo {len(camera_ids)} cámaras...")
        for camera_id in camera_ids:
            self.stop_camera(camera_id)

    def get_all_status(self) -> dict:
        with self._lock:
            status = {}
            for camera_id, worker in self._workers.items():
                worker_status = worker.get_status()
                camera = self._camera_repo.get_by_id(camera_id)
                if camera and camera.is_dual_lens:
                    worker_status["is_dual_lens"] = True
                    worker_status["virtual_lenses"] = self.get_dual_lens_ids(camera_id)
                else:
                    worker_status["is_dual_lens"] = False
                status[camera_id] = worker_status
            return status


if __name__ == "__main__":
    cm1 = CameraManager()
    cm2 = CameraManager()
    assert cm1 is cm2, "Singleton no está funcionando correctamente"
    print("CameraManager Singleton: OK")