# PARTE 10 — Críticas de un Sinodal Experto y Defensa Técnica

> Esta parte simula a un revisor exigente que ataca cada decisión, seguido de una **defensa técnica sólida**. Estúdiala para no quedarte mudo ante la crítica: reconoce lo válido, contextualiza y propón la mejora.

## 10.1 "Un solo proceso es una arquitectura frágil y no escala."

**Crítica:** Todo el sistema corre en un proceso Flask; si se cae, se cae todo, y no puedes repartir carga.

**Defensa:**
- Es una decisión **consciente y adecuada al dominio**: un *appliance* LAN de hasta 4 cámaras, no un SaaS multi-tenant masivo.
- El estado (cámaras, buffers, pools, modelo) son **singletons en memoria**; multi-worker los duplicaría y **rompería** la coordinación (N conexiones a cada cámara que sólo aceptan 1–4, eventos duplicados, N pools).
- La **resiliencia** está en la auto-recuperación de subcomponentes (supervisor de go2rtc con backoff, watchdog de FFmpeg, reconexión, reconciliadores), no en N réplicas.
- **Camino de escalado claro y declarado:** externalizar estado a Redis (blocklist, rate-limit), un proceso por cámara, GPU para transcode/IA; la **API REST ya es stateless** y escalaría horizontalmente.
- En operación real, el cuello de botella es la **cámara** (conexiones, GOP) y la **CPU/GPU de inferencia**, no el WSGI. Multi-worker no resolvería eso.

## 10.2 "Python y el GIL no sirven para vídeo en tiempo real."

**Crítica:** Un lenguaje con GIL no puede procesar varios streams de vídeo.

**Defensa:**
- El sistema **no decodifica vídeo en Python**: FFmpeg y go2rtc (procesos en C/Go) hacen decode/encode/restream; PyTorch libera el GIL en los tensores.
- Python actúa como **orquestador** y mueve numpy, donde el GIL apenas pesa.
- El vivo ni siquiera pasa por Python (go2rtc). La IA corre en hilo dedicado a baja resolución/fps.
- Resultado: rendimiento real adecuado para el dominio, con la productividad del ecosistema Python de visión/IA (ultralytics, OpenCV, numpy) que en otro lenguaje sería mucho más costoso.

## 10.3 "Servir vídeo en vivo con un binario externo (go2rtc) es una dependencia peligrosa."

**Crítica:** Dependes de un binario de terceros que no controlas.

**Defensa:**
- go2rtc resuelve un problema **real y difícil** (1 conexión RTSP → N consumidores multiprotocolo con WebRTC sub-segundo) que reimplementar sería reinventar la rueda con peor calidad.
- Está **gestionado y supervisado** por `Go2RtcManager` (genera su config desde la BD, lo lanza, lo reinicia con backoff, lo reconcilia) → no es una caja negra sin control.
- Es **software libre**, empaquetable en Docker, y sustituible (MediaMTX) sin tocar el resto: el acoplamiento es por URLs/HTTP.
- La alternativa (servir MJPEG/HLS desde Flask) fue **probada y eliminada** por coste de CPU, latencia y conexión-por-cliente.

## 10.4 "La IA en una sola cámara y en CPU es pobre."

**Crítica:** Sólo una cámara con detección y en CPU; un sistema serio detecta en todas con GPU.

**Defensa:**
- Es una **restricción de hardware honesta** (PC modesto, sin GPU garantizada): correr 4 inferencias 1080p en CPU no es realista.
- Las tres palancas (gate de movimiento, substream 640×384, 6 fps) hacen la detección **viable y útil** en la cámara crítica, con cooldowns que evitan flood.
- El diseño **no está cerrado a GPU**: `GO2RTC_HWACCEL` ya usa QSV; añadir CUDA y multi-cámara es configuración + cola priorizada, no rediseño.
- Para el caso de uso (vigilancia doméstica/pequeño comercio), una cámara crítica bien detectada > cuatro mal detectadas.

