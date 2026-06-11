# PARTE 6 — go2rtc en Profundidad

> Capítulo crítico. go2rtc es la **única** capa de streaming en vivo del sistema. Resuelve el problema central de las cámaras IP baratas (pocas conexiones RTSP) y entrega WebRTC sub-segundo. Implementado en `backend/app/streaming/`.

## 6.1 Qué es go2rtc

go2rtc es un **servidor de medios ligero** (un binario Go) que:
- **Ingiere** una fuente una sola vez (RTSP, FFmpeg, etc.).
- **Re-expone** ese flujo simultáneamente como **WebRTC, RTSP, HLS, MSE/MP4**.
- Puede **copiar** el stream sin recodificar (`-c copy`, CPU≈0) o **transcodificar** (FFmpeg interno o `exec:`).

**El problema que resuelve aquí:** una cámara IP suele aceptar 1–4 sesiones RTSP. Si el preview, la grabación, la IA y cada móvil abrieran su propia sesión, la cámara se satura y se cae. go2rtc abre **1 conexión** a la cámara y multiplexa por software a N consumidores.

## 6.2 Cómo lo usa el proyecto (`Go2RtcManager`)

`Go2RtcManager` (singleton) gobierna todo el ciclo de vida:

| Método | Qué hace |
|---|---|
| `start(cameras)` | Valida binario, genera YAML, lanza proceso, arranca **supervisor** + **reconciliador**. |
| `write_config(cameras)` | Genera `go2rtc.generated.yaml` desde la BD. |
| `reconcile()` / `_reconcile_loop()` | Cada 15 s lee la BD; si cambió el conjunto de streams, regenera YAML y reinicia. |
| `rtsp_restream_url(id, lens, quality)` | `rtsp://<host>:8554/cam_<id>[_l1][_medium]`. |
| `hls_url(id, lens, quality)` | `http://<host>:1984/api/stream.m3u8?src=cam_<id>...`. |
| `webrtc_api_base()` | `http://127.0.0.1:1984` (para el proxy de señalización). |
| `stop()` | Detiene supervisor y mata el proceso. |

**Supervisor:** vigila `proc.poll()` cada 2 s; si muere, reinicia con backoff `[1,2,5,10,20,30]s`.
**Reconciliador:** sincroniza la config con la BD (añadir/activar cámara → nuevo stream) con **guardia anti-churn** (si la BD se lee vacía, conserva la config previa).

## 6.3 Cómo se configuran los streams: el `go2rtc.yaml`

El YAML lo genera la función **pura** `build_go2rtc_config(cameras, ...)` (testeable). Estructura real generada:

```yaml
api:
  listen: 0.0.0.0:1984          # API HTTP (HLS, WebRTC signaling, MSE)
rtsp:
  listen: :8554                 # restream RTSP
webrtc:
  listen: :8555
  candidates:
  - 192.168.0.101:8555          # candidato ICE = IP LAN del servidor
streams:
  # Stream nativo: RTSP de la cámara, SIN transcode (-c copy)
  cam_9: rtsp://admin:admin@192.168.0.100:554/user=admin_password=...?real_stream

  # Dual-lens, lente 1 (mitad inferior): decode HEVC por QSV, crop, encode H.264 por GPU
  cam_9_l1: exec:ffmpeg -hide_banner -loglevel error -c:v hevc_qsv -rtsp_transport tcp
            -i rtsp://127.0.0.1:8554/cam_9 -vf crop=iw:ih/2:0:ih/2
            -c:v h264_qsv -g 15 -bf 0 -async_depth 1 -an -rtsp_transport tcp -f rtsp {output}
  cam_9_l1_medium: exec:ffmpeg ... -vf crop=iw:ih/2:0:ih/2,scale=-2:480 ...
  cam_9_l1_low:    exec:ffmpeg ... -vf crop=iw:ih/2:0:ih/2,scale=-2:360 ...

  # Lente 2 (mitad superior)
  cam_9_l2:        exec:ffmpeg ... -vf crop=iw:ih/2:0:0 ...
  cam_9_l2_medium: ...
  cam_9_l2_low:    ...
log:
  level: info
```

**Reglas de negocio de la generación:**
1. Sólo cámaras `is_active=True` (salvo el reconciliador, que incluye inactivas — go2rtc conecta perezoso, sin coste si nadie consume).
2. Cámaras sin `rtsp_url` se omiten.
3. `cam_<id>` = stream nativo (`-c copy`).
4. Calidades `cam_<id>_medium` (480p), `cam_<id>_low` (360p).
5. Dual-lens: `cam_<id>_l1/_l2` (crop por hardware) + sus calidades.
6. Con `GO2RTC_HWACCEL=qsv`: decoder `hevc_qsv`, encoder `h264_qsv`, flags de baja latencia `-g 15 -bf 0 -async_depth 1`.

