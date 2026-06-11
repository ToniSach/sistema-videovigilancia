# PARTE 11 — Resúmenes para Estudiar

> Cuatro niveles de profundidad para repasar según el tiempo disponible. Lee el de 1 página justo antes de entrar; el de 20 la semana previa.

---

# RESUMEN DE 20 PÁGINAS (estudio profundo)

## 1. Qué es y qué resuelve
Sistema **NVR/VMS LAN** para hasta **4 cámaras IP**, sin nube ni cuotas, con **detección por IA (YOLOv8)**, grabación continua + por evento, alertas multicanal (Telegram + WebSocket) y dos clientes nativos (escritorio PySide6 + Android Kotlin). Resuelve el vacío entre NVR comerciales cerrados, nubes de pago (privacidad/costo) y software libre complejo: un *appliance* privado, de baja latencia, que opera 100% en LAN.

**Restricciones que definen el diseño:** cámaras baratas que aceptan 1–4 conexiones RTSP y tienen GOP largo; hardware modesto sin GPU garantizada; funcionamiento sin internet.

## 2. Arquitectura
Dos aplicaciones de proceso (backend Flask + escritorio) y una app móvil, comunicadas por **REST/JWT** y **WebSocket**, con **go2rtc** como capa de medios.

**Principio CRÍTICO:** casi todo es **singleton en memoria** (`CameraManager`, `DatabaseManager`, `EventManager`, `GlobalExecutor`, `Go2RtcManager`…). Por eso el backend **corre en un solo proceso** (`app.run(threaded=True)`), no detrás de Gunicorn multi-worker (duplicaría estado y saturaría cámaras).

**Composición (`create_app`):** JWT+CORS+rate-limit → `init_db` (`create_all`) → contenedor DI → hilo daemon que arranca cámaras async → servicios de fondo (go2rtc, keepalive, storage, métricas, consistencia) → blueprints con `safe_register` (fallos no fatales; `/health` lista lo cargado).

**Patrones:** Singleton, DI/Service Locator, Repository, Pub/Sub (eventos), Factory, Producer/Consumer.

## 3. Pipeline de captura
`RTSP → FFmpegWorker → CircularFrameBuffer → FrameDistributor → {AIScheduler, RecordingManager}`. El **vivo NO** usa este pipeline (lo sirve go2rtc). `FFmpegWorker` lanza ffmpeg, lee `rawvideo bgr24` por stdout, flags de baja latencia (`-probesize 32 -analyzeduration 0 -fflags nobuffer -flags low_delay`), reconecta con backoff (`MAX_RECONNECT=10`), watchdog `WATCHDOG_TIMEOUT=30s` → `FROZEN`. El buffer es newest-wins (2–3 frames); el distribuidor reparte en el `GlobalExecutor` (zero-copy/`needs_copy`). Dual-lens: split l1/l2 en go2rtc, no en Python.

## 4. Streaming en vivo (go2rtc)
go2rtc abre **1** conexión RTSP por cámara y re-expone **WebRTC/RTSP/HLS**. `Go2RtcManager` genera `go2rtc.yaml` desde la BD (función pura `build_go2rtc_config`), lo lanza, lo **supervisa** (backoff `[1,2,5,10,20,30]s`) y lo **reconcilia** (15 s). Streams: `cam_X` (nativo `-c copy`), `_medium/_low` (escalados), dual-lens `_l1/_l2` con `exec:ffmpeg -c:v hevc_qsv ... crop ... h264_qsv -g 15 -bf 0 -async_depth 1`. `StreamKeepAlive` mantiene transcoders calientes (`ffmpeg -c copy -f null -`) para conmutación instantánea. WebRTC: `WebRTCSignalingService` valida JWT+permiso y proxya el SDP a go2rtc en loopback; ICE con candidatos host (IP LAN:8555). Escritorio: VLC del restream RTSP con `--network-caching=150 --rtsp-tcp --drop-late-frames`. **Mayor latencia = GOP de la cámara**, no el backend (`/cameras/<id>/latency` → `frame_age`).

## 5. IA
YOLOv8-nano (ultralytics) en PyTorch CPU. `AIFrameSource` lee `cam_X_low` (640×384@6fps, letterbox). `AIScheduler` (hilo worker, no callback — fix F1.2): `MotionDetector` gatea YOLO (ahorra ~90% CPU) → `model.predict(conf≈0.35, classes=[person,vehicle])` → filtro por clase/confianza → agrupar + **cooldown por clase** (≈30 s) → `EventData`. Conceptos: confianza = P(obj)·P(clase|obj); IoU = ∩/∪; NMS quita duplicados (IoU>0.45); métricas P/R/mAP. Una cámara a la vez (`AI_CAMERA_ID`). Si faltan torch/ultralytics → 503 `AI_DEPENDENCIES_MISSING` con el pip exacto (chequeo upfront).

