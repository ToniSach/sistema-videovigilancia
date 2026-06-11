# Empaquetado de NVR-VMS a `.exe` (Windows)

Genera un **producto de escritorio autocontenido**: una carpeta con
`NVR-VMS.exe` que el usuario final ejecuta con doble clic. No requiere instalar
Python, PostgreSQL, FFmpeg ni nada: todo viaja dentro.

## Qué incluye el producto

```
NVR-VMS\                     ← carpeta a entregar
├─ NVR-VMS.exe               ← lo que ejecuta el usuario (GUI + launcher)
├─ _internal\               ← runtime de PySide6 + VLC (libvlc, plugins)
└─ backend\
   ├─ NVR-Backend.exe        ← servidor Flask (lo arranca el launcher solo)
   └─ _internal\             ← torch, ultralytics, ffmpeg/ffprobe/go2rtc,
                                PostgreSQL portátil, modelo YOLO, WSDL ONVIF…
```

Al ejecutar `NVR-VMS.exe`:
1. Arranca `NVR-Backend.exe`, que **inicializa un PostgreSQL embebido** (port
   5433) en `%LOCALAPPDATA%\NVR-VMS\pgdata`, carga IA, cámaras y go2rtc.
2. Se muestra una **pantalla de carga** mientras el backend inicializa.
3. La interfaz se abre **solo cuando el backend responde** `GET /api/v1/health`.

Todos los datos del usuario (base de datos, grabaciones, secretos, logs) viven
en `%LOCALAPPDATA%\NVR-VMS\`. Los secretos (`SECRET_KEY`, `JWT_SECRET_KEY`, la
contraseña de la BD) se **generan únicos en el primer arranque** y se guardan
en `secrets.env`; no están hardcodeados en el `.exe`.

## Requisitos de la máquina de COMPILACIÓN

- El repo con su entorno virtual `env\` (trae torch, PySide6, ultralytics, …).
- Estos programas instalados (de ellos se copian los binarios al bundle):
  - **PostgreSQL 15/16/17** (`C:\Program Files\PostgreSQL\NN`)
  - **VLC** (`C:\Program Files\VideoLAN\VLC`)
  - **FFmpeg** (en PATH, o vía Chocolatey)
  - `go2rtc.exe` (ya está en la raíz del repo)
  - `yolov8n.pt` (ya está en la raíz del repo)

> El usuario FINAL no necesita nada de lo anterior: solo hace falta en la
> máquina donde compilas.

## Compilar

```powershell
packaging\build.bat
```

Pasos que ejecuta (≈15-30 min la primera vez):
1. Instala PyInstaller si falta.
2. `stage_vendor.py` copia los binarios a `C:\NVR-VMS-build\vendor`.
3. Compila `NVR-Backend.exe` (spec `nvr_backend.spec`).
4. Compila `NVR-VMS.exe` (spec `nvr_desktop.spec`).
5. Mete el backend dentro de la carpeta del producto.

Resultado: **`C:\NVR-VMS-build\dist\NVR-VMS\`** (comprime esa carpeta y
distribúyela). Cambia el destino con `set BUILD_ROOT=D:\otra\ruta` antes de
`build.bat`.

### Apuntar a rutas de origen distintas
`stage_vendor.py` acepta overrides por entorno: `PG_DIR`, `VLC_DIR`,
`FFMPEG_EXE`, `FFPROBE_EXE`, `GO2RTC_EXE`, `YOLO_PT`, `VENDOR_DIR`.

## Notas

- **Solo Windows.** Los binarios incluidos son `.exe`/`.dll` de Windows.
- El backend empaquetado abre puertos de firewall (1984/8554/8555 de go2rtc) con
  `netsh` la primera vez; puede pedir permiso de administrador.
- PostgreSQL embebido usa el puerto **5433** para no chocar con un PostgreSQL
  que el usuario pudiera tener en el 5432. Cambia con `NVR_PG_PORT`.
- Para depurar el arranque del backend en el producto, mira
  `%LOCALAPPDATA%\NVR-VMS\logs\`.
