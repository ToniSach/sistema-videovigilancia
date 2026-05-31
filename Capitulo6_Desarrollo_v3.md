# CAPÍTULO 6. DESARROLLO

## 6.1 Introducción

El presente capítulo expone el proceso de construcción del Sistema de Videovigilancia LAN basado en arquitectura cliente–servidor, desarrollado como solución comercial de tipo NVR/VMS (*Network Video Recorder / Video Management System*) para entornos residenciales y de pequeña empresa. A diferencia de los sistemas tradicionales que dependen de servicios externos en la nube, este proyecto se diseñó bajo el paradigma *LAN-first*, en el cual la captura, el procesamiento, el almacenamiento y la consulta del video ocurren íntegramente dentro de la red local del usuario. La única salida hacia Internet es la pasarela de notificaciones de Telegram, condición indispensable para cumplir con los principios de privacidad y soberanía de datos planteados en los capítulos previos.

El desarrollo se llevó a cabo siguiendo una metodología iterativa orientada a la verificación temprana de cada componente. A lo largo del proceso se priorizaron tres atributos no funcionales que son críticos en un sistema de videovigilancia: la **baja latencia** del enlace de previsualización en vivo, la **fiabilidad** del subsistema de grabación continua y la **integridad** de la cadena de notificaciones (detección → snapshot → mensaje en cliente). Estos atributos guiaron decisiones de diseño concretas que se describirán en las secciones subsiguientes, tales como la sustitución del modelo de empuje de cuadros basado en señales de Qt por un modelo de muestreo activo (*pull-based*), la introducción de un *splitter* para cámaras *dual-lens* compartiendo un único flujo RTSP, o la persistencia del snapshot antes de la emisión del evento para evitar condiciones de carrera entre los suscriptores.

Cabe señalar que el sistema en su estado actual integra dos aplicaciones cliente: un cliente de escritorio basado en PySide6 (Qt 6) que sirve como interfaz administrativa principal, y una aplicación móvil Android dirigida a la operación itinerante del usuario final. Ambas se comunican con el mismo *backend* Flask a través de una API REST autenticada con JSON Web Tokens (JWT) y consumen los mismos endpoints, lo que evitó la duplicación de lógica de negocio. El presente capítulo aborda en detalle la implementación del *backend* y del cliente de escritorio, mientras que las secciones relativas a la aplicación móvil se mantienen conforme a la versión consolidada por el equipo de desarrollo móvil.

El capítulo se estructura en torno al recorrido completo del cuadro de video, desde su captura por el subsistema FFmpeg hasta su entrega al usuario en forma de previsualización en vivo, evento persistido en base de datos o alerta enriquecida con imagen y clip. Cada bloque del recorrido se justifica desde el punto de vista de los requisitos del producto, se documenta con la decisión de diseño correspondiente y se contrasta con las alternativas evaluadas durante la implementación.

---

## 6.2 Tecnologías y herramientas empleadas

La selección de la pila tecnológica respondió a criterios de madurez de la comunidad, disponibilidad de bindings nativos en Python, cumplimiento de licencias compatibles con un producto comercial y compatibilidad con Windows 11 como sistema operativo objetivo del despliegue de referencia. La Tabla 6.1 resume los componentes principales y la versión efectivamente utilizada en producción.

**Tabla 6.1. Pila tecnológica del Sistema de Videovigilancia LAN**

| Capa | Tecnología | Versión | Función dentro del sistema |
|---|---|---|---|
| Lenguaje | Python | 3.13 | Lenguaje único para *backend* y cliente de escritorio. |
| Framework web | Flask | 3.1.0 | Servidor HTTP del *backend* y enrutador de la API REST. |
| Autenticación | Flask-JWT-Extended | 4.7.1 | Emisión y verificación de tokens de acceso y *refresh*. |
| ORM | SQLAlchemy | 2.0.36 | Mapeo objeto-relacional con sintaxis declarativa moderna. |
| Base de datos | PostgreSQL (psycopg2) | 16.x | Almacenamiento transaccional de cámaras, eventos, grabaciones y usuarios. |
| Migraciones | Alembic | última | Versionado de esquema de base de datos. |
| Visión por computadora | OpenCV (`cv2`) | 4.11.0.86 | Manipulación de cuadros, codificación JPEG, *motion detection*. |
| Inferencia | Ultralytics YOLOv8 | 8.3.75 | Detección de personas, vehículos y objetos clase COCO. |
| Tensor *backend* | PyTorch + Torchvision (CPU) | 2.6.0 / 0.21.0 | Soporte de cómputo para el modelo YOLOv8n. |
| Captura | FFmpeg | 8.0 | Decodificación de RTSP y rescalado a `bgr24` de tamaño fijo. |
| Cliente escritorio | PySide6 (Qt) | 6.8.2 | Interfaz administrativa nativa en Windows. |
| Descubrimiento | wsdiscovery + onvif-zeep | 2.1.2 / 0.2.12 | Búsqueda automática de cámaras ONVIF en la subred. |
| Mensajería | python-telegram-bot + Bot API | 21.10 | Envío de alertas con foto y video al usuario. |
| QR | qrcode + pypng | 7.4.2 | Vinculación rápida del cliente móvil con la cuenta del usuario. |
| Servidor productivo | Gunicorn (gevent) | — | Servidor WSGI utilizado como referencia para despliegue. |

La instalación del entorno requiere una secuencia específica: PyTorch debe instalarse en primer lugar desde el índice oficial CPU de PyTorch (`https://download.pytorch.org/whl/cpu`), debido a que la combinación `torch==2.6.0+cpu` y `torchvision==0.21.0+cpu` no se distribuye por el repositorio PyPI estándar. Posteriormente se instala el resto del archivo `requirements.txt`. Esta precedencia se documenta en el propio `requirements.txt` y se reproduce en el archivo `CLAUDE.md` del repositorio. Si las dependencias de IA estuvieran ausentes, el sistema no falla en arranque, sino que el endpoint `POST /api/v1/ai/<id>/activate` responde con el código HTTP **503** y un campo `error_code: AI_DEPENDENCIES_MISSING`, indicando el comando exacto de instalación que el operador debe ejecutar. Esta política de "fallar tarde pero con mensaje accionable" se aplica a lo largo del proyecto para evitar diagnósticos opacos durante el despliegue.

El binario `ffmpeg` debe estar accesible en la variable de entorno `PATH`, lo cual se verifica en el arranque mediante `shutil.which("ffmpeg")`. La ausencia del binario provoca una excepción explícita en `FFmpegWorker` antes incluso de intentar abrir el primer flujo RTSP, lo cual evita que la cámara entre en un bucle silencioso de reconexión.

---

## 6.3 Arquitectura general del sistema

### 6.3.1 Visión de alto nivel

El sistema adopta una arquitectura **cliente–servidor de proceso único**, en la cual un único proceso Python del *backend* gestiona todas las cámaras configuradas y atiende todas las solicitudes HTTP entrantes. Los clientes (escritorio y móvil) se conectan exclusivamente a este proceso mediante REST y MJPEG sobre HTTP. La Figura 6.1 ilustra la composición general.

```
┌──────────────────────────────────────────────────────────────┐
│                        Red de área local                     │
│                                                              │
│   ┌────────────┐   RTSP   ┌────────────────────────────┐     │
│   │ Cámara 1   │─────────▶│                            │     │
│   ├────────────┤          │                            │     │
│   │ Cámara 2   │─────────▶│  Backend Flask + Singletons│     │
│   ├────────────┤          │  (Python 3.13, proceso     │     │
│   │ Cámara 3   │─────────▶│   único, gevent)           │     │
│   ├────────────┤          │                            │     │
│   │ Cámara 4   │─────────▶│  PostgreSQL                │     │
│   └────────────┘          │  Almacenamiento local      │     │
│                           └──────┬──────────┬──────────┘     │
│                                  │ REST/MJPEG               │
│                          ┌───────┴─────┐  ┌─┴────────────┐  │
│                          │ Cliente     │  │ Aplicación   │  │
│                          │ Escritorio  │  │ Móvil        │  │
│                          │ PySide6     │  │ Android      │  │
│                          └─────────────┘  └──────┬───────┘  │
└─────────────────────────────────────────────────│──────────┘
                                                  │
                                            Internet (solo
                                            Telegram Bot API)
```

**Figura 6.1.** Arquitectura general de despliegue. El proceso *backend* es la única entidad con acceso simultáneo a las cámaras y a la base de datos; todo el resto del sistema accede a través de la API REST.

### 6.3.2 Restricción crítica: proceso único

Una restricción no funcional de primer orden, que condiciona buena parte de las decisiones posteriores, es que **el *backend* debe ejecutarse en un proceso único**. Esta restricción surge porque casi todas las clases con estado de larga vida del sistema están implementadas como *singletons* mediante el patrón `__new__`. Estos *singletons* mantienen en memoria del proceso recursos no serializables o de imposible replicación, tales como subprocesos de FFmpeg, *buffers* circulares de cuadros, *pools* de modelos de IA, *pools* de conexión a base de datos y suscripciones al *event bus*. Los componentes implicados son los siguientes:

- `CameraManager` ([camera_manager.py](backend/app/cameras/camera_manager.py)) — registro de cámaras activas y sus *workers*.
- `DependencyContainer` ([container.py](backend/app/container.py)) — contenedor de inyección de dependencias con servicios y repositorios.
- `DatabaseManager` ([connection.py](backend/app/database/connection.py)) — *pool* de conexiones a PostgreSQL.
- `EventManager` ([event_manager.py](backend/app/events/event_manager.py)) — *bus* de eventos publicador/suscriptor.
- `GlobalExecutor` ([executor.py](backend/app/core/executor.py)) — *pool* limitado a 50 hilos para aplicar *back-pressure* a tareas en ráfaga.
- `metrics_collector`, `live_hls_service`, `mjpeg_streamer`, `consistency_checker` — servicios auxiliares con estado.

Como consecuencia directa, el servidor de producción Gunicorn está configurado en [gunicorn.conf.py](gunicorn.conf.py) con `workers = 1` y `worker_class = "gevent"`, garantizando un único proceso multiplexado por corrutinas en lugar de varios procesos paralelos. Escalar horizontalmente requeriría reubicar el estado compartido en almacenamiento externo (Redis, base de datos), refactorización que se documenta como trabajo futuro en el Capítulo 8.

### 6.3.3 Pipeline por cámara

Por cada cámara activa, el método `CameraManager.start_camera()` construye e interconecta una cadena de procesamiento independiente, cuyas etapas se muestran en la Figura 6.2.

```
RTSP ──▶ FFmpegWorker ──▶ CircularFrameBuffer ──▶ FrameDistributor ──┬─▶ MJPEGStreamer (zero-copy)
                                                                     ├─▶ AIScheduler (motion-gated)
                                                                     ├─▶ RecordingManager
                                                                     └─▶ LiveHLSService
```

**Figura 6.2.** *Pipeline* de procesamiento por cámara. Las flechas representan transferencia de cuadros por referencia salvo en los consumidores que declaran `needs_copy=True`.

Las responsabilidades de cada bloque son las siguientes:

- **FFmpegWorker:** lanza un subproceso `ffmpeg` que abre la URL RTSP, decodifica los paquetes H.264/H.265 y emite por *standard output* cuadros crudos en formato `bgr24` reescalados a la resolución objetivo. Implementa un *watchdog* con tiempo de espera de 30 segundos para marcar la cámara como `FROZEN` si no llegan cuadros, así como reconexión con *backoff* exponencial hasta diez intentos consecutivos.

- **CircularFrameBuffer:** *deque* protegida por *lock*, de tamaño tres cuadros por defecto. Su política es "el cuadro más reciente gana": cuando llega un cuadro nuevo y el *buffer* está lleno, se descarta el más antiguo. Esta política privilegia la latencia frente a la integridad temporal, lo cual es deseable para el flujo en vivo y aceptable para la grabación, que utiliza un mecanismo distinto basado en *splice* de segmentos continuos.

