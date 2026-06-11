# PARTE 3 — Pipelines Detallados

> Para cada paso: **módulo que interviene · datos que recibe · datos que genera · protocolos · formatos**. Los diagramas usan `─►` para flujo de datos.

---

## 3.1 Pipeline de visualización en vivo (cámara → pantalla)

**Idea central:** el vivo **NO** pasa por el `FFmpegWorker`/buffer de Python. go2rtc abre **una** conexión RTSP a la cámara y la re-expone. Cada cliente elige protocolo (WebRTC, RTSP o HLS) y calidad.

```
Cámara IP ─RTSP/TCP─► go2rtc (sidecar) ─┬─► WebRTC (P2P UDP)  ─► Navegador / WebRTC client
   (H.264/H.265)      (1 conexión, -c copy)├─► RTSP restream    ─► Escritorio (VLC)
                                          └─► HLS (.m3u8/.ts)  ─► Android (ExoPlayer)
```

**Paso a paso (escritorio, RTSP):**
1. **Cliente** pide metadatos de la cámara: `GET /api/v1/cameras/<id>` (REST, JWT). *Genera:* JSON con `stream_url`, `stream_urls{main|l1|l2}{high|medium|low}`, `hls_url`, `webrtc_url`.
2. **`Go2RtcManager.rtsp_restream_url()`** construye `rtsp://<host_lan>:8554/cam_<id>[_l1][_medium]`.
3. **`RtspVideoWidget`** (PySide6) instancia `VLCPlayer` con flags `--network-caching=150 --rtsp-tcp --drop-late-frames --no-audio-time-stretch` y hace `play_url(url)`.
4. **VLC** conecta a go2rtc:8554 (RTSP/TCP). *Protocolo:* RTSP (control) + RTP (medios) dentro de la sesión TCP interleaved.
5. **go2rtc** entrega el H.264 ya ingerido de la cámara **sin recodificar** (`-c copy`) → VLC decodifica (HW/SW) y dibuja.

**Paso a paso (navegador, WebRTC):**
1. Cliente crea `RTCPeerConnection`, genera **SDP offer**, lo envía: `POST /api/v1/cameras/<id>/webrtc` (`application/sdp`, JWT, permiso `view`).
2. **`WebRTCSignalingService.exchange()`** reenvía el offer a `http://127.0.0.1:1984/api/webrtc?src=cam_<id>` y devuelve el **SDP answer**.
3. Cliente y go2rtc negocian **ICE** (candidatos host de la IP LAN, `:8555`), establecen conexión **P2P UDP**; el vídeo viaja directo (no por Flask). *Latencia típica sub-segundo.*

**Paso a paso (Android, HLS):**
1. `LiveViewFragment` obtiene `hls_url` = `http://<host>:1984/api/stream.m3u8?src=cam_<id>_l1_medium`.
2. **ExoPlayer** descarga el manifiesto `.m3u8` y segmentos `.ts` por **HTTP**; go2rtc los genera bajo demanda.

**Formatos:** vídeo H.264/H.265 (cámara) → contenedor según protocolo (RTP para WebRTC/RTSP, MPEG-TS para HLS). **Latencia:** WebRTC < RTSP < HLS. El mayor factor de latencia es el **GOP/I-frame de la cámara** (ver tuning en CLAUDE.md), no el backend.

---

## 3.2 Pipeline de grabación (stream → almacenamiento)

```
Cámara/restream ─RTSP─► FFmpeg (RecordingManager) ─► segmentos MP4 ─► disco
                                                  └─► fila Recording (BD)
```

