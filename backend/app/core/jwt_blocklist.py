"""
================================================================================
MÓDULO: core.jwt_blocklist — Revocación de JWT en dos capas (logout/cambio clave)
================================================================================

PROPÓSITO
    Mantener la lista de tokens JWT revocados para que un logout (o un cambio de
    contraseña) invalide tokens que aún no han expirado por su `exp`. flask_jwt_
    extended consulta `is_revoked(jti)` en cada request protegido.

RESPONSABILIDAD PRINCIPAL
    Capa primaria: diccionario en memoria {jti: exp_timestamp}, thread-safe,
    con GC automático que descarta entradas vencidas.

    Capa de persistencia opcional: tabla `revoked_tokens` en la BD principal.
    - Cuando se revoca un jti se intenta persistir (best-effort: si falla, sólo
      queda en memoria y se loguea warning).
    - Al arrancar `rehydrate_from_db()` carga los jti aún vigentes para que un
      logout no se "olvide" si el backend se reinicia 5 min después.

    Esto cubre el caso típico de LAN single-process. Para multi-proceso o alta
    sensibilidad → Redis con TTL (refactor menor en esta misma clase).

DEPENDENCIAS
    threading ................. lock e instancia thread-safe.
    database.connection ....... db_manager (sesiones) — import diferido.
    database.models.RevokedToken ... tabla de persistencia — import diferido.

COMPONENTES RELACIONADOS
    main.create_app() ......... registra @jwt.token_in_blocklist_loader →
                                is_revoked, y llama rehydrate_from_db() al
                                arrancar (paso 5 del pipeline #1).
    services.auth_service ..... llama revoke() en logout / cambio de contraseña.

PUNTO DE ENTRADA EN LA ARQUITECTURA
    Singleton global `jwt_blocklist` al pie del módulo. Estado vivo en memoria de
    proceso (encaja con la restricción de PROCESO ÚNICO); la BD es solo respaldo
    para sobrevivir reinicios.

PIPELINE(S)
    Pipeline #2 (Autenticación): revoke() al cerrar sesión; is_revoked() en cada
    request protegido; rehydrate_from_db() en el arranque (#1, paso 5).
================================================================================
"""
from __future__ import annotations

