"""
Servicio de gestión de dispositivos móviles.
"""
import logging
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List

from backend.app.database.models import MobileDevice, User
from backend.app.database.connection import db_manager
from backend.app.config import settings

logger = logging.getLogger(__name__)


class DeviceService:
    """Gestiona dispositivos móviles (autenticación por refresh token).

    NOTA: las notificaciones push por FCM/Firebase fueron eliminadas del
    proyecto. El móvil recibe alertas en tiempo real por WebSocket en la LAN
    (NotificationWebSocketService) mientras esté conectado.
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def register_device(self, user_id: int, device_uuid: str, device_name: str,
                       platform: str) -> tuple[MobileDevice, str, str]:
        """
        Registra un nuevo dispositivo o actualiza uno existente.

        Returns:
            Tupla (device, access_token, refresh_token)
        """
        try:
            with db_manager.get_session() as session:
                # Buscar dispositivo existente
                device = session.query(MobileDevice).filter_by(device_uuid=device_uuid).first()

                if device:
                    # Actualizar
                    device.user_id = user_id
                    device.is_active = True
                    device.last_seen_at = datetime.utcnow()
                else:
                    # Crear nuevo
                    device = MobileDevice(
                        user_id=user_id,
                        device_uuid=device_uuid,
                        device_name=device_name,
                        platform=platform,
                        is_active=True
                    )
                    session.add(device)
                
                # Generar tokens
                refresh_token = secrets.token_urlsafe(32)
                access_token = secrets.token_urlsafe(32)
                
                # Hash del refresh token para almacenar
                refresh_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
                device.refresh_token_hash = refresh_hash
                
                session.flush()
                session.expunge(device)
                
                self.logger.info(f"Dispositivo registrado: {device_uuid} para user {user_id}")
                return device, access_token, refresh_token
                
        except Exception as e:
            self.logger.error(f"Error registrando dispositivo: {e}")
            raise
    
    def validate_refresh_token(self, device_uuid: str, refresh_token: str) -> Optional[MobileDevice]:
        """Valida refresh token y retorna dispositivo."""
        try:
            with db_manager.get_session() as session:
                device = session.query(MobileDevice).filter_by(device_uuid=device_uuid).first()
                
                if not device or not device.is_active:
                    return None
                
                # Verificar hash
                refresh_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
                if device.refresh_token_hash != refresh_hash:
                    return None
                
                # Actualizar last_seen
                device.last_seen_at = datetime.utcnow()
                session.flush()
                session.expunge(device)
                
                return device
        except Exception as e:
            self.logger.error(f"Error validando refresh token: {e}")
            return None
    
    def deactivate_device(self, device_id: int) -> bool:
        """Desactiva dispositivo."""
        try:
            with db_manager.get_session() as session:
                device = session.get(MobileDevice, device_id)
                if device:
                    device.is_active = False
                    return True
                return False
        except Exception as e:
            self.logger.error(f"Error desactivando dispositivo: {e}")
            raise
    
    def get_user_devices(self, user_id: int) -> List[MobileDevice]:
        """Obtiene dispositivos de un usuario."""
        try:
            with db_manager.get_session() as session:
                devices = session.query(MobileDevice).filter_by(
                    user_id=user_id, is_active=True
                ).all()
                for d in devices:
                    session.expunge(d)
                return devices
        except Exception as e:
            self.logger.error(f"Error obteniendo dispositivos: {e}")
            raise