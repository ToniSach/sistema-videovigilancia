# PARTE 1 — Visión General del Sistema

## 1.1 Problema que resuelve el sistema

Las pequeñas y medianas instalaciones (hogares, comercios, oficinas, laboratorios) que quieren videovigilancia **moderna con detección por IA** se enfrentan a tres opciones, todas malas:

1. **NVR/DVR comercial cerrado** (Hikvision, Dahua): caja propietaria, sin IA real configurable, sin integración con apps propias, telemetría a la nube del fabricante y dependencia de un ecosistema cerrado.
2. **Plataformas en la nube** (Ring, Nest, Wyze): cuota mensual, los vídeos salen de la red local hacia servidores de terceros (riesgo de privacidad), y dejan de funcionar sin internet.
3. **Software libre genérico** (ZoneMinder, Frigate, Shinobi): potentes pero complejos de instalar, con curvas de aprendizaje altas y normalmente sin un cliente de escritorio nativo ni una app móvil propia integrada.

**El problema concreto** que este proyecto ataca: *construir un grabador/gestor de vídeo en red (NVR/VMS) que funcione **100% en la red local (LAN)**, sin nube y sin cuotas, para hasta 4 cámaras IP, con detección de objetos por IA (YOLOv8), grabación continua y por evento, alertas por Telegram, y clientes propios (escritorio + móvil), manteniendo **baja latencia de vídeo** y **privacidad total** (los datos nunca salen de la red).*

### Restricciones de diseño que definen el problema
- **Sin internet obligatorio.** Todo el camino crítico (vídeo, grabación, IA, notificaciones por WebSocket) debe operar en una LAN aislada. Telegram es el único canal que requiere salida a internet, y es opcional.
- **Cámaras IP "baratas".** El sistema asume cámaras XiongMai/genéricas y similares que aceptan **pocas conexiones RTSP simultáneas** (típicamente 1–4) y que tienen GOP/intervalo de I-frame largo (latencia inherente). El diseño debe minimizar conexiones a la cámara.
- **Hardware modesto.** Se ejecuta en un PC normal (no servidor dedicado). Por eso la IA se gatea por movimiento y sólo corre en una cámara a la vez, y el streaming se hace por copia de stream (`-c copy`) sin recodificar siempre que sea posible.

## 1.2 Objetivos del proyecto

**Objetivo general:** Desarrollar un sistema NVR/VMS LAN para hasta 4 cámaras IP con captura, detección por IA, grabación, alertas multicanal y clientes de escritorio y móvil, sin dependencia de la nube.

**Objetivos específicos:**
1. **Captura robusta** de RTSP con reconexión automática y detección de cámara congelada (watchdog).
2. **Visualización en vivo de baja latencia** reutilizando una sola conexión por cámara (go2rtc → WebRTC/RTSP/HLS).
3. **Detección de objetos** (persona/vehículo) con YOLOv8, gateada por movimiento para ahorrar CPU.
4. **Grabación dual:** continua (segmentada) + clips de evento (pre/post-roll).
5. **Gestión de almacenamiento** con política de retención por espacio y tiempo, y reconciliación BD↔disco.
6. **Notificaciones multicanal** (Telegram + WebSocket en tiempo real) enrutadas por preferencias de usuario.
7. **Multi-usuario seguro:** autenticación JWT, roles (admin/usuario) y permisos granulares por cámara.
8. **Descubrimiento y control ONVIF:** descubrimiento automático (WS-Discovery), control PTZ, audio bidireccional, LEDs/IR, sincronización de hora.
9. **Clientes nativos:** escritorio (PySide6 + VLC) y móvil Android (Kotlin + ExoPlayer).

## 1.3 Casos de uso