**Paso a paso (grabación continua):**
1. **`RecordingManager`** lanza `ffmpeg -rtsp_transport tcp -i <fuente> -c copy -f segment -segment_time 120 -reset_timestamps 1 ... cam_<id>_%Y%m%d_%H%M%S.mp4`. *Fuente:* restream go2rtc (`rtsp://127.0.0.1:8554/cam_<id>`) si `GO2RTC_AS_SOURCE`, o la cámara directa.
2. **`-c copy`**: no recodifica (CPU≈0); copia el H.264 al contenedor MP4. *Requiere keyframes para cortes limpios.*
3. Al cerrar cada segmento, se inserta una fila **`Recording`** (camera_id, start_time, end_time, file_path, file_size_bytes, duration_seconds).
4. **`StorageManager`** vigila la cuota (`MAX_STORAGE_GB`) y borra los más antiguos.
5. **`ConsistencyChecker`** reconcilia BD↔FS periódicamente.

**Datos:** entra H.264 (RTP/RTSP); sale MP4 (`.mp4`) + metadatos. **Protocolos:** RTSP/TCP (ingesta), sistema de archivos (salida). **Riesgo defendible:** segmentos sin `end_time` = grabación en curso o crash; el checker los limpia.

---

## 3.3 Pipeline de detección IA (frame → detección)

```
go2rtc cam_X_low ─RTSP─► AIFrameSource (FFmpeg) ─► frame 640x384 bgr24
        ─► AIScheduler ─► MotionDetector (gate) ─► YOLOv8 ─► detecciones ─► EventData
```

**Paso a paso:**
1. **`AIFrameSource`** lanza `ffmpeg -rtsp_transport tcp -fflags nobuffer -flags low_delay -i rtsp://127.0.0.1:8554/cam_<id>_low -an -vf "scale=640:384:force_original_aspect_ratio=decrease,pad=640:384:(ow-iw)/2:(oh-ih)/2,fps=6" -pix_fmt bgr24 -f rawvideo -`. *Genera:* frames `640×384×3` (737 KB) *latest-frame* (newest-wins).
2. **`AIScheduler`** (hilo worker) toma el frame más reciente.
3. **`MotionDetector`** decide si hay movimiento (OpenCV). Si **no**, se descarta sin invocar YOLO (ahorro de CPU).
4. Si **sí**, **`YLOModelPool`** ejecuta `model.predict(frame, conf=AI_CONFIDENCE, classes=[...])`. *Genera:* cajas `[x1,y1,x2,y2]`, clase, confianza.
5. **Filtrado:** sólo clases permitidas (person/vehicle…) y `conf ≥ AI_CONFIDENCE (≈0.35)`.
6. **Cooldown por clase** (`AI_EVENT_COOLDOWN_SECONDS≈30`): si ya se alertó de "person" hace <30 s, no se re-emite.
7. Se publica **`EventData`** (event_type, camera_id, confidence, bbox, frame/snapshot) al **`EventManager`**.

**Datos:** entra frame BGR; sale lista de detecciones → evento. **Formatos:** numpy `uint8 HxWx3`; bbox en píxeles del frame 640×384. (Matemática en [08_IA.md](08_IA.md).)

---

## 3.4 Pipeline de generación de eventos

```
Productor (IA / movimiento / FFmpeg offline) ─► EventManager.publish(EventData)
   ─► [pool 8 hilos] ─┬─► EventService ─► fila Event (BD) + snapshot JPEG
                      ├─► RecordingManager ─► clip de evento
                      ├─► NotificationRouter ─► notificaciones (Telegram/WS)
                      └─► MetricsCollector ─► métricas
```

**Paso a paso:**
1. Un productor crea `EventData` y llama `EventManager.publish(...)`.
2. El bus despacha **en paralelo** a cada suscriptor (pool de 8 hilos), aislando fallos.
3. **`EventService`** persiste la fila `Event` (`event_type`, `confidence`, `snapshot_path`, `clip_path`, `acknowledged=False`, `created_at`) y guarda el **snapshot** (JPEG, optimizado por `image_optimizer`).
4. Otros tipos de evento del sistema: `camera_offline`/`camera_reconnected` (los publica el monitor/worker), `tampering`.

**Datos:** `EventData` in-memory → fila `Event` + archivo JPEG. **Índices BD:** `(camera_id, created_at)`, `event_type`, `(acknowledged, created_at)` para queries de UI ("no leídos recientes").

---

## 3.5 Pipeline de generación de clips

