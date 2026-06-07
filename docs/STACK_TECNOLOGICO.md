# Stack Tecnológico — Ecosistema de Videovigilancia LAN (NVR/VMS)

> Documento de referencia para el repositorio de producción. Inventario formal de
> tecnologías, dependencias clave y herramientas por componente.
>
> **Sobre las versiones:** las columnas reflejan las versiones **exactas fijadas
> (pinned) en el repositorio**, verificadas contra `requirements.txt` y
> `requirements-backend.txt` (Python: backend + escritorio) y
> `CamLink_app/app/build.gradle.kts` (móvil). Estos ficheros son la fuente de
> verdad. Dos dependencias se usan en runtime pero **no están fijadas** en los
> requirements (`onnxruntime` y `python-vlc`); se anotan como tal y se recogen en
> la sección de **Observaciones de verificación** (§1.4).

El sistema se divide en tres componentes desplegables:

1. **Servidor / Backend** — Flask (proceso único): ONVIF, orquestación, IA, grabación, API.
2. **Aplicación Móvil** — Android (Kotlin).
3. **Aplicación de Escritorio** — PySide6 (Qt 6).

Además, un **sidecar de medios** (go2rtc) compartido por todos los clientes para el
streaming en vivo.

---

## 1. Tablas de Tecnologías por Componente

### 1.1 Servidor (Backend Flask · Python 3.13)

> Versiones exactas de `requirements-backend.txt` (contenedor/servidor) y
> `requirements.txt` (dev/escritorio). Ambos coinciden salvo OpenCV (el servidor
> usa la variante *headless*) y la ausencia de PySide6 en el de servidor.

| Tecnología / Dependencia | Versión (fijada) | Propósito / Justificación |
|---|---|---|
| **Python** | 3.13 | Lenguaje base del backend. Requerido por el proyecto (tipado moderno, rendimiento del intérprete). |
| **Flask** | 3.1.0 | Framework web/HTTP. Sirve la API REST `/api/v1` con su servidor integrado en modo *threaded* (diseño mono-proceso). |
| **Werkzeug** | 3.1.3 | WSGI subyacente de Flask; manejo de peticiones y `Range` para servir vídeo con *seek*. |
| **Flask-JWT-Extended** | 4.7.1 | Autenticación por **JWT** (access/refresh) de todos los clientes; base del control de acceso multi-tenant. |
| **Flask-Cors** | 5.0.0 | CORS controlado para los orígenes permitidos (clientes en LAN). |
| **flask-sock** + **simple-websocket** | 0.7.0 / 1.1.0 | Canal **WebSocket** `/ws/notifications` para el *push* de eventos en tiempo real a móvil y escritorio. |
| **SQLAlchemy** | 2.0.36 | ORM (`DeclarativeBase` + `Mapped[...]`). Modela usuarios, cámaras, permisos, eventos, grabaciones y preferencias. |
| **psycopg2-binary** | 2.9.10 | Driver PostgreSQL (`postgresql+psycopg2://`). *(Solo en `requirements-backend.txt`.)* |
| **alembic** | 1.14.0 | Migraciones de esquema versionadas. *(Solo en `requirements-backend.txt`.)* |
| **PostgreSQL** *(servicio)* | 16.x | Motor de base de datos relacional. Servicio externo (ver `docker-compose.yml`), no dependencia pip. |
| **onvif-zeep** + **zeep** + **lxml** | 0.2.12 / 4.3.1 / 5.3.0 | Cliente **ONVIF** (SOAP/WSDL): PTZ (`ContinuousMove`), sincronización horaria, IR/LED e *imaging*. |
| **WSDiscovery** | 2.1.2 | Descubrimiento **WS-Discovery** (multicast) de cámaras ONVIF en la red. |
| **go2rtc** *(binario sidecar)* | 1.9.14 | Capa única de medios: ingiere RTSP **una sola vez** por cámara y re-publica **WebRTC / RTSP / HLS**. Pieza central del streaming. |
| **FFmpeg / ffprobe** *(binario)* | 7.x | Transcodes (HEVC→H264, *crop* por lente, escalas) vía go2rtc y grabación `‑c copy`; sondeo de resolución con `ffprobe`. Binario del sistema (empaquetado en Docker). |
| **Ultralytics (YOLOv8)** | 8.3.75 | Modelo de detección de objetos (persona/vehículo) para las alertas inteligentes. (`ultralytics-thop` 2.0.14.) |
| **ONNX Runtime** | *(sin fijar)* | Motor de inferencia real del modelo `.onnx` (`CPUExecutionProvider`). **No está fijado** en requirements; ver §1.4. |
| **PyTorch / TorchVision** | 2.6.0+cpu / 0.21.0+cpu | Backend de Ultralytics. Se instala **aparte** con el índice CPU (no en requirements; ver cabecera de `requirements.txt`). |
| **OpenCV** | 4.11.0.86 | Detección de movimiento (gateo de IA) y fotogramas/snapshots. Servidor: `opencv-python-headless`; dev/escritorio: `opencv-python`. |
| **NumPy** | 2.1.1 | Cómputo numérico (OpenCV/ONNX/Ultralytics). Fijado a 2.1.1 por el tope de Ultralytics (`<=2.1.1`). |
| **Pillow** | 12.0.0 | Tratamiento de imágenes (snapshots/thumbnails); compatibilidad con PyTorch 2.6. |
| **python-telegram-bot** | 21.10 | Integración con **Telegram** (bot de alertas y vinculación de chats). |
| **requests** | 2.32.3 | Cliente HTTP de apoyo (sondeos a la API de Telegram `getUpdates`, utilidades internas). |
| **cryptography** | 44.0.0 | Primitivas criptográficas (firma **HMAC** de URLs de medios, soporte de seguridad). |
| **qrcode** (+ **pypng**) | 7.4.2 | Generación del **QR** de onboarding (vinculación del móvil). |
| **pydantic** | 2.10.4 | Validación/estructura de datos en servicios y configuración. |
| **PyYAML** | 6.0.2 | Generación del fichero de configuración de go2rtc (`go2rtc.generated.yaml`). |
| **python-dotenv** | 1.0.1 | Carga de configuración desde `.env` (settings del backend). |
| *Rate limiter* (middleware propio) | — | Limitación de tasa de la API; implementado como middleware del proyecto (sin dependencia externa). |

