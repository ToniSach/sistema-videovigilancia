# PARTE 2 — Arquitectura Completa (módulo por módulo)

> Para cada módulo: **qué hace · por qué existe · inputs · outputs · dependencias · clases · flujo interno · riesgos · mejoras**. El orden sigue el flujo de arranque y luego las capas.

---

## 2.0 Patrones arquitectónicos transversales (defender bien)

- **Singleton (`__new__`)**: estado de larga vida en memoria de proceso. Usado por `CameraManager`, `DatabaseManager`, `EventManager`, `GlobalExecutor`, `Go2RtcManager`, `JWTBlocklist`, `AIService`, `StreamKeepAlive`, etc. *Motivo:* recursos costosos y únicos (hilos, subprocesos, pools) que no deben duplicarse.
- **Inyección de dependencias (Service Locator)**: `DependencyContainer` construye y entrega repos/servicios; los endpoints hacen `get_service("nombre")`. *Motivo:* evita imports circulares y acopla por interfaz.
- **Repository**: `BaseRepository` + `CameraRepository`/`EventRepository`/`RecordingRepository` encapsulan el acceso a datos. *Motivo:* aísla SQLAlchemy de la lógica de negocio.
- **Pub/Sub (Event Bus)**: `EventManager` desacopla productores (FFmpeg/IA/movimiento) de consumidores (BD, Telegram, WS, métricas).
- **Factory**: `create_app()` arma la aplicación de forma determinista.
- **Producer/Consumer**: `CircularFrameBuffer` (productor: FFmpeg) → `FrameDistributor` (consumidores: IA, grabación).

---

## 2.1 `main.py` — Composición de la aplicación (`create_app`)

- **Qué hace:** función *factory* que ensambla toda la app Flask y arranca los servicios de fondo.
- **Por qué existe:** centraliza el *wiring* (orden de inicialización importa: BD antes que contenedor, contenedor antes que blueprints).
- **Inputs:** variables de entorno (`Settings`), base de datos PostgreSQL.
- **Outputs:** objeto `Flask` listo para `app.run(threaded=True)`; hilos de fondo vivos.
- **Dependencias:** `Settings`, `DatabaseManager`, `DependencyContainer`, `CameraManager`, `Go2RtcManager`, `StorageManager`, `MetricsCollector`, `ConsistencyChecker`, `StreamKeepAlive`, blueprints.

**Flujo interno (orden real de arranque):**
1. Configura **JWT** + **CORS** + **rate limiter**.
2. `db_manager.init_db()` → crea tablas (`Base.metadata.create_all()`), de modo que una BD nueva funciona sin correr Alembic.
3. `get_container()` → construye el contenedor DI (repos + servicios).
4. Lanza un **hilo daemon** que espera ~0.5 s y luego llama `CameraManager().start_all_active()` — las cámaras suben **asíncronamente** para que el servidor HTTP esté listo de inmediato.
5. Arranca `go2rtc` (`Go2RtcManager.start`), `StreamKeepAlive`, `StorageManager`, `MetricsCollector`, `LiveHLSService`, `ConsistencyChecker`, monitor de cámaras congeladas, y (si procede) `TelegramNotifier`/bot poller.
6. Registra blueprints con `safe_register()` — **los fallos de import de un blueprint se loguean pero NO son fatales** (`"Error registrando '<name>'"`). Si una ruta falta en producción, revisar el log de arranque y `/api/v1/health` (devuelve la lista `blueprints`).

- **Riesgos:** si un servicio de fondo lanza excepción no capturada, puede degradar el sistema silenciosamente; el orden de arranque es frágil (dependencias implícitas).
- **Mejoras:** *health gating* (no servir requests hasta que BD y go2rtc estén OK); arranque declarativo con verificación de dependencias.

---

## 2.2 `config.py` — `Settings`