```
Evento ─► RecordingManager.make_clip()
   ├─ Camino A: SPLICE de segmentos continuos (pre 10s + post 10s) con FFmpeg
   └─ Camino B (fallback): buffer RAM de pre-roll + grabación post
   ─► archivo clip MP4 ─► Event.clip_path (BD)
```

**Paso a paso:**
1. Llega un evento con `created_at = T`.
2. Se define ventana `[T-10s, T+10s]` (pre/post-roll).
3. **Camino A (preferido):** localizar los segmentos continuos que cubren la ventana y *splice*/recorte con FFmpeg (`-ss`/`-to`, `-c copy` cuando los keyframes lo permiten) → un MP4 de ~20 s.
4. **Camino B (fallback):** si no hay grabación continua, usar el pre-roll que el manager mantiene en RAM + grabar el post-roll en vivo.
5. Actualizar `Event.clip_path`. El clip se adjunta a la alerta (Telegram) y se ve en la UI.

**Datos:** segmentos MP4 → clip MP4. **Protocolo:** sistema de archivos + FFmpeg. **Riesgo:** cortes en `-c copy` sólo son exactos en keyframes; si se requiere precisión de frame hay que recodificar (más CPU).

---

## 3.6 Pipeline de notificaciones

```
EventData ─► EventManager ─► (suscriptores)
   ├─ TelegramNotifier ─HTTPS─► Telegram Bot API (sendPhoto/sendMessage) ─► chats vinculados
   ├─ ws_broker ─WebSocket─► clientes LAN (escritorio/móvil) en tiempo real
   └─ NotificationRouter ─► filtra por NotificationPreference (tipo, cámara, horario, días, canal)
        └─► NotificationLog (BD) con cooldown_key (anti-flood)
```

**Paso a paso:**
1. El evento llega a los suscriptores de notificación.
2. **`NotificationRouter`** resuelve, por cada usuario con acceso a la cámara, sus `NotificationPreference` (¿habilitado para ese `event_type`+cámara?, ¿dentro de `schedule_start/end` y `NotificationDay`?, ¿qué canales?).
3. **Anti-flood:** se consulta `NotificationLog.cooldown_key = "camera:<id>:<event_type>"`; si hubo envío reciente, se omite.
4. **Telegram:** `TelegramNotifier` hace `POST https://api.telegram.org/bot<token>/sendPhoto` con `chat_id` (de `UserTelegramChat`), la foto del snapshot y un caption. *Protocolo:* HTTPS. *Requiere internet.*
5. **WebSocket:** `ws_broker` difunde un JSON `{type:"event", camera_id, event_type, ...}` a los clientes conectados (`/api/v1/ws`). *Protocolo:* WebSocket sobre la LAN (sin internet).
6. Se registra `NotificationLog` (channel, status `sent|failed`, error).

**Vinculación de Telegram (sub-pipeline):**
1. Usuario pide vincular → `telegram_link_service` crea `TelegramVerificationCode` (6–8 dígitos, expira en 5 min) y muestra un **deep-link**/QR `https://t.me/<bot>?start=<code>`.
2. El usuario abre el chat y envía `/start <code>`; el **bot poller** (`getUpdates`) recibe el `chat_id`, valida el código y crea `UserTelegramChat`.

**Datos:** evento → mensaje Telegram (HTTPS) + JSON WS. **Formatos:** multipart/form-data (foto Telegram), JSON (WS).

---

## 3.7 Pipeline de reproducción histórica (playback)

```
Cliente ─REST(JWT)─► /recordings/<id>/playback-url ─► SignedUrlService.sign() ─► URL firmada
Reproductor ─HTTP(Range)─► /recordings/<id>/media?token=... ─► MP4 (206 Partial Content)
```

