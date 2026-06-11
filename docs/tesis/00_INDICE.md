# Manual de Defensa de Tesis — Sistema NVR/VMS LAN

> **Propósito de este documento.** Manual de estudio técnico exhaustivo para defender ante un jurado especializado el sistema de videovigilancia LAN (NVR/VMS) desarrollado en este repositorio. Todo el contenido está anclado al **código real** del proyecto (clases, métodos, archivos y líneas). Está dividido en archivos independientes para poder cargarse como fuentes separadas en **NotebookLM** y para convertirse a Word/PDF.

## Cómo usar este manual

1. **Lee en orden** las Partes 1 → 8 para construir el modelo mental completo del sistema.
2. **Memoriza** la Parte 9 (100+ preguntas de sinodales con respuesta ideal, respuesta corta y preguntas de seguimiento).
3. **Practica** la Parte 10 (críticas de un sinodal experto + defensa técnica).
4. **Repasa** la Parte 11 (resúmenes de 20, 10, 5 y 1 página) la noche antes de la exposición.

## Estructura del repositorio (mapa mental)

```
sistema-videovigilancia/
├── backend/app/            # Aplicación Flask (REST + pipelines + IA + grabación)
│   ├── main.py             # create_app(): composición de toda la app
│   ├── config.py           # Settings (singleton): todas las perillas de configuración
│   ├── container.py        # DependencyContainer (inyección de dependencias)
│   ├── cameras/            # ONVIF, PTZ, descubrimiento, audio, LED, time-sync, CameraManager
│   ├── workers/            # FFmpegWorker (captura de frames)
│   ├── streaming/          # go2rtc, keepalive, WebRTC signaling, HLS, frame_buffer
│   ├── processing/         # IA (YOLOv8) y detección de movimiento
│   ├── recording/          # Grabación continua + clips + retención
│   ├── events/             # EventManager (bus de eventos)
│   ├── notifications/      # Telegram, WebSocket broker
│   ├── services/           # Lógica de negocio (auth, permisos, cámaras, IA…)
│   ├── database/           # Modelos SQLAlchemy + conexión PostgreSQL
│   └── api/                # Blueprints REST + middleware (rate-limit, auditoría)
├── desktop_app/src/        # Cliente de escritorio PySide6 (Qt6) + VLC
└── CamLink_app/            # Cliente Android nativo (Kotlin) + ExoPlayer/Retrofit
```

## Índice de archivos del manual

| Archivo | Parte | Contenido |
|---|---|---|
| [01_VISION_GENERAL.md](01_VISION_GENERAL.md) | 1 | Problema, objetivos, casos de uso, usuarios, arquitectura, tecnologías y su justificación |
| [02_ARQUITECTURA.md](02_ARQUITECTURA.md) | 2 | Cada módulo: qué hace, por qué, I/O, dependencias, clases, flujo, riesgos, mejoras |
| [03_PIPELINES.md](03_PIPELINES.md) | 3 | Los 10 pipelines paso a paso (vivo, grabación, IA, eventos, clips, notificaciones, playback, auth, ONVIF, PTZ) |
| [04_TECNOLOGIAS.md](04_TECNOLOGIAS.md) | 4 | RTSP, RTP, RTCP, HLS, WebRTC, WS, HTTP(S), REST, JWT, ONVIF, SOAP, XML, WS-Discovery, FFmpeg, go2rtc, YOLO, OpenCV, PySide6, PostgreSQL, Docker, Alembic |
| [05_ONVIF.md](05_ONVIF.md) | 5 | ONVIF en profundidad: WS-Discovery, SOAP, capabilities, profiles, streams, PTZ, fallbacks, BD |
| [06_GO2RTC.md](06_GO2RTC.md) | 6 | go2rtc en profundidad: YAML, publicación/consumo, FFmpeg, RTSP, WebRTC, clientes |
| [07_BASE_DATOS.md](07_BASE_DATOS.md) | 7 | 16 tablas, relaciones, PK/FK, cardinalidades, justificación, queries |
| [08_IA.md](08_IA.md) | 8 | YOLO, inferencia, procesamiento de frame, detección, filtrado, eventos (matemático y conceptual) |
| [09_PREGUNTAS_SINODALES.md](09_PREGUNTAS_SINODALES.md) | 9 | 100+ preguntas difíciles con respuesta ideal, corta y seguimiento |
| [10_CRITICAS_DEFENSA.md](10_CRITICAS_DEFENSA.md) | 10 | Críticas de sinodal experto + defensa técnica sólida |
| [11_RESUMENES.md](11_RESUMENES.md) | 11 | Resúmenes de 20, 10, 5 y 1 página |

## Resumen ejecutivo (una frase por capa)

- **Captura:** FFmpeg lee RTSP de cada cámara y entrega frames crudos a un buffer circular que alimenta IA y grabación.
- **Vivo:** go2rtc abre **una** conexión RTSP por cámara y la re-expone como WebRTC/RTSP/HLS a los clientes sin recodificar.
- **IA:** YOLOv8-nano corre **gateado por movimiento** sobre un substream de baja resolución; sólo una cámara a la vez.
- **Grabación:** segmentos MP4 continuos + clips de evento (pre/post) generados con FFmpeg.
- **Eventos:** un bus pub/sub (`EventManager`) desacopla detección de persistencia y notificación.
- **Notificaciones:** Telegram (Bot API) + WebSocket en tiempo real, enrutadas por preferencias de usuario.
- **Datos:** PostgreSQL con 16 tablas (multi-tenant: owner + permisos por cámara).
- **Seguridad:** JWT (access 15 min / refresh 7 días) con blocklist persistente + URLs firmadas HMAC para medios.
- **Clientes:** escritorio PySide6 (VLC) y Android (Kotlin, ExoPlayer/Retrofit).
