# 📦 Paquete de Entrega — Sistema NVR/VMS de Videovigilancia LAN

Proyecto de titulación · Sistema de videovigilancia en red local (NVR/VMS) para
hasta 4 cámaras IP, con captura FFmpeg, detección de objetos YOLOv8, detección
de movimiento, grabación continua y por eventos, alertas por Telegram, cliente
de escritorio (PySide6) y cliente móvil Android (CamLink).

> Este paquete **no duplica** el código ni la documentación: contiene los
> artefactos propios de la entrega (respaldo de base de datos, guía de
> instalación, informe de auditoría) y **índices** que apuntan a cada parte del
> repositorio. Así hay un único punto de verdad.

---

## 🗂️ Contenido de `/Entrega`

| Carpeta | Qué encontrarás |
|---------|-----------------|
| [`CodigoFuente/`](CodigoFuente/README.md) | Dónde está el código (backend, escritorio, móvil) y su estructura. |
| [`BaseDatos/`](BaseDatos/RESTAURACION.md) | Respaldo PostgreSQL (`backup_nvr_db.sql`), esquema, script de creación y guía de restauración. |
| [`Documentacion/`](Documentacion/README.md) | Índice de la documentación técnica y de tesis. |
| [`Instalacion/`](Instalacion/INSTALACION.md) | Guía paso a paso de instalación y ejecución. |
| [`Manuales/`](Manuales/README.md) | Manual de usuario integral. |
| [`Scripts/`](Scripts/README.md) | Scripts de empaquetado, documentación y base de datos. |
| [`INFORME_AUDITORIA.md`](INFORME_AUDITORIA.md) | Informe de auditoría y preparación del repositorio. |

---

## 🚀 Inicio rápido (demostración)

1. **Requisitos:** Python 3.13, PostgreSQL 17, FFmpeg en `PATH`. (`go2rtc.exe`
   ya viene en el repo.)
2. **Entorno + dependencias:**
   ```powershell
   python -m venv env; .\env\Scripts\activate
   pip install torch==2.6.0+cpu torchvision==0.21.0+cpu --index-url https://download.pytorch.org/whl/cpu
   pip install -r requirements.txt
   ```
3. **Configuración:** `copy .env.example .env` y edita los secretos
   (`SECRET_KEY`, `JWT_SECRET_KEY`, `POSTGRES_*`).
4. **Base de datos:** sigue [`BaseDatos/RESTAURACION.md`](BaseDatos/RESTAURACION.md).
5. **Ejecutar:**
   ```powershell
   python backend/app/main.py          # API en http://localhost:5000
   python desktop_app/src/main.py      # cliente de escritorio
   ```
6. **Verificar:** `GET http://localhost:5000/api/v1/health` → 200.

La guía completa está en [`Instalacion/INSTALACION.md`](Instalacion/INSTALACION.md).

---

## 🧱 Arquitectura (resumen)

- **Backend Flask** (proceso único por diseño) — API REST + WebSocket, JWT,
  SQLAlchemy 2.0 sobre PostgreSQL.
- **Pipeline por cámara:** `RTSP → FFmpegWorker → CircularFrameBuffer →
  FrameDistributor →` { IA (YOLOv8 con *gating* por movimiento) + grabación }.
- **Directo (vista en vivo):** servido por **go2rtc** (WebRTC/RTSP/HLS) directo
  al cliente; el pipeline interno solo alimenta IA y grabación.
- **Notificaciones:** bus de eventos → Telegram + WebSocket (LAN, sin internet).
- **Clientes:** escritorio (PySide6) y móvil Android (CamLink, Kotlin).

Detalle técnico en [`CLAUDE.md`](../CLAUDE.md) y
[`Documentacion/`](Documentacion/README.md).

---

## 📌 Datos clave

| Aspecto | Valor |
|---------|-------|
| Lenguajes | Python 3.13 (backend + escritorio), Kotlin (Android) |
| Base de datos | PostgreSQL 17 (`nvr_db`) |
| Streaming en vivo | go2rtc (WebRTC/RTSP/HLS) |
| IA | YOLOv8n (Ultralytics) |
| Idioma del proyecto | Español (código, comentarios y commits) |