- **FrameDistributor:** distribuidor publicador/suscriptor de cuadros que despacha cada cuadro a los N consumidores registrados. La distribución se realiza sobre el `GlobalExecutor`, con tamaño de cola limitado a 2 elementos para evitar acumulación. Los consumidores declaran al registrarse si necesitan una copia del cuadro (`needs_copy=True`) o si pueden operar sobre el mismo *buffer* de memoria sin riesgo de modificaciones concurrentes; el *streamer* MJPEG aprovecha este último caso para evitar la sobrecarga de `np.copy()` en el camino crítico.

### 6.3.4 Tratamiento de cámaras *dual-lens*

Una característica distintiva del proyecto es el soporte nativo de cámaras *dual-lens*, las cuales entregan dos puntos de vista por un único flujo RTSP, fusionados horizontal o verticalmente en un solo cuadro. El modelo de referencia utilizado en la validación experimental es la cámara XiongMai XM535, que ofrece dos lentes en una resolución combinada de 1280×1440 (dos lentes apilados verticalmente de 1280×720).

Cuando una cámara se marca con el atributo `Camera.is_dual_lens = True`, el sistema **no** instancia dos pipelines independientes. En su lugar, `CameraManager.start_dual_lens_camera()` mantiene un único `FFmpegWorker` que reescala el flujo combinado a la resolución configurada por las variables `FFMPEG_DUAL_LENS_WIDTH` y `FFMPEG_DUAL_LENS_HEIGHT` (por defecto 960×1080, equivalente a dos lentes de 960×540). El módulo `DualLensSplitter` ([dual_lens_splitter.py](backend/app/cameras/dual_lens_splitter.py)) se interpone en la salida del distribuidor, divide cada cuadro en dos sub-cuadros y los inyecta directamente en el `MJPEGStreamer` con identificadores lógicos `l1` y `l2`. Los identificadores se propagan a través del parámetro `stream_id` y permiten que el cliente solicite cada lente por separado mediante una URL distinta. Esta elección de diseño evita duplicar buffers, distribuidores y conexiones RTSP, lo cual reduce el consumo de memoria y de ancho de banda de red en aproximadamente un 50 % en comparación con la alternativa de dos pipelines paralelos.

### 6.3.5 Composición de la aplicación Flask

El arranque del *backend* ([main.py](backend/app/main.py)) sigue una secuencia ordenada y deliberadamente verbosa:

1. **Inicialización de extensiones:** JWT, CORS (con lista blanca explícita, sin comodín `*`), *rate limiter*.
2. **Inicialización de la base de datos:** `db_manager.init_db()` crea las tablas si no existen, complementando los *scripts* de Alembic para permitir un arranque "en limpio" sin migraciones manuales.
3. **Construcción del contenedor de dependencias:** `get_container()` instancia repositorios y servicios siguiendo el patrón de inyección de dependencias.
4. **Arranque asíncrono de cámaras:** un hilo *daemon* espera 0.5 segundos y luego invoca `CameraManager().start_all_active()`. Este retraso evita que el servidor HTTP quede pendiente mientras se establecen las conexiones RTSP, las cuales pueden tardar varios segundos por cámara.
5. **Servicios auxiliares:** `StorageManager`, `MetricsCollector`, `LiveHLSService`, `ConsistencyChecker` y un monitor de cámaras estancadas.
6. **Registro de *blueprints*:** mediante la función defensiva `safe_register()`, que tolera fallos individuales sin abortar el arranque. Las fallas se registran en el log con el patrón `"Error registrando '<nombre>'"`, y el endpoint público `/api/v1/health` devuelve la lista de *blueprints* efectivamente activos para facilitar el diagnóstico remoto.

---

## 6.4 Implementación del *backend*: captura, distribución y *streaming*

### 6.4.1 Modelo de datos

El esquema relacional del sistema se implementó con SQLAlchemy 2.0 utilizando la sintaxis declarativa moderna (`DeclarativeBase` y `Mapped[...]`). Esta sintaxis ofrece tipado estático verificable por el comprobador `mypy` y elimina la ambigüedad de la sintaxis clásica. La Tabla 6.2 resume las entidades principales y su responsabilidad.

**Tabla 6.2. Entidades del modelo de datos**

| Entidad | Propósito | Relaciones clave |
|---|---|---|
| `User` | Cuenta de usuario con rol (`admin`/`user`). | 1-N con `Camera`, `MobileDevice`, `NotificationPreference`, `UserTelegramChat`. |
| `Camera` | Cámara IP registrada con sus capacidades. | N-1 con `User` (`owner_id`); 1-N con `Event`, `Recording`, `UserCameraPermission`. |
| `UserCameraPermission` | Permisos granulares por par usuario-cámara (ver, PTZ, LEDs, audio, descarga). | N-1 con `User` y `Camera`. |
| `Event` | Evento de seguridad detectado (movimiento, persona, vehículo, *tampering*). | N-1 con `Camera`; 1-N con `NotificationLog`. |
| `Recording` | Segmento de grabación continua o por evento. | N-1 con `Camera`. |
| `MobileDevice` | Dispositivo móvil registrado con su token FCM. | N-1 con `User`. |
| `NotificationPreference` | Preferencias de notificación por usuario, tipo de evento y cámara opcional. | 1-N con `NotificationChannel` y `NotificationDay`. |
| `UserTelegramChat` | Vinculación entre usuario y `chat_id` de Telegram. | N-1 con `User`. |
| `RevokedToken` | JWT revocados persistentes entre reinicios. | N-1 opcional con `User`. |
| `SystemConfig` | Configuración global tipo clave-valor. | — |
| `AuditLog` | Registro de auditoría de acciones sensibles. | N-1 opcional con `User`. |

Conviene destacar tres decisiones de diseño:

- **Permisos granulares.** La existencia de `UserCameraPermission` permite que el propietario de una cámara (`owner_id`) comparta el acceso con otros usuarios sin transferir la propiedad. Para todas las verificaciones de autorización se utiliza el servicio `PermissionService`, dado que la sola comprobación de `owner_id` no captura los casos de cámaras compartidas. Esta convención se ha encapsulado en el decorador `@require_camera_permission`, aplicado a todos los endpoints sensibles definidos en [cameras.py](backend/app/api/routes/cameras.py).

- **Normalización de notificaciones.** Una preferencia de notificación se descompone en filas separadas para los canales (Telegram, *push*) y los días de la semana, en lugar de codificarse como cadena delimitada. Esta normalización facilita las consultas SQL y los cambios futuros del producto sin alterar el formato de filas existentes.

- **Persistencia de JWT revocados.** El *blocklist* en memoria (`JWTBlocklist`) es la fuente primaria de consulta por su latencia mínima, pero se complementa con la tabla `RevokedToken`, la cual permite que un cierre de sesión efectuado en el instante *t = 0* sobreviva al reinicio del *backend* en *t = 5 min*. Al arrancar, el *blocklist* se **rehidrata** desde la base de datos con los `jti` cuya `expires_at` aún no ha vencido, y un *garbage collector* purga las entradas vencidas periódicamente.

### 6.4.2 Captura RTSP con FFmpeg

La captura del flujo se delega íntegramente al binario `ffmpeg`, encapsulado en la clase `FFmpegWorker` ([ffmpeg_worker.py](backend/app/workers/ffmpeg_worker.py)). Se prefirió esta arquitectura sobre `cv2.VideoCapture` por dos razones técnicas comprobadas durante la fase de pruebas: la primera, que la implementación de OpenCV abre internamente un FFmpeg con `analyzeduration` de cinco segundos, lo cual añade un retardo de cinco segundos en cada arranque de cámara y en cada reconexión; la segunda, que el control fino del *demuxer* (probesize, *flush_packets*, *low_delay*) no es accesible desde la API de alto nivel de OpenCV.

**Optimización del arranque.** Para minimizar la latencia de la primera previsualización, el comando `ffmpeg` se construye con un conjunto deliberadamente reducido de banderas, comentadas exhaustivamente en el código:

```
-rtsp_transport tcp        # Fiable frente a pérdida de paquetes en LAN
-timeout 10000000          # 10 s (microsegundos) — FFmpeg 6+
-probesize 32              # No esperar 5 MB antes de empezar a entregar
-analyzeduration 0         # No analizar el stream, entregar inmediato
-fflags nobuffer+flush_packets
-flags low_delay           # Decoder en modo low-latency
-flags2 +fast              # Saltar reordenamiento de B-frames
-strict experimental
-vsync passthrough         # Entrega frames tal como llegan
-vf scale=W:H:force_original_aspect_ratio=disable
-f rawvideo -pix_fmt bgr24
pipe:1
```

Con esta configuración, FFmpeg entrega el primer cuadro decodificado con los primeros bytes recibidos por RTSP, en lugar de esperar a acumular varios megabytes para inferir parámetros del flujo. La pérdida de la auto-inferencia es asumible porque la resolución real ya se determina previamente mediante `ffprobe` y se persiste en la columna `Camera.resolution_width/height`, lo cual permite reusar el valor en arranques posteriores sin invocar `ffprobe` de nuevo.

**Compatibilidad multi-versión.** Una sutileza significativa es que las versiones de FFmpeg anteriores a la 6 utilizan la bandera `-stimeout`, mientras que la 6 y posteriores requieren `-timeout`. El método de clase `_detect_ffmpeg_version()` ejecuta `ffmpeg -version` una única vez por proceso, parsea la salida con la expresión regular `r"ffmpeg version n?(\d+)\.(\d+)"` y cachea el resultado en `_FFMPEG_VERSION_CACHE`. Esta detección permite que el mismo código corra contra FFmpeg 4 (distribuciones Linux antiguas), FFmpeg 6 (Ubuntu 24.04) y FFmpeg 8 (binarios recientes para Windows), sin necesidad de configuración manual.

Adicionalmente, la clase mantiene una **lista negra dinámica** de opciones no soportadas (`_UNSUPPORTED_OPTIONS`): si el primer arranque de FFmpeg falla con un mensaje del tipo `"Option XXX not found"` o `"Unrecognized option 'YYY'"`, el nombre de la opción se añade a la lista y se omite en los siguientes intentos. Esto permite reaccionar a binarios FFmpeg compilados con conjuntos de opciones distintos sin requerir un nuevo despliegue.

**Lectura exacta de cuadros.** Una vez establecido el subproceso, el bucle principal lee del *pipe* de salida bloques de exactamente `W × H × 3` bytes (formato `bgr24`), los reinterpreta como un `numpy.ndarray` de forma `(H, W, 3)` y los inserta en el *buffer* circular. La función auxiliar `_read_exact()` garantiza la atomicidad ante lecturas parciales del *pipe*, escenario frecuente en sistemas con presión de E/S.

**Reconexión y *watchdog*.** El sistema implementa dos mecanismos de recuperación complementarios:

- *Reconexión con backoff exponencial:* si el *pipe* se cierra (subproceso terminó o la cámara cerró la sesión), el bucle externo espera `min(2^(n-1), 30)` segundos antes del siguiente intento, hasta un máximo de diez intentos. Antes esta espera era `5 × 1.5^n`, que generaba 7.5 s de retardo en el primer intento y resultaba inaceptable para una LAN donde la cámara suele volver al instante.

- *Watchdog de cuadros:* un hilo separado verifica cada dos segundos si han transcurrido más de 30 segundos desde el último cuadro recibido (`WATCHDOG_TIMEOUT`). En tal caso fuerza un `kill()` del subproceso FFmpeg, lo cual provoca que el bucle externo reinicie la sesión. Esta política protege contra el escenario en que el *pipe* aparenta estar abierto pero FFmpeg ha quedado bloqueado internamente (por ejemplo, en un *backoff* TCP del *kernel*).

