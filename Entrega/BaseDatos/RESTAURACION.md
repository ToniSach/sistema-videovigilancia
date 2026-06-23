# Base de Datos — Respaldo y Restauración

Sistema NVR/VMS · Motor **PostgreSQL 17** · Base de datos **`nvr_db`**

Esta carpeta contiene todo lo necesario para recrear la base de datos del sistema
para la **demostración** y la **evaluación**.

---

## 1. Motor y conexión

| Parámetro        | Valor por defecto |
|------------------|-------------------|
| Motor            | PostgreSQL 17.5   |
| Host             | `localhost`       |
| Puerto           | `5432`            |
| Base de datos    | `nvr_db`          |
| Usuario (rol)    | `nvr_user`        |
| Contraseña       | *(la que definas en `.env` → `POSTGRES_PASSWORD`)* |
| Driver (backend) | `postgresql+psycopg2://` |

> El backend lee estos valores de `.env` (claves `POSTGRES_*`). La cadena de
> conexión se arma en `backend/app/config.py → get_database_url()`.
>
> **Nota:** existe un `data/surveillance.db` (SQLite) en el repositorio que es
> **legacy y NO se usa**. La base de datos activa es exclusivamente PostgreSQL.

---

## 2. Archivos de esta carpeta

| Archivo                | Contenido |
|------------------------|-----------|
| `crear_bd.sql`         | Crea el rol `nvr_user` y la base `nvr_db` vacía (ejecutar como `postgres`). |
| `esquema_nvr_db.sql`   | Solo **estructura** (tablas, índices, constraints), sin datos. |
| `backup_nvr_db.sql`    | Respaldo **completo**: estructura **+ datos de demostración**. |

### Datos de demostración incluidos en `backup_nvr_db.sql`

| Tabla                      | Filas |
|----------------------------|------:|
| `users`                    | 3     |
| `cameras`                  | 2     |
| `events`                   | 13    |
| `recordings`               | 40    |
| `notification_channels`    | 4     |
| `notification_preferences` | 4     |
| `notification_days`        | 28    |
| `mobile_devices`           | 2     |
| `link_tokens`              | 2     |
| `system_config`            | 9     |
| `audit_logs`               | 7     |

> Las contraseñas de los usuarios están **hasheadas** (no hay credenciales en
> texto plano en el respaldo). Para la demo, inicia sesión con las credenciales
> de demostración conocidas o crea un nuevo administrador (ver sección 5).

---

## 3. Restauración rápida (recomendada)

Desde esta carpeta `Entrega/BaseDatos/`, con PostgreSQL ya instalado y corriendo:

```powershell
# Ruta de las herramientas de PostgreSQL 17 (ajústala si difiere)
$PG = "C:\Program Files\PostgreSQL\17\bin"

# 1) Crear el rol y la base de datos vacía (como superusuario 'postgres')
& "$PG\psql" -U postgres -f crear_bd.sql

# 2) Cargar estructura + datos de demostración
$env:PGPASSWORD = "la-password-de-nvr_user"
& "$PG\psql" -U nvr_user -h localhost -d nvr_db -f backup_nvr_db.sql
```

En Linux/Mac el procedimiento es idéntico usando `psql` desde el `PATH`.

---

## 4. Restauración solo del esquema (BD vacía)

Si solo quieres la estructura (la app irá creando datos sola):

```powershell
& "$PG\psql" -U postgres -f crear_bd.sql
$env:PGPASSWORD = "la-password-de-nvr_user"
& "$PG\psql" -U nvr_user -h localhost -d nvr_db -f esquema_nvr_db.sql
```

> El backend también ejecuta `Base.metadata.create_all()` al arrancar
> (`backend/app/main.py`), así que una base **vacía** se autoinicializa con
> solo lanzar el backend; este paso es opcional.

---

## 5. Crear un administrador para la demo (opcional)

Si necesitas un usuario administrador nuevo, lo más simple es registrarlo desde
la app de escritorio (pantalla de login → registro) o, si existe, mediante el
script de gestión del backend. Tras crearlo, asígnale el rol `admin` si hiciera
falta:

```sql
UPDATE users SET role = 'admin' WHERE username = 'tu_usuario';
```

---

## 6. Regenerar el respaldo (para el desarrollador)

Para volver a generar estos archivos con el estado actual de la BD:

```powershell
$PG = "C:\Program Files\PostgreSQL\17\bin"
$env:PGPASSWORD = "la-password-de-nvr_user"

# Respaldo completo (estructura + datos)
& "$PG\pg_dump" -h localhost -U nvr_user -d nvr_db --no-owner --no-privileges `
    --encoding=UTF8 -f backup_nvr_db.sql

# Solo esquema
& "$PG\pg_dump" -h localhost -U nvr_user -d nvr_db --no-owner --no-privileges `
    --schema-only --encoding=UTF8 -f esquema_nvr_db.sql
```

---

## 7. Migraciones (Alembic)

El proyecto incluye configuración de Alembic (`alembic.ini`,
`backend/app/database/migrations/`), pero el esquema se materializa en la
práctica con `create_all()` al arrancar el backend (esta base **no** usa la
tabla de control `alembic_version`). Para una gestión formal de cambios de
esquema en el futuro:

```powershell
alembic upgrade head
```
