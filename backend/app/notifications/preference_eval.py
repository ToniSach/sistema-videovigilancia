"""
================================================================================
MÓDULO: preference_eval — Evaluación ÚNICA de preferencias de notificación
================================================================================

PROPÓSITO
    Una sola función que responde, dado un usuario/dispositivo y un evento, QUÉ
    CANALES quiere ese destinatario para ese evento EN ESTE MOMENTO. Antes esta
    regla estaba copiada (y podía divergir) en tres sitios (router, ws_broker,
    telegram_notifier); ahora todos consumen `wanted_channels()`.

ALCANCE POR DISPOSITIVO (device_id)
    Las preferencias son POR DISPOSITIVO para el canal in-app (WebSocket):
      - device_id = <MobileDevice.id> → reglas del IN-APP de ESE teléfono.
      - device_id = None              → reglas "de CUENTA": las usa el escritorio
        (y cualquier login sin dispositivo) para su in-app, y son las ÚNICAS que
        deciden TELEGRAM (Telegram va al chat de la cuenta, no a un teléfono).
    Un teléfono SIN fila propia para un evento cae a la fila de cuenta como valor
    por defecto (parámetro `fallback_to_account`).

REGLA DE NEGOCIO (consolidada)
    1. Debe existir una NotificationPreference HABILITADA para (user, device,
       event_type). Modelo OPT-IN: sin preferencia que aplique → conjunto vacío.
    2. Precedencia de cámara DENTRO del mismo alcance: la específica de la cámara
       del evento gana sobre la "general" (camera_id=NULL).
    3. La ventana horaria (cruce de medianoche soportado) y los días deben
       permitir el momento actual.
    4. Si la preferencia pasa los filtros, sus canales se suman. Una preferencia
       SIN canales = "sin entrega" (respeta desmarcar todos los canales).

SALIDA
    `wanted_channels(...)` → set[str] ('app', 'telegram', …). Vacío = no notificar.

PIPELINE(S)
    #13 Notificaciones — predicado central de "¿quiere este evento?".
================================================================================
"""
import logging
from datetime import datetime, time
from typing import Optional, Set, List

from backend.app.database.models import (
    NotificationPreference, NotificationChannel, NotificationDay,
)

logger = logging.getLogger(__name__)

# Canales que implican entrega "en la app" (push in-app por WebSocket). 'push' y
# 'web' se aceptan por compatibilidad con datos antiguos; se entregan igual que
# 'app' (FCM/Firebase fue eliminado del proyecto).
APP_CHANNELS: Set[str] = {"app", "push", "web"}
TELEGRAM_CHANNEL = "telegram"


def _schedule_ok(pref: NotificationPreference, now_t: time) -> bool:
    """¿La hora actual cae dentro de la ventana de la preferencia? Sin ventana
    (start/end None) → siempre True. Soporta ventanas que cruzan medianoche."""
    if pref.schedule_start is None or pref.schedule_end is None:
        return True
    if pref.schedule_start < pref.schedule_end:
        return pref.schedule_start <= now_t <= pref.schedule_end
    return now_t >= pref.schedule_start or now_t <= pref.schedule_end


def _day_ok(session, pref_id: int, today: int) -> bool:
    """¿Hoy es un día permitido? Sin días configurados → True (todos).
    Convención: (weekday()+1) % 7, 0=domingo … 6=sábado."""
    days = session.query(NotificationDay).filter_by(preference_id=pref_id).all()
    if not days:
        return True
    return any(d.day_of_week == today for d in days)


def _applicable_prefs(session, user_id: int, device_id: Optional[int],
                      event_type: str, camera_id: Optional[int]) -> List[NotificationPreference]:
    """
    Preferencias HABILITADAS de un alcance concreto (user, device) para el
    evento, ya resuelta la precedencia de cámara (específica > general). Lista
    vacía si no hay ninguna aplicable en ESE alcance.
    """
    prefs = session.query(NotificationPreference).filter_by(
        user_id=user_id, device_id=device_id, event_type=event_type, enabled=True
    ).all()
    if not prefs:
        return []
    specific = [p for p in prefs if p.camera_id == camera_id]
    general = [p for p in prefs if p.camera_id is None]
    return specific if specific else general


def wanted_channels(
    session,
    user_id: int,
    event_type: str,
    camera_id: Optional[int],
    device_id: Optional[int] = None,
    fallback_to_account: bool = False,
    now: Optional[datetime] = None,
) -> Set[str]:
    """
    Conjunto de canales que el destinatario quiere para este evento AHORA.

    Inputs:
        session, user_id, event_type, camera_id: el evento a evaluar.
        device_id: alcance. None = preferencias de CUENTA (escritorio + Telegram);
            un id = preferencias del IN-APP de ese teléfono.
        fallback_to_account: si device_id no es None y NO tiene fila propia para
            este evento, usar la de cuenta como valor por defecto (útil para que
            un teléfono recién vinculado herede los ajustes de cuenta).
        now: momento de evaluación (default datetime.now()), inyectable para tests.
    Outputs:
        set[str] de canales. Vacío = no notificar.
    Excepciones: NO se capturan aquí; el llamador decide su política fail-open.
    """
    now = now or datetime.now()

    # Elegir el ALCANCE efectivo. El fallback a cuenta SOLO ocurre si el
    # dispositivo no tiene NINGUNA fila para este evento (teléfono recién
    # vinculado que hereda los ajustes de cuenta). Si el dispositivo SÍ tiene una
    # fila —aunque esté DESACTIVADA— se respeta su alcance y NO se cae a cuenta;
    # de lo contrario, "desactivar en el móvil" no tendría efecto en el in-app
    # (la fila desactivada se confundía con "inexistente" y caía a cuenta).
    scope = device_id
    if fallback_to_account and device_id is not None:
        has_any = session.query(NotificationPreference).filter_by(
            user_id=user_id, device_id=device_id, event_type=event_type
        ).first() is not None
        if not has_any:
            scope = None  # teléfono sin config para este evento → usa la de cuenta

    applicable = _applicable_prefs(session, user_id, scope, event_type, camera_id)
    if not applicable:
        return set()

    now_t = now.time()
    today = (now.weekday() + 1) % 7

    result: Set[str] = set()
    for p in applicable:
        if not _schedule_ok(p, now_t):
            continue
        if not _day_ok(session, p.id, today):
            continue
        # Los canales explícitos del usuario. Una preferencia SIN canales =
        # "sin entrega" (ni in-app ni telegram): respeta el desmarcar ambos
        # canales en el cliente. (Antes se asumía 'app' por defecto, lo que
        # impedía apagar el in-app desmarcando el canal.)
        chans = session.query(NotificationChannel).filter_by(
            preference_id=p.id
        ).all()
        result.update(c.channel for c in chans)
    return result