| ID | Caso de uso | Actor | Descripción |
|---|---|---|---|
| CU-01 | Configuración inicial | Admin | Primer arranque: crear usuario admin (`/auth/setup`), descubrir cámaras por ONVIF, darlas de alta. |
| CU-02 | Ver en vivo | Usuario | Abrir el mosaico/cámara individual; el vídeo se sirve por go2rtc (WebRTC/RTSP/HLS). |
| CU-03 | Detección de intruso | Sistema | YOLO detecta "person" fuera de horario → genera Evento → snapshot + clip + alerta Telegram + push WebSocket. |
| CU-04 | Revisar grabaciones | Usuario | Navegar la timeline, seleccionar un segmento, reproducir vía URL firmada (seek con Range). |
| CU-05 | Controlar PTZ | Usuario con permiso | Mover la cámara (pan/tilt/zoom) por ONVIF `ContinuousMove`; bloqueo de PTZ para evitar conflictos. |
| CU-06 | Audio bidireccional | Usuario con permiso | Escuchar el micrófono de la cámara (ffplay) y hablar por su altavoz (FFmpeg→RTP). |
| CU-07 | Vincular Telegram | Usuario | Generar código/QR de vinculación; el bot asocia el chat al usuario. |
| CU-08 | Onboarding móvil | Usuario | Escanear un QR del escritorio que registra el dispositivo móvil (token de enlace). |
| CU-09 | Gestionar permisos | Admin | Compartir una cámara con otro usuario con flags (view, ptz, leds, audio, download). |
| CU-10 | Monitoreo de salud | Admin | Consultar `/system/health` (CPU, RAM, disco, estado de cada cámara, FPS). |

## 1.4 Usuarios del sistema

- **Administrador (`role="admin"`):** acceso total a todas las cámaras, gestión de usuarios y permisos, configuración del sistema. Se crea en el primer arranque (setup).
- **Usuario estándar (`role="user"`):** ve y opera **sólo** las cámaras que posee (`owner_id`) o las que le han compartido vía `UserCameraPermission`. Cada permiso es granular: `can_view`, `can_control_ptz`, `can_control_leds`, `can_control_audio`, `can_download_recordings`.
- **Dispositivos móviles:** se registran como `MobileDevice` (UUID + hash de refresh token) ligados a un usuario; reciben notificaciones por WebSocket mientras la app está activa.
- **Bot de Telegram (actor externo):** canal de salida de alertas; los chats se vinculan a usuarios vía `UserTelegramChat`.

> **Punto clave para defensa:** la pertenencia (`owner_id`) por sí sola **no** basta para autorizar — siempre se consulta `PermissionService.check_permission()` porque existen cámaras compartidas. La jerarquía es: *admin → propietario → permiso explícito*.

## 1.5 Arquitectura general

El sistema son **dos aplicaciones de proceso separado** (backend y escritorio) más una app móvil, comunicadas por **REST autenticado con JWT** y por **WebSocket** para tiempo real, con **go2rtc** como capa de medios en vivo.

```
┌───────────────────────────── RED LAN (sin nube) ─────────────────────────────┐
│                                                                              │
│   Cámaras IP (RTSP/ONVIF)                                                     │
│        │  RTSP (1 sola conexión por cámara)                                   │
│        ▼                                                                      │
│   ┌──────────────────────── BACKEND (Flask, 1 proceso) ────────────────────┐ │
│   │                                                                        │ │
│   │  go2rtc (sidecar) ──► WebRTC / RTSP / HLS  ──────────────┐             │ │
│   │     │ restream RTSP (127.0.0.1:8554)                     │             │ │
│   │     ▼                                                    │             │ │
│   │  FFmpegWorker ─► CircularFrameBuffer ─► FrameDistributor │             │ │
│   │                                          ├─ AIScheduler (YOLOv8)       │ │
│   │                                          └─ RecordingManager (MP4)     │ │
│   │  EventManager (bus) ─► EventService(BD) / TelegramNotifier / WS broker │ │
│   │  REST API (JWT) ─ PostgreSQL ─ StorageManager ─ ConsistencyChecker     │ │
│   └────────────────────────────────────────────────────────┼─────────────┘ │
│        ▲ REST+JWT / WebSocket                                │ medios         │
│        │                                                     ▼                │
│   ┌──────────────────┐                          ┌──────────────────┐         │
│   │ Escritorio PySide6│  VLC (RTSP restream)    │  Android (Kotlin) │ ExoPlayer│
│   │  (Qt6)            │◄────────────────────────│  Retrofit + WS    │ (HLS)    │
│   └──────────────────┘                          └──────────────────┘         │
└──────────────────────────────────────────────────────────────────────────────┘
        (Telegram Bot API: única salida opcional a internet, para alertas)
```

### Principio arquitectónico CRÍTICO: estado singleton en memoria
Casi todos los componentes de larga vida son **singletons** (`__new__`) que mantienen hilos, subprocesos FFmpeg, buffers, pools de modelos y pools de BD **en memoria del proceso**: `CameraManager`, `DependencyContainer`, `DatabaseManager`, `EventManager`, `GlobalExecutor` (≤50 hilos), `metrics_collector`, `go2rtc_manager`, etc.

