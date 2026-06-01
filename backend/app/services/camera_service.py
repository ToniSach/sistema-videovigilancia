import logging
from typing import Optional

from ..cameras.camera_manager import CameraManager
from ..cameras.onvif_discovery import ONVIFDiscovery
from ..database.models import Camera
from ..database.repositories.camera_repository import CameraRepository
from ..streaming.frame_distributor import FrameDistributor
from ..streaming.frame_buffer import FrameData


class CameraService:
    """
    Servicio de negocio para gestión completa de cámaras.
    Coordina entre repositorio, manager de streaming y descubrimiento ONVIF.
    """

    def __init__(
        self,
        camera_repo: CameraRepository,
        camera_manager: CameraManager,
        onvif_discovery: ONVIFDiscovery
    ):
        self._camera_repo = camera_repo
        self._camera_manager = camera_manager
        self._onvif_discovery = onvif_discovery
        self._logger = logging.getLogger(__name__)

    def get_all_cameras(self) -> list[dict]:
        """
        Obtiene todas las cámaras con su estado de worker.

        Returns:
            Lista de diccionarios con datos de cámara y estado
        """
        cameras = self._camera_repo.get_all()
        result = []

        for camera in cameras:
            camera_dict = camera.to_dict() if hasattr(camera, 'to_dict') else self._camera_to_dict(camera)

            # Agregar estado del worker si existe
            worker = self._camera_manager.get_worker(camera.id)
            if worker:
                camera_dict["worker_status"] = worker.get_status()
            else:
                camera_dict["worker_status"] = None

            result.append(camera_dict)

        return result

    def get_camera(self, camera_id: int) -> dict | None:
        """
        Obtiene una cámara específica con su estado.

        Args:
            camera_id: ID de la cámara

        Returns:
            Diccionario con datos y estado, o None si no existe
        """
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            return None

        camera_dict = camera.to_dict() if hasattr(camera, 'to_dict') else self._camera_to_dict(camera)

        worker = self._camera_manager.get_worker(camera.id)
        camera_dict["worker_status"] = worker.get_status() if worker else None

        return camera_dict

    def add_camera(self, data: dict) -> dict:
        """
        Agrega nueva cámara al sistema y la inicia si está activa.

        Política de probe ONVIF (cambiada en 2026-05):
        - Si el cliente envía `rtsp_url`, NO se hace probe por defecto. La cámara
          se crea con esa URL y se intenta iniciar. Esto evita que un router/red
          sin multicast ONVIF (Totalplay, etc.) bloquee el alta de cámaras
          conocidas durante varios minutos con un 400.
        - Si NO hay `rtsp_url`, se hace probe ONVIF para auto-descubrir la URL.
          Si fracasa, devolvemos 400 (sin URL no podemos arrancar el worker).
        - `force_probe=true` fuerza el probe aunque haya `rtsp_url` (útil si el
          cliente quiere autocompletar profile_token / manufacturer / has_ptz...).
        """
        # Validar duplicado por IP antes de crear
        ip = data.get('ip_address')
        if ip:
            existing = self._camera_repo.get_by_ip(ip)
            if existing:
                raise ValueError(f"Ya existe una cámara con IP {ip}: '{existing.name}' (ID: {existing.id})")

        has_rtsp = bool(data.get('rtsp_url'))
        force_probe = bool(data.get('force_probe'))
        # Compat: `skip_probe=true` de versiones viejas sigue siendo válido.
        legacy_skip = bool(data.get('skip_probe'))
        # Probe activo si: hay IP y (no hay rtsp_url) o el cliente lo forzó.
        # legacy_skip lo anula explícitamente.
        do_probe = bool(ip) and not legacy_skip and (force_probe or not has_rtsp)

        if do_probe:
            try:
                device_info = self._onvif_discovery.probe_single_ip(
                    ip,
                    username=data.get('username') or None,
                    password=data.get('password') or None,
                )
                if device_info:
                    # Auto-completar desde el descubrimiento (sin sobreescribir
                    # lo que el cliente envió explícitamente).
                    data.setdefault('rtsp_url', device_info['rtsp_url'])
                    data.setdefault('username', device_info['username'])
                    data.setdefault('password', device_info['password'])
                    data.setdefault('onvif_url', device_info.get('onvif_url', ''))
                    data.setdefault('profile_token', device_info.get('profile_token', ''))
                    data.setdefault('manufacturer', device_info.get('manufacturer', 'Unknown'))
                    data.setdefault('connection_type', device_info.get('connection_type', 'manual'))
                    data.setdefault('has_ptz', device_info.get('has_ptz', False))
                    data.setdefault('has_audio', device_info.get('has_audio', False))
                    data.setdefault('resolution_width', device_info.get('resolution_width', 1920))
                    data.setdefault('resolution_height', device_info.get('resolution_height', 1080))
                    data.setdefault('fps', device_info.get('fps', 15))
                    # Auto-sugerir dual-lens por resolución/modelo (el cliente
                    # puede sobreescribirlo). El ONVIF no expone "dual-lens", así
                    # que lo inferimos.
                    try:
                        from backend.app.cameras.camera_heuristics import suggest_dual_lens
                        data.setdefault('is_dual_lens', suggest_dual_lens(
                            device_info.get('resolution_width', 0),
                            device_info.get('resolution_height', 0),
                            device_info.get('model', ''),
                            data.get('name', ''),
                        ))
                    except Exception:
                        pass
                elif not has_rtsp:
                    # Sin rtsp_url y sin probe exitoso: no sabemos a qué apuntar.
                    raise ValueError(
                        "No se pudo conectar a la cámara por ONVIF y no se "
                        "proporcionó rtsp_url. Verifique IP/credenciales o "
                        "introduzca la URL RTSP manualmente."
                    )
                else:
                    # Hay rtsp_url + probe falló: log y seguimos. El worker FFmpeg
                    # intentará conectarse al RTSP; si también falla, la auto-
                    # desactivación marcará is_active=False tras MAX_RECONNECT.
                    self._logger.info(
                        f"Probe ONVIF falló para {ip} pero hay rtsp_url; "
                        f"se crea la cámara y se intentará iniciar con la URL provista"
                    )
            except ValueError:
                raise
            except Exception as e:
                # Si tenemos rtsp_url, el probe es solo nice-to-have: no abortamos.
                if has_rtsp:
                    self._logger.warning(
                        f"Probe ONVIF excepcional para {ip}: {e}. "
                        f"Se crea con la rtsp_url provista."
                    )
                else:
                    raise ValueError(f"Error validando cámara: {e}")

        # Crear objeto Camera. Algunos campos pueden venir como None desde el
        # cliente (LineEdit vacío); los normalizamos a strings vacíos para no
        # romper columnas NOT NULL.
        camera = Camera(
            name=(data.get('name') or 'Nueva Cámara'),
            ip_address=(data.get('ip_address') or ''),
            rtsp_url=(data.get('rtsp_url') or ''),
            onvif_url=(data.get('onvif_url') or ''),
            username=(data.get('username') or ''),
            password=(data.get('password') or ''),
            profile_token=(data.get('profile_token') or ''),
            is_active=data.get('is_active', True),
            has_ai=data.get('has_ai', False),
            has_ptz=data.get('has_ptz', False),
            has_leds=data.get('has_leds', False),
            has_audio=data.get('has_audio', False),
            is_dual_lens=data.get('is_dual_lens', False),
            resolution_width=data.get('resolution_width', 1920),
            resolution_height=data.get('resolution_height', 1080),
            fps=data.get('fps', 25),
            connection_type=data.get('connection_type', 'manual')
        )

        # Guardar en BD
        created_camera = self._camera_repo.create(camera)

        # Iniciar si está activa
        if created_camera.is_active:
            self._camera_manager.start_camera(created_camera)  # register_mjpeg=True por defecto
        else:
            # Alta inactiva: aun así intentamos poner la hora correcta en la
            # cámara (best-effort, en segundo plano). Si está activa, esto ya
            # lo hace start_camera → _maybe_sync_time.
            try:
                import threading
                from backend.app.cameras.time_sync import sync_camera_time
                threading.Thread(
                    target=lambda: sync_camera_time(created_camera),
                    name=f"AddTimeSync-{created_camera.id}", daemon=True,
                ).start()
            except Exception:
                pass

        result = created_camera.to_dict() if hasattr(created_camera, 'to_dict') else self._camera_to_dict(created_camera)
        result["worker_status"] = None

        return result

    def update_camera(self, camera_id: int, data: dict) -> dict | None:
        """
        Actualiza datos de una cámara existente.

        Args:
            camera_id: ID de la cámara a actualizar
            data: Diccionario con campos a actualizar

        Returns:
            Cámara actualizada o None si no existe
        """
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            return None

        was_active = camera.is_active

        # Actualizar campos permitidos
        for key, value in data.items():
            if hasattr(camera, key) and key != 'id':
                setattr(camera, key, value)

        # Guardar cambios
        updated = self._camera_repo.update(camera)

        # Si cambió el estado de activación o configuración crítica, reiniciar
        restart_needed = (
            'rtsp_url' in data or 
            'resolution_width' in data or 
            'resolution_height' in data or 
            'fps' in data or
            'is_active' in data
        )

        if restart_needed:
            self._camera_manager.restart_camera(camera_id)
            # ✅ CORREGIDO: No llamamos start_mjpeg_for_camera aquí
            # porque restart_camera ya llama a start_camera con register_mjpeg=True

        return self.get_camera(camera_id)

    def delete_camera(self, camera_id: int) -> bool:
        """
        Elimina una cámara del sistema y todas sus referencias en otras tablas.

        La BD tiene FKs sobre `cameras.id` desde: events, recordings,
        user_camera_permissions y notification_preferences. PostgreSQL las
        protege por defecto (RESTRICT), así que hay que limpiarlas explícitamente
        antes de borrar la cámara.

        Estrategia:
        - notification_preferences.camera_id (nullable) → SET NULL
          (preservamos la preferencia del usuario, solo desligamos de la cámara).
        - user_camera_permissions, events, recordings → DELETE
          (no tienen sentido sin la cámara).
        """
        self._logger.info(f"Borrando cámara {camera_id} y sus dependencias...")

        # 1) Parar el worker
        try:
            self._camera_manager.stop_camera(camera_id)
        except Exception as e:
            self._logger.warning(f"stop_camera({camera_id}) falló: {e}")

        # 2) Limpiar referencias en BD
        from backend.app.database.connection import db_manager
        from sqlalchemy import text
        try:
            with db_manager.get_session() as session:
                # Importar los modelos dentro para evitar ciclos
                from backend.app.database.models import (
                    NotificationPreference, UserCameraPermission,
                    Event, Recording,
                )

                # Notification preferences: SET NULL (la preferencia sigue vigente
                # para "cualquier cámara" del mismo event_type).
                cnt_np = session.query(NotificationPreference).filter_by(
                    camera_id=camera_id
                ).update({"camera_id": None})
                # Permisos por cámara: DELETE
                cnt_p = session.query(UserCameraPermission).filter_by(
                    camera_id=camera_id
                ).delete()
                # Eventos: DELETE
                cnt_e = session.query(Event).filter_by(camera_id=camera_id).delete()
                # Grabaciones: DELETE (archivos físicos quedan pero el registro DB no)
                cnt_r = session.query(Recording).filter_by(camera_id=camera_id).delete()

                session.commit()
                self._logger.info(
                    f"Cámara {camera_id}: limpiado {cnt_np} notif_prefs, "
                    f"{cnt_p} permisos, {cnt_e} eventos, {cnt_r} grabaciones"
                )
        except Exception as e:
            self._logger.error(f"Error limpiando dependencias de cámara {camera_id}: {e}")
            raise

        # 3) Finalmente borrar la cámara
        return self._camera_repo.delete(camera_id)

    def toggle_camera(self, camera_id: int, active: bool) -> dict | None:
        """
        Activa o desactiva una cámara.

        Args:
            camera_id: ID de la cámara
            active: True para activar, False para desactivar

        Returns:
            Cámara actualizada o None
        """
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            return None

        camera.is_active = active
        updated = self._camera_repo.update(camera)

        if active:
            # CRÍTICO: El registro MJPEG es automático en CameraManager
            self._camera_manager.start_camera(updated)
        else:
            self._camera_manager.stop_camera(camera_id)

        return self.get_camera(camera_id)

    def set_ai_camera(self, camera_id: int) -> bool:
        """
        Establece una cámara como la única cámara con AI activada.
        Desactiva has_ai en todas las demás.

        Args:
            camera_id: ID de la cámara para AI

        Returns:
            True si se actualizó correctamente
        """
        try:
            # Desactivar AI en todas las cámaras primero
            all_cameras = self._camera_repo.get_all()
            for cam in all_cameras:
                if cam.has_ai:
                    cam.has_ai = False
                    self._camera_repo.update(cam)

            # Activar en la seleccionada
            target = self._camera_repo.get_by_id(camera_id)
            if target:
                target.has_ai = True
                self._camera_repo.update(target)
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error al establecer cámara AI {camera_id}: {e}")
            return False

    def discover_cameras(self, timeout: int = 120,
                         subnet_scan: bool = True) -> list[dict]:
        """
        Descubre cámaras ONVIF en la red local.

        Args:
            timeout: segundos para WS-Discovery (es bloqueante).
            subnet_scan: si True y WS-Discovery devuelve 0, escanea el subnet.
                         Ponlo en False para arranques rápidos.
        """
        try:
            return self._onvif_discovery.discover(timeout=timeout,
                                                  subnet_scan=subnet_scan)
        except Exception as e:
            self._logger.error(f"Error en descubrimiento ONVIF: {e}")
            return []

    def get_camera_status(self, camera_id: int) -> dict:
        """
        Obtiene estado detallado de una cámara.

        Args:
            camera_id: ID de la cámara

        Returns:
            Diccionario con estado del worker e info de cámara
        """
        worker = self._camera_manager.get_worker(camera_id)
        camera = self._camera_repo.get_by_id(camera_id)

        return {
            "camera_id": camera_id,
            "worker_status": worker.get_status() if worker else None,
            "camera_info": camera.to_dict() if camera and hasattr(camera, 'to_dict') else self._camera_to_dict(camera) if camera else None
        }

    def _camera_to_dict(self, camera: Camera) -> dict:
        """Helper para convertir Camera a dict si el modelo no tiene to_dict."""
        return {
            "id": camera.id,
            "name": camera.name,
            "ip_address": camera.ip_address,
            "rtsp_url": camera.rtsp_url,
            "onvif_url": camera.onvif_url,
            "username": camera.username,
            "is_active": camera.is_active,
            "has_ai": camera.has_ai,
            "has_ptz": camera.has_ptz,
            "has_leds": camera.has_leds,
            "has_audio": camera.has_audio,
            "is_dual_lens": camera.is_dual_lens,
            "resolution_width": camera.resolution_width,
            "resolution_height": camera.resolution_height,
            "fps": camera.fps,
            "connection_type": camera.connection_type if hasattr(camera, 'connection_type') else "unknown"
        }
    
    def ptz_control(self, camera_id: int, direction: str, speed: float = 0.5) -> dict:
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise ValueError("Cámara no encontrada")
        if not camera.has_ptz:
            raise ValueError("La cámara no soporta PTZ")

        from backend.app.cameras.ptz_controller import ptz_manager
        controller = ptz_manager.get(camera)
        if not controller.is_supported():
            # Reintentar una vez: la cámara puede haber estado offline al cargar el cache
            ptz_manager.drop(camera_id)
            controller = ptz_manager.get(camera)
            if not controller.is_supported():
                raise RuntimeError("PTZ no disponible (no se pudo conectar por ONVIF)")

        if direction == "stop":
            success = controller.stop()
        else:
            success = controller.move(direction, speed=speed)
        if not success:
            raise RuntimeError(f"Error moviendo PTZ hacia {direction}")
        return {"direction": direction, "status": "ok"}

    def ptz_presets(self, camera_id: int) -> list[dict]:
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise ValueError("Cámara no encontrada")
        from backend.app.cameras.ptz_controller import ptz_manager
        return ptz_manager.get(camera).get_presets()

    def ptz_goto_preset(self, camera_id: int, preset_token: str) -> dict:
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise ValueError("Cámara no encontrada")
        from backend.app.cameras.ptz_controller import ptz_manager
        ok = ptz_manager.get(camera).goto_preset(preset_token)
        if not ok:
            raise RuntimeError(f"Error yendo a preset {preset_token}")
        return {"preset_token": preset_token, "status": "ok"}

    def ptz_save_preset(self, camera_id: int, name: str) -> dict:
        """Guarda la posición PTZ actual como preset con [name]."""
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise ValueError("Cámara no encontrada")
        if not name or not name.strip():
            raise ValueError("name requerido")
        from backend.app.cameras.ptz_controller import ptz_manager
        token = ptz_manager.get(camera).set_preset(name.strip())
        if not token:
            raise RuntimeError("La cámara no aceptó el preset")
        return {"preset_token": token, "name": name.strip(), "status": "ok"}

    # ------------------------------------------------------------------
    # LEDs (IR-Cut filter)
    # ------------------------------------------------------------------
    def set_led_state(self, camera_id: int, state: str) -> dict:
        """
        state ∈ {"on", "off", "auto"} → IR-Cut OFF / ON / AUTO.
        """
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise ValueError("Cámara no encontrada")
        from backend.app.cameras.led_controller import led_manager
        ctrl = led_manager.get(camera)
        if not ctrl.is_supported():
            led_manager.drop(camera_id)
            ctrl = led_manager.get(camera)
            if not ctrl.is_supported():
                raise RuntimeError("LEDs no disponibles (ONVIF Imaging no respondió)")

        state_l = (state or "").lower()
        if state_l == "on":
            ok = ctrl.turn_on()
        elif state_l == "off":
            ok = ctrl.turn_off()
        elif state_l == "auto":
            ok = ctrl.set_auto()
        else:
            raise ValueError("state debe ser on|off|auto")
        if not ok:
            raise RuntimeError(f"Error aplicando estado LED {state_l}")
        return {"camera_id": camera_id, "state": state_l}

    # ------------------------------------------------------------------
    # Audio bidireccional (talk-back)
    # ------------------------------------------------------------------
    def audio_talk(self, camera_id: int, data: dict | None = None) -> dict:
        """Inicia stream de micrófono local hacia la cámara."""
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise ValueError("Cámara no encontrada")
        from backend.app.cameras.audio_controller import audio_manager
        ctrl = audio_manager.get(camera)
        if not ctrl.is_supported():
            raise RuntimeError("Audio no disponible en esta cámara")
        mic = (data or {}).get("mic_device")
        ok = ctrl.start_talk(mic_device=mic)
        if not ok:
            raise RuntimeError("No se pudo iniciar audio (revisa logs FFmpeg)")
        return {"camera_id": camera_id, "talking": True}

    def audio_stop(self, camera_id: int) -> dict:
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise ValueError("Cámara no encontrada")
        from backend.app.cameras.audio_controller import audio_manager
        ctrl = audio_manager.get(camera)
        ok = ctrl.stop_talk()
        return {"camera_id": camera_id, "talking": False, "stopped": ok}

    def audio_status(self, camera_id: int) -> dict:
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise ValueError("Cámara no encontrada")
        from backend.app.cameras.audio_controller import audio_manager
        ctrl = audio_manager.get(camera)
        return {
            "camera_id": camera_id,
            "supported": ctrl.is_supported(),
            "active": ctrl.is_active(),
            "listening": ctrl.is_listening(),
        }

    def audio_listen_start(self, camera_id: int) -> dict:
        """Reproduce el audio de la cámara en los altavoces locales del servidor."""
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise ValueError("Cámara no encontrada")
        if not camera.rtsp_url:
            raise ValueError("La cámara no tiene RTSP configurada")
        from backend.app.cameras.audio_controller import audio_manager
        ctrl = audio_manager.get(camera)
        ok = ctrl.start_listen(camera.rtsp_url)
        if not ok:
            raise RuntimeError("No se pudo iniciar listen (¿ffplay en PATH? ¿stream tiene audio?)")
        return {"camera_id": camera_id, "listening": True}

    def audio_listen_stop(self, camera_id: int) -> dict:
        camera = self._camera_repo.get_by_id(camera_id)
        if not camera:
            raise ValueError("Cámara no encontrada")
        from backend.app.cameras.audio_controller import audio_manager
        ctrl = audio_manager.get(camera)
        ok = ctrl.stop_listen()
        return {"camera_id": camera_id, "listening": False, "stopped": ok}

    @staticmethod
    def list_audio_input_devices() -> list[str]:
        """Lista de micrófonos disponibles en el host (para el selector de la GUI)."""
        from backend.app.cameras.audio_controller import list_input_audio_devices
        return list_input_audio_devices()