## 6. Eventos, grabación, clips
**Bus** `EventManager`: productores publican `EventData`; suscriptores (EventService→BD+JPEG, RecordingManager→clip, NotificationRouter, MetricsCollector) corren en pool de 8 hilos. **Grabación continua:** `ffmpeg -c copy -f segment` (MP4 ~2 min) desde el restream → filas `Recording`. **Clips:** ventana pre/post (10+10 s) por splice de segmentos (o fallback RAM) → `Event.clip_path`. **StorageManager:** retención por cuota/antigüedad (borrado atómico). **ConsistencyChecker:** reconcilia BD↔FS.

## 7. Notificaciones
`TelegramNotifier` (Bot API `sendPhoto`/`sendMessage`, HTTPS) + `ws_broker` (WebSocket LAN, sin internet) + `NotificationRouter` (filtra por `NotificationPreference`: tipo, cámara, horario, días, canal). Anti-flood con `NotificationLog.cooldown_key`. Vinculación Telegram: código de un uso (5 min) canjeado en el bot (`/start <code>`) vía poller. Onboarding móvil: QR (`link_tokens`, 15 min).

## 8. Reproducción histórica
`/recordings/<id>/playback-url` → `SignedUrlService.sign("recording:<id>")` = `v1.<exp>.<sig>` (HMAC-SHA256, ≈5 min). Reproductor pide `/media?token=...` con `Range:` → 206. Verificación en tiempo constante (`hmac.compare_digest`). VOD HLS: `HLSService` remux MP4→m3u8/ts.

## 9. ONVIF / PTZ
**Descubrimiento:** WS-Discovery multicast `239.255.255.250:3702` (Probe→ProbeMatch con XAddrs) + pre-check TCP + FAST PATH (SOAP propio sin WSDL, ~6 cred × 4 puertos) + SLOW PATH (zeep/WSDL) + fallback RTSP. **Auth:** UsernameToken, PasswordText→fallback PasswordDigest (`Base64(SHA1(Nonce+Created+Pass))`). **Operaciones:** GetCapabilities, GetProfiles, GetStreamUri, ContinuousMove/Stop. **PTZ:** vector `Velocity{PanTilt,Zoom}` hasta Stop; `PTZLockService` (mutex con timeout). **Físico:** audio (ffplay escucha; FFmpeg→RTP G.711 habla), LED/IR (Imaging), `time_sync` (SetSystemDateAndTime — necesario para digest). Fallbacks por capacidad faltante; persistencia del puerto ONVIF. BD `cameras` guarda url/cred/profile_token/capacidades/diagnóstico.

## 10. Base de datos
PostgreSQL + SQLAlchemy 2.0; pool 10+20, pre-ping, recycle 1 h. **16 tablas:** users, cameras, user_camera_permissions (N:M con flags), events, recordings, mobile_devices, notification_preferences/channels/days, telegram_verification_codes, user_telegram_chats, notification_logs (cooldown_key), link_tokens, system_config, audit_logs, revoked_tokens. Multi-tenant: `owner_id` + permisos. Índices por consultas reales `(camera_id,created_at)`, `(acknowledged,created_at)`, `(camera_id,start_time)`. CASCADE/SET NULL por semántica.

## 11. Seguridad
JWT access 15 min + refresh 7 días (claims sub/role/jti); `@jwt_required` + `@require_camera_permission` (admin→owner→flag). Blocklist dual (memoria+`revoked_tokens`, rehidratada). Contraseñas PBKDF2:SHA256; refresh móvil como hash. URLs de medios firmadas HMAC. Rate limiter + auditoría. **Deudas:** credenciales de cámara en claro (→Fernet), HTTP plano (→TLS), rate-limit en memoria (→Redis).

## 12. Clientes
**Escritorio (PySide6/Qt6):** `api_client` (JWT + refresh ante 401, QThreadPool), `rtsp_video` (VLC restream go2rtc, flags baja latencia), `playback_service` (URLs firmadas), vistas (live mosaico, playback timeline, control PTZ/audio, gestión, eventos, settings, usuarios, permisos). **Android (Kotlin):** Retrofit/OkHttp + `JwtAuthenticator` (refresh), ExoPlayer (HLS/RTSP de go2rtc), `NotificationWebSocketService` (foreground service WS), QR onboarding, vinculación Telegram, `DeviceIdentity` (UUID). WebRTC móvil = mejora futura.

## 13. Escalabilidad/Rendimiento
~4 cámaras (CPU/proceso). Escalar: Redis (blocklist/rate-limit) + proceso por cámara + GPU. go2rtc multiplexa (no 1 conexión por cliente). Latencia: flags FFmpeg + buffer pequeño + QSV (−90% CPU dual-lens) + keepalive. Watchdog/reconexión ante congelado.

