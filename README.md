# Sistema de Videovigilancia LAN

Sistema NVR/VMS completo para videovigilancia en red local (LAN) con soporte para hasta 4 cámaras simultáneas, detección de objetos mediante IA (YOLOv8), detección de movimiento, grabación continua y por eventos, notificaciones Telegram y panel de control desktop.

## Características Principales

- **Backend**: Flask REST API con autenticación JWT, SQLAlchemy ORM, FFmpeg para captura de video
- **Procesamiento de Video**: Pipeline eficiente con workers independientes, buffer circular, streaming MJPEG
- **Inteligencia Artificial**: YOLOv8n para detección de personas, vehículos y mascotas (1 cámara a la vez)
- **Detección de Movimiento**: Algoritmo optimizado con OpenCV para cámaras sin IA
- **Grabación**: Pre-evento y post-evento automático, gestión inteligente de almacenamiento
- **Notificaciones**: Bot de Telegram para alertas en tiempo real
- **Descubrimiento**: Soporte ONVIF y WS-Discovery para configuración automática de cámaras
- **UI Desktop**: Interfaz gráfica moderna con PySide6

## Requisitos del Sistema

- **Python**: Versión 3.13 o superior
- **FFmpeg**: Debe estar instalado en el sistema y disponible en PATH
  - Windows: Descargar desde https://ffmpeg.org/download.html
  - Linux: `sudo apt-get install ffmpeg`
  - macOS: `brew install ffmpeg`
- **Hardware**:
  - CPU: 4 cores recomendados
  - RAM: 4GB mínimo
  - Red: 100Mbps LAN
  - GPU: Opcional, para aceleración CUDA con YOLO (si no se usa CPU)

## Estructura del Proyecto

```
sistema-videovigilancia/
├── backend/app/          # API Flask, workers, procesamiento
├── desktop_app/src/      # Aplicación desktop PySide6
├── config/               # Configuraciones
├── recordings/           # Videos grabados y snapshots
├── logs/                 # Logs del sistema
└── data/                 # Base de datos SQLite
```

## Instalación

1. Clonar o descargar el proyecto
2. Crear entorno virtual (recomendado):
   ```bash
   python -m venv venv
   # Windows: venv\Scripts\activate
   # Linux/macOS: source venv/bin/activate
   ```
3. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```
4. Configurar variables de entorno:
   ```bash
   cp .env.example .env
   # Editar .env con tus valores (claves JWT, token de Telegram, etc.)
   ```

## Uso

### Iniciar Backend
```bash
python backend/app/main.py
```
El servidor Flask iniciará en `http://0.0.0.0:5000` (configurable en .env)

### Iniciar Aplicación Desktop
```bash
python desktop_app/src/main.py
```

### Acceso Web (alternativo a Desktop)
Abrir navegador en `http://localhost:5000` (si se sirve frontend estático) o usar la API directamente.

## Configuración de Cámaras

1. Usar el descubrimiento ONVIF en la UI Desktop, o
2. Agregar manualmente mediante la API o interfaz gráfica proporcionando:
   - URL RTSP (ej: `rtsp://192.168.1.100:554/stream1`)
   - Credenciales ONVIF (si aplica)
   - Capacidades (PTZ, LEDs, Audio)

## Licencia

Proyecto privado - Uso doméstico.
