import os
import threading
import time
import logging
from typing import Optional

from ..database.repositories.recording_repository import RecordingRepository
from ..config import settings


class StorageManager:
    CLEANUP_INTERVAL = 600  # 10 minutos

    def __init__(self, recording_repo: RecordingRepository):
        self._recording_repo = recording_repo
        self._recordings_path = settings.RECORDINGS_PATH
        self._max_size_bytes = int(settings.MAX_STORAGE_GB * 1024**3)
        self._running = False
        self._thread: Optional[threading.Thread] = None

        logging.info(f"StorageManager inicializado: max={settings.MAX_STORAGE_GB}GB")

    def get_used_space_bytes(self) -> int:
        total_size = 0
        try:
            if os.path.exists(self._recordings_path):
                for dirpath, dirnames, filenames in os.walk(self._recordings_path):
                    for f in filenames:
                        fp = os.path.join(dirpath, f)
                        if not os.path.islink(fp):
                            total_size += os.path.getsize(fp)
        except Exception as e:
            logging.error(f"Error calculando espacio usado: {e}")
        return total_size

    def get_stats(self) -> dict:
        used = self.get_used_space_bytes()
        max_gb = settings.MAX_STORAGE_GB
        used_gb = used / (1024**3)
        percent = (used / self._max_size_bytes * 100) if self._max_size_bytes > 0 else 0

        return {
            "used_gb": round(used_gb, 2),
            "max_gb": max_gb,
            "percent_used": round(percent, 2),
            "free_gb": round(max_gb - used_gb, 2)
        }

    def run_cleanup(self) -> int:
        try:
            used_bytes = self.get_used_space_bytes()
            threshold = self._max_size_bytes * 0.90

            if used_bytes < threshold:
                return 0

            logging.warning(f"Almacenamiento alto: {used_bytes/1024**3:.2f}GB usados. Iniciando limpieza...")

            deleted_count = 0
            target_size = self._max_size_bytes * 0.70

            while self.get_used_space_bytes() > target_size:
                old_recordings = self._recording_repo.get_oldest(count=20)

                if not old_recordings:
                    break

                for recording in old_recordings:
                    try:
                        if recording.file_path and os.path.exists(recording.file_path):
                            os.remove(recording.file_path)
                            logging.info(f"Archivo eliminado: {recording.file_path}")

                        self._recording_repo.delete(recording.id)
                        deleted_count += 1

                    except Exception as e:
                        logging.error(f"Error eliminando grabación {recording.id}: {e}")

                time.sleep(0.1)

            final_used = self.get_used_space_bytes()
            logging.info(f"Limpieza completada. Eliminados: {deleted_count}. "
                        f"Espacio ahora: {final_used/1024**3:.2f}GB")
            return deleted_count

        except Exception as e:
            logging.error(f"Error en cleanup: {e}")
            return 0

    def start(self) -> None:
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._thread.start()
        logging.info("StorageManager iniciado")

    def _cleanup_loop(self) -> None:
        while self._running:
            try:
                self.run_cleanup()
            except Exception as e:
                logging.error(f"Error en loop de cleanup: {e}")

            time.sleep(self.CLEANUP_INTERVAL)

    def stop(self) -> None:
        self._running = False
        logging.info("StorageManager detenido")
