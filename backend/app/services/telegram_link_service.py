"""
================================================================================
MÓDULO: telegram_link_service — Vinculación de cuentas con Telegram por código
================================================================================

PROPÓSITO
    Vincula un usuario del sistema con uno o varios chats de Telegram para que
    pueda recibir alertas por ese canal. Usa un código temporal de 6 caracteres
    que el usuario envía al bot; al verificarlo se crea la relación usuario-chat.

RESPONSABILIDAD PRINCIPAL
    - Generar códigos únicos, efímeros (5 min) y con límite de 3 por hora/usuario
      (anti-abuso).
    - Verificar el código entrante (lo aporta el poller del bot junto al chat_id)
      y crear/reactivar la vinculación de forma IDEMPOTENTE (no duplica chats).
    - Desvincular chats (baja lógica) y listar los chats activos del usuario.

DEPENDENCIAS
    database.models ........ TelegramVerificationCode, UserTelegramChat
    database.connection .... db_manager (una sesión por operación)

COMPONENTES RELACIONADOS
    - api.routes.telegram_link: expone generación/desvinculación/listado.
    - TelegramBotPoller: detecta el código en los mensajes al bot y llama a
      verify_code con el chat_id real del remitente.
    - NotificationRouter._send_telegram: CONSUME los UserTelegramChat activos
      que aquí se crean, para entregar las alertas (Pipeline #13).

PUNTO DE ENTRADA
    Instancia `TelegramLinkService()` desde las rutas y desde el poller del bot.

PIPELINE(S)
    #13 Notificaciones — etapa de provisión del canal Telegram: produce los
    UserTelegramChat sobre los que el router entrega las alertas.
================================================================================
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
    """
    Gestiona la vinculación de usuarios con chats de Telegram (códigos temporales).

    ROL: emitir/verificar códigos y administrar los UserTelegramChat. Lo
    instancian/consumen las rutas de `api.routes.telegram_link` y el
    TelegramBotPoller. NO envía mensajes (eso lo hace TelegramNotifier vía el
    NotificationRouter). Pipeline #13, etapa de provisión del canal Telegram.
    """

    CODE_LENGTH = 6              # longitud del código alfanumérico (A-Z0-9)
    CODE_EXPIRY_MINUTES = 5      # vida útil del código
    MAX_ATTEMPTS_PER_HOUR = 3    # tope de códigos por usuario/hora (anti-abuso)
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def generate_code(self, user_id: int, device_id: Optional[int] = None) -> str:
        """
        Propósito: genera un código único (garantiza unicidad reintentando) y
            efímero para que el usuario lo envíe al bot de Telegram. El código
            recuerda el DISPOSITIVO que lo pidió (Telegram es por dispositivo), y
            ese device_id se propaga al UserTelegramChat al verificar.
        Inputs: user_id; device_id (None = vinculación de cuenta/escritorio).
        Outputs: el código (str de CODE_LENGTH caracteres).
        Excepciones: ValueError si se superó MAX_ATTEMPTS_PER_HOUR; re-lanza BD.
        Llamado por: ruta de "vincular Telegram" (api.routes.telegram_link).
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
                    device_id=device_id,
                    code=code,
                    expires_at=datetime.utcnow() + timedelta(minutes=self.CODE_EXPIRY_MINUTES),
                    used=False
                )
                session.add(verification)

                self.logger.info(f"Código generado para user {user_id} device {device_id}")
                return code

        except Exception as e:
            self.logger.error(f"Error generando código: {e}")
            raise
    
    def verify_code(self, code: str, telegram_chat_id: str, 
                   telegram_username: Optional[str] = None) -> bool:
        """
        Propósito: verifica el código y crea/reactiva la relación usuario-chat
            de forma IDEMPOTENTE (ver nota en el cuerpo). Es el punto que cierra
            la vinculación del canal Telegram (#13).
        Inputs: code (el que el usuario envió al bot); telegram_chat_id (chat
            real del remitente, lo aporta el poller); telegram_username opcional.
        Outputs: True si la vinculación fue exitosa; False si el código no existe,
            ya se usó o caducó.
        Excepciones: capturadas → False (no propaga).
        Llamado por: TelegramBotPoller al detectar el código en un mensaje al bot.
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

                # Telegram POR DISPOSITIVO: el chat se vincula al alcance que pidió
                # el código (verification.device_id; NULL = cuenta/escritorio).
                device_id = verification.device_id

                # Vinculación IDEMPOTENTE: si ya existe un registro para este
                # (usuario, dispositivo, chat) lo reactivamos en vez de duplicar.
                existing = session.query(UserTelegramChat).filter_by(
                    user_id=verification.user_id,
                    device_id=device_id,
                    telegram_chat_id=str(telegram_chat_id),
                ).first()

                if existing:
                    existing.is_active = True
                    existing.telegram_username = telegram_username or existing.telegram_username
                    existing.linked_at = datetime.utcnow()
                else:
                    session.add(UserTelegramChat(
                        user_id=verification.user_id,
                        device_id=device_id,
                        telegram_chat_id=str(telegram_chat_id),
                        telegram_username=telegram_username,
                        is_active=True
                    ))

                self.logger.info(
                    f"Usuario {verification.user_id} (device {device_id}) vinculado "
                    f"con Telegram chat {telegram_chat_id}"
                )
                return True
                
        except Exception as e:
            self.logger.error(f"Error verificando código: {e}")
            return False
    
    def unlink_telegram(self, user_id: int, chat_id: int) -> bool:
        """
        Propósito: baja LÓGICA de un chat vinculado (is_active=False); deja de
            recibir alertas por él. Inputs: user_id; chat_id (id de la fila
            UserTelegramChat, no el chat_id de Telegram). Outputs: True si existía
            y pertenecía al usuario, False si no. Excepciones: re-lanza errores
            de BD. Llamado por: ruta de desvinculación.
        """
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
    
    def get_user_chats(self, user_id: int, device_id: Optional[int] = None,
                       _scope_device: bool = False) -> list:
        """
        Propósito: lista los chats de Telegram ACTIVOS como dicts serializables.
            Telegram es por dispositivo: si `_scope_device` es True, devuelve solo
            los chats del alcance `device_id` (el dispositivo que pregunta); si es
            False, todos los del usuario (vista admin/escritorio).
        Inputs: user_id; device_id (alcance); _scope_device (acotar o no).
        Outputs: list[dict] con id, telegram_username, linked_at, device_id.
        Excepciones: re-lanza errores de BD. Llamado por: ruta de listado de chats.
        """
        try:
            with db_manager.get_session() as session:
                q = session.query(UserTelegramChat).filter_by(
                    user_id=user_id, is_active=True
                )
                if _scope_device:
                    q = q.filter(UserTelegramChat.device_id == device_id)
                chats = q.all()
                result = []
                for c in chats:
                    result.append({
                        "id": c.id,
                        "telegram_username": c.telegram_username,
                        "linked_at": c.linked_at.isoformat(),
                        "device_id": c.device_id,
                    })
                    session.expunge(c)
                return result
        except Exception as e:
            self.logger.error(f"Error obteniendo chats: {e}")
            raise