"""
Servicio de vinculación de cuentas Telegram mediante códigos temporales.
"""
import logging
import random
import string
from datetime import datetime, timedelta
from typing import Optional

from backend.app.database.models import TelegramVerificationCode, UserTelegramChat
from backend.app.database.connection import db_manager

logger = logging.getLogger(__name__)


class TelegramLinkService:
    """Gestiona vinculación de usuarios con Telegram."""
    
    CODE_LENGTH = 6
    CODE_EXPIRY_MINUTES = 5
    MAX_ATTEMPTS_PER_HOUR = 3
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def generate_code(self, user_id: int) -> str:
        """
        Genera código de vinculación para un usuario.
        
        Raises:
            ValueError: Si excede límite de códigos por hora.
        """
        try:
            with db_manager.get_session() as session:
                # Verificar límite por hora
                one_hour_ago = datetime.utcnow() - timedelta(hours=1)
                count = session.query(TelegramVerificationCode).filter(
                    TelegramVerificationCode.user_id == user_id,
                    TelegramVerificationCode.created_at >= one_hour_ago
                ).count()
                
                if count >= self.MAX_ATTEMPTS_PER_HOUR:
                    raise ValueError("Límite de códigos por hora excedido")
                
                # Generar código aleatorio
                code = ''.join(random.choices(string.ascii_uppercase + string.digits, 
                                             k=self.CODE_LENGTH))
                
                # Verificar unicidad
                while session.query(TelegramVerificationCode).filter_by(code=code).first():
                    code = ''.join(random.choices(string.ascii_uppercase + string.digits, 
                                                 k=self.CODE_LENGTH))
                
                # Crear registro
                verification = TelegramVerificationCode(
                    user_id=user_id,
                    code=code,
                    expires_at=datetime.utcnow() + timedelta(minutes=self.CODE_EXPIRY_MINUTES),
                    used=False
                )
                session.add(verification)
                
                self.logger.info(f"Código generado para user {user_id}")
                return code
                
        except Exception as e:
            self.logger.error(f"Error generando código: {e}")
            raise
    
    def verify_code(self, code: str, telegram_chat_id: str, 
                   telegram_username: Optional[str] = None) -> bool:
        """
        Verifica código de vinculación y crea relación usuario-chat.
        
        Args:
            code: Código ingresado por usuario
            telegram_chat_id: ID del chat de Telegram
            telegram_username: Username opcional
        
        Returns:
            True si vinculación exitosa
        """
        try:
            with db_manager.get_session() as session:
                # Buscar código válido
                verification = session.query(TelegramVerificationCode).filter_by(
                    code=code,
                    used=False
                ).first()
                
                if not verification:
                    return False
                
                if verification.expires_at < datetime.utcnow():
                    return False
                
                # Marcar como usado
                verification.used = True
                
                # Crear vinculación
                chat = UserTelegramChat(
                    user_id=verification.user_id,
                    telegram_chat_id=str(telegram_chat_id),
                    telegram_username=telegram_username,
                    is_active=True
                )
                session.add(chat)
                
                self.logger.info(
                    f"Usuario {verification.user_id} vinculado con Telegram "
                    f"chat {telegram_chat_id}"
                )
                return True
                
        except Exception as e:
            self.logger.error(f"Error verificando código: {e}")
            return False
    
    def unlink_telegram(self, user_id: int, chat_id: int) -> bool:
        """Desvincula chat específico."""
        try:
            with db_manager.get_session() as session:
                chat = session.query(UserTelegramChat).filter_by(
                    id=chat_id, user_id=user_id
                ).first()
                
                if chat:
                    chat.is_active = False
                    return True
                return False
        except Exception as e:
            self.logger.error(f"Error desvinculando: {e}")
            raise
    
    def get_user_chats(self, user_id: int) -> list:
        """Obtiene chats de Telegram vinculados a usuario."""
        try:
            with db_manager.get_session() as session:
                chats = session.query(UserTelegramChat).filter_by(
                    user_id=user_id, is_active=True
                ).all()
                result = []
                for c in chats:
                    result.append({
                        "id": c.id,
                        "telegram_username": c.telegram_username,
                        "linked_at": c.linked_at.isoformat()
                    })
                    session.expunge(c)
                return result
        except Exception as e:
            self.logger.error(f"Error obteniendo chats: {e}")
            raise