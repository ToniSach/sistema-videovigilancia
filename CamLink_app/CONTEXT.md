# CONTEXT.md — CamLink Android App + Servidor NVR Python
> Archivo de contexto unificado para IAs. Describe el estado completo de la app Android, el servidor Python, y el contrato de integración entre ambos.

---

## 1. Visión general del sistema

```
┌─────────────────────────┐        HTTP/REST (Retrofit)       ┌──────────────────────────────┐
│   CamLink (Android)     │ ◄────────────────────────────────► │  Backend Flask (Python)      │
│   Kotlin · Views XML    │        ip:5000/api/v1/...          │  PostgreSQL · FFmpeg · YOLO  │
└─────────────────────────┘                                    └──────────────────────────────┘
         │                                                               │
         │  RTSP stream (ExoPlayer)                                      │
         └───────────────────────────────────────────────────────────────┘
                    rtsp://user:pass@ip:554/...
```

- La app Android **no habla ONVIF directamente**. Toda la lógica ONVIF/cámara vive en el servidor Python.
- La app se conecta al backend Flask vía **Retrofit + OkHttp**.
- Los streams de video se consumen directamente vía **RTSP con ExoPlayer** (URL obtenida del servidor).
- El servidor también soporta **MJPEG y HLS** como alternativas al RTSP.

---

## 2. App Android — CamLink

### 2.1 Datos técnicos

| Campo | Valor |
|---|---|
| Package | `com.ipn.mx.onvif` |
| Nombre | CamLink |
| minSdk | 29 |
| targetSdk / compileSdk | 34 |
| Lenguaje | Kotlin |
| UI | Views XML (sin Compose, sin ViewBinding) |
| Arquitectura | Single-Activity + Navigation Component |
| Build | AGP 9.0.0, Kotlin 2.2.10 |

### 2.2 Dependencias clave

| Categoría | Librería | Versión |
|---|---|---|
| Navegación | Navigation Fragment + UI KTX | 2.7.7 |
| Cámara (QR) | CameraX core/camera2/lifecycle/view | 1.3.4 |
| QR | ML Kit Barcode Scanning | 17.3.0 |
| Video RTSP | Media3 ExoPlayer + RTSP + UI | 1.3.1 |
| HTTP | OkHttp | 4.12.0 |
| REST | Retrofit2 + Gson converter | 2.11.0 |
| Concurrencia | Kotlinx Coroutines Android | 1.8.1 |
| Lifecycle | ViewModel + Runtime KTX | 2.8.2 |
| OkHttp Logging | OkHttp logging-interceptor | 4.12.0 |

### 2.3 Permisos

```
CAMERA, INTERNET, RECORD_AUDIO, FOREGROUND_SERVICE
```

### 2.4 Sesión / autenticación local

- Almacenada en `SharedPreferences("auth_prefs", MODE_PRIVATE)`
- **Claves de conexión:** `serverIp`, `serverPort`, `serverUser`, `serverPassword`
- **Claves JWT:** `jwt_access_token`, `jwt_refresh_token`
- Auto-login: `QrScanFragment` verifica si existen `serverIp` **y** `jwt_access_token` al iniciar
- Si hay token → navega directo a `liveViewFragment` sin re-login
- Si no hay token → muestra pantalla de conexión manual
- Cerrar sesión (`menuCerrarSesion`): llama `RetrofitClient.clearToken()` + `.clear()` en prefs + navega a `qrScanFragment`

### 2.5 Configuración de seguridad de red (Android)

Android 9+ (API 28+) bloquea por defecto el tráfico HTTP no cifrado. La app tiene `minSdk 29` y se conecta a `http://{ip}:5000/...`, por lo que se requiere configuración explícita.

**Archivo `res/xml/network_security_config.xml` (creado):**
```xml
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="true" />
</network-security-config>
```
> Permite HTTP en todas las IPs de la LAN. Adecuado para un NVR en red local privada.

**`AndroidManifest.xml` — atributos añadidos a `<application>`:**
```xml
<application
    android:networkSecurityConfig="@xml/network_security_config"
    android:usesCleartextTraffic="true"
    ... >
```
- `networkSecurityConfig` es el método oficial y robusto para Android 9+.
- `usesCleartextTraffic` actúa como respaldo para API 27 e inferior.

---

## 3. Navegación Android

**Destino inicial:** `qrScanFragment`

