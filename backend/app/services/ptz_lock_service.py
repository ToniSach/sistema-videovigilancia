# backend/app/services/ptz_lock_service.py
"""
PTZ Lock Service - Control de exclusión mutua para PTZ.
Evita que múltiples usuarios controlen la misma cámara simultáneamente.
"""
import threading
import time
import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class LockInfo:
    user_id: int
    username: str
    acquired_at: datetime
    expires_at: datetime


class PTZLockService:
    """
    Servicio de bloqueo distribuido en memoria para control PTZ.
    - Timeout automático: 30 segundos
    - Liberación explícita por usuario
    - Reentrancia: mismo usuario puede adquirir múltiples veces (contador)
    """
    
    DEFAULT_TIMEOUT = 30  # segundos
    
    def __init__(self):
        self._locks: Dict[int, LockInfo] = {}
        self._lock_counts: Dict[int, int] = {}  # camera_id -> contador de reentrancia
        self._mutex = threading.RLock()
        self._cleanup_running = True
        self._cleanup_thread = threading.Thread(target=self._cleanup_expired, daemon=True)
        self._cleanup_thread.start()
    
    def acquire_lock(self, camera_id: int, user_id: int, username: str, 
                     timeout_seconds: int = None) -> Tuple[bool, Optional[str]]:
        """
        Intenta adquirir el lock PTZ para una cámara.
        Returns:
            (success, error_message)
        """
        if timeout_seconds is None:
            timeout_seconds = self.DEFAULT_TIMEOUT
        
        with self._mutex:
            now = datetime.now()
            
            # Reentrancia: mismo usuario ya tiene el lock
            if camera_id in self._locks and self._locks[camera_id].user_id == user_id:
                self._lock_counts[camera_id] = self._lock_counts.get(camera_id, 0) + 1
                logger.debug(f"Usuario {username} re-adquirió lock PTZ cámara {camera_id}")
                return True, None
            
            # Otro usuario tiene el lock
            if camera_id in self._locks:
                current = self._locks[camera_id]
                if now < current.expires_at:
                    remaining = (current.expires_at - now).seconds
                    return False, f"Cámara en uso por {current.username} ({remaining}s restantes)"
                else:
                    # Lock expirado, liberar
                    del self._locks[camera_id]
                    self._lock_counts.pop(camera_id, None)
            
            # Adquirir nuevo lock
            self._locks[camera_id] = LockInfo(
                user_id=user_id,
                username=username,
                acquired_at=now,
                expires_at=now + timedelta(seconds=timeout_seconds)
            )
            self._lock_counts[camera_id] = 1
            
            logger.info(f"Lock PTZ adquirido: cámara {camera_id} por {username}")
            return True, None
    
    def release_lock(self, camera_id: int, user_id: int) -> bool:
        """Libera el lock PTZ (decrementa contador de reentrancia)."""
        with self._mutex:
            if camera_id not in self._locks:
                return False
            if self._locks[camera_id].user_id != user_id:
                return False
            
            self._lock_counts[camera_id] -= 1
            if self._lock_counts[camera_id] <= 0:
                del self._locks[camera_id]
                del self._lock_counts[camera_id]
                logger.info(f"Lock PTZ liberado: cámara {camera_id}")
            else:
                logger.debug(f"Lock PTZ decrementado (reentrancia): cámara {camera_id}")
            return True
    
    def extend_lock(self, camera_id: int, user_id: int, extra_seconds: int = 30) -> bool:
        """Extiende el tiempo de un lock existente."""
        with self._mutex:
            if camera_id not in self._locks:
                return False
            if self._locks[camera_id].user_id != user_id:
                return False
            self._locks[camera_id].expires_at = datetime.now() + timedelta(seconds=extra_seconds)
            return True
    
    def get_lock_status(self, camera_id: int) -> Optional[dict]:
        """Retorna estado del lock para una cámara."""
        with self._mutex:
            if camera_id not in self._locks:
                return None
            info = self._locks[camera_id]
            now = datetime.now()
            remaining = max(0, (info.expires_at - now).seconds)
            return {
                "locked_by": info.username,
                "user_id": info.user_id,
                "acquired_at": info.acquired_at.isoformat(),
                "remaining_seconds": remaining
            }
    
    def force_unlock(self, camera_id: int, admin_user_id: int) -> bool:
        """Fuerza liberación de lock (solo admin)."""
        with self._mutex:
            if camera_id in self._locks:
                logger.warning(f"Admin {admin_user_id} forzó liberación de lock cámara {camera_id}")
                del self._locks[camera_id]
                self._lock_counts.pop(camera_id, None)
                return True
            return False
    
    def _cleanup_expired(self):
        """Limpia locks expirados periódicamente."""
        while self._cleanup_running:
            time.sleep(10)
            with self._mutex:
                now = datetime.now()
                expired = [cid for cid, info in self._locks.items() if now > info.expires_at]
                for cid in expired:
                    logger.warning(f"Lock PTZ expirado automáticamente: cámara {cid}")
                    del self._locks[cid]
                    self._lock_counts.pop(cid, None)
    
    def shutdown(self):
        self._cleanup_running = False


# Instancia global
ptz_lock_service = PTZLockService()