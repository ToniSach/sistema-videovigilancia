"""
================================================================================
MÓDULO: notification_preference_service — CRUD de preferencias de notificación
================================================================================

PROPÓSITO
    Capa de negocio para crear/leer/actualizar/borrar las reglas con las que
    cada usuario decide cómo quiere ser notificado: por qué tipo de evento, en
    qué cámara (o todas), por qué canales, en qué horario y qué días.

RESPONSABILIDAD PRINCIPAL
    Gestionar la tripleta de tablas que modela una preferencia:
      - NotificationPreference (fila base: usuario, tipo de evento, cámara,
        habilitado, ventana horaria).
      - NotificationChannel (1..N canales: 'telegram', 'app', …).
      - NotificationDay (0..N días de la semana en que aplica).
    Encapsula transacciones BD y el `expunge` para devolver objetos desligados
    de la sesión (consumibles desde las rutas sin "DetachedInstance").

DEPENDENCIAS
    database.models ........ NotificationPreference, NotificationChannel, NotificationDay
    database.connection .... db_manager (una sesión por operación)

COMPONENTES RELACIONADOS
    - api.routes.notifications: expone este servicio como endpoints REST.
    - NotificationRouter (Pipeline #13): CONSUME estas preferencias en runtime
      para decidir a quién/por dónde notificar. Este módulo solo las administra.

PUNTO DE ENTRADA
    Instancia `NotificationPreferenceService()` desde las rutas; cada método es
    autocontenido (abre y cierra su propia sesión).

PIPELINE(S)
    #13 Notificaciones — etapa de CONFIGURACIÓN (no de runtime): define los datos
    que el NotificationRouter leerá al rutear cada evento.
================================================================================
"""
import logging
from typing import List, Optional
from datetime import time

from backend.app.database.models import (
    NotificationPreference, NotificationChannel, NotificationDay
)
from backend.app.database.connection import db_manager

logger = logging.getLogger(__name__)