Cabe señalar que el firmware de algunas cámaras de gama doméstica, en particular la XiongMai XM535 utilizada en las pruebas, cierra la sesión RTSP aproximadamente cada dos minutos por motivos de gestión interna. Este comportamiento, ajeno al *backend*, se observa en los logs como reconexiones periódicas con `_reconnect_attempts = 1` y se considera operación normal.

### 6.4.3 *Buffer* circular y propiedad de cuadros

La clase `CircularFrameBuffer` ([frame_buffer.py](backend/app/streaming/frame_buffer.py)) implementa una `collections.deque` protegida por *lock* con la siguiente semántica:

- **Inserción:** el método `put()` añade al final. Si la cola está llena, `deque` con `maxlen` descarta automáticamente el extremo opuesto (el cuadro más antiguo). Se mantiene un contador `dropped_frames` para diagnóstico.
- **Lectura:** el método `get_latest()` devuelve la **referencia** al cuadro más reciente sin realizar copia. La decisión de copiar o no se delega al `FrameDistributor`, en función de las declaraciones de los consumidores. Esta separación es deliberada y permite el modo *zero-copy* aprovechado por el *streamer* MJPEG.
- **Tamaño:** `maxsize = 2` por defecto en producción, suficiente para tolerar un retraso transitorio de un consumidor sin perder el cuadro vigente y manteniendo un costo de memoria mínimo (~1.4 MB a 960×540×3).

El parámetro `FrameData.frame_id` es un contador monotónico por *buffer* que permite a los consumidores detectar duplicados o saltos. El distribuidor lo utiliza para descartar publicaciones repetidas en su bucle principal.

### 6.4.4 *FrameDistributor*: publicación a múltiples consumidores

El módulo `FrameDistributor` ([frame_distributor.py](backend/app/streaming/frame_distributor.py)) ejecuta un hilo dedicado por cámara que muestrea el *buffer* circular y reparte cada nuevo cuadro a todos los consumidores registrados. El diseño incorpora cuatro decisiones notables:

1. **Identificación por nombre.** Cada consumidor se registra con una clave única (`mjpeg`, `ai_scheduler`, `recording`, etc.). Esto permite reemplazar dinámicamente un consumidor (por ejemplo, al reactivar la IA con un nuevo *scheduler*) sin necesidad de reiniciar todo el *pipeline*.

2. **Política de copia configurable.** El parámetro booleano `needs_copy` se evalúa en cada distribución: si vale `True`, se realiza `frame.copy()` antes de despachar; si vale `False`, se entrega la referencia compartida. El *streamer* MJPEG declara `needs_copy=False` porque serializa el cuadro a JPEG de forma inmediata (operación de solo lectura) y no lo retiene; el grabador y el módulo de IA declaran `needs_copy=True` porque pueden mantener el cuadro en memoria por varios milisegundos o segundos.

3. **WeakMethod para evitar referencias zombi.** Los consumidores que se registran con un *bound method* (caso usual: `self._on_frame`) se almacenan envueltos en un `weakref.WeakMethod`. Si el objeto dueño es liberado por el recolector de basura sin haber invocado `unregister_consumer()`, el distribuidor lo detecta en la siguiente iteración y se desregistra automáticamente, evitando que se acumulen invocaciones a métodos sobre objetos inexistentes.

4. **Despacho en *executor* global.** Cada invocación de callback se encola en el `GlobalExecutor`, que mantiene un *pool* limitado a 50 hilos compartido por todos los componentes del sistema. Si el *executor* está saturado, el `submit()` devuelve `None` y el distribuidor registra una advertencia *throttled* (uno cada 100 cuadros) sin bloquear el pipeline. Esta política de *back-pressure* protege al sistema de avalanchas de procesamiento en escenarios degradados (por ejemplo, IA lenta + grabador lento + ráfaga de eventos simultáneos).

### 6.4.5 *Streaming* MJPEG

El *streamer* MJPEG ([mjpeg_streamer.py](backend/app/streaming/mjpeg_streamer.py)) es la pieza encargada de servir la previsualización en vivo a los clientes. Implementa una arquitectura **una codificación en vuelo por *stream***, con las siguientes propiedades:

- **Cola por cliente.** Cada cliente HTTP registrado dispone de una `queue.Queue(maxsize=1)`. Cuando una nueva codificación JPEG está disponible, el *streamer* la inyecta en todas las colas con la política *drop-oldest*: si la cola está llena (el cliente lee más lento que el productor), se descarta el cuadro anterior antes de insertar el nuevo. El recuento de descartes por cliente queda disponible para diagnóstico.

- **Drop-newest en el codificador.** Antes incluso de la cola por cliente, el *streamer* mantiene una bandera `_encoding_in_flight[key]` por par `(camera_id, stream_id)`. Si ya hay una codificación en curso, el cuadro nuevo se descarta sin entrar a la cola, lo cual evita acumulación interna y mantiene la latencia acotada. Esta política, contraintuitiva al principio, fue la solución a un caso observado en el que el codificador iba "atrasado en el tiempo" varios segundos mientras la cámara ya entregaba cuadros recientes.

- **Codificación adaptativa.** El método `_encode_frame()` aplica un escalado a `MJPEG_MAX_WIDTH` (por defecto 960 px) usando `cv2.INTER_AREA` y codifica con `IMWRITE_JPEG_QUALITY=75`, `JPEG_OPTIMIZE=0` y `JPEG_PROGRESSIVE=0`. La calidad 75 se eligió tras medir que produce JPEGs aproximadamente 40 % menores que la calidad 85 sin diferencia perceptual en una previsualización en vivo. El JPEG no progresivo y sin optimización Huffman ahorra entre 5 % y 10 % de CPU en el codificador.

- **Telemetría de latencia.** Para cada codificación se registran dos métricas: el `encode_ms` (tiempo de `cv2.imencode`) y el `frame_age_ms` (diferencia entre `time.time()` en el envío y `frame_data.timestamp` original del *buffer*). La segunda métrica es la **latencia real del *backend***: si su percentil 95 está por debajo de 500 ms, el problema percibido en el cliente proviene de fuera del *backend* (red, GOP de la cámara, render del cliente). Esta separación de hipótesis ha demostrado ser invaluable durante la depuración de problemas de latencia.

- **Endpoint de diagnóstico.** El endpoint `GET /api/v1/cameras/<id>/latency?stream=main` devuelve los percentiles 50, promedio y máximo de las últimas 30 muestras tanto de `encode` como de `frame_age`, junto con el conteo de clientes activos y de cuadros descartados por *backlog*. Este endpoint es la herramienta principal para diagnóstico en producción.

### 6.4.6 Servidor HTTP y desactivación de Nagle

Una sutileza de bajo nivel relevante para la latencia: el servidor de desarrollo de Flask, basado en `werkzeug.serving.WSGIRequestHandler`, no establece la opción `TCP_NODELAY` en los *sockets* aceptados. Esto significa que el algoritmo de Nagle del *kernel* puede retener pequeñas escrituras durante hasta 40 ms para fusionarlas con escrituras posteriores. Para un *stream* MJPEG con cuadros pequeños (~30–60 KB), este comportamiento añade un retardo perceptible.

La solución implementada en [main.py](backend/app/main.py) consiste en una subclase `_LowLatencyHandler(WSGIRequestHandler)` que activa `TCP_NODELAY` y, opcionalmente, `TCP_QUICKACK` sobre cada conexión aceptada. El servidor Flask se instancia pasando esta clase como `request_handler`. El *handler* es retrocompatible con el servidor estándar y no introduce dependencias nuevas.

Adicionalmente, todos los endpoints que devuelven `Response` con `mimetype='multipart/x-mixed-replace'` se construyen con `direct_passthrough=True` y la cabecera `X-Accel-Buffering: no`. La primera evita que Werkzeug copie internamente el cuerpo de la respuesta; la segunda instruye a *proxies* intermedios (nginx, en futuros despliegues con balanceador) que no almacenen en *buffer* el flujo.

### 6.4.7 Acceso a base de datos y *pool* de conexiones

El acceso a PostgreSQL se realiza a través del *singleton* `DatabaseManager` ([connection.py](backend/app/database/connection.py)), que expone un `scoped_session` con `pool_size=20`, `max_overflow=30` y `pool_timeout=30` configurables vía `.env`. Estos valores son holgados para un sistema con un máximo de cuatro cámaras y unos cientos de eventos diarios. El patrón de uso recomendado en todo el código es:

```python
with db_manager.get_session() as session:
    # operaciones sobre session
    session.commit()
```

El gestor de contexto se encarga del `rollback()` ante excepciones y del `close()` al finalizar, lo cual evita fugas de conexión en escenarios de error. Los repositorios (`CameraRepository`, `EventRepository`, `RecordingRepository`) heredan de `BaseRepository` y proporcionan operaciones idiomáticas (filtros, paginación, conteos), aislando el resto del sistema de los detalles de SQLAlchemy.

---

## 6.5 Detección inteligente, eventos y grabación

### 6.5.1 Motion detection como puerta de entrada a YOLO

Ejecutar inferencia YOLOv8 sobre cada cuadro de cada cámara es computacionalmente prohibitivo en hardware CPU sin GPU. Un YOLOv8n procesa aproximadamente entre dos y cinco cuadros por segundo en una CPU moderna, mientras que el sistema necesita atender hasta cuatro cámaras a 15 fps. Para resolver este desbalance se introduce una etapa previa de **detección de movimiento** ([motion_detector.py](backend/app/processing/motion/motion_detector.py)) que actúa como puerta de entrada barata: solo los cuadros con cambio detectable son sometidos a la red neuronal.

El algoritmo implementado es deliberadamente simple para minimizar el costo por cuadro:

1. Conversión del cuadro BGR a escala de grises mediante `cv2.cvtColor()`.
2. Suavizado con desenfoque Gaussiano de ventana 21×21 para mitigar el ruido del sensor.
3. Diferencia absoluta con el cuadro anterior (`cv2.absdiff`).
4. Umbralización binaria con valor 25 sobre 255.
5. Dilatación con kernel 3×3 dos veces, para conectar regiones adyacentes.
6. Cálculo del *score* como fracción de píxeles activos sobre el total.

Se considera que hay movimiento cuando el *score* supera el umbral `sensitivity`, cuyo valor por defecto se ajustó a **0.015** (1.5 % de los píxeles) tras observaciones empíricas con cámaras *dual-lens* reescaladas a 960×540, en las cuales las personas a media distancia ocupan una fracción reducida del cuadro. El valor previo de 0.02 resultaba demasiado restrictivo en este escenario. Cuando el flujo se interrumpe por más de cinco segundos, el detector se reinicia mediante `reset()` para no comparar contra un cuadro de referencia obsoleto, lo cual evitaría una avalancha de falsos positivos al restablecerse la conexión.

### 6.5.2 *AIScheduler*: arquitectura desacoplada

La clase `AIScheduler` ([ai_scheduler.py](backend/app/processing/ai/ai_scheduler.py)) orquesta la cadena `motion detection → inferencia → callback de evento`. Su diseño se rige por una decisión crítica: el procesamiento pesado **no debe ejecutarse en el hilo del distribuidor**, ya que ese hilo es compartido por todos los consumidores de cuadros y bloquearlo retrasaría también el *streaming* MJPEG y la grabación.

La solución consiste en separar el camino crítico en dos etapas:

1. **Callback ligero (`_on_frame`)** registrado en el `FrameDistributor`: recibe el cuadro, lo encola en un `InferenceQueue` de tamaño cinco y retorna inmediatamente. Aplica un *throttling* por intervalo (uno de cada N cuadros) según el modo seleccionado.

2. **Hilo dedicado (`_inference_worker`)**: extrae tareas de la cola, ejecuta el detector de movimiento y, solo si hay movimiento, invoca `YLOModelPool.detect()`. Las detecciones se agrupan por clase y se entregan al callback registrado, aplicando además un *cooldown* configurable por clase para evitar disparos múltiples.

