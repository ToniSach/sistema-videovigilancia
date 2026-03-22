"""
Cliente HTTP para API backend con manejo de JWT y refresh automático.
"""
import json
import logging
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass

import requests
from PySide6.QtCore import QObject, Signal, QThreadPool, QRunnable

from desktop_app.src.config import config
from desktop_app.src.models.user import AuthTokens, User

logger = logging.getLogger(__name__)


@dataclass
class APIResponse:
    """Respuesta estandarizada de API."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    status_code: int = 200


class APIWorker(QRunnable):
    """Worker para requests HTTP en background."""
    
    def __init__(self, func: Callable, callback: Optional[Callable] = None):
        super().__init__()
        self.func = func
        self.callback = callback
        self.setAutoDelete(True)
    
    def run(self):
        try:
            result = self.func()
            if self.callback:
                self.callback(result)
        except Exception as e:
            logger.error(f"Error en APIWorker: {e}")
            if self.callback:
                self.callback(APIResponse(success=False, error=str(e), status_code=0))


class APIClient(QObject):
    """Cliente API singleton con manejo de tokens."""
    
    # Señales
    auth_error = Signal()  # Token inválido, requerir re-login
    request_error = Signal(str)  # Error general
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        super().__init__()
        self._initialized = True
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })
        self.tokens: Optional[AuthTokens] = None
        self._thread_pool = QThreadPool.globalInstance()
        self._base_url = config.API_BASE_URL
        
        logger.info("APIClient inicializado")
    
    def set_tokens(self, tokens: AuthTokens):
        """Establece tokens de autenticación."""
        self.tokens = tokens
        self.session.headers["Authorization"] = f"Bearer {tokens.access_token}"
    
    def clear_tokens(self):
        """Limpia tokens (logout)."""
        self.tokens = None
        self.session.headers.pop("Authorization", None)
    
    def _refresh_token_if_needed(self) -> bool:
        """Intenta refrescar token si es necesario."""
        if not self.tokens:
            return False
        
        try:
            response = self.session.post(
                f"{self._base_url}/auth/refresh",
                headers={"Authorization": f"Bearer {self.tokens.refresh_token}"},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                self.tokens.access_token = data.get("access_token")
                self.session.headers["Authorization"] = f"Bearer {self.tokens.access_token}"
                return True
        except Exception as e:
            logger.error(f"Error refrescando token: {e}")
        
        return False
    
    def _make_request(self, method: str, endpoint: str, 
                     data: Dict = None, 
                     params: Dict = None,
                     stream: bool = False) -> APIResponse:
        """Ejecuta request síncrono."""
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        
        try:
            if method == "GET":
                response = self.session.get(url, params=params, stream=stream, timeout=config.TIMEOUT)
            elif method == "POST":
                response = self.session.post(url, json=data, timeout=config.TIMEOUT)
            elif method == "PUT":
                response = self.session.put(url, json=data, timeout=config.TIMEOUT)
            elif method == "PATCH":
                response = self.session.patch(url, json=data, timeout=config.TIMEOUT)
            elif method == "DELETE":
                response = self.session.delete(url, json=data, timeout=config.TIMEOUT)
            else:
                return APIResponse(success=False, error=f"Método no soportado: {method}")
            
            # Manejar 401 Unauthorized
            if response.status_code == 401:
                if self._refresh_token_if_needed():
                    return self._make_request(method, endpoint, data, params, stream)
                else:
                    self.auth_error.emit()
                    return APIResponse(success=False, error="Sesión expirada", status_code=401)
            
            if stream:
                return APIResponse(success=True, data=response, status_code=response.status_code)
            
            try:
                json_data = response.json()
                return APIResponse(
                    success=json_data.get("success", response.status_code < 400),
                    data=json_data.get("data"),
                    error=json_data.get("error"),
                    status_code=response.status_code
                )
            except:
                return APIResponse(
                    success=response.status_code < 400,
                    data=response.text,
                    status_code=response.status_code
                )
                
        except requests.RequestException as e:
            logger.error(f"Request error: {e}")
            return APIResponse(success=False, error=str(e), status_code=0)
    
    def request_async(self, method: str, endpoint: str,
                     callback: Callable[[APIResponse], None],
                     data: Dict = None,
                     params: Dict = None,
                     stream: bool = False):
        """Ejecuta request asíncrono."""
        worker = APIWorker(
            lambda: self._make_request(method, endpoint, data, params, stream),
            callback
        )
        self._thread_pool.start(worker)
    
    # ============ MÉTODOS CONVENIENTES (REST completos) ============
    
    def get(self, endpoint: str, callback: Callable, params: Dict = None):
        """GET asíncrono."""
        self.request_async("GET", endpoint, callback, params=params)
    
    def post(self, endpoint: str, callback: Callable, data: Dict = None):
        """POST asíncrono."""
        self.request_async("POST", endpoint, callback, data=data)
    
    def put(self, endpoint: str, callback: Callable, data: Dict = None):
        """PUT asíncrono."""
        self.request_async("PUT", endpoint, callback, data=data)
    
    def patch(self, endpoint: str, callback: Callable, data: Dict = None):
        """PATCH asíncrono."""
        self.request_async("PATCH", endpoint, callback, data=data)
    
    def delete(self, endpoint: str, callback: Callable, data: Dict = None):
        """DELETE asíncrono."""
        self.request_async("DELETE", endpoint, callback, data=data)
    
    def login(self, username: str, password: str, callback: Callable[[APIResponse], None]):
        """Login especial que no requiere token previo."""
        def do_login():
            try:
                response = requests.post(
                    f"{self._base_url}/auth/login",
                    json={"username": username, "password": password},
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    return APIResponse(success=True, data=data)
                else:
                    try:
                        err = response.json()
                        return APIResponse(success=False, error=err.get("error", "Login failed"), 
                                        status_code=response.status_code)
                    except:
                        return APIResponse(success=False, error="Login failed", 
                                        status_code=response.status_code)
            except Exception as e:
                return APIResponse(success=False, error=str(e))
        
        worker = APIWorker(do_login, callback)
        self._thread_pool.start(worker)


# Instancia global
api_client = APIClient()