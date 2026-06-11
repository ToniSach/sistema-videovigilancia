# PARTE 9 — Banco de Preguntas de Sinodales (110+)

> Formato por pregunta: **(I)** respuesta ideal · **(C)** respuesta corta · **(S)** preguntas de seguimiento probables. Agrupadas por tema.

---

## A. Arquitectura

**1. ¿Por qué el backend debe correr en un solo proceso?**
- **(I)** Porque casi todos los componentes de larga vida (`CameraManager`, `DatabaseManager`, `EventManager`, `GlobalExecutor`, `Go2RtcManager`, etc.) son singletons que mantienen hilos, subprocesos FFmpeg, buffers, pools de modelos y de BD **en memoria del proceso**. Con múltiples workers WSGI habría N copias del estado: N conexiones a cada cámara (saturando cámaras que aceptan 1–4), N pools de BD, N modelos cargados, eventos duplicados. Por eso uso `app.run(threaded=True)`: un proceso con pool de hilos.
- **(C)** Porque el estado vive en singletons en memoria; multi-worker los duplicaría y rompería la coordinación de cámaras, IA y grabación.
- **(S)** ¿Cómo escalarías a más cámaras entonces? ¿Qué moverías a Redis para permitir multi-worker?

**2. ¿No es el GIL de Python un problema para un sistema de vídeo?**
- **(I)** El trabajo pesado de CPU no ocurre en Python: la decodificación/codificación la hace **FFmpeg/go2rtc** (procesos en C), y la inferencia YOLO libera el GIL en las operaciones de tensor de PyTorch. Python orquesta y mueve numpy. El GIL limita el paralelismo de bytecode puro, que aquí es mínimo. Además uso subprocesos e hilos de I/O donde el GIL se libera.
- **(C)** El cómputo pesado está fuera del GIL (FFmpeg, go2rtc, PyTorch); Python sólo orquesta.
- **(S)** ¿Dónde sí te afectaría el GIL? ¿Considerarías multiprocessing para la IA?

**3. ¿Qué patrones de diseño usaste y por qué?**
- **(I)** Singleton (recursos únicos costosos), Inyección de Dependencias / Service Locator (`DependencyContainer`, rompe imports circulares), Repository (aísla SQLAlchemy), Pub/Sub (`EventManager` desacopla detección de efectos), Factory (`create_app`), Producer/Consumer (`CircularFrameBuffer`→`FrameDistributor`).
- **(C)** Singleton, DI/Service Locator, Repository, Pub/Sub, Factory y Producer/Consumer.
- **(S)** ¿Desventajas del Service Locator frente a DI por constructor? ¿Cómo testeas un singleton?

**4. ¿Cómo arranca el sistema sin bloquear el servidor HTTP?**
- **(I)** `create_app()` configura JWT/CORS/rate-limit, inicializa la BD y el contenedor, y lanza un **hilo daemon** que espera ~0.5 s y llama `CameraManager().start_all_active()`. Así el HTTP responde de inmediato y las cámaras suben asíncronamente. Los servicios de fondo (go2rtc, keepalive, storage, métricas, consistencia) se arrancan también en el setup.
- **(C)** Las cámaras se inician en un hilo daemon tras levantar el HTTP, de forma asíncrona.
- **(S)** ¿Qué pasa si una cámara falla al iniciar? ¿Cómo evitas que tumbe a las demás?

**5. ¿Cómo desacoplas la detección de sus efectos (BD, Telegram, métricas)?**
- **(I)** Con un bus de eventos: los productores publican `EventData` en `EventManager.publish()`, que despacha a los suscriptores (`EventService`, `TelegramNotifier`, `NotificationRouter`, `MetricsCollector`) en un pool de 8 hilos, aislando fallos. Añadir un consumidor no toca a los productores.
- **(C)** Un bus pub/sub (`EventManager`) reparte cada evento a suscriptores independientes.
- **(S)** ¿Qué pasa si un suscriptor es lento? ¿Cómo garantizas la entrega?

**6. ¿Por qué el vivo no pasa por tu pipeline de frames de Python?**
- **(I)** Porque servir vídeo desde Python (decodificar + recodificar MJPEG/HLS) es caro, añade latencia y abre una conexión por cliente. go2rtc abre **una** conexión RTSP a la cámara y re-expone WebRTC/RTSP/HLS sin recodificar. El pipeline FFmpeg→buffer→distributor alimenta sólo **IA y grabación**.
- **(C)** go2rtc sirve el vivo (1 conexión, multiprotocolo); el pipeline Python es sólo para IA/grabación.
- **(S)** ¿Por qué eliminaste MJPEG? ¿Qué latencia mides en cada protocolo?

**7. ¿Qué pasa si un blueprint de la API falla al importarse?**
- **(I)** `safe_register()` lo registra de forma best-effort: el fallo se loguea (`"Error registrando '<name>'"`) pero **no es fatal**. El resto de la API sigue funcionando. Para verificar qué se cargó, `GET /api/v1/health` devuelve la lista `blueprints`.
- **(C)** El fallo se loguea pero no tumba la app; `/health` lista los blueprints cargados.
- **(S)** ¿No es peligroso ocultar fallos? ¿Cómo lo detectarías en producción?

**8. ¿Cómo manejas las cámaras de doble lente?**
- **(I)** Una cámara `is_dual_lens=True` entrega un único RTSP "lado a lado"; go2rtc lo divide por hardware (crop QSV) en `cam_X_l1`/`cam_X_l2`, identificados por `stream_id` l1/l2. No se crean buffers/distribuidores separados; el split ocurre en la capa de medios.
- **(C)** El RTSP side-by-side se *cropea* en go2rtc a dos streams l1/l2; no se duplica el pipeline.
- **(S)** ¿Por qué cropear en go2rtc y no en Python/OpenCV? ¿Coste de CPU?

**9. ¿Cómo se recupera el sistema si go2rtc muere?**
- **(I)** `Go2RtcManager` tiene un **supervisor** que vigila el proceso cada 2 s y lo reinicia con backoff `[1,2,5,10,20,30]s`, y un **reconciliador** que cada 15 s sincroniza la config con la BD. `StreamKeepAlive` mantiene los transcoders calientes. La caída se repara en ≤30 s.
- **(C)** Supervisor con backoff + reconciliador + keepalive; auto-recuperación en ≤30 s.
- **(S)** ¿Qué pasa con la grabación durante esos segundos? ¿Y con la IA?

**10. ¿Cómo evitas imports circulares entre servicios?**
- **(I)** El `DependencyContainer` construye y entrega los servicios por nombre; los endpoints hacen `get_service("x")` en vez de importar la clase. Así la dependencia se resuelve en runtime y no en tiempo de import.
- **(C)** Con un contenedor DI que resuelve servicios por nombre en runtime.
- **(S)** ¿Eso no oculta las dependencias? ¿Cómo lo documentas?

---

## B. Redes

**11. Explica el rol de RTSP, RTP y RTCP.**
- **(I)** RTSP es el **control** de sesión (DESCRIBE/SETUP/PLAY/TEARDOWN) y negocia el transporte vía SDP. RTP **transporta** los medios (paquetes con secuencia/timestamp/SSRC) sobre UDP o TCP-interleaved. RTCP lleva **estadísticas** (jitter, pérdida) y permite sincronización A/V. La cámara expone RTSP; go2rtc es cliente RTSP de la cámara y servidor RTSP del restream.
- **(C)** RTSP controla, RTP transporta, RTCP reporta calidad/sincroniza.
- **(S)** ¿TCP o UDP para RTP y por qué? ¿Cómo afecta el jitter?