### 1.2 Aplicación Móvil (Android · Kotlin)

> Versiones exactas declaradas en `CamLink_app/app/build.gradle.kts`.

| Tecnología / Dependencia | Versión (fijada) | Propósito / Justificación |
|---|---|---|
| **Kotlin** | 2.0.21 | Lenguaje de la aplicación Android. |
| **Android Gradle Plugin (AGP)** | 8.7.x | Sistema de compilación (Gradle Kotlin DSL). `compileSdk 35` (Android 15), `minSdk 29`, `targetSdk 35`, JDK 17. |
| **AndroidX Core-KTX / AppCompat** | 1.15.0 / 1.7.1 | Base de la app (extensiones Kotlin, compatibilidad de UI). |
| **Material Components** | 1.12.0 | Componentes de UI (tarjetas, `TabLayout`, botones, *toggles*). |
| **ConstraintLayout** | 2.2.x | Maquetación de las vistas (live, rejilla, grabaciones). |
| **AndroidX Navigation** | 2.8.5 | Navegación entre *fragments* y paso de argumentos (deep-link a grabaciones/alertas). |
| **AndroidX Lifecycle** | 2.8.7 | Ciclo de vida y *coroutine scopes* ligados a la vista. |
| **Media3 / ExoPlayer** | 1.5.1 | Reproductor de vídeo: **HLS/RTSP** del directo (fallback) y **VOD** de grabaciones; soporte de `Range`/seek. |
| **Android System WebView** | 148.x | Reproductor **WebRTC** del directo de baja latencia (carga una página local que negocia con go2rtc). |
| **Retrofit** | 2.11.0 | Cliente REST tipado contra la API del backend (cámaras, PTZ, grabaciones, preferencias). |
| **OkHttp + Logging Interceptor** | 4.12.0 | Capa HTTP (autenticación JWT, *refresh* de token) y cliente **WebSocket** del servicio de notificaciones. |
| **Gson (converter-gson)** | 2.11.0 | Serialización/deserialización JSON de los DTO de la API. |
| **Kotlin Coroutines** | 1.9.0 | Concurrencia asíncrona para llamadas de red sin bloquear la UI. |
| **CameraX** | 1.4.1 | Cámara del dispositivo para el **escáner QR** de onboarding. |
| **ML Kit Barcode Scanning** | 17.3.x | Lectura del **código QR** de vinculación (servidor + *link token*). |
| **Foreground Service** *(framework)* | — | Mantiene el WebSocket de alertas vivo en segundo plano (`FOREGROUND_SERVICE_DATA_SYNC`). |

### 1.3 Aplicación de Escritorio (PySide6 · Python 3.13)

> El escritorio comparte el `requirements.txt` con el backend (incluye PySide6).
> `python-vlc` se usa pero **no está fijado** en requirements (ver §1.4).

| Tecnología / Dependencia | Versión (fijada) | Propósito / Justificación |
|---|---|---|
| **Python** | 3.13 | Lenguaje base del cliente de escritorio. |
| **PySide6 (Qt 6)** | 6.8.2 | *Framework* de UI de escritorio (vistas, diálogos, *widgets*, estilos). Bindings oficiales de Qt para Python. |
| **Qt Core (QThreadPool / QRunnable)** | 6.8.2 | **Gestión de hilos** del cliente: las llamadas REST corren en un *thread pool* para no congelar la UI; resultados marshalados al hilo principal. |
| **QSettings** *(Qt Core)* | 6.8.2 | Persistencia local de preferencias y *flags* (p. ej. onboarding ya mostrado). |
| **python-vlc (libVLC)** | *(sin fijar)* | Reproducción del **directo (RTSP restream de go2rtc)** y de **grabaciones**; recorte por lente vía *crop* de VLC. **No está en requirements**; ver §1.4. |
| **VLC media player (runtime)** | 3.0.x | Motor multimedia nativo requerido por `python-vlc` (debe estar instalado en el equipo). |
| **requests** | 2.32.3 | Cliente HTTP del `api_client` (REST + JWT, descubrimiento ONVIF contra el backend). |
| **qrcode** | 7.4.2 | Generación del **QR** de vinculación del móvil desde el escritorio. |

