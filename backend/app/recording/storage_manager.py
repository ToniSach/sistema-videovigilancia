import os
import threading
import time
import logging
from typing import Optional

# FIX F0.3: Agregar imports para path seguro
from pathlib import Path

from ..database.repositories.recording_repository import RecordingRepository
from ..config import settings


class StorageManager:
    CLEANUP_INTERVAL = 600  # 10 minutos

    def __init__(self, recording_repo: RecordingRepository):
        self._recording_repo = recording_repo
        self._running = False
        self._thread: Optional[threading.Thread] = None

        logging.info(f"StorageManager inicializado: max={settings.MAX_STORAGE_GB}GB")

    # Ruta y cuota se leen EN CALIENTE desde settings (no se cachean), para que
    # un cambio guardado desde la app de escritorio aplique sin reiniciar.
    @property
    def _recordings_path(self) -> str:
        return settings.RECORDINGS_PATH

    @property
    def _max_size_bytes(self) -> int:
        return int(settings.MAX_STORAGE_GB * 1024 ** 3)

    # Usar repositorio para calcular tamaño total
    def get_used_space_bytes(self) -> int:
        try:
            return self._recording_repo.get_total_size_bytes()
        except Exception as e:
            logging.error(f"Error obteniendo tamaño desde BD: {e}")
            total = 0
            if os.path.exists(self._recordings_path):
                for dirpath, dirnames, filenames in os.walk(self._recordings_path):
                    for f in filenames:
                        fp = os.path.join(dirpath, f)
                        if not os.path.islink(fp):
                            total += os.path.getsize(fp)
            return total

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

    # FIX F0.3: Método de eliminación atómica con rollback
    def delete_recording_atomic(self, recording_id: int) -> bool:
        """
        Elimina grabación de forma atómica (BD + filesystem).
        
        Patrón:
        1. Renombrar archivo a .deleting (marca de eliminación en progreso)
        2. Borrar registro de BD (transacción)
        3. Si éxito: eliminar archivo físico
        4. Si fallo: restaurar nombre original (rollback)
        """
        try:
            recording = self._recording_repo.get_by_id(recording_id)
            if not recording:
                logging.warning(f"Grabación {recording_id} no existe en BD")
                return False
            
            file_path = recording.file_path
            if not file_path or not os.path.exists(file_path):
                # Archivo ya no existe, solo limpiar BD
                self._recording_repo.delete(recording_id)
                return True
            
            # 1. Renombrar a .deleting (operación atómica en FS)
            deleting_path = file_path + ".deleting"
            try:
                os.rename(file_path, deleting_path)
                logging.info(f"Archivo marcado para eliminación: {deleting_path}")
            except OSError as e:
                logging.error(f"No se pudo renombrar archivo para eliminación: {e}")
                return False
            
            # 2. Intentar borrar de BD
            try:
                self._recording_repo.delete(recording_id)
                logging.info(f"Registro {recording_id} eliminado de BD")
            except Exception as e:
                # Rollback: restaurar archivo
                logging.error(f"Error eliminando de BD, haciendo rollback: {e}")
                try:
                    os.rename(deleting_path, file_path)
                    logging.info(f"Rollback exitoso: {file_path} restaurado")
                except OSError as rollback_error:
                    logging.critical(f"ROLLBACK FALLIDO: {file_path} está en {deleting_path}")
                return False
            
            # 3. Éxito en BD, eliminar físicamente
            try:
                os.remove(deleting_path)
                logging.info(f"Archivo físico eliminado: {deleting_path}")
                return True
            except OSError as e:
                # El registro ya no existe, pero el archivo .deleting queda
                # Un job de limpieza posterior debe encargarse de estos
                logging.warning(f"Archivo marcado .deleting no pudo eliminarse: {e}")
                return True  # Desde perspectiva BD, la operación fue exitosa
                
        except Exception as e:
            logging.error(f"Error en eliminación atómica: {e}")
            return False

    def run_cleanup(self) -> int:
        """
        Ejecuta limpieza de almacenamiento usando eliminación atómica.
        """
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
                    # FIX F0.3: Usar método atómico en lugar de os.remove directo
                    if self.delete_recording_atomic(recording.id):
                        deleted_count += 1
                    time.sleep(0.1)  # Evitar saturar I/O

            final_used = self.get_used_space_bytes()
            logging.info(f"Limpieza completada. Eliminados: {deleted_count}. Espacio ahora: {final_used/1024**3:.2f}GB")
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