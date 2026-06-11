"""
================================================================================
MÓDULO: qr_service — Vinculación de dispositivos por código QR
================================================================================

PROPÓSITO
    Permite vincular un móvil a una cuenta sin teclear credenciales: el cliente
    autenticado (desktop) genera un QR; el móvil lo escanea, extrae el servidor
    y el `link_token`, y lo canjea para registrarse.

RESPONSABILIDAD PRINCIPAL
    - Emitir un `LinkToken` de un solo uso y vida corta (5 min) e incrustarlo,
      junto a la URL del servidor, en una imagen PNG con el QR.
    - Invalidar tokens previos del usuario al emitir uno nuevo (un QR vigente).
    - Validar (sin consumir) y consumir (marcar usado) el token al canjearlo.

DEPENDENCIAS
    database.models ........ LinkToken
    database.connection .... db_manager (una sesión por operación)
    services.device_service  DeviceService (registro del móvil tras el canje)
    qrcode / io / json ..... render del QR a PNG en memoria

COMPONENTES RELACIONADOS
    - api.routes.qr: expone la generación del QR y el canje del token.
    - DeviceService: tras consumir el token, registra el MobileDevice.

PUNTO DE ENTRADA
    Instancia `QRService()` desde las rutas.

PIPELINE(S)
    #2 Autenticación (variante por QR) y arranque del #13 (provisión del móvil):
    emite el token efímero que el móvil canjea para obtener su sesión.
================================================================================
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
    """
    Gestiona los tokens de vinculación para login/registro por QR.

    ROL: emitir el QR con un token efímero y validar/consumir ese token al
    canjearlo. Lo instancian/consumen las rutas de `api.routes.qr`. Colabora con
    DeviceService (registro del móvil). Pipelines #2 (auth) y #13 (provisión móvil).
    """

    # Vida útil del token de vinculación (minutos). Corta a propósito: el QR
    # debe canjearse en el acto; pasado este tiempo hay que regenerarlo.
    TOKEN_EXPIRY_MINUTES = 5
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.device_service = DeviceService()
    
    def generate_link_token(self, user_id: int, server_host: str, server_port: int) -> Tuple[str, bytes]:
        """
        Propósito: invalida los tokens previos del usuario, crea uno nuevo
            (UUID4, caduca en TOKEN_EXPIRY_MINUTES) y lo renderiza en un QR PNG
            junto a la URL del servidor para que el móvil sepa a dónde conectarse.
        Inputs: user_id; server_host, server_port (los del backend, para
            incrustarlos en el QR).
        Outputs: tupla (token_string, qr_image_bytes PNG).
        Excepciones: re-lanza errores de BD/render tras loguear.
        Llamado por: ruta GET de generación de QR (desde el desktop autenticado).
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
        Propósito: valida un token SIN consumirlo (lo deja disponible para el
            canje real). Comprueba que exista, no esté usado y no haya caducado.
        Inputs: token. Outputs: user_id si válido, None si inválido/caducado/usado.
        Excepciones: capturadas → None (no propaga).
        Llamado por: ruta de verificación previa del QR (chequeo de validez).
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
        Propósito: CANJE real del token — lo marca como usado (un solo uso) y
            devuelve el user_id al que vincular el móvil. Tras esto la ruta
            registra el dispositivo vía DeviceService.
        Inputs: token. Outputs: user_id si válido y no caducado, None si no.
        Excepciones: capturadas → None (no propaga).
        Llamado por: ruta de canje del QR (registro del móvil).
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