```
qrScanFragment ──[action_qr_to_liveView]──► liveViewFragment ◄──[action_cameraList_to_liveView]── cameraListFragment
                   (popUpTo inclusive)            │                         │
                                                  ├──► cameraListFragment  ├──► recordingsFragment
                                                  ├──► recordingsFragment  │         └──► playbackFragment
                                                  └──► eventConfigFragment └──► eventConfigFragment
```

| Acción | Origen → Destino |
|---|---|
| `action_qr_to_liveView` | qrScan → liveView (popUpTo qrScan inclusive) |
| `action_liveView_to_cameraList` | liveView → cameraList |
| `action_liveView_to_recordings` | liveView → recordings |
| `action_liveView_to_eventConfig` | liveView → eventConfig |
| `action_cameraList_to_liveView` | cameraList → liveView |
| `action_cameraList_to_recordings` | cameraList → recordings |
| `action_cameraList_to_eventConfig` | cameraList → eventConfig |
| `action_recordings_to_playback` | recordings → playback |
| `action_recordings_to_eventConfig` | recordings → eventConfig |

**Argumentos de navegación:**
- `liveViewFragment`: `cameraId: String` (default `""`)
- `playbackFragment`: `recordingId: String` (default `""`)
- `qrScan → liveView` también pasa: `serverIp`, `serverPort`, `serverUser`, `serverPassword`

---

## 4. Fragmentos Android — estado y vistas

### 4.1 QrScanFragment (`fragment_qr_scan.xml`)

**Función:** Login / escaneo QR.

**IDs de vistas:**
- `tvInstruction` — texto de instrucción
- `qrContainer` — FrameLayout donde irá el PreviewView de CameraX
- `btnManualConnect` — abre diálogo de conexión manual

**Diálogo manual (`dialog_manual_connect.xml`):**
- `etIp` — campo IP (ej: `192.168.1.100`)
- `etPort` — campo puerto (numérico, ej: `5000`)
- `etUser` — usuario
- `etPassword` — contraseña

**Flujo implementado:**
1. Verifica `serverIp` + `jwt_access_token` en SharedPreferences → auto-login si ambos existen
2. Si no → muestra `btnManualConnect`
3. Diálogo manual → guarda IP/puerto en prefs → `POST /api/v1/auth/login` → guarda JWT → navega a `liveViewFragment`
4. Maneja errores HTTP (401, 404) y de conexión con mensajes descriptivos en Toast

**TODO pendiente:** Implementar CameraX + ML Kit para escaneo QR real. Placeholder comentado presente en el código. El QR debe contener el texto en formato `ip:port:user:password`.

---

### 4.2 LiveViewFragment (`fragment_live_view.xml`)

**Función:** Vista principal de cámara en vivo + controles PTZ.

**IDs de vistas:**
- `cameraFeedContainer` — FrameLayout 16:9 donde va el player RTSP
- `btnRemoveCam` — X roja (top-end del feed)
- `btnPrevFeed` — flecha izquierda (dentro del feed)
- `btnNextFeed` — flecha derecha (dentro del feed)
- `btnMic` — toggle micrófono
- `btnRecord` — toggle grabación (56dp, fondo rojo especial)
- `btnNight` — toggle modo noche
- `joystickOuter` — FrameLayout 110dp, anillo exterior del joystick PTZ
- `joystickThumb` — View 50dp, botón central del joystick
- `btnVideoList` — navega a cameraList
- `btnFullscreen` — pantalla completa (pendiente)

**Estado interno:**
```kotlin
var cameras: List<CameraResponse>   // lista cargada del servidor
var currentCamIndex: Int            // índice de la cámara activa
var player: ExoPlayer?              // instancia actual del player
var isRecording: Boolean
var micOn: Boolean
var nightOn: Boolean
```

**Flujo implementado:**
1. Al crear la vista, añade un `SurfaceView` al `cameraFeedContainer`
2. Llama a `GET /api/v1/cameras` → `Response<CameraListResponse>` → verifica `isSuccessful` → `body()?.data` → llena `cameras`
3. Reproduce la primera cámara con `ExoPlayer` + `RtspMediaSource`
4. `btnPrevFeed`/`btnNextFeed` ciclan `currentCamIndex` y llaman a `playCurrentCamera()`
5. Joystick calcula `normX`/`normY` y llama a `POST /cameras/{id}/ptz` si `camera.hasPtz == true`
6. `onPause`/`onResume` pausan y reanudan el player; `onDestroyView` lo libera