**Consecuencia (a defender):** el backend **debe correr como un solo proceso**. Por eso usa el servidor integrado de Flask `app.run(threaded=True)` (un proceso, pool de hilos) y **no** se pone detrás de Gunicorn/uWSGI multi-worker, que rompería los singletons (habría N copias del estado, N conexiones a cada cámara, N pools…). Es una decisión consciente y adecuada para un *appliance* LAN.

## 1.6 Componentes principales

| Componente | Archivo | Responsabilidad |
|---|---|---|
| `create_app()` | `backend/app/main.py` | Compone la app: JWT, CORS, rate-limit, BD, contenedor DI, arranque async de cámaras, servicios de fondo, blueprints. |
| `Settings` | `backend/app/config.py` | Configuración (singleton) desde `.env` + overrides de BD. |
| `DependencyContainer` | `backend/app/container.py` | Inyección de dependencias (repos + servicios). |
| `CameraManager` | `backend/app/cameras/camera_manager.py` | Ciclo de vida de cada cámara y su pipeline. |
| `FFmpegWorker` | `backend/app/workers/ffmpeg_worker.py` | Captura RTSP→frames crudos, reconexión, watchdog. |
| `CircularFrameBuffer` / `FrameDistributor` | `backend/app/streaming/frame_buffer.py` | Buffer "newest-wins" + fan-out a consumidores. |
| `Go2RtcManager` | `backend/app/streaming/go2rtc_manager.py` | Sidecar de medios en vivo (WebRTC/RTSP/HLS). |
| `AIScheduler` / `YLOModelPool` | `backend/app/processing/ai/` | Detección YOLOv8 gateada por movimiento. |
| `RecordingManager` / `StorageManager` | `backend/app/recording/` | Grabación continua + clips + retención. |
| `EventManager` | `backend/app/events/event_manager.py` | Bus pub/sub de eventos. |
| `TelegramNotifier` / `ws_broker` | `backend/app/notifications/` | Alertas Telegram + WebSocket. |
| ONVIF/PTZ | `backend/app/cameras/onvif_*.py`, `ptz_controller.py` | Descubrimiento, SOAP, PTZ, audio, LED, hora. |
| Modelos | `backend/app/database/models.py` | 16 tablas SQLAlchemy 2.0. |
| Cliente escritorio | `desktop_app/src/` | PySide6 + VLC (vivo) + reproducción histórica. |
| Cliente móvil | `CamLink_app/` | Kotlin + Retrofit + ExoPlayer + WebSocket. |

## 1.7 Tecnologías utilizadas y su justificación

| Tecnología | Para qué se usa | Por qué se eligió |
|---|---|---|
| **Python 3.13 + Flask** | Backend REST y orquestación. | Ecosistema de visión/IA (OpenCV, ultralytics, numpy) es Python-first; Flask es minimalista y encaja con el diseño de **un solo proceso multihilo**. |
| **FFmpeg** | Captura RTSP→frames, grabación, clips, transcodificación. | Estándar de facto, soporta todos los codecs/contenedores, flags finos de baja latencia, presente en todas las plataformas. |
| **go2rtc** | Re-streaming en vivo (WebRTC/RTSP/HLS) desde una sola conexión RTSP. | Resuelve el límite de conexiones de las cámaras baratas y entrega WebRTC sub-segundo sin recodificar (`-c copy`). |
| **YOLOv8 (ultralytics) + PyTorch CPU** | Detección de objetos (persona/vehículo). | SOTA en velocidad/precisión, fácil de usar, modelo "nano" corre en CPU; PyTorch CPU evita dependencia de GPU. |
| **OpenCV** | Detección de movimiento, manejo de frames, dibujo de cajas, JPEG de snapshots. | Librería de visión madura y rápida (operaciones vectorizadas). |
| **PostgreSQL + SQLAlchemy 2.0 + Alembic** | Persistencia relacional. | Integridad referencial (FK/cascadas), concurrencia real (pool), tipos ricos (Time, DateTime). SQLAlchemy 2.0 (`Mapped[...]`) da tipado; Alembic versiona el esquema. |
| **JWT (flask-jwt-extended)** | Autenticación stateless. | Tokens firmados sin sesión en servidor; access corto (15 min) + refresh (7 días) + blocklist para revocar. |
| **ONVIF / SOAP / WS-Discovery** | Descubrimiento y control de cámaras. | Estándar de la industria de videovigilancia; permite PTZ, perfiles, stream URIs y descubrimiento multicast independiente del fabricante. |
| **PySide6 (Qt6) + python-vlc** | Cliente de escritorio. | Qt6 da UI nativa multiplataforma; libVLC decodifica RTSP con flags de baja latencia y aceleración por hardware. |
| **Kotlin + Retrofit + ExoPlayer (Media3) + OkHttp** | Cliente Android. | Stack estándar Android: Retrofit/OkHttp para REST+JWT, ExoPlayer para HLS/RTSP, OkHttp WebSocket para notificaciones. |
| **Docker + docker-compose** | Empaquetado opcional (PostgreSQL + backend con FFmpeg y go2rtc embebidos). | Reproducibilidad; bridge networking con puertos publicados (funciona en Windows). |

