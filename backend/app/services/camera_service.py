import logging
from typing import Optional

from ..cameras.camera_manager import CameraManager
from ..cameras.onvif_discovery import ONVIFDiscovery
from ..database.models import Camera
from ..database.repositories.camera_repository import CameraRepository
from ..streaming.frame_distributor import FrameDistributor
from ..streaming.frame_buffer import FrameData
from ..streaming.mjpeg_streamer import mjpeg_streamer


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
        self._mjpeg = mjpeg_streamer
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

        Args:
            data: Diccionario con datos de la cámara

        Returns:
            Diccionario con la cámara creada
        """
        # Validar si hay IP para probar
        ip = data.get('ip_address')
        if ip:
            try:
                discovery = ONVIFDiscovery()
                device_info = discovery.probe_single_ip(ip)
                if device_info:
                    # Auto-completar desde el descubrimiento
                    data['rtsp_url'] = device_info['rtsp_url']
                    data['username'] = device_info['username']
                    data['password'] = device_info['password']
                    data['manufacturer'] = device_info.get('manufacturer', 'Unknown')
                    data['connection_type'] = device_info.get('connection_type', 'manual')
                    # Opcionalmente actualizar resolución/fps
                    data['resolution_width'] = device_info.get('resolution_width', data.get('resolution_width', 1920))
                    data['resolution_height'] = device_info.get('resolution_height', data.get('resolution_height', 1080))
                    data['fps'] = device_info.get('fps', data.get('fps', 15))
                else:
                    # Si falla el probe, no guardar (o guardar con advertencia)
                    raise ValueError("No se pudo conectar a la cámara. Verifique IP y credenciales.")
            except Exception as e:
                raise ValueError(f"Error validando cámara: {e}")

        # Crear objeto Camera
        camera = Camera(
            name=data.get('name', 'Nueva Cámara'),
            ip_address=data.get('ip_address', ''),
            rtsp_url=data.get('rtsp_url', ''),
            onvif_url=data.get('onvif_url', ''),
            username=data.get('username', ''),
            password=data.get('password', ''),
            profile_token=data.get('profile_token', ''),
            is_active=data.get('is_active', True),
            has_ai=data.get('has_ai', False),
            has_ptz=data.get('has_ptz', False),
            has_leds=data.get('has_leds', False),
            has_audio=data.get('has_audio', False),
            is_dual_lens=data.get('is_dual_lens', False),
            resolution_width=data.get('resolution_width', 1920),
            resolution_height=data.get('resolution_height', 1080),
            fps=data.get('fps', 25),
            connection_type=data.get('connection_type', 'manual')  # Nuevo campo
        )

        # Guardar en BD
        created_camera = self._camera_repo.create(camera)

        # Iniciar si está activa
        if created_camera.is_active:
            self._camera_manager.start_camera(created_camera)  # register_mjpeg=True por defecto

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
            # CRÍTICO: No necesitamos llamar start_mjpeg_for_camera aquí
            # porque restart_camera ya llama a start_camera con register_mjpeg=True

        return self.get_camera(camera_id)

    def delete_camera(self, camera_id: int) -> bool:
        """
        Elimina una cámara del sistema.

        Args:
            camera_id: ID de la cámara a eliminar

        Returns:
            True si se eliminó, False si no existía
        """
        # Detener worker primero
        self._camera_manager.stop_camera(camera_id)

        # Eliminar de BD
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

    def discover_cameras(self) -> list[dict]:
        """
        Descubre cámaras ONVIF en la red local.

        Returns:
            Lista de diccionarios con información de cámaras encontradas
        """
        try:
            discovered = self._onvif_discovery.discover()
            return discovered
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

    def start_mjpeg_for_camera(self, camera_id: int) -> None:
        """
        Registra el streamer MJPEG como consumer del distributor de la cámara.
        
        NOTA: Este método se mantiene por compatibilidad pero YA NO SE USA
        automáticamente. El registro MJPEG ahora se hace automáticamente
        en CameraManager.start_camera().
        
        Args:
            camera_id: ID de la cámara
        """
        distributor = self._camera_manager.get_distributor(camera_id)
        if distributor:
            distributor.register_consumer(
                "mjpeg", 
                lambda fd: self._mjpeg.update_frame(camera_id, fd)
            )
            self._logger.info(f"MJPEG registrado manualmente para cámara {camera_id} (método legacy)")

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