- **Qué hace:** carga `.env` del raíz y expone toda la configuración como atributos; permite **overrides desde BD** (`system_config`) para ciertas claves (`_DB_OVERRIDE_KEYS`) sin reiniciar.
- **Por qué existe:** un único punto de verdad para perillas; evita `os.getenv` disperso.
- **Inputs:** `.env`, variables de entorno, tabla `system_config`.
- **Outputs:** singleton `settings`; `get_database_url()` → `postgresql+psycopg2://...`.
- **Perillas clave:** `MAX_CAMERAS=4`, `MAX_CONCURRENT_FFMPEG`, `MAX_AI_INFERENCE_QUEUE`, `AI_CAMERA_ID`, `AI_CONFIDENCE≈0.35`, `AI_EVENT_COOLDOWN_SECONDS≈30`, `FFMPEG_RESOLUTION_WIDTH/HEIGHT/FPS`, `FFMPEG_DUAL_LENS_WIDTH/HEIGHT`, `GO2RTC_*` (enabled, puertos, HWACCEL=qsv), `JWT_ACCESS_TOKEN_MINUTES=15`, `JWT_REFRESH_TOKEN_DAYS=7`, `MEDIA_URL_SECRET`.
- **Riesgos:** algunas perillas definidas pero no usadas (p.ej. `DB_POOL_SIZE` está en config pero `connection.py` lo tiene hardcodeado); falta de validación de enums (`AI_FORMAT`).
- **Mejoras:** validar tipos/enums al cargar; consumir `DB_POOL_SIZE` desde config.

---

## 2.3 `container.py` — `DependencyContainer`

- **Qué hace:** singleton que crea y almacena repos y servicios, y los entrega por nombre (`get(name)`).
- **Por qué existe:** desacopla la construcción de objetos de su uso; rompe imports circulares (los endpoints no importan servicios directamente).
- **Inputs:** `DatabaseManager` (para repos).
- **Outputs:** instancias compartidas de `auth_service`, `camera_service`, `event_service`, `ai_service`, `recording`-related, `permission_service`, etc.
- **Riesgos:** `Service Locator` oculta dependencias (menos explícito que DI por constructor); `__init__` podría ejecutarse dos veces sin lock estricto.
- **Mejoras:** lock en `__new__`; tipado de las claves del contenedor (enum).

---

## 2.4 `database/connection.py` — `DatabaseManager`

- **Qué hace:** gestiona el `engine` y las sesiones de SQLAlchemy contra PostgreSQL.
- **Por qué existe:** centraliza el pool y ofrece un `contextmanager get_session()` con commit/rollback automáticos.
- **Inputs:** `settings.get_database_url()`.
- **Outputs:** sesiones (`Session`), `init_db()` (crea tablas).
- **Configuración del pool:** `QueuePool`, `pool_size=10`, `max_overflow=20` (≤30 conexiones), `pool_pre_ping=True` (valida antes de usar), `pool_recycle=3600`.
- **Patrón de sesión:**
  ```python
  @contextmanager
  def get_session(self):
      session = self._session_factory()
      try:
          yield session; session.commit()
      except Exception as e:
          session.rollback(); raise e
      finally:
          session.close()
  ```
- **Riesgos:** no hay timeout por query; pool fijo hardcodeado.
- **Mejoras:** `statement_timeout`; tamaños de pool desde `config`.

---

## 2.5 `cameras/camera_manager.py` — `CameraManager` (singleton)

- **Qué hace:** orquesta el ciclo de vida de cada cámara: arranque/parada del pipeline, dual-lens, monitor de cámaras congeladas, y elección de la fuente (cámara directa o restream de go2rtc).
- **Por qué existe:** punto único de control de todos los workers de captura.
- **Inputs:** filas `Camera` de la BD; configuración (`GO2RTC_AS_SOURCE`).
- **Outputs:** workers vivos, buffers, distribuidores; estado por cámara (running/frozen).
- **Métodos clave:** `start_camera()`, `start_all_active()`, `start_dual_lens_camera()`, `_go2rtc_source_url()` (devuelve `rtsp://127.0.0.1:8554/cam_X` si `GO2RTC_ENABLED && GO2RTC_AS_SOURCE`).
- **Flujo (`start_camera`):** crea `CircularFrameBuffer` → `FFmpegWorker` (fuente directa o go2rtc) → `FrameDistributor` → registra consumidores (`AIScheduler`, `recording_manager`).
- **Dual-lens:** una sola RTSP "side-by-side" se divide en dos streams lógicos `l1`/`l2` (go2rtc publica `cam_X_l1`/`cam_X_l2`); **no** se crean buffers/distribuidores separados.
- **Riesgos:** estado global; una excepción al iniciar una cámara no debe tumbar las demás.
- **Mejoras:** supervisión por cámara con backoff unificado; métricas por worker expuestas.

