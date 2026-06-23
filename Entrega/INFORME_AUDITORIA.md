# Informe de Auditoría y Preparación para Entrega

**Proyecto:** Sistema NVR/VMS de Videovigilancia LAN
**Fecha:** 2026-06-22
**Rama:** `Pruebas-debugging`
**Alcance:** Auditoría integral, limpieza, securización de configuración, respaldo
de base de datos y organización del paquete de entrega académica.
**Restricción aplicada:** No se modificó ninguna funcionalidad. Por decisión del
responsable, **no se borró ni movió nada del disco** — la limpieza se aplicó como
*exclusión* (paquete de entrega + saneo de `.gitignore`), dejando el repositorio
de trabajo intacto.

---

## 1. Resumen ejecutivo

Se inventarió todo el repositorio, se identificaron y documentaron los artefactos
no necesarios para la evaluación, se securizó la configuración (plantilla sin
secretos), se generó un **respaldo real de la base de datos PostgreSQL** con datos
de demostración, y se construyó un paquete de entrega profesional en `/Entrega`
que **referencia** (no duplica) el código y la documentación canónicos.

| Métrica | Valor |
|---------|------:|
| Código fuente backend (Python) | 99 archivos |
| Código fuente escritorio (Python) | 46 archivos |
| Código fuente móvil (Kotlin) | 24 archivos |
| Tablas en BD con datos de demo | 16 |
| Secretos reales detectados (a proteger) | 5 |
| Artefactos no esenciales identificados | ~2.8 GB |
| Funcionalidad modificada | Ninguna |

---

## 2. Archivos ELIMINADOS

**Ninguno.** Por decisión explícita («solo excluir, no tocar»), no se eliminó
ningún archivo del disco. La limpieza se materializó como exclusión del paquete de
entrega y mediante reglas de `.gitignore`. La sección 6 lista lo que *puede*
eliminarse de forma segura si más adelante se desea reducir tamaño.

---

## 3. Archivos CREADOS (paquete de entrega)

| Archivo | Propósito |
|---------|-----------|
| `Entrega/README.md` | Índice maestro del paquete de entrega. |
| `Entrega/INFORME_AUDITORIA.md` | Este informe. |
| `Entrega/CodigoFuente/README.md` | Índice que apunta al código (sin duplicarlo). |
| `Entrega/Documentacion/README.md` | Índice de documentación técnica y de tesis. |
| `Entrega/Instalacion/INSTALACION.md` | Guía paso a paso de instalación y ejecución. |
| `Entrega/Manuales/README.md` | Índice del manual de usuario. |
| `Entrega/Scripts/README.md` | Índice de scripts de empaquetado/documentación/BD. |
| `Entrega/BaseDatos/backup_nvr_db.sql` | Respaldo PostgreSQL **completo** (esquema + datos demo). |
| `Entrega/BaseDatos/esquema_nvr_db.sql` | Respaldo **solo esquema**. |
| `Entrega/BaseDatos/crear_bd.sql` | Script de creación de rol y base de datos. |
| `Entrega/BaseDatos/RESTAURACION.md` | Guía de restauración de la BD. |
| `.env.example` (raíz) | Plantilla de configuración documentada, **sin secretos**. |

## 4. Archivos MODIFICADOS

| Archivo | Cambio |
|---------|--------|
| `.gitignore` | Se des-ignoró `.env.example` (plantilla segura); se añadieron reglas para `telemetry/*.csv|jsonl` y `NVR-VMS-build/`. No afecta a código. |

> Nota: el árbol de trabajo ya contenía cambios previos (archivos `M`/`D`) ajenos
> a esta auditoría. El commit de la auditoría incluye **solo** los archivos de las
> secciones 3 y 4.

---

## 5. Inventario clasificado

### Código fuente (se conserva — núcleo evaluable)
- `backend/app/` — API Flask, workers, IA, grabación, streaming (99 `.py`).
- `desktop_app/src/` — cliente PySide6 (46 `.py`).
- `CamLink_app/app/src/` — cliente Android Kotlin (24 `.kt`).

### Recursos necesarios (se conservan)
- `go2rtc.exe` — binario de la capa de directo (versionado).
- `yolov8n.pt` / `yolov8n.onnx` — modelos de IA (no versionados; necesarios en runtime).
- `backend/wsdl/` — descriptores ONVIF.
- `alembic.ini`, `backend/app/database/migrations/` — migraciones.

### Dependencias (se conservan)
- `requirements.txt` (completo) y `requirements-backend.txt` (headless).

