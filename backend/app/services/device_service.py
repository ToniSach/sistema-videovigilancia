"""
================================================================================
MÓDULO: device_service — Registro y autenticación de dispositivos móviles
================================================================================

PROPÓSITO
    Gestiona el ciclo de vida de los dispositivos móviles (app Android CamLink):
    alta/actualización, emisión y validación de refresh tokens, baja lógica y
    listado por usuario.

RESPONSABILIDAD PRINCIPAL
    - Registrar un móvil por su `device_uuid` (idempotente: si ya existe lo
      reactiva y reasigna al usuario en lugar de duplicar).
    - Emitir un par de tokens opacos (access + refresh) y persistir SOLO el
      hash SHA-256 del refresh (nunca el token en claro).
    - Validar el refresh token comparando hashes y refrescar `last_seen_at`.

NOTA IMPORTANTE
    Las notificaciones push por FCM/Firebase fueron ELIMINADAS del proyecto. El
    móvil recibe alertas en tiempo real por WebSocket en la LAN mientras esté
    conectado (ver WSNotificationBroker / Pipeline #13). Aquí no se guardan
    tokens FCM.

DEPENDENCIAS
    database.models ........ MobileDevice, User
    database.connection .... db_manager (una sesión por operación)
    config.settings ........ configuración global (importada para uso futuro)
    secrets / hashlib ...... generación de tokens y hashing

COMPONENTES RELACIONADOS
    - api.routes.devices / api.routes.mobile: exponen estos métodos como REST.
    - QRService: tras validar el QR, el móvil llama a register_device.
    - NotificationRouter: usa los dispositivos vivos para la entrega por WebSocket.

PUNTO DE ENTRADA
    Instancia `DeviceService()` desde las rutas; métodos autocontenidos.

PIPELINE(S)
    #13 Notificaciones (móvil) — etapa de provisión de dispositivos y de la
    sesión móvil (refresh token) sobre la que viaja la entrega por WebSocket.
================================================================================
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
        Propósito: alta o actualización idempotente de un dispositivo por su
            device_uuid, emitiendo un par de tokens nuevos.
        Inputs: user_id (dueño); device_uuid (id estable del aparato);
            device_name; platform ('android'/'ios'/…).
        Outputs: tupla (device desligado, access_token, refresh_token EN CLARO).
            El refresh en claro se devuelve UNA sola vez al cliente; en BD solo
            queda su hash SHA-256.
        Excepciones: re-lanza errores de BD tras loguear.
        Llamado por: ruta de registro/vinculación de dispositivos (tras QR).
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
        """
        Propósito: valida el refresh token de un móvil (compara hashes) para
            emitir un nuevo access token; de paso refresca `last_seen_at`.
        Inputs: device_uuid; refresh_token (en claro, recibido del cliente).
        Outputs: MobileDevice (desligado) si es válido y está activo; None si no
            existe, está inactivo o el hash no coincide.
        Excepciones: capturadas → devuelve None (no propaga).
        Llamado por: ruta de refresh de sesión móvil.
        """
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
        """
        Propósito: baja LÓGICA del dispositivo (is_active=False); deja de aceptar
            su refresh token y de recibir notificaciones. Inputs: device_id.
            Outputs: True si existía, False si no. Excepciones: re-lanza errores
            de BD. Llamado por: ruta de baja/cierre de sesión del móvil.
        """
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
        """
        Propósito: lista los dispositivos ACTIVOS de un usuario (desligados de la
            sesión). Inputs: user_id. Outputs: List[MobileDevice]. Excepciones:
            re-lanza errores de BD. Llamado por: ruta de listado de dispositivos.
        """
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