### 1.4 Observaciones de verificación (gaps detectados)

| Dependencia | Estado | Recomendación |
|---|---|---|
| **onnxruntime** | Usada en runtime (la IA corre el modelo `.onnx` con `CPUExecutionProvider`), pero **no aparece** en `requirements.txt` ni `requirements-backend.txt`. | **Fijarla explícitamente** (p. ej. `onnxruntime==1.20.x`) para builds reproducibles; hoy depende de que esté presente en el entorno. |
| **python-vlc** | El cliente de escritorio reproduce con libVLC, pero **no está en requirements**. | **Añadirla** (`python-vlc==3.0.x`) y documentar que requiere el runtime de **VLC** instalado en el equipo. |
| **PySide6 en `requirements.txt`** | El escritorio comparte el `requirements.txt` con el backend. Para el contenedor headless se usa `requirements-backend.txt` (sin PySide6), lo cual es correcto. | Mantener la separación; opcionalmente extraer un `requirements-desktop.txt` para aislar la GUI. |

---

## 2. Herramientas de Entorno de Desarrollo (comunes)

| Herramienta | Categoría | Uso en el proyecto |
|---|---|---|
| **Visual Studio Code** | IDE | Editor principal (backend Python + escritorio PySide6). |
| **Android Studio** | IDE | Desarrollo, depuración y *profiling* de la app móvil; emulador/ADB. |
| **PyCharm** *(opcional)* | IDE | Alternativa para el código Python (backend/escritorio). |
| **Git** | Control de versiones | Gestión del monorepo (backend, móvil, escritorio). |
| **Gradle (Kotlin DSL)** | Build (móvil) | Compilación y empaquetado del APK (`assembleDebug`/`assembleRelease`). |
| **Docker + Docker Compose** | Contenedores | Despliegue de PostgreSQL + backend (con FFmpeg y go2rtc embebidos). Ver `DOCKER.md`. |
| **Alembic CLI** | Migraciones BD | Evolución del esquema (`alembic upgrade head`). |
| **unittest** (`run_tests.py`) | Testing (backend) | Suite canónica del backend (no pytest). |
| **JUnit + Espresso** | Testing (móvil) | Pruebas unitarias e instrumentadas de Android. |
| **Ruff / Flake8** | Linter (Python) | Análisis estático y estilo del código Python. |
| **Black / isort** | Formato (Python) | Formateo consistente e imports ordenados. |
| **mypy** *(opcional)* | Tipado (Python) | Verificación estática de tipos donde aplica. |
| **ktlint / detekt** | Linter/Formato (Kotlin) | Estilo y análisis estático del código Android. |
| **go2rtc Web UI** (`:1984`) | Diagnóstico de medios | Inspección de streams, prueba de WebRTC/HLS y estado de productores/consumidores. |
| **VLC media player** | Pruebas RTSP | Verificación manual de URLs RTSP de cámara y del restream de go2rtc. |
| **FFmpeg / ffprobe (CLI)** | Diagnóstico de medios | Inspección de códecs/resolución y pruebas de transcode fuera de la app. |
| **ONVIF Device Manager** | Herramienta ONVIF | Verificación de dispositivos ONVIF, perfiles y PTZ en la red. |
| **Wireshark** | Análisis de red | Depuración de RTSP/RTP, ONVIF (SOAP) y WS-Discovery (multicast `:3702`). |
| **Postman / curl** | Pruebas de API | Validación manual de los endpoints REST con JWT. |
| **DBeaver / pgAdmin** | Cliente de BD | Inspección y administración de PostgreSQL. |

---

## 3. Resumen de Protocolos y Puertos (referencia rápida)

| Servicio | Protocolo | Puerto | Origen → Destino |
|---|---|---|---|
| Vídeo de cámara | RTSP / RTP (H.265) | 554 | Cámara → go2rtc |
| Control de cámara | ONVIF (SOAP/HTTP) | 8899 | Backend → Cámara |
| Descubrimiento | WS-Discovery (UDP multicast) | 3702 | Backend ↔ Cámaras |
| API / HLS de medios | HTTP | 1984 | Clientes/Backend → go2rtc |
| Restream RTSP | RTSP | 8554 | Desktop/Backend → go2rtc |
| Medios WebRTC | WebRTC (UDP/TCP) | 8555 | Móvil → go2rtc |
| API REST + WebSocket | HTTP / WS (JWT) | 5000 | Clientes → Backend |
| Base de datos | PostgreSQL | 5432 | Backend → PostgreSQL |
| Notificaciones externas | HTTPS | 443 | Backend → Telegram Bot API |

> Documentación relacionada: [`docs/ARQUITECTURA.md`](ARQUITECTURA.md) (diagramas de
> arquitectura, flujo de datos y secuencias).