### Configuración
- `.env` — **secretos reales** (ignorado por git; NO incluir en la entrega).
- `.env.example` / `.env-example` — plantillas (la primera, nueva y canónica).
- `config/` — vacía.

### Documentación (se conserva)
- `docs/` (arquitectura, comparativa, UML, tesis, manual), `Capitulo6_Desarrollo_v3.{md,docx}`,
  `README.md`, `CLAUDE.md`, `packaging/README.md`.

### Archivos temporales / logs / cachés (no esenciales)
- `__pycache__/` en todo el árbol, `go2rtc.log`.

### Archivos de prueba / evidencias (no esenciales para evaluar)
- `backend/tests/` (scripts sueltos, `frame_*.jpg`, `snapshots/`, `.pytest_cache/`,
  `backend/tests/.env`, `backend/tests/yolov8n.pt`).

### Datos de benchmarking (no esenciales)
- `telemetry/*.csv`, `telemetry/*.jsonl` (~2.4 MB).

### Bases de datos
- **Activa:** PostgreSQL `nvr_db` (respaldada en `Entrega/BaseDatos/`).
- **Legacy/no usada:** `data/surveillance.db` (SQLite).

### Artefactos de build (no esenciales)
- `CamLink_app/{.gradle,.idea,.kotlin,build,app/build,app/release,local.properties}` (~341 MB).
- `env/` — entorno virtual de Python (~2.48 GB).

### Herramientas de desarrollo
- `.vscode/`, `.claude/`, `.idea/` (configuración de IDE/asistentes).

---

## 6. Limpieza recomendada (NO ejecutada — segura de aplicar)

Si en el futuro se desea reducir el tamaño del repositorio/entrega, estos
elementos pueden eliminarse del disco sin afectar a la funcionalidad (son
regenerables o intermedios). **Todos están ya cubiertos por `.gitignore`.**

| Elemento | Tamaño aprox. | Por qué es seguro |
|----------|--------------:|-------------------|
| `env/` | 2.48 GB | Entorno virtual; se recrea con `pip install`. |
| `CamLink_app/.gradle`, `build`, `app/build`, `app/release` | ~341 MB | Artefactos de Gradle; se regeneran al compilar. |
| `telemetry/*.csv`, `*.jsonl` | 2.4 MB | Datos de benchmarking de sesiones pasadas. |
| `__pycache__/` (todo el árbol) | menor | Bytecode de Python; se regenera. |
| `go2rtc.log` | 142 KB | Log de ejecución. |
| `backend/tests/` (artefactos) | menor | Frames/snapshots/`.pytest_cache` de pruebas. |
| `data/surveillance.db` | 176 KB | SQLite legacy, no usado por el sistema. |

> Comando sugerido (PowerShell) para purgar cachés de Python sin tocar el resto:
> `Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force`

---

## 7. Riesgos encontrados

| # | Riesgo | Severidad | Estado / Recomendación |
|---|--------|-----------|------------------------|
| R1 | **Secretos reales en `.env`** (claves JWT, token Telegram, password BD). | Alta | `.env` está gitignored (no en git). Mitigado con `.env.example`. **Si se copia la carpeta del proyecto para la entrega, EXCLUIR `.env` y `backend/tests/.env`.** |
| R2 | **`backend/tests/.env`** con credenciales adicionales. | Media | Gitignored. Excluir de cualquier copia de entrega. |
| R3 | Token de Telegram activo embebido. | Media | Por decisión, se deja el `.env` local intacto. Recomendable revocar/regenerar el token con @BotFather tras la titulación. |
| R4 | `README.md` raíz **desactualizado** (menciona streaming MJPEG y BD SQLite, ya no vigentes). | Baja | No se modificó (fuera de alcance). El paquete de entrega usa documentación vigente (`Entrega/`, `CLAUDE.md`, `docs/`). |
| R5 | Archivos fuente **sin trackear** en git (`backend/app/api/media_links.py`, `infrastructure/network_monitor.py`, `services/recording_view_lock_service.py`, `CamLink_app/.../data/`). | Media | Verificar si deben versionarse antes de la entrega final (podrían faltar en un clon limpio). |
| R6 | El árbol de trabajo tiene cambios previos sin commitear (varios `M`/`D`). | Baja | Ajenos a esta auditoría; revisar y commitear/descartar aparte. |

---

## 8. Credenciales encontradas