---

## 2.6 `workers/ffmpeg_worker.py` — `FFmpegWorker`

- **Qué hace:** lanza `ffmpeg` como subproceso, lee **frames crudos** por `stdout`, reescala a la resolución objetivo, reconecta con backoff y vigila con watchdog.
- **Por qué existe:** desacopla la decodificación (en FFmpeg, C, fuera del GIL) de Python; entrega `numpy` listos para IA/grabación.
- **Inputs:** `camera.rtsp_url` (o `source_url` del restream go2rtc), resolución/fps objetivo, `rtsp_transport`.
- **Outputs:** frames `bgr24` (numpy) al `CircularFrameBuffer`; estado (`RUNNING`/`FROZEN`).
- **Flags de baja latencia (defender):** `-probesize 32 -analyzeduration 0` (sin warm-up de 5 s), `-fflags nobuffer+flush_packets -flags low_delay -flags2 +fast`, salida `rawvideo pix_fmt bgr24`.
- **Constantes:** `MAX_RECONNECT=10` (backoff), `WATCHDOG_TIMEOUT=30s` (sin frames → `FROZEN`).
- **Flujo:** construir comando → `Popen` → leer exactamente `W*H*3` bytes por frame → `np.frombuffer().reshape()` → push al buffer → si EOF/timeout, matar y reconectar.
- **Riesgos:** parsing de frames por tamaño fijo es frágil si la resolución real difiere (mitigado con `_detect_real_resolution` por `ffprobe`); subprocesos zombis si no se limpian.
- **Mejoras:** leer dimensiones del stream dinámicamente; métricas de drops/latencia por worker.

---

## 2.7 `streaming/frame_buffer.py` — `CircularFrameBuffer` + `FrameDistributor`

- **Qué hace:** `CircularFrameBuffer` es un deque thread-safe pequeño (default 2–3 frames, *newest-wins*, cuenta descartes). `FrameDistributor` reparte cada frame a N consumidores en el `GlobalExecutor`.
- **Por qué existe:** absorber jitter sin acumular latencia (siempre el frame más nuevo) y permitir múltiples consumidores sin que uno lento bloquee al resto.
- **Inputs:** frames del `FFmpegWorker`.
- **Outputs:** entrega a consumidores registrados con `needs_copy=True/False` (zero-copy cuando el consumidor copia/serializa de inmediato).
- **Riesgos:** un consumidor lento provoca descartes (aceptable para vivo, no para grabación — por eso la grabación va por go2rtc `-c copy`, no por este buffer).
- **Mejoras:** prioridades por consumidor; backpressure configurable.

> **Nota arquitectónica:** el **vivo NO se sirve desde este pipeline**. go2rtc habla RTSP con la cámara una vez y re-expone WebRTC/RTSP/HLS. El pipeline `FFmpegWorker→buffer→distributor` alimenta **sólo IA + (opcional) grabación**. MJPEG fue eliminado por completo.

---

## 2.8 `streaming/go2rtc_manager.py` — `Go2RtcManager` (singleton)

- **Qué hace:** gestiona el binario sidecar go2rtc: genera `go2rtc.yaml` desde la BD, lo lanza, lo supervisa (backoff `[1,2,5,10,20,30]s`), y lo **reconcilia** cada 15 s con los cambios de cámaras. Construye URLs RTSP/HLS/WebRTC.
- **Por qué existe:** centralizar la conexión a la cámara (1 RTSP) y reexponer múltiples protocolos/calidades sin recodificar.
- **Inputs:** cámaras de BD, `GO2RTC_*` (puertos, host público, HWACCEL, candidatos ICE).
- **Outputs:** `go2rtc.generated.yaml`, proceso go2rtc vivo, URLs (`rtsp_restream_url`, `hls_url`, `webrtc_api_base`).
- **YAML generado:** secciones `api`/`rtsp`/`webrtc`(candidates)/`streams`. Streams: `cam_X` (RTSP nativo, sin transcode), `cam_X_medium/low` (escalados), y dual-lens `cam_X_l1/l2(_medium/_low)` con `exec:ffmpeg ... -c:v hevc_qsv ... -vf crop=...,scale=... -c:v h264_qsv -g 15 -bf 0 -async_depth 1 ...`.
- **Detección de IP LAN:** `detect_lan_ip()` abre un socket UDP a `10.255.255.255:1` (no envía nada) para que el SO revele la interfaz de salida.
- **Riesgos:** dependencia de binario externo; churn si la BD se lee vacía (mitigado con guardia anti-churn); firewall en Windows (abre 1984/8554/8555 con UAC).
- **Mejoras:** recarga en caliente sin matar el proceso; métricas de transcoders activos.

