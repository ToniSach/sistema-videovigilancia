"""
MÓDULO: api.routes.qr — Generación de códigos QR de vinculación de dispositivos.

PROPÓSITO
    `POST /api/v1/qr/generate` produce una imagen PNG con un QR que codifica un
    token de vinculación de un solo uso + la IP/puerto del backend. El usuario
    escanea el QR desde la app móvil (CamLink) para enlazar el dispositivo a su
    cuenta sin teclear credenciales.

RESPONSABILIDAD
    Resolver la IP del servidor en la LAN, delegar la creación del token+QR en
    QRService.generate_link_token y devolver el PNG con cabeceras no-cache.

DEPENDENCIAS / PIPELINE
    services.qr_service (QRService) · config.settings (SERVER_PORT).
    Apoya el Pipeline #2/#13 (alta y notificaciones del dispositivo móvil); el
    token se canjea después vía el flujo de devices/mobile.
"""
import socket
from flask import Blueprint, request, jsonify, make_response
from flask_jwt_extended import jwt_required, get_jwt_identity

from backend.app.services.qr_service import QRService
from backend.app.config import settings

qr_bp = Blueprint("qr", __name__, url_prefix="/api/v1/qr")
qr_service = QRService()


@qr_bp.route("/generate", methods=["POST"])
@jwt_required()
def generate_qr():
    """Genera código QR para vinculación de dispositivo."""
    try:
        user_id = int(get_jwt_identity())
        
        # Obtener IP del servidor
        try:
            server_ip = socket.gethostbyname(socket.gethostname())
        except:
            server_ip = request.host.split(':')[0] if request.host else "127.0.0.1"
        
        server_port = settings.SERVER_PORT
        
        token, qr_bytes = qr_service.generate_link_token(user_id, server_ip, server_port)
        
        response = make_response(qr_bytes)
        response.headers.set("Content-Type", "image/png")
        response.headers.set("Cache-Control", "no-cache, no-store, must-revalidate")
        return response
        
    except Exception as e:
        return jsonify({"success": False, "error": "Error interno del servidor"}), 500