| Ubicación | Tipo | En git | Acción |
|-----------|------|--------|--------|
| `.env` | `SECRET_KEY`, `JWT_SECRET_KEY`, `MEDIA_URL_SECRET` | No (ignorado) | Reemplazados por placeholders en `.env.example`. |
| `.env` | `TELEGRAM_BOT_TOKEN` (activo), `TELEGRAM_CHAT_ID` | No (ignorado) | Placeholder en plantilla. Revocación recomendada (R3). |
| `.env` | `POSTGRES_PASSWORD` (`nvr_pass`) | No (ignorado) | Placeholder en plantilla. |
| `backend/tests/.env` | Credenciales de pruebas | No (ignorado) | Excluir de la entrega. |

> **Ningún secreto está versionado en git.** El riesgo es únicamente *copiar la
> carpeta del proyecto* con los `.env` dentro.

---

## 9. Configuraciones críticas

- **Proceso único obligatorio:** el backend mantiene singletons con hilos,
  subprocesos FFmpeg y buffers en memoria. No usar WSGI multi-worker.
- **Base de datos:** PostgreSQL vía `POSTGRES_*`; cadena en `get_database_url()`.
- **go2rtc** (`GO2RTC_*`): única capa de directo. `GO2RTC_AS_SOURCE=true` es
  crítico para cámaras que solo aceptan 1 conexión RTSP (XiongMai).
- **IA:** una sola cámara con YOLO a la vez (`AI_CAMERA_ID`).
- **Almacenamiento:** `RECORDINGS_PATH` recomendado FUERA de OneDrive.
- **Seguridad:** en `APP_ENV=production` se exige no usar secretos por defecto.

---

## 10. Dependencias críticas

- **PyTorch CPU** (`torch==2.6.0+cpu`) — instalar **primero** con el index-url
  especial; si falta, la activación de IA devuelve 503 `AI_DEPENDENCIES_MISSING`.
- **Ultralytics 8.3.75** (YOLOv8) — detección de objetos.
- **FFmpeg** — en `PATH`; el backend lo verifica al arrancar.
- **go2rtc** — binario incluido (`go2rtc.exe`); capa de directo.
- **PostgreSQL 17** + `psycopg2-binary` — base de datos activa.
- **Flask 3.1 / Flask-JWT-Extended / flask-sock** — API REST + WebSocket.
- **PySide6 6.8.2** — cliente de escritorio.
- Versiones congeladas en `requirements.txt` / `requirements-backend.txt`.

---

## 11. Estructura final recomendada

```
sistema-videovigilancia/
├── Entrega/                    ← paquete de entrega (NUEVO)
│   ├── README.md               ← índice maestro
│   ├── INFORME_AUDITORIA.md
│   ├── CodigoFuente/README.md  ← apunta al código (no lo duplica)
│   ├── BaseDatos/              ← respaldo PG + scripts + restauración
│   ├── Documentacion/README.md
│   ├── Instalacion/INSTALACION.md
│   ├── Manuales/README.md
│   └── Scripts/README.md
├── backend/app/                ← código backend
├── desktop_app/src/            ← código escritorio
├── CamLink_app/                ← código móvil (Android)
├── docs/                       ← documentación técnica y de tesis
├── packaging/                  ← empaquetado a .exe
├── .env.example                ← plantilla SIN secretos (NUEVO)
├── requirements*.txt
├── go2rtc.exe
├── CLAUDE.md
└── README.md
```

---

## 12. Checklist de entrega

- [x] Inventario completo del repositorio.
- [x] Identificación de artefactos no esenciales.
- [x] `.gitignore` saneado (plantilla versionable, telemetría/build excluidos).
- [x] `.env.example` documentado y **sin secretos**.
- [x] Verificado que ningún secreto está en git.
- [x] Respaldo real de PostgreSQL (esquema + datos de demo).
- [x] Scripts de creación de BD y guía de restauración.
- [x] Guía de instalación paso a paso.
- [x] Índices de código, documentación, manuales y scripts.
- [x] README maestro de la entrega.
- [x] Informe de auditoría (este documento).
- [ ] **Pendiente (responsable):** excluir `.env` y `backend/tests/.env` de
      cualquier copia física de la entrega.
- [ ] **Pendiente (responsable):** decidir si versionar los archivos fuente sin
      trackear (R5) antes de la entrega final.
- [ ] **Recomendado:** revocar/regenerar el token de Telegram tras la titulación.
- [ ] **Opcional:** actualizar el `README.md` raíz (R4).
- [ ] **Opcional:** purgar artefactos pesados de la sección 6 para reducir tamaño.

---

*Verificación funcional (Fase 9) no ejecutada por indicación expresa: esta
auditoría no modificó funcionalidad, por lo que el comportamiento del sistema es
idéntico al previo. La checklist de verificación sugerida está en
`Instalacion/INSTALACION.md` §8.*
