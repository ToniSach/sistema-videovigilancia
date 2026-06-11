# backend/app/services/ptz_lock_service.py
"""
================================================================================
MÓDULO: services.ptz_lock_service — Exclusión mutua del control PTZ (Pipeline #8)
================================================================================

PROPÓSITO
    Lock en memoria que garantiza que SOLO UN usuario controle el PTZ de una
    cámara a la vez. Evita "tirones" cuando dos operadores intentan mover la
    misma cámara simultáneamente.

RESPONSABILIDAD PRINCIPAL
    Conceder/liberar/forzar un lock por cámara con: timeout automático (30s),
    reentrancia por usuario (contador), extensión de tiempo y limpieza periódica
    de locks expirados en un hilo de fondo. NO mueve la cámara: eso lo hace
    CameraService.ptz_control; este servicio solo arbitra el acceso.

ESTADO Y CONCURRENCIA
    Estado vivo en memoria de proceso (_locks, _lock_counts) protegido por un
    RLock. Singleton de módulo `ptz_lock_service`. Como vive en memoria, encaja
    en la restricción de proceso único del backend (no se comparte entre workers).

DEPENDENCIAS
    Solo stdlib: threading (RLock + hilo de limpieza), datetime/timedelta.

COMPONENTES RELACIONADOS
    Lo INSTANCIA: este propio módulo, como singleton global al importar.
    Lo CONSUME: blueprint `cameras_bp` (api/routes/cameras.py) en los endpoints
        PTZ: adquiere el lock ANTES de llamar a CameraService.ptz_control y lo
        libera/expande según corresponda; el admin puede forzar la liberación.

PUNTO DE ENTRADA
    `ptz_lock_service.acquire_lock(camera_id, user_id, username)` (instancia
    global ya creada al final del módulo).

PIPELINE(S)
    #8 PTZ — etapa de control de concurrencia: gate previo al movimiento real.
================================================================================
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
    """Tenencia actual de un lock PTZ: quién lo tiene y hasta cuándo es válido."""
    user_id: int
    username: str
    acquired_at: datetime
    expires_at: datetime


class PTZLockService:
    """
    Lock de control PTZ en memoria de proceso (capa del Pipeline #8).

    Rol: árbitro de concurrencia para el movimiento de cámaras. No es un lock
    "distribuido" real (vive en un solo proceso), lo cual es suficiente porque el
    backend corre como proceso único.
    Garantías:
      - Timeout automático: 30s (un cliente que se cuelga libera la cámara solo).
      - Liberación explícita por el mismo usuario que lo tomó.
      - Reentrancia: el mismo usuario puede readquirir (contador); se libera
        cuando el contador llega a 0.

    Lo instancia: el módulo (singleton global `ptz_lock_service`).
    Lo consume: cameras_bp (endpoints PTZ).
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
        Intenta adquirir el lock PTZ de una cámara (gate previo al movimiento).

        Casos: (a) el mismo usuario ya lo tiene → reentrancia (incrementa
        contador, True); (b) otro usuario lo tiene y sigue vigente → rechazo con
        mensaje y segundos restantes; (c) lock libre o expirado → lo concede.

        Inputs: camera_id, user_id, username, timeout_seconds (default 30s).
        Outputs: (success: bool, error_message: str|None). El mensaje describe
            quién tiene la cámara y cuánto falta, para mostrarlo en la UI.
        Llamado por: endpoint PTZ de cameras_bp ANTES de CameraService.ptz_control.
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
        """
        Libera el lock PTZ decrementando el contador de reentrancia; solo cuando
        llega a 0 se elimina realmente. Solo el dueño del lock puede liberarlo.

        Outputs: True si el solicitante era el dueño; False si no lo tenía.
        Llamado por: endpoint PTZ de cameras_bp tras terminar el movimiento.
        """
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
        """Renueva el vencimiento del lock (keep-alive durante un control PTZ
        sostenido). Solo el dueño puede extenderlo. Outputs: True si se extendió.
        Llamado por: heartbeat del cliente mientras mantiene el PTZ activo."""
        with self._mutex:
            if camera_id not in self._locks:
                return False
            if self._locks[camera_id].user_id != user_id:
                return False
            self._locks[camera_id].expires_at = datetime.now() + timedelta(seconds=extra_seconds)
            return True
    
    def get_lock_status(self, camera_id: int) -> Optional[dict]:
        """Estado del lock de una cámara para la UI (quién, desde cuándo, cuánto
        queda). Outputs: dict o None si la cámara no está bloqueada.
        Llamado por: endpoint de estado PTZ de cameras_bp (polling de la UI)."""
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
        """
        Libera el lock SIN ser el dueño ni respetar la reentrancia (override de
        admin para destrabar una cámara cuyo dueño no la suelta).

        Outputs: True si había un lock que liberar; False si no.
        Llamado por: endpoint admin de force-unlock (cameras_bp); la autorización
            de admin se valida en la capa de ruta, no aquí.
        """
        with self._mutex:
            if camera_id in self._locks:
                logger.warning(f"Admin {admin_user_id} forzó liberación de lock cámara {camera_id}")
                del self._locks[camera_id]
                self._lock_counts.pop(camera_id, None)
                return True
            return False
    
    def _cleanup_expired(self):
        """Hilo de fondo (daemon): cada 10s purga locks ya vencidos. Segunda
        línea de defensa frente al timeout — libera la cámara aunque el cliente
        nunca llame a release_lock (se desconectó/crasheó)."""
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
        """Detiene el hilo de limpieza (apagado ordenado del backend)."""
        self._cleanup_running = False


# Instancia global (singleton de módulo): los endpoints PTZ importan ESTA
# instancia para que el estado de los locks sea compartido en todo el proceso.
ptz_lock_service = PTZLockService()