El intervalo de inferencia es configurable mediante las variables `AI_INFERENCE_INTERVAL_LOW` y `AI_INFERENCE_INTERVAL_HIGH`. Los valores por defecto, seis y tres respectivamente, equivalen a aproximadamente 2.5 y 5 inferencias por segundo a 15 fps. Estos valores se redujeron desde los 12/6 iniciales tras observar pérdidas de eventos cortos —por ejemplo, una persona que cruza el campo de visión en menos de un segundo no era detectada cuando solo se evaluaba uno de cada 12 cuadros.

**Cooldown por clase.** La estructura `_last_alert_time: Dict[str, float]` registra, por nombre de clase (`person`, `vehicle`, etc.), el instante de la última alerta entregada. Cuando llega una nueva detección, si el tiempo transcurrido es menor que `cooldown_seconds`, la alerta se silencia. En los tests el valor de *cooldown* se ajusta a cero para no perder eventos durante validaciones rápidas; en producción se recomienda 30 segundos como mínimo. La estructura `_cooldown_lock` protege el diccionario frente a accesos concurrentes desde múltiples cámaras (no aplica con el diseño actual, pero queda preparado para evolución futura).

**Validación temprana de dependencias.** Antes incluso de instanciar el *scheduler*, el método `activate_ai()` invoca `YLOModelPool.check_dependencies()`, que verifica la presencia de `torch` y `ultralytics`. Si alguno falta, lanza `RuntimeError` con el mensaje exacto. El endpoint HTTP correspondiente atrapa esta excepción y la traduce a un código `503` con `error_code: AI_DEPENDENCIES_MISSING`, mostrando al operador la línea `pip install` exacta. Esta política sustituye un escenario anterior en el cual el hilo *worker* fallaba silenciosamente al importar `ultralytics`, sin que el cliente HTTP recibiera ninguna indicación.

**Cámaras *dual-lens* y selección de lente.** La activación de IA admite el parámetro `lens` con valores `main`, `l1` o `l2`. Para cámaras estándar siempre se utiliza `main`; para cámaras *dual-lens* el operador puede activar IA en uno o ambos lentes por separado, instanciando un *scheduler* independiente por par `(camera_id, lens)`. La persistencia del flag `Camera.has_ai = True` ocurre solo si al menos un lente sigue activo, evitando que la desactivación de un único lente reescriba el estado de la cámara.

### 6.5.3 *Event bus*: publicación y consumo desacoplado

El componente `EventManager` ([event_manager.py](backend/app/events/event_manager.py)) implementa el patrón **publicador-suscriptor** sobre la dataclass `EventData`. Sus características principales son:

- **Suscripción por tipo y global.** Los suscriptores pueden registrarse para un `event_type` específico (`subscribe()`) o para todos los eventos del sistema (`subscribe_all()`).

- **Despacho asíncrono.** El método `emit()` no invoca los callbacks de forma síncrona; en lugar de ello los encola en un `ThreadPoolExecutor` con ocho hilos (`thread_name_prefix="EventManager"`). Este *executor* sustituyó un diseño anterior en el cual cada `emit()` creaba un `threading.Thread` *daemon* por callback. En ráfagas de eventos, ese diseño generaba decenas de hilos simultáneos esperando treinta segundos a la API de Telegram, agotando el *file descriptor* del proceso.

- **Aislamiento de fallos.** Cada callback se ejecuta dentro de `_safe_call()`, que captura y registra cualquier excepción sin propagar. Un suscriptor defectuoso (por ejemplo, un servicio caído) no impide la entrega del evento al resto.

Los suscriptores efectivamente registrados durante el arranque son:

- `EventService._on_event`: persiste el evento en la tabla `events` y deriva el ruteo a `NotificationRouter`.
- `RecordingManager._on_event`: dispara la grabación del clip de evento.
- `MetricsCollector`: actualiza estadísticas en memoria para el endpoint `/metrics`.
- `TelegramNotifier` (modo activo, opcional): envío directo de alerta. En la configuración por defecto este suscriptor está **inactivo** porque el ruteo se realiza ya a través de `NotificationRouter`, evitando duplicaciones.

### 6.5.4 Persistencia del *snapshot* antes de la emisión

Un problema operativo identificado durante las pruebas integradas se relacionaba con el momento en que el *snapshot* del evento se grababa a disco. La implementación original delegaba esta responsabilidad en `EventService._on_event`, suscriptor del *event bus*. Sin embargo, dado que `EventManager.emit()` dispara **todos** los suscriptores en paralelo, no podía garantizarse que el `EventService` terminara de escribir el archivo antes de que `TelegramNotifier` intentara leerlo. El resultado era una condición de carrera silenciosa: en aproximadamente el 30 % de los eventos, el mensaje de Telegram se enviaba sin la fotografía adjunta porque el archivo aún no existía.

La solución implementada en [ai_service.py](backend/app/services/ai_service.py) consiste en **guardar el *snapshot* antes de la emisión**, dentro del método `_handle_detection`. El archivo se genera con ruta absoluta:

```python
snap_dir = os.path.abspath(os.path.join(
    settings.RECORDINGS_PATH, "snapshots", str(camera_id)
))
os.makedirs(snap_dir, exist_ok=True)
snap_filename = f"{int(ts)}_{safe_type}.jpg"
snapshot_path = os.path.join(snap_dir, snap_filename)
cv2.imwrite(snapshot_path, frame)
```

La ruta resultante se incluye en `event_data.metadata["snapshot_path"]` antes de invocar `event_manager.emit(event_data)`. Esto garantiza que todos los suscriptores reciban una referencia válida al archivo ya escrito. `EventService` conserva un fallback que escribe el *snapshot* si la metadata llegara vacía, manteniendo retrocompatibilidad con orígenes de eventos que no apliquen la nueva política.

Es importante notar que la ruta se construye en su **forma absoluta** desde el comienzo. Si se guardara como ruta relativa (por ejemplo, `recordings/snapshots/5/123.jpg`), el `TelegramNotifier` realizaría posteriormente un `os.path.join(base_absoluta, "recordings/snapshots/...")` produciendo una ruta inválida con doble prefijo (`/.../recordings/recordings/snapshots/...`). Este error específico ocurrió en una iteración previa y motivó la convención de "absoluto desde el origen".

### 6.5.5 Grabación: continuo + *splice* de eventos

El módulo `RecordingManager` ([recording_manager.py](backend/app/recording/recording_manager.py)) implementa dos modos de grabación complementarios:

**Modo continuo.** Cuando una cámara se activa, se inicia automáticamente un hilo de grabación continua segmentado en archivos MP4 de duración fija (`CONTINUOUS_SEGMENT_DURATION = 120 s`). El nombre del archivo codifica la marca de tiempo UTC del segmento (`cam_<id>_<YYYYMMDD>_<HHMMSS>.mp4`), lo cual permite localizar segmentos por fecha sin necesidad de consulta a base de datos. Cada segmento queda registrado en la tabla `recordings` con su `start_time`, `end_time`, `file_path`, `file_size_bytes` y `duration_seconds`.

**Modo evento (*splice*).** Cuando llega un evento (movimiento, persona, vehículo), el método `_on_event` determina si la cámara dispone de grabación continua activa:

- *Si sí*, ejecuta un **splice** mediante `ffmpeg -c copy`: localiza los segmentos continuos que cubren la ventana `[t-10 s, t+10 s]`, los concatena y recorta la ventana exacta, todo **sin recodificar**. El costo es de aproximadamente 200 ms por evento. Esta estrategia elimina la necesidad de mantener un *pre-buffer* en memoria del orden de 500 MB por cámara, lo que constituía el modelo anterior.

- *Si no*, recurre a un **pre-buffer en memoria** circular de 75 cuadros (`PRE_BUFFER_SIZE`), aproximadamente cinco segundos a 15 fps, y recodifica con `libx264 -preset ultrafast -crf 28`. Esta vía es de fallback y se considera no preferida.

**Compatibilidad FFmpeg 8.** El comando de re-encoding utilizado en el modo de fallback se actualizó para ser compatible con FFmpeg 8.0:

```
ffmpeg -y -hide_banner -loglevel warning
       -f rawvideo -vcodec rawvideo -s WxH -pix_fmt bgr24
       -framerate 15 -i pipe:0
       -an -c:v libx264 -pix_fmt yuv420p
       -preset ultrafast -crf 28
       -fps_mode cfr -r 15 -movflags +faststart
       <salida>
```

La bandera `-fps_mode cfr` sustituye al `-vsync cfr` deprecado en FFmpeg 8: utilizando esta última, el archivo resultante presentaba duración nula (`0 s`) en los reproductores. La bandera `-movflags +faststart` reubica el átomo `moov` al inicio del archivo, lo cual habilita el reproductor de Telegram para iniciar la reproducción progresiva sin descargar el archivo entero. Finalmente, `-an` elimina la pista de audio (la cámara no entrega audio en este modelo) y `-pix_fmt yuv420p` garantiza compatibilidad con el reproductor de Telegram, que rechaza `yuv444p`.

**Parsing UTC de nombres de segmento.** Una sutileza identificada durante las pruebas: el módulo de *splice* parseaba el nombre del segmento sin información de zona horaria, lo cual provocaba que `datetime.strptime(...).timestamp()` interpretara el valor como hora local. En máquinas con `tzinfo` distinto de UTC el resultado era una ventana de búsqueda incorrecta y `_find_continuous_segments` retornaba lista vacía. La corrección añade explícitamente `replace(tzinfo=timezone.utc)`:

```python
dt_utc = datetime.strptime(
    f"{parts[-2]}_{parts[-1]}", "%Y%m%d_%H%M%S"
).replace(tzinfo=timezone.utc)
seg_start = dt_utc.timestamp()
```

**Validación de duración.** Tras finalizar el clip, el método auxiliar `_probe_duration()` ejecuta `ffprobe` para obtener la duración real del MP4 generado. Si la duración es inferior a un segundo, se registra una advertencia y el archivo se descarta. Esta validación protege contra el caso en que FFmpeg crea un contenedor vacío por fallo de E/S sin reportar el error en `returncode`.

### 6.5.6 Pasarela de notificaciones

El sistema implementa una **pasarela de notificaciones** dividida en dos capas, articuladas por el `NotificationRouter` ([notification_router.py](backend/app/services/notification_router.py)).

**Capa de ruteo.** `NotificationRouter.route_event()` se invoca desde `EventService._on_event` cuando el evento ya ha sido persistido en base de datos. El ruteo se ejecuta en un `ThreadPoolExecutor` de cuatro hilos (`thread_name_prefix="NotifyRouter"`) para no bloquear el suscriptor. Internamente, `_process_event()` abre **una única sesión** de SQLAlchemy y la propaga a todos los métodos auxiliares (`_notify_user_if_applies`, `_check_day`, `_send_notification`). Antes de esta optimización, el flujo abría hasta treinta sesiones por evento con diez usuarios, agotando el *pool* de conexiones bajo carga sostenida.

Para cada cámara, el ruteador identifica los usuarios elegibles:

1. El propietario de la cámara (`Camera.owner_id`).
2. Todos los usuarios con `UserCameraPermission.can_view = True` sobre esa cámara.

A continuación, para cada usuario:

1. Recupera sus `NotificationPreference` para el `event_type`. Las preferencias específicas por cámara prevalecen sobre las generales.
2. Aplica el filtro de horario (`schedule_start`/`schedule_end`, soportando ventanas que cruzan la medianoche).
3. Aplica el filtro de día de la semana (`NotificationDay`).
4. Verifica el *cooldown* (`{user_id}:{camera_id}:{event_type}`, 300 segundos por defecto).
5. Para cada `NotificationChannel` configurado, despacha al *notifier* correspondiente.