## 10.5 "Las credenciales de las cámaras están en claro en la BD."

**Crítica:** Riesgo de seguridad grave.

**Defensa:**
- **Reconocido como deuda técnica** (transparencia). Mitigación actual: BD en LAN con acceso restringido y usuario de BD limitado.
- **Mejora concreta y acotada:** cifrar `Camera.password` con Fernet (clave en `.env`/variable de entorno/KMS), descifrando sólo en el momento de usar la credencial (ONVIF/RTSP). No cambia el esquema, sólo el servicio.
- El resto de secretos **sí** está protegido: contraseñas de usuario con PBKDF2, refresh tokens móviles como hash, URLs de medios firmadas con HMAC.

## 10.6 "Usas HTTP plano: cualquiera en la LAN puede espiar."

**Crítica:** Sin TLS, los tokens y el vídeo viajan en claro.

**Defensa:**
- En una **LAN doméstica de confianza** el modelo de amenaza es bajo, pero la crítica es válida.
- **WebRTC ya cifra** el medio (SRTP/DTLS).
- **Mejora:** TLS interno para la REST API (certificado propio/mkcert o reverse proxy), y opcionalmente VPN para acceso remoto. El diseño no impide TLS; es trabajo de despliegue.
- Los access tokens son **cortos (15 min)** y revocables, limitando el valor de un token interceptado.

## 10.7 "JWT con blocklist contradice el principio stateless."

**Crítica:** Si necesitas una lista de revocados, no es stateless.

**Defensa:**
- Es un **trade-off estándar y aceptado**: JWT da escalabilidad y comodidad móvil; la blocklist es el precio de poder revocar (logout, cambio de contraseña).
- El impacto es mínimo: la blocklist es un **dict en memoria** (consulta O(1)) respaldado por BD, con GC de expirados y rehidratación al arrancar.
- Los access tokens cortos hacen que, incluso sin revocar, la ventana de exposición sea pequeña; el refresh largo se revoca por dispositivo (hash).

## 10.8 "No tienes migraciones versionadas; `create_all` es peligroso en producción."

**Crítica:** Cambiar el esquema con `create_all` puede corromper datos.

**Defensa:**
- `create_all` se usa para que una **BD nueva** arranque sin fricción (demo/instalación); **no** borra ni altera columnas existentes.
- La **infraestructura Alembic ya está** (`alembic.ini`, `migrations/`); para producción la ruta es generar migraciones versionadas (`--autogenerate`) con upgrade/downgrade.
- Es una mejora de **operación**, no un defecto de diseño del modelo de datos (que sí está normalizado e indexado).

## 10.9 "El reconciliador de go2rtc tarda 15 s; eso es lento para añadir una cámara."

**Crítica:** 15 s de espera para que un stream aparezca es mucho.

**Defensa:**
- 15 s es el **peor caso** del ciclo de reconciliación; el alta de cámara es una operación poco frecuente (configuración), no del camino crítico de operación.
- Hay **guardia anti-churn** para no regenerar/reiniciar en exceso (estabilidad > inmediatez).
- **Mejora:** disparar `reconcile()` puntualmente tras `POST /cameras` (evento explícito) en lugar de esperar al tick, o usar la API de recarga de go2rtc sin reiniciar el proceso.

## 10.10 "Mucho estado global (singletons) dificulta pruebas y mantenimiento."

**Crítica:** Los singletons son un anti-patrón para testabilidad.

**Defensa:**
- Los singletons modelan **recursos físicamente únicos** (los hilos de captura, los subprocesos, el modelo en memoria): tener dos `CameraManager` sería un error semántico, no una ventaja.
- La **lógica pura** se extrae y testea aislada (p.ej. `build_go2rtc_config` es una función pura; `CircularFrameBuffer` se testea sin red).
- El acoplamiento se mitiga con el **contenedor DI** (un único punto de construcción) y los singletons se resetean en `setUp`.
- Es un equilibrio pragmático entre pureza arquitectónica y la realidad de gestionar hardware/procesos.