**Paso a paso:**
1. Cliente autenticado lista grabaciones: `GET /api/v1/recordings/?camera_id=&date=&limit=` (JWT). *Genera:* timeline (id, start_time, end_time, filename, duration, file_size).
2. Pide URL de reproducción: `GET /recordings/<id>/playback-url` → backend valida permiso (`view`/`download`) y devuelve `/recordings/<id>/media?token=v1.<exp>.<sig>`.
3. **`SignedUrlService`** firmó `recording:<id>` con **HMAC-SHA256** y caducidad corta (≈5 min). *Por qué:* ExoPlayer/VLC/AVPlayer **no** envían header `Authorization` al pedir medios; la URL firmada autoriza sin JWT.
4. El reproductor pide el medio con cabeceras **`Range: bytes=...`**; el backend verifica la firma (tiempo constante, `hmac.compare_digest`) + caducidad y responde **206** con el rango (permite *seek*).
5. **Móvil/VOD HLS:** alternativamente `HLSService` remuxea el MP4 a `.m3u8`+`.ts` para ExoPlayer.

**Datos:** id de grabación → bytes MP4 por rango. **Protocolos:** REST/JWT (autorización) + HTTP Range (medios). **Seguridad:** la firma incluye el recurso y la expiración; no se puede reutilizar para otra grabación ni tras caducar.

---

## 3.8 Pipeline de autenticación

```
POST /auth/login {user,pass} ─► verify_password (PBKDF2) ─► create_access(15m)+create_refresh(7d)
Endpoint protegido ─► @jwt_required ─► firma+exp+blocklist(jti) ─► @require_camera_permission
POST /auth/refresh (refresh JWT) ─► nuevo access
POST /auth/logout ─► jwt_blocklist.revoke(jti, exp)  (memoria + tabla revoked_tokens)
```

**Paso a paso (login):**
1. `POST /api/v1/auth/login` con `{username, password}` (HTTP/JSON).
2. **`auth_service.login`**: busca `User`, `verify_password(plain, password_hash)` (PBKDF2:SHA256), comprueba `is_active`.
3. Genera **access token** (claims `sub=user.id`, `role`, `jti`, `exp=+15min`) y **refresh token** (`exp=+7días`).
4. Devuelve `{access_token, refresh_token, user}`.

**Paso a paso (acceso a endpoint):**
1. Cliente envía `Authorization: Bearer <access>`.
2. **`@jwt_required()`** valida firma con `JWT_SECRET_KEY` + expiración + que `jti` **no** esté en `JWTBlocklist.is_revoked()`.
3. **`@require_camera_permission("view")`** llama `PermissionService.check_permission(user_id, camera_id, "view")` (admin→owner→`UserCameraPermission`). Si deniega → **403**.

**Paso a paso (refresh/logout):**
- `POST /auth/refresh` (con refresh token) → nuevo access.
- `POST /auth/logout` → `jwt_blocklist.revoke(jti, exp)`: añade a dict en memoria (rápido) y persiste en `revoked_tokens` (sobrevive reinicios; se rehidrata al arrancar).

**Onboarding inicial:** `GET /auth/setup-status` (¿hay admin?) → `POST /auth/setup` crea el primer admin.
**Datos:** credenciales → tokens firmados. **Protocolos:** HTTP(S)/JSON, JWT (HS256).

---

## 3.9 Pipeline ONVIF (descubrimiento → alta)

```
Cliente ─► /discovery ─► ONVIFDiscovery.discover()
   1) WS-Discovery multicast 239.255.255.250:3702 (Probe ─► ProbeMatch)
   2) pre-check TCP (¿IP alcanzable?)
   3) FAST PATH: SOAP directo (GetCapabilities/GetProfiles/GetStreamUri)
   4) SLOW PATH: onvif-zeep (WSDL local)
   5) Fallback RTSP (patrones genéricos)
   ─► dict por cámara (rtsp_url, onvif_url, profile_token, has_ptz/audio, resolución…)
   ─► usuario confirma ─► POST /cameras ─► fila Camera (BD)
```

