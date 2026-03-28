import threading
import logging

from ..database.models import Camera
from ..database.repositories.camera_repository import CameraRepository
from ..streaming.frame_buffer import CircularFrameBuffer
from ..streaming.frame_distributor import FrameDistributor
from ..workers.ffmpeg_worker import FFmpegWorker, WorkerStatus
from ..streaming.mjpeg_streamer import mjpeg_streamer


class CameraManager:
    """
    Singleton que gestiona el ciclo de vida de todas las cámaras:
    - Inicia/detiene workers FFmpeg
    - Crea buffers y distribuidores por cámara
    - Proporciona acceso centralizado a componentes de streaming
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
        self._buffers: dict[int, CircularFrameBuffer] = {}
        self._distributors: dict[int, FrameDistributor] = {}
        self._lock = threading.Lock()
        self._camera_repo = CameraRepository()
        self._logger = logging.getLogger(__name__)
        self._initialized = True

    def start_camera(self, camera: Camera, register_mjpeg: bool = True) -> bool:
        """
        Inicia el pipeline completo para una cámara: buffer -> distributor -> worker.

        Args:
            camera: Instancia de Camera a iniciar
            register_mjpeg: Si es True, registra automáticamente el streamer MJPEG como consumidor

        Returns:
            True si se inició correctamente, False si ya estaba corriendo
        """
        with self._lock:
            if camera.id in self._workers:
                self._logger.warning(f"Cámara {camera.id} ya está activa")
                return False

            try:
                # Crear buffer circular
                buffer = CircularFrameBuffer(camera_id=camera.id, maxsize=5)

                # Crear distribuidor de frames
                distributor = FrameDistributor(camera_id=camera.id)

                # Crear y configurar worker FFmpeg
                worker = FFmpegWorker(camera=camera, frame_buffer=buffer)

                # Iniciar: primero distributor (escucha), luego worker (produce)
                distributor.start(buffer)
                worker.start()

                # Guardar referencias
                self._workers[camera.id] = worker
                self._buffers[camera.id] = buffer
                self._distributors[camera.id] = distributor

                # CRÍTICO: Registrar MJPEG automáticamente si se solicita
                if register_mjpeg:
                    distributor.register_consumer(
                        "mjpeg", 
                        lambda fd: mjpeg_streamer.update_frame(camera.id, fd)
                    )
                    self._logger.info(f"MJPEG registrado automáticamente para cámara {camera.id}")

                self._logger.info(f"Cámara {camera.id} ({camera.name}) iniciada correctamente")
                return True

            except Exception as e:
                self._logger.error(f"Error al iniciar cámara {camera.id}: {e}")
                return False

    def stop_camera(self, camera_id: int) -> bool:
        """
        Detiene completamente una cámara y libera recursos.

        Args:
            camera_id: ID de la cámara a detener

        Returns:
            True si se detuvo, False si no existía
        """
        with self._lock:
            if camera_id not in self._workers:
                return False

            try:
                # Detener en orden inverso: worker -> distributor
                worker = self._workers.pop(camera_id)
                worker.stop()

                distributor = self._distributors.pop(camera_id)
                distributor.stop()

                buffer = self._buffers.pop(camera_id)
                buffer.clear()

                # Limpiar también del streamer MJPEG
                mjpeg_streamer.unregister_camera(camera_id)

                self._logger.info(f"Cámara {camera_id} detenida")
                return True

            except Exception as e:
                self._logger.error(f"Error al detener cámara {camera_id}: {e}")
                return False

    def restart_camera(self, camera_id: int) -> bool:
        """
        Reinicia una cámara: detener y volver a iniciar.

        Args:
            camera_id: ID de la cámara a reiniciar

        Returns:
            True si se reinició correctamente
        """
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            self._logger.error(f"No se encontró cámara {camera_id} para reiniciar")
            return False

        self.stop_camera(camera_id)
        # Al reiniciar, también registramos MJPEG automáticamente
        return self.start_camera(camera, register_mjpeg=True)

    def get_distributor(self, camera_id: int) -> FrameDistributor | None:
        """
        Obtiene el distribuidor de frames de una cámara.

        Args:
            camera_id: ID de la cámara

        Returns:
            FrameDistributor o None si no está activa
        """
        return self._distributors.get(camera_id)

    def get_buffer(self, camera_id: int) -> CircularFrameBuffer | None:
        """
        Obtiene el buffer de frames de una cámara.

        Args:
            camera_id: ID de la cámara

        Returns:
            CircularFrameBuffer o None si no está activa
        """
        return self._buffers.get(camera_id)

    def get_worker(self, camera_id: int) -> FFmpegWorker | None:
        """
        Obtiene el worker FFmpeg de una cámara.

        Args:
            camera_id: ID de la cámara

        Returns:
            FFmpegWorker o None si no está activa
        """
        return self._workers.get(camera_id)

    def start_all_active(self) -> None:
        """Inicia todas las cámaras marcadas como activas en la base de datos."""
        active_cameras = self._camera_repo.get_active_cameras()

        self._logger.info(f"Iniciando {len(active_cameras)} cámaras activas...")

        for camera in active_cameras:
            if not camera.rtsp_url:
                self._logger.warning(f"Cámara {camera.id} no tiene URL RTSP, omitiendo")
                continue
            # CRÍTICO: Pasar register_mjpeg=True para iniciar streaming automáticamente
            self.start_camera(camera, register_mjpeg=True)

    def stop_all(self) -> None:
        """Detiene todas las cámaras gestionadas."""
        camera_ids = list(self._workers.keys())

        self._logger.info(f"Deteniendo {len(camera_ids)} cámaras...")

        for camera_id in camera_ids:
            self.stop_camera(camera_id)

    def get_all_status(self) -> dict[int, dict]:
        """
        Obtiene el estado de todos los workers activos.

        Returns:
            Diccionario camera_id -> estado
        """
        with self._lock:
            return {
                camera_id: worker.get_status()
                for camera_id, worker in self._workers.items()
            }


if __name__ == "__main__":
    # Test básico del singleton
    cm1 = CameraManager()
    cm2 = CameraManager()
    assert cm1 is cm2, "Singleton no está funcionando correctamente"
    print("CameraManager Singleton: OK")