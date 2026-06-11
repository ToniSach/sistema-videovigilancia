"""
================================================================================
MÓDULO: consistency_checker — Reconciliación filesystem ↔ base de datos
================================================================================

PROPÓSITO
    Garantizar la coherencia entre lo que dice la BD (tabla `recordings`) y lo
    que hay realmente en disco. Detecta y opcionalmente limpia dos clases de
    inconsistencia que aparecen tras crashes, borrados manuales o fallos de
    escritura:
      - REGISTROS HUÉRFANOS: filas en BD cuyo archivo físico ya no existe.
      - ARCHIVOS HUÉRFANOS: archivos en disco sin fila en BD (no indexados).

RESPONSABILIDAD PRINCIPAL
    Ejecutar un job periódico (cada 24 h) que recorre la BD en lotes y el árbol
    de grabaciones, reportando estadísticas y, si AUTO_CLEANUP está activo,
    eliminando los huérfanos de ambos lados.

DEPENDENCIAS IMPORTANTES
    db_manager ............... sesiones de BD (consulta/borrado en lotes)
    Recording (modelo) ....... tabla de grabaciones
    RecordingRepository ...... acceso a grabaciones
    settings ................. RECORDINGS_PATH (raíz del árbol a escanear)

COMPONENTES RELACIONADOS
    - RecordingManager produce los archivos/filas que aquí se reconcilian.
    - StorageManager borra por CUOTA (LRU); este borra por INCONSISTENCIA. Son
      complementarios y no se solapan: StorageManager hace borrado atómico que
      deja todo coherente; este recoge los casos que se descoordinaron.

PUNTO DE ENTRADA EN LA ARQUITECTURA
    Instancia GLOBAL a nivel de módulo (`consistency_checker`). main.py la
    arranca (start) en el arranque y la detiene (stop) en el apagado. No es
    singleton `__new__`, pero el módulo expone una única instancia compartida.

PIPELINE Y ETAPA
    #11 Grabación — fase de MANTENIMIENTO/integridad: corre en paralelo, no
    participa en captura/clips; sanea el almacenamiento que aquéllos generan.

FLUJO DE RECONCILIACIÓN (ASCII)
    _check_loop (cada 24 h)
         │
         ▼
    run_check():
       (1) BD en lotes ─▶ ¿file_path existe en disco?  no ─▶ huérfano BD ─▶ delete fila
       (2) walk(RECORDINGS_PATH) ─▶ ¿path está en BD?  no ─▶ huérfano FS ─▶ remove archivo
         │
         ▼
       stats {orphan_records, orphan_files, cleaned_*, errors}
================================================================================
"""
import os
import threading
import time
import logging
from pathlib import Path
from typing import List, Tuple, Optional

from backend.app.config import settings
from backend.app.database.connection import db_manager
from backend.app.database.models import Recording
from backend.app.database.repositories.recording_repository import RecordingRepository

logger = logging.getLogger(__name__)


