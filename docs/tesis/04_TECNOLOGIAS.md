# PARTE 4 — Explicación de Tecnologías (nivel defensa de tesis)

> Para cada tecnología: **qué es · cómo funciona internamente · qué envía · qué recibe · cuándo se usa en el proyecto · por qué se eligió · ventajas · desventajas · alternativas consideradas.**

---

## 4.1 RTSP (Real Time Streaming Protocol)

- **Qué es:** protocolo de **control** de sesión de streaming (RFC 2326/7826), el "mando a distancia" del vídeo (PLAY, PAUSE, TEARDOWN). No transporta el vídeo en sí; negocia cómo y dónde se transporta.
- **Cómo funciona:** cliente→servidor con métodos tipo HTTP: `OPTIONS`, `DESCRIBE` (devuelve un **SDP** con codecs/pistas), `SETUP` (acuerda el transporte: RTP/UDP o RTP/TCP-interleaved), `PLAY`, `TEARDOWN`. Mantiene estado de sesión (`Session:` id).
- **Qué envía/recibe:** envía comandos de control; recibe SDP y luego flujos **RTP** (medios) + **RTCP** (estadísticas).
- **Cuándo se usa aquí:** la cámara expone `rtsp://...`; lo consumen go2rtc (ingesta), el `FFmpegWorker`/`AIFrameSource` (si leen restream), `RecordingManager`, y VLC en el escritorio (restream de go2rtc).
- **Por qué:** es el estándar universal de cámaras IP.
- **Ventajas:** ubicuo, soporta TCP (fiable) y UDP (baja latencia), control de sesión.
- **Desventajas:** no atraviesa NAT/firewalls fácilmente; las cámaras baratas limitan conexiones; latencia ligada al GOP.
- **Alternativas:** RTMP (obsoleto, orientado a publicación), SRT (mejor en redes con pérdida pero menos soporte en cámaras).

## 4.2 RTP (Real-time Transport Protocol)

- **Qué es:** protocolo de **transporte de medios** (RFC 3550) sobre UDP (o TCP interleaved). Lleva el vídeo/audio "troceado" en paquetes.
- **Cómo funciona:** cada paquete tiene cabecera con **secuencia** (detecta pérdidas/reordena), **timestamp** (sincroniza reproducción), **SSRC** (identifica la fuente) y **payload type** (codec). El payload son unidades del codec (p.ej. NAL units de H.264).
- **Qué envía/recibe:** envía paquetes de medios; el receptor reordena por secuencia y reconstruye usando timestamps.
- **Cuándo se usa aquí:** dentro de las sesiones RTSP (cámara↔go2rtc, go2rtc↔VLC) y como medio en WebRTC.
- **Ventajas:** ligero, tiempo real, tolera pérdidas (UDP).
- **Desventajas:** sin fiabilidad por sí mismo (depende de RTCP/retransmisiones a nivel superior).
- **Alternativas:** transporte directo en MPEG-TS (HLS) para escenarios no-RTP.

## 4.3 RTCP (RTP Control Protocol)

- **Qué es:** canal de **control/estadística** que acompaña a RTP (mismo RFC 3550).
- **Cómo funciona:** envía **Sender Reports / Receiver Reports** con jitter, pérdida de paquetes y marcas para **sincronización A/V** (NTP↔RTP timestamp). Permite adaptar calidad.
- **Cuándo se usa aquí:** implícito en RTSP/WebRTC (lo gestionan FFmpeg/go2rtc/VLC); el proyecto no lo manipula directamente, pero su existencia explica la sincronización y el control de calidad.
- **Ventajas:** feedback de calidad sin tocar el flujo de medios.
- **Desventajas:** overhead pequeño; poco útil si no se actúa sobre sus reportes.

## 4.4 HLS (HTTP Live Streaming)