**Patrón *Circuit Breaker*.** Tanto el canal Telegram como el canal FCM se invocan a través de un objeto `CircuitBreaker` que mantiene un contador de fallos consecutivos. Tras cinco fallos consecutivos el circuito pasa a estado `OPEN` y rechaza las invocaciones siguientes durante sesenta segundos. Transcurrido ese plazo se entra en estado `HALF_OPEN`: la siguiente invocación, si tiene éxito, restablece el estado `CLOSED`; si falla, mantiene `OPEN`. Esta protección evita que el sistema pague treinta segundos de *timeout* HTTP por cada notificación cuando la API de Telegram está indisponible.

**Capa de envío (Telegram).** `TelegramNotifier` ([telegram_notifier.py](backend/app/notifications/telegram_notifier.py)) realiza la llamada efectiva al *Bot API*. La clase soporta varios `chat_id` simultáneos (configurable como lista separada por comas en `SystemConfig`) y dos métodos principales:

- `send_event_notification(event_data)`: si la metadata contiene `snapshot_path` válido, envía la foto con `sendPhoto` y la información del evento como `caption`; en caso contrario envía solo texto con `sendMessage`. Tres reintentos con *backoff* exponencial.
- `send_event_with_media(event_data)`: secuencia foto + vídeo, donde el clip se entrega como segundo mensaje cuando el `clip_path` esté disponible.

**Validación de ruta del *snapshot*.** El método `_validate_snapshot_path()` aplica dos verificaciones críticas:

1. *Prevención de path traversal*: el `os.path.realpath()` del archivo debe estar contenido en el directorio base `settings.RECORDINGS_PATH`. Cualquier `..` en la ruta hace fallar la verificación.
2. *Existencia*: si el archivo no existe físicamente, se registra el evento como advertencia y se envía la notificación solo con texto.

El directorio base se inicializa con `os.path.abspath(settings.RECORDINGS_PATH)`. En la implementación original se calculaba mediante una ruta relativa al módulo, lo cual producía una ruta diferente (`<root>/backend/recordings/`) inexistente en el sistema de archivos: la verificación fallaba siempre y todas las notificaciones se enviaban sin foto. La corrección a la ruta absoluta basada en `settings` resolvió este problema y se acompañó de logs de diagnóstico cuando la validación falla, indicando la ruta exacta evaluada.

**Bot interactivo (long-polling).** Adicionalmente al canal de salida, el módulo `TelegramBotPoller` ([telegram_bot_poller.py](backend/app/notifications/telegram_bot_poller.py)) realiza *long-polling* sobre `getUpdates` para implementar comandos entrantes:

- `/start` — bienvenida y código de vinculación.
- `/vincular <CÓDIGO>` — vincula el chat con un usuario tras introducir el código mostrado en el cliente de escritorio.
- `/desvincular` — desactiva la vinculación.
- `/estado` — devuelve estado de las cámaras del usuario.
- `/ayuda` — lista de comandos.

El `offset` de `getUpdates` se persiste en `SystemConfig` para no reprocesar mensajes ya consumidos tras un reinicio del *backend*.

---

## 6.6 API REST, seguridad y modelo multi-tenant

### 6.6.1 Organización por *blueprints*

La API REST se organiza en *blueprints* de Flask, cada uno responsable de un dominio funcional concreto. Esta segmentación facilita la lectura del código, permite habilitar o deshabilitar funcionalidades específicas mediante la función `safe_register()` y separa con claridad las superficies de prueba unitaria. La Tabla 6.3 enumera los *blueprints* registrados durante el arranque, con su prefijo de URL y propósito.

**Tabla 6.3. *Blueprints* de la API REST**

| *Blueprint* | Prefijo | Responsabilidad |
|---|---|---|
| `auth_bp` | `/api/v1/auth` | Login, *refresh*, *logout*, `/me`, cambio de contraseña, QR de conexión. |
| `cameras_bp` | `/api/v1/cameras` | CRUD de cámaras, control de captura, *stream* MJPEG, *snapshot*, latencia. |
| `events_bp` | `/api/v1/events` | Listado, filtrado, reconocimiento y descarga de eventos. |
| `recordings_bp` | `/api/v1/recordings` | Listado, descarga y eliminación de grabaciones. |
| `ai_bp` | `/api/v1/ai` | Activar/desactivar IA por cámara y lente, cambiar modo, consultar estado. |
| `users_bp` | `/api/v1/users` | Gestión de usuarios (admin) y consulta del perfil propio. |
| `permissions_bp` | `/api/v1/permissions` | Concesión y revocación de `UserCameraPermission`. |
| `devices_bp` | `/api/v1/devices` | Registro de `MobileDevice` con su token FCM. |
| `notifications_bp` | `/api/v1/notifications` | Preferencias por usuario, evento y cámara. |
| `qr_bp` | `/api/v1/qr` | Generación de `LinkToken` y QR para vincular dispositivos móviles. |
| `telegram_link_bp` | `/api/v1/telegram` | Códigos temporales para vincular cuenta con Telegram. |
| `storage_bp` | `/api/v1/storage` | Consulta de uso de almacenamiento y políticas de retención. |
| `metrics_bp` | `/api/v1/metrics` | Métricas internas en formato JSON (cuadros/s, latencia, *workers*). |
| `system_bp` | `/api/v1/system` | Estado del sistema, *health check*, configuración global. |
| `mobile_bp` | `/api/v1/mobile` | Endpoints específicos del cliente móvil (sincronización de catálogo). |

El registro se realiza mediante una función defensiva:

```python
def safe_register(bp, name):
    try:
        app.register_blueprint(bp)
        blueprints_registered.append(name)
    except Exception as e:
        logger.error(f"Error registrando '{name}': {e}")
```

El endpoint `GET /api/v1/health` devuelve la lista `blueprints_registered`, lo cual permite al operador verificar en producción cuáles módulos llegaron a registrarse satisfactoriamente sin recurrir a los archivos de log.

### 6.6.2 Modelo de autenticación con JWT

El sistema utiliza **Flask-JWT-Extended** con un esquema de dos tokens: un `access_token` de duración corta (15 minutos por defecto, configurable mediante `JWT_ACCESS_TOKEN_MINUTES`) y un `refresh_token` de duración prolongada (siete días por defecto, configurable mediante `JWT_REFRESH_TOKEN_DAYS`). Las claves de firma `SECRET_KEY` y `JWT_SECRET_KEY` se cargan desde el archivo `.env`. Cuando la variable `APP_ENV=production` está activa, el arranque del sistema falla con `RuntimeError` si las claves siguen siendo los valores por defecto: esta política de *fail-fast* garantiza que un despliegue accidental sin secretos seguros se detecte en el primer arranque y no semanas después por una auditoría de seguridad.

**Flujo de autenticación.** El cliente obtiene los dos tokens mediante `POST /api/v1/auth/login` proporcionando `username` y `password`. La verificación delega en `AuthService.login()`, que utiliza la función `cryptography.fernet`-libre `bcrypt`-equivalente almacenada en `password_hash`. Los siguientes ataques se mitigan automáticamente:

- *Fuerza bruta:* el *rate limiter* (`Flask-Limiter`) restringe `login` a un número limitado de intentos por minuto por IP. La configuración exacta vive en [rate_limiter.py](backend/app/api/middleware/rate_limiter.py).
- *Enumeración de usuarios:* las respuestas a credenciales inválidas retornan siempre el mismo mensaje genérico (`"Credenciales inválidas"`) y código `401`, sin distinguir entre "usuario inexistente" y "contraseña incorrecta".
- *Filtración de excepciones:* el `try/except` general devuelve el mensaje genérico `"Error interno del servidor"`; el detalle con `exc_info=True` solo se registra en el log del servidor.

**Token de acceso y *claims*.** El *payload* del token incluye los *claims* estándar (`sub`, `iat`, `exp`, `jti`) y un *claim* adicional `role` que contiene `"admin"` o `"user"`. Este *claim* se utiliza en el decorador `@require_admin` ([helpers.py](backend/app/api/helpers.py)) para proteger los endpoints de administración del sistema (gestión de usuarios, configuración global). El decorador interrumpe la petición con código `403` si el *claim* no coincide.

**Refresh token y rotación.** El endpoint `POST /api/v1/auth/refresh` exige el *refresh token* mediante el parámetro `@jwt_required(refresh=True)` y emite un nuevo *access token*. Verifica adicionalmente que el usuario siga existiendo y esté activo (`is_active = True`), lo cual permite revocar el acceso de un usuario simplemente desactivándolo en la base de datos sin necesidad de propagar el cambio a tokens emitidos previamente.

### 6.6.3 Revocación de tokens (*blocklist*)

El *blocklist* implementado en `JWTBlocklist` ([jwt_blocklist.py](backend/app/core/jwt_blocklist.py)) cubre los escenarios de cierre de sesión (`/logout`) y cambio de contraseña. Su diseño combina dos capas:

- **Capa en memoria.** Un `dict[str, float]` mapea cada `jti` revocado con el *timestamp* de su `exp` original. La verificación es *O(1)* y se invoca en el `token_in_blocklist_loader` registrado al arrancar Flask-JWT-Extended.

- **Capa persistente.** La tabla `revoked_tokens` almacena los mismos `jti` con su `expires_at`, el `user_id` opcional y el motivo (`logout`, `password_change`). El método `_persist_revocation` realiza el `INSERT` con política *best-effort*: si la persistencia falla por alguna razón (tabla aún sin migrar, sesión bloqueada), la revocación queda activa en memoria y se registra una advertencia. El flujo de cierre de sesión no se interrumpe.

Al arrancar el sistema, `rehydrate_from_db()` carga en memoria los `jti` cuyo `expires_at` aún no ha vencido y, oportunísticamente, purga las entradas ya vencidas mediante un único `DELETE`. La rehidratación se ejecuta una sola vez por proceso, lo cual evita la situación en que un cierre de sesión en *t = 0* se "olvida" si el *backend* se reinicia en *t = 5 min*.

Un *garbage collector* implícito (`_maybe_gc`) recorre el diccionario en memoria cada 60 segundos y elimina las entradas vencidas. Esta política mantiene acotado el consumo de memoria incluso con un volumen alto de cierres de sesión.

### 6.6.4 Modelo multi-tenant: cámaras y permisos

El sistema implementa un modelo *multi-tenant* donde cada cámara pertenece a un usuario propietario (`Camera.owner_id`) y puede compartirse con otros usuarios mediante registros en `UserCameraPermission`. Esta tabla incluye los siguientes flags granulares:

- `can_view`: visualización del *stream* en vivo y de grabaciones.
- `can_control_ptz`: control de cabezal motorizado.
- `can_control_leds`: encendido/apagado de iluminación.
- `can_control_audio`: bidireccional de audio (no implementado en el cliente actual; reservado).
- `can_download_recordings`: descarga de archivos MP4.

La lógica de verificación se centraliza en `PermissionService` y se expone mediante el decorador `@require_camera_permission`, que admite el flag concreto a verificar. Un ejemplo simplificado de uso, presente en [cameras.py](backend/app/api/routes/cameras.py), es:

```python
@cameras_bp.route("/<int:camera_id>/ptz", methods=["POST"])
@jwt_required()
@require_camera_permission("can_control_ptz")
def control_ptz(camera_id):
    ...
```

El decorador resuelve el `user_id` desde el JWT, consulta la combinación de `owner_id` y `UserCameraPermission`, y solo permite continuar si el usuario es el propietario **o** si tiene el *flag* solicitado. La política de "propietario implícitamente concede todos los permisos" simplifica la administración personal sin sacrificar la granularidad cuando se comparte con terceros.

### 6.6.5 *Rate limiting* y CORS

El componente `Flask-Limiter` se inicializa con una política por defecto adecuada para un sistema doméstico (300 solicitudes por minuto por IP), con reglas específicas más estrictas en los endpoints de autenticación (`login`, `refresh`). El almacenamiento del contador es en memoria, suficiente para el modelo de proceso único.