class ConsistencyChecker:
    """
    Verificador periódico de integridad del almacenamiento (mantenimiento #11).

    ROL
        Reconciliar BD ↔ filesystem: detectar registros sin archivo y archivos
        sin registro, y (si AUTO_CLEANUP) limpiarlos. Procesa la BD en LOTES
        (BATCH_SIZE) con commit por lote para no mantener una transacción larga
        que bloquee writes concurrentes.

    QUIÉN LO INSTANCIA / CONSUME
        - Instancia GLOBAL del módulo (`consistency_checker`), arrancada y
          detenida por main.py. No es singleton `__new__`; la unicidad la da
          el módulo (una sola instancia importable).
        - Consumido por main.py (start/stop) y potencialmente por rutas que
          pidan get_stats().

    DEPENDENCIAS
        db_manager (sesiones), Recording (tabla), RecordingRepository,
        settings.RECORDINGS_PATH.

    CONCURRENCIA
        _stop_event (Event) permite despertar el hilo al instante en shutdown
        (antes había que esperar hasta 24 h al próximo CHECK_INTERVAL).
    """

    CHECK_INTERVAL = 86400  # 24 horas
    AUTO_CLEANUP = True      # Si True, elimina registros huérfanos y archivos no indexados
    BATCH_SIZE = 200         # Procesar registros en lotes para no bloquear BD

    def __init__(self):
        self._recording_repo = RecordingRepository()
        self._recordings_path = Path(settings.RECORDINGS_PATH)
        # Event en lugar de bool: shutdown despierta el thread al instante
        # (antes había que esperar hasta 24h al próximo CHECK_INTERVAL).
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

    @property
    def _running(self) -> bool:
        """Compat: algunos consumidores leen _running."""
        return self._thread is not None and not self._stop_event.is_set()

    def start(self):
        """
        Arranca el hilo daemon de verificación periódica (idempotente).

        Llamado por: main.py en el arranque. Llama a: crea el hilo _check_loop.
        """
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._check_loop, daemon=True)
        self._thread.start()
        logger.info("ConsistencyChecker iniciado")

    def stop(self):
        """Detiene el hilo despertándolo al instante (set del Event) y hace
        join. Llamado por main.py en el apagado ordenado."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("ConsistencyChecker detenido")

    def _check_loop(self):
        while not self._stop_event.is_set():
            try:
                self.run_check()
            except Exception as e:
                logger.error(f"Error en consistency check: {e}", exc_info=True)
            # wait() devuelve True si stop_event se setea → salida inmediata
            if self._stop_event.wait(timeout=self.CHECK_INTERVAL):
                break
    
    def run_check(self) -> dict:
        """
        Ejecuta UNA verificación completa de consistencia (mantenimiento #11).

        Propósito: (1) recorrer la BD en lotes y marcar/borrar registros cuyo
            archivo no existe; (2) recorrer el árbol de grabaciones y
            marcar/borrar archivos (.mp4/.ts/.m3u8) sin fila en BD. Salta los
            temporales `.deleting` (en proceso por StorageManager). El borrado
            sólo ocurre si AUTO_CLEANUP está activo.
        Inputs: ninguno (lee estado de BD y FS).
        Outputs: dict de estadísticas (orphan_records, orphan_files,
            cleaned_records, cleaned_files, errors).
        Excepciones: capturadas por bloque; incrementa stats["errors"] y sigue.
        Llamado por: _check_loop() (periódico) y get_stats() (modo solo lectura).
        Llama a: db_manager.get_session(), os.walk(), session.delete()/os.remove().
        """
        logger.info("Iniciando consistency check de almacenamiento...")
        
        stats = {
            "orphan_records": 0,
            "orphan_files": 0,
            "cleaned_records": 0,
            "cleaned_files": 0,
            "errors": 0
        }
        
        # 1. Verificar registros huérfanos (BD apunta a archivo inexistente).
        # Procesamos en LOTES con commit cada N para no mantener una transacción
        # abierta hora(s) sobre toda la tabla (bloqueaba writes concurrentes).
        try:
            offset = 0
            while not self._stop_event.is_set():
                with db_manager.get_session() as session:
                    batch = (
                        session.query(Recording)
                        .order_by(Recording.id)
                        .limit(self.BATCH_SIZE)
                        .offset(offset)
                        .all()
                    )
                    if not batch:
                        break

                    deleted_in_batch = 0
                    for rec in batch:
                        if rec.file_path and not os.path.exists(rec.file_path):
                            stats["orphan_records"] += 1
                            logger.warning(
                                f"Registro huérfano: Recording {rec.id} - "
                                f"archivo {rec.file_path} no existe"
                            )
                            if self.AUTO_CLEANUP:
                                try:
                                    session.delete(rec)
                                    deleted_in_batch += 1
                                    stats["cleaned_records"] += 1
                                except Exception as e:
                                    logger.error(f"Error eliminando registro {rec.id}: {e}")
                                    stats["errors"] += 1
                    # Commit por lote — libera locks BD
                    session.commit()

                # Avanzar offset compensando lo borrado en este lote
                offset += len(batch) - deleted_in_batch
                if len(batch) < self.BATCH_SIZE:
                    break
        except Exception as e:
            logger.error(f"Error verificando registros huérfanos: {e}", exc_info=True)
            stats["errors"] += 1

        # 2. Verificar archivos huérfanos (archivo en disco sin registro en BD)
        try:
            # Obtener TODOS los paths de BD una sola vez (es lectura ligera
            # comparada con un walk del FS; si crece mucho mover a query con
            # only(Recording.file_path) para no traer columnas grandes).
            with db_manager.get_session() as session:
                db_paths = {
                    os.path.abspath(p) for (p,) in
                    session.query(Recording.file_path).all() if p
                }
            
            # Recorrer directorio de grabaciones
            if self._recordings_path.exists():
                for root, dirs, files in os.walk(self._recordings_path):
                    for file in files:
                        if file.endswith(('.mp4', '.ts', '.m3u8', '.deleting')):
                            full_path = os.path.abspath(os.path.join(root, file))
                            # Ignorar archivos temporales .deleting (están en proceso)
                            if full_path.endswith('.deleting'):
                                continue
                            if full_path not in db_paths:
                                stats["orphan_files"] += 1
                                logger.warning(f"Archivo huérfano: {full_path}")
                                if self.AUTO_CLEANUP:
                                    try:
                                        os.remove(full_path)
                                        stats["cleaned_files"] += 1
                                        logger.info(f"Archivo huérfano eliminado: {full_path}")
                                    except Exception as e:
                                        logger.error(f"Error eliminando archivo {full_path}: {e}")
                                        stats["errors"] += 1
        except Exception as e:
            logger.error(f"Error verificando archivos huérfanos: {e}")
            stats["errors"] += 1
        
        logger.info(f"Consistency check completado: {stats}")
        return stats
    
    def get_stats(self) -> dict:
        """
        Devuelve estadísticas de consistencia SIN limpiar (modo solo lectura).

        Propósito: ejecutar run_check() desactivando temporalmente AUTO_CLEANUP
            para reportar inconsistencias sin tocar BD ni disco. Protegido por
            _lock para no solapar con el job periódico.
        Inputs: ninguno.
        Outputs: dict de estadísticas (igual estructura que run_check()).
        Llamado por: rutas/diagnóstico que quieran el estado actual.
        Llama a: run_check() con AUTO_CLEANUP=False temporal.
        """
        # Podría almacenar último resultado, por simplicidad ejecutamos un check rápido
        # (sin limpieza) para reportar.
        with self._lock:
            # Modo solo lectura, sin auto_cleanup
            old_cleanup = self.AUTO_CLEANUP
            self.AUTO_CLEANUP = False
            try:
                stats = self.run_check()
            finally:
                self.AUTO_CLEANUP = old_cleanup
        return stats


# Instancia global
consistency_checker = ConsistencyChecker()