-- ===========================================================================
--  Sistema NVR/VMS — Creación del rol y la base de datos (PostgreSQL)
--
--  Ejecutar UNA sola vez como superusuario (postgres) ANTES de restaurar el
--  respaldo. Crea el rol de aplicación y la base de datos vacía.
--
--  Uso (Windows, PostgreSQL 17):
--    "C:\Program Files\PostgreSQL\17\bin\psql" -U postgres -f crear_bd.sql
--
--  Cambia la contraseña por una propia y refléjala en .env (POSTGRES_PASSWORD).
-- ===========================================================================

-- Rol de aplicación (no superusuario)
CREATE ROLE nvr_user WITH LOGIN PASSWORD 'cambia-esta-password';

-- Base de datos propiedad del rol de aplicación, en UTF-8
CREATE DATABASE nvr_db OWNER nvr_user ENCODING 'UTF8' TEMPLATE template0;

-- Privilegios sobre la base de datos
GRANT ALL PRIVILEGES ON DATABASE nvr_db TO nvr_user;