**12. ¿Por qué usas RTSP sobre TCP y no UDP?**
- **(I)** En WiFi/redes con pérdida, UDP pierde paquetes → artefactos/cortes. TCP (RTSP interleaved) garantiza orden y entrega, a costa de algo más de latencia. El proyecto fija `RTSP_TRANSPORT=tcp` y VLC usa `--rtsp-tcp`. En LAN cableada estable, UDP daría menos latencia.
- **(C)** TCP por fiabilidad en WiFi; UDP solo si la red es muy estable.
- **(S)** ¿Cómo lo harías configurable por cámara? ¿Mediste la diferencia de latencia?

**13. ¿Cómo descubres cámaras sin conocer su IP?**
- **(I)** Con **WS-Discovery**: envío un `Probe` SOAP por multicast UDP a `239.255.255.250:3702` filtrando `NetworkVideoTransmitter`; las cámaras responden `ProbeMatch` con su `XAddrs` (URL del device service), de donde extraigo IP y puerto. Complemento con escaneo de subred y pre-check TCP.
- **(C)** WS-Discovery multicast (Probe/ProbeMatch) + escaneo de subred de respaldo.
- **(S)** ¿Qué pasa si el switch bloquea multicast? ¿Cruza VLANs?

**14. ¿Por qué WebRTC necesita señalización y qué es ICE?**
- **(I)** El medio WebRTC viaja P2P, pero los pares primero deben intercambiar descriptores **SDP** (codecs, parámetros) — eso es la señalización (la hace mi backend como proxy WHEP). **ICE** descubre rutas posibles (candidatos host/STUN/TURN) y prueba pares hasta encontrar uno que conecte. En LAN aislada uso sólo candidatos host (la IP LAN:8555).
- **(C)** La señalización intercambia SDP; ICE encuentra la ruta P2P (host/STUN/TURN).
- **(S)** ¿Cómo darías acceso remoto fuera de la LAN? ¿STUN vs TURN?

**15. ¿Cómo detectas la IP LAN del servidor para anunciarla a WebRTC?**
- **(I)** `detect_lan_ip()` abre un socket UDP "conectado" a `10.255.255.255:1` (no envía datos) y lee `getsockname()[0]`: así el SO revela la interfaz/IP de salida. Si falla, cae a `127.0.0.1`.
- **(C)** Un socket UDP de prueba revela la interfaz de salida sin enviar tráfico.
- **(S)** ¿Qué pasa con múltiples interfaces (VPN, Docker)? ¿Lo puedes forzar?

**16. ¿Qué protocolo de vivo usa cada cliente y por qué?**
- **(I)** Navegador: **WebRTC** (mínima latencia). Escritorio: **RTSP** restream con VLC. Android: **HLS** (robusto en WiFi vía ExoPlayer). Todos salen de go2rtc desde una sola ingesta.
- **(C)** WebRTC (web), RTSP (escritorio), HLS (móvil).
- **(S)** ¿Por qué no WebRTC en móvil? ¿Latencia comparada?

**17. ¿Cómo permite el seek en grabaciones por HTTP?**
- **(I)** El reproductor pide el MP4 con cabeceras **`Range: bytes=...`** y el backend responde **206 Partial Content** con ese rango. La URL está firmada con HMAC (no requiere header Authorization, que los reproductores no envían).
- **(C)** HTTP Range (206) + URL firmada HMAC para autorizar sin JWT.
- **(S)** ¿Y si el cliente pide rangos enormes? ¿Cómo evitas leakage de archivos?

**18. ¿Qué es WebSocket y para qué lo usas?**
- **(I)** Es un canal full-duplex persistente (upgrade desde HTTP) que permite push del servidor sin polling. Lo uso para **notificaciones en tiempo real** en LAN (sin internet); en Android un foreground service mantiene el WS.
- **(C)** Canal bidireccional persistente para notificaciones en vivo dentro de la LAN.
- **(S)** ¿Cómo reconectas si se cae? ¿Coste de batería en móvil?

**19. ¿Por qué HLS tiene más latencia que WebRTC?**
- **(I)** HLS parte el vídeo en segmentos de varios segundos y el cliente debe acumular algunos antes de reproducir; eso introduce segundos de latencia. WebRTC envía RTP en tiempo real sin segmentar. Por eso HLS es para móvil/robustez y WebRTC para mínima latencia.
- **(C)** HLS bufferiza segmentos (segundos); WebRTC es tiempo real.
- **(S)** ¿LL-HLS reduciría eso? ¿Vale la pena la complejidad?

**20. ¿Cuál es la mayor fuente de latencia de tu vivo?**
- **(I)** No es el backend: es el **GOP / intervalo de I-frame de la cámara**. FFmpeg/decoders no pueden mostrar nada hasta recibir un keyframe; con GOP 50 a 15 fps son ~3.3 s al (re)conectar. Lo mitigo con flags de bajo buffer y recomendando bajar el GOP a ≤30 en la cámara. Para medir, `GET /cameras/<id>/latency` da `frame_age` (captura→envío); si <500 ms, el backend es fluido.
- **(C)** El GOP de la cámara; el endpoint `/latency` lo demuestra (frame_age < 500 ms = backend fluido).
- **(S)** ¿Cómo bajas el GOP? ¿Qué coste tiene en bitrate?

---

## C. Streaming

**21. ¿Qué problema concreto resuelve go2rtc?**
- **(I)** Las cámaras IP aceptan pocas sesiones RTSP (1–4). Si preview, grabación, IA y cada móvil abrieran la suya, la cámara se satura. go2rtc abre **1** conexión y multiplexa a N consumidores en WebRTC/RTSP/HLS, con `-c copy` (CPU≈0).
- **(C)** Centraliza la conexión a la cámara: 1 RTSP, N consumidores, multiprotocolo.
- **(S)** ¿Qué pasaría sin go2rtc con 4 clientes? ¿Cómo lo probaste?

**22. ¿Cómo generas la configuración de go2rtc?**
- **(I)** Una función pura `build_go2rtc_config(cameras,...)` convierte las filas `Camera` en el dict del YAML; `write_config()` lo serializa a `go2rtc.generated.yaml`. Define `cam_X` (nativo `-c copy`), calidades `_medium/_low`, y dual-lens `_l1/_l2` con `exec:ffmpeg` (QSV). Un reconciliador la regenera al cambiar la BD.
- **(C)** Desde la BD, con una función pura que emite el YAML; se reconcilia cada 15 s.
- **(S)** ¿Por qué `exec:ffmpeg` explícito y no `#hardware=qsv`? ¿Cómo lo testeas?

**23. Explica los flags de transcode dual-lens (`-g 15 -bf 0 -async_depth 1`).**
- **(I)** `-g 15`: GOP de 15 frames (~1 s) para recuperación rápida ante pérdidas. `-bf 0`: sin B-frames → menor latencia. `-async_depth 1`: QSV entrega frame a frame, menos jitter. `crop=iw:ih/2:...` separa los lentes; `scale=-2:480/360` genera calidades.
- **(C)** GOP corto + sin B-frames + entrega frame a frame → baja latencia en el transcode.
- **(S)** ¿Qué coste tiene GOP corto? ¿Cuándo subirías B-frames?

