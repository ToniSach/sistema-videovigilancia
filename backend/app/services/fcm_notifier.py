"""
Notificador Firebase Cloud Messaging (FCM) para dispositivos móviles.
"""
import logging
from typing import Optional, Dict, Any

# Nota: En producción, instalar: pip install firebase-admin
try:
    import firebase_admin
    from firebase_admin import credentials, messaging
    FCM_AVAILABLE = True
except ImportError:
    FCM_AVAILABLE = False

logger = logging.getLogger(__name__)


class FCMNotifier:
    """
    Servicio de notificaciones push via Firebase Cloud Messaging.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._initialized = False
        self._app = None
        
        if not FCM_AVAILABLE:
            self.logger.warning("firebase-admin no instalado. Notificaciones push deshabilitadas.")
            return
        
        try:
            # Inicializar con credenciales por defecto (GOOGLE_APPLICATION_CREDENTIALS)
            self._app = firebase_admin.initialize_app()
            self._initialized = True
            self.logger.info("FCM inicializado correctamente")
        except Exception as e:
            self.logger.error(f"Error inicializando FCM: {e}")
    
    def send_to_device(self, fcm_token: str, title: str, body: str, 
                      data: Optional[Dict[str, str]] = None,
                      image_url: Optional[str] = None) -> bool:
        """
        Envía notificación a dispositivo específico.
        
        Args:
            fcm_token: Token FCM del dispositivo
            title: Título de la notificación
            body: Cuerpo del mensaje
            data: Datos adicionales (payload)
            image_url: URL de imagen opcional
        """
        if not self._initialized or not FCM_AVAILABLE:
            self.logger.warning("FCM no inicializado, no se puede enviar notificación")
            return False
        
        try:
            notification = messaging.Notification(
                title=title,
                body=body,
                image_url=image_url
            )
            
            message = messaging.Message(
                notification=notification,
                data=data or {},
                token=fcm_token,
                android=messaging.AndroidConfig(
                    priority='high',
                    notification=messaging.AndroidNotification(
                        channel_id='alerts',
                        priority='high'
                    )
                ),
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(alert={'title': title, 'body': body})
                    )
                )
            )
            
            response = messaging.send(message, app=self._app)
            self.logger.debug(f"Notificación enviada: {response}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error enviando notificación FCM: {e}")
            return False
    
    def send_to_topic(self, topic: str, title: str, body: str, 
                     data: Optional[Dict[str, str]] = None) -> bool:
        """
        Envía notificación a topic (grupo de dispositivos).
        """
        if not self._initialized or not FCM_AVAILABLE:
            return False
        
        try:
            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data=data or {},
                topic=topic
            )
            messaging.send(message, app=self._app)
            return True
        except Exception as e:
            self.logger.error(f"Error enviando a topic: {e}")
            return False


# Instancia global
fcm_notifier = FCMNotifier()