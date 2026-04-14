import threading
import logging

from ..database.models import Camera
from ..database.repositories.camera_repository import CameraRepository
from ..streaming.frame_buffer import CircularFrameBuffer, FrameData
from ..streaming.frame_distributor import FrameDistributor
from ..workers.ffmpeg_worker import FFmpegWorker, WorkerStatus
from ..streaming.mjpeg_streamer import mjpeg_streamer


class CameraManager:
    """
    Singleton que gestiona el ciclo de vida de todas las cámaras:
    - Inicia/detiene workers FFmpeg
    - Crea buffers y distribuidores por cámara
    - Proporciona acceso centralizado a componentes de streaming
    - SOPORTE DUAL LENS: Divide cámaras side-by-side en dos streams independientes
      SIN crear buffers/distribuidores separados; los frames divididos se inyectan
      directamente en el MJPEG streamer con un stream_id ('l1' / 'l2').
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
        self._lifecycle_lock = threading.Lock()
        self._camera_repo = CameraRepository()
        self._logger = logging.getLogger(__name__)
        self._initialized = True

    def start_camera(self, camera: Camera, register_mjpeg: bool = True) -> bool:
        if camera.is_dual_lens:
            return self.start_dual_lens_camera(camera)

        with self._lifecycle_lock:
            if camera.id in self._workers:
                self._logger.warning(f"Cámara {camera.id} ya está activa")
                return False

            try:
                buffer = CircularFrameBuffer(camera_id=camera.id, maxsize=3)
                distributor = FrameDistributor(camera_id=camera.id)
                worker = FFmpegWorker(camera=camera, frame_buffer=buffer)

                distributor.start(buffer)
                worker.start()

                self._workers[camera.id] = worker
                self._buffers[camera.id] = buffer
                self._distributors[camera.id] = distributor

                if register_mjpeg:
                    distributor.register_consumer(
                        "mjpeg",
                        lambda fd: mjpeg_streamer.update_frame(camera.id, "main", fd),
                        needs_copy=False
                    )
                    self._logger.info(f"MJPEG registrado automáticamente para cámara {camera.id} (stream main)")

                self._logger.info(f"Cámara {camera.id} ({camera.name}) iniciada correctamente")
                return True

            except Exception as e:
                self._logger.error(f"Error al iniciar cámara {camera.id}: {e}")
                return False

    def start_dual_lens_camera(self, parent_camera: Camera) -> bool:
        from ..processing.dual_lens_splitter import DualLensSplitter

        with self._lifecycle_lock:
            if parent_camera.id in self._workers:
                self._logger.warning(f"Cámara dual {parent_camera.id} ya está activa")
                return False

            try:
                self._logger.info(f"Iniciando cámara DUAL LENS {parent_camera.id}")

                raw_buffer = CircularFrameBuffer(camera_id=parent_camera.id, maxsize=5)
                raw_distributor = FrameDistributor(camera_id=parent_camera.id)
                worker = FFmpegWorker(camera=parent_camera, frame_buffer=raw_buffer)

                raw_distributor.start(raw_buffer)
                worker.start()

                self._workers[parent_camera.id] = worker
                self._buffers[parent_camera.id] = raw_buffer
                self._distributors[parent_camera.id] = raw_distributor

                splitter = DualLensSplitter(parent_camera.id, split_mode="vertical")

                def split_and_distribute(frame_data: FrameData):
                    frame = frame_data.frame
                    timestamp = frame_data.timestamp

                    left, right = splitter.split(frame)

                    if left is not None:
                        fd_l1 = FrameData(
                            frame=left,
                            timestamp=timestamp,
                            camera_id=parent_camera.id,
                            frame_id=frame_data.frame_id,
                            stream_id="l1"
                        )
                        mjpeg_streamer.update_frame(parent_camera.id, "l1", fd_l1)

                    if right is not None:
                        fd_l2 = FrameData(
                            frame=right,
                            timestamp=timestamp,
                            camera_id=parent_camera.id,
                            frame_id=frame_data.frame_id,
                            stream_id="l2"
                        )
                        mjpeg_streamer.update_frame(parent_camera.id, "l2", fd_l2)

                raw_distributor.register_consumer(
                    "dual_lens_splitter",
                    split_and_distribute,
                    needs_copy=True
                )

                self._logger.info(f"Cámara dual {parent_camera.id} iniciada correctamente")
                self._logger.info(f"  → Lente izquierdo disponible con stream_id='l1'")
                self._logger.info(f"  → Lente derecho disponible con stream_id='l2'")
                return True

            except Exception as e:
                self._logger.error(f"Error iniciando cámara dual {parent_camera.id}: {e}", exc_info=True)
                return False

    def stop_camera(self, camera_id: int) -> bool:
        with self._lifecycle_lock:
            if camera_id in self._workers:
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

                # Limpiar streams MJPEG (main y posibles lentes duales)
                try:
                    # Para cámara normal o dual, limpiar main
                    mjpeg_streamer.cleanup_stream(camera_id, "main")
                except Exception:
                    pass
                try:
                    # Si es dual, limpiar lentes
                    mjpeg_streamer.cleanup_stream(camera_id, "l1")
                    mjpeg_streamer.cleanup_stream(camera_id, "l2")
                except Exception as e:
                    self._logger.debug(f"Limpieza de streams virtuales: {e}")

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
            return self.start_camera(camera, register_mjpeg=True)

    def get_distributor(self, camera_id):
        if isinstance(camera_id, str) and "_l" in camera_id:
            self._logger.debug(f"get_distributor llamado con ID de lente virtual '{camera_id}' -> retorna None")
            return None
        return self._distributors.get(camera_id)

    def get_buffer(self, camera_id):
        if isinstance(camera_id, str) and "_l" in camera_id:
            return None
        return self._buffers.get(camera_id)

    def get_worker(self, camera_id: int) -> FFmpegWorker | None:
        return self._workers.get(camera_id)

    def get_dual_lens_ids(self, parent_id: int) -> list:
        return [f"{parent_id}_l1", f"{parent_id}_l2"]

    def start_all_active(self) -> None:
        active_cameras = self._camera_repo.get_active_cameras()
        self._logger.info(f"Iniciando {len(active_cameras)} cámaras activas...")
        for camera in active_cameras:
            if not camera.rtsp_url:
                self._logger.warning(f"Cámara {camera.id} no tiene URL RTSP, omitiendo")
                continue
            self.start_camera(camera, register_mjpeg=True)

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