**Patrón correcto para `loadCameras()`:**
```kotlin
val response = api.getCameras()
if (response.isSuccessful) {
    cameras = response.body()?.data ?: emptyList()
    if (cameras.isNotEmpty()) { currentCamIndex = 0; playCurrentCamera(surfaceView) }
    else Toast.makeText(requireContext(), "No hay cámaras registradas", Toast.LENGTH_LONG).show()
} else {
    val msg = when (response.code()) {
        401 -> "Sesión expirada, vuelve a iniciar sesión"
        403 -> "Sin permisos para ver cámaras"
        500 -> "Error interno del servidor"
        else -> "Error ${response.code()}"
    }
    Toast.makeText(requireContext(), msg, Toast.LENGTH_LONG).show()
}
```
> ⚠️ **Nunca llamar `api.getCameras().data` directamente** — si el servidor devuelve un error HTTP (401, 500…), Gson intenta parsear el cuerpo de error como `CameraListResponse` y lanza `expected BEGIN_ARRAY`.

**TODO pendiente:** grabación real (`btnRecord`), micrófono (`btnMic`), modo noche (`btnNight`), pantalla completa (`btnFullscreen`) — pendientes de endpoints en el servidor.

---

### 4.3 CameraListFragment (`fragment_camera_list.xml`)

**Función:** Mosaico de feeds de múltiples cámaras.

**IDs de vistas:**
- `feedCam1` / `feedCam2` — FrameLayout 16:9; al tocar navega a liveView con el `camera.id` real del servidor
- `tvCamName1` / `tvCamName2` — nombre de la cámara activa (controlado por el fragment)
- `btnRemoveCam1` / `btnRemoveCam2` — cancela stream del slot y oculta el feed
- `btnPrevPage` / `btnNextPage` — paginación entre grupos de 2 cámaras (`@drawable/ic_arrow_left` / `ic_arrow_right`)
- `tvPageInfo` — página actual / total (ej: "1 / 3")
- `btnVideoList` — navega a liveView
- `btnFullscreen` — pendiente

**Flujo implementado:**
1. Carga cámaras con `Response<CameraListResponse>` al crear la vista
2. `renderPage()` muestra las 2 cámaras de la página actual y lanza sus streams MJPEG
3. Cada stream corre en `Dispatchers.IO`; detecta frames JPEG por marcadores `0xFF 0xD8`/`0xFF 0xD9`
4. El token JWT se pasa como query param `?token=...` (requerido por el endpoint de streaming)
5. `onPause` cancela todos los streams; `onResume` los relanza si hay cámaras cargadas

---

### 4.4 RecordingsFragment (`fragment_recordings.xml`)

**Función:** Lista de grabaciones del servidor.

**IDs de vistas:**
- `rvRecordings` — RecyclerView con LinearLayoutManager configurado

**Item (`item_recording.xml`):**
- `tvRecordingName` — nombre de la grabación
- `btnPlay` — reproduce → navega a playbackFragment
- `btnFavorite` — estrella (activa/inactiva)
- `ivAlert` — ícono de alerta (visibility=gone por defecto, visible si la grabación tiene evento)
- Divider en la parte inferior

**Flujo implementado:**
1. Crea `RecordingAdapter` con callbacks `onPlay` (navega a playback) y `onFavorite` (Toast pendiente)
2. Llama a `GET /api/v1/recordings` → `api.getRecordings().data` → `adapter.submitList(recordings)`
3. Navega a `playbackFragment` pasando `recordingId`

**TODO pendiente:** endpoint de favorito en el servidor, filtros/ordenamiento.

---

### 4.5 PlaybackFragment (`fragment_playback.xml`)

**Función:** Reproducción de una grabación.

**IDs de vistas:**
- `videoContainer` — FrameLayout para el player de video
- `seekBar` — barra de progreso (listener configurado, falta conectar al player)
- `btnPlayPause` — play/pausa
- `btnPrevious` — grabación anterior
- `btnNext` — grabación siguiente

**Flujo implementado:**
1. Recibe `recordingId` como argumento de navegación
2. Llama a `GET /api/v1/recordings/{id}` → `api.getRecording(id).data` → obtiene `file_url`
3. Inicializa `ExoPlayer` con `PlayerView` (controles propios desactivados — `useController = false`)
4. `seekBar` actualizado cada 1 segundo con corrutina; `onProgressChanged` hace `seekTo` en el player
5. `btnPlayPause` alterna play/pausa e intercambia el ícono
6. `btnPrevious` hace `seekTo(0)`; `btnNext` muestra Toast (sin grabación siguiente disponible)
7. `onDestroyView` libera el player

