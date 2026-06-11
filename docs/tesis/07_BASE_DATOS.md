# PARTE 7 — Base de Datos

> Motor: **PostgreSQL**. ORM: **SQLAlchemy 2.0** (`DeclarativeBase` + `Mapped[...]`). Definición en `backend/app/database/models.py`. Conexión en `connection.py` (`QueuePool`, 10 base + 20 overflow, `pool_pre_ping`, `pool_recycle=3600`). El esquema se crea con `Base.metadata.create_all()` al arrancar (Alembic disponible para producción).

## 7.1 Diagrama entidad-relación (resumen)

```
                 ┌──────────┐
                 │  users   │
                 └────┬─────┘
       owner_id (1:N) │ ├── permissions (1:N) ── user_camera_permissions ──┐
                      │ ├── devices (1:N) ── mobile_devices                │
                      │ ├── notification_preferences (1:N) ──┬─ channels   │
                      │ ├── telegram_chats (1:N) ── user_telegram_chats     │ (N:M usuario-cámara)
                      │ ├── telegram_verification_codes (1:N)               │
                      │ └── link_tokens (1:N)                               │
                      ▼                                                     ▼
                 ┌──────────┐ owner (N:1)                          ┌──────────┐
                 │ cameras  │◄─────────────────────────────────────│  (FK)    │
                 └────┬─────┘                                       └──────────┘
       camera_id (1:N)│ ├── events (1:N) ── notification_logs (1:N)
                      │ └── recordings (1:N)
   Independientes: system_config, audit_logs, revoked_tokens
```

## 7.2 Tablas (PK/FK, columnas, cardinalidades, justificación)

### 1) `users` — autenticación/autorización
- **PK:** `id`. **Único:** `username`.
- Columnas: `username` (String 50, UNIQUE NOT NULL), `password_hash` (String 255, PBKDF2:SHA256), `role` (String 20, default `"user"` ∈ {admin,user}), `is_active` (Bool, default True, *soft-delete*), `created_at` (DateTime).
- **Relaciones (1:N):** `cameras_owned`, `permissions`, `devices`, `notification_preferences`, `telegram_chats`.
- **Justificación:** `is_active` permite desactivar sin perder histórico; índice único en `username` → login O(log n); `role` como string para extender roles sin migración de enum.

### 2) `cameras` — dispositivos
- **PK:** `id`. **FK:** `owner_id → users.id` (nullable, index).
- Columnas: `name` (100), `ip_address` (45, IPv4/IPv6), `rtsp_url` (500, NOT NULL), `onvif_url` (500), `username`/`password` (100, credenciales — *TODO cifrar*), `profile_token` (100), `is_active`, `has_ai`/`has_ptz`/`has_leds`/`has_audio`/`is_dual_lens` (Bool), `resolution_width`(1920)/`resolution_height`(1080)/`fps`(15), `connection_type` (20: onvif|rtsp_fallback|manual|unreachable), `last_error_code` (50), `last_connected_at`, `fallback_url` (500), `created_at`.
- **Relaciones:** `owner` (N:1), `events`(1:N), `recordings`(1:N), `user_permissions`(1:N).
- **Justificación:** campos de **diagnóstico** (`connection_type`, `last_error_code`, `last_connected_at`, `fallback_url`) evitan depender del log; `is_dual_lens` activa el split l1/l2; flags `has_*` controlan qué UI mostrar.

### 3) `user_camera_permissions` — control de acceso granular (N:M)
- **PK:** `id`. **FK:** `user_id → users.id` (CASCADE, index), `camera_id → cameras.id` (CASCADE, index). **UNIQUE(user_id, camera_id)** + índice compuesto.
- Columnas: `can_view` (default True), `can_control_ptz`, `can_control_leds`, `can_control_audio`, `can_download_recordings` (default False), `created_at`.
- **Cardinalidad:** N:M (junction table entre usuarios y cámaras).
- **Justificación:** UNIQUE evita duplicados (permite *upsert*); CASCADE limpia permisos al borrar usuario/cámara; flags booleanos (no enum) → SQL limpio y extensible. La verificación es jerárquica: **admin → owner → este registro** (`PermissionService.check_permission`).

### 4) `events` — alertas IA/seguridad
- **PK:** `id`. **FK:** `camera_id → cameras.id` (CASCADE, NOT NULL).
- Columnas: `event_type` (50: motion|person|vehicle|camera_offline|camera_reconnected|tampering), `confidence` (Float 0–1), `snapshot_path` (500), `clip_path` (500), `acknowledged` (Bool default False), `created_at`.
- **Índices:** `(camera_id, created_at)`, `event_type`, `(acknowledged, created_at)`.
- **Relaciones:** `camera` (N:1), `notification_logs` (1:N).
- **Justificación:** los índices compuestos sirven a las queries típicas de la UI (timeline de una cámara; "no leídos recientes"); `confidence` guarda el score YOLO original; `acknowledged` = marcar leído sin borrar.