**Paso a paso:**
1. `POST /api/v1/discovery/discover?timeout=&subnet_scan=` (JWT).
2. **WS-Discovery:** envío UDP multicast de un `Probe` SOAP a `239.255.255.250:3702`; cada cámara responde con `ProbeMatch` (contiene `XAddrs` = endpoint del device service).
3. **Pre-check TCP:** si la IP no es alcanzable (p.ej. IP estática de otra subred), se marca `unreachable` con instrucciones.
4. **FAST PATH:** `onvif_soap` prueba un puñado de credenciales × puertos en pocos segundos; si obtiene auth + perfiles + stream URI, devuelve el dict.
5. **SLOW PATH:** `onvif_common` carga WSDL local y usa zeep para casos difíciles.
6. **Fallback RTSP:** si ONVIF falla, prueba patrones RTSP conocidos → `connection_type="rtsp_fallback"`.
7. El usuario confirma y `POST /api/v1/cameras` persiste `Camera` (url, credenciales, `profile_token`, capacidades, diagnóstico).

**Datos:** XML SOAP ↔ dict de cámara. **Protocolos:** WS-Discovery (UDP multicast), SOAP/HTTP (control), RTSP (fallback). (Sobres XML reales en [05_ONVIF.md](05_ONVIF.md).)

---

## 3.10 Pipeline PTZ (control de movimiento)

```
Cliente ─REST(JWT, perm ptz)─► /cameras/<id>/ptz/move?direction=&speed=
   ─► ptz_lock_service.acquire_lock (mutex con timeout)
   ─► PTZController.move() ─► SOAP ContinuousMove (Velocity{PanTilt,Zoom}) ─► cámara
   ─► (stop) PTZController.stop() ─► SOAP Stop
```

**Paso a paso:**
1. `PUT/POST /api/v1/cameras/<id>/ptz/move?direction=right&speed=0.5` (JWT + permiso `control_ptz`).
2. **`PTZLockService.acquire_lock(camera_id, user_id)`**: mutex en memoria con timeout (p.ej. 30 s) para que dos usuarios no muevan a la vez; si está tomado, devuelve "en uso por <alice> (Ns restantes)".
3. **`PTZController`** (cacheado por cámara) conecta por el puerto ONVIF correcto (itera candidatos; persiste el que funciona), elige el primer perfil con `PTZConfiguration`, valida con `GetStatus`.
4. **`move()`** construye `ContinuousMove` con `Velocity = {PanTilt:{x:±speed, y:±speed}, Zoom:{x:±speed}}` y lo envía como **SOAP** al `ptz_service`.
5. Al soltar el control, `Stop` (PanTilt+Zoom).
6. **Sin PTZ:** si ningún perfil tiene `PTZConfiguration`, `is_supported()=False` y la UI oculta los controles.

**Datos:** dirección+velocidad → sobre SOAP `ContinuousMove`. **Protocolo:** SOAP/HTTP. **Concurrencia:** serializada por el lock. (Detalle SOAP en [05_ONVIF.md](05_ONVIF.md).)

---

## 3.11 Tabla resumen de pipelines

| Pipeline | Entrada | Salida | Protocolos | Formatos |
|---|---|---|---|---|
| Vivo | RTSP cámara | vídeo en cliente | RTSP/RTP, WebRTC, HLS | H.264/H.265, MPEG-TS |
| Grabación | RTSP | MP4 + fila Recording | RTSP, FS | MP4 (`-c copy`) |
| IA | RTSP low | EventData | RTSP, in-proc | numpy bgr24, bbox |
| Eventos | EventData | fila Event + JPEG | in-proc, FS | JSON/dataclass, JPEG |
| Clips | segmentos MP4 | clip MP4 | FS, FFmpeg | MP4 |
| Notificaciones | EventData | Telegram + WS | HTTPS, WebSocket | multipart, JSON |
| Playback | recording_id | bytes MP4 / HLS | REST/JWT, HTTP Range, HLS | MP4, m3u8/ts |
| Auth | credenciales | JWT | HTTP/JSON, JWT | HS256 |
| ONVIF | multicast | dict cámara | WS-Discovery, SOAP, RTSP | XML/SOAP |
| PTZ | dirección+velocidad | movimiento físico | SOAP/HTTP | XML/SOAP |
