"""
Storage Consistency Checker - Reconciliación entre base de datos y filesystem.
Ejecuta jobs periódicos para detectar y limpiar inconsistencias.
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
    Verifica periódicamente la integridad del almacenamiento:
    - Registros en BD sin archivo físico (huérfanos)
    - Archivos físicos sin registro en BD (no indexados)
    Opcionalmente limpia automáticamente.
    """
    
    CHECK_INTERVAL = 86400  # 24 horas
    AUTO_CLEANUP = True      # Si True, elimina registros huérfanos y archivos no indexados
    
    def __init__(self):
        self._recording_repo = RecordingRepository()
        self._recordings_path = Path(settings.RECORDINGS_PATH)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        
    def start(self):
        """Inicia el checker en segundo plano."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._check_loop, daemon=True)
        self._thread.start()
        logger.info("ConsistencyChecker iniciado")
    
    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("ConsistencyChecker detenido")
    
    def _check_loop(self):
        while self._running:
            try:
                self.run_check()
            except Exception as e:
                logger.error(f"Error en consistency check: {e}")
            time.sleep(self.CHECK_INTERVAL)
    
    def run_check(self) -> dict:
        """
        Ejecuta una verificación completa.
        Retorna estadísticas de lo encontrado.
        """
        logger.info("Iniciando consistency check de almacenamiento...")
        
        stats = {
            "orphan_records": 0,
            "orphan_files": 0,
            "cleaned_records": 0,
            "cleaned_files": 0,
            "errors": 0
        }
        
        # 1. Verificar registros huérfanos (BD apunta a archivo inexistente)
        try:
            with db_manager.get_session() as session:
                recordings = session.query(Recording).all()
                for rec in recordings:
                    if rec.file_path and not os.path.exists(rec.file_path):
                        stats["orphan_records"] += 1
                        logger.warning(f"Registro huérfano: Recording {rec.id} - archivo {rec.file_path} no existe")
                        if self.AUTO_CLEANUP:
                            try:
                                session.delete(rec)
                                stats["cleaned_records"] += 1
                                logger.info(f"Registro huérfano eliminado: {rec.id}")
                            except Exception as e:
                                logger.error(f"Error eliminando registro {rec.id}: {e}")
                                stats["errors"] += 1
                session.commit()
        except Exception as e:
            logger.error(f"Error verificando registros huérfanos: {e}")
            stats["errors"] += 1
        
        # 2. Verificar archivos huérfanos (archivo en disco sin registro en BD)
        try:
            # Obtener todos los paths de grabaciones desde BD
            with db_manager.get_session() as session:
                db_paths = set()
                recordings = session.query(Recording).all()
                for rec in recordings:
                    if rec.file_path:
                        db_paths.add(os.path.abspath(rec.file_path))
            
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
        """Retorna estadísticas actuales (última ejecución o en tiempo real)."""
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