### 5) `recordings` — grabaciones
- **PK:** `id`. **FK:** `camera_id → cameras.id` (CASCADE, NOT NULL).
- Columnas: `start_time` (NOT NULL), `end_time` (nullable = en curso/crash), `file_path` (500), `file_size_bytes` (Integer default 0), `duration_seconds` (Float).
- **Índice:** `(camera_id, start_time)` → timeline rápido.
- **Justificación:** `end_time` nullable distingue grabaciones cerradas de las que siguen activas; `to_dict()` expone alias `started_at`/`filename` para compatibilidad con la app móvil.

### 6) `mobile_devices` — dispositivos móviles
- **PK:** `id`. **FK:** `user_id → users.id` (index). **UNIQUE/index:** `device_uuid`.
- Columnas: `device_uuid` (100, UNIQUE), `device_name`, `platform` (ios|android), `is_active`, `last_seen_at`, `created_at`, `refresh_token_hash` (255, NOT NULL).
- **Justificación:** se guarda el **hash** del refresh token (no el token) para poder **revocar** por dispositivo; `last_seen_at` detecta dispositivos muertos. (No usa FCM; notifica por WebSocket.)

### 7) `notification_preferences` — reglas de notificación
- **PK:** `id`. **FK:** `user_id → users.id` (CASCADE), `camera_id → cameras.id` (SET NULL, nullable). **UNIQUE(user_id, event_type, camera_id)**.
- Columnas: `event_type` (50), `camera_id` (NULL = global), `enabled` (default True), `schedule_start`/`schedule_end` (Time), `created_at`.
- **Relaciones (1:N, cascade):** `channels`, `days`.
- **Justificación:** `camera_id` NULL = preferencia global; `schedule_*` limita a horario; normalización a `channels`/`days`.

### 8) `notification_channels` — canales (Telegram/push)
- **PK:** `id`. **FK:** `preference_id → notification_preferences.id`.
- Columna: `channel` (telegram|push|email).
- **Justificación:** una preferencia puede entregar por varios canales a la vez.

### 9) `notification_days` — días activos
- **PK:** `id`. **FK:** `preference_id`. Columna `day_of_week` (0–6).
- **Justificación:** activar notificaciones sólo ciertos días (p.ej. L–V).

### 10) `telegram_verification_codes` — vinculación Telegram
- **PK:** `id`. **FK:** `user_id`. **UNIQUE/index:** `code`.
- Columnas: `code` (10), `expires_at` (index, +5 min), `used` (Bool), `created_at`.
- **Justificación:** token efímero de un solo uso para vincular sin OAuth.

### 11) `user_telegram_chats` — chats vinculados
- **PK:** `id`. **FK:** `user_id`. Columnas: `telegram_chat_id` (50, index), `telegram_username` (100), `linked_at`, `is_active`.
- **Justificación:** **sin UNIQUE** en `user_id` → un usuario puede tener varios chats (personal + grupo).

### 12) `notification_logs` — auditoría + cooldown
- **PK:** `id`. **FK:** `event_id`, `user_id`. Columnas: `channel`, `status` (sent|failed|pending), `error_message` (Text), `sent_at`, `cooldown_key` (200, index).
- **Justificación:** `cooldown_key = "camera:<id>:<event_type>"` deduplica alertas (anti-flood); `status` permite reintentos.

### 13) `link_tokens` — QR de onboarding móvil
- **PK:** `id`. **FK:** `user_id`. **UNIQUE:** `token` (UUID). Columnas: `expires_at` (+15 min), `used`, `created_at`.
- **Justificación:** registrar un móvil escaneando un QR del escritorio, sin teclear credenciales.

### 14) `system_config` — configuración persistente
- **PK:** `id`. **UNIQUE:** `key`. Columnas: `value` (Text), `updated_at` (onupdate).
- **Justificación:** overrides de `.env` en runtime para claves de `_DB_OVERRIDE_KEYS` (p.ej. `AI_CONFIDENCE`, `MAX_STORAGE_GB`) sin reiniciar.

### 15) `audit_logs` — trazabilidad de acciones
- **PK:** `id`. **FK:** `user_id` (nullable = sistema). Columnas: `action` (100), `resource_type`/`resource_id`, `details` (Text), `ip_address`, `user_agent`, `created_at`. **Índices:** `(user_id, created_at)`, `action`.
- **Justificación:** cumplimiento/forense ("¿quién borró esta grabación?"). *Mejora:* purga automática (>90 días).