- **Qué es:** streaming sobre **HTTP** (Apple) que parte el vídeo en **segmentos** (`.ts`/`.m4s`) descritos por un **manifiesto** `.m3u8`.
- **Cómo funciona:** el cliente descarga el `.m3u8`, ve la lista de segmentos, y los pide por HTTP en orden; para "directo", el manifiesto se actualiza. Soporta múltiples *bitrates* (ABR).
- **Qué envía/recibe:** el cliente envía GET HTTP; recibe manifiesto + segmentos MPEG-TS.
- **Cuándo se usa aquí:** **directo** en móvil (go2rtc `/api/stream.m3u8?src=cam_X`) y **VOD** de grabaciones (`HLSService` remuxea MP4→HLS).
- **Por qué:** ExoPlayer lo reproduce de forma robusta; atraviesa NAT/proxies (es sólo HTTP).
- **Ventajas:** compatible con todo, cacheable, fiable en WiFi.
- **Desventajas:** **latencia alta** (segmentos de varios segundos); por eso no se usa para el caso de mínima latencia (ahí WebRTC).
- **Alternativas:** MPEG-DASH (equivalente abierto), LL-HLS (baja latencia, más complejo).

## 4.5 WebRTC

- **Qué es:** stack de **comunicación en tiempo real P2P** en navegadores/apps; latencia sub-segundo.
- **Cómo funciona:** (1) **Señalización** (fuera de banda) intercambia **SDP** offer/answer; (2) **ICE** descubre candidatos (host/STUN/TURN) y elige un par para la conexión; (3) el medio viaja por **SRTP** (RTP cifrado) directo entre pares; **DTLS** negocia las claves.
- **Qué envía/recibe:** señalización SDP (por el backend) + medios SRTP (P2P).
- **Cuándo se usa aquí:** vivo de mínima latencia; el backend (`WebRTCSignalingService`) hace de proxy de señalización WHEP hacia go2rtc; los candidatos ICE son la IP LAN (`HOST_LAN_IP:8555`).
- **Por qué:** la mejor latencia para vivo en navegador.
- **Ventajas:** sub-segundo, cifrado obligatorio, P2P (descarga al servidor del medio).
- **Desventajas:** complejo (ICE/STUN/TURN); en LAN aislada hay que fijar candidatos host; soporte móvil nativo pendiente en este proyecto.
- **Alternativas:** RTSP de baja latencia (lo que usa el escritorio con VLC), MSE+WebSocket.

## 4.6 WebSocket

- **Qué es:** canal **full-duplex** persistente sobre una única conexión TCP, iniciado con un *upgrade* HTTP.
- **Cómo funciona:** handshake HTTP `Upgrade: websocket` → a partir de ahí, mensajes en *frames* bidireccionales sin el overhead de re-abrir HTTP.
- **Qué envía/recibe:** mensajes JSON en ambos sentidos.
- **Cuándo se usa aquí:** **notificaciones en tiempo real** (`ws_broker` → escritorio/móvil) sin internet; en Android, un **foreground service** mantiene el WS abierto.
- **Por qué:** push instantáneo en LAN sin depender de FCM/Telegram.
- **Ventajas:** baja latencia, bidireccional, sin *polling*.
- **Desventajas:** conexión que hay que mantener viva/reconectar; consume batería en móvil.
- **Alternativas:** SSE (unidireccional), long-polling, FCM (push del SO, requiere Google).

## 4.7 HTTP / HTTPS

- **Qué es:** protocolo petición/respuesta sin estado; HTTPS = HTTP sobre **TLS** (cifrado + autenticación de servidor).
- **Cómo funciona:** métodos (GET/POST/PUT/DELETE), cabeceras, cuerpos; TLS añade handshake con certificados y cifrado simétrico de sesión.
- **Cuándo se usa aquí:** **REST API** del backend (HTTP en LAN), descarga de medios (HTTP **Range** para *seek*), HLS, y **HTTPS** hacia la **Telegram Bot API**.
- **Ventajas:** universal, cacheable, atraviesa NAT.
- **Desventajas:** sin estado (de ahí JWT); en LAN se usa HTTP plano (riesgo de sniffing intra-LAN).
- **Alternativas:** gRPC (no encaja con clientes heterogéneos/medios).

## 4.8 REST

