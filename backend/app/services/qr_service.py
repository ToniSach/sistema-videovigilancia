"""
Servicio de generación de tokens para login vía QR.
"""
import logging
import uuid
import qrcode
import io
import json
from datetime import datetime, timedelta
from typing import Optional, Tuple

from backend.app.database.models import LinkToken
from backend.app.database.connection import db_manager
from backend.app.services.device_service import DeviceService

logger = logging.getLogger(__name__)


class QRService:
    """Gestiona tokens de vinculación para login QR."""
    
    TOKEN_EXPIRY_MINUTES = 5
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.device_service = DeviceService()
    
    def generate_link_token(self, user_id: int, server_host: str, server_port: int) -> Tuple[str, bytes]:
        """
        Genera token de vinculación y código QR.
        
        Returns:
            Tupla (token_string, qr_image_bytes)
        """
        try:
            with db_manager.get_session() as session:
                # Invalidar tokens anteriores del usuario
                session.query(LinkToken).filter_by(
                    user_id=user_id, used=False
                ).update({"used": True})
                
                # Crear nuevo token
                token = str(uuid.uuid4())
                link_token = LinkToken(
                    token=token,
                    user_id=user_id,
                    expires_at=datetime.utcnow() + timedelta(minutes=self.TOKEN_EXPIRY_MINUTES),
                    used=False
                )
                session.add(link_token)
                
                # Generar datos para QR
                qr_data = {
                    "server": f"http://{server_host}:{server_port}",
                    "link_token": token,
                    "version": "1.0"
                }
                
                # Generar imagen QR
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_H,
                    box_size=10,
                    border=4,
                )
                qr.add_data(json.dumps(qr_data))
                qr.make(fit=True)
                
                img = qr.make_image(fill_color="black", back_color="white")
                buffer = io.BytesIO()
                img.save(buffer, format='PNG')
                qr_bytes = buffer.getvalue()
                
                self.logger.info(f"Link token generado para user {user_id}")
                return token, qr_bytes
                
        except Exception as e:
            self.logger.error(f"Error generando QR: {e}")
            raise
    
    def validate_link_token(self, token: str) -> Optional[int]:
        """
        Valida token de vinculación.
        
        Returns:
            user_id si válido, None si inválido
        """
        try:
            with db_manager.get_session() as session:
                link_token = session.query(LinkToken).filter_by(
                    token=token,
                    used=False
                ).first()
                
                if not link_token:
                    return None
                
                if link_token.expires_at < datetime.utcnow():
                    return None
                
                user_id = link_token.user_id
                session.expunge(link_token)
                return user_id
                
        except Exception as e:
            self.logger.error(f"Error validando token: {e}")
            return None
    
    def consume_link_token(self, token: str) -> Optional[int]:
        """
        Consume token (marca como usado) y retorna user_id.
        """
        try:
            with db_manager.get_session() as session:
                link_token = session.query(LinkToken).filter_by(
                    token=token,
                    used=False
                ).first()
                
                if not link_token or link_token.expires_at < datetime.utcnow():
                    return None
                
                link_token.used = True
                user_id = link_token.user_id
                return user_id
                
        except Exception as e:
            self.logger.error(f"Error consumiendo token: {e}")
            return None