CORS se configura con **lista blanca explícita** de orígenes permitidos, sin admitir el comodín `*`. Los orígenes habilitados corresponden a:

- El cliente de escritorio cuando se ejecuta en *modo desarrollo* (`http://localhost:<puerto>`).
- La aplicación móvil en su modo *debug*.

Para entornos de producción, la lista se ajusta mediante la variable de entorno `CORS_ALLOWED_ORIGINS`. Esta configuración previene ataques CSRF desde sitios web maliciosos que intenten realizar peticiones autenticadas hacia el *backend* en LAN.

### 6.6.6 Descubrimiento ONVIF y registro de cámaras

El módulo `OnvifDiscovery` ([onvif_discovery.py](backend/app/cameras/onvif_discovery.py)) descubre cámaras ONVIF en la subred local mediante el protocolo **WS-Discovery** (multicast a `239.255.255.250:3702`). Una vez detectado un dispositivo, el módulo intenta:

1. Obtener el endpoint ONVIF (`GetDeviceInformation`, `GetCapabilities`).
2. Listar los perfiles de medios (`GetProfiles`) y construir las URLs RTSP para cada uno (`GetStreamUri`).
3. Detectar capacidades adicionales (PTZ, *imaging*, *audio*) consultando los *namespaces* soportados.

**Diccionario de credenciales comunes.** Muchas cámaras de gama doméstica se distribuyen con credenciales por defecto que el usuario no modifica. El módulo mantiene una lista `COMMON_CREDENTIALS` con pares (usuario, contraseña) ordenados por frecuencia: `admin/admin`, `admin/12345`, `admin/<vacío>`, `root/root`, entre otros. Si el operador no proporciona credenciales explícitamente, el descubridor itera la lista hasta encontrar un par válido o agotar la lista. Esta lista deriva de bases de datos públicas (cirt.net/passwords) y de telemetría de las botnets Mirai y Hajime, que documentaron exhaustivamente las credenciales por defecto de cámaras IP chinas baratas (XiongMai, TVT, etc.).

**Fallback RTSP.** Si la negociación ONVIF falla pero el puerto RTSP (554) está accesible, el sistema registra la cámara con `connection_type = "rtsp_fallback"` y guarda la URL RTSP detectada como `fallback_url`. Esta política permite operar cámaras con firmware ONVIF defectuoso o incompleto sin descartarlas.

**Silenciado de logs ruidosos.** La biblioteca `wsdiscovery` emite advertencias constantes del tipo `"daemon - WARNING - could not find handler for: _handle_probe"`. Estas advertencias responden al hecho de que el descubridor recibe paquetes WS-Discovery emitidos por otros dispositivos de la red (que no son respuestas a sus propias *probes*). Para evitar contaminar el log del *backend*, el módulo eleva el nivel del *logger* `daemon` a `ERROR`.

### 6.6.7 Vinculación rápida de dispositivos móviles vía QR

Para evitar que el usuario tenga que introducir manualmente la URL del servidor, las credenciales y un token largo en la aplicación móvil, el sistema implementa un mecanismo de **vinculación por QR**. El flujo, implementado en `QRService` y el *blueprint* `qr_bp` ([qr.py](backend/app/api/routes/qr.py)), es el siguiente:

1. El usuario, ya autenticado en el cliente de escritorio, solicita un código QR mediante `POST /api/v1/qr/generate`.
2. El servicio genera un `LinkToken` aleatorio (UUID4 truncado a 36 caracteres) y lo persiste en la tabla `link_tokens` con `expires_at = now + 5 min` y `used = False`. El token se asocia con el `user_id` del solicitante.
3. La biblioteca `qrcode` codifica un JSON con la forma `{"server": "<IP>:<puerto>", "token": "<uuid>"}` y devuelve la imagen PNG resultante. El cliente la muestra mediante `qr_link_dialog`.
4. La aplicación móvil escanea el código, extrae los campos, contacta el endpoint `POST /api/v1/qr/exchange` con el token, y recibe a cambio un par `access_token` + `refresh_token` JWT estándar.
5. El registro `LinkToken` se marca como `used = True` y deja de ser válido (es de un solo uso).

Esta arquitectura ofrece varias ventajas frente al esquema *username/password* tradicional:

- La aplicación móvil **nunca** conoce la contraseña del usuario.
- El QR caduca en cinco minutos, limitando la ventana de oportunidad si la imagen se filtra.
- Cada dispositivo móvil recibe su propio par de tokens, y por tanto su propia trazabilidad en `AuditLog`.

### 6.6.8 Vinculación con Telegram

Análogamente, la vinculación con Telegram emplea la tabla `telegram_verification_codes`. El cliente solicita un código de seis caracteres mediante `POST /api/v1/telegram/code`, el sistema lo persiste con `expires_at = now + 10 min` y se lo muestra al usuario. Este envía el comando `/vincular <CÓDIGO>` al bot de Telegram desde su cuenta. El módulo `TelegramBotPoller` recibe el comando, valida el código, marca el registro como `used = True` y crea una entrada en `UserTelegramChat` con el `telegram_chat_id` del remitente. A partir de ese momento, las notificaciones del usuario se entregan a ese chat.

La separación de las tablas `telegram_verification_codes` y `user_telegram_chats` permite que un usuario tenga **múltiples** chats vinculados (por ejemplo, un chat personal y un canal familiar), añadiendo simplemente otra entrada en la segunda tabla. El campo `is_active` en `UserTelegramChat` permite desactivar individualmente un chat sin perder el histórico.

### 6.6.9 Endpoint de captura de cuadro (*snapshot*)

El endpoint `GET /api/v1/cameras/<id>/snapshot` devuelve la imagen JPEG del cuadro más reciente. La implementación:

1. Localiza el `CircularFrameBuffer` correspondiente.
2. Invoca `get_latest()` y obtiene `frame_data.frame`.
3. Codifica con `cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])`.
4. Devuelve los *bytes* con `mimetype="image/jpeg"`.

El endpoint cuenta con `@require_camera_permission("can_view")` y no requiere mantener una conexión persistente, por lo cual es la vía recomendada para integraciones externas (paneles de control, dashboards). Es preferible al MJPEG para clientes que solo necesitan una imagen periódica.

### 6.6.10 Endpoints de control PTZ y LEDs

Las cámaras ONVIF que reportan capacidad PTZ exponen un servicio SOAP cuyo *binding* se encapsula en `PTZController`. Los endpoints del *backend* envuelven los comandos de movimiento (`MoveLeft`, `MoveRight`, `Stop`, `GoToPreset`, `SetPreset`) y exigen el flag `can_control_ptz`. Para evitar comandos solapados desde múltiples clientes simultáneos, el servicio `PTZLockService` aplica un *lock* por cámara con tiempo de vida limitado (15 segundos): un cliente que tome el *lock* tiene exclusividad durante ese tiempo, y se renueva implícitamente con cada comando.

El control de LEDs blancas/IR (presentes en cámaras con `has_leds = True`) se delega en `LedController` mediante peticiones HTTP propietarias al firmware de la cámara. Las URLs y *payloads* exactos varían según el fabricante; el módulo aplica un *dispatcher* basado en el campo `Camera.connection_type` para enrutar al *handler* adecuado.

---

## 6.7 Cliente de escritorio (PySide6)

### 6.7.1 Justificación tecnológica

El cliente de escritorio se implementó con **PySide6** (Qt 6.8.2) como interfaz administrativa principal. Esta elección obedece a tres criterios:

- *Coherencia tecnológica:* mantener Python en ambos extremos (servidor y cliente principal) reduce el costo de mantenimiento y facilita compartir modelos de datos —aunque, como se verá, los modelos del cliente y los del *backend* son clases distintas que comparten esquema.
- *Riqueza de controles nativos:* Qt provee `QGraphicsView`, `QMediaPlayer`, *widgets* de timeline para *playback*, *joysticks* virtuales y diálogos complejos que serían costosos de replicar en una aplicación web.
- *Aspecto nativo en Windows:* el estilo `Fusion` aplicado en [main.py](desktop_app/src/main.py) y la carga selectiva de la fuente *Inter* (con *fallback* a Segoe UI en Windows, San Francisco en macOS y Ubuntu en Linux) garantizan una experiencia visual consistente con el sistema operativo.

La aplicación se distribuye como un proceso independiente que se comunica con el *backend* exclusivamente a través de la API REST y de los flujos MJPEG, sin compartir memoria, base de datos ni archivos de configuración con el servidor. Esto preserva el modelo cliente-servidor incluso cuando ambos se ejecutan en el mismo equipo, lo cual ocurre en el despliegue de referencia.

### 6.7.2 Organización de la interfaz

La ventana principal ([main_window.py](desktop_app/src/ui/main_window.py)) se estructura como una pila de vistas (`QStackedWidget`) navegables desde una barra lateral. Las vistas registradas son:

**Tabla 6.4. Vistas del cliente de escritorio**

| Vista | Archivo | Propósito |
|---|---|---|
| Login | [login_view.py](desktop_app/src/ui/views/login_view.py) | Autenticación contra `POST /api/v1/auth/login`. |
| Live View | [live_view.py](desktop_app/src/ui/views/live_view.py) | Visualización en vivo con cuadrícula adaptativa y paginación. |
| Gestión de cámaras | [camera_management_view.py](desktop_app/src/ui/views/camera_management_view.py) | Descubrimiento ONVIF, registro, edición, configuración dual-lens. |
| Control de cámara | [camera_control_view.py](desktop_app/src/ui/views/camera_control_view.py) | PTZ, LEDs, audio, presets. |
| Playback | [playback_view.py](desktop_app/src/ui/views/playback_view.py) | Reproducción de grabaciones con timeline interactivo. |
| Eventos | [events_view.py](desktop_app/src/ui/views/events_view.py) | Listado con filtros, snapshot ampliado, reconocimiento. |
| Notificaciones | [notifications_view.py](desktop_app/src/ui/views/notifications_view.py) | Configuración de preferencias por usuario, evento y cámara. |
| Usuarios | [users_view.py](desktop_app/src/ui/views/users_view.py) | Gestión de cuentas (solo administrador). |
| Permisos | [permissions_view.py](desktop_app/src/ui/views/permissions_view.py) | Concesión y revocación de `UserCameraPermission`. |
| Sistema | [system_view.py](desktop_app/src/ui/views/system_view.py) | Estado, métricas, *latency probe*. |
| Configuración | [settings_view.py](desktop_app/src/ui/views/settings_view.py) | Preferencias locales del cliente. |

A esto se suman diálogos especializados (`qr_link_dialog`, `telegram_link_dialog`, `info_dialog`, `onboarding_wizard`) y componentes reutilizables (`glass_card`, `timeline_widget`, `ptz_joystick`, `video_player`, `camera_control_panel`, `help_button`).

### 6.7.3 Cliente HTTP y *marshalling* al hilo principal

El módulo `APIClient` ([api_client.py](desktop_app/src/services/api_client.py)) implementa el patrón *singleton* y encapsula todas las llamadas REST al *backend*. Sus características destacables son:

- **Almacenamiento de tokens en memoria.** Los `access_token` y `refresh_token` JWT se mantienen en el atributo `_tokens` de tipo `AuthTokens`. La aplicación no persiste los tokens en disco para evitar exposición ante usuarios secundarios del mismo equipo; el usuario debe autenticarse al iniciar la aplicación.
- **Refresh automático.** Cuando una petición devuelve `401`, el cliente intenta refrescar el `access_token` mediante el `refresh_token`. Si el *refresh* también falla, emite la señal `auth_error` que la ventana principal escucha para llevar al usuario a la vista de Login.
- **Ejecución en *thread pool*.** Cada solicitud se envuelve en un `APIWorker` (subclase de `QRunnable`) y se delega al `QThreadPool` global. Esto evita bloquear el hilo de la interfaz mientras espera la respuesta del servidor.
- **Marshalling al hilo principal.** Los *callbacks* de respuesta no se invocan directamente desde el hilo del *pool*, ya que un *callback* puede manipular *widgets* y Qt requiere que esas manipulaciones ocurran en el hilo principal. La clase `_CallbackDispatcher` vive en el hilo principal y conecta una `Signal` con `Qt.QueuedConnection`, lo cual garantiza que la invocación del *callback* se *encole* en el bucle de eventos de Qt y se ejecute en el contexto correcto.

