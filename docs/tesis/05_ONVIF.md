# PARTE 5 — ONVIF en Profundidad

> Capítulo crítico. ONVIF (Open Network Video Interface Forum) es el estándar que permite descubrir y controlar cámaras IP de distintos fabricantes de forma uniforme. El proyecto lo implementa en `backend/app/cameras/` con un **cliente SOAP propio sin WSDL** (FAST PATH) y un cliente `onvif-zeep` de respaldo (SLOW PATH).

## 5.1 Servicios ONVIF que usa el proyecto

| Servicio ONVIF | Operaciones usadas | Para qué |
|---|---|---|
| **Device** | `GetCapabilities`, `GetDeviceInformation`, `GetSystemDateAndTime`, `SetSystemDateAndTime` | Descubrir capacidades, fabricante/modelo, sincronizar hora. |
| **Media** | `GetProfiles`, `GetStreamUri`, `GetSnapshotUri` | Obtener perfiles, la **URL RTSP** y el snapshot. |
| **PTZ** | `GetConfigurations`, `GetStatus`, `ContinuousMove`, `Stop`, `GotoPreset`/`SetPreset` | Mover la cámara y leer su estado. |
| **Imaging** | `GetImagingSettings`, `SetImagingSettings`, `GetOptions`, `SendAuxiliaryCommand` | LEDs/IR-cut, luz blanca. |
| **Discovery (WS-Discovery)** | `Probe`/`ProbeMatch` | Encontrar cámaras en la LAN. |

**Clases/archivos del proyecto que intervienen:**
- `onvif_discovery.py` → `ONVIFDiscovery` (WS-Discovery + orquestación FAST/SLOW/RTSP).
- `onvif_soap.py` → cliente SOAP manual (construye sobres, maneja auth digest/text).
- `onvif_common.py` → puertos candidatos, cliente zeep, persistencia del puerto.
- `ptz_controller.py` → `PTZController` (ContinuousMove/Stop/presets/status).
- `audio_controller.py`, `led_controller.py`, `time_sync.py` → control físico.
- `camera_heuristics.py` → marca/modelo, dual-lens, sugerencia de URLs.
- `ptz_lock_service.py` → mutex de PTZ.
- `database/models.py` → persiste `Camera` (url, credenciales, `profile_token`, capacidades, diagnóstico).

## 5.2 Cómo descubre cámaras (visión general)

El método `ONVIFDiscovery.discover()` ejecuta cinco fases:

1. **WS-Discovery multicast** a `239.255.255.250:3702`: envía `Probe`, recoge `ProbeMatch`, extrae IP/`XAddrs`.
2. **Pre-check TCP** de cada IP (≈1 s): descarta IPs no alcanzables (p.ej. IP estática de otra subred) y las reporta como `unreachable` con explicación.
3. **FAST PATH (SOAP directo):** por cada IP, prueba ~6 credenciales top × 4 puertos ONVIF en ~6 s; si obtiene `auth_ok + profiles + stream_uri`, devuelve el dict de la cámara.
4. **SLOW PATH (onvif-zeep):** si el FAST PATH falla, carga el WSDL local y reintenta puertos/credenciales para cámaras "difíciles".
5. **Fallback RTSP:** si ONVIF falla del todo, prueba patrones RTSP genéricos → `connection_type="rtsp_fallback"` (sin PTZ/audio ONVIF).

**Puertos ONVIF candidatos** (`onvif_common.candidate_ports`): se prioriza el puerto guardado en `camera.onvif_url` y luego `[80, 8080, 8000, 8899, ...]`.

## 5.3 WS-Discovery: cómo funciona y mensajes XML

WS-Discovery usa **SOAP-over-UDP** sobre el grupo multicast `239.255.255.250:3702`. El cliente difunde un `Probe`; las cámaras que son `NetworkVideoTransmitter` responden `ProbeMatch`.

### Mensaje `Probe` que envía el sistema (ejemplo real de la forma usada)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <s:Header>
    <a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action>
    <a:MessageID>uuid:9f8c2b1e-7a3d-4f1c-bb2a-1c2d3e4f5a6b</a:MessageID>
    <a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>
  </s:Header>
  <s:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </s:Body>