- **Qué es:** estilo arquitectónico para APIs sobre HTTP basado en **recursos** (`/cameras/<id>`), **verbos** y **representaciones** (JSON).
- **Cómo funciona aquí:** blueprints Flask por recurso (`auth`, `cameras`, `recordings`, `events`, `permissions`, `ai`, `system`…); respuestas JSON `{success, data|error}`.
- **Por qué:** simple, interoperable con escritorio (requests) y móvil (Retrofit).
- **Ventajas:** stateless, fácil de versionar (`/api/v1`), cacheable.
- **Desventajas:** *over/under-fetching* (mitigable con parámetros).
- **Alternativas:** GraphQL (innecesario para este dominio acotado), gRPC.

## 4.9 JWT (JSON Web Token)

- **Qué es:** token firmado y auto-contenido: `header.payload.signature` en base64url.
- **Cómo funciona:** el payload lleva **claims** (`sub`=user.id, `role`, `exp`, `iat`, `jti`); la firma (HS256 con `JWT_SECRET_KEY`) garantiza integridad. El servidor valida la firma sin guardar sesión.
- **Qué envía/recibe:** el cliente envía `Authorization: Bearer <token>`; el servidor valida y extrae claims.
- **Cuándo se usa aquí:** **access** (15 min) para endpoints, **refresh** (7 días) para renovar; **blocklist** por `jti` (memoria + tabla `revoked_tokens`) para logout/revocación; además **URLs firmadas** HMAC para medios (porque los reproductores no mandan el header).
- **Ventajas:** stateless, escalable, cómodo para móvil.
- **Desventajas:** revocar exige blocklist (rompe lo stateless); si se filtra el secreto, todos los tokens caen.
- **Alternativas:** sesiones server-side (estado), OAuth2 completo (sobredimensionado para LAN).

## 4.10 ONVIF

- **Qué es:** estándar de interoperabilidad para dispositivos de videovigilancia IP (perfiles S, T, G…). Define servicios web (SOAP) para device, media, PTZ, imaging, events.
- **Cómo funciona:** descubrimiento por **WS-Discovery**; control por **SOAP** (GetCapabilities, GetProfiles, GetStreamUri, PTZ ContinuousMove…); seguridad por **WS-UsernameToken**.
- **Cuándo se usa aquí:** descubrir cámaras, obtener RTSP URI y perfiles, controlar PTZ, audio, LEDs, hora.
- **Ventajas:** independiente del fabricante, rico (PTZ, eventos, imaging).
- **Desventajas:** implementaciones inconsistentes entre cámaras (hay que poner fallbacks/heurísticas).
- **Alternativas:** protocolos propietarios (Hikvision ISAPI, Dahua) — menos portables.
(Capítulo completo en [05_ONVIF.md](05_ONVIF.md).)

## 4.11 SOAP

- **Qué es:** protocolo de mensajería **XML** para servicios web; mensajes = "sobres" (`Envelope` → `Header` + `Body`).
- **Cómo funciona:** el `Header` lleva seguridad (WS-Security UsernameToken con nonce/created/password-digest); el `Body` lleva la operación (p.ej. `GetProfiles`). Se envía por HTTP POST con `SOAPAction`.
- **Cuándo se usa aquí:** toda la comunicación ONVIF de control. `onvif_soap.py` construye los sobres a mano (sin WSDL) para el FAST PATH.
- **Ventajas:** contrato formal (WSDL/XSD), seguridad estándar.
- **Desventajas:** verboso, pesado vs REST/JSON.
- **Alternativas:** REST/JSON (no es lo que hablan las cámaras ONVIF).

## 4.12 XML

- **Qué es:** lenguaje de marcado jerárquico con espacios de nombres (`xmlns`).
- **Cómo se usa aquí:** formato de todos los mensajes SOAP/ONVIF y WS-Discovery; se parsea con `ElementTree`/zeep.
- **Ventajas:** estándar, namespaces, validable por esquema.
- **Desventajas:** verboso; parsing más costoso que JSON.

## 4.13 WS-Discovery

