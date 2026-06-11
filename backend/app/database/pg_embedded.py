"""
PostgreSQL EMBEBIDO para la distribución empaquetada (.exe).

Levanta un servidor PostgreSQL local cuyos binarios viajan dentro del bundle
(carpeta `pgsql/`), usando un directorio de datos en %LOCALAPPDATA%\\NVR-VMS\\pgdata.
La PRIMERA vez ejecuta `initdb` y crea la base/rol; en arranques posteriores
solo levanta el servidor ya inicializado.

Solo actúa cuando EMBEDDED_PG=1 (lo activa `runtime.bootstrap()` en modo
congelado). En DESARROLLO no hace absolutamente nada: se sigue usando el
PostgreSQL del sistema configurado en el `.env`.
"""
import os
import socket
import subprocess
import time
import logging
from pathlib import Path

from backend.app.runtime import bundle_dir, app_data_dir

logger = logging.getLogger(__name__)

# En Windows, evita abrir ventanas de consola al lanzar los binarios de PG.
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class EmbeddedPostgres:
    """Gestiona el ciclo de vida de un PostgreSQL local empaquetado (singleton)."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._started = False
        self._proc = None      # postgres.exe como hijo directo
        self._logfh = None     # handle del logfile del servidor
        self.bin = bundle_dir() / "pgsql" / "bin"
        self.data = app_data_dir() / "pgdata"
        self.logfile = app_data_dir() / "logs" / "postgres.log"
        self.port = int(os.getenv("POSTGRES_PORT", "5433"))
        self.user = os.getenv("POSTGRES_USER", "nvr_user")
        self.password = os.getenv("POSTGRES_PASSWORD", "nvr_local")
        self.db = os.getenv("POSTGRES_DB", "nvr_db")

    # ------------------------------------------------------------------ utils
    def enabled(self) -> bool:
        return os.getenv("EMBEDDED_PG") == "1" and (self.bin / self._exe_name("pg_ctl")).exists()

    @staticmethod
    def _exe_name(name: str) -> str:
        return name + (".exe" if os.name == "nt" else "")

    def _exe(self, name: str) -> str:
        return str(self.bin / self._exe_name(name))

    def _run(self, args, capture: bool = True, **kw):
        """subprocess.run con consola oculta y PGPASSWORD inyectado."""
        env = dict(os.environ)
        env.setdefault("PGPASSWORD", self.password)
        if capture:
            return subprocess.run(
                args, env=env, creationflags=_NO_WINDOW,
                capture_output=True, text=True, **kw,
            )
        # Sin PIPE: para procesos que dejan un demonio vivo (pg_ctl start), capturar
        # bloquearía a subprocess.run esperando un EOF que el demonio nunca produce.
        return subprocess.run(
            args, env=env, creationflags=_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw,
        )

    def _port_open(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            return s.connect_ex(("127.0.0.1", self.port)) == 0
        finally:
            s.close()

    # ------------------------------------------------------------------ ciclo
    def start(self) -> bool:
        """Inicializa (si hace falta) y arranca el servidor. Idempotente."""
        if not self.enabled():
            return False
        try:
            if self._port_open():
                logger.info("PostgreSQL embebido: el puerto %s ya responde; reutilizando", self.port)
                self._ensure_db()
                return True

            if not (self.data / "PG_VERSION").exists():
                self._initdb()

            self._start_server()
            self._wait_ready()
            self._ensure_db()
            self._started = True
            logger.info("PostgreSQL embebido arrancado en 127.0.0.1:%s", self.port)
            return True
        except Exception as e:
            logger.critical("No se pudo arrancar PostgreSQL embebido: %s", e, exc_info=True)
            raise

    def _start_server(self) -> None:
        """
        Lanza `postgres.exe` como HIJO DIRECTO del backend (vía Popen), no como
        demonio desligado de pg_ctl. Así, si el launcher cierra el backend con
        taskkill /T, el servidor se reapea con el árbol y no queda huérfano.
        Un postmaster muerto sin cerrar deja postmaster.pid: lo limpiamos si el
        puerto está libre (recuperación tras un cierre forzado anterior).
        """
        self.logfile.parent.mkdir(parents=True, exist_ok=True)
        stale = self.data / "postmaster.pid"
        if stale.exists() and not self._port_open():
            try:
                stale.unlink()
            except OSError:
                pass
        self._logfh = open(self.logfile, "a", encoding="utf-8")
        self._proc = subprocess.Popen(
            [self._exe("postgres"), "-D", str(self.data),
             "-p", str(self.port), "-c", "listen_addresses=127.0.0.1"],
            stdout=self._logfh, stderr=subprocess.STDOUT,
            creationflags=_NO_WINDOW,
        )

    def _initdb(self) -> None:
        logger.info("Inicializando cluster PostgreSQL embebido en %s", self.data)
        self.data.mkdir(parents=True, exist_ok=True)
        pwfile = app_data_dir() / "_pg_init_pw.txt"
        pwfile.write_text(self.password, encoding="utf-8")
        try:
            r = self._run([
                self._exe("initdb"),
                "-D", str(self.data),
                "-U", self.user,
                "-A", "scram-sha-256",
                "--pwfile", str(pwfile),
                "-E", "UTF8",
                "--locale=C",
            ])
            if r.returncode != 0:
                raise RuntimeError(f"initdb falló: {r.stderr or r.stdout}")
        finally:
            try:
                pwfile.unlink()
            except OSError:
                pass

        # Forzar escucha solo en localhost y en nuestro puerto.
        with open(self.data / "postgresql.conf", "a", encoding="utf-8") as f:
            f.write(f"\n# NVR-VMS embebido\nlisten_addresses = '127.0.0.1'\nport = {self.port}\n")

    def _wait_ready(self, timeout: int = 60) -> None:
        deadline = time.time() + timeout
        isready = self._exe("pg_isready")
        while time.time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError(
                    f"postgres.exe terminó al arrancar (código {self._proc.returncode}); "
                    f"revisa {self.logfile}"
                )
            r = self._run([isready, "-h", "127.0.0.1", "-p", str(self.port), "-U", self.user])
            if r.returncode == 0:
                return
            time.sleep(0.5)
        raise TimeoutError("PostgreSQL embebido no quedó listo a tiempo")

    def _ensure_db(self) -> None:
        """Crea la base de datos de la app si todavía no existe."""
        check = self._run([
            self._exe("psql"), "-h", "127.0.0.1", "-p", str(self.port),
            "-U", self.user, "-d", "postgres", "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname='{self.db}'",
        ])
        if check.returncode == 0 and check.stdout.strip() == "1":
            return
        created = self._run([
            self._exe("createdb"), "-h", "127.0.0.1", "-p", str(self.port),
            "-U", self.user, self.db,
        ])
        if created.returncode != 0 and "already exists" not in (created.stderr or ""):
            raise RuntimeError(f"createdb falló: {created.stderr or created.stdout}")
        logger.info("Base de datos '%s' creada en el PostgreSQL embebido", self.db)

    def stop(self) -> None:
        if not self._started:
            return
        try:
            # Apagado limpio (checkpoint WAL) con pg_ctl -m fast.
            self._run([
                self._exe("pg_ctl"), "-D", str(self.data),
                "-m", "fast", "-w", "-t", "20", "stop",
            ], capture=False)
            if self._proc is not None:
                try:
                    self._proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    self._proc.terminate()
            logger.info("PostgreSQL embebido detenido")
        except Exception as e:
            logger.error("Error deteniendo PostgreSQL embebido: %s", e)
            if self._proc is not None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
        finally:
            if self._logfh is not None:
                try:
                    self._logfh.close()
                except Exception:
                    pass
                self._logfh = None
            self._proc = None
            self._started = False


# Instancia global (singleton)
embedded_pg = EmbeddedPostgres()