**24. ¿Por qué `StreamKeepAlive`?**
- **(I)** El primer frame de un transcoder QSV recién arrancado tarda ~3.5–5 s. Para que conmutar de cámara sea instantáneo, mantengo cada stream caliente con un consumidor mínimo `ffmpeg -c copy -f null -` (no decodifica, CPU≈0). Reconcilia con la BD cada 15 s.
- **(C)** Mantiene los transcoders calientes para conmutación instantánea, sin coste de CPU.
- **(S)** ¿No desperdicia ancho de banda? ¿Lo limitarías a cámaras visibles?

**25. ¿Cómo autorizas WebRTC sin exponer go2rtc?**
- **(I)** El cliente envía el **SDP offer** a `POST /cameras/<id>/webrtc` con JWT y permiso `view`; `WebRTCSignalingService` valida y reenvía el offer a go2rtc en loopback (`127.0.0.1:1984`), devolviendo el answer. go2rtc nunca se expone directo al cliente.
- **(C)** El backend hace de proxy de señalización autenticado hacia go2rtc en loopback.
- **(S)** ¿Y si alguien llega directo a go2rtc:1984? ¿Lo proteges por firewall?

**26. ¿Cómo comparten cámara la grabación y la IA sin abrir dos conexiones?**
- **(I)** Ambas leen el **restream de go2rtc** (`rtsp://127.0.0.1:8554/cam_X` y `cam_X_low`), no la cámara. Con `GO2RTC_AS_SOURCE`, hasta el `FFmpegWorker` lee el restream. Así sólo go2rtc habla con la cámara.
- **(C)** Leen el restream local de go2rtc, no la cámara; 1 conexión real.
- **(S)** ¿Qué latencia añade el restream? ¿Y si go2rtc se reinicia?

**27. ¿Qué pasa al añadir una cámara en caliente?**
- **(I)** `POST /cameras` la persiste; el reconciliador (15 s) regenera el YAML añadiendo `cam_<nuevo>` y reinicia go2rtc; el keepalive abre su transcoder. En el siguiente `GET /cameras` el cliente ya ve sus URLs.
- **(C)** El reconciliador detecta el cambio en BD y republica el stream automáticamente.
- **(S)** ¿15 s no es mucho? ¿Podrías recargar sin reiniciar el proceso?

**28. ¿Cómo logras baja latencia en el escritorio con VLC?**
- **(I)** Flags: `--network-caching=150` (buffer mínimo), `--rtsp-tcp` (fiable), `--drop-late-frames` (descarta tardíos → latencia acotada), `--no-audio-time-stretch`. Y `play_url_async()` para cambiar de fuente sin recrear el player ni congelar la UI.
- **(C)** Buffer de red mínimo + descarte de frames tardíos + RTSP/TCP.
- **(S)** ¿Por qué no 0 ms de caching? ¿Qué pasa con audio?

**29. ¿Qué calidades ofreces y cómo elige el cliente?**
- **(I)** `high` (nativo), `medium` (480p), `low` (360p), por lente si es dual. El backend expone `stream_urls{...}{high|medium|low}` y el cliente elige según ancho de banda (p.ej. móvil en WiFi débil → low). La IA usa siempre `low`.
- **(C)** high/medium/low por stream; el cliente elige por ancho de banda.
- **(S)** ¿Harías ABR automático? ¿Cómo medirías el ancho de banda del cliente?

**30. ¿Por qué eliminaste MJPEG?**
- **(I)** MJPEG recodificaba en el backend (CPU alta), tenía peor compresión, mayor latencia y abría una conexión por cliente. go2rtc con `-c copy` y WebRTC lo supera en todo. Fue una decisión de simplificación y rendimiento.
- **(C)** MJPEG era caro y de alta latencia; go2rtc lo reemplaza con ventaja.
- **(S)** ¿Quedó algún caso de uso de MJPEG? ¿Compatibilidad con clientes viejos?

---

## D. ONVIF

**31. ¿Qué es ONVIF y qué servicios usas?**
- **(I)** Estándar de interoperabilidad para cámaras IP basado en SOAP. Uso Device (capabilities, info, hora), Media (profiles, stream/snapshot URI), PTZ (continuous move/stop/presets), Imaging (LED/IR) y Discovery (WS-Discovery).
- **(C)** Estándar SOAP de cámaras; uso Device, Media, PTZ, Imaging y Discovery.
- **(S)** ¿Qué perfil ONVIF (S/T/G)? ¿Usas el servicio de eventos de la cámara?

**32. Describe tu FAST PATH vs SLOW PATH de descubrimiento.**
- **(I)** FAST PATH: cliente SOAP propio (sin WSDL) que prueba ~6 credenciales × 4 puertos en ~6 s; si obtiene auth+profiles+stream, devuelve la cámara. SLOW PATH: `onvif-zeep` con WSDL local para cámaras difíciles. Si todo falla, fallback RTSP con patrones genéricos.
- **(C)** SOAP manual rápido primero; zeep/WSDL como respaldo; RTSP como último recurso.
- **(S)** ¿Por qué SOAP a mano? ¿Qué cámaras necesitan el SLOW PATH?

**33. Explica la autenticación WS-Security UsernameToken.**
- **(I)** Intento `PasswordText` (claro) y, si la cámara responde `NotAuthorized`, degrado a `PasswordDigest`: `Base64(SHA1(Nonce + Created + Password))`, con Nonce y Created en la cabecera para evitar replay. Por eso sincronizo la hora de la cámara.
- **(C)** UsernameToken; PasswordText con fallback a PasswordDigest (SHA1 de nonce+created+pass).
- **(S)** ¿Por qué intentar Text primero? ¿Qué pasa si la hora está desfasada?

**34. ¿Cómo obtienes la URL RTSP de la cámara?**
- **(I)** Con `GetStreamUri` sobre el `ProfileToken`, especificando `RTP-Unicast`/`RTSP`. La cámara devuelve `MediaUri.Uri`; le inyecto las credenciales y la guardo como `rtsp_url`.
- **(C)** `GetStreamUri` sobre el perfil → `MediaUri.Uri` → `rtsp_url`.
- **(S)** ¿Y el substream de baja resolución? ¿Múltiples perfiles?

**35. ¿Qué haces si una cámara no soporta una capacidad?**
- **(I)** Degrado con gracia: sin perfil PTZ → `is_supported()=False` y oculto controles; sin audio config → oculto audio; ONVIF roto pero RTSP ok → `connection_type="rtsp_fallback"`; IP de otra subred → `unreachable` con instrucciones. Persisto el puerto ONVIF que funciona.
- **(C)** Fallbacks y flags: oculto lo no soportado y registro el diagnóstico en BD.
- **(S)** ¿Cómo sabe la UI qué mostrar? ¿Guardas las capacidades?

