# Guía de Instalación y Ejecución — Sistema NVR/VMS

Guía paso a paso para instalar, configurar y ejecutar el sistema para la
**demostración** y la **evaluación**.

---

## 1. Requisitos previos

| Componente   | Versión / nota |
|--------------|----------------|
| **Python**   | 3.13 (obligatorio) |
| **PostgreSQL** | 17 (la base de datos activa) |
| **FFmpeg**   | Debe estar en el `PATH` (el backend hace `shutil.which("ffmpeg")` al arrancar) |
| **go2rtc**   | Incluido en el repo: `./go2rtc.exe` (capa de directo WebRTC/RTSP/HLS) |
| Hardware     | CPU 4 núcleos, 4 GB RAM mínimo, LAN 100 Mbps. GPU opcional para YOLO. |

> FFmpeg: descárgalo de <https://ffmpeg.org/download.html> (Windows) y añade su
> carpeta `bin` al `PATH`.

---

## 2. Crear el entorno de Python

```powershell
python -m venv env
.\env\Scripts\activate     # Windows
# source env/bin/activate  # Linux/Mac
```

## 3. Instalar dependencias

**PyTorch debe instalarse PRIMERO** con su índice especial (CPU):

```powershell
pip install torch==2.6.0+cpu torchvision==0.21.0+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

> `requirements.txt` = entorno completo (incluye PySide6 para la app de
> escritorio). `requirements-backend.txt` = solo backend headless (+ `psycopg2`,
> `alembic`); útil si separas backend y cliente.

Si faltan `torch`/`ultralytics`, `POST /api/v1/ai/<id>/activate` devuelve
**503 + `AI_DEPENDENCIES_MISSING`** con el comando `pip` exacto para arreglarlo.

## 4. Configurar variables de entorno

```powershell
copy .env.example .env      # Windows   (en la raíz del repositorio)
# cp .env.example .env      # Linux/Mac
```

Edita `.env` y define como mínimo:
- `SECRET_KEY` y `JWT_SECRET_KEY` — genera valores únicos:
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- `POSTGRES_*` — credenciales de tu PostgreSQL.
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — opcionales (alertas Telegram).

La plantilla completa y documentada está en
[`.env.example`](../../.env.example) (raíz del repositorio).

## 5. Preparar la base de datos

Sigue [`../BaseDatos/RESTAURACION.md`](../BaseDatos/RESTAURACION.md):

```powershell
$PG = "C:\Program Files\PostgreSQL\17\bin"
& "$PG\psql" -U postgres -f ..\BaseDatos\crear_bd.sql
$env:PGPASSWORD = "tu-password"
& "$PG\psql" -U nvr_user -h localhost -d nvr_db -f ..\BaseDatos\backup_nvr_db.sql
```

> Alternativa: con la base **vacía**, el backend ejecuta `create_all()` al
> arrancar y crea las tablas solo. Restaurar el respaldo solo es necesario si
> quieres los **datos de demostración** (usuarios, cámaras, eventos).

---

## 6. Ejecutar el sistema

### Backend (API REST + WebSocket, puerto 5000)
```powershell
python backend/app/main.py
```
Verifica que arrancó: `GET http://localhost:5000/api/v1/health` → 200.

### Aplicación de escritorio (PySide6)
```powershell
python desktop_app/src/main.py
```

### Aplicación móvil (Android — CamLink)
El cliente Android está en [`CamLink_app/`](../../CamLink_app/). Ábrelo en
**Android Studio**, configura la IP del backend en la LAN y compila/instala el
APK en un dispositivo de la misma red. (El directo usa WebRTC y las
notificaciones llegan por WebSocket en la LAN, sin internet.)

---

## 7. Notas de la arquitectura (importante para la demo)

- El **backend es un único proceso** por diseño (mantiene singletons con hilos,
  subprocesos FFmpeg y buffers en memoria). **No** lo pongas detrás de un WSGI
  multi-worker (Gunicorn/uWSGI).
- El **directo** (vista en vivo) lo sirve **go2rtc** directamente al cliente
  (WebRTC/RTSP/HLS); el pipeline interno de FFmpeg solo alimenta IA + grabación.
- Solo **una cámara** corre YOLO a la vez (`AI_CAMERA_ID`).
- Empaquetado a `.exe` de Windows: ver [`../../packaging/README.md`](../../packaging/README.md).

---

## 8. Verificación funcional sugerida (checklist)

1. `GET /api/v1/health` responde 200 y lista los *blueprints*.
2. Login en la app de escritorio (autenticación JWT).
3. Las cámaras de demostración aparecen en la lista.
4. La vista en vivo (WebRTC vía go2rtc) reproduce.
5. Reproducción de grabaciones (hay 40 de demo en la BD).
6. Activar IA en una cámara genera eventos/notificaciones.
