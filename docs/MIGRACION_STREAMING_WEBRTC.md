# Documento de migración tecnológica — Streaming en vivo y reproducción de grabaciones

**Proyecto:** LAN NVR/VMS (sistema-videovigilancia) + app móvil CamLink_app
**Ámbito:** Sustitución de MJPEG y HLS-en-vivo por **WebRTC** (directo) + **HLS `-c copy`** (grabaciones), con un servidor de medios **go2rtc** como capa de ingesta/restream.
**Estado:** `BORRADOR` · pendiente de mediciones reales (ver §12 y §13)
**Autor:** _____________  ·  **Fecha:** _____________  ·  **Versión:** 0.2

> Las secciones marcadas con 🟨 **HUECO** y los textos entre `[corchetes]` indican **datos que faltan
> por agregar** (típicamente mediciones del banco de pruebas). Deben completarse antes de la defensa.

---

## Índice

1. [Resumen ejecutivo](#1-resumen-ejecutivo)
2. [Motivación y problema actual](#2-motivación-y-problema-actual) 🟨
3. [Arquitectura actual (AS-IS)](#3-arquitectura-actual-as-is)
4. [Arquitectura objetivo (TO-BE)](#4-arquitectura-objetivo-to-be)
5. [Comparativa tecnológica detallada y fundamentación](#5-comparativa-tecnológica-detallada-y-fundamentación)
6. [Decisiones de diseño abiertas](#6-decisiones-de-diseño-abiertas)
7. [Inventario de clases: eliminar / modificar / mantener / crear](#7-inventario-de-clases)
8. [Nuevo componente: go2rtc](#8-nuevo-componente-go2rtc)
9. [Flujos detallados](#9-flujos-detallados)
10. [Cambios en el cliente de escritorio (PySide6)](#10-cambios-en-el-cliente-de-escritorio-pyside6)
11. [Cambios en la app móvil (CamLink_app — Android/Kotlin)](#11-cambios-en-la-app-móvil-camlink_app--androidkotlin)
12. [Plantilla de benchmarking ANTES / DESPUÉS](#12-plantilla-de-benchmarking-antes--después) 🟨
13. [Evidencia visual (capturas y GIFs)](#13-evidencia-visual-capturas-y-gifs) 🟨
14. [Plan de migración por fases](#14-plan-de-migración-por-fases)
15. [Riesgos y mitigaciones](#15-riesgos-y-mitigaciones)
16. [Plan de rollback](#16-plan-de-rollback)
17. [Glosario](#17-glosario)
18. [Referencias](#18-referencias)

---

## 1. Resumen ejecutivo

El sistema actual gira en torno a **decodificar cada cámara a frames RAW (BGR24) en memoria de
Python**, de forma continua, para alimentar MJPEG, IA, movimiento y grabación. Esto obliga a un
**ciclo completo decode → (procesado) → encode en software** por cámara, que es el origen del alto
consumo de CPU.

Esta migración persigue tres objetivos, fundamentados en §5:

| Objetivo | Mecanismo | Resultado esperado |
|---|---|---|
| **Eliminar el re-encode de directo** | MJPEG → **WebRTC** vía go2rtc (`-c copy`) | CPU de directo ≈ 0; latencia 0,1–0,3 s |
| **Eliminar el transcode de HLS en vivo** | Borrar `LiveHLSService` (2× libx264/cámara) | −1,2 a −1,6 cores por cámara |
| **Eliminar el re-encode de grabación** | Grabar `-c copy` desde go2rtc | CPU de grabación ≈ 0; mejor calidad |

Python deja de decodificar las 4 cámaras: **solo decodifica la cámara que tiene IA activa**.

> **Alcance móvil:** la app móvil está en este repo, en [CamLink_app/](../CamLink_app/) — **Android
> nativo en Kotlin** (`com.ipn.mx.onvif`), **ExoPlayer/Media3 1.5.1**, Retrofit + OkHttp y WebSocket de
> notificaciones. Detalles y cambios en §11.

---

## 2. Motivación y problema actual

### 2.1 Síntomas observados
- Consumo de CPU elevado y sostenido incluso con pocas cámaras.
- "Tirones" / congelación del vídeo en vivo (preview MJPEG) cuando la IA o la grabación compiten por CPU.
- Latencia perceptible en el directo.

### 2.2 🟨 HUECO — Línea base de consumo (medición real)

> Tomar las medidas **antes** de migrar, en condiciones reproducibles. Fuente: `htop`/Administrador de
> tareas, `GET /api/v1/cameras/<id>/latency`, `GET /api/v1/metrics`.

| Escenario | Nº cám | IA | Clientes | CPU % media | CPU % pico | RAM (MB) | `frame_age` ms | Notas |
|---|---|---|---|---|---|---|---|---|
| Idle (sin clientes)            |  |  |  | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | — |  |
| 1 cliente MJPEG                |  |  |  | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` |  |
| 4 clientes MJPEG               |  |  |  | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` |  |
| HLS en vivo (1 cám, 2 perfiles)|  |  |  | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | — |  |
| MJPEG + IA + grabación         |  |  |  | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` |  |
| **Caso peor real**             |  |  |  | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` |  |

### 2.3 🟨 HUECO — Evidencia del problema (insertar imágenes/GIFs en `docs/img/migracion/`)
```
![GIF: preview MJPEG congelándose bajo carga](img/migracion/before_mjpeg_stall.gif)
> Fig. 2.1 — El directo MJPEG se traba al activar IA + grabación. frame_age medido: [PENDIENTE] ms.

![Captura: CPU al 100% con htop](img/migracion/before_cpu.png)
> Fig. 2.2 — Procesos ffmpeg (decode + 2× libx264 HLS + recode grabación) saturando los cores.

![Captura: /latency con frame_age alto](img/migracion/before_latency.png)
> Fig. 2.3 — frame_age_ms > 1500 ms → cuello de botella de CPU en el backend.
```

---

## 3. Arquitectura actual (AS-IS)

### 3.1 Pipeline por cámara (vigente)
```
RTSP ─► FFmpegWorker ─► CircularFrameBuffer ─► FrameDistributor ─┬─► MJPEGStreamer  (cv2.imencode por frame)
       (decode a raw BGR24)                                       ├─► AIScheduler    (motion + YOLO)
                                                                  ├─► RecordingManager (re-encode libx264)
                                                                  └─► LiveHLSService  (2× libx264 por cámara)
```

### 3.2 Costes confirmados en el código
| Coste | Ubicación | Detalle |
|---|---|---|
| Decode H.264 → raw BGR24 | [ffmpeg_worker.py:438-459](../backend/app/workers/ffmpeg_worker.py#L438-L459) | `-f rawvideo -pix_fmt bgr24 pipe:1` |
| Re-encode de **grabación** | [recording_manager.py:777-798](../backend/app/recording/recording_manager.py#L777-L798) | `rawvideo → libx264 -preset ultrafast -crf 28` |
| Encode MJPEG por frame | [mjpeg_streamer.py:479-486](../backend/app/streaming/mjpeg_streamer.py#L479-L486) | `cv2.imencode(".jpg", …)` |
| **2× libx264** por cámara (HLS vivo) | [live_hls_service.py:139-164](../backend/app/streaming/live_hls_service.py#L139-L164) | Perfiles 480p + 720p simultáneos |
| Motion a resolución completa | [motion_detector.py:57-78](../backend/app/processing/motion/motion_detector.py#L57-L78) | `GaussianBlur(21×21)` sobre 1280×720 |
| Móvil: directo RTSP directo a cámara | [LiveViewFragment.kt:282-283](../CamLink_app/app/src/main/java/com/ipn/mx/onvif/ui/LiveViewFragment.kt#L282-L283) | `RtspMediaSource` → no pasa por backend |

### 3.3 Lo que **sí** está bien hecho (y se conserva)
- MJPEG: **un solo encode por stream** repartido a N clientes (zero-copy, `needs_copy=False`).
- `CircularFrameBuffer` con `maxsize=2` (siempre el frame más reciente).
- IA **gated por movimiento** (YOLO solo si hay movimiento).
- Splice de eventos con **`-c copy`** sobre la grabación continua.
- HLS de grabaciones (`HLSService`) ya usa **`-c copy`** (remux, sin transcode).

---

## 4. Arquitectura objetivo (TO-BE)

```
   Cámaras IP (RTSP / H.264)
        │   (1 sola conexión por cámara la hace go2rtc)
        ▼
┌─────────────────────────────────────────────────────────┐
│  go2rtc  (proceso sidecar, Go — NO es código Python)      │
│   · Ingesta RTSP                                          │
│   · WebRTC / WHEP   ─────────────────────► DIRECTO        │
│   · MSE sobre WS    ─────────────────────► fallback web   │
│   · Restream RTSP local ─────────────────► grabación e IA │
└───┬────────────────────┬───────────────────────┬──────────┘
    │ WebRTC (proxy auth) │ RTSP local (-c copy)  │ RTSP local (1 conexión)
    ▼                     ▼                       ▼
 Clientes           ┌───────────────┐     ┌──────────────────────────────┐
 (web / móvil /     │ RecordingMgr  │     │  Backend Python (Flask)       │
  escritorio)       │ FFmpeg -c copy│     │   · FFmpegWorker SOLO cám. IA │
                    │  → MP4 seg.   │     │   · MotionDetector + YOLO     │
                    └───────────────┘     │   · EventManager / alertas    │
                                          │   · Signaling WebRTC (WHEP)   │
                                          │   · API grabaciones + HLS VOD │
                                          │   · Auth JWT + URLs firmadas  │
                                          └──────────────────────────────┘
```

**Principios:** (1) go2rtc habla con la cámara una sola vez; (2) el directo no se transcodifica
(WebRTC entrega el H.264 nativo); (3) la grabación no se transcodifica (`-c copy`); (4) Python solo
decodifica píxeles para IA; (5) el backend monoproceso se mantiene (go2rtc es externo y sin estado de
negocio → no rompe la restricción de "1 proceso").

---

## 5. Comparativa tecnológica detallada y fundamentación

> Esta sección es la **fundamentación técnica** del cambio (núcleo para la defensa de tesis). Las
> propiedades generales de cada tecnología son verificables en la literatura (§18); los valores
> específicos de **este** sistema están marcados `[PENDIENTE: medición]`.

### 5.1 Tabla maestra de tecnologías de streaming

| Tecnología | Latencia típica | Compresión inter-frame | Ancho de banda (720p, 1 cliente) | Escala con N clientes | CPU servidor | Soporte navegador | Soporte móvil nativo | Reconexión / respuesta | Curva de aprendizaje | Madurez | Uso típico en industria |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **MJPEG** (actual directo web/escritorio) | 0,1–0,5 s | ❌ No (cada frame completo) | **Muy alto** ~5–10 Mbps | ❌ Lineal O(n) | Medio (encode/frame) | ✅ Nativo (`<img>`) | ⚠️ Difícil | ⚡ Instantánea (sin GOP) | 🟢 Baja | Alta (legado) | Cámaras baratas, paneles internos |
| **HLS clásico** (actual HLS-vivo) | 6–30 s | ✅ Sí | ~1,5 Mbps | ✅ Infinita (CDN) | **Alto** (transcode) | ✅ Safari nativo / `hls.js` | ✅ AVPlayer/ExoPlayer | 🟢 Robusta (buffer) | 🟡 Media | Muy alta | VOD, streaming masivo |
| **LL-HLS** | 2–5 s | ✅ Sí | ~1,5 Mbps | ✅ Infinita (CDN) | Alto | ✅ Safari / `hls.js` | ✅ nativo | 🟢 Robusta | 🟠 Media-alta | Media | Deportes/eventos casi-directo |
| **HLS-VOD `-c copy`** (nuevo: grabaciones) | arranque 1–3 s | ✅ Sí | ≈ bitrate cámara | ✅ Infinita | **≈ 0** (remux) | ✅ igual | ✅ nativo | ✅ Seek excelente | 🟡 Media | Alta | Revisar grabaciones (NVR) |
| **MPEG-DASH** | 6–30 s (LL: 2–5) | ✅ Sí | ~1,5 Mbps | ✅ Infinita (CDN) | Alto | ⚠️ `dash.js` (no Safari) | ✅ ExoPlayer | 🟢 Robusta | 🟠 Media-alta | Alta | Netflix/YouTube VOD |
| **WebRTC** (nuevo: directo) | **0,05–0,3 s** | ✅ Sí | **Bajo** ~1–2 Mbps | 🟡 Media (peer/SFU) | **Muy bajo** (`-c copy`) | ✅ Nativo | ⚠️ Requiere libwebrtc | ⚡ ICE rápida | 🔴 **Alta** | Alta | Videollamadas, vigilancia en directo |
| **MSE + fMP4/WS** | 0,3–1,5 s | ✅ Sí | Bajo | 🟡 Media-alta | Muy bajo (`-c copy`) | ⚠️ MSE (iOS Safari ≥17) | ⚠️ Custom | 🟢 Buena | 🟠 Media-alta | Media | Paneles low-latency (Frigate MSE) |
| **RTSP** (actual directo móvil) | 0,2–2 s | ✅ Sí | Bajo | ❌ Baja (cámara limita) | ≈ 0 (passthrough) | ❌ No nativo | ✅ ExoPlayer-RTSP | 🟡 Media | 🟡 Media | Muy alta | Cámaras IP, clientes NVR |
| **RTMP** | 1–3 s | ✅ Sí | Bajo | — (ingesta) | Bajo | ❌ (Flash muerto) | ⚠️ libs | 🟡 Media | 🟡 Media | Alta (legado) | Ingesta a YouTube/Twitch |
| **SRT** | 0,5–3 s | ✅ (transporte) | Bajo | — (transporte) | Bajo | ❌ | ⚠️ libs | 🟢 Robusta en pérdida | 🟠 Media-alta | Creciente | Contribución broadcast |

> **Lectura para la tesis:** la latencia y la escalabilidad son **inversamente proporcionales**. MJPEG
> y WebRTC dan baja latencia; HLS/DASH dan escala masiva vía CDN a costa de segundos de retraso. Para
> **vigilancia en vivo** prima la latencia → **WebRTC**. Para **revisar grabados** (VOD) prima el seek
> y la robustez, no la latencia → **HLS `-c copy`**.

### 5.2 ANTES vs DESPUÉS por funcionalidad

| Función | Tecnología ANTES | Tecnología DESPUÉS | Latencia (antes → después) | CPU servidor (antes → después) | Motivo principal |
|---|---|---|---|---|---|
| Directo web/escritorio | MJPEG (`cv2.imencode`/frame) | **WebRTC** (go2rtc) | 0,1–0,5 s → **0,1–0,3 s** | encode/frame → **≈ 0** | Quitar encode y ancho de banda O(n) |
| Directo "alternativo" | HLS-vivo (2× libx264) | **WebRTC** (go2rtc) | 3–6 s → **0,1–0,3 s** | **1,2–1,6 cores/cám → ≈ 0** | Eliminar transcode y latencia |
| Directo móvil | RTSP directo a cámara | **RTSP vía go2rtc** (o WebRTC) | 1–3 s → 0,5–2 s (o 0,1–0,3) | 0 → 0 | Conexión única a cámara, menos latencia |
| Grabación continua | `rawvideo → libx264 crf28` | **FFmpeg `-c copy`** | — | **0,1–0,2 cores/cám → ≈ 0** | Sin recodificar; mejor calidad |
| Grabación de eventos | Splice `-c copy` (ya) | Igual (sin cambios) | — | ≈ 0 → ≈ 0 | Ya era óptimo |
| Reproducir grabación | MP4 progresivo / HLS `-c copy` | **HLS `-c copy`** (firmado) | <1 s → 1–3 s | ≈ 0 → ≈ 0 | Seek robusto + auth |
| IA (inferencia) | PyTorch CPU, imgsz≈640 | **ONNX/OpenVINO, imgsz=320** | — | alto → **2–4× menos** | Acelerar y reducir RAM (§5.6) |
| Detección movimiento | `absdiff` a 1280×720 | `absdiff` a ~320×180 | — | medio → **~10× menos** | El movimiento no necesita resolución |

> Las cifras de CPU son estimaciones de ingeniería basadas en el comportamiento del código; los
> valores **medidos** del banco de pruebas se completarán en §12 → `[PENDIENTE: medición]`.

### 5.3 Fundamentación J1 — Por qué eliminar MJPEG en el directo

**Problema.** MJPEG no comprime entre fotogramas: cada frame es un JPEG completo. El ancho de banda
crece **linealmente con cada cliente** (no es cacheable ni multicast), y el servidor paga un
`cv2.imencode` por frame ([mjpeg_streamer.py:479-486](../backend/app/streaming/mjpeg_streamer.py#L479-L486)).
**Evidencia.** Con `MAX_MJPEG_CLIENTS_PER_CAMERA=25`, 25 clientes ⇒ `[PENDIENTE: medición de Mbps y CPU]`.
**Alternativas consideradas.** (a) Mantener MJPEG bajando calidad/FPS — parche, no ataca la raíz; (b)
HLS-vivo — alta latencia (descartado, J2); (c) **WebRTC** — H.264 nativo, sin re-encode, sub-segundo.
**Decisión.** WebRTC vía go2rtc. **Impacto esperado.** CPU de directo ≈ 0; ancho de banda con
compresión inter-frame; latencia 0,1–0,3 s. **Coste.** Curva de aprendizaje alta de WebRTC (mitigada
porque go2rtc encapsula el SFU/transporte y el navegador lo soporta nativo).

### 5.4 Fundamentación J2 — Por qué eliminar el HLS en vivo

**Problema.** `LiveHLSService` lanza **dos procesos libx264** por cámara (480p + 720p)
([live_hls_service.py:139-164](../backend/app/streaming/live_hls_service.py#L139-L164)) → el mayor gasto
de CPU del sistema (~1,2–1,6 cores/cámara) **y** 3–6 s de latencia. Para vigilancia en vivo, ambas cosas
son inaceptables. **Alternativas.** (a) Un solo perfil — reduce a la mitad pero sigue con latencia; (b)
LL-HLS — 2–5 s, sigue lejos del tiempo real; (c) **WebRTC** — cubre el caso de uso real. **Decisión.**
Eliminar `LiveHLSService`; el directo lo cubre WebRTC. **Impacto.** −1,2 a −1,6 cores por cámara
`[PENDIENTE: medición]`.

### 5.5 Fundamentación J3 — Por qué grabar con `-c copy` en vez de recodificar

**Problema.** La grabación continua **decodifica H.264 a raw y lo vuelve a codificar** con libx264
ultrafast crf 28 ([recording_manager.py:777-798](../backend/app/recording/recording_manager.py#L777-L798)).
Es un ciclo decode+encode redundante sobre material que **ya viene comprimido**, y además **degrada la
calidad** (crf 28). **Alternativas.** (a) Aceleración HW (NVENC/QSV) — baja el coste pero exige
hardware; (b) **`-c copy`** — copia el flujo de la cámara sin tocar píxeles. **Decisión.** `-c copy`
desde el restream de go2rtc. El **splice de eventos ya usa `-c copy`** y está preparado para MP4
fragmentado, por lo que el cambio es coherente. **Impacto.** CPU de grabación ≈ 0 y **mejor** calidad
(sin recompresión). **Riesgo.** Se pierde la normalización de resolución/fps que hacía libx264 → se
asume la del flujo de la cámara (configurable por sub-stream).

### 5.6 Fundamentación J4 — IA: PyTorch vs ONNX vs OpenVINO

| Backend inferencia | Velocidad relativa CPU | RAM modelo | Curva de aprendizaje | Notas |
|---|---|---|---|---|
| **PyTorch** (actual) | 1× (referencia) | ~100–150 MB | 🟡 Media | Arrastra `torch` completo |
| **ONNX Runtime** | ~2–3× | Menor | 🟢 Baja-media | `model.export(format='onnx')`; portable |
| **OpenVINO** (CPU Intel) | ~3–4× | Menor | 🟠 Media | El más rápido en CPU Intel; soportado por Ultralytics |

Además, fijar **`imgsz=320`** (vs ~640) reduce el coste ~4× (≈ cuadrático con la resolución) y
`torch.set_num_threads()` evita que YOLO compita con FFmpeg. **Decisión.** Exportar YOLOv8n a
ONNX/OpenVINO + `imgsz=320`. **Impacto.** `[PENDIENTE: medición de inferencias/s antes y después]`.

### 5.7 Fundamentación J5 — Por qué go2rtc como capa de medios

**Problema.** Hoy cada consumidor (MJPEG, grabación, móvil, IA) abre **su propia conexión RTSP a la
cámara**. Las cámaras IP suelen aceptar **1–4 sesiones RTSP** simultáneas → se saturan. Además Python
hace el trabajo de transporte/decodificación que no le corresponde. **Alternativas.** (a) **go2rtc**
(Go, ligero, WebRTC/MSE/HLS/RTSP sin transcode; motor usado por **Frigate**); (b) **MediaMTX** (Go,
equivalente); (c) seguir sin servidor de medios (statu quo, descartado). **Decisión.** go2rtc como
sidecar (D-2 en §6 deja abierta la comparación final con MediaMTX). **Impacto.** Una sola conexión por
cámara; reexposición multi-protocolo `-c copy`; Python libre salvo IA.

### 5.8 Fundamentación J6 — Por qué conservar HLS solo para grabaciones (VOD)

HLS es **malo para el directo** (latencia) pero **ideal para VOD**: seek nativo, buffer adaptativo y
soporte nativo en reproductores móviles. Como las grabaciones ya son H.264, el HLS se genera con
**`-c copy`** (CPU ≈ 0), reutilizando `HLSService`. **Decisión.** Mantener y reutilizar `HLSService`
para reproducción de grabaciones; no usarlo para el directo.

### 5.9 Curva de aprendizaje y esfuerzo de implementación (resumen)

| Cambio | Curva de aprendizaje | Esfuerzo dev | Riesgo técnico | Reversible |
|---|---|---|---|---|
| Grabación `-c copy` | 🟢 Baja | Bajo-medio | Bajo | ✅ (flag) |
| Apagar HLS-vivo | 🟢 Baja | Trivial | Bajo | ✅ |
| Motion en baja resolución | 🟢 Baja | Bajo | Bajo | ✅ |
| IA → ONNX/OpenVINO | 🟡 Media | Medio | Medio | ✅ |
| go2rtc sidecar | 🟡 Media | Medio | Medio | ✅ |
| Directo WebRTC (web/escritorio) | 🔴 Alta | Medio-alto | Medio | ✅ (MJPEG en paralelo) |
| Móvil RTSP→go2rtc (camino A) | 🟢 Baja | Mínimo | Bajo | ✅ |
| Móvil WebRTC (camino B) | 🔴 Alta | Alto | Medio-alto | ✅ |

---

## 6. Decisiones de diseño abiertas

> Decisiones que requieren validación de producto/infra **antes** de implementar. Marcar la opción
> elegida y la fecha.

### D-1 — Latencia del directo en la app móvil  ⭐ (la principal)
**Contexto.** Hoy el móvil reproduce RTSP **directo a la cámara** con ExoPlayer (latencia ~1–3 s, coste
de servidor 0). ¿Necesita el producto **directo sub-segundo** en el teléfono?

| Opción | Tecnología | Latencia | Esfuerzo | Cuándo elegirla |
|---|---|---|---|---|
| **A** (recomendada de inicio) | RTSP vía go2rtc (ExoPlayer sin cambios) | 0,5–2 s | Mínimo (1 URL) | Si ~1–2 s es aceptable |
| **B** | WebRTC/WHEP (libwebrtc Android) | 0,1–0,3 s | Alto (código nuevo) | Si el producto exige tiempo casi-real |

**Decisión:** ☐ A  ☐ B  → `[PENDIENTE: decisión de producto]` · **Justificación:** _______________ · **Fecha:** ________

### D-2 — Servidor de medios: go2rtc vs MediaMTX
Ambos en Go, sin transcode. go2rtc destaca en WebRTC/MSE y es el motor de Frigate; MediaMTX es muy
sólido en RTSP/RTMP/SRT. **Decisión:** ☐ go2rtc ☐ MediaMTX → `[PENDIENTE]` · **Criterio:** _______

### D-3 — Acceso remoto (móvil fuera de la LAN)
WebRTC necesita atravesar NAT. **Decisión:** ☐ Solo LAN ☐ TURN (coturn) ☐ VPN/túnel → `[PENDIENTE]`

### D-4 — Reproducción de grabaciones en móvil: MP4 progresivo vs HLS
MP4 `+faststart` ya funciona y permite seek; HLS añade robustez en redes malas a costa de una
dependencia (`media3-exoplayer-hls`). **Decisión:** ☐ MP4 ☐ HLS → `[PENDIENTE]`

### D-5 — Gestión del proceso go2rtc
☐ Sidecar lanzado/vigilado por el backend ☐ Servicio del sistema (systemd) ☐ Contenedor → `[PENDIENTE]`

---

## 7. Inventario de clases

Nomenclatura: ❌ eliminar · ✏️ modificar · ✅ mantener · ➕ crear.

### 7.1 Backend — Streaming
| Clase / archivo | Acción | Justificación |
|---|---|---|
| `MJPEGStreamer`, `ClientInfo` — [mjpeg_streamer.py](../backend/app/streaming/mjpeg_streamer.py) | ❌ Eliminar | Directo pasa a WebRTC (J1). |
| `LiveHLSService`, `HLSProfile` — [live_hls_service.py](../backend/app/streaming/live_hls_service.py) | ❌ Eliminar | 2× libx264/cámara (J2). |
| `HLSService` — [hls_service.py](../backend/app/streaming/hls_service.py) | ✅/✏️ Mantener+reutilizar | Remux `-c copy` para VOD móvil (J8). Añadir URLs firmadas. |
| `FrameDistributor` — [frame_distributor.py](../backend/app/streaming/frame_distributor.py) | ✏️ Modificar | Solo se instancia para cámaras con IA; quitar consumer "mjpeg". |
| `CircularFrameBuffer`, `FrameData` — [frame_buffer.py](../backend/app/streaming/frame_buffer.py) | ✏️ Modificar | Solo para cámaras con IA. |
| `ImageOptimizer` — [image_optimizer.py](../backend/app/utils/image_optimizer.py) | ✅ Mantener | Thumbnails y snapshots. |

### 7.2 Backend — Captura / cámaras
| Clase / archivo | Acción | Justificación |
|---|---|---|
| `CameraManager` — [camera_manager.py](../backend/app/cameras/camera_manager.py) | ✏️ Modificar | No registra MJPEG ni HLS-vivo; crea buffer/distribuidor/worker **solo si hay IA**; alta/baja en go2rtc. |
| `FFmpegWorker`, `WorkerStatus` — [ffmpeg_worker.py](../backend/app/workers/ffmpeg_worker.py) | ✏️ Modificar | Su input pasa a ser el restream de go2rtc; arranca solo para la cámara de IA. |
| `DualLensSplitter` — [dual_lens_splitter.py](../backend/app/cameras/dual_lens_splitter.py) | ✏️ Modificar | Solo si la cámara dual tiene IA. |
| PTZ/Audio/LED/ONVIF (`PTZController`, `AudioController`, `LEDController`, ONVIF*) | ✅ Mantener | Independientes del transporte de vídeo. |

### 7.3 Backend — IA / movimiento / grabación
| Clase / archivo | Acción | Justificación |
|---|---|---|
| `AIScheduler` — [ai_scheduler.py](../backend/app/processing/ai/ai_scheduler.py) | ✅ Mantener | (Optim. aparte: imgsz, ONNX.) |
| `MotionDetector`, `MotionResult` — [motion_detector.py](../backend/app/processing/motion/motion_detector.py) | ✏️ Modificar (opcional) | Ejecutar sobre frame ~320×180 (J en §5.2). |
| `YLOModelPool`, `Detection` — [model_pool.py](../backend/app/processing/ai/model_pool.py) | ✏️ Modificar | ONNX/OpenVINO + imgsz=320 (J6). |
| `InferenceQueue`, `InferenceTask` — [inference_queue.py](../backend/app/processing/ai/inference_queue.py) | ✅ Mantener | — |
| `RecordingManager` — [recording_manager.py](../backend/app/recording/recording_manager.py) | ✏️ Modificar | Continuo → `-c copy` desde go2rtc; thumbnails; splice se conserva (J3). |
| `StorageManager` — [storage_manager.py](../backend/app/recording/storage_manager.py) | ✅ Mantener | — |

### 7.4 Backend — API / infraestructura
| Clase / archivo | Acción | Justificación |
|---|---|---|
| Rutas cámaras — [api/routes/cameras.py](../backend/app/api/routes/cameras.py) | ✏️ Modificar | Quitar MJPEG/HLS-vivo; añadir signaling WebRTC y `stream_url` go2rtc; mantener `/latency`. |
| Rutas móviles — [api/routes/mobile.py](../backend/app/api/routes/mobile.py) | ✏️ Modificar | Listado grabaciones, HLS-VOD firmado, thumbnails. |
| ➕ `Go2RtcManager` (nuevo) | ➕ Crear | Genera config desde BD, arranca/vigila go2rtc. |
| ➕ `WebRTCSignalingService` (nuevo) | ➕ Crear | Proxy WHEP autenticado (JWT + permisos) hacia go2rtc. |
| ➕ `SignedUrlService` (nuevo) | ➕ Crear | Tokens cortos para medios (players nativos sin `Authorization`). |
| `MetricsCollector`, `EventManager`, `DatabaseManager`, `DependencyContainer`, auth, repos | ✅ Mantener | — |

### 7.5 Cliente de escritorio (PySide6)
| Clase / archivo | Acción | Justificación |
|---|---|---|
| `MJPEGThread`, `VideoStreamerService`, `Frame` — [video_streamer.py](../desktop_app/src/services/video_streamer.py) | ❌ Eliminar/reescribir | Directo deja de ser MJPEG. |
| ➕ `LiveRtspPlayer` (nuevo, reusa libVLC) | ➕ Crear | Reproduce el restream RTSP de go2rtc con `VLCPlayer` (ya existe). |
| `VLCPlayer`, `PlaybackService` — [playback_service.py](../desktop_app/src/services/playback_service.py) | ✅ Mantener | Ya reproduce grabaciones con VLC. |
| `CameraWidget`, `VideoPlayerWidget`, `LiveView` — [live_view.py](../desktop_app/src/ui/views/live_view.py) | ✏️ Modificar | Usar el nuevo player. |
| `APIClient` — [api_client.py](../desktop_app/src/services/api_client.py) | ✏️ Modificar | Quitar URLs MJPEG; pedir `stream_url` y URLs firmadas. |

### 7.6 App móvil (CamLink_app) — ver §11 para el detalle
| Clase / archivo | Acción | Justificación |
|---|---|---|
| `LiveViewFragment` — [LiveViewFragment.kt](../CamLink_app/app/src/main/java/com/ipn/mx/onvif/ui/LiveViewFragment.kt) | ✏️ Modificar | Camino A: usar `stream_url` de go2rtc. Camino B: WebRTC. |
| `PlaybackFragment` — [PlaybackFragment.kt](../CamLink_app/app/src/main/java/com/ipn/mx/onvif/ui/PlaybackFragment.kt) | ✏️ Modificar | `file_url` firmado; opcional HLS. |
| `RecordingAdapter`/`RecordingsFragment` | ✏️ Modificar | Thumbnails en el listado. |
| `ApiService`, `ApiModels` | ✏️ Modificar | `stream_url`, `thumbnail_url`, (opcional) endpoint HLS/webrtc. |

### 7.7 Resumen numérico
- **Eliminar:** 4 clases backend + 3 escritorio.
- **Crear:** 3 backend (`Go2RtcManager`, `WebRTCSignalingService`, `SignedUrlService`) + 1 escritorio (`LiveRtspPlayer`).
- **Modificar:** ~8 backend, ~3 escritorio, ~4 móvil.

---

## 8. Nuevo componente: go2rtc

### 8.1 Por qué go2rtc
Escrito en Go (binario único, ligero); soporta WebRTC/WHEP, MSE, RTSP, HLS, MJPEG de salida con
**`-c copy`**; resuelve "una conexión por cámara"; es el motor de medios de **Frigate**. Alternativa:
**MediaMTX** (ver D-2).

### 8.2 Despliegue
Binario gestionado por `Go2RtcManager` como **proceso sidecar** (arranque/parada/reinicio vigilado);
config generada desde la BD (`Camera.rtsp_url`, dual-lens, etc.). Ver D-5.

### 8.3 Esqueleto de configuración (generado)
```yaml
# go2rtc.yaml (generado por Go2RtcManager desde la BD)
streams:
  cam_1: rtsp://usuario:pass@192.168.1.50:554/stream1
  cam_2: rtsp://usuario:pass@192.168.1.51:554/stream1
api:
  listen: "127.0.0.1:1984"     # solo localhost; el backend hace de proxy con auth
webrtc:
  candidates:
    - 192.168.1.10:8555        # IP del servidor en la LAN (ICE host)
    # - stun:stun.l.google.com:19302   # si hay acceso remoto (ver D-3)
```

---

## 9. Flujos detallados

### 9.1 Directo (WebRTC) — web/escritorio
```
Cliente                         Backend (Flask)                 go2rtc (127.0.0.1:1984)
  │ POST /api/v1/cameras/5/webrtc  (JWT, SDP offer) ─►│                                    │
  │              valida JWT + PermissionService.can_view(user, 5)                          │
  │                              │── WHEP: POST offer ───────────────►│                    │
  │                              │◄── SDP answer ─────────────────────┤                    │
  │◄── SDP answer ───────────────┤                                    │                    │
  │════════ media WebRTC (H.264 nativo, DTLS-SRTP, sin transcode) ════│                    │
```
Latencia 0,1–0,3 s; CPU backend ≈ 0; autorización antes de reenviar el offer.

### 9.2 Reproducción de grabaciones (HLS VOD) — foco app móvil
```
App móvil                        Backend (Flask)                   Disco
  │ 1. GET /recordings?camera_id=5&from=…&to=…  (JWT) ─►│ valida JWT+permiso              │
  │◄── lista [{id, ts, dur, thumb_url}] ────────────────┤                                 │
  │ 2. usuario toca grabación 123                                                          │
  │ 3. GET /recordings/123/hls  (JWT) ─►│ HLSService: remux -c copy ─────────────────────┤
  │◄── index.m3u8 (segmentos con ?token=corto) ─────────┤                                 │
  │ 4. ExoPlayer/AVPlayer descarga .ts ?token=… (seek nativo) ◄═════ .ts (H.264) ═════════│
```
Reproductor nativo; CPU servidor ≈ 0 (remux); auth por **URL firmada**; thumbnails al cerrar grabación.

### 9.3 IA (sin cambios de lógica, sí de fuente)
```
go2rtc (restream RTSP local) ─► FFmpegWorker (decode raw, SOLO cám. IA)
                                   └─► CircularFrameBuffer ─► FrameDistributor
                                         └─► AIScheduler ─► MotionDetector ─► YOLO
                                               └─► EventManager ─► (alertas + splice -c copy)
```

---

## 10. Cambios en el cliente de escritorio (PySide6)
1. **Eliminar** `MJPEGThread`/`VideoStreamerService`/`Frame` de [video_streamer.py](../desktop_app/src/services/video_streamer.py).
2. **Crear** `LiveRtspPlayer` reusando `VLCPlayer` (ya en [playback_service.py](../desktop_app/src/services/playback_service.py)) apuntando al restream RTSP de go2rtc (`rtsp://servidor:8554/cam_5`). LAN ≈ 0,2–0,5 s. Alternativa: `QWebEngineView` con WebRTC de go2rtc.
3. **Modificar** `CameraWidget`/`LiveView`/`VideoPlayerWidget` para usar el nuevo player.
4. **Modificar** `APIClient`: el endpoint de "stream URL" devuelve la URL de go2rtc / info de signaling.

---

## 11. Cambios en la app móvil (CamLink_app — Android/Kotlin)

> Código en [CamLink_app/](../CamLink_app/). Android nativo (`com.ipn.mx.onvif`, minSdk 29 / target 35),
> **ExoPlayer/Media3 1.5.1**, Retrofit 2.11 + OkHttp 4.12. **No** usa React Native ni WebRTC.

### 11.1 Estado actual (AS-IS) — verificado en el código
| Función | Implementación actual | Coste servidor | Latencia |
|---|---|---|---|
| **Directo** | `ExoPlayer` + `RtspMediaSource` sobre `camera.rtspUrl` **directo a la cámara** — [LiveViewFragment.kt:282-283](../CamLink_app/app/src/main/java/com/ipn/mx/onvif/ui/LiveViewFragment.kt#L282-L283) | **0** | ~1–3 s `[PENDIENTE: medir]` |
| **Grabaciones** | `ExoPlayer` sobre `recording.fileUrl` **MP4 progresivo** — [PlaybackFragment.kt:120-131](../CamLink_app/app/src/main/java/com/ipn/mx/onvif/ui/PlaybackFragment.kt#L120-L131) | 0 | <1 s |
| **API/auth** | Retrofit + interceptor Bearer + `JwtAuthenticator` (401→refresh) — [RetrofitClient.kt:44-66](../CamLink_app/app/src/main/java/com/ipn/mx/onvif/network/RetrofitClient.kt#L44-L66) | — | — |

> **Clave:** el directo móvil **ya no consume CPU del servidor** (va directo a la cámara). El ahorro de
> CPU de la migración viene de escritorio/web + HLS-vivo + recodificación de grabación. En móvil el
> beneficio es **latencia** y **conexión única a la cámara** (saturación de sesiones RTSP).

### 11.2 ⚠️ Riesgo detectado
El interceptor solo añade `Authorization` a las llamadas **Retrofit**; ExoPlayer **no** lo envía al
pedir el MP4/RTSP. Hoy funciona porque RTSP lleva credenciales en la URL y el `fileUrl` va sin auth o
con token embebido → **verificar** y migrar a URL firmada.

### 11.3 Directo (TO-BE) — la decisión D-1
Ver §6 D-1. Camino **A** (RTSP→go2rtc, mínimo, 0,5–2 s) recomendado de inicio; camino **B** (WebRTC,
0,1–0,3 s, alto esfuerzo) solo si se exige sub-segundo.

### 11.4 Grabaciones (TO-BE)
El MP4 progresivo ya funciona (CPU 0). Cambios: **URL firmada** para `file_url`; **thumbnails** en el
listado (`RecordingResponse` ya tiene patrón en `NotificationItem.thumbnail_url`); HLS opcional (D-4).

### 11.5 Tabla de endpoints (✅ existe · ➕ nuevo · ✏️ modificar)
| Estado | Método | Ruta | Auth | Devuelve |
|---|---|---|---|---|
| ➕ (solo camino B) | `POST` | `/api/v1/cameras/<id>/webrtc` | JWT | SDP answer (WHEP) |
| ✏️ | `GET` | `/api/v1/cameras/` | JWT | añadir `stream_url` de go2rtc |
| ✅ | `GET` | `/api/v1/recordings` | JWT | listado (añadir `thumbnail_url`) |
| ✅ | `GET` | `/api/v1/recordings/<id>` | JWT | detalle con `file_url` (→ firmado) |
| ➕ | `GET` | `/api/v1/recordings/<id>/hls` | JWT | `index.m3u8` (opcional, D-4) |
| ➕ | `GET` | `/api/v1/recordings/<id>/thumbnail` | JWT/firmada | JPEG |
| ✏️ | `GET` | `file_url` del MP4 | → URL firmada | MP4 con `Range` |

---

## 12. Plantilla de benchmarking ANTES / DESPUÉS

🟨 **HUECO** — Rellenar con mediciones reales. Mismas cámaras y clientes en ambas columnas.

### 12.1 Consumo de CPU/RAM
| Escenario | ANTES CPU% | DESPUÉS CPU% | ANTES RAM | DESPUÉS RAM | Δ |
|---|---|---|---|---|---|
| Idle (4 cám, sin clientes)        | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` |  |
| 1 cám en directo                  | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` |  |
| 4 cám en directo                  | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` |  |
| 4 cám + IA en 1                   | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` |  |
| 4 cám + IA + grabación continua   | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` |  |
| Reproducir 1 grabación (móvil)    | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` |  |

### 12.2 Latencia de directo (vidrio-a-vidrio)
| Métrica | ANTES MJPEG | ANTES HLS-vivo | DESPUÉS WebRTC | DESPUÉS RTSP escritorio | Móvil A (go2rtc) | Móvil B (WebRTC) |
|---|---|---|---|---|---|---|
| Latencia (ms) | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` |
| Tiempo a primer frame (ms) | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` |

> Método vidrio-a-vidrio: cámara apuntando a un cronómetro en pantalla; foto simultánea del original y
> del vídeo recibido; repetir N=`[PENDIENTE]` veces; reportar media y p95.

### 12.3 Arranque/seek de grabación
| Métrica | ANTES (MP4) | DESPUÉS (HLS `-c copy`) | DESPUÉS (MP4+Range) |
|---|---|---|---|
| Tiempo hasta primer fotograma (ms) | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` |
| Tiempo de seek (ms) | `[PENDIENTE]` | `[PENDIENTE]` | `[PENDIENTE]` |

### 12.4 Ancho de banda
| Escenario | ANTES | DESPUÉS | Δ |
|---|---|---|---|
| 1 cliente directo  | `[PENDIENTE]` | `[PENDIENTE]` |  |
| 10 clientes directo | `[PENDIENTE]` | `[PENDIENTE]` |  |

### 12.5 IA
| Métrica | ANTES (PyTorch, imgsz≈640) | DESPUÉS (ONNX/OpenVINO, imgsz=320) |
|---|---|---|
| Inferencias/s | `[PENDIENTE]` | `[PENDIENTE]` |
| RAM modelo (MB) | `[PENDIENTE]` | `[PENDIENTE]` |

---

## 13. Evidencia visual (capturas y GIFs)

🟨 **HUECO** — Guardar en `docs/img/migracion/` y enlazar.

### 13.1 ANTES
```
![GIF: tirones del directo MJPEG bajo carga](img/migracion/before_mjpeg_stall.gif)
> Fig. 13.1 — Congelaciones del preview al competir con IA/grabación. [PENDIENTE: GIF]

![Captura: CPU saturada](img/migracion/before_cpu.png)
> Fig. 13.2 — Procesos ffmpeg de transcode ocupando los cores. [PENDIENTE: captura]

![Captura: /latency con frame_age alto](img/migracion/before_latency.png)
> Fig. 13.3 — frame_age_ms elevado = cuello de botella backend. [PENDIENTE: captura]
```

### 13.2 DESPUÉS
```
![GIF: directo WebRTC fluido](img/migracion/after_webrtc_smooth.gif)
> Fig. 13.4 — Mismo escenario, sin tirones. [PENDIENTE: GIF]

![Captura: CPU en reposo con go2rtc](img/migracion/after_cpu.png)
> Fig. 13.5 — Sin procesos de transcode; CPU libre salvo IA. [PENDIENTE: captura]

![Captura: reproducción de grabación en móvil (HLS)](img/migracion/after_mobile_playback.png)
> Fig. 13.6 — HLS VOD en el reproductor nativo. [PENDIENTE: captura]
```

### 13.3 Diagramas comparativos
```
![Diagrama AS-IS vs TO-BE](img/migracion/arquitectura_comparativa.png)
> Fig. 13.7 — Pipeline antiguo (decode+encode software) vs nuevo (go2rtc -c copy). [PENDIENTE: diagrama]
```

---

## 14. Plan de migración por fases

| Fase | Objetivo | Tareas principales | Reversible |
|---|---|---|---|
| **0. Línea base** | Medir el "antes" | Rellenar §2.2, §12, §13.1 | — |
| **1. go2rtc en paralelo** | Levantar go2rtc sin tocar nada | `Go2RtcManager`, config desde BD, validar restream RTSP local | ✅ |
| **2. Grabación `-c copy`** | Quitar el re-encode | `RecordingManager` (continuo desde go2rtc), thumbnails, verificar splice | ✅ (flag) |
| **3. Directo WebRTC** | Sustituir MJPEG | `WebRTCSignalingService`, endpoint `/webrtc`, cliente web/escritorio | ✅ (MJPEG en paralelo) |
| **4. Grabaciones VOD móvil** | HLS firmado + thumbnails | `SignedUrlService`, endpoints, listado | ✅ |
| **5. Móvil directo** | Camino A (o B según D-1) | `stream_url` go2rtc en `LiveViewFragment` (o WebRTC) | ✅ |
| **6. Limpieza** | Borrar lo obsoleto | Eliminar `MJPEGStreamer`, `LiveHLSService`, `video_streamer.py` | ❌ (commit final) |
| **7. Medir el "después"** | Validar mejora | Rellenar §12/§13.2 y comparar | — |

> Recomendación: mantener MJPEG **en paralelo** (tras flag) durante 3–5 para comparar y revertir.

---

## 15. Riesgos y mitigaciones
| Riesgo | Impacto | Mitigación |
|---|---|---|
| WebRTC no atraviesa NAT (móvil remoto) | Directo no carga fuera de LAN | coturn (TURN) o túnel (D-3) |
| go2rtc cae | Sin directo | `Go2RtcManager` vigila/reinicia; healthcheck en `/health` |
| Players nativos sin `Authorization` | Medios sin auth o 401 | `SignedUrlService` (tokens cortos en query) |
| GOP largo en cámara | Latencia inicial alta | Bajar GOP (ver CLAUDE.md §Latency) o sub-stream GOP corto |
| Reescritura cliente escritorio | Esfuerzo | Reusar `VLCPlayer` (RTSP) → bajo esfuerzo |
| IA sigue necesitando decode | No baja a 0 el CPU | Esperado; es el único coste legítimo de píxeles |
| H.265 en WebRTC | Soporte navegador limitado | Forzar H.264 en cámara/sub-stream para directo |
| Curva de aprendizaje WebRTC | Retraso de desarrollo | go2rtc encapsula el transporte; empezar por móvil camino A |

---

## 16. Plan de rollback
- **Por fase:** cada fase 1–5 va tras un flag; desactivarlo restaura el camino anterior sin desplegar.
- **Fase 6 (eliminación):** commit aislado; rollback = `git revert`.
- **Datos:** la migración **no cambia el esquema de BD ni el formato de grabación** (MP4 H.264) → sin
  riesgo de pérdida de datos ni migración Alembic.

---

## 17. Glosario
| Término | Definición |
|---|---|
| **WebRTC** | Estándar de vídeo en tiempo real peer-to-peer; latencia sub-segundo. |
| **WHEP** | WebRTC-HTTP Egress Protocol: signaling estándar para *recibir* WebRTC vía HTTP. |
| **MSE** | Media Source Extensions: reproducción de fragmentos en el navegador (fallback de WebRTC). |
| **HLS** | HTTP Live Streaming: vídeo segmentado por HTTP; ideal para VOD/grabaciones. |
| **`-c copy`** | Remux sin recodificar: copia el códec original; CPU ≈ 0. |
| **go2rtc / MediaMTX** | Servidores de medios en Go que reexponen RTSP como WebRTC/HLS/MSE sin transcode. |
| **TURN / STUN (coturn)** | Servidores para que WebRTC atraviese NAT en acceso remoto. |
| **GOP / keyframe** | Intervalo entre fotogramas clave; determina la latencia inicial de un stream. |
| **VOD** | Video On Demand: vídeo pregrabado (las grabaciones). |
| **ICE / SDP** | Negociación de candidatos de red / descripción de sesión en WebRTC. |
| **fMP4** | MP4 fragmentado, base de HLS/DASH/MSE de baja latencia. |

---

## 18. Referencias
1. W3C — *WebRTC 1.0: Real-Time Communication Between Browsers*. `[PENDIENTE: URL/cita]`
2. IETF — *HTTP Live Streaming (RFC 8216)*. `[PENDIENTE: URL/cita]`
3. IETF draft — *WebRTC-HTTP Egress Protocol (WHEP)*. `[PENDIENTE: URL/cita]`
4. go2rtc — documentación oficial del proyecto. `[PENDIENTE: URL]`
5. MediaMTX — documentación oficial del proyecto. `[PENDIENTE: URL]`
6. Frigate NVR — arquitectura (uso de go2rtc + detección de objetos). `[PENDIENTE: URL]`
7. Ultralytics YOLOv8 — exportación ONNX/OpenVINO. `[PENDIENTE: URL]`
8. AndroidX Media3 (ExoPlayer) — fuentes RTSP/HLS. `[PENDIENTE: URL]`
9. `[PENDIENTE: añadir normas de citación de la institución / formato IEEE o APA]`

---

_Fin del documento — versión 0.2 (borrador). Pendiente: completar 🟨 HUECOS §2.2, §12, §13 y los
`[corchetes]` de mediciones y referencias._