---

## 2.9 `streaming/stream_keepalive.py` — `StreamKeepAlive` (singleton)

- **Qué hace:** mantiene "calientes" los transcoders de go2rtc abriendo un consumidor ligero `ffmpeg -c copy -f null -` por cada stream activo, para que cambiar a una cámara sea instantáneo (evita el arranque en frío ~3.5–5 s).
- **Por qué existe:** la latencia de "primer frame" en go2rtc/QSV es alta; tener el transcoder vivo elimina ese coste al conmutar.
- **Inputs:** cámaras activas de BD (+ `STREAM_KEEPALIVE` extra), reconcilia cada 15 s.
- **Outputs:** subprocesos `ffmpeg -c copy` por stream (`cam_X_medium` o `cam_X_l1_medium`).
- **Riesgos:** consumo extra de CPU/red por mantener streams vivos; procesos a supervisar.
- **Mejoras:** keep-alive sólo de cámaras "favoritas"/visibles.

---

## 2.10 `streaming/webrtc_signaling.py` — `WebRTCSignalingService`

- **Qué hace:** proxy de señalización WHEP: valida JWT + permiso `view`, reenvía el **SDP offer** del cliente a go2rtc (`POST /api/webrtc?src=cam_X`, `Content-Type: application/sdp`) y devuelve el **SDP answer**.
- **Por qué existe:** el medio WebRTC viaja P2P (no por Flask), pero la negociación SDP necesita un intermediario autenticado; así go2rtc no se expone directo al cliente.
- **Inputs:** `camera_id`, `offer_sdp`.
- **Outputs:** `answer_sdp` (o `WebRTCSignalingError`).
- **Riesgos:** timeout de 10 s a go2rtc; validación mínima del SDP (`"v=0" in answer`).
- **Mejoras:** soporte TURN configurable para acceso remoto.

---

## 2.11 `streaming/hls_service.py` — `HLSService` (VOD)

- **Qué hace:** convierte grabaciones MP4 a HLS **on-demand** (remux `-c copy`) para playback en móvil; caché con TTL, límite de 2 conversiones simultáneas, protección anti path-traversal en segmentos.
- **Por qué existe:** ExoPlayer reproduce HLS de forma fiable; el directo HLS lo sirve go2rtc, pero el **VOD** (grabaciones) lo genera este servicio.
- **Inputs:** `recording_id`, ruta MP4.
- **Outputs:** `index.m3u8` + segmentos `.ts`.
- **Riesgos:** caché crece; conversiones concurrentes limitadas.
- **Mejoras:** pre-generación perezosa por LRU; limpieza por tamaño.

---

## 2.12 `processing/ai/` — Subsistema de IA

- **`YLOModelPool`**: singleton que carga **una** instancia del modelo YOLOv8 (`.pt`) y la comparte; `check_dependencies()` verifica `torch`/`ultralytics` y, si faltan, hace que `POST /ai/<id>/activate` devuelva **503 `AI_DEPENDENCIES_MISSING`** con el `pip install` exacto (el chequeo es *upfront*, no muere en el hilo).
- **`AIScheduler`**: hilo worker (no callback del distribuidor — fix F1.2) que: lee frame del `AIFrameSource`, aplica **detección de movimiento** como *gate*, ejecuta YOLO sólo si hay movimiento, agrupa detecciones por clase, aplica **cooldowns por clase**, y publica `EventData` al `EventManager`.
- **`AIFrameSource`**: fuente dedicada y ligera que lee el **substream bajo** de go2rtc (`cam_X_low`, ~360p@6fps) con FFmpeg, entrega frames `640x384 bgr24` *latest-frame* sin colas ni distribuidor (CPU mínima).
- **`AIService`**: control single-camera (`AI_CAMERA_ID`); `activate_ai()` valida dependencias y arranca/para el scheduler.
- **Inputs/Outputs:** entra frame 640×384; sale lista de detecciones (clase, confianza, bbox) → `EventData`.
- **Riesgos:** una sola cámara con IA; el modelo en CPU limita FPS.
- **Mejoras:** soporte GPU (QSV/CUDA), multi-cámara con cola priorizada.
(Detalle matemático en [08_IA.md](08_IA.md).)