</s:Envelope>
```

- **`Action = ...discovery/Probe`**: indica que es una sonda.
- **`To = urn:...ws:2005:04:discovery`**: dirección lógica de descubrimiento (multicast).
- **`Types = dn:NetworkVideoTransmitter`**: filtra para que respondan **sólo cámaras** (no otros dispositivos WS-Discovery).

### Respuesta `ProbeMatch` que recibe (ejemplo)

```xml
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
  <s:Header>
    <a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</a:Action>
    <a:RelatesTo>uuid:9f8c2b1e-7a3d-4f1c-bb2a-1c2d3e4f5a6b</a:RelatesTo>
  </s:Header>
  <s:Body>
    <d:ProbeMatches>
      <d:ProbeMatch>
        <a:EndpointReference>
          <a:Address>urn:uuid:3fa1f2e0-1234-5678-9abc-001122334455</a:Address>
        </a:EndpointReference>
        <d:Types>dn:NetworkVideoTransmitter tds:Device</d:Types>
        <d:Scopes>
          onvif://www.onvif.org/type/video_encoder
          onvif://www.onvif.org/hardware/IPC-Model
          onvif://www.onvif.org/name/Camera
        </d:Scopes>
        <d:XAddrs>http://192.168.1.19:8000/onvif/device_service</d:XAddrs>
        <d:MetadataVersion>1</d:MetadataVersion>
      </d:ProbeMatch>
    </d:ProbeMatches>
  </s:Body>
</s:Envelope>
```

- **`XAddrs`**: la URL del **device service** — el código extrae de aquí la **IP** y el **puerto** ONVIF.
- **`Scopes`**: pistas de `hardware`/`name`/`type` (las usa `camera_heuristics` para marca/modelo).
- **`RelatesTo`**: correlaciona la respuesta con el `MessageID` del Probe.

## 5.4 Autenticación: WS-Security UsernameToken (Text vs Digest)

ONVIF protege las operaciones con **WS-UsernameToken**. El proyecto intenta primero **PasswordText** (claro) y, si la cámara responde `NotAuthorized`, reintenta con **PasswordDigest** (recomendado). Lógica en `onvif_soap.py`:

```python
root, err = self._do_request(url, body, action, use_digest=False)   # PasswordText
if root is not None:
    return root, None, "PasswordText"
if err and err.kind == "auth":                                      # Fault NotAuthorized
    root2, err2 = self._do_request(url, body, action, use_digest=True)  # PasswordDigest
    if root2 is not None:
        return root2, None, "PasswordDigest"
    return None, err2 or err, ""
```

### Cálculo del PasswordDigest (estándar WS-Security)

```
Nonce       = 16 bytes aleatorios (se envía en Base64)
Created     = timestamp UTC ISO-8601 (p.ej. 2026-06-07T12:00:00Z)
PasswordDigest = Base64( SHA1( Nonce_bytes + Created_bytes + Password_bytes ) )
```

### Cabecera SOAP con UsernameToken (Digest)

```xml
<s:Header>
  <wsse:Security s:mustUnderstand="1"
    xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
    <wsse:UsernameToken>
      <wsse:Username>admin</wsse:Username>
      <wsse:Password Type="...#PasswordDigest">eF3k...Base64Digest...=</wsse:Password>
      <wsse:Nonce EncodingType="...#Base64Binary">bX9q...Base64Nonce...=</wsse:Nonce>
      <wsu:Created>2026-06-07T12:00:00Z</wsu:Created>
    </wsse:UsernameToken>
  </wsse:Security>
</s:Header>
```

> **Defensa:** el digest evita enviar la contraseña en claro; el `Nonce` + `Created` previenen ataques de **replay** (la cámara rechaza nonces repetidos o timestamps fuera de ventana — de ahí la importancia de `time_sync`).

## 5.5 Obtener capacidades (`GetCapabilities`)

### Petición SOAP

```xml
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
  <s:Header><!-- UsernameToken --></s:Header>
  <s:Body>
    <tds:GetCapabilities>
      <tds:Category>All</tds:Category>
    </tds:GetCapabilities>
  </s:Body>
</s:Envelope>
```

### Respuesta (fragmento)

```xml
<tds:GetCapabilitiesResponse>
  <tds:Capabilities>
    <tt:Media>
      <tt:XAddr>http://192.168.1.19:8000/onvif/media_service</tt:XAddr>
    </tt:Media>
    <tt:PTZ>
      <tt:XAddr>http://192.168.1.19:8000/onvif/ptz_service</tt:XAddr>
    </tt:PTZ>
    <tt:Imaging>
      <tt:XAddr>http://192.168.1.19:8000/onvif/imaging_service</tt:XAddr>
    </tt:Imaging>
  </tt:Capabilities>
</tds:GetCapabilitiesResponse>
```

- La presencia de `<tt:PTZ>` → `has_ptz=True`; `<tt:Imaging>` permite control de LEDs/IR; los `XAddr` dan los endpoints de cada servicio.

## 5.6 Obtener perfiles (`GetProfiles`)

### Petición

```xml
<s:Body>
  <trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>
