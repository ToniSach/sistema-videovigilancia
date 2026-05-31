"""
Configuración Gunicorn para NVR.
CRÍTICO: workers=1 porque CameraManager es singleton en memoria.
"""
import multiprocessing

bind = "0.0.0.0:5000"
workers = 1                         # ← Solo 1 proceso (estado compartido de cámaras)
worker_class = "gevent"             # Async I/O para múltiples clientes HTTP
worker_connections = 1000
timeout = 120
keepalive = 5
max_requests = 1000
max_requests_jitter = 50

accesslog = "-"
errorlog = "-"
loglevel = "info"

# Nota: Para producción con HTTPS, descomenta:
# keyfile = "/path/to/key.pem"
# certfile = "/path/to/cert.pem"