import time
import logging
import threading
from typing import Callable, Optional, Dict, Tuple

from ..cameras.camera_manager import CameraManager
from ..processing.ai.ai_scheduler import AIScheduler
from ..events.event_manager import event_manager, EventData
from ..config import settings

logger = logging.getLogger(__name__)

_VALID_LENSES = ("main", "l1", "l2")


class AIService:
    """
    Servicio singleton que gestiona el procesamiento de IA para cámaras.

    Las cámaras dual-lens pueden activar IA por lente (l1, l2) por separado.
    La clave interna de schedulers es (camera_id, lens).
    """

    def __init__(self, camera_manager: CameraManager):
        self._camera_manager = camera_manager
        self._schedulers: Dict[Tuple[int, str], AIScheduler] = {}
        self._lock = threading.Lock()
        self._event_callback: Optional[
            Callable[[int, str, str, float, dict], None]
        ] = None

        self._gpu_available = self._detect_gpu()

        logger.info(
            f"AIService inicializado | GPU: {'disponible' if self._gpu_available else 'no disponible'}"
        )

    # ------------------------------------------------------------------
    # Utilidades internas
    # ------------------------------------------------------------------
    def _detect_gpu(self) -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except Exception:
            return False

    def _detection_callback(self, cam_id, class_name, conf, frame, metadata):
        self._handle_detection(cam_id, class_name, conf, frame, metadata)

    def _can_activate_more_ai(self) -> bool:
        max_ai = getattr(settings, "MAX_AI_CAMERAS", 4)
        if len(self._schedulers) < max_ai:
            return True
        if self._gpu_available:
            logger.warning(
                f"Superando límite ({max_ai}) pero GPU disponible → permitido"
            )
            return True
        logger.error(
            f"Límite de IA alcanzado ({max_ai}) y sin GPU"
        )
        return False

    # ------------------------------------------------------------------
    # Eventos
    # ------------------------------------------------------------------
    def set_event_callback(
        self,
        callback: Callable[[int, str, str, float, dict], None]
    ) -> None:
        self._event_callback = callback
        with self._lock:
            schedulers = list(self._schedulers.values())
        for scheduler in schedulers:
            scheduler.set_detection_callback(self._detection_callback)

    def _handle_detection(
        self,
        camera_id: int,
        class_name: str,
        confidence: float,
        frame,
        metadata: Optional[dict] = None
    ) -> None:
        metadata = metadata or {}

        if self._event_callback:
            try:
                self._event_callback(
                    camera_id, class_name, class_name, confidence, metadata
                )
            except Exception as e:
                logger.error(f"Error en event_callback externo: {e}")

        # Guardar el SNAPSHOT ANTES de emitir el evento. Si no, los
        # subscribers (TelegramNotifier, NotificationRouter) recibirían
        # event_data con snapshot_path=None y mandarían solo texto.
        # Como event_manager.emit() dispara TODOS los subscribers en
        # paralelo, no podíamos confiar en que EventService lo guardara
        # primero — había una race condition silenciosa.
        ts = time.time()
        snapshot_path = metadata.get("snapshot_path")
        if snapshot_path is None and frame is not None:
            try:
                import os as _os
                import cv2 as _cv2
                from backend.app.config import settings as _settings
                # ABSOLUTO desde el inicio: settings.RECORDINGS_PATH puede ser
                # relativo ("recordings/") y se interpreta contra CWD. Si lo
                # guardamos relativo en metadata, TelegramNotifier hace
                # os.path.join(base_abs, relativo_con_'recordings') y obtiene
                # ".../recordings/recordings/snapshots/..." (doble 'recordings')
                # que no existe → snapshot no se envía.
                snap_dir = _os.path.abspath(_os.path.join(
                    _settings.RECORDINGS_PATH, "snapshots", str(camera_id)
                ))
                _os.makedirs(snap_dir, exist_ok=True)
                safe_type = class_name.replace(" ", "_")
                snap_filename = f"{int(ts)}_{safe_type}.jpg"
                snapshot_path = _os.path.join(snap_dir, snap_filename)
                if not _cv2.imwrite(snapshot_path, frame):
                    logger.error(f"cv2.imwrite falló para {snapshot_path}")
                    snapshot_path = None
                else:
                    logger.debug(f"📸 Snapshot pre-emit guardado: {snapshot_path}")
            except Exception as e:
                logger.error(f"Error guardando snapshot pre-emit: {e}")
                snapshot_path = None

        event_metadata = {
            "class": class_name,
            "count": metadata.get("count", 1),
            "object_count": metadata.get("count", 1),  # alias para TelegramNotifier
            "snapshot_path": snapshot_path,
            "lens": metadata.get("lens"),
        }

        camera_name = f"Camara_{camera_id}"
        try:
            camera = self._camera_manager.get_camera_by_id(camera_id)
            if camera:
                camera_name = camera.name
        except Exception:
            pass

        event_data = EventData(
            camera_id=camera_id,
            camera_name=camera_name,
            event_type=class_name,
            confidence=confidence,
            timestamp=ts,
            frame=frame,
            metadata=event_metadata,
        )

        try:
            event_manager.emit(event_data)
        except Exception as e:
            logger.error(f"Error publicando evento: {e}")

    # ------------------------------------------------------------------
    # Control de IA
    # ------------------------------------------------------------------
    def _normalize_lens(self, lens: Optional[str]) -> str:
        lens = (lens or "main").lower()
        if lens not in _VALID_LENSES:
            raise ValueError(f"lens debe ser uno de {_VALID_LENSES}, recibido: {lens}")
        return lens

    def activate_ai(self, camera_id: int, lens: str = "main",
                    mode: str = "low_cpu") -> bool:
        """
        Activa IA en una cámara (y opcionalmente en un lente concreto).

        Para cámaras mono → lens="main".
        Para cámaras dual-lens → lens="l1" o "l2" para procesar solo ese lente.

        Devuelve False si faltan dependencias (torch/ultralytics) o la cámara
        no tiene distributor activo. Lanza RuntimeError si faltan dependencias
        para que el endpoint HTTP pueda devolver un 503 con mensaje útil.
        """
        # Validación temprana de dependencias — falla rápido y con mensaje claro
        from ..processing.ai.model_pool import YLOModelPool
        ok, msg = YLOModelPool.check_dependencies()
        if not ok:
            logger.error(f"No se puede activar IA cam={camera_id}: {msg}")
            raise RuntimeError(msg)

        lens = self._normalize_lens(lens)
        key = (camera_id, lens)

        with self._lock:
            # EXCLUSIVIDAD POR CÁMARA: la IA es costosa (~2.5 inferencias/s
            # por scheduler en CPU). Permitir IA en l1 y l2 simultáneamente
            # duplicaría el coste sin valor agregado: el campo de visión
            # cubre escenas distintas pero la alerta de "persona detectada"
            # es la misma a efectos de notificación.
            # Si ya hay un scheduler en OTRO lente de esta misma cámara,
            # rechazamos la activación con mensaje claro.
            existing_lenses = [
                k[1] for k in self._schedulers.keys() if k[0] == camera_id
            ]
            if existing_lenses and lens not in existing_lenses:
                msg = (
                    f"IA ya activa en lente '{existing_lenses[0]}' de cámara "
                    f"{camera_id}. Solo se permite IA en un lente a la vez. "
                    f"Desactiva el lente actual antes de cambiar."
                )
                logger.warning(msg)
                raise RuntimeError(msg)

            if not self._can_activate_more_ai() and key not in self._schedulers:
                return False

        distributor = self._camera_manager.get_distributor(camera_id, lens)
        if distributor is None:
            logger.error(
                f"No hay distributor para cámara {camera_id} lens={lens}. "
                f"¿La cámara está activa? ¿Es dual-lens si pediste l1/l2?"
            )
            return False

        with self._lock:
            if key in self._schedulers:
                logger.warning(f"Reactivando IA cámara={camera_id} lens={lens}")
                old = self._schedulers.pop(key)
                try:
                    old.stop(distributor)
                except Exception as e:
                    logger.error(f"Error deteniendo scheduler previo: {e}")

            # Cooldown configurable vía .env (AI_EVENT_COOLDOWN_SECONDS).
            # Default 30s en producción. Bajar a 0 SOLO para tests rápidos
            # — con 0s el flood de eventos satura GlobalExecutor (Telegram
            # + snapshot + splice) y la encoder MJPEG queda en cola → la
            # live preview salta segundos. Ver settings.AI_EVENT_COOLDOWN_SECONDS.
            cooldown_s = getattr(settings, "AI_EVENT_COOLDOWN_SECONDS", 30)
            scheduler = AIScheduler(camera_id, mode, cooldown_seconds=cooldown_s)
            # BUG FIX CRÍTICO: el callback DEBE registrarse SIEMPRE.
            # _detection_callback → _handle_detection → event_manager.emit(...)
            # → EventService persiste a BD + RecordingManager graba clip +
            #   NotificationRouter y TelegramNotifier mandan al usuario.
            #
            # Antes esto estaba envuelto en `if self._event_callback:` —
            # condicional erróneo porque `_event_callback` es para callbacks
            # externos OPCIONALES (que ahora invocamos dentro de
            # _handle_detection si está seteado). El flujo principal nunca
            # debió depender de ese flag → ningún evento llegaba a Telegram.
            scheduler.set_detection_callback(self._detection_callback)

            scheduler.start(distributor)
            self._schedulers[key] = scheduler

        logger.info(f"IA activada | cámara={camera_id} | lens={lens} | modo={mode}")
        # Persistir has_ai=True para reactivar en el próximo arranque del backend
        self._persist_has_ai(camera_id, True)
        return True

    def _persist_has_ai(self, camera_id: int, has_ai: bool) -> None:
        """Guarda has_ai en la BD para que la activación sobreviva reinicios."""
        try:
            from backend.app.database.connection import db_manager
            from backend.app.database.models import Camera as CameraModel
            with db_manager.get_session() as session:
                cam = session.query(CameraModel).filter_by(id=camera_id).first()
                if cam is not None:
                    cam.has_ai = has_ai
                    session.commit()
        except Exception as e:
            logger.warning(f"No se pudo persistir has_ai={has_ai} cam={camera_id}: {e}")

    def deactivate_ai(self, camera_id: int, lens: str = "main") -> bool:
        lens = self._normalize_lens(lens)
        key = (camera_id, lens)

        with self._lock:
            scheduler = self._schedulers.pop(key, None)

        if not scheduler:
            logger.warning(f"No hay IA activa cámara={camera_id} lens={lens}")
            return False

        distributor = self._camera_manager.get_distributor(camera_id, lens)
        if distributor:
            try:
                scheduler.stop(distributor)
            except Exception as e:
                logger.error(f"Error deteniendo scheduler: {e}")

        logger.info(f"IA desactivada | cámara={camera_id} | lens={lens}")
        # Solo desmarcar has_ai si no queda OTRO lente con IA activa
        with self._lock:
            other_active = any(
                key[0] == camera_id for key in self._schedulers.keys()
            )
        if not other_active:
            self._persist_has_ai(camera_id, False)
        return True

    def change_mode(self, camera_id: int, mode: str, lens: str = "main") -> bool:
        lens = self._normalize_lens(lens)
        with self._lock:
            scheduler = self._schedulers.get((camera_id, lens))
        if not scheduler:
            return False
        scheduler.set_mode(mode)
        return True

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------
    def is_active(self, camera_id: int, lens: str = "main") -> bool:
        lens = self._normalize_lens(lens)
        with self._lock:
            return (camera_id, lens) in self._schedulers

    def get_ai_status(self) -> dict:
        with self._lock:
            snapshot = dict(self._schedulers)

        active = []
        stats = {}
        for (cam_id, lens), scheduler in snapshot.items():
            active.append({"camera_id": cam_id, "lens": lens})
            try:
                stats[f"{cam_id}:{lens}"] = scheduler.get_stats()
            except Exception:
                stats[f"{cam_id}:{lens}"] = {}

        return {
            "active": active,
            "active_count": len(active),
            "gpu_available": self._gpu_available,
            "schedulers": stats,
        }

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def stop_all(self) -> None:
        with self._lock:
            snapshot = dict(self._schedulers)
            self._schedulers.clear()

        for (cam_id, lens), scheduler in snapshot.items():
            distributor = self._camera_manager.get_distributor(cam_id, lens)
            if distributor:
                try:
                    scheduler.stop(distributor)
                except Exception as e:
                    logger.error(f"Error deteniendo IA {cam_id}/{lens}: {e}")
        logger.info("Todos los schedulers de IA detenidos")