**Por qué `exec:` explícito y no `#hardware=qsv`:** con `-c:v hevc_qsv` los frames no quedan en formato compatible con el atajo `#hardware`; hay que emitir el comando FFmpeg completo.

**Significado de los flags de transcode:**
- `-g 15`: GOP de 15 frames (~1 s a 15 fps) → recuperación rápida ante pérdidas WiFi.
- `-bf 0`: sin B-frames → menor latencia.
- `-async_depth 1`: QSV entrega frame a frame (menos jitter).
- `crop=iw:ih/2:0:ih/2` / `0:0`: divide el frame "apilado" en lente inferior/superior.
- `{output}`: placeholder que go2rtc sustituye por su RTSP de salida.

## 6.4 Cómo se publica un stream

"Publicar" aquí = declararlo en el YAML. go2rtc **no** conecta hasta que **alguien consume** (lazy). Flujo de alta de cámara en caliente:
1. `POST /cameras` crea la fila.
2. El **reconciliador** (cada 15 s) detecta el cambio, regenera el YAML con `cam_<nuevo>` y reinicia go2rtc.
3. `StreamKeepAlive` abre un consumidor `-c copy` de `cam_<nuevo>_medium` para mantener caliente el transcoder.

## 6.5 Cómo se consume un stream (endpoints de go2rtc usados)

| Endpoint go2rtc | Protocolo | Quién lo usa |
|---|---|---|
| `rtsp://<host>:8554/cam_X[...]` | RTSP | Escritorio (VLC), grabación, IA. |
| `http://<host>:1984/api/stream.m3u8?src=cam_X[...]` | HLS | Android (ExoPlayer). |
| `http://127.0.0.1:1984/api/webrtc?src=cam_X` | WebRTC (WHEP) | Backend (proxy de señalización). |
| `http://<host>:1984/api/...` (MSE/streams) | HTTP | Diagnóstico/visor web. |

## 6.6 Cómo interactúa con FFmpeg

Dos planos:
- **Interno a go2rtc:** los streams `exec:ffmpeg ...` o `ffmpeg:...` ejecutan FFmpeg para crop/scale/transcode (dual-lens, calidades).
- **Externo (consumidores del proyecto):** `RecordingManager` y `AIFrameSource` lanzan **su propio** FFmpeg que lee del **restream** de go2rtc (`rtsp://127.0.0.1:8554/cam_X[_low]`), no de la cámara. Así, captura + grabación + IA comparten 1 sola conexión a la cámara (vía go2rtc).

`FFmpegWorker` puede leer del restream si `GO2RTC_ENABLED && GO2RTC_AS_SOURCE` (`_go2rtc_source_url()`), reforzando "1 conexión RTSP, N consumidores".

## 6.7 Cómo interactúa con RTSP

go2rtc es **cliente RTSP** de la cámara (ingesta) y **servidor RTSP** del restream (puerto 8554). El restream es lo que consumen VLC, la grabación y la IA. Transporte TCP por defecto (`RTSP_TRANSPORT=tcp`) por fiabilidad en WiFi.

## 6.8 Cómo interactúa con WebRTC

```
Cliente ──POST /api/v1/cameras/9/webrtc (SDP offer, JWT, perm view)──► Backend
Backend ──POST http://127.0.0.1:1984/api/webrtc?src=cam_9 (offer)──► go2rtc
go2rtc  ──SDP answer (candidatos ICE: 192.168.0.101:8555)──► Backend ──► Cliente
Cliente ⇆ go2rtc : ICE/STUN, conexión P2P UDP ; el vídeo viaja directo (SRTP)
```

- **Señalización** via backend (autenticada); **medio** P2P (no por Flask).
- **Candidatos ICE:** en LAN aislada, sólo el host `192.168.0.101:8555` (sin STUN/TURN). Para acceso remoto: `GO2RTC_WEBRTC_CANDIDATES=stun:...,turn:...`.
- **`detect_lan_ip()`** autodetecta la IP a anunciar si `GO2RTC_PUBLIC_HOST` está vacío.

## 6.9 Cómo interactúa con el cliente de escritorio

El escritorio **no** habla WebRTC; consume el **restream RTSP** con VLC:
```python
_LIVE_VLC_OPTS = ["--quiet", "--no-video-title-show",
  "--network-caching=150",   # colchón mínimo de red (ms)
  "--rtsp-tcp",              # RTSP sobre TCP (robusto en WiFi)
  "--drop-late-frames",      # descarta tardíos → latencia acotada
  "--no-audio-time-stretch"]
vlc.play_url("rtsp://192.168.0.101:8554/cam_9_l1_medium")
```
Cambiar de cámara/calidad usa `play_url_async()` (swap sin recrear el player, no congela la UI).