**36. ¿Cómo manejas que el puerto ONVIF cambie tras un reinicio de la cámara?**
- **(I)** `candidate_ports()` prioriza el puerto guardado en `onvif_url` y luego prueba `[80,8080,8000,8899,...]`. Al encontrar el que responde, `_persist_onvif_port()` lo guarda, así el siguiente comando no reintenta.
- **(C)** Itero puertos candidatos y persisto el que funciona.
- **(S)** ¿Cuánto tarda el reintento? ¿Cacheas el cliente ONVIF?

**37. ¿Qué datos ONVIF guardas en la BD?**
- **(I)** En `cameras`: `rtsp_url`, `onvif_url` (con puerto), `username/password`, `profile_token`, flags `has_ptz/leds/audio/dual_lens`, resolución/fps, y diagnóstico (`connection_type`, `last_error_code`, `last_connected_at`, `fallback_url`).
- **(C)** URL/credenciales/profile_token, capacidades y diagnóstico de conexión.
- **(S)** ¿Las credenciales en claro? ¿Cómo lo mitigarías?

**38. ¿Cómo sincronizas la hora y por qué importa?**
- **(I)** `SetSystemDateAndTime` pone la hora local en la cámara. Importa para timestamps coherentes de eventos/grabaciones y porque el digest WS-Security rechaza peticiones con `Created` fuera de ventana.
- **(C)** `SetSystemDateAndTime`; coherencia de timestamps y validez del digest.
- **(S)** ¿Usas NTP? ¿Zona horaria?

**39. ¿Por qué no delegas la detección en el motor de eventos de la cámara?**
- **(I)** Porque las cámaras varían mucho en calidad de detección; haciendo YOLO en el servidor obtengo detección **uniforme** y configurable en todo el parque, independiente del firmware. El servicio Events de ONVIF queda marginal.
- **(C)** Para uniformidad y control: la IA vive en el servidor, no en cámaras heterogéneas.
- **(S)** ¿No perderías eficiencia? ¿Combinarías ambas?

**40. ¿Cómo construyes un sobre SOAP sin WSDL?**
- **(I)** Concateno el `Envelope` con los namespaces correctos, la cabecera `wsse:Security` (UsernameToken) y el `Body` con la operación (p.ej. `GetProfiles`), lo envío por HTTP POST con `SOAPAction`, y parseo la respuesta con ElementTree. Es más rápido que cargar el WSDL.
- **(C)** Plantillas XML con namespaces + UsernameToken, POST con SOAPAction, parse con ElementTree.
- **(S)** ¿No es frágil? ¿Cómo manejas variaciones entre fabricantes?

---

## E. PTZ

**41. ¿Cómo mueves la cámara y qué es ContinuousMove?**
- **(I)** `ContinuousMove` mueve a una **velocidad** (vector `PanTilt{x,y}` y `Zoom{x}` en [-1,1]) hasta recibir `Stop`. El cliente envía `move` al pulsar y `stop` al soltar. También existen `AbsoluteMove`, `RelativeMove` y `GotoPreset`.
- **(C)** Movimiento por vector de velocidad hasta `Stop`; uso ContinuousMove.
- **(S)** ¿Por qué velocidad y no posición absoluta? ¿Soportas presets?

**42. ¿Cómo evitas que dos usuarios muevan la cámara a la vez?**
- **(I)** `PTZLockService` es un mutex en memoria por cámara con timeout auto-liberable; el segundo usuario recibe "en uso por X (N s restantes)". Es reentrante para el mismo usuario y forzable por admin.
- **(C)** Un mutex de PTZ con timeout; serializa el control.
- **(S)** ¿Qué pasa si el usuario con el lock se desconecta? ¿Lo persistes?

**43. ¿Cómo sabes si una cámara soporta PTZ?**
- **(I)** Busco un perfil con `PTZConfiguration` en `GetProfiles` y valido con `GetStatus`. Si ninguno la tiene, `is_supported()=False` y la UI oculta el joystick.
- **(C)** Perfil con `PTZConfiguration` + `GetStatus`; si no, oculto controles.
- **(S)** ¿Y cámaras con PTZ digital? ¿Detectas límites de recorrido?

**44. ¿Cómo se traduce el joystick del cliente a ONVIF?**
- **(I)** El joystick 3×3 manda `direction` + `speed`; el endpoint mapea a un vector `Velocity` (p.ej. right → PanTilt.x=+speed) y llama `PTZController.move()`, que emite el SOAP `ContinuousMove`. Al soltar, `stop`.
- **(C)** dirección+velocidad → vector Velocity → SOAP ContinuousMove.
- **(S)** ¿Latencia del control? ¿Repetición de comandos?

**45. ¿Qué seguridad aplica al PTZ?**
- **(I)** JWT + permiso `can_control_ptz` (decorador `require_camera_permission("control_ptz")`) + el lock de PTZ. Sin permiso → 403.
- **(C)** JWT + flag `can_control_ptz` + mutex.
- **(S)** ¿Auditas los movimientos PTZ? ¿Rate limit?

**46. ¿Cómo manejas la reconexión del cliente PTZ entre comandos?**
- **(I)** `PTZController` se cachea por cámara (singleton) y reusa la conexión; si el puerto cambió, reintenta candidatos y persiste el bueno. Así no reconecto en cada comando.
- **(C)** Controlador cacheado por cámara, con reintento de puertos.
- **(S)** ¿TTL de la cache? ¿Qué pasa tras un error de red?

---

## F. Inteligencia Artificial

**47. ¿Por qué YOLOv8-nano y no un modelo mayor?**
- **(I)** Porque corre en CPU en hardware modesto con latencia aceptable. Un modelo mayor daría más precisión pero no es viable en CPU para tiempo real; el nano, gateado por movimiento y a baja resolución, equilibra precisión/velocidad.
- **(C)** Es el mejor compromiso velocidad/precisión en CPU.
- **(S)** ¿Cuánto mejora con GPU? ¿mAP del nano vs s/m?

**48. ¿Cómo ahorras CPU en la inferencia?**
- **(I)** Tres palancas: (1) gate de movimiento (sólo infiero si hay cambio, ~90% menos inferencias), (2) substream `low` 640×384, (3) 6 fps. Además, una sola cámara con IA (`AI_CAMERA_ID`).
- **(C)** Gate de movimiento + baja resolución + 6 fps + una cámara.
- **(S)** ¿Cómo eliges qué cámara lleva IA? ¿Multi-cámara con cola?

**49. Explica NMS e IoU.**
- **(I)** IoU = intersección/unión de dos cajas ∈[0,1]. NMS ordena por score, fija la mejor y elimina las de IoU>umbral (≈0.45) de la misma clase, quitando duplicados del mismo objeto. (Detalle en [08_IA.md](08_IA.md).)
- **(C)** IoU mide solape; NMS quita cajas duplicadas con IoU alto.
- **(S)** ¿Soft-NMS? ¿Qué umbral usas?

**50. ¿Cómo evitas inundar de alertas?**
- **(I)** Cooldown por clase (`AI_EVENT_COOLDOWN_SECONDS≈30`): una persona estática genera 1 evento, no 100. Además `NotificationLog.cooldown_key` deduplica a nivel de notificación.
- **(C)** Cooldown por clase + dedup por `cooldown_key`.
- **(S)** ¿Cooldown fijo o adaptativo? ¿Por cámara?

