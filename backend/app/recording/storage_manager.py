"""
================================================================================
MÓDULO: storage_manager — Rotación y limpieza por cuota del almacenamiento
================================================================================

PROPÓSITO
    Mantener el uso de disco de las grabaciones por debajo de la cuota
    configurada (MAX_STORAGE_GB). Un hilo de fondo vigila el espacio usado y,
    cuando se supera el umbral, borra las grabaciones MÁS ANTIGUAS (política
    LRU por antigüedad) de forma atómica BD + filesystem.

RESPONSABILIDAD PRINCIPAL
    - Medir el espacio usado (vía RecordingRepository; fallback a walk del FS).
    - Ejecutar limpieza por cuota: al pasar del 90 %, borrar las más viejas
      hasta bajar al 70 %.
    - Borrado ATÓMICO con rollback (rename .deleting → delete BD → remove file).

DEPENDENCIAS IMPORTANTES
    RecordingRepository ... tamaño total, get_oldest(), get_by_id(), delete()
    settings .............. RECORDINGS_PATH, MAX_STORAGE_GB (leídos en caliente)

COMPONENTES RELACIONADOS
    - RecordingManager produce los archivos/filas que este módulo rota.
    - ConsistencyChecker (storage/) reconcilia FS↔BD; es complementario: este
      borra por CUOTA, aquel borra por INCONSISTENCIA.
    - Ruta REST storage.py expone get_stats() a la UI (polling de espacio).

PUNTO DE ENTRADA EN LA ARQUITECTURA
    main.py (paso 10 del arranque) construye StorageManager(recording_repo) y
    llama a start(); el finally de main.py llama a stop(). NO es singleton.

PIPELINE Y ETAPA
    #11 Grabación — fase de RETENCIÓN/rotación: corre en paralelo a la grabación
    para que el disco no se llene. No participa en la captura ni en los clips.

FLUJO DE LIMPIEZA (ASCII)
    _cleanup_loop (cada 600 s)
         │
         ▼
    run_cleanup(): usado >= 90 % cuota ?
         │ sí
         ▼
    mientras usado > 70 % cuota:
         get_oldest(20) ──▶ delete_recording_atomic(id)
                                 │ rename → .deleting
                                 │ delete fila BD  (rollback si falla)
                                 └ remove archivo físico
================================================================================
"""
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
    """
    Hilo de rotación/limpieza de grabaciones por cuota (etapa de retención #11).

    ROL
        Garantizar que las grabaciones no superen MAX_STORAGE_GB borrando las más
        antiguas. La ruta y la cuota se leen EN CALIENTE de settings, así que un
        cambio guardado desde la app de escritorio aplica sin reiniciar.

    QUIÉN LO INSTANCIA / CONSUME
        - Instancia y arranca: main.py (paso 10). NO es singleton `__new__`.
        - Consume: la ruta REST storage.py llama get_stats() para el panel de
          almacenamiento.

    DEPENDENCIAS
        RecordingRepository (medición y borrado en BD), settings (ruta+cuota).

    UMBRALES
        Limpia al superar el 90 % de la cuota y se detiene al bajar al 70 %
        (histéresis para no borrar en cada ciclo).
    """

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

    def get_used_space_bytes(self) -> int:
        """
        Devuelve el espacio total usado por las grabaciones, en bytes.

        Propósito: base de la decisión de limpieza. Usa el SUM de la BD (rápido);
            si falla, hace fallback a un os.walk del directorio de grabaciones.
        Inputs: ninguno.
        Outputs: int (bytes usados).
        Llamado por: run_cleanup() y get_stats().
        Llama a: recording_repo.get_total_size_bytes().
        """
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
        """
        Resumen de uso de almacenamiento para la UI (panel de almacenamiento).

        Propósito: exponer GB usados/máx/libres y % de uso.
        Inputs: ninguno.
        Outputs: dict con used_gb, max_gb, percent_used, free_gb.
        Llamado por: ruta REST storage.py (polling de la UI).
        Llama a: get_used_space_bytes().
        """
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
        Ejecuta UNA pasada de limpieza por cuota (LRU por antigüedad).

        Propósito (etapa de retención #11): si el uso supera el 90 % de la cuota,
            borrar grabaciones de la más vieja a la más nueva (en lotes de 20)
            con borrado atómico hasta bajar al 70 %.
        Inputs: ninguno.
        Outputs: int — número de grabaciones eliminadas (0 si no hizo falta).
        Excepciones: capturadas → devuelve 0.
        Llamado por: _cleanup_loop() cada CLEANUP_INTERVAL.
        Llama a: get_used_space_bytes(), recording_repo.get_oldest(),
            delete_recording_atomic().
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
        """
        Arranca el hilo daemon de limpieza periódica.

        Propósito: lanzar _cleanup_loop en segundo plano (idempotente).
        Inputs/Outputs: ninguno.
        Llamado por: main.py (paso 10 del arranque).
        Llama a: crea threading.Thread(target=_cleanup_loop).
        """
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._thread.start()
        logging.info("StorageManager iniciado")

    def _cleanup_loop(self) -> None:
        """Bucle del hilo: run_cleanup() cada CLEANUP_INTERVAL hasta stop()."""
        while self._running:
            try:
                self.run_cleanup()
            except Exception as e:
                logging.error(f"Error en loop de cleanup: {e}")
            time.sleep(self.CLEANUP_INTERVAL)

    def stop(self) -> None:
        """Detiene el hilo de limpieza (baja la bandera _running). Llamado por
        el finally de main.py en el apagado ordenado."""
        self._running = False
        logging.info("StorageManager detenido")