# Código Fuente

Para evitar duplicar archivos (y mantener un único punto de verdad), el código
fuente **no se copia** dentro de `/Entrega`: vive en la raíz del repositorio.
Esta carpeta es solo el índice que indica dónde está cada parte.

## Ubicación del código (raíz del repositorio)

| Componente | Carpeta | Lenguaje | Archivos |
|------------|---------|----------|---------:|
| **Backend** (API Flask, workers, procesamiento, IA) | [`../../backend/app/`](../../backend/app/) | Python 3.13 | 99 `.py` |
| **App de escritorio** (cliente PySide6) | [`../../desktop_app/src/`](../../desktop_app/src/) | Python 3.13 | 46 `.py` |
| **App móvil** (cliente Android CamLink) | [`../../CamLink_app/`](../../CamLink_app/) | Kotlin | 24 `.kt` |

## Estructura del backend (`backend/app/`)

```
api/            Rutas REST + middleware (JWT, CORS, rate limiter)
cameras/        CameraManager, FFmpegWorker, dual-lens splitter
core/           GlobalExecutor y utilidades base
database/       Modelos SQLAlchemy 2.0, repositorios, migraciones
events/         EventManager (bus de eventos)
notifications/  Telegram, router por usuario, broker WebSocket
processing/     IA (YOLOv8, AIScheduler) y detección de movimiento
recording/      Grabación continua y por evento
storage/        Gestión de cuota de disco
streaming/      Integración con go2rtc (directo)
workers/        Hilos de trabajo de larga vida
main.py         Composición de la app Flask (create_app)
```

## Documentación técnica del código

- Arquitectura y pipelines: [`../../docs/`](../../docs/) y
  [`../Documentacion/`](../Documentacion/).
- Diagramas UML (clases y paquetes): [`../../docs/uml/`](../../docs/uml/).
- Guía para Claude Code / arquitectura resumida: [`../../CLAUDE.md`](../../CLAUDE.md).

## Dependencias

- Backend + escritorio: [`../../requirements.txt`](../../requirements.txt)
- Solo backend (headless): [`../../requirements-backend.txt`](../../requirements-backend.txt)
- Binario de medios: [`../../go2rtc.exe`](../../go2rtc.exe)