**TODO pendiente:** `btnNext` con lista de grabaciones encadenadas, endpoint de favorito.

---

### 4.6 EventConfigFragment (`fragment_event_config.xml`)

**Función:** Configurar notificaciones por tipo de evento.

**IDs de vistas:**
- `spinnerNotif1` — tipo notificación evento 1: `["Seleccionar...", "Push", "Telegram", "Ambas"]`
- `spinnerNotif2` — tipo notificación evento 2 (igual)
- `checkRecord1` — checkbox "Grabar" evento 1 (checked por defecto)
- `checkRecord2` — checkbox "Grabar" evento 2 (checked por defecto)

**TODO:** Guardar configuración en el servidor, asociar cada evento a una cámara específica.

---

### 4.7 BaseMenuFragment (abstracto)

Añade menú de toolbar (3 barras) a todos los fragmentos que lo extiendan.

**Fragmentos que lo extienden:** `LiveViewFragment`, `CameraListFragment`, `RecordingsFragment`, `EventConfigFragment`

**Ítems del menú (`menu_main.xml`):**
- `menuGrabaciones` → navega a recordingsFragment
- `menuNotificaciones` → navega a eventConfigFragment
- `menuCerrarSesion` → limpia SharedPreferences + navega a `qrScanFragment`

---

## 5. Servidor Python — NVR Backend

### 5.1 Datos técnicos

| Campo | Valor |
|---|---|
| Framework | Flask |
| Puerto | 5000 (configurable en `.env`) |
| Base URL API | `http://{ip}:5000/api/v1` |
| BD | PostgreSQL 17 (`nvr_db`) |
| Streaming | MJPEG, RTSP, HLS |
| IA | YOLOv8 (detección personas/vehículos) |
| Notificaciones | Telegram Bot API |
| Autenticación | JWT (`flask-jwt-extended`) |
| OS | Linux Mint 22.3 / Ubuntu 24.04 |

### 5.2 Estructura del backend

```
backend/app/
├── api/          # Rutas Flask (auth, cameras, recordings, system)
├── cameras/      # ONVIF discovery, camera manager, PTZ, LED, audio
├── core/         # Seguridad, executor, configuración
├── database/     # Modelos SQLAlchemy, repositorios
├── events/       # Event manager y bus de eventos
├── notifications/ # Telegram notifier
├── processing/   # IA YOLO, detección de movimiento
├── recording/    # Gestión de grabaciones
├── services/     # CameraService, AuthService, AIService, EventService
├── storage/      # StorageManager
├── streaming/    # FFmpeg worker, MJPEG streamer, HLS, frame buffer
└── main.py       # Entry point
```

### 5.3 Endpoints del servidor (implementados)

#### Autenticación
| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/v1/auth/login` | Login, devuelve JWT token |
| POST | `/api/v1/auth/refresh` | Renovar token |
| POST | `/api/v1/auth/logout` | Cerrar sesión |

#### Cámaras
| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/cameras` | Listar cámaras registradas |
| GET | `/api/v1/cameras/{id}` | Detalle de una cámara |
| POST | `/api/v1/cameras` | Agregar cámara |
| PUT | `/api/v1/cameras/{id}` | Actualizar cámara |
| DELETE | `/api/v1/cameras/{id}` | Eliminar cámara |
| POST | `/api/v1/cameras/probe` | Detectar cámara por IP (ONVIF o RTSP) |
| POST | `/api/v1/cameras/{id}/ptz` | Comando PTZ |

#### Streaming
| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/cameras/{id}/stream` | URL del stream MJPEG |
| GET | `/api/v1/cameras/{id}/hls` | Stream HLS |

#### Grabaciones
| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/recordings` | Listar grabaciones |
| GET | `/api/v1/recordings/{id}` | Detalle de grabación |
| DELETE | `/api/v1/recordings/{id}` | Eliminar grabación |

#### Sistema
| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/system/status` | Estado general del sistema |

### 5.4 Formato de respuesta del servidor (envuelto)

Todos los endpoints de lista y detalle devuelven un objeto con `success` y `data`, **no un array directo**:

```json
// GET /api/v1/cameras
{ "success": true, "data": [ { ...cámara... } ] }

// GET /api/v1/recordings
{ "success": true, "data": [ { ...grabación... } ] }