Este patrón resuelve uno de los errores más sutiles de PySide6: la generación de mensajes `"Cannot set parent, new parent is in different thread"` cuando una respuesta HTTP intenta modificar la jerarquía de *widgets* desde un hilo secundario.

### 6.7.4 Streaming en vivo: arquitectura *pull-based*

La visualización en vivo es la funcionalidad con mayor impacto sobre la percepción de calidad del producto. La implementación original empleaba un esquema *push-based* tradicional: la clase `MJPEGThread` decodificaba cada cuadro JPEG y emitía una `Signal(Frame)` que el `CameraWidget` recibía y dibujaba en el hilo principal. Este esquema resultó inadecuado por una razón fundamental del bucle de eventos de Qt:

> *Las señales con `Qt.QueuedConnection` se encolan en una cola interna que el bucle de eventos procesa en serie. Si el productor (el hilo MJPEG) emite señales más rápido que el consumidor (el hilo principal renderizando) puede procesarlas, la cola crece sin límite. Cada nueva señal añade latencia al cuadro que finalmente se muestra, hasta el punto de que el usuario observa video con un retraso de varios segundos respecto a la cámara.*

Tras una refactorización completa, el cliente adopta una arquitectura ***pull-based*** ([video_streamer.py](desktop_app/src/services/video_streamer.py)):

1. El hilo `MJPEGThread` mantiene una **única** variable `_latest_pixmap` protegida por `QMutex`. Cuando decodifica un JPEG nuevo, **sobreescribe** el anterior. No emite ninguna señal.
2. La vista `live_view.py` programa un `QTimer` con intervalo de 67 ms (aproximadamente 15 cuadros por segundo, coincidente con el FPS del *streaming*). En cada disparo, invoca `MJPEGThread.pop_latest_pixmap(last_seq)` que devuelve el último cuadro y un número de secuencia.
3. Si el número de secuencia no ha cambiado desde la última lectura, el `QTimer` no redibuja; si ha cambiado, el cuadro se asigna al `QLabel` correspondiente.

Esta arquitectura proporciona **drop-newest natural**: si la decodificación produce diez cuadros entre dos disparos del temporizador, los nueve primeros se descartan implícitamente al ser sobreescritos. La cola de Qt no acumula nada porque no se emiten señales por cuadro. La latencia percibida en el cliente se reduce de varios segundos a aproximadamente la suma de un período del temporizador (67 ms) más la latencia del *backend* y la red.

### 6.7.5 Tuning de red TCP en el cliente

Adicionalmente al diseño *pull-based*, el cliente aplica ajustes a la conexión TCP que consume el flujo MJPEG. Estos ajustes se inyectan en el adaptador `HTTPAdapter` de la sesión `requests`:

- `SO_RCVBUF = 128 KB`: el *buffer* de recepción del *kernel* se limita a un tamaño equivalente aproximadamente a un solo cuadro JPEG. Esto evita que, tras una pausa en el procesamiento del cliente (por ejemplo, durante un cambio de vista), el *kernel* acumule muchos cuadros encolados que serían entregados retrospectivamente al reanudar.
- `TCP_NODELAY = 1`: desactiva el algoritmo de Nagle en el lado cliente, complementando el desactivado previamente en el lado servidor.

Adicionalmente, el procesamiento del *buffer* recibido implementa **drop-newest a nivel de parser**: si el *buffer* contiene varios JPEG completos (lo cual ocurre tras una congelación temporal del consumidor o un *burst* de la red), el código parsea todos y conserva únicamente el último para decodificación y publicación en `_latest_pixmap`. La política se documenta explícitamente con el comentario:

> *"Si en el buffer hay varios frames acumulados (porque hubo backlog en la red o el GUI iba detrás), parseamos todos pero solo decodificamos y emitimos el ÚLTIMO. Esto mantiene la latencia baja después de un freeze de FFmpeg."*

### 6.7.6 Cuadrícula adaptativa y paginación

La vista en vivo soporta entre una y cuatro cámaras simultáneamente. El número de columnas se ajusta automáticamente al ancho disponible:

- 1 cámara: una columna a pantalla completa.
- 2 cámaras: dos columnas, manteniendo la relación de aspecto 16:9.
- 3-4 cámaras: cuadrícula 2×2.

Cuando el usuario tiene más cámaras que celdas visibles, se habilita un control de paginación inferior ◀ Pág X/Y ▶ que conmuta entre subconjuntos. Para evitar la destrucción y reconstrucción costosas de los `CameraWidget`, la vista mantiene una *cache* de *widgets* por `camera_id` y solo añade/elimina los necesarios. Los *widgets* descartados invocan previamente `release_resources()` para detener su `MJPEGThread` antes de programar `deleteLater()` mediante el bucle de eventos.

La selección de escalado utiliza `Qt.FastTransformation` en lugar de `Qt.SmoothTransformation`. La diferencia visual es mínima en el caso de redimensionado de cuadros de video, pero el ahorro de CPU es significativo al actualizar 60 *widgets* por segundo en una cuadrícula 2×2.

### 6.7.7 Selección de lente en cámaras *dual-lens*

Cuando el usuario añade una cámara *dual-lens*, la vista `camera_management_view.py` presenta una previsualización con dos botones de radio (L1 / L2) que conmutan el `stream_id` enviado en la URL del MJPEG. La previsualización emplea el mismo mecanismo *pull-based* descrito en la sección 6.7.4 y permite al usuario validar la correcta detección de los dos lentes antes de confirmar el registro de la cámara.

### 6.7.8 *Playback* con timeline interactivo

La vista de *playback* ([playback_view.py](desktop_app/src/ui/views/playback_view.py)) combina un reproductor `QMediaPlayer` con un componente personalizado `TimelineWidget` ([timeline_widget.py](desktop_app/src/ui/components/timeline_widget.py)) que representa las grabaciones disponibles en una franja horaria horizontal. El usuario puede:

1. Seleccionar la fecha mediante un `QCalendarWidget`.
2. Visualizar las barras de los segmentos continuos y de los clips de evento, diferenciadas por color.
3. Hacer clic en cualquier punto del *timeline* para saltar a esa marca de tiempo en el reproductor.
4. Descargar el archivo MP4 si tiene el permiso `can_download_recordings`.

El módulo `PlaybackService` ([playback_service.py](desktop_app/src/services/playback_service.py)) consulta los endpoints `GET /api/v1/recordings` y `GET /api/v1/events` y consolida los resultados en una lista cronológica. La reproducción consume el endpoint `GET /api/v1/recordings/<id>/file`, que sirve el archivo MP4 con `Content-Type: video/mp4` y soporte para *byte-range* HTTP (necesario para el *seek*).

### 6.7.9 Diálogos de vinculación

Los diálogos `qr_link_dialog.py`, `telegram_link_dialog.py` e `info_dialog.py` implementan el flujo de vinculación descrito en 6.6.7 y 6.6.8. La interacción del usuario es deliberadamente lineal:

1. Botón "Vincular dispositivo móvil" en la barra de herramientas.
2. El diálogo solicita un QR al *backend*, lo muestra durante cinco minutos y cuenta el tiempo restante.
3. Cuando la aplicación móvil completa el intercambio, el *backend* registra el `LinkToken.used = True` y el *polling* del diálogo (cada dos segundos a `GET /api/v1/devices/<uuid>`) detecta el nuevo dispositivo y muestra un mensaje de éxito.

El diálogo `onboarding_wizard.py` guía al primer arranque del cliente: configuración del servidor, registro de la primera cámara mediante descubrimiento ONVIF, vinculación opcional de Telegram. Esta secuencia se inspira en patrones de UX establecidos por aplicaciones similares (UniFi Protect, Synology Surveillance Station) y reduce significativamente la fricción de la primera experiencia.

### 6.7.10 Botones de ayuda contextual

Cada vista incluye un componente `HelpButton` ([help_button.py](desktop_app/src/ui/components/help_button.py)) que, al pulsarse, muestra un `QToolTip` o un `info_dialog` con explicaciones específicas. Los textos se centralizan en [help_texts.py](desktop_app/src/ui/help_texts.py) en formato Markdown, lo cual facilita su mantenimiento y futura traducción.

---

## 6.8 Aplicación móvil (Android)

> **Nota editorial:** Las secciones 6.8.x se conservan literalmente conforme al documento original consolidado por el equipo de desarrollo móvil. El contenido de esta sección **no se modifica** respecto a `Capitulo6_Desarrollo_v2.pdf`. A continuación se incluye un marcador para que durante la consolidación final en formato Word se reemplace este bloque por las páginas correspondientes del documento original. Los apartados que abarca son:
>
> - 6.8.1 Plataforma y *stack* tecnológico de la aplicación móvil.
> - 6.8.2 Arquitectura interna (MVVM, *Repository* pattern).
> - 6.8.3 Autenticación: vinculación por QR y refresco de tokens.
> - 6.8.4 Consumo de la API REST.
> - 6.8.5 Reproducción de *streams* MJPEG/HLS en el cliente Android.
> - 6.8.6 Notificaciones *push* con Firebase Cloud Messaging.
> - 6.8.7 Interfaz de usuario y navegación.
> - 6.8.8 Persistencia local y modo *offline*.
> - 6.8.9 Pruebas y empaquetado.

**[CONTENIDO DE LA APLICACIÓN MÓVIL — INSERTAR AQUÍ EL TEXTO ORIGINAL SIN MODIFICAR]**

---

## 6.9 Verificación y validación

### 6.9.1 Estrategia de pruebas

La validación del sistema se estructuró en tres niveles que se ejecutan con frecuencias y propósitos distintos:

- **Pruebas unitarias**, centradas en componentes con lógica autónoma verificable sin recurrir a recursos externos (cámaras, base de datos, red). Se ejecutan en cada modificación del código.
- **Pruebas de integración**, que validan la interacción entre módulos (`AIScheduler ↔ EventManager ↔ EventService ↔ NotificationRouter`) ante eventos simulados.
- **Pruebas funcionales y de carga** sobre el sistema desplegado, utilizando cámaras reales y clientes simulados.

El conjunto canónico de pruebas se ejecuta mediante el *script* `backend/tests/run_tests.py` ([run_tests.py](backend/tests/run_tests.py)), construido sobre el módulo `unittest` de la biblioteca estándar de Python en lugar de *pytest*. Esta elección obedece al criterio de minimizar dependencias externas en la cadena de validación: `unittest` está disponible en cualquier instalación estándar de Python sin instalación adicional. El *script* admite los modificadores `-v` para modo verboso y `-t <TestName>` para ejecutar una clase o método individual:

```powershell
python backend/tests/run_tests.py
python backend/tests/run_tests.py -v
python backend/tests/run_tests.py -t TestCircularFrameBuffer
python backend/tests/run_tests.py -t TestCircularFrameBuffer.test_clear
```

Cabe destacar que la carpeta `backend/tests/` contiene también un conjunto de *scripts* de exploración (`a.py`, `g.py`, `l.py`, `nt.py`, `nvt_prueba.py`, `test-pipeline-completo.py`, `todo.py`, entre otros) que se utilizaron durante el desarrollo iterativo para probar comportamientos específicos en aislamiento. Estos archivos **no** forman parte de la suite y no se garantiza su ejecución limpia; los nuevos casos de prueba se incorporan exclusivamente a `run_tests.py`.

### 6.9.2 Componentes verificados

