# ============================================================================
# Backend NVR/VMS — imagen Linux lista para producción en LAN.
#
# Incluye TODO lo que en Windows tienes que instalar a mano:
#   - Python 3.13 + dependencias del backend (sin PySide6; la GUI es nativa).
#   - FFmpeg (captura RTSP, splice de grabaciones, audio talk-back).
#   - go2rtc (binario único, capa de medios WebRTC/RTSP/HLS).
#   - PyTorch CPU + Ultralytics (YOLOv8) para la IA.
#
# La base de datos PostgreSQL va en su propio contenedor (ver docker-compose).
# El cliente de escritorio (PySide6) y la app móvil se ejecutan aparte; este
# contenedor expone la API REST + WebSocket + go2rtc que ellos consumen.
# ============================================================================
FROM python:3.13-slim-bookworm

# go2rtc: versión fija del binario (Linux amd64). Cambia GO2RTC_VERSION o
# ARCH si despliegas en arm64 (p.ej. una Raspberry Pi / mini-PC ARM).
ARG GO2RTC_VERSION=v1.9.9
ARG GO2RTC_ARCH=amd64

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # El backend corre como proceso ÚNICO (singletons con estado en memoria).
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=5000 \
    # go2rtc es la única capa de directo; viene activado.
    GO2RTC_ENABLED=true \
    GO2RTC_BINARY=/usr/local/bin/go2rtc \
    # Rutas persistentes (se montan como volúmenes en docker-compose).
    RECORDINGS_PATH=/data/recordings \
    AI_MODEL=/data/models/yolov8n.pt

# ---- Dependencias del sistema -------------------------------------------------
# ffmpeg: captura/recodificación. curl/ca-certificates: descargar go2rtc.
# tini: init mínimo para reaping de los subprocesos ffmpeg/go2rtc que lanza
#       el backend (evita procesos zombie). libgomp1: runtime OpenMP de torch.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        tini \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ---- go2rtc (binario único) ---------------------------------------------------
RUN curl -fsSL \
      "https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/go2rtc_linux_${GO2RTC_ARCH}" \
      -o /usr/local/bin/go2rtc \
    && chmod +x /usr/local/bin/go2rtc \
    && /usr/local/bin/go2rtc --version || true

WORKDIR /app

# ---- PyTorch CPU (índice especial) PRIMERO ------------------------------------
# Igual que en Windows: torch antes que el resto, desde el index CPU.
RUN pip install --no-cache-dir \
        torch==2.6.0+cpu torchvision==0.21.0+cpu \
        --index-url https://download.pytorch.org/whl/cpu

# ---- Resto de dependencias del backend ----------------------------------------
COPY requirements-backend.txt /app/requirements-backend.txt
RUN pip install --no-cache-dir -r requirements-backend.txt

# ---- Código de la aplicación --------------------------------------------------
# Solo lo que el backend necesita (la GUI desktop NO va en la imagen).
COPY backend/ /app/backend/
COPY alembic.ini /app/alembic.ini
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /data/recordings /data/models

# Puertos: 5000 API/WS Flask · 1984 go2rtc (HLS/API) · 8554 RTSP · 8555 WebRTC.
EXPOSE 5000 1984 8554 8555/tcp 8555/udp

# tini como PID 1 → reapea ffmpeg/go2rtc; el entrypoint espera a Postgres.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
CMD ["python", "backend/app/main.py"]