---

## 2.13 `processing/motion/motion_detector.py` — Detección de movimiento

- **Qué hace:** compara frames consecutivos (diferencia/umbralización con OpenCV) para decidir si hay movimiento; **gatea** la inferencia YOLO.
- **Por qué existe:** YOLO es caro; correrlo sólo cuando hay movimiento ahorra ~90% de CPU.
- **Inputs:** frames de baja resolución.
- **Outputs:** booleano "hay movimiento" + (opcional) regiones.
- **Riesgos:** falsos positivos por cambios de iluminación/lluvia; umbral fijo.
- **Mejoras:** sustracción de fondo adaptativa (MOG2), zonas de interés (ROIs) configurables.

---

## 2.14 `recording/recording_manager.py` — `RecordingManager`

- **Qué hace:** grabación **continua** (segmentos MP4, p.ej. 2 min, vía FFmpeg, idealmente `-c copy` desde el restream go2rtc) y **clips de evento** (pre + post-roll, p.ej. 10+10 s) por *splice* de los archivos continuos o fallback en RAM.
- **Por qué existe:** persistir vídeo para revisión histórica y evidencia; los clips dan contexto al evento sin reproducir horas.
- **Inputs:** stream RTSP (cámara o restream), señales de evento del `EventManager`.
- **Outputs:** filas `Recording` (start/end, ruta, tamaño, duración) + archivos MP4; `Event.clip_path`.
- **Riesgos:** disco lleno; segmentos huérfanos; `-c copy` requiere keyframes alineados.
- **Mejoras:** índice de keyframes para *splice* exacto; grabación por GOP completo.

---

## 2.15 `recording/storage_manager.py` — `StorageManager`

- **Qué hace:** aplica la **política de retención**: borra grabaciones antiguas por cuota de espacio y/o antigüedad, de forma atómica con rollback.
- **Por qué existe:** el almacenamiento es finito; sin retención el disco se llena y la captura falla.
- **Inputs:** `MAX_STORAGE_GB`, días de retención, estado del disco.
- **Outputs:** archivos borrados + filas `Recording` eliminadas; espacio liberado.
- **Riesgos:** borrar de más; condición de carrera con grabaciones en curso.
- **Mejoras:** retención por cámara; "proteger" grabaciones marcadas.

---

## 2.16 `storage/consistency_checker.py` — `ConsistencyChecker`

- **Qué hace:** reconcilia periódicamente (cada ~24 h) **BD ↔ sistema de archivos**: detecta filas sin archivo y archivos sin fila.
- **Por qué existe:** crashes/borrados manuales generan inconsistencias; sin reconciliación la timeline miente.
- **Inputs:** tabla `recordings`, carpeta de grabaciones.
- **Outputs:** correcciones (eliminar filas huérfanas, registrar archivos faltantes), log.
- **Riesgos:** ventana entre escaneos; archivos en escritura.
- **Mejoras:** verificación incremental por eventos de FS.

---

## 2.17 `events/event_manager.py` — `EventManager` (bus pub/sub)

- **Qué hace:** los productores publican `EventData`; el manager despacha a suscriptores en un pool compartido (8 hilos).
- **Por qué existe:** desacoplar detección de sus efectos (persistir, notificar, métricas); añadir un consumidor no toca a los productores.
- **Inputs:** `EventData` (event_type, camera_id, confidence, frame/snapshot…).
- **Outputs:** invocaciones a suscriptores: `EventService` (BD), `TelegramNotifier`, `NotificationRouter`, `MetricsCollector`.
- **Regla de diseño:** *toda* fuente de eventos publica por aquí, **nunca** llama a los notificadores directamente.
- **Riesgos:** un suscriptor lento consume hilos del pool; errores en un suscriptor no deben afectar a otros.
- **Mejoras:** colas por suscriptor; reintentos con backoff.

---

## 2.18 `notifications/` — Telegram + WebSocket