**51. ¿Qué pasa si faltan torch/ultralytics?**
- **(I)** `check_dependencies()` corre **upfront** al activar la IA; si faltan, `POST /ai/<id>/activate` devuelve **503 + `AI_DEPENDENCIES_MISSING`** con el `pip install` exacto, en vez de morir en el hilo worker.
- **(C)** 503 claro con el comando pip, no fallo silencioso.
- **(S)** ¿Por qué no fallar al import? ¿Dónde se carga el modelo?

**52. ¿Cómo procesas un frame de extremo a extremo?**
- **(I)** `AIFrameSource` entrega 640×384 BGR → `MotionDetector` (gate) → `model.predict(conf=0.35, classes=[...])` → filtro por clase/confianza → agrupar + cooldown → `EventData` → `EventManager`.
- **(C)** Frame→gate→YOLO→filtro→cooldown→evento.
- **(S)** ¿Dónde dibujas el snapshot? ¿Qué guardas?

**53. ¿Por qué corre el scheduler en un hilo y no en el callback del distribuidor?**
- **(I)** Porque la inferencia es lenta y bloquearía el reparto de frames a otros consumidores (fix F1.2). Un hilo worker desacopla la IA del fan-out.
- **(C)** Para no bloquear el reparto de frames con la inferencia.
- **(S)** ¿Cómo pasas el frame al hilo? ¿Cola o latest-frame?

**54. ¿Cómo manejas falsos positivos del gate de movimiento (lluvia, luz)?**
- **(I)** El gate sólo decide *cuándo* correr YOLO; los falsos del gate los filtra YOLO (no detecta persona en la lluvia). Como mejora, usaría sustracción de fondo adaptativa (MOG2) y ROIs.
- **(C)** YOLO filtra los falsos del gate; mejorable con MOG2/ROIs.
- **(S)** ¿Y de noche con IR? ¿Reentrenarías?

**55. ¿Reentrenaste el modelo?**
- **(I)** No; uso el preentrenado en COCO (persona, vehículos…), que cubre el dominio de vigilancia doméstica. Reentrenar requeriría dataset etiquetado propio; queda como trabajo futuro para clases específicas.
- **(C)** Uso COCO preentrenado; no reentrené.
- **(S)** ¿Qué clases te interesarían? ¿Cómo etiquetarías?

**56. ¿Cómo serializas una detección?**
- **(I)** `{event_type, camera_id, confidence, bbox[x1,y1,x2,y2], timestamp, snapshot_path}`. El bbox está en coords del frame 640×384.
- **(C)** JSON con clase, confianza, bbox y ruta de snapshot.
- **(S)** ¿Reescalas el bbox a la resolución original? ¿Tracking?

**57. ¿Cómo medirías la precisión de tu sistema de detección?**
- **(I)** Con un set etiquetado, calcularía Precisión/Recall y mAP@0.5; en operación, contaría falsos positivos/negativos por cámara y ajustaría `conf` y el gate. El endpoint de eventos permite revisar histórico.
- **(C)** Precisión/Recall/mAP en test; FP/FN en operación.
- **(S)** ¿Tienes ground truth? ¿Curva PR?

**58. ¿Qué clases detectas y por qué?**
- **(I)** Principalmente person y vehículos (índices COCO), porque son los relevantes para intrusión/vigilancia. Filtro el resto para no generar ruido (p.ej. mascotas) salvo configuración.
- **(C)** Persona y vehículos; el resto se filtra.
- **(S)** ¿Configurable por cámara? ¿Zonas?

---

## G. Base de Datos

**59. ¿Por qué PostgreSQL y no SQLite?**
- **(I)** Hay múltiples clientes y hilos concurrentes escribiendo eventos/grabaciones; SQLite sufre con escritores concurrentes (bloqueo de archivo). PostgreSQL da concurrencia real, FK/cascadas y tipos (Time/DateTime). SQLite quedó como legado.
- **(C)** Concurrencia real e integridad referencial; SQLite no escala con escritores.
- **(S)** ¿Mediste contención? ¿Tamaño esperado de la BD?

**60. Explica tu modelo multi-tenant.**
- **(I)** `cameras.owner_id` da propiedad total; `user_camera_permissions` (N:M) comparte con flags (`can_view`, `can_control_ptz`...). La autorización es jerárquica: admin → owner → permiso explícito (`PermissionService.check_permission`).
- **(C)** Propiedad + tabla de permisos N:M con flags; chequeo jerárquico.
- **(S)** ¿Por qué owner_id y no sólo permisos? ¿Cambio de dueño?

**61. ¿Cómo deduplicas notificaciones a nivel de BD?**
- **(I)** `NotificationLog.cooldown_key = "camera:<id>:<event_type>"` con índice; antes de enviar consulto si hubo envío reciente con esa clave. Evita flood.
- **(C)** Clave de cooldown indexada en `notification_logs`.
- **(S)** ¿Ventana de cooldown? ¿Por usuario o global?

**62. ¿Cómo eliges los índices?**
- **(I)** Por las consultas reales: `(camera_id, created_at)` para timelines, `(acknowledged, created_at)` para "no leídos", `(camera_id, start_time)` para grabaciones, `cooldown_key`, `jti`/`expires_at` para blocklist.
- **(C)** Índices dirigidos por las queries más frecuentes de la UI.
- **(S)** ¿Mediste con EXPLAIN? ¿Índices parciales?

**63. ¿CASCADE o SET NULL? ¿Por qué?**
- **(I)** CASCADE en `user_camera_permissions` (borrar usuario/cámara limpia permisos) y en eventos/grabaciones por cámara. SET NULL en `notification_preferences.camera_id` para que una preferencia **global** sobreviva al borrado de una cámara.
- **(C)** CASCADE donde el hijo no tiene sentido sin el padre; SET NULL en preferencias globales.
- **(S)** ¿Qué pasa con grabaciones al borrar cámara? ¿Y los archivos?

**64. ¿Cómo mantienes consistencia entre la BD y los archivos?**
- **(I)** `ConsistencyChecker` reconcilia periódicamente: borra filas `Recording` sin archivo y registra archivos huérfanos. Evita que la timeline mienta tras crashes/borrados manuales.
- **(C)** Un checker periódico reconcilia BD↔FS.
- **(S)** ¿Cada cuánto? ¿Y archivos en escritura?

**65. ¿Por qué guardas el hash del refresh token móvil y no el token?**
- **(I)** Para poder **revocar** por dispositivo y para que una filtración de la BD no exponga tokens válidos. Guardo `refresh_token_hash` (PBKDF2) en `mobile_devices`.
- **(C)** Seguridad: hash permite revocar y no expone el token.
- **(S)** ¿Cómo revocas un dispositivo? ¿Rotación de tokens?

**66. ¿Cómo configuras el pool de conexiones?**
- **(I)** `QueuePool` con `pool_size=10`, `max_overflow=20` (≤30), `pool_pre_ping=True` (valida antes de usar) y `pool_recycle=3600` (evita timeouts del servidor). Sesiones con contextmanager (commit/rollback/close).
- **(C)** 10+20 conexiones, pre-ping y recycle 1 h.
- **(S)** ¿Por qué esos tamaños? ¿Statement timeout?