import threading
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class JWTBlocklist:
    """
    Registro de jti revocados, thread-safe, con persistencia opcional a BD.

    ROL: responder is_revoked(jti) para el loader de flask_jwt_extended y
    registrar revocaciones en logout/cambio de clave.

    SINGLETON (por qué): debe haber UN solo registro de revocaciones por proceso
    para que cualquier hilo que atienda un request vea las mismas revocaciones.
    Doble-check con `_instance_lock` para que sea seguro bajo concurrencia. El
    estado vive en memoria (encaja con PROCESO ÚNICO) y se respalda en la tabla
    `revoked_tokens` para sobrevivir reinicios.

    QUIÉN LO INSTANCIA / CONSUME: instancia global `jwt_blocklist` al pie del
    módulo; lo consumen el loader JWT (is_revoked) y AuthService (revoke).
    """
    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        """Devuelve el singleton, creándolo bajo lock la primera vez."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Inicializa el dict de revocados, el lock y el estado de GC/rehidrtado
        una sola vez (idempotente: protegido por el flag `_initialized`)."""
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._revoked: dict[str, float] = {}
        self._lock = threading.Lock()
        self._last_gc = time.time()
        self._rehydrated = False

    # ---- API principal ---------------------------------------------------

    def revoke(self, jti: str, exp: float, user_id: int | None = None,
                reason: str = "logout") -> None:
        """
        Revoca un token por su jti hasta su exp original (pipeline #2).

        Inputs: jti (id único del token), exp (timestamp UNIX de expiración),
            user_id (opcional, para auditoría), reason ("logout"/"password_change"
            /...). No hace nada si jti o exp son falsy.
        Outputs: None. Añade el jti en memoria y lo persiste en BD best-effort
            (un fallo de BD se loguea pero NO rompe el logout).
        Llamado por: AuthService al cerrar sesión o cambiar contraseña.
        Llama a: _persist_revocation, _maybe_gc.
        """
        if not jti or not exp:
            return
        with self._lock:
            self._revoked[jti] = float(exp)
            self._maybe_gc()

        self._persist_revocation(jti, float(exp), user_id, reason)

    def is_revoked(self, jti: str) -> bool:
        """
        Indica si un token está revocado (consultado en CADA request protegido).

        Inputs: jti del token a comprobar.
        Outputs: bool — True solo si el jti está revocado Y todavía vigente. Si
            ya pasó su exp, se descarta del dict y devuelve False (el token caduca
            por sí solo). Dispara un GC oportunista de entradas vencidas.
        Llamado por: el loader @jwt.token_in_blocklist_loader de main.create_app().
        """
        if not jti:
            return False
        now = time.time()
        with self._lock:
            self._maybe_gc(now=now)
            exp = self._revoked.get(jti)
            if exp is None:
                return False
            if exp < now:
                # Token ya expiró por sí solo, sacar del dict
                self._revoked.pop(jti, None)
                return False
            return True

    def size(self) -> int:
        """Número de tokens revocados actualmente en memoria (diagnóstico)."""
        with self._lock:
            return len(self._revoked)

    # ---- Persistencia opcional -------------------------------------------

    def rehydrate_from_db(self) -> int:
        """
        Rehidrata la blocklist desde la BD al arrancar (pipeline #1, paso 5).

        Carga en memoria los `revoked_tokens` aún vigentes y purga de la tabla
        los ya expirados (GC oportunista). Idempotente: solo actúa la 1ª vez
        (flag `_rehydrated`).

        Outputs: int — nº de tokens cargados. Si la tabla no existe (BD sin
            migrar) NO falla: loguea warning y la blocklist queda solo en memoria.
        Llamado por: main.create_app() tras init_db().
        """
        if self._rehydrated:
            return 0
        try:
            from backend.app.database.connection import db_manager
            from backend.app.database.models import RevokedToken
            now_dt = datetime.utcnow()
            loaded = 0
            with db_manager.get_session() as session:
                rows = session.query(RevokedToken).filter(
                    RevokedToken.expires_at > now_dt
                ).all()
                with self._lock:
                    for row in rows:
                        self._revoked[row.jti] = row.expires_at.replace(
                            tzinfo=timezone.utc
                        ).timestamp()
                        loaded += 1
                # GC oportunista de los ya expirados
                session.query(RevokedToken).filter(
                    RevokedToken.expires_at <= now_dt
                ).delete(synchronize_session=False)
                session.commit()
            self._rehydrated = True
            if loaded:
                logger.info(
                    f"JWTBlocklist rehidratada: {loaded} token(s) revocado(s) vigentes"
                )
            return loaded
        except Exception as e:
            # Si la tabla aún no existe (BD vieja sin migración) seguimos
            # funcionando con blocklist sólo en memoria.
            logger.warning(
                f"No se pudo rehidratar JWTBlocklist desde BD ({e}); "
                "blocklist funcionará sólo en memoria hasta el próximo reinicio"
            )
            return 0

    def _persist_revocation(self, jti: str, exp: float,
                             user_id: int | None, reason: str) -> None:
        """Persiste en BD; silenciosamente best-effort."""
        try:
            from backend.app.database.connection import db_manager
            from backend.app.database.models import RevokedToken
            with db_manager.get_session() as session:
                # No duplicar si ya existe
                exists = session.query(RevokedToken).filter_by(jti=jti).first()
                if exists:
                    return
                session.add(RevokedToken(
                    jti=jti,
                    expires_at=datetime.utcfromtimestamp(exp),
                    user_id=user_id,
                    reason=reason,
                ))
                session.commit()
        except Exception as e:
            logger.debug(
                f"No se pudo persistir revocación de jti={jti[:8]}...: {e}"
            )

    # ---- GC --------------------------------------------------------------

    def _maybe_gc(self, now: float | None = None) -> None:
        """Limpia entradas expiradas si pasaron >60s desde la última pasada."""
        now = now or time.time()
        if now - self._last_gc < 60:
            return
        self._last_gc = now
        before = len(self._revoked)
        self._revoked = {jti: exp for jti, exp in self._revoked.items() if exp >= now}
        removed = before - len(self._revoked)
        if removed:
            logger.debug(f"JWTBlocklist GC: {removed} entradas expiradas eliminadas")


# Instancia global
jwt_blocklist = JWTBlocklist()