- **Qué es:** descubrimiento de servicios en LAN por **multicast UDP** (grupo `239.255.255.250:3702`).
- **Cómo funciona:** el cliente envía un **`Probe`** (SOAP) buscando `NetworkVideoTransmitter`; los dispositivos responden con **`ProbeMatch`** que incluye `XAddrs` (URL del device service) y `Types/Scopes`.
- **Cuándo se usa aquí:** `onvif_discovery.discover()` para encontrar cámaras sin conocer su IP.
- **Ventajas:** *zero-config*, no requiere saber IPs.
- **Desventajas:** multicast puede bloquearse por switches/VLAN/Windows firewall; no cruza subredes.
- **Alternativas:** escaneo de subred (también implementado como complemento).

## 4.14 FFmpeg

- **Qué es:** suite de procesamiento multimedia (libav*) por línea de comandos: decodifica, codifica, remuxea, filtra.
- **Cómo funciona aquí:** se lanza como **subproceso**; el proyecto lo usa para (a) captura RTSP→`rawvideo bgr24` (worker/IA), (b) grabación `-c copy` a MP4 segmentado, (c) clips (recorte/splice), (d) audio (ffplay escucha; FFmpeg→RTP habla), (e) `ffprobe` para resolución real.
- **Flags de baja latencia (defender):** `-probesize 32 -analyzeduration 0 -fflags nobuffer+flush_packets -flags low_delay -flags2 +fast`.
- **Ventajas:** lo decodifica/codifica todo; fuera del GIL (C); flags finos.
- **Desventajas:** API por CLI/strings (frágil); gestionar subprocesos/zombies.
- **Alternativas:** GStreamer (más complejo de desplegar en Windows), OpenCV VideoCapture (más lento por analyzeduration).

## 4.15 go2rtc

- **Qué es:** servidor de medios ligero que ingiere una fuente (RTSP, etc.) y la re-expone como **WebRTC/RTSP/HLS/MSE** desde **una** conexión.
- **Cómo funciona aquí:** sidecar configurado por `go2rtc.yaml` (generado desde la BD); define streams `cam_X` (`-c copy`) y derivados (`_medium/_low`, dual-lens `_l1/_l2` con transcode QSV).
- **Por qué:** resuelve el límite de conexiones de las cámaras y entrega WebRTC sub-segundo.
- **Ventajas:** multiprotocolo, `-c copy` (CPU≈0), dual-lens, multi-calidad.
- **Desventajas:** binario externo a supervisar; ICE en LAN requiere candidatos host.
- **Alternativas:** MediaMTX (rtsp-simple-server), Janus/mediasoup (SFUs pesados).
(Capítulo completo en [06_GO2RTC.md](06_GO2RTC.md).)

## 4.16 YOLO (You Only Look Once) — YOLOv8

- **Qué es:** detector de objetos *single-shot* (una pasada de CNN devuelve cajas + clases + confianza).
- **Cómo funciona:** una red convolucional divide la imagen y predice, en una sola inferencia, *bounding boxes*, *objectness* y probabilidades de clase; YOLOv8 es *anchor-free* y trae NMS integrado.
- **Cuándo se usa aquí:** modelo "nano" (`.pt`) en CPU vía `ultralytics`; gateado por movimiento; clases person/vehicle.
- **Ventajas:** rápido, preciso, fácil (`model.predict`).
- **Desventajas:** en CPU limita FPS (de ahí: una cámara, low-res, gate de movimiento).
- **Alternativas:** SSD/MobileNet (más ligero, menos preciso), Faster R-CNN (preciso, lento).
(Matemática en [08_IA.md](08_IA.md).)

## 4.17 OpenCV

- **Qué es:** librería de visión por computador (operaciones de imagen vectorizadas en C++/numpy).
- **Cómo se usa aquí:** **detección de movimiento** (diferencias/umbral), manipulación de frames, dibujo de cajas, codificación **JPEG** de snapshots, redimensionado.
- **Ventajas:** rápida, madura, integra con numpy.
- **Desventajas:** API extensa; algunos algoritmos básicos requieren tuning.
- **Alternativas:** Pillow (más lento), scikit-image (científico, menos tiempo real).