**67. ¿Por qué normalizas channels/days en tablas aparte?**
- **(I)** Para evitar columnas repetidas o CSV: un usuario puede tener varios canales (telegram+push) y varios días activos. Normalizar (3FN) facilita consultas y mantenimiento.
- **(C)** Normalización 3FN: relaciones 1:N en vez de listas embebidas.
- **(S)** ¿No complica las queries? ¿JSONB sería alternativa?

**68. ¿Cómo evolucionas el esquema?**
- **(I)** Hoy `create_all()` al arrancar (BD nueva funciona sin Alembic); para producción usaría migraciones Alembic versionadas (`--autogenerate`) con upgrade/downgrade.
- **(C)** `create_all` ahora; Alembic versionado para producción.
- **(S)** ¿Riesgo de `create_all` con cambios? ¿Cómo migrarías datos?

---

## H. Seguridad

**69. ¿Cómo funciona tu autenticación JWT?**
- **(I)** Login verifica contraseña (PBKDF2:SHA256) y emite **access** (15 min, claims `sub/role/jti`) y **refresh** (7 días). Los endpoints usan `@jwt_required()`; valido firma+exp+blocklist(jti). Refresh emite nuevo access; logout revoca el jti.
- **(C)** Access 15 min + refresh 7 días, firmados HS256, con blocklist por jti.
- **(S)** ¿Por qué access corto? ¿Dónde guardas el secreto?

**70. JWT es stateless; ¿cómo revocas un token?**
- **(I)** Con una **blocklist dual**: dict en memoria (rápido) + tabla `revoked_tokens` (persistente). Al arrancar, `rehydrate_from_db()` la recarga; un GC descarta los expirados. El access corto limita la ventana si no se revoca.
- **(C)** Blocklist por jti en memoria + BD, rehidratada al arrancar.
- **(S)** ¿No rompe lo stateless? ¿Coste de consultar la blocklist?

**71. ¿Cómo reproduces medios si los reproductores no mandan el JWT?**
- **(I)** Con **URLs firmadas**: `SignedUrlService.sign("recording:<id>")` produce `v1.<exp>.<sig>` (HMAC-SHA256). El endpoint de medios verifica firma (tiempo constante, `hmac.compare_digest`) + caducidad (≈5 min). Autoriza sin header Authorization.
- **(C)** Tokens HMAC con caducidad embebidos en la URL.
- **(S)** ¿Y si se filtra la URL? ¿Liga la firma al usuario/IP?

**72. ¿Cómo verificas permisos por cámara?**
- **(I)** `PermissionService.check_permission(user, camera, tipo)`: admin → True; owner → True; si no, busca `UserCameraPermission` y comprueba el flag. El decorador `require_camera_permission("view"|"control_ptz"|...)` lo aplica antes del endpoint; sin permiso → 403.
- **(C)** Jerárquico admin→owner→flag, vía decorador.
- **(S)** ¿Qué pasa si olvidas el decorador? ¿Cómo lo garantizas?

**73. ¿Cómo almacenas contraseñas?**
- **(I)** `password_hash` con **PBKDF2:SHA256** (Werkzeug, salt de 16 bytes). Nunca en claro. Verifico con `verify_password`. Mejora posible: Argon2.
- **(C)** PBKDF2:SHA256 con salt; nunca en claro.
- **(S)** ¿Por qué no bcrypt/Argon2? ¿Iteraciones?

**74. ¿No es un riesgo guardar las credenciales de las cámaras en claro?**
- **(I)** Sí, es una deuda reconocida (`Camera.password` en claro). Mitigación actual: BD en LAN con acceso restringido. Mejora: cifrarlas con Fernet (clave en `.env`/KMS) y descifrar sólo al usarlas.
- **(C)** Es deuda técnica; la mitigaría con cifrado Fernet.
- **(S)** ¿Dónde guardarías la clave? ¿Rotación?

**75. ¿Cómo te proteges de fuerza bruta y abuso de API?**
- **(I)** Rate limiter (flask-limiter) por IP/usuario; auditoría (`audit_logs`) de acciones críticas; access tokens cortos. Mejora: backend Redis para el rate limit y bloqueo tras N intentos de login.
- **(C)** Rate limiting + auditoría + tokens cortos.
- **(S)** ¿El rate limit sobrevive a reinicios? ¿Lockout de login?

**76. ¿Qué riesgos tiene usar HTTP plano en la LAN?**
- **(I)** Sniffing intra-LAN (tokens, vídeo). En una LAN doméstica de confianza el riesgo es bajo, pero lo correcto sería TLS (certificado propio/mkcert) para REST y SRTP ya cifra WebRTC. Es una mejora pendiente.
- **(C)** Riesgo de sniffing; mitigable con TLS interno (WebRTC ya usa SRTP).
- **(S)** ¿Certificados en LAN? ¿mTLS para clientes?

**77. ¿Cómo evitas path traversal al servir segmentos/medios?**
- **(I)** Valido los nombres de segmento (sin `..`/separadores), sirvo sólo desde rutas controladas y reproduzco por id de grabación (no por ruta del cliente). `HLSService` valida el nombre de segmento.
- **(C)** Validación de nombres + acceso por id, no por ruta.
- **(S)** ¿Symlinks? ¿Normalización de rutas?

**78. ¿Cómo vinculas Telegram de forma segura?**
- **(I)** Código de un solo uso (`telegram_verification_codes`, 6–8 dígitos, expira 5 min) entregado por deep-link/QR; el usuario envía `/start <code>` al bot; el poller valida el código y crea `UserTelegramChat`. El código caduca y se marca `used`.
- **(C)** Código efímero de un uso canjeado en el bot.
- **(S)** ¿Y si interceptan el código? ¿Caducidad/uso único?

**79. ¿Qué claims lleva tu JWT y por qué?**
- **(I)** `sub`=user.id, `role` (admin/user), `exp`/`iat` y `jti` (para revocación). `role` permite autorizar sin consultar BD en cada request; `jti` habilita la blocklist.
- **(C)** sub, role, exp, iat, jti.
- **(S)** ¿Metes permisos en el token? ¿Tamaño del token?

**80. ¿Cómo auditas acciones sensibles?**
- **(I)** Decorador `@audit_action` y `log_action_sync` escriben en `audit_logs` (user, action, resource, ip, user_agent, details, éxito/fallo). Sirve para forense ("¿quién borró esta grabación?").
- **(C)** Tabla `audit_logs` poblada por decorador/función.
- **(S)** ¿Purgas logs? ¿Detectas patrones anómalos?

---

## I. Escalabilidad

**81. ¿Hasta cuántas cámaras escala y qué lo limita?**
- **(I)** El diseño apunta a `MAX_CAMERAS=4`. Lo limitan: CPU (transcodes go2rtc, IA), ancho de banda y el ser un solo proceso. Para más cámaras: GPU para transcode/IA, separar go2rtc en otra máquina, y mover estado a Redis para multi-worker.
- **(C)** ~4 por CPU/proceso único; escalar con GPU + estado externo.
- **(S)** ¿Qué se satura primero? ¿Cómo lo medirías?

**82. ¿Cómo escalarías a multi-worker sin romper los singletons?**
- **(I)** Externalizando el estado compartido: blocklist y rate-limit a Redis, coordinación de cámaras a un servicio dedicado (un "camera worker" por cámara), y la BD ya es compartida. La API REST sí escalaría horizontalmente.
- **(C)** Mover estado a Redis y dedicar un proceso por cámara; la API ya es stateless.
- **(S)** ¿Cómo evitas dos workers capturando la misma cámara? ¿Locks distribuidos?

