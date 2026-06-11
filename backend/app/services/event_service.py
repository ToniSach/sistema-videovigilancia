"""
================================================================================
MÓDULO: services.event_service — Persistencia de eventos (Pipeline #10)
================================================================================

PROPÓSITO
    Suscriptor del bus de eventos que PERSISTE cada EventData en la BD (tabla
    events) y dispara el ruteo de notificaciones. También expone consultas de
    lectura (listado, stats, acknowledge) para la API.

RESPONSABILIDAD PRINCIPAL
    Ser el sumidero de almacenamiento del Pipeline #10: cada detección emitida
    por AIService (u otra fuente) llega vía EventManager → _on_event → fila en
    BD + NotificationRouter. Garantiza que exista un snapshot asociado (fallback
    si nadie lo guardó antes).

DEPENDENCIAS
    events.event_manager ........ se SUSCRIBE a todos los eventos (subscribe_all)
    database.repositories ....... EventRepository (CRUD de Event)
    services.notification_router  ruteo por reglas de usuario (carga perezosa)
    cv2 / config.settings ....... fallback de snapshot a disco

COMPONENTES RELACIONADOS
    Lo INSTANCIA: DependencyContainer (container.py) con EventRepository →
        singleton `event_service`; en su __init__ ya se autosuscribe al bus.
    Lo CONSUME (lectura): blueprint `events_bp` (api/routes/events.py) —
        get_events / get_stats / acknowledge_event.
    Lo CONSUME (escritura): EventManager, que invoca _on_event en su pool de
        hilos cuando se emite un EventData.

PUNTO DE ENTRADA
    Escritura: _on_event (callback del bus). Lectura: get_events / get_stats.

PIPELINE(S)
    #10 Eventos — etapa de persistencia: EventData → fila en BD → encadena con
    #13 Notificaciones (NotificationRouter) y #12 Clips (update_event_clip_path
    enlaza el clip extraído por el RecordingManager).
================================================================================
"""
import os
import cv2
import time
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any

from ..events.event_manager import EventManager, EventData, event_manager
from ..database.repositories.event_repository import EventRepository
from ..database.models import Event
from ..config import settings