// GET /api/v1/recordings/{id}
{ "success": true, "data": { ...grabación... } }
```

Esto requiere los wrappers definidos en `ApiModels.kt` (ver sección 11.2).

### 5.5 Tablas en PostgreSQL

`cameras`, `users`, `events`, `recordings`, `audit_logs`, `link_tokens`, `mobile_devices`, `notification_channels`, `notification_days`, `notification_logs`, `notification_preferences`, `system_config`, `telegram_verification_codes`, `user_camera_permissions`, `user_telegram_chats`

### 5.6 Cámara configurada

| Campo | Valor |
|---|---|
| Nombre | Foco Camera H265 |
| IP | `192.168.101.108` (**dinámica** — puede cambiar) |
| Puerto RTSP | 554 |
| URL RTSP | `rtsp://fync:wp6wet@192.168.101.108:554/user=fync&password=wp6wet&channel=1&stream=0.sdp` |
| Usuario | fync |
| Contraseña | wp6wet |
| Codec | H.265/HEVC |
| Resolución | 1920×1080 @ 12 FPS |
| Audio | PCM A-law G.711, 8000Hz mono |
| PTZ | ❌ No soportado (protocolo propietario iCSee) |
| ONVIF | ❌ No soportado |

> ⚠️ La IP de la cámara puede cambiar al reconectarse al WiFi. Redescubrir con `nmap -sn 192.168.101.0/24` y buscar el host con puertos 80, 554 y 34567 abiertos.

### 5.7 Autenticación JWT

El servidor usa `flask-jwt-extended`. La app Android debe:
1. Hacer `POST /api/v1/auth/login` con `{ "username": "...", "password": "..." }`
2. Guardar el token JWT recibido
3. Incluirlo en todas las peticiones: `Authorization: Bearer {token}`
4. Renovar con `POST /api/v1/auth/refresh` antes de que expire

---

## 6. Contrato de integración Android ↔ Servidor

### 6.1 Flujo completo de conexión

```
QrScanFragment
    │
    ├── [Manual/QR] → POST /api/v1/auth/login
    │                  body: { username, password }
    │                  resp: { access_token, refresh_token }
    │                  → guardar token en SharedPreferences (.commit() síncrono)
    │
    └──► liveViewFragment
              │
              ├── GET /api/v1/cameras → .data → lista de cámaras
              │   resp: { success: true, data: [{ id, name, rtsp_url, has_ptz, ... }] }
              │
              └── ExoPlayer.play(rtsp_url de la cámara activa)
```

### 6.2 Formato de login

**Request:**
```json
POST /api/v1/auth/login
{
  "username": "nomelo",
  "password": "TU_PASSWORD"
}
```