**83. ¿Soporta múltiples usuarios concurrentes?**
- **(I)** Sí: JWT stateless, pool de BD (≤30 conexiones), go2rtc multiplexa el vivo (no una conexión por cliente a la cámara), y el rate limiter protege. El cuello sería el ancho de banda del servidor hacia muchos clientes.
- **(C)** Sí, por JWT stateless + pool BD + go2rtc multiplex.
- **(S)** ¿Cuántos clientes simultáneos por cámara? ¿WebRTC P2P ayuda?

**84. ¿Cómo gestionas el crecimiento del almacenamiento?**
- **(I)** `StorageManager` aplica retención por cuota (`MAX_STORAGE_GB`) y antigüedad, borrando los más antiguos de forma atómica; `ConsistencyChecker` reconcilia. Mejora: retención por cámara y "proteger" grabaciones marcadas.
- **(C)** Retención por espacio/tiempo con borrado atómico.
- **(S)** ¿Retención por cámara? ¿Qué proteges de borrado?

**85. ¿Cómo añades una cámara sin downtime?**
- **(I)** `POST /cameras` y el reconciliador de go2rtc republica el stream en ≤15 s; `CameraManager` arranca su pipeline. No reinicio el sistema.
- **(C)** Alta en caliente vía reconciliador; sin downtime.
- **(S)** ¿Y quitar una cámara? ¿Limpieza de recursos?

**86. ¿Cómo manejarías acceso desde fuera de la LAN?**
- **(I)** Añadiría TURN/STUN (`GO2RTC_WEBRTC_CANDIDATES`) para WebRTC, un reverse proxy con TLS para la API, y VPN como opción más simple/segura. El diseño LAN-first ya contempla candidatos ICE externos.
- **(C)** TURN/STUN + TLS + VPN para remoto.
- **(S)** ¿Riesgos de exponerlo a internet? ¿Autenticación reforzada?

**87. ¿Qué pasa si la BD se cae?**
- **(I)** Los endpoints fallan con error controlado (`api_error_response`), pero la captura/streaming de go2rtc sigue (no depende de la BD para el vivo). La grabación que necesita persistir metadatos se vería afectada. `pool_pre_ping` ayuda a reconectar.
- **(C)** El vivo sigue (go2rtc); la API y la persistencia fallan controladamente.
- **(S)** ¿Reintentos? ¿Modo degradado?

**88. ¿Cómo monitoreas el sistema en producción?**
- **(I)** `GET /system/health` (CPU/RAM/disco, estado por cámara, FPS), `MetricsCollector`, telemetría a CSV/JSONL, y logs estructurados. Mejora: exportar a Prometheus/Grafana.
- **(C)** /health + métricas + telemetría + logs.
- **(S)** ¿Alertas de salud? ¿SLOs?

---

## J. Rendimiento

**89. ¿Cómo logras baja latencia en la captura FFmpeg?**
- **(I)** Flags `-probesize 32 -analyzeduration 0` (sin warm-up de 5 s), `-fflags nobuffer+flush_packets -flags low_delay -flags2 +fast`, salida rawvideo, y `CircularFrameBuffer` de 2–3 frames (newest-wins). `ffprobe` con los mismos flags para detectar resolución.
- **(C)** Flags de bajo buffer + buffer "newest-wins" pequeño.
- **(S)** ¿Por qué no analyzeduration por defecto? ¿Drops?

**90. ¿Cómo evitas que un consumidor lento bloquee el pipeline?**
- **(I)** El `FrameDistributor` reparte en el `GlobalExecutor` y el buffer descarta el frame viejo (no encola): un consumidor lento provoca drops, no bloqueo. Por eso la grabación crítica va por go2rtc `-c copy`, no por este buffer.
- **(C)** Newest-wins + fan-out en pool: el lento dropea, no bloquea.
- **(S)** ¿Y la IA si va lenta? ¿Backpressure?

**91. ¿Cuál es el coste de CPU del transcode dual-lens?**
- **(I)** Con QSV (Intel iGPU), el decode HEVC + crop + encode H.264 baja la CPU ~90% vs `libx264` software. NVENC está bloqueado por driver viejo, por eso QSV.
- **(C)** ~90% menos CPU con QSV vs software.
- **(S)** ¿Mediste fps/uso? ¿Y sin iGPU?

**92. ¿Por qué leer frames crudos por stdout y no usar OpenCV VideoCapture?**
- **(I)** `cv2.VideoCapture` paga el `analyzeduration` por defecto (segundos de warm-up) y da menos control de flags. Leer rawvideo de FFmpeg por stdout con flags de baja latencia es más rápido y predecible.
- **(C)** Más control y menos latencia que VideoCapture.
- **(S)** ¿Coste de parsear bytes? ¿Resolución dinámica?

**93. ¿Cómo evitas recodificar al grabar?**
- **(I)** Grabo con `-c copy` desde el restream de go2rtc: copio el H.264 al MP4 segmentado sin decodificar (CPU≈0). El precio es que los cortes sólo son exactos en keyframes.
- **(C)** `-c copy` (sin transcode); cortes alineados a keyframes.
- **(S)** ¿Y si necesitas corte exacto de frame? ¿GOP?

**94. ¿Cómo mides la latencia real del vivo?**
- **(I)** `GET /cameras/<id>/latency?stream=main` devuelve `frame_age` (captura→envío). Si <500 ms, el backend es fluido y el delay es de la cámara (GOP) o del cliente/red.
- **(C)** Endpoint `/latency` con `frame_age`; <500 ms = backend fluido.
- **(S)** ¿Cómo aíslas latencia de cámara vs red vs cliente?

**95. ¿Qué haces si una cámara se "congela"?**
- **(I)** El watchdog del `FFmpegWorker` (`WATCHDOG_TIMEOUT=30s`) la marca `FROZEN` si no llegan frames, y reconecta con backoff (hasta `MAX_RECONNECT=10`). Un monitor de cámaras congeladas lo supervisa a nivel de `CameraManager`.
- **(C)** Watchdog 30 s → FROZEN + reconexión con backoff.
- **(S)** ¿Notificas la caída? (sí, evento `camera_offline`) ¿Reintentos infinitos?

**96. ¿Cómo optimizas los snapshots de eventos?**
- **(I)** `image_optimizer` comprime los JPEG (calidad/escala) para reducir tamaño en BD/disco y acelerar el envío por Telegram, sin perder utilidad visual.
- **(C)** Compresión JPEG optimizada de los snapshots.
- **(S)** ¿Thumbnails? ¿Formato WebP?

---

## K. Diseño de Software / Patrones

**97. ¿Cómo testeas un sistema lleno de singletons y subprocesos?**
- **(I)** La suite canónica es `backend/tests/run_tests.py` (unittest). Para lo testeable, aíslo lógica pura (p.ej. `build_go2rtc_config` es una función pura) y testeo componentes como `CircularFrameBuffer`. Los singletons se resetean en setup. Hay muchos scripts ad-hoc que NO son la suite.
- **(C)** unittest sobre lógica pura y componentes aislados; funciones puras donde se puede.
- **(S)** ¿Mockeas FFmpeg/go2rtc? ¿Cobertura?

