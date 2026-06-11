"""
Paquete `core` — utilidades transversales de bajo nivel del backend.

Componentes:
    security.py ......... hash/verificación de contraseñas (bcrypt) — Pipeline #2
    jwt_blocklist.py .... revocación de tokens JWT (memoria + BD) — Pipeline #2
    executor.py ......... GlobalExecutor (ThreadPoolExecutor compartido)
    hardware_detector.py  detección GPU/CPU para IA — apoyo Pipeline #9
    qr_generator.py ..... generación de imágenes QR (utilidad)

Son piezas base reutilizadas por servicios y middleware; varias mantienen
estado vivo en memoria de proceso (encaja con la restricción de proceso único
descrita en main.py).
"""