**Response:**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ..."
}
```

> **Importante:** Los campos `serverUser` y `serverPassword` guardados en SharedPreferences son las credenciales para este endpoint, **no** credenciales ONVIF directas.

### 6.3 Formato de respuesta — cámara

```json
{
  "id": 1,
  "name": "Foco Camera H265",
  "ip_address": "192.168.101.108",
  "rtsp_url": "rtsp://fync:wp6wet@192.168.101.108:554/user=fync&password=wp6wet&channel=1&stream=0.sdp",
  "has_ptz": false,
  "has_audio": true,
  "connection_type": "rtsp",
  "resolution_width": 1920,
  "resolution_height": 1080,
  "fps": 12
}
```

### 6.4 Comandos PTZ (cuando la cámara lo soporte)

**Request:**
```json
POST /api/v1/cameras/{id}/ptz
Authorization: Bearer {token}
{
  "x": 0.5,     // normX del joystick, -1.0 a 1.0
  "y": -0.3,    // normY del joystick, -1.0 a 1.0
  "zoom": 0.0   // zoom, -1.0 a 1.0 (0 = sin cambio)
}
```

### 6.5 Grabaciones

**Listar:**
```
GET /api/v1/recordings
Authorization: Bearer {token}
```
```json
{
  "success": true,
  "data": [
    {
      "id": "rec_001",
      "camera_id": 1,
      "filename": "2026-05-25_14-30-00.mp4",
      "started_at": "2026-05-25T14:30:00",
      "duration_seconds": 120,
      "has_alert": false,
      "is_favorite": false,
      "file_url": "http://..."
    }
  ]
}
```

**Obtener detalle para reproducir:**
```
GET /api/v1/recordings/{id}
```
Devuelve `{ "success": true, "data": { ..., "file_url": "..." } }`. Extraer `data.file_url` y pasarla a ExoPlayer.

### 6.6 Headers obligatorios en todas las peticiones autenticadas

```
Authorization: Bearer {access_token}
Content-Type: application/json
```

---

## 7. TODOs pendientes en Android

| Prioridad | Tarea | Fragmento |
|---|---|---|
| Alta | Escaneo QR real con CameraX + ML Kit | `QrScanFragment` |
| ~~Alta~~ | ~~Stream en `CameraListFragment`~~ — ✅ implementado vía MJPEG con paginación | `CameraListFragment` |
| Media | Grabación real — endpoint en servidor | `LiveViewFragment` |
| Media | Micrófono — endpoint en servidor | `LiveViewFragment` |
| Media | Modo noche — endpoint en servidor | `LiveViewFragment` |
| Media | Pantalla completa | `LiveViewFragment`, `CameraListFragment` |
| Media | Endpoint de favorito en servidor | `RecordingsFragment` |
| Baja | `btnNext` en playback con lista encadenada | `PlaybackFragment` |
| Baja | Renovación automática de JWT expirado | `RetrofitClient` |
| Baja | EventConfig — guardar config en servidor | `EventConfigFragment` |

---

## 8. Variables de entorno del servidor (.env)

```env
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=nvr_user
POSTGRES_PASSWORD=nvr_pass
POSTGRES_DB=nvr_db
DATABASE_URL=postgresql://nvr_user:nvr_pass@localhost:5432/nvr_db
SECRET_KEY=change-this-in-production
JWT_SECRET_KEY=change-this-jwt-secret
FFMPEG_FPS=12
MAX_STORAGE_GB=50.0
LOG_LEVEL=INFO
SERVER_HOST=0.0.0.0
SERVER_PORT=5000
```

## 9. Cómo arrancar el servidor

```bash
cd ~/Documents/sistema-videovigilancia
source .venv/bin/activate
LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu python3 backend/app/main.py
```

---

## 10. Notas importantes para la IA

- La app usa `serverPort` = `"5000"` para conectarse al backend Flask.
- El `serverUser`/`serverPassword` de SharedPreferences son credenciales del **backend Flask** (tabla `users`), no credenciales ONVIF de la cámara.
- La cámara actual **no tiene PTZ** (protocolo propietario iCSee). El joystick llama al endpoint pero `camera.hasPtz == false` lo cortocircuita antes de la llamada.
- No existe ViewBinding — siempre usar `findViewById` con los IDs documentados en la sección 4.
- No usar Jetpack Compose.
- Usar `viewLifecycleOwner.lifecycleScope.launch` (no `GlobalScope`) para todas las llamadas de red en fragmentos.
- `RetrofitClient` es un `object` singleton. Llamar a `RetrofitClient.create(baseUrl, context)` cada vez que se necesita una instancia.
- **Todos los endpoints devuelven `{ "success": true, "data": ... }` — siempre extraer `.data` del wrapper**, nunca usar la respuesta directamente como lista o modelo.
- **`saveToken()` usa `.commit()` (síncrono)** para garantizar que el token esté en disco antes de navegar al siguiente fragmento. No cambiar a `.apply()`.
- **`RetrofitClient.create()` no pisa el token en memoria si ya existe** — solo lee SharedPreferences en arranque en frío o tras logout.

---

## 11. Capa de red — archivos implementados

### 11.1 Estructura de carpetas

```
app/src/main/java/com/ipn/mx/onvif/
├── model/
│   └── ApiModels.kt          ← data classes de request/response + wrappers
├── network/
│   ├── ApiService.kt         ← interfaz Retrofit con todos los endpoints
│   └── RetrofitClient.kt     ← singleton OkHttp + JWT interceptor
└── ui/
    ├── QrScanFragment.kt     ← login real
    ├── LiveViewFragment.kt   ← ExoPlayer + PTZ + .data en getCameras()
    ├── RecordingsFragment.kt ← carga del servidor + .data en getRecordings()
    ├── RecordingAdapter.kt   ← adapter RecyclerView
    └── PlaybackFragment.kt   ← ExoPlayer + seekBar + .data en getRecording()
