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
    def __init__(self, event_repo: EventRepository):
        self._event_repo = event_repo
        self._snapshots_dir = os.path.join(settings.RECORDINGS_PATH, "snapshots")
        os.makedirs(self._snapshots_dir, exist_ok=True)

        # Suscribirse a todos los eventos
        event_manager.subscribe_all(self._on_event)
        logging.info("EventService inicializado y suscrito a eventos")

    def _on_event(self, event_data: EventData) -> None:
        snapshot_path = None

        # Guardar snapshot si hay frame
        if event_data.frame is not None:
            try:
                cam_snap_dir = os.path.join(self._snapshots_dir, str(event_data.camera_id))
                os.makedirs(cam_snap_dir, exist_ok=True)

                ts = int(event_data.timestamp)
                safe_event_type = event_data.event_type.replace(" ", "_")
                snapshot_filename = f"{ts}_{safe_event_type}.jpg"
                snapshot_path = os.path.join(cam_snap_dir, snapshot_filename)

                # Guardar imagen
                success = cv2.imwrite(snapshot_path, event_data.frame)
                if not success:
                    logging.error(f"No se pudo guardar snapshot en {snapshot_path}")
                    snapshot_path = None
                else:
                    logging.debug(f"Snapshot guardado: {snapshot_path}")
            except Exception as e:
                logging.error(f"Error guardando snapshot: {e}")
                snapshot_path = None

        # Crear evento en BD
        try:
            event = Event(
                camera_id=event_data.camera_id,
                event_type=event_data.event_type,
                confidence=event_data.confidence,
                snapshot_path=snapshot_path,
                clip_path=None,  # Se actualizará luego si hay grabación
                acknowledged=False
            )

            saved_event = self._event_repo.create(event)
            logging.info(
                f"Evento guardado: {event_data.event_type} "
                f"cámara {event_data.camera_id} id={saved_event.id}"
            )
        except Exception as e:
            logging.error(f"Error guardando evento en BD: {e}")

    def get_events(
        self, 
        camera_id: Optional[int] = None, 
        event_type: Optional[str] = None, 
        hours: int = 24, 
        limit: int = 50
    ) -> List[Dict[str, Any]]:
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
        try:
            return self._event_repo.acknowledge(event_id)
        except Exception as e:
            logging.error(f"Error reconociendo evento {event_id}: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
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
        try:
            event = self._event_repo.get_by_id(event_id)
            if event:
                event.clip_path = clip_path
                return True
            return False
        except Exception as e:
            logging.error(f"Error actualizando clip_path: {e}")
            return False
