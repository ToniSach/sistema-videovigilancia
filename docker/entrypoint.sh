#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Entrypoint del backend NVR.
#  1) Espera a que PostgreSQL acepte conexiones (evita el crash de arranque si
#     el contenedor de BD aún está inicializando).
#  2) Ejecuta el comando (por defecto: el servidor Flask, ver CMD del Dockerfile).
# Las tablas se crean solas en el arranque (Base.metadata.create_all en main.py),
# así que no es obligatorio correr `alembic upgrade head` para una BD nueva.
# ---------------------------------------------------------------------------
set -e

echo "[entrypoint] Esperando PostgreSQL en ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}..."
python - <<'PY'
import os, socket, sys, time
host = os.getenv("POSTGRES_HOST", "db")
port = int(os.getenv("POSTGRES_PORT", "5432"))
for _ in range(60):  # hasta ~60s
    try:
        with socket.create_connection((host, port), timeout=2):
            print("[entrypoint] PostgreSQL disponible.")
            sys.exit(0)
    except OSError:
        time.sleep(1)
print("[entrypoint] AVISO: PostgreSQL no respondió en 60s; arranco igualmente.",
      file=sys.stderr)
PY

exec "$@"