```

### 11.2 ApiModels.kt

Data classes Kotlin mapeadas con `@SerializedName` a los campos JSON del servidor:

| Clase | Uso |
|---|---|
| `LoginRequest` | Body de `POST /auth/login` |
| `LoginResponse` | Respuesta con `access_token` y `refresh_token` |
| `CameraResponse` | Ítem de `GET /cameras` |
| `PtzRequest` | Body de `POST /cameras/{id}/ptz` con `x`, `y`, `zoom` |
| `RecordingResponse` | Ítem de `GET /recordings` con `id`, `filename`, `has_alert`, `is_favorite`, `file_url` |
| `CameraListResponse` | Wrapper `{ success, data: List<CameraResponse> }` |
| `RecordingListResponse` | Wrapper `{ success, data: List<RecordingResponse> }` |
| `RecordingDetailResponse` | Wrapper `{ success, data: RecordingResponse }` |

### 11.3 ApiService.kt

Interfaz Retrofit con endpoints `suspend`. Los tipos de retorno usan los wrappers:

```
POST  auth/login              → LoginResponse
POST  auth/refresh
POST  auth/logout
GET   cameras/                → Response<CameraListResponse>   (slash final requerido; Response<> para capturar errores HTTP antes de parsear)
GET   cameras/{id}
POST  cameras/{id}/ptz
GET   recordings              → RecordingListResponse
GET   recordings/{id}         → RecordingDetailResponse
DELETE recordings/{id}
```

> ⚠️ El endpoint de cámaras tiene slash final: `cameras/`. Flask redirige sin slash y OkHttp no sigue la redirección correctamente.

### 11.4 RetrofitClient.kt

- `object` singleton
- `create(baseUrl, context)` — construye `OkHttpClient` con interceptor JWT y `HttpLoggingInterceptor` (nivel BODY en debug). **Solo lee `accessToken` de SharedPreferences si `accessToken == null`** (evita race condition con login reciente).
- `saveToken(context, accessToken, refreshToken)` — guarda en memoria y en `auth_prefs` usando **`.commit()` (síncrono)**, garantizando que el token esté persistido antes de que `LiveViewFragment` llame a `create()`.
- `clearToken(context)` — limpia token de memoria y prefs con `.apply()` (logout no tiene urgencia de timing)
- `buildBaseUrl(context)` — reconstruye `"http://ip:port"` desde `auth_prefs`; devuelve `null` si no hay IP guardada

**Dependencia requerida en `build.gradle.kts`:**
```kotlin
implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
```

### 11.5 RecordingAdapter.kt

`RecyclerView.Adapter` para `item_recording.xml`:
- Muestra `filename` en `tvRecordingName`
- `ivAlert` visible solo si `rec.hasAlert == true`
- `btnFavorite` muestra `ic_star_active` o `ic_star_inactive` según `rec.isFavorite`
- Callbacks `onPlay` y `onFavorite` inyectados en el constructor
- `submitList(newItems)` reemplaza la lista completa y llama `notifyDataSetChanged()`

---

## 12. Historial de cambios

### 2026-05-25 — Fix "expected BEGIN_ARRAY" en getCameras()

**Causa raíz:** `ApiService.getCameras()` retornaba `CameraListResponse` directamente. Cuando el servidor devuelve un error HTTP (401, 500…), Gson intenta parsear el cuerpo de error `{"success": false, "error": "..."}` como `CameraListResponse` y falla con `expected BEGIN_ARRAY` porque `data` no es una lista.

**Archivos modificados:**
- ✅ `network/ApiService.kt` — tipo de retorno de `getCameras()` cambiado de `CameraListResponse` a `Response<CameraListResponse>`
- ✅ `ui/LiveViewFragment.kt` — `loadCameras()` ahora verifica `response.isSuccessful` y maneja códigos HTTP (401, 403, 500) con mensajes descriptivos antes de acceder a `body()?.data`

**Regla general aplicada:** Cualquier endpoint donde un error HTTP pueda llegar con un cuerpo JSON distinto al tipo esperado debe usar `Response<T>` en Retrofit para capturar el error antes de que Gson intente parsear.

---

### 2026-05-25 — Integración inicial Android ↔ Servidor

**Archivos nuevos:**
- ✅ `model/ApiModels.kt` — data classes para login, cámaras, PTZ y grabaciones
- ✅ `network/ApiService.kt` — interfaz Retrofit con 9 endpoints
- ✅ `network/RetrofitClient.kt` — singleton con JWT interceptor, logging y helpers de sesión
- ✅ `ui/RecordingAdapter.kt` — adapter RecyclerView para lista de grabaciones
- ✅ `res/xml/network_security_config.xml` — permite tráfico HTTP en LAN (Android 9+)

**Archivos reemplazados/modificados:**
- ✅ `AndroidManifest.xml` — añadidos `networkSecurityConfig` y `usesCleartextTraffic`
- ✅ `ui/QrScanFragment.kt` — login real (`POST /auth/login`), guarda JWT, maneja errores HTTP
- ✅ `ui/LiveViewFragment.kt` — carga cámaras (`GET /cameras`) con `.data`; reproduce RTSP con ExoPlayer; PTZ con `POST /cameras/{id}/ptz`
- ✅ `ui/RecordingsFragment.kt` — carga grabaciones reales (`GET /recordings`) con `.data`; usa `RecordingAdapter`
- ✅ `ui/PlaybackFragment.kt` — obtiene URL (`GET /recordings/{id}`) con `.data`; reproduce con ExoPlayer + seekBar
- ✅ `model/ApiModels.kt` — añadidos wrappers `CameraListResponse`, `RecordingListResponse`, `RecordingDetailResponse`
- ✅ `network/ApiService.kt` — tipos de retorno actualizados a wrappers; slash añadido en `cameras/`
- ✅ `network/RetrofitClient.kt` — `saveToken` usa `.commit()` síncrono; `create()` no pisa token en memoria si ya existe

**Cambios en `build.gradle.kts`:**
- ✅ Añadida dependencia `okhttp3:logging-interceptor:4.12.0`

**Pendiente tras esta sesión:**
- ❌ Escaneo QR real (CameraX + ML Kit)
- ❌ Stream en `CameraListFragment`
- ❌ Endpoints de grabación, micrófono y modo noche en el servidor
- ❌ Renovación automática de JWT expirado

### 2026-05-25 — CameraListFragment: streams MJPEG paginados

**Función implementada:** El fragment ahora carga todas las cámaras del servidor y las muestra de 2 en 2 con paginación. Cada feed muestra un stream MJPEG en vivo dentro de un `ImageView`.

**Archivos modificados:**
- ✅ `ui/CameraListFragment.kt` — reescrito completamente
- ✅ `res/layout/fragment_camera_list.xml` — añadidos IDs a los `TextView` de nombre y barra de paginación

**Cambios en `fragment_camera_list.xml`:**
- `tvCamName1` / `tvCamName2` — `TextView` de nombre ahora tienen ID y texto controlado por el fragment (antes hardcodeado con `@string/cam_1_label`)
- `btnPrevPage` / `btnNextPage` — nuevos `ImageButton` con `@drawable/ic_arrow_left` y `@drawable/ic_arrow_right`
- `tvPageInfo` — `TextView` central que muestra "página actual / total" (ej: "1 / 3")
- Los tres elementos de paginación están anclados con constraints debajo de `feedCam2` y sobre la barra inferior

**Lógica de `CameraListFragment.kt`:**
- Carga cámaras con `Response<CameraListResponse>` (mismo patrón que `LiveViewFragment`)
- `pageIndex` rastrea la página actual; `renderPage()` cancela streams anteriores y lanza los de la página nueva
- `btnPrevPage`/`btnNextPage` actualizan `pageIndex` y llaman `renderPage()`; se deshabilitan en los extremos
- Al tocar un feed navega a `liveViewFragment` pasando el `camera.id` real (no hardcodeado "cam1"/"cam2")
- `btnRemoveCam1`/`btnRemoveCam2` cancelan la corrutina del slot y ocultan el feed

**Stream MJPEG (sin ExoPlayer):**
- Se usa MJPEG en lugar de RTSP porque es más liviano para múltiples streams simultáneos
- Endpoint: `GET /api/v1/cameras/{id}/stream?token={jwt}` — el token va como **query param**, no en header (requerido por el servidor para conexiones streaming)
- `OkHttpClient` dedicado con `readTimeout = 0` (streaming infinito) y `connectTimeout = 10s`
- Cada slot tiene su propia corrutina en `Dispatchers.IO` que lee el `multipart/x-mixed-replace` byte a byte, detecta marcadores JPEG (`0xFF 0xD8` inicio, `0xFF 0xD9` fin) y actualiza el `ImageView` en el hilo principal
- Las corrutinas se cancelan en `onPause` y `onDestroyView`; se reanudan en `onResume`

**Método nuevo requerido en `RetrofitClient.kt`:**
```kotlin
fun getAccessToken(context: Context): String? {
    if (accessToken != null) return accessToken
    val prefs = context.getSharedPreferences("auth_prefs", Context.MODE_PRIVATE)
    return prefs.getString("jwt_access_token", null)
}
```

**Estado de TODOs actualizado:**
- ✅ Stream en `CameraListFragment` — implementado vía MJPEG
- ✅ Paginación de cámaras (2 por página)
- ❌ Pantalla completa — pendiente
- ❌ Desconexión real de cámara (solo oculta el feed localmente)