- **`TelegramNotifier`**: suscriptor del bus; envía foto+texto por **Telegram Bot API** (`sendPhoto`/`sendMessage` vía HTTPS).
- **`telegram_bot_poller`**: long-polling del bot (`getUpdates`) para recibir comandos y completar la **vinculación** (canjear código).
- **`ws_broker`**: broker WebSocket que **difunde** notificaciones en tiempo real a clientes LAN (escritorio/móvil) sin internet.
- **`NotificationRouter` / `notification_preference_service`**: aplican reglas por usuario (tipo de evento, cámara, horario, días, canal) antes de entregar.
- **`telegram_link_service` / `device_service`**: vinculación de chats y registro de dispositivos (refresh token hash).
- **Riesgos:** Telegram depende de internet; WS requiere clientes conectados.
- **Mejoras:** cola persistente de notificaciones fallidas con reintento.
(Detalle de flujo en [03_PIPELINES.md](03_PIPELINES.md) §6.)

---

## 2.19 `cameras/onvif_*` + `ptz_controller` + control físico

- **`onvif_discovery`**: WS-Discovery multicast (`239.255.255.250:3702`), envía `Probe`, parsea `ProbeMatch`, pre-check TCP, FAST PATH (SOAP directo) y SLOW PATH (onvif-zeep), y fallback RTSP.
- **`onvif_soap`**: cliente SOAP sin WSDL; construye sobres XML (`GetCapabilities`, `GetProfiles`, `GetStreamUri`), maneja auth `PasswordText` y `PasswordDigest` (WS-Security UsernameToken).
- **`onvif_common`**: resolución de puertos candidatos, cliente zeep, persistencia del puerto descubierto.
- **`ptz_controller`**: `ContinuousMove`/`Stop`/presets/`GetStatus`; cache singleton por cámara; reconexión por puertos.
- **`audio_controller`**: escuchar (ffplay) y hablar (FFmpeg→RTP `pcm_alaw 8k`); detección de soporte de audio.
- **`led_controller`**: `SetImagingSettings`/`SendAuxiliaryCommand` (IR/luz blanca).
- **`time_sync`**: `SetSystemDateAndTime` (hora local → cámara).
- **`ptz_lock_service`**: mutex en memoria con timeout para evitar comandos PTZ concurrentes.
- **`camera_heuristics`**: detección de marca/modelo y dual-lens; sugerencia de URLs.
- **Riesgos:** heterogeneidad de cámaras (capacidades faltantes); credenciales en claro en BD.
- **Mejoras:** cifrar credenciales (Fernet); caché de capacidades por modelo.
(Capítulo completo en [05_ONVIF.md](05_ONVIF.md).)

---

## 2.20 `database/models.py` — Modelos (16 tablas)

- **Qué hace:** define el esquema SQLAlchemy 2.0 (`DeclarativeBase`, `Mapped[...]`).
- **Tablas:** `users`, `cameras`, `user_camera_permissions`, `events`, `recordings`, `mobile_devices`, `notification_preferences`, `notification_channels`, `notification_days`, `telegram_verification_codes`, `user_telegram_chats`, `notification_logs`, `link_tokens`, `system_config`, `audit_logs`, `revoked_tokens`.
- **Multi-tenant:** `User` posee `Camera` (`owner_id`); `UserCameraPermission` comparte con flags.
(Detalle columna por columna en [07_BASE_DATOS.md](07_BASE_DATOS.md).)

---

## 2.21 Capa de servicios (`services/`)

- **`auth_service`**: login/refresh, generación de JWT (claims `sub`, `role`, `jti`).
- **`user_service`**: CRUD de usuarios (admin).
- **`permission_service`**: `check_permission()` (admin→owner→explícito), `get_accessible_cameras()`, decorador `require_camera_permission`.
- **`camera_service`**: alta/edición/estado de cámaras; enriquecimiento de campos de vivo (URLs).
- **`event_service`**: persistencia de eventos.
- **`ai_service`**: control de IA single-camera.
- **`signed_url_service`**: firma HMAC-SHA256 de URLs de medios (`v1.<exp>.<sig>`), verificación en tiempo constante.
- **`notification_*`, `telegram_link_service`, `device_service`, `qr_service`, `ptz_lock_service`**: ya descritos.
- **Riesgos:** *Service Locator* oculta dependencias; algunas validaciones de permiso podrían faltar si un endpoint nuevo olvida el decorador.
- **Mejoras:** pruebas de contrato por servicio; tipado estricto de DTOs.

