-- ============================================================================
-- WIPE TOTAL DE CÁMARAS Y DEPENDENCIAS
-- ----------------------------------------------------------------------------
-- Uso: ejecutar con el backend DETENIDO para evitar condiciones de carrera
-- con grabaciones, eventos o workers FFmpeg activos.
--
-- Ejecutar:
--   psql -h <POSTGRES_HOST> -U <POSTGRES_USER> -d <POSTGRES_DB> \
--        -f backend/app/database/migrations/manual_wipe_cameras.sql
--
-- Lo que hace:
--   - Limpia FKs que apuntan a cameras: notification_logs, events, recordings,
--     user_camera_permissions, notification_preferences.
--   - Borra todas las cámaras.
--   - Resetea las secuencias de IDs para que el próximo POST empiece en 1.
--
-- Lo que NO hace:
--   - Eliminar archivos físicos en disco (recordings/<id>/...). Si quieres
--     limpiarlos, después de correr este SQL: rmdir /s /q recordings (Windows)
--     o rm -rf recordings/* (Linux). El consistency_checker barre huérfanos en
--     el siguiente arranque, pero si tienes muchos archivos es más rápido
--     borrarlos manualmente.
-- ============================================================================

BEGIN;

-- 1) notification_logs apuntan a events.id (CASCADE definido en modelo, pero
--    si la BD se creó antes del CASCADE, lo limpiamos a mano).
DELETE FROM notification_logs;

-- 2) notification_preferences: FK con ON DELETE SET NULL. Forzamos NULL para
--    preservar las preferencias globales del usuario sin atarlas a la cámara.
UPDATE notification_preferences SET camera_id = NULL WHERE camera_id IS NOT NULL;

-- 3) Permisos por cámara
DELETE FROM user_camera_permissions;

-- 4) Eventos
DELETE FROM events;

-- 5) Grabaciones (registros; los archivos físicos quedan en disco hasta que
--    el consistency_checker los detecte como huérfanos en el siguiente arranque)
DELETE FROM recordings;

-- 6) Cámaras
DELETE FROM cameras;

-- 7) Reset de secuencias (para que el próximo INSERT empiece en id=1)
ALTER SEQUENCE IF EXISTS cameras_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS events_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS recordings_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS user_camera_permissions_id_seq RESTART WITH 1;
ALTER SEQUENCE IF EXISTS notification_logs_id_seq RESTART WITH 1;

COMMIT;

-- Sanity check (debería devolver 0 en todas)
SELECT 'cameras' AS tabla, COUNT(*) AS filas FROM cameras
UNION ALL SELECT 'events', COUNT(*) FROM events
UNION ALL SELECT 'recordings', COUNT(*) FROM recordings
UNION ALL SELECT 'user_camera_permissions', COUNT(*) FROM user_camera_permissions
UNION ALL SELECT 'notification_logs', COUNT(*) FROM notification_logs
UNION ALL SELECT 'notification_preferences (camera_id NOT NULL)',
       COUNT(*) FROM notification_preferences WHERE camera_id IS NOT NULL;
