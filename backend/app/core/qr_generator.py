"""
Generador de códigos QR para conexión rápida de dispositivos.
Permite a apps móviles escanear y conectarse automáticamente.
"""
import logging
import json
from io import BytesIO
from typing import Optional

import qrcode
from PIL import Image

logger = logging.getLogger(__name__)


class QRGenerator:
    """
    Generador de códigos QR para configuración de conexión.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def generate_connection_qr(
        self, 
        username: str, 
        server_ip: str, 
        port: int, 
        token: str,
        box_size: int = 10,
        border: int = 4
    ) -> bytes:
        """
        Genera un código QR con datos de conexión al servidor.
        
        Args:
            username: Nombre de usuario
            server_ip: Dirección IP del servidor
            port: Puerto del servidor Flask
            token: Token JWT de acceso
            box_size: Tamaño de cada caja del QR (default 10)
            border: Tamaño del borde en cajas (default 4)
            
        Returns:
            bytes: Imagen PNG en formato bytes
            
        Raises:
            RuntimeError: Si falla la generación del QR
        """
        try:
            # Crear objeto de configuración
            config_data = {
                "username": username,
                "server": f"http://{server_ip}:{port}",
                "token": token,
                "version": "1.0"
            }
            
            # Serializar a JSON compacto
            json_string = json.dumps(config_data, separators=(',', ':'))
            
            # Crear QR
            qr = qrcode.QRCode(
                version=None,  # Auto-ajuste
                error_correction=qrcode.constants.ERROR_CORRECT_H,  # Alta corrección
                box_size=box_size,
                border=border,
            )
            
            qr.add_data(json_string)
            qr.make(fit=True)
            
            # Crear imagen
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Convertir a bytes PNG
            buffer = BytesIO()
            img.save(buffer, format='PNG', optimize=True)
            buffer.seek(0)
            
            return buffer.getvalue()
            
        except Exception as error:
            self.logger.error(f"Error generando QR: {error}")
            raise RuntimeError("No se pudo generar el código QR") from error
    
    def save_qr(self, qr_bytes: bytes, output_path: str) -> str:
        """
        Guarda los bytes de un QR en archivo PNG.
        
        Args:
            qr_bytes: Bytes de la imagen PNG
            output_path: Ruta completa donde guardar (ej: /tmp/qr.png)
            
        Returns:
            str: Ruta del archivo guardado
            
        Raises:
            IOError: Si no se puede escribir el archivo
        """
        try:
            with open(output_path, 'wb') as f:
                f.write(qr_bytes)
            
            self.logger.info(f"QR guardado en: {output_path}")
            return output_path
            
        except IOError as error:
            self.logger.error(f"Error al guardar QR en {output_path}: {error}")
            raise IOError(f"No se pudo guardar el archivo: {error}") from error
    
    def generate_wifi_qr(
        self, 
        ssid: str, 
        password: str, 
        security: str = "WPA",
        output_path: Optional[str] = None
    ) -> bytes:
        """
        Genera QR para configuración WiFi (formato estándar WiFi).
        Útil para configurar cámaras o dispositivos IoT.
        
        Args:
            ssid: Nombre de la red WiFi
            password: Contraseña de la red
            security: Tipo de seguridad (WPA, WEP, nopass)
            output_path: Opcional, ruta para guardar archivo
            
        Returns:
            bytes: Imagen PNG del QR WiFi
        """
        try:
            # Formato estándar WiFi QR
            wifi_string = f"WIFI:S:{ssid};T:{security};P:{password};;"
            
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_H,
                box_size=10,
                border=4,
            )
            
            qr.add_data(wifi_string)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            
            buffer = BytesIO()
            img.save(buffer, format='PNG')
            buffer.seek(0)
            
            qr_bytes = buffer.getvalue()
            
            if output_path:
                self.save_qr(qr_bytes, output_path)
            
            return qr_bytes
            
        except Exception as error:
            self.logger.error(f"Error generando QR WiFi: {error}")
            raise RuntimeError("No se pudo generar QR WiFi") from error