La Tabla 6.5 enumera los principales casos de prueba unitarios incluidos en la *suite*, agrupados por componente.

**Tabla 6.5. Cobertura de pruebas unitarias**

| Componente | Casos representativos |
|---|---|
| `CircularFrameBuffer` | Inserción, descarte cuando se alcanza `maxsize`, conteo de `dropped_frames`, reinicio con `clear()`. |
| `MotionDetector` | Devolución de "sin movimiento" en el primer cuadro, detección sobre cuadros generados sintéticamente, comportamiento tras `reset()`. |
| `StorageManager` | Cálculo de uso de disco, eliminación de archivos antiguos, respeto del límite `MAX_STORAGE_GB`. |
| `ImageOptimizer` | Codificación JPEG con calidad configurable, redimensionado conservando aspecto. |
| `ConsistencyChecker` | Detección de huérfanos entre tabla `recordings` y sistema de archivos, generación de informe. |
| `JWTBlocklist` | Revocación, expiración natural, GC periódico, persistencia mediante *mock* del `db_manager`. |
| `PermissionService` | Resolución de propietario implícito vs. permisos explícitos, denegación correcta. |
| `CircuitBreaker` | Transiciones `CLOSED → OPEN → HALF_OPEN → CLOSED`, *fast fail* en estado abierto. |

Las pruebas siguen el patrón **Arrange-Act-Assert** y, cuando dependen de recursos externos (base de datos, sistema de archivos), utilizan `tempfile.TemporaryDirectory()` y *mocks* sobre `db_manager.get_session` para evitar contaminación del entorno.

### 6.9.3 Pruebas funcionales con cámaras reales

Las pruebas funcionales se llevaron a cabo contra el modelo de referencia XiongMai XM535 instalado en una red local Ethernet 1 Gbps, ejecutando el *backend* sobre Windows 11 con CPU Intel y sin GPU dedicada. Los escenarios probados se documentan a continuación.

**Escenario 1: arranque en frío y primera previsualización.** Se midió el tiempo desde el arranque del *backend* hasta la entrega del primer cuadro al cliente. Con la configuración optimizada (`probesize=32`, `analyzeduration=0`, cache de resolución en `Camera.resolution_width/height`), el tiempo medio observado es de aproximadamente 1.2 s, frente a los 5–7 s del diseño anterior basado en `cv2.VideoCapture`.

**Escenario 2: latencia en régimen permanente.** El endpoint `GET /api/v1/cameras/<id>/latency` reporta percentil 50 de `frame_age` por debajo de 200 ms y percentil 95 por debajo de 450 ms en la configuración por defecto (`MJPEG_MAX_WIDTH=960`, `MJPEG_QUALITY=75`). Estos valores se mantuvieron estables durante sesiones de hasta dos horas continuas.

**Escenario 3: detección y notificación.** Se realizaron 30 pruebas controladas atravesando manualmente el campo de visión de una cámara con IA activa. La tasa de detección de personas (clase `person` de YOLOv8n) superó el 95 % con umbral de confianza `AI_CONFIDENCE=0.35`. La notificación de Telegram, incluyendo la fotografía adjunta, se entregó al usuario en un tiempo medio de 3.1 s desde el instante de la detección. El clip de 20 segundos (10 s pre + 10 s post) extraído mediante *splice* llegó como segundo mensaje en un tiempo medio de 4.7 s adicionales.

**Escenario 4: reconexión ante caída de cámara.** Se desconectó la cámara durante intervalos crecientes (5 s, 30 s, 2 min) para verificar el comportamiento del *watchdog* y del *backoff* exponencial. En todos los casos el flujo se restableció sin intervención manual y el sistema registró eventos `camera_offline` y `camera_reconnected` correctamente.

**Escenario 5: carga con 10 clientes simultáneos.** Se ejecutó el *script* `backend/tests/phantom_runner.py`, que lanza diez procesos cliente independientes consumiendo el MJPEG de la misma cámara. La elección de utilizar procesos en lugar de hilos fue deliberada: los hilos clientes competían por el GIL de Python y producían artefactos de medición que no se observaban en la operación real. La tasa efectiva de cuadros por cliente se mantuvo por encima de 12 fps y la latencia *p95* se mantuvo por debajo de 700 ms.

### 6.9.4 Diagnóstico y observabilidad

Para facilitar el diagnóstico en producción, el sistema expone los siguientes mecanismos:

- **Endpoint `/api/v1/health`**: estado de los *blueprints* registrados, base de datos, ejecutor global, motor de eventos.
- **Endpoint `/api/v1/metrics`**: contadores por cámara (cuadros procesados, cuadros distribuidos, descartes, clientes activos), uso del *executor* global, estadísticas del *event bus*.
- **Endpoint `/api/v1/cameras/<id>/latency`**: percentiles de `encode` y `frame_age` por cámara y `stream_id`.
- **Logs estructurados**: niveles `INFO`, `WARNING` y `ERROR` con prefijos por componente (`[AI cam=1]`, `[TELEGRAM]`, `[MJPEG (1, 'main')]`) que permiten filtrar con `findstr` o `Select-String` en PowerShell.
- **Snapshots de depuración**: la opción `_save_debug_snapshot` del `FFmpegWorker` permite guardar el primer cuadro decodificado en `debug_snapshots/cam_<id>_original.jpg` para verificar que la cámara entrega imagen útil incluso si los consumidores fallan.

Estos mecanismos resolvieron en la práctica la mayoría de los incidentes durante el desarrollo. La hipótesis típica ("se ve lento") se reduce a una comparación de `frame_age` (latencia del *backend*) y el GOP de la cámara (latencia inherente al codificador de la cámara). Tal como se documenta en el archivo `CLAUDE.md` del repositorio:

> *Si `frame_age` es inferior a 500 ms, el backend está fluido y el delay viene de la cámara (GOP) o del cliente/red. Para reducir el GOP, el operador debe acceder al panel web de la cámara y ajustar el "I-frame interval" o "Keyframe interval" a un valor menor o igual a 30 cuadros.*

---

## 6.10 Despliegue y operación

### 6.10.1 Instalación del entorno

Las dependencias se instalan en dos pasos para respetar la precedencia de PyTorch:

```powershell
pip install torch==2.6.0+cpu torchvision==0.21.0+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

El binario FFmpeg debe instalarse en el sistema (por ejemplo, mediante `winget install ffmpeg` en Windows o `apt install ffmpeg` en Linux) y estar accesible en `PATH`. PostgreSQL 16 puede instalarse localmente o como contenedor; las credenciales se configuran en el archivo `.env`. Un ejemplo mínimo de `.env` es:

```
APP_ENV=production
SECRET_KEY=<32+ chars>
JWT_SECRET_KEY=<32+ chars>
POSTGRES_HOST=localhost
POSTGRES_DB=nvr_db
POSTGRES_USER=nvr_user
POSTGRES_PASSWORD=<contraseña fuerte>
RECORDINGS_PATH=C:/nvr/recordings
MAX_STORAGE_GB=100
TELEGRAM_BOT_TOKEN=<token del bot>
```

### 6.10.2 Arranque del *backend*

En entornos de desarrollo, el servidor de Flask es suficiente:

```powershell
python backend/app/main.py
```

Para entornos productivos se utiliza Gunicorn con la configuración definida en [gunicorn.conf.py](gunicorn.conf.py):

```powershell
gunicorn -c gunicorn.conf.py "backend.app.main:create_app()"
```

Los parámetros relevantes de la configuración son:

- `workers = 1` y `worker_class = "gevent"`: un único proceso multiplexado por corrutinas, condición ineludible por la presencia de *singletons* con estado en memoria (cámaras, *event bus*, *pool* de modelos, *executor* global).
- `worker_connections = 1000`: capacidad de conexiones HTTP concurrentes.
- `timeout = 120`: tolerancia ante operaciones largas (descarga de grabaciones, generación de QR con red lenta).
- `keepalive = 5`: tiempo de espera entre solicitudes en la misma conexión, ajustado para no penalizar al cliente móvil con conexiones móviles fluctuantes.
- `max_requests = 1000` con jitter de 50: reciclaje proactivo del *worker* tras un volumen alto de solicitudes, mitigando posibles fugas de memoria acumulativas.

### 6.10.3 Política de almacenamiento

El servicio `StorageManager` ejecuta un hilo de mantenimiento que cada hora verifica el uso del directorio `RECORDINGS_PATH`. Si supera el umbral `MAX_STORAGE_GB`, elimina las grabaciones más antiguas hasta liberar al menos el 10 % del espacio. El borrado actualiza la tabla `recordings`, sincronizando el estado lógico con el físico. Adicionalmente, el `ConsistencyChecker` ejecuta una verificación cruzada cada seis horas: detecta archivos huérfanos (en disco pero no en base de datos) y entradas huérfanas (en base de datos pero sin archivo), reportando el conteo en el endpoint `/api/v1/storage`.

### 6.10.4 Operación habitual

El operador interactúa con el sistema a través del cliente de escritorio. Los flujos cotidianos son:

1. *Iniciar sesión* con credenciales personales.
2. *Visualizar* las cámaras en la vista en vivo.
3. *Recibir notificaciones* en Telegram cuando ocurre un evento. La notificación incluye la fotografía instantánea y, segundos después, un clip de 20 segundos centrado en el momento del evento.
4. *Revisar* eventos pasados en la vista correspondiente, descargando el clip si se requiere.
5. *Consultar* grabaciones continuas mediante el *playback* con *timeline*.

Para incidentes operativos (cámara desconectada, IA con falsos positivos, almacenamiento al borde de la capacidad), el sistema expone notificaciones específicas y el endpoint `/api/v1/health` ofrece una visión consolidada del estado.

---

## 6.11 Conclusión del capítulo

El presente capítulo ha documentado la construcción del Sistema de Videovigilancia LAN tal como se halla en su versión actual. El proceso comprendió la definición de una pila tecnológica coherente, el diseño de una arquitectura cliente-servidor de proceso único, la implementación del *pipeline* de captura, distribución, codificación y *streaming* de cuadros, la integración de detección inteligente basada en YOLOv8 con detección de movimiento como puerta de entrada, la persistencia transaccional de eventos y grabaciones en PostgreSQL, la construcción de una pasarela de notificaciones con doble canal (Telegram y FCM), el desarrollo de una API REST autenticada con JWT y modelo *multi-tenant*, y la implementación de un cliente de escritorio en PySide6 con arquitectura *pull-based* para baja latencia. La aplicación móvil se integra con la misma API y se documenta en su sección correspondiente.

Las decisiones de diseño documentadas a lo largo del capítulo reflejan un proceso iterativo de descubrimiento de restricciones reales: el efecto de Nagle sobre la latencia del MJPEG, la condición de carrera entre suscriptores del *event bus* al leer el *snapshot*, la deriva de zona horaria en el *parser* de nombres de segmento continuo, la diferencia de comportamiento entre `-vsync cfr` y `-fps_mode cfr` en FFmpeg 8, y la divergencia entre las arquitecturas *push-based* y *pull-based* en Qt al manipular ráfagas de cuadros. Cada uno de estos hallazgos motivó cambios concretos en el código, documentados *in situ* mediante comentarios que explican el motivo (no solo el comportamiento) de cada decisión.

El sistema resultante cumple con los requisitos funcionales y no funcionales establecidos en el capítulo anterior: visualización en vivo con latencia inferior a un segundo en LAN, detección y notificación con foto en menos de cuatro segundos, grabación continua con almacenamiento controlado y notificaciones diferenciadas por usuario, evento y horario. Los componentes implementados ofrecen una base extensible: futuras evoluciones podrían incorporar GPU para múltiples cámaras con IA simultánea, reubicación del estado compartido en Redis para soporte multi-proceso, o cifrado *at-rest* de las grabaciones para entornos con requisitos regulatorios adicionales. Estas líneas se abordan en el Capítulo 8.

---





