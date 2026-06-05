# Despliegue con Docker (Windows y Linux)

Levanta el **backend** del NVR (API + go2rtc + IA + grabación) y su base de
datos PostgreSQL con un solo comando. El cliente de escritorio y la app móvil
se instalan aparte y apuntan a la IP de este servidor.

> **Requisito:** Docker Desktop (Windows) o Docker Engine (Linux). En Windows,
> Docker Desktop ejecuta contenedores **Linux** sobre WSL2 — por eso la imagen
> incluye el binario `go2rtc` de Linux y todo funciona igual que en Linux.
>
> La red va en **modo bridge con puertos publicados** (no host-networking), así
> que es idéntico en Windows y Linux. En la LAN no hace falta internet salvo
> para: descargar imágenes/binarios la primera vez, el modelo YOLO inicial y
> Telegram.

## 1. Configurar

Edita **`.env.docker`** y cambia los secretos:

- `POSTGRES_PASSWORD`, `SECRET_KEY`, `JWT_SECRET_KEY` (genera claves robustas:
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`).
- `HOST_LAN_IP` → la IP LAN de tu PC (ej. `192.168.1.50`). **Solo** necesaria
  para WebRTC; HLS y RTSP funcionan sin ella. En Windows la ves con `ipconfig`.

## 2. Construir y arrancar

```powershell
docker compose up -d --build
```

La primera vez tarda (descarga PyTorch CPU, Ultralytics y el binario go2rtc).
La imagen ya trae **FFmpeg** y **go2rtc**: no instalas nada a mano.

## 3. Comprobar

```powershell
docker compose ps                 # ambos servicios "running"/"healthy"
docker compose logs -f backend    # logs en vivo
```
Abre en el navegador del PC: `http://localhost:5000/api/v1/health`

## 4. Conectar los clientes

Desde otro equipo de la LAN, apunta el cliente de escritorio / app móvil a la
IP del servidor (la de `HOST_LAN_IP`):

| Servicio             | Puerto | Uso                                      |
|----------------------|--------|------------------------------------------|
| API REST + WebSocket | 5000   | login, cámaras, eventos, notificaciones  |
| go2rtc (HLS/panel)   | 1984   | directo móvil (HLS) y panel go2rtc       |
| RTSP (restream)      | 8554   | directo escritorio (VLC)                 |
| WebRTC               | 8555   | directo de baja latencia (opcional)      |

Ejemplo: si el PC es `192.168.1.50`, el escritorio usa `http://192.168.1.50:5000`.

> **Firewall de Windows:** la primera vez, Windows puede pedir permiso para que
> Docker acepte conexiones entrantes en esos puertos. Acéptalo (redes privadas).

## 5. Primer uso

La base de datos arranca **vacía**: la primera vez que abras el cliente de
escritorio te pedirá **crear el usuario administrador**. Desde ahí creas más
usuarios o vinculas el móvil con el código QR/numérico.

## 6. Datos persistentes

- `./data/recordings` → grabaciones y snapshots (en la carpeta del proyecto).
- `./data/models` → modelos YOLO (se descargan solos la 1ª vez que activas la IA;
  sin internet, copia ahí `yolov8n.pt`).
- Volumen `pgdata` → base de datos PostgreSQL.

## Operación

```powershell
docker compose down          # parar
docker compose up -d         # arrancar (sin reconstruir)
docker compose up -d --build # reconstruir tras cambiar código
docker compose logs -f       # ver logs
```

## Notas

- **Un solo proceso backend:** el diseño usa singletons con estado en memoria
  (cámaras, buffers, go2rtc). No escales `backend` a varias réplicas.
- **Notificaciones:** el móvil recibe alertas por **WebSocket** en la LAN (sin
  Firebase/FCM, que fue eliminado). Telegram es opcional y requiere internet.
- **Las notificaciones solo se generan para la cámara con IA activa**; actívala
  desde el cliente de escritorio (panel de la cámara → «Activar IA»).
- **ARM (Raspberry Pi / mini-PC):** reconstruye con
  `docker compose build --build-arg GO2RTC_ARCH=arm64`.