</s:Body>
```

### Respuesta (fragmento)

```xml
<trt:GetProfilesResponse>
  <trt:Profiles token="Profile000" fixed="true">
    <tt:Name>MainStream</tt:Name>
    <tt:VideoEncoderConfiguration token="VEnc000">
      <tt:Encoding>H264</tt:Encoding>
      <tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution>
      <tt:RateControl><tt:FrameRateLimit>25</tt:FrameRateLimit></tt:RateControl>
    </tt:VideoEncoderConfiguration>
    <tt:PTZConfiguration token="PTZCfg000">...</tt:PTZConfiguration>
    <tt:AudioEncoderConfiguration token="AEnc000"><tt:Encoding>G711</tt:Encoding></tt:AudioEncoderConfiguration>
  </trt:Profiles>
</trt:GetProfilesResponse>
```

- **`token="Profile000"`** → se guarda como `Camera.profile_token` (lo usa PTZ y stream).
- **`PTZConfiguration`** presente → la cámara soporta PTZ en ese perfil.
- **`AudioEncoderConfiguration`** → `has_audio=True`.
- **`Resolution`/`FrameRateLimit`** → se guardan `resolution_width/height`, `fps`.

## 5.7 Obtener el stream RTSP (`GetStreamUri`)

### Petición

```xml
<s:Body>
  <trt:GetStreamUri xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
    <trt:StreamSetup>
      <tt:Stream xmlns:tt="http://www.onvif.org/ver10/schema">RTP-Unicast</tt:Stream>
      <tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>
    </trt:StreamSetup>
    <trt:ProfileToken>Profile000</trt:ProfileToken>
  </trt:GetStreamUri>
</s:Body>
```

### Respuesta

```xml
<trt:GetStreamUriResponse>
  <trt:MediaUri>
    <tt:Uri>rtsp://192.168.1.19:554/Streaming/Channels/101</tt:Uri>
    <tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
    <tt:Timeout>PT60S</tt:Timeout>
  </trt:MediaUri>
</trt:GetStreamUriResponse>
```

- **`Uri`** → se guarda como `Camera.rtsp_url` (luego go2rtc lo ingiere). Las credenciales se insertan en la URL (`rtsp://admin:pass@...`).

## 5.8 PTZ: controlar el movimiento (`ContinuousMove` / `Stop`)

`PTZController` (cacheado por cámara) conecta por el puerto correcto, elige el primer perfil con `PTZConfiguration` y valida con `GetStatus`. El movimiento es un **vector de velocidad**:

```python
req = ptz.create_type("ContinuousMove")
req.ProfileToken = "Profile000"
req.Velocity = {"PanTilt": {"x": 0.5, "y": 0.0}, "Zoom": {"x": 0.0}}
ptz.ContinuousMove(req)   # mueve a la derecha a media velocidad
# ...al soltar:
ptz.Stop({"ProfileToken": "Profile000", "PanTilt": True, "Zoom": True})
```

### SOAP `ContinuousMove`

```xml
<s:Body>
  <tptz:ContinuousMove xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
    <tptz:ProfileToken>Profile000</tptz:ProfileToken>
    <tptz:Velocity>
      <tt:PanTilt x="0.5" y="0.0"
        xmlns:tt="http://www.onvif.org/ver10/schema"/>
      <tt:Zoom x="0.0" xmlns:tt="http://www.onvif.org/ver10/schema"/>
    </tptz:Velocity>
  </tptz:ContinuousMove>
</s:Body>
```

- **`PanTilt.x`** ∈ [-1, 1]: negativo = izquierda, positivo = derecha. **`PanTilt.y`**: arriba/abajo. **`Zoom.x`**: acercar/alejar.
- Es **movimiento continuo**: la cámara se mueve hasta recibir `Stop`. Por eso el cliente envía `move` al pulsar y `stop` al soltar.
- **Tipos de movimiento ONVIF:** `ContinuousMove` (velocidad, el que usa el proyecto), `AbsoluteMove` (a una posición exacta), `RelativeMove` (desplazamiento relativo), `GotoPreset` (a un preset guardado).

### Concurrencia: `PTZLockService`

```python
ok, msg = ptz_lock_service.acquire_lock(camera_id=5, user_id=1, username="alice")  # (True, None)
ok, msg = ptz_lock_service.acquire_lock(camera_id=5, user_id=2, username="bob")     # (False, "Cámara en uso por alice (28s restantes)")
```

Mutex en memoria con **timeout** que se auto-libera, **reentrante** para el mismo usuario, y forzable por admin.

## 5.9 Imaging / LEDs / IR-cut