class NotificationPreferenceService:
    """
    Gestiona preferencias de notificación por usuario (CRUD).

    ROL: administrar las reglas de notificación; NO entrega notificaciones (eso
    es el NotificationRouter). Sin estado propio: cada método abre su sesión BD.
    Lo instancian/consumen las rutas de `api.routes.notifications`.
    Pipeline #13, etapa de configuración.
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def create_preference(self, user_id: int, event_type: str,
                         camera_id: Optional[int] = None,
                         enabled: bool = True,
                         channels: List[str] = None,
                         schedule_start: Optional[time] = None,
                         schedule_end: Optional[time] = None,
                         days_of_week: List[int] = None,
                         device_id: Optional[int] = None) -> NotificationPreference:
        """
        Propósito: crea una preferencia completa (fila base + canales + días) en
            una sola transacción.
        Inputs:
            user_id, event_type: a quién y para qué tipo de evento.
            camera_id: cámara concreta, o None para "todas las cámaras".
            enabled: si la regla está activa.
            channels: lista de canales p.ej. ['telegram', 'app'].
            schedule_start/end: ventana horaria (None = sin restricción).
            days_of_week: enteros 0-6 (0=domingo); None/vacío = todos los días.
        Outputs: NotificationPreference (desligado de la sesión, sin canales/días
            cargados como relación).
        Excepciones: re-lanza cualquier error de BD tras loguearlo.
        Llamado por: ruta POST de preferencias (api.routes.notifications).

        IDEMPOTENTE (upsert): existe un UNIQUE(user_id, event_type, camera_id).
        Si ya hay una preferencia para esa tripleta, la ACTUALIZA en vez de
        fallar con IntegrityError (HTTP 500). Antes, el cliente que tuviera su
        caché desincronizada hacía POST de algo existente → 500 → "no me deja
        guardar". Ahora POST = "asegúrate de que exista así", sin sorpresas.
        """
        try:
            with db_manager.get_session() as session:
                # ¿Ya existe la preferencia (misma combinación incl. dispositivo)?
                # → upsert. device_id None = preferencia "de cuenta".
                pref = session.query(NotificationPreference).filter_by(
                    user_id=user_id,
                    device_id=device_id,
                    event_type=event_type,
                    camera_id=camera_id,
                ).first()

                if pref is None:
                    pref = NotificationPreference(
                        user_id=user_id,
                        device_id=device_id,
                        event_type=event_type,
                        camera_id=camera_id,
                        enabled=enabled,
                        schedule_start=schedule_start,
                        schedule_end=schedule_end,
                    )
                    session.add(pref)
                    session.flush()
                    accion = "creada"
                else:
                    pref.enabled = enabled
                    pref.schedule_start = schedule_start
                    pref.schedule_end = schedule_end
                    # Reemplazar canales/días existentes (semántica de "estado
                    # deseado", igual que update_preference).
                    session.query(NotificationChannel).filter_by(
                        preference_id=pref.id
                    ).delete()
                    session.query(NotificationDay).filter_by(
                        preference_id=pref.id
                    ).delete()
                    accion = "actualizada"

                # (Re)crear canales
                if channels:
                    for ch in channels:
                        session.add(NotificationChannel(preference_id=pref.id, channel=ch))

                # (Re)crear días
                if days_of_week:
                    for day in days_of_week:
                        session.add(NotificationDay(preference_id=pref.id, day_of_week=day))

                session.flush()
                session.expunge(pref)
                self.logger.info(
                    f"Preferencia {accion} para user {user_id}, event {event_type}"
                )
                return pref
        except Exception as e:
            self.logger.error(f"Error creando preferencia: {e}")
            raise
    
    def get_user_preferences(self, user_id: int, device_id: Optional[int] = None,
                             _scope_device: bool = False) -> List[NotificationPreference]:
        """
        Propósito: lista preferencias del usuario, adjuntando canales (`.channels`)
            y días (`.days`) ya cargados para serializarlos sin la sesión.
            Telegram/in-app son por dispositivo: si `_scope_device` es True, solo
            las del alcance `device_id`; si no, todas las del usuario.
        Inputs: user_id; device_id (alcance); _scope_device (acotar o no).
        Outputs: List[NotificationPreference] (desligadas).
        Excepciones: re-lanza errores de BD. Llamado por: ruta GET de preferencias.
        """
        try:
            with db_manager.get_session() as session:
                q = session.query(NotificationPreference).filter_by(user_id=user_id)
                if _scope_device:
                    q = q.filter(NotificationPreference.device_id == device_id)
                prefs = q.all()
                for p in prefs:
                    # Cargar relaciones
                    p.channels = session.query(NotificationChannel).filter_by(preference_id=p.id).all()
                    p.days = session.query(NotificationDay).filter_by(preference_id=p.id).all()
                    session.expunge(p)
                return prefs
        except Exception as e:
            self.logger.error(f"Error obteniendo preferencias: {e}")
            raise
    
    def update_preference(self, pref_id: int, **kwargs) -> Optional[NotificationPreference]:
        """
        Propósito: actualiza una preferencia. Campos simples (enabled,
            schedule_start, schedule_end) se modifican in situ; 'channels' y
            'days_of_week' se REEMPLAZAN por completo (borra y recrea) para
            evitar duplicados.
        Inputs: pref_id; kwargs con cualquier subconjunto de los campos anteriores.
        Outputs: NotificationPreference actualizada (desligada), o None si el id
            no existe.
        Excepciones: re-lanza errores de BD. Llamado por: ruta PUT/PATCH de preferencias.
        """
        try:
            with db_manager.get_session() as session:
                pref = session.get(NotificationPreference, pref_id)
                if not pref:
                    return None
                
                # Actualizar campos simples
                for key in ['enabled', 'schedule_start', 'schedule_end']:
                    if key in kwargs:
                        setattr(pref, key, kwargs[key])
                
                # Actualizar canales (reemplazar todos)
                if 'channels' in kwargs:
                    session.query(NotificationChannel).filter_by(preference_id=pref.id).delete()
                    for ch in kwargs['channels']:
                        session.add(NotificationChannel(preference_id=pref.id, channel=ch))
                
                # Actualizar días (reemplazar todos)
                if 'days_of_week' in kwargs:
                    session.query(NotificationDay).filter_by(preference_id=pref.id).delete()
                    for day in kwargs['days_of_week']:
                        session.add(NotificationDay(preference_id=pref.id, day_of_week=day))
                
                session.flush()
                session.expunge(pref)
                return pref
        except Exception as e:
            self.logger.error(f"Error actualizando preferencia: {e}")
            raise
    
    def delete_preference(self, pref_id: int) -> bool:
        """
        Propósito: elimina la preferencia (los canales/días asociados caen por
            cascada del modelo). Inputs: pref_id. Outputs: True si existía y se
            borró, False si no existía. Excepciones: re-lanza errores de BD.
            Llamado por: ruta DELETE de preferencias.
        """
        try:
            with db_manager.get_session() as session:
                pref = session.get(NotificationPreference, pref_id)
                if pref:
                    session.delete(pref)
                    return True
                return False
        except Exception as e:
            self.logger.error(f"Error eliminando preferencia: {e}")
            raise