## 14. Justificación tecnológica (resumen)
Flask (un proceso, control), FFmpeg (todo codec, fuera GIL), go2rtc (1 conexión multiprotocolo), YOLOv8-nano (CPU viable), OpenCV (visión rápida), PostgreSQL (concurrencia/integridad), JWT (stateless), ONVIF (interoperabilidad), PySide6+VLC (nativo baja latencia), Kotlin/ExoPlayer (estándar Android), Docker/Alembic (despliegue/migración).

## 15. Limitaciones y mejoras
Ver tabla en [10_CRITICAS_DEFENSA.md](10_CRITICAS_DEFENSA.md) §10.14: cifrar credenciales, TLS, Redis, Alembic versionado, IA GPU multi-cámara + tracking, métricas formales de IA, reconcile event-driven, purga de auditoría.

---

# RESUMEN DE 10 PÁGINAS (repaso sólido)

**Qué es:** NVR/VMS LAN, 4 cámaras IP, IA YOLOv8, grabación continua+evento, Telegram+WebSocket, clientes escritorio+móvil, sin nube.

**Arquitectura:** backend Flask **un proceso** (singletons en memoria: CameraManager, EventManager, Go2RtcManager, DB, executor). `create_app` arranca cámaras en hilo daemon; blueprints best-effort; PostgreSQL; contenedor DI. Patrones: Singleton, DI, Repository, Pub/Sub, Factory, Producer/Consumer.

**Captura:** FFmpeg→buffer(newest-wins)→distribuidor→IA+grabación; flags baja latencia; watchdog 30 s→FROZEN; reconexión backoff. Vivo NO pasa por aquí.

**Vivo (go2rtc):** 1 conexión RTSP → WebRTC/RTSP/HLS. YAML generado desde BD; supervisor+reconciliador+keepalive; dual-lens crop QSV; WebRTC vía proxy de señalización autenticado; ICE host LAN. Latencia dominante = GOP cámara.

**IA:** YOLOv8-nano CPU; `AIFrameSource` 640×384@6fps; gate de movimiento; `predict(conf≈0.35, classes)`; cooldown por clase; una cámara (`AI_CAMERA_ID`); 503 si faltan deps. IoU/NMS/confianza.

**Eventos/grabación/clips:** `EventManager` pub/sub (8 hilos) → BD+JPEG, clip pre/post, notificaciones, métricas. Grabación `-c copy` MP4 segmentado; retención por cuota; checker BD↔FS.

**Notificaciones:** Telegram (HTTPS) + WebSocket (LAN); router por preferencias (tipo/cámara/horario/días/canal); anti-flood `cooldown_key`; vinculación por código/QR.

**Playback:** URLs firmadas HMAC (`v1.<exp>.<sig>`, 5 min) + HTTP Range (206); VOD HLS por remux.

**ONVIF/PTZ:** WS-Discovery (Probe/ProbeMatch) + FAST/SLOW/RTSP; auth UsernameToken Text→Digest; GetProfiles/GetStreamUri; PTZ ContinuousMove (Velocity)+Stop+lock; audio RTP G.711; time_sync. Fallbacks por capacidad; puerto persistido.

**BD:** PostgreSQL, 16 tablas, multi-tenant (owner+permisos N:M con flags), índices por consultas reales, CASCADE/SET NULL semánticos, blocklist persistente, auditoría.

**Seguridad:** JWT 15 min/7 días + blocklist dual; PBKDF2; permisos jerárquicos admin→owner→flag; URLs firmadas; rate-limit; auditoría. Deudas: credenciales claro, HTTP plano.

**Clientes:** escritorio PySide6+VLC; Android Kotlin+ExoPlayer+WS foreground service.

**Escala/rendimiento:** ~4 cámaras; go2rtc multiplexa; QSV −90% CPU; escalar con Redis+proceso/cámara+GPU.

**Defensa clave:** cada decisión responde al dominio (appliance LAN, cámaras baratas, hardware modesto, privacidad). Límites reconocidos con ruta de mejora concreta.

---

# RESUMEN DE 5 PÁGINAS (repaso rápido)