### 16) `revoked_tokens` — blocklist JWT persistente
- **PK:** `id`. **UNIQUE:** `jti`. Columnas: `expires_at` (index), `revoked_at`, `user_id`, `reason` (logout|password_change|admin_revoke).
- **Justificación:** revocación que **sobrevive reinicios**; `JWTBlocklist.rehydrate_from_db()` la carga al arrancar; GC descarta `expires_at < now`.

## 7.3 Configuración del pool (defensa)

```python
create_engine(url, poolclass=QueuePool, pool_size=10, max_overflow=20,
              pool_pre_ping=True, pool_recycle=3600, echo=False)
```
- `pool_pre_ping=True`: valida la conexión antes de usarla (evita "server closed connection").
- `pool_recycle=3600`: recicla conexiones cada hora (evita timeouts del lado servidor).
- Sesiones con `@contextmanager`: commit al salir, rollback ante excepción, close siempre.

## 7.4 Justificación de diseño (visión normalizadora)

- **3FN** en lo esencial: las preferencias de notificación se normalizan en `channels`/`days` (en vez de columnas repetidas o CSV).
- **Multi-tenant por dos vías:** propiedad (`cameras.owner_id`) + compartición (`user_camera_permissions`), porque una cámara puede ser compartida.
- **Índices dirigidos por consultas reales** (no por defecto): `(camera_id, created_at)` para timelines; `cooldown_key` para anti-flood; `jti`/`expires_at` para blocklist.
- **CASCADE vs SET NULL** elegidos por semántica: borrar un usuario borra sus permisos (CASCADE) pero una preferencia global sobrevive a borrar una cámara (`camera_id` SET NULL).
- **Soft-delete** (`is_active`) en `users`/`cameras` para conservar auditoría.

## 7.5 Consultas importantes (ejemplos)

**Cámaras accesibles por un usuario (no admin):**
```sql
SELECT c.id FROM cameras c WHERE c.owner_id = :uid
UNION
SELECT p.camera_id FROM user_camera_permissions p
WHERE p.user_id = :uid AND p.can_view = TRUE;
```

**Timeline de eventos no reconocidos recientes de una cámara:**
```sql
SELECT id, event_type, confidence, snapshot_path, clip_path, created_at
FROM events
WHERE camera_id = :cid AND acknowledged = FALSE
ORDER BY created_at DESC
LIMIT 50;            -- usa índice (acknowledged, created_at) / (camera_id, created_at)
```

**Grabaciones de una cámara en un día (para la timeline de playback):**
```sql
SELECT id, start_time, end_time, file_path, duration_seconds, file_size_bytes
FROM recordings
WHERE camera_id = :cid
  AND start_time >= :day_start AND start_time < :day_end
ORDER BY start_time;  -- usa índice (camera_id, start_time)
```

**Anti-flood: ¿se notificó esta clase recientemente?**
```sql
SELECT 1 FROM notification_logs
WHERE cooldown_key = :key            -- 'camera:5:person'
  AND sent_at > (NOW() - INTERVAL '30 seconds')
LIMIT 1;
```

**Espacio total ocupado por cámara (para retención):**
```sql
SELECT camera_id, SUM(file_size_bytes) AS bytes, COUNT(*) AS n
FROM recordings GROUP BY camera_id ORDER BY bytes DESC;
```

**¿Token revocado? (blocklist):**
```sql
SELECT 1 FROM revoked_tokens
WHERE jti = :jti AND expires_at > NOW() LIMIT 1;
```

**Usuarios que deben recibir alerta de un evento (preferencias + horario):**
```sql
SELECT DISTINCT np.user_id
FROM notification_preferences np
JOIN notification_days nd ON nd.preference_id = np.id
JOIN notification_channels nc ON nc.preference_id = np.id
WHERE np.event_type = :etype
  AND (np.camera_id = :cid OR np.camera_id IS NULL)
  AND np.enabled = TRUE
  AND nd.day_of_week = :dow
  AND (np.schedule_start IS NULL OR :now_time BETWEEN np.schedule_start AND np.schedule_end);
```

## 7.6 Migraciones

- **Estado real:** el esquema se crea con `Base.metadata.create_all()` en `main.py`; existe infraestructura Alembic (`alembic.ini`, `migrations/`, `script.py.mako`) y un `manual_wipe_cameras.sql` para limpieza en pruebas.
- **Recomendación para producción/tesis:** generar migraciones versionadas (`alembic revision --autogenerate`) para evolucionar el esquema sin `create_all`, y poder hacer *downgrade*.