class EventService:
    """
    Persiste eventos y los enruta a notificaciones (capa del Pipeline #10).

    Rol: suscriptor del EventManager. Se autosuscribe en __init__, así que basta
    con instanciarlo (lo hace el contenedor) para que empiece a guardar eventos.

    Lo instancia: container.py (singleton `event_service`).
    Lo consume: events_bp (lectura) + EventManager (escritura vía _on_event).
    Dependencias: EventRepository, event_manager, NotificationRouter.
    """

    def __init__(self, event_repo: EventRepository):
        self._event_repo = event_repo
        self._snapshots_dir = os.path.join(settings.RECORDINGS_PATH, "snapshots")
        os.makedirs(self._snapshots_dir, exist_ok=True)

        # Autosuscripción al bus: a partir de aquí cada EventData emitido se
        # persiste vía _on_event (corre en el pool de hilos de EventManager).
        event_manager.subscribe_all(self._on_event)
        logging.info("EventService inicializado y suscrito a eventos")

    def _on_event(self, event_data: EventData) -> None:
        # Callback del bus (Pipeline #10): persiste el evento y dispara el ruteo
        # de notificaciones. Corre en un hilo del pool de EventManager.
        #
        # El snapshot AHORA se guarda en AIService._handle_detection ANTES
        # de emitir el evento (para que TelegramNotifier lo encuentre en
        # event_data.metadata sin race condition). Aquí solo leemos el
        # path y, como fallback, lo guardamos si todavía no existía.
        snapshot_path = None
        if event_data.metadata:
            snapshot_path = event_data.metadata.get("snapshot_path")

        if snapshot_path is None and event_data.frame is not None:
            # Fallback: si nadie guardó el snapshot antes, lo hacemos aquí
            try:
                cam_snap_dir = os.path.join(self._snapshots_dir, str(event_data.camera_id))
                os.makedirs(cam_snap_dir, exist_ok=True)
                ts = int(event_data.timestamp)
                safe_event_type = event_data.event_type.replace(" ", "_")
                snapshot_filename = f"{ts}_{safe_event_type}.jpg"
                snapshot_path = os.path.join(cam_snap_dir, snapshot_filename)
                if not cv2.imwrite(snapshot_path, event_data.frame):
                    logging.error(f"No se pudo guardar snapshot en {snapshot_path}")
                    snapshot_path = None
            except Exception as e:
                logging.error(f"Error guardando snapshot (fallback): {e}")
                snapshot_path = None

        # Crear evento en BD + NOTIFICACIONES
        try:
            event = Event(
                camera_id=event_data.camera_id,
                event_type=event_data.event_type,
                confidence=event_data.confidence,
                snapshot_path=snapshot_path,
                clip_path=None,
                acknowledged=False
            )

            saved_event = self._event_repo.create(event)

            logging.info(
                f"Evento guardado: {event_data.event_type} "
                f"cámara {event_data.camera_id} id={saved_event.id}"
            )

            # Compartir el id del evento en la metadata del EventData (objeto
            # común a todos los suscriptores del bus). Así, cuando el
            # RecordingManager termine de generar el clip por splice (~10-20s
            # después), puede enlazarlo al evento vía update_event_clip_path
            # (Pipeline #12) y rellenar Event.clip_path. Sin esto, los eventos
            # nunca quedaban ligados a su grabación.
            try:
                if event_data.metadata is None:
                    event_data.metadata = {}
                event_data.metadata["event_id"] = saved_event.id
            except Exception:
                pass

            # ============================
            # NUEVO: RUTEO DE NOTIFICACIONES
            # ============================
            try:
                from backend.app.services.notification_router import notification_router
                notification_router.route_event(saved_event, event_data)
            except Exception as e:
                logging.error(f"Error enviando notificación: {e}")

        except Exception as e:
            logging.error(f"Error guardando evento en BD: {e}")

    def get_events(
        self, 
        camera_id: Optional[int] = None, 
        event_type: Optional[str] = None, 
        hours: int = 24, 
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Lista eventos recientes con filtros opcionales (consulta de lectura #10).

        Inputs: camera_id (filtra por cámara), event_type (filtra por tipo en
            memoria), hours (ventana temporal), limit (tope).
        Outputs: lista de dicts (event.to_dict); [] ante error (degradación suave
            para no romper la UI de eventos).
        Llamado por: GET /api/v1/events (events_bp, endpoint de polling).
        """
        try:
            if camera_id:
                events = self._event_repo.get_by_camera(camera_id, limit=limit)
            elif event_type:
                events = self._event_repo.get_recent(hours=hours, limit=limit)
                events = [e for e in events if e.event_type == event_type]
            else:
                events = self._event_repo.get_recent(hours=hours, limit=limit)

            return [event.to_dict() for event in events[:limit]]
        except Exception as e:
            logging.error(f"Error obteniendo eventos: {e}")
            return []

    def acknowledge_event(self, event_id: int) -> bool:
        """Marca un evento como reconocido (visto por el operador).
        Outputs: True si se actualizó; False ante error.
        Llamado por: endpoint de acknowledge de events_bp."""
        try:
            return self._event_repo.acknowledge(event_id)
        except Exception as e:
            logging.error(f"Error reconociendo evento {event_id}: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """
        Resumen de eventos de las últimas 24h para el dashboard.
        Outputs: {total_24h, by_type (conteo por tipo), unacknowledged}.
        Llamado por: endpoint de stats del dashboard (events_bp/system_bp).
        """
        try:
            events = self._event_repo.get_recent(hours=24, limit=1000)

            stats = {}
            for event in events:
                stats[event.event_type] = stats.get(event.event_type, 0) + 1

            return {
                "total_24h": len(events),
                "by_type": stats,
                "unacknowledged": len([e for e in events if not e.acknowledged])
            }
        except Exception as e:
            logging.error(f"Error obteniendo estadísticas: {e}")
            return {"total_24h": 0, "by_type": {}, "unacknowledged": 0}

    def update_event_clip_path(self, event_id: int, clip_path: str) -> bool:
        """
        Enlaza el clip extraído con su evento (puente #10 Eventos ↔ #12 Clips).

        Lo invoca el RecordingManager tras extraer por splice el clip del evento
        desde la grabación continua.
        Outputs: True si el evento existe; False si no.
        """
        try:
            event = self._event_repo.get_by_id(event_id)
            if event:
                event.clip_path = clip_path
                return True
            return False
        except Exception as e:
            logging.error(f"Error actualizando clip_path: {e}")
            return False