1. **Sistema:** NVR/VMS LAN, ≤4 cámaras, IA YOLOv8, grabación continua+evento, Telegram+WebSocket, escritorio+móvil, **sin nube**.
2. **Backend = un proceso** porque el estado son singletons en memoria; `app.run(threaded=True)`. `create_app` compone todo; cámaras async en hilo daemon; blueprints best-effort; PostgreSQL + contenedor DI.
3. **Captura:** FFmpeg → buffer newest-wins → distribuidor → IA+grabación. Flags baja latencia; watchdog 30 s; reconexión backoff. **El vivo lo sirve go2rtc, no este pipeline.**
4. **go2rtc:** 1 conexión RTSP → WebRTC/RTSP/HLS; YAML desde BD; supervisor+reconciliador+keepalive; dual-lens crop QSV; WebRTC proxy autenticado (ICE host LAN). Latencia = GOP cámara.
5. **IA:** nano en CPU, frame 640×384@6fps, gate de movimiento (−90% CPU), `predict(conf 0.35)`, cooldown por clase, una cámara. IoU/NMS. 503 si faltan deps.
6. **Eventos:** bus pub/sub (8 hilos) → BD+snapshot, clip pre/post, notificaciones, métricas. Grabación `-c copy`; retención por cuota; checker BD↔FS.
7. **Notificaciones:** Telegram (HTTPS, remoto) + WebSocket (LAN); router por preferencias; anti-flood cooldown_key; vinculación por código/QR.
8. **Playback:** URLs firmadas HMAC + HTTP Range (206); VOD HLS.
9. **ONVIF/PTZ:** WS-Discovery multicast; FAST(SOAP propio)/SLOW(zeep)/RTSP; auth Text→Digest; ContinuousMove+Stop+lock; audio/LED/time_sync; fallbacks.
10. **BD:** 16 tablas, multi-tenant (owner+permisos N:M), índices dirigidos, CASCADE/SET NULL.
11. **Seguridad:** JWT 15 m/7 d + blocklist dual; PBKDF2; permisos admin→owner→flag; URLs firmadas; rate-limit+auditoría.
12. **Clientes:** PySide6+VLC; Kotlin+ExoPlayer+WS.
13. **Defensa:** decisiones justificadas por el dominio; deudas (credenciales claro, HTTP, Redis, Alembic) con mejoras acotadas.

---

# RESUMEN DE 1 PÁGINA (la noche antes)

**QUÉ:** NVR/VMS **LAN sin nube**, ≤4 cámaras IP, IA **YOLOv8**, grabación continua+clips de evento, alertas **Telegram + WebSocket**, clientes **escritorio (PySide6/VLC)** y **Android (Kotlin/ExoPlayer)**.

**POR QUÉ UN PROCESO:** todo es **singleton en memoria** (cámaras, buffers, pools, go2rtc) → multi-worker rompería el modelo. Flask `threaded=True`.

**VIVO = go2rtc:** abre **1 conexión RTSP** por cámara y re-expone **WebRTC (mín. latencia) / RTSP (escritorio) / HLS (móvil)**, `-c copy`. Supervisado+reconciliado; keepalive de transcoders; dual-lens crop QSV. **Mayor latencia = GOP de la cámara**, no el backend (`/latency`→frame_age).

**CAPTURA (sólo IA+grabación):** FFmpeg→buffer newest-wins→distribuidor; flags baja latencia; watchdog 30 s→FROZEN+reconexión.

**IA:** nano en CPU, `cam_X_low` 640×384@6fps, **gate de movimiento** (−90% CPU), `conf≈0.35`, **cooldown por clase**, **una cámara** (`AI_CAMERA_ID`). IoU/NMS. Faltan deps→**503 AI_DEPENDENCIES_MISSING**.

**EVENTOS:** **bus pub/sub** `EventManager` → BD+snapshot / clip pre+post / Telegram+WS / métricas.

**DATOS:** PostgreSQL, **16 tablas**, multi-tenant = `owner_id` + `user_camera_permissions` (flags); índices por consultas reales.

**SEGURIDAD:** **JWT** access 15 min + refresh 7 d (sub/role/jti) + **blocklist dual** (mem+BD); **PBKDF2**; permisos **admin→owner→flag**; **URLs firmadas HMAC** para medios (Range/206).

**ONVIF/PTZ:** **WS-Discovery** multicast (Probe/ProbeMatch); SOAP FAST/SLOW + fallback RTSP; auth **Text→Digest**; **ContinuousMove**+Stop+lock; audio/LED/time_sync.

**DEFENSA EN 1 FRASE:** *"Appliance LAN privado; cada decisión responde a cámaras baratas (pocas conexiones), hardware modesto y privacidad sin nube; conozco los límites (credenciales en claro, HTTP, una cámara IA) y tengo mejora concreta para cada uno sin rediseñar el núcleo."*

**NÚMEROS CLAVE:** 4 cámaras · access 15 min/refresh 7 d · conf 0.35 · cooldown 30 s · IA 640×384@6fps · watchdog 30 s · go2rtc reconcile 15 s · pool BD 10+20 · go2rtc 1984(API)/8554(RTSP)/8555(WebRTC).