**98. ¿Cómo aplicas el patrón Repository?**
- **(I)** `BaseRepository` + repos concretos (`CameraRepository`, `EventRepository`, `RecordingRepository`) encapsulan las queries; los servicios usan repos, no SQLAlchemy directo. Aísla el ORM y facilita cambios.
- **(C)** Repos por entidad que encapsulan el acceso a datos.
- **(S)** ¿No es overhead? ¿Unit of Work?

**99. ¿Por qué un bus de eventos y no llamadas directas?**
- **(I)** Para desacoplar: el productor no conoce a los consumidores. Añadir Telegram, métricas o un nuevo canal no toca la IA. Aísla fallos (un suscriptor que falla no rompe a otros) y permite procesamiento concurrente.
- **(C)** Desacople + extensibilidad + aislamiento de fallos.
- **(S)** ¿Garantías de entrega? ¿Orden de eventos?

**100. ¿Cómo manejas errores sin filtrar stack traces al cliente?**
- **(I)** `api_error_response()` loguea el detalle con `exc_info` en el servidor y devuelve al cliente un mensaje genérico o el explícito. Así no expongo nombres de tablas/esquema.
- **(C)** Log completo en servidor, mensaje genérico al cliente.
- **(S)** ¿Códigos de error? ¿i18n?

**101. ¿Cómo está organizado el código por capas?**
- **(I)** API (blueprints) → Servicios (lógica) → Repos (datos) → Modelos (ORM). Transversal: core (executor, security, jwt), streaming, processing, recording, events, notifications, cameras. Separa responsabilidades.
- **(C)** API→Servicios→Repos→Modelos, con módulos transversales.
- **(S)** ¿Dependencias entre capas? ¿Inversión de dependencias?

**102. ¿Por qué Service Locator en vez de DI por constructor?**
- **(I)** Por pragmatismo: rompe imports circulares y evita pasar dependencias por toda la cadena de Flask. Su desventaja es ocultar dependencias; lo acoto centralizando el registro en `container.py`.
- **(C)** Pragmatismo anti-import-circular; lo centralizo para mitigar su opacidad.
- **(S)** ¿Migrarías a DI explícita? ¿Cómo testeas?

**103. ¿Cómo garantizas thread-safety en el buffer de frames?**
- **(I)** `CircularFrameBuffer` usa un lock/deque thread-safe (newest-wins) y cuenta drops; el `FrameDistributor` reparte copias (`needs_copy`) o zero-copy cuando el consumidor serializa de inmediato. Evita data races entre productor (FFmpeg) y consumidores.
- **(C)** Deque con lock + política de copia por consumidor.
- **(S)** ¿Zero-copy no es peligroso? ¿Cuándo copias?

**104. ¿Cómo separas configuración de código?**
- **(I)** `Settings` carga `.env` y permite overrides desde `system_config` en runtime para ciertas claves. El código lee `settings.X`, nunca `os.getenv` disperso.
- **(C)** `Settings` (.env + overrides BD) como única fuente de config.
- **(S)** ¿Validación de config? ¿Recarga en caliente?

**105. ¿Cómo evitas duplicar lógica en los endpoints?**
- **(I)** Helpers: `api_error_response`, `require_admin`, `require_camera_permission`, `get_service`. Decoradores para auth/permiso/auditoría. DRY.
- **(C)** Decoradores y helpers compartidos (DRY).
- **(S)** ¿Decorador compuesto auth+admin? ¿Orden de decoradores?

---

## L. Justificación tecnológica

**106. ¿Por qué Flask y no FastAPI/Django?**
- **(I)** Flask es minimalista y encaja con el modelo de un solo proceso multihilo y mucho estado en memoria; no necesito el async de FastAPI (el trabajo pesado está en subprocesos) ni el peso de Django (ORM/admin) habiendo elegido SQLAlchemy. Flask da control total del wiring.
- **(C)** Minimalismo y control; async/ORM completos no aportan aquí.
- **(S)** ¿FastAPI no daría mejor I/O concurrente? ¿Por qué no?

**107. ¿Por qué go2rtc y no MediaMTX o un SFU (Janus/mediasoup)?**
- **(I)** go2rtc es ligero, soporta `-c copy`, dual-lens vía exec FFmpeg, y entrega WebRTC/RTSP/HLS desde una config simple. Un SFU es para muchos-a-muchos (videoconferencia), excesivo aquí. MediaMTX es similar; go2rtc tenía mejor WebRTC/HWAccel para mi caso.
- **(C)** Ligero, multiprotocolo y `-c copy`; un SFU es sobredimensionado.
- **(S)** ¿Probaste MediaMTX? ¿Diferencias?

**108. ¿Por qué Telegram y no FCM/push nativo?**
- **(I)** Telegram no requiere infraestructura propia ni registro de proyecto Google, funciona fuera de la LAN y envía fotos. Para tiempo real en LAN uso WebSocket (sin internet). FCM añadiría dependencia de Google sin cubrir el caso offline.
- **(C)** Cero infraestructura y funciona remoto; WS cubre el caso LAN.
- **(S)** ¿FCM para push del SO con app cerrada? ¿Lo añadirías?

**109. ¿Por qué PySide6 para el escritorio y no una web?**
- **(I)** Quería un cliente nativo de baja latencia con libVLC (control fino de decodificación) y UI rica (mosaico, joystick, timeline). Qt6 es nativo y multiplataforma; una web añadiría latencia/limitaciones para RTSP/VLC.
- **(C)** Nativo + libVLC de baja latencia + UI rica.
- **(S)** ¿No duplicas UI con el móvil? ¿Mantenibilidad?

**110. ¿Por qué Kotlin/ExoPlayer en Android y HLS?**
- **(I)** Stack estándar Android: Retrofit/OkHttp para REST+JWT con `Authenticator` que refresca tokens, ExoPlayer (Media3) para HLS/RTSP robusto en WiFi, y OkHttp WebSocket en un foreground service para notificaciones. WebRTC nativo queda como mejora.
- **(C)** Stack nativo Android; HLS por robustez; WebRTC pendiente.
- **(S)** ¿Por qué no WebRTC ya? ¿Batería del foreground service?

**111. ¿Por qué SQLAlchemy 2.0 con `Mapped[...]`?**
- **(I)** Da tipado estático (mejor autocompletado/validación), API moderna y desacopla de SQL crudo. Con Alembic permite migraciones. Frente a SQL a mano, reduce errores y acelera el desarrollo.
- **(C)** Tipado moderno + migraciones + menos SQL manual.
- **(S)** ¿Overhead del ORM? ¿Queries crudas cuando hace falta?

**112. Si lo rehicieras, ¿qué cambiarías?**
- **(I)** Cifraría credenciales de cámara (Fernet), añadiría TLS interno, migraría rate-limit/blocklist a Redis para permitir multi-worker de la API, generaría migraciones Alembic versionadas, y soportaría GPU multi-cámara para la IA con tracking. Mantendría go2rtc, el bus de eventos y el modelo de permisos.
- **(C)** Cifrado de credenciales, TLS, Redis, Alembic versionado, IA GPU multi-cámara.
- **(S)** ¿Qué priorizarías primero? ¿Por qué?
