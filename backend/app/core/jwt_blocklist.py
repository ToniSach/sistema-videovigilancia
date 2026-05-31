"""
Blocklist de JWT revocados (logout, cambio de contraseña).

Capa primaria: diccionario en memoria {jti: exp_timestamp}, thread-safe,
con GC automático que descarta entradas vencidas.

Capa de persistencia opcional: tabla `revoked_tokens` en la BD principal.
- Cuando se revoca un jti se intenta persistir (best-effort: si falla, sólo
  queda en memoria y se loguea warning).
- Al arrancar `rehydrate_from_db()` carga los jti aún vigentes para que un
  logout no se "olvide" si el backend se reinicia 5 min después.

Esto cubre el caso típico de LAN single-process. Para multi-proceso o alta
sensibilidad → Redis con TTL (refactor menor en esta misma clase).
"""
from __future__ import annotations

import threading
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class JWTBlocklist:
    """Singleton thread-safe con persistencia opcional a BD."""
    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
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
        Marca un jti como revocado hasta su exp original. Persiste en BD
        si la tabla existe (best-effort: nunca rompe el flujo de logout).
        """
        if not jti or not exp:
            return
        with self._lock:
            self._revoked[jti] = float(exp)
            self._maybe_gc()

        self._persist_revocation(jti, float(exp), user_id, reason)

    def is_revoked(self, jti: str) -> bool:
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
        with self._lock:
            return len(self._revoked)

    # ---- Persistencia opcional -------------------------------------------

    def rehydrate_from_db(self) -> int:
        """
        Lee tokens revocados vigentes de la BD al arrancar y los carga en
        memoria. Devuelve cuántos se cargaron. Llamar UNA vez en startup.
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