## 6.10 `StreamKeepAlive`: por qué existe

El "primer frame" de un transcoder QSV recién arrancado tarda ~3.5–5 s. Para que **conmutar de cámara sea instantáneo**, `StreamKeepAlive` mantiene vivo cada stream activo con un consumidor mínimo:
```
ffmpeg -rtsp_transport tcp -i rtsp://127.0.0.1:8554/cam_X_medium -c copy -f null -
```
`-c copy` (no decodifica, CPU≈0) + `-f null` (no escribe). Reconcilia contra la BD cada 15 s.

## 6.11 Configuración (perillas `GO2RTC_*`)

| Variable | Default | Significado |
|---|---|---|
| `GO2RTC_ENABLED` | true | Activar la capa de vivo. |
| `GO2RTC_BINARY` | "go2rtc" | Ruta/binario (PATH o absoluta). |
| `GO2RTC_API_HOST/PORT` | 0.0.0.0:1984 | API (HLS/WebRTC/MSE). |
| `GO2RTC_RTSP_PORT` | 8554 | Restream RTSP. |
| `GO2RTC_WEBRTC_PORT` | 8555 | Puerto ICE/WebRTC. |
| `GO2RTC_PUBLIC_HOST` | "" | IP anunciada (vacío = autodetectar). |
| `GO2RTC_WEBRTC_CANDIDATES` | "" | Candidatos ICE extra (STUN/TURN). |
| `GO2RTC_HWACCEL` | qsv | "", "qsv" (Intel), "cuda" (NVIDIA). |
| `GO2RTC_AS_SOURCE` | false | El worker/grabación/IA leen el restream en vez de la cámara. |
| `GO2RTC_CONFIG_PATH` | …/go2rtc.generated.yaml | Dónde escribir el YAML. |
| `WEBRTC_ENABLED` | false | Habilitar el proxy de señalización WHEP. |
| `STREAM_KEEPALIVE` | "" | Streams extra a mantener calientes. |

## 6.12 Aceleración por hardware (contexto del proyecto)

Según la memoria del proyecto (RTX 4050 + Intel UHD): **QSV funciona**, **NVENC está bloqueado por un driver viejo**, por eso `GO2RTC_HWACCEL=qsv`. El transcode dual-lens (decode HEVC + crop + encode H.264) por iGPU Intel reduce CPU ~90% frente a `libx264` software.

## 6.13 Resiliencia y red aislada

- **Supervisor + reconciliador + keep-alive + backoff** → auto-recuperación; una caída de go2rtc se repara en ≤30 s sin perder grabación/IA largo rato.
- **Firewall (Windows):** `_ensure_firewall_rules()` abre 1984/8554/8555 (idempotente, UAC si hace falta).
- **Sin internet:** WebRTC con candidatos host locales (P2P en LAN); HLS/RTSP siempre funcionan en LAN.

## 6.14 Tabla "consumidor → URL" (cámara dual-lens id=9, host 192.168.0.101)

| Consumidor | URL |
|---|---|
| Escritorio (VLC), mono | `rtsp://192.168.0.101:8554/cam_9` |
| Escritorio/móvil l1 medium | `rtsp://192.168.0.101:8554/cam_9_l1_medium` |
| Android HLS l1 | `http://192.168.0.101:1984/api/stream.m3u8?src=cam_9_l1` |
| Android HLS l1 low (WiFi débil) | `...?src=cam_9_l1_low` |
| WebRTC (señalización) | `http://127.0.0.1:1984/api/webrtc?src=cam_9` |
| Grabación continua | `rtsp://127.0.0.1:8554/cam_9` (`-c copy`) |
| IA (substream bajo) | `rtsp://127.0.0.1:8554/cam_9_l1_low` |

## 6.15 Conclusiones para defensa

1. **Centralización:** go2rtc es la única vía a la cámara → 1 conexión RTSP, N consumidores.
2. **Multiprotocolo desde un punto:** WebRTC (mínima latencia), RTSP (escritorio), HLS (móvil).
3. **`-c copy` por defecto:** CPU≈0 salvo dual-lens/calidades, que usan QSV.
4. **Auto-gestión:** generación de YAML desde BD + supervisor + reconciliador + keep-alive.
5. **Seguridad:** la señalización WebRTC pasa por el backend (JWT + permiso), go2rtc no se expone directo.
6. **Funciona sin nube:** candidatos ICE locales; todo en LAN.