## 4.18 PySide6 (Qt6)

- **Qué es:** *bindings* oficiales de Qt6 para Python (UI nativa multiplataforma).
- **Cómo se usa aquí:** cliente de escritorio (ventana, vistas apiladas, mosaico de vivo, timeline, joystick PTZ); `QThreadPool` para I/O sin bloquear la UI; integra **libVLC** dibujando en el `winId()` del widget.
- **Ventajas:** nativo, potente, multiplataforma, señales/slots.
- **Desventajas:** curva de aprendizaje; empaquetado pesado.
- **Alternativas:** Tkinter (básico), Electron (web, más pesado).

## 4.19 PostgreSQL

- **Qué es:** RDBMS relacional ACID, robusto y concurrente.
- **Cómo se usa aquí:** 16 tablas (usuarios, cámaras, permisos, eventos, grabaciones, notificaciones, tokens…), acceso por SQLAlchemy 2.0 con `QueuePool` (10+20).
- **Ventajas:** integridad (FK/cascadas), concurrencia, tipos ricos (Time/DateTime), índices.
- **Desventajas:** servicio extra a desplegar.
- **Alternativas:** SQLite (legado; sufre con escritores concurrentes), MySQL/MariaDB.

## 4.20 Docker

- **Qué es:** contenedores para empaquetar la app con sus dependencias.
- **Cómo se usa aquí (opcional):** `docker compose up` levanta PostgreSQL + backend (que **incluye** FFmpeg y el binario go2rtc); **bridge networking** con puertos publicados (funciona en Windows); `HOST_LAN_IP` para ICE de WebRTC; instala desde `requirements-backend.txt` (sin PySide6, la GUI corre nativa).
- **Ventajas:** reproducibilidad, despliegue homogéneo.
- **Desventajas:** WebRTC/multicast en contenedores requiere cuidado (host networking/candidatos).
- **Alternativas:** instalación nativa (modo principal del proyecto).

## 4.21 Alembic

- **Qué es:** herramienta de **migraciones** de esquema para SQLAlchemy (versiona cambios de BD).
- **Cómo se usa aquí:** configurado (`alembic.ini`, `migrations/`), aunque `main.py` también llama `Base.metadata.create_all()` al arrancar, de modo que una BD nueva funciona sin correr `alembic upgrade head`. *Estado real:* el esquema se crea principalmente por `create_all`; las migraciones versionadas están como infraestructura para producción.
- **Ventajas:** evolución controlada del esquema, *upgrade/downgrade*.
- **Desventajas:** mantener migraciones al día requiere disciplina.
- **Alternativas:** Django migrations (no aplica), DDL manual.

---

### Mapa "tecnología ↔ dónde vive en el código"

| Tecnología | Archivo(s) clave |
|---|---|
| RTSP/RTP/FFmpeg | `workers/ffmpeg_worker.py`, `processing/ai/ai_frame_source.py`, `recording/recording_manager.py` |
| go2rtc/WebRTC/HLS | `streaming/go2rtc_manager.py`, `webrtc_signaling.py`, `hls_service.py` |
| WebSocket | `notifications/ws_broker.py`, `api/routes/ws.py`, Android `NotificationWsClient`/`NotificationWebSocketService` |
| REST/HTTP | `api/routes/*.py`, desktop `services/api_client.py`, Android `ApiService` |
| JWT | `services/auth_service.py`, `core/security.py`, `core/jwt_blocklist.py`, `services/signed_url_service.py` |
| ONVIF/SOAP/WS-Discovery | `cameras/onvif_discovery.py`, `onvif_soap.py`, `onvif_common.py`, `ptz_controller.py` |
| YOLO/OpenCV | `processing/ai/model_pool.py`, `ai_scheduler.py`, `processing/motion/motion_detector.py` |
| PySide6/VLC | `desktop_app/src/ui/**`, `services/playback_service.py`, `ui/components/rtsp_video.py` |
| PostgreSQL/SQLAlchemy/Alembic | `database/models.py`, `database/connection.py`, `database/migrations/` |