---

## 2.22 Capa API (`api/`)

- **Blueprints** (`routes/`): `auth`, `users`, `permissions`, `cameras`, `recordings`, `events`, `ai`, `storage`, `system`, `metrics`, `qr`, `notifications`, `devices`, `telegram_link`, `mobile`, `ws`.
- **Middleware**: `rate_limiter` (flask-limiter), `audit` (decorador `@audit_action` → tabla `audit_logs`), `helpers` (`api_error_response`, `require_admin`, `get_service`).
- **Registro:** `safe_register()` — fallos no fatales.
- **Riesgos:** `audit_logs` crece sin purga; rate limiter en memoria (no persistente).
- **Mejoras:** purga automática de auditoría; backend Redis para rate limit.

---

## 2.23 Cliente de escritorio (`desktop_app/src/`)

- **`api_client`**: REST con `requests` + manejo de **JWT refresh** ante 401; `QThreadPool` para no bloquear la UI.
- **`rtsp_video` (VLC)**: vivo por libVLC desde el **restream RTSP de go2rtc** con flags de baja latencia (`--network-caching=150 --rtsp-tcp --drop-late-frames --no-audio-time-stretch`).
- **`playback_service`**: reproducción histórica (URLs firmadas) con `VLCPlayer`.
- **Vistas**: `live_view` (mosaico), `playback_view` (timeline), `camera_control_view` (PTZ/audio), `camera_management_view`, `events_view`, `settings_view`, `users_view`, `permissions_view`.
- **Componentes**: `ptz_joystick` (3×3), `camera_control_panel`, `timeline_widget`, `video_player`, `toast`, `sparkline`.
- **Riesgos:** acoplar swaps de VLC con la UI; manejo de errores de stream.
- **Mejoras:** reconexión automática de vivo; indicadores de estado por cámara.
(Detalle en [03_PIPELINES.md](03_PIPELINES.md).)

---

## 2.24 Cliente Android (`CamLink_app/`)

- **Red**: `RetrofitClient` (singleton), `ApiService` (endpoints), `JwtAuthenticator` (OkHttp `Authenticator` que renueva el token ante 401), `NotificationWsClient`.
- **Modelos**: `ApiModels.kt` (DTOs Gson).
- **UI (Fragments)**: `LiveViewFragment` (ExoPlayer HLS/RTSP de go2rtc), `PlaybackFragment`/`TimelineFragment` (VOD), `CameraListFragment`, `RecordingsFragment`, `NotificationsPanelFragment`, `TelegramLinkFragment`, `QrScanFragment` (onboarding por QR), `EventConfigFragment`.
- **Servicio**: `NotificationWebSocketService` (foreground service que mantiene el WS y muestra notificaciones locales).
- **`DeviceIdentity`**: UUID estable del dispositivo.
- **Riesgos:** WebRTC móvil pendiente (usa HLS/RTSP); batería por foreground service.
- **Mejoras:** WebRTC nativo; reconexión WS robusta.

---

## 2.25 Núcleo y utilidades (`core/`, `utils/`, `infrastructure/`)

- **`core/executor.py` (`GlobalExecutor`)**: `ThreadPoolExecutor` único (≤50 hilos) para fan-out de frames y tareas de fondo.
- **`core/security.py`**: hashing de contraseñas (PBKDF2:SHA256 vía Werkzeug), helpers JWT (`get_current_user_id`).
- **`core/jwt_blocklist.py`**: blocklist dual (memoria + BD), GC de expirados, `rehydrate_from_db()`.
- **`core/hardware_detector.py`**: detecta capacidades HW (QSV/NVENC) para aceleración.
- **`core/qr_generator.py` / `qr_service`**: QR de vinculación.
- **`infrastructure/metrics/collector.py`**: métricas del sistema; **`telemetry.py`**: registro a CSV/JSONL.
- **`utils/image_optimizer.py`**: optimización de snapshots JPEG.
- **Riesgos:** el pool global compartido puede saturarse si un consumidor monopoliza hilos.
- **Mejoras:** pools separados por tipo de carga (I/O vs CPU).