## 10.11 "Telegram depende de internet; tu sistema 'LAN sin nube' usa la nube."

**Crítica:** Contradicción: vendes LAN-first pero notificas por Telegram (nube).

**Defensa:**
- El **camino crítico** (vídeo, grabación, IA, notificación en vivo por **WebSocket**) es 100% LAN, sin internet.
- Telegram es un canal **complementario y opcional** para alcanzar al usuario **fuera** de casa; si no hay internet, el WS sigue funcionando dentro de la LAN.
- No se sube vídeo a ninguna nube de vigilancia: sólo una foto/mensaje opcional al chat del propio usuario. La privacidad del grueso de datos se mantiene en local.

## 10.12 "¿Cómo sé que tu detección es fiable? No presentas métricas."

**Crítica:** Falta evaluación cuantitativa (precisión/recall) de la IA.

**Defensa:**
- El detector es **YOLOv8 preentrenado en COCO**, cuyo rendimiento (mAP) está publicado y validado por la comunidad; no es un modelo casero sin respaldo.
- En operación, el sistema **registra eventos con snapshot/confidence**, lo que permite auditar FP/FN y ajustar `conf`/gate por cámara.
- **Mejora honesta:** preparar un set etiquetado propio del entorno real y reportar Precisión/Recall/mAP, además de tuning del umbral por escena.

## 10.13 "¿Por qué no Frigate/ZoneMinder, que ya existen?"

**Crítica:** Reinventas soluciones maduras.

**Defensa:**
- Esos sistemas son potentes pero **complejos de integrar** con clientes propios (escritorio + móvil), modelo de permisos a medida, vinculación Telegram/QR, control ONVIF/PTZ/audio/LED unificado y la arquitectura concreta requerida.
- El proyecto tiene **valor académico y de integración**: implementa de extremo a extremo (descubrimiento ONVIF propio, pipeline de captura, go2rtc gestionado, bus de eventos, multi-tenant, dos clientes nativos), demostrando dominio de cada capa, no sólo configuración de una caja existente.
- Además incorpora ideas de esos sistemas (gate de movimiento, clips pre/post) con un diseño propio defendible.

## 10.14 Tabla de limitaciones reconocidas → mitigación/mejora

| Limitación | Mitigación actual | Mejora propuesta |
|---|---|---|
| Un solo proceso | Auto-recuperación de subsistemas | Estado en Redis + proceso por cámara |
| IA una cámara/CPU | Gate + low-res + 6 fps + cooldown | GPU (QSV/CUDA) multi-cámara + tracking |
| Credenciales cámara en claro | LAN restringida | Cifrado Fernet |
| HTTP plano | LAN de confianza; WebRTC=SRTP | TLS interno + VPN remoto |
| Sin métricas IA formales | Eventos con confidence/snapshot | Dataset etiquetado + P/R/mAP |
| `create_all` | No destructivo; BD nueva | Alembic versionado |
| Reconcile 15 s | Anti-churn | Reconcile event-driven tras alta |
| `audit_logs` sin purga | Índices para consulta | Job de purga > 90 días |
| Rate limit en memoria | Suficiente en 1 proceso | Backend Redis |

## 10.15 Cierre de defensa (frase fuerte)

> "El sistema está deliberadamente diseñado como un *appliance* LAN privado: cada decisión —un proceso, go2rtc, IA gateada por movimiento, JWT con blocklist, permisos por cámara— responde a las restricciones reales del dominio (cámaras baratas con pocas conexiones, hardware modesto, privacidad sin nube). Conozco sus límites y tengo para cada uno una ruta de mejora concreta y acotada, sin necesidad de rediseñar el núcleo. Eso es ingeniería: optimizar para el contexto, no para un benchmark abstracto."