## 1.8 Ventajas y desventajas de cada decisión tecnológica

### Flask de un solo proceso (vs Gunicorn multi-worker)
- ✅ **Ventajas:** simplicidad; los singletons (cámaras, buffers, pools) viven en un único espacio de memoria sin coordinación distribuida; cero overhead de IPC; ideal para *appliance* LAN de 4 cámaras.
- ❌ **Desventajas:** no escala horizontalmente; un fallo del proceso tira todo; el GIL limita el paralelismo de CPU puro (mitigado porque el trabajo pesado —FFmpeg, go2rtc, YOLO— ocurre en subprocesos o libera el GIL).
- **Defensa:** el cuello de botella real es la **cámara** (conexiones RTSP, GOP) y la **GPU/CPU de inferencia**, no el WSGI. Multi-worker rompería el modelo sin aportar.

### go2rtc como capa de vivo (vs servir MJPEG/HLS desde el backend)
- ✅ **Ventajas:** una sola conexión RTSP a la cámara; WebRTC de latencia sub-segundo; `-c copy` (CPU≈0); soporta dual-lens y multi-calidad; clientes heterogéneos (navegador WebRTC, VLC RTSP, ExoPlayer HLS).
- ❌ **Desventajas:** dependencia de un binario externo (sidecar) que hay que supervisar; WebRTC requiere configurar candidatos ICE (HOST_LAN_IP) en LAN.
- **Defensa:** MJPEG fue **eliminado** porque recodificaba en el backend (CPU alta, latencia, una conexión por cliente). go2rtc es la elección correcta para multiplexar.

### YOLOv8-nano en CPU, una cámara a la vez (vs multi-cámara en GPU)
- ✅ **Ventajas:** corre en cualquier PC sin GPU; gateado por movimiento ahorra ~90% de inferencias; cooldowns evitan inundación de alertas.
- ❌ **Desventajas:** sólo una cámara con IA simultánea (`AI_CAMERA_ID`); latencia de detección mayor que en GPU.
- **Defensa:** para 4 cámaras domésticas, la IA simultánea en todas en CPU no es realista; el diseño prioriza **una cámara crítica** con detección fiable y deja la puerta abierta a GPU (QSV/CUDA) por configuración.

### PostgreSQL (vs SQLite)
- ✅ **Ventajas:** concurrencia real (pool de conexiones), integridad (FK + cascadas), tipos (Time/DateTime), robustez ante múltiples clientes.
- ❌ **Desventajas:** requiere un servicio extra (más pesado que SQLite embebido).
- **Defensa:** hay múltiples clientes concurrentes (escritorio + varios móviles + hilos de fondo) escribiendo eventos/grabaciones; SQLite sufriría con escritores concurrentes. SQLite quedó como legado no usado.

### JWT stateless con blocklist (vs sesiones en servidor)
- ✅ **Ventajas:** sin estado de sesión; escalable; cómodo para móvil (refresh largo).
- ❌ **Desventajas:** revocar un JWT requiere una blocklist (rompe la pureza stateless); el token vive hasta expirar si no se revoca.
- **Defensa:** se implementa **blocklist dual** (memoria + tabla `revoked_tokens`) que se rehidrata al arrancar, y access tokens cortos (15 min) que limitan la ventana de exposición.

### Telegram (vs FCM/push nativo)
- ✅ **Ventajas:** cero infraestructura propia de push; funciona fuera de la LAN; entrega fotos y texto; gratis.
- ❌ **Desventajas:** depende de internet y de los servidores de Telegram; no es "push del SO".
- **Defensa:** en LAN, las alertas en tiempo real llegan por **WebSocket** (sin internet); Telegram es el canal **remoto** complementario. FCM se evaluó pero añade dependencia de Google y registro de proyecto; el WebSocket cubre el caso LAN sin terceros.