`led_controller.py` usa **Imaging** (`SetImagingSettings` para IR-cut/brillo) y `SendAuxiliaryCommand` para la **luz blanca** (comandos auxiliares como `tt:IRLamp|On`). No todas las cámaras lo soportan; si falla, la UI oculta el control.

## 5.10 Audio bidireccional

- **Escuchar** (`audio_controller.start_listen`): `ffplay -rtsp_transport tcp -fflags nobuffer -i <rtsp> -vn` reproduce el audio de la cámara en los altavoces del cliente del servidor.
- **Hablar** (`start_talk`): `ffmpeg -f dshow -i audio="<mic>" -acodec pcm_alaw -ar 8000 -ac 1 -f rtp rtp://<cam_ip>:5004` envía el micrófono a la cámara por **RTP** (G.711 a-law 8 kHz mono — el códec típico de *backchannel* ONVIF).

## 5.11 Eventos ONVIF

ONVIF define un **PullPoint** de eventos (movimiento detectado por la propia cámara, sabotaje, etc.). **En este proyecto la detección la hace el backend (YOLO + movimiento)**, no el motor de eventos de la cámara; por eso el servicio Events de ONVIF se usa de forma marginal/no central. Es un punto honesto a declarar: *"la inteligencia vive en el servidor, no delegada a la cámara"*, lo que da uniformidad entre cámaras de distinta calidad.

## 5.12 Sincronización de hora (`time_sync`)

`SetSystemDateAndTime` ajusta la hora de la cámara a la local del servidor. **Por qué importa:** (1) los timestamps de eventos/grabaciones deben ser coherentes; (2) **WS-Security Digest** rechaza peticiones con `Created` fuera de ventana, así que una cámara desfasada puede fallar la autenticación.

## 5.13 Qué pasa cuando una cámara NO soporta una capacidad (fallbacks)

El sistema está diseñado para la **heterogeneidad** del parque de cámaras:

| Situación | Manejo en el código |
|---|---|
| IP anunciada de otra subred (no alcanzable) | Pre-check TCP → `connection_type="unreachable"` + instrucciones al usuario. |
| ONVIF falla pero RTSP funciona | `_try_rtsp_fallback()` → patrones RTSP genéricos → `connection_type="rtsp_fallback"` (sin PTZ/audio). |
| Credenciales incorrectas | Fault `NotAuthorized` → error "Invalid credentials" devuelto al usuario. |
| El puerto ONVIF cambió (reinicio) | `candidate_ports()` itera; al encontrar el bueno, `_persist_onvif_port()` lo guarda en BD. |
| Sin perfil con `PTZConfiguration` | `PTZController.is_supported()=False` → UI oculta PTZ. |
| Dos usuarios mueven PTZ a la vez | `PTZLockService` deniega al segundo con tiempo restante. |
| Sin `AudioEncoderConfiguration` | `has_audio=False` → botones de audio ocultos. |

## 5.14 Qué datos ONVIF se almacenan en la BD (`cameras`)

```python
Camera(
  name, ip_address,
  rtsp_url   = "rtsp://admin:12345@192.168.1.19:554/Streaming/Channels/101",
  onvif_url  = "http://192.168.1.19:8000/onvif/device_service",  # puerto descubierto
  username, password,                 # credenciales (TODO: cifrar)
  profile_token = "Profile000",       # perfil para PTZ/stream
  has_ptz, has_leds, has_audio, is_dual_lens,
  resolution_width=1920, resolution_height=1080, fps=30,
  connection_type = "onvif",          # | rtsp_fallback | manual | unreachable
  last_error_code, last_connected_at, fallback_url,
  owner_id,
)
```

## 5.15 Secuencia completa: descubrimiento → control PTZ (resumen)

1. `POST /discovery/discover` → WS-Discovery + pre-check + FAST/SLOW/RTSP → dict por cámara.
2. Usuario confirma → `POST /cameras` → fila `Camera`.
3. `PUT /cameras/<id>/ptz/move?direction=right&speed=0.5` → `PTZLockService.acquire_lock` → `PTZController.move()` → SOAP `ContinuousMove` al `ptz_service` → motor de la cámara gira.
4. Al soltar → `Stop`.

> **Frase de defensa:** *"Implementé un cliente ONVIF de dos niveles: un FAST PATH SOAP propio que resuelve el 90% de las cámaras en segundos, y un SLOW PATH con WSDL/zeep para casos difíciles, más un fallback RTSP puro. La autenticación intenta PasswordText y degrada a PasswordDigest, y todo está blindado contra la heterogeneidad real del parque de cámaras con heurísticas, persistencia del puerto descubierto y un mutex de PTZ."*
