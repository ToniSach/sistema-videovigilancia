"""
MJPEG Streaming Server - Servidor de streaming MJPEG para cámaras.
Implementa gestión de colas por cliente y generador de streams.
Soporta múltiples streams por cámara (main, l1, l2) mediante stream_id.
"""

import threading
import logging
import time
from typing import Dict, Optional, Tuple
from collections import defaultdict
from queue import Queue, Full
from dataclasses import dataclass
from ..core.executor import global_executor

import cv2

from .frame_buffer import FrameData

logger = logging.getLogger(__name__)


@dataclass
class ClientInfo:
    """Información de un cliente conectado."""
    queue: Queue
    last_access: float
    dropped_frames: int = 0


class MJPEGStreamer:
    """
    Servidor de streaming MJPEG.
    Gestiona colas individuales por cliente para evitar bloqueos.
    Soporta streams identificados por (camera_id, stream_id).
    """
    
    DEFAULT_MAX_CLIENTS = 10
    DEFAULT_CAMERA_TIMEOUT = 30
    DEFAULT_QUEUE_SIZE = 1
    
    def __init__(
        self,
        max_clients_per_camera: int = DEFAULT_MAX_CLIENTS,
        camera_timeout: int = DEFAULT_CAMERA_TIMEOUT,
        queue_size: int = DEFAULT_QUEUE_SIZE
    ):
        self._max_clients = max_clients_per_camera
        self._camera_timeout = camera_timeout
        self._queue_size = queue_size
        
        # Clave: (camera_id, stream_id) -> {client_id: ClientInfo}
        self._clients: Dict[Tuple[int, str], Dict[str, ClientInfo]] = defaultdict(dict)
        self._lock = threading.RLock()
        
        self._cleanup_thread: Optional[threading.Thread] = None
        self._running = False
        
    def start(self) -> None:
        """Inicia el thread de limpieza de clientes inactivos."""
        self._running = True
        self._cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
        self._cleanup_thread.start()
        logger.info("MJPEGStreamer iniciado")
        
    def shutdown(self) -> None:
        """Detiene el streamer y limpia todas las colas."""
        self._running = False
        
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=2.0)
            
        with self._lock:
            for key in list(self._clients.keys()):
                self._cleanup_stream(key)
            self._clients.clear()
            
        logger.info("MJPEGStreamer detenido")
    
    def register_client(self, camera_id: int, stream_id: str, client_id: str) -> Optional[Queue]:
        """
        Registra un nuevo cliente para un stream específico (cámara + stream_id).
        Retorna la cola asignada o None si se alcanzó el límite.
        """
        key = (camera_id, stream_id)
        with self._lock:
            stream_clients = self._clients[key]
            
            if len(stream_clients) >= self._max_clients:
                logger.warning(f"Límite de clientes alcanzado para stream {key}")
                return None
            
            queue = Queue(maxsize=self._queue_size)
            stream_clients[client_id] = ClientInfo(
                queue=queue,
                last_access=time.time(),
                dropped_frames=0
            )
            
            logger.info(f"Cliente {client_id} registrado para stream {key} "
                       f"({len(stream_clients)}/{self._max_clients})")
            return queue
    
    def unregister_client(self, camera_id: int, stream_id: str, client_id: str) -> None:
        """Elimina un cliente y su cola asociada de un stream específico."""
        key = (camera_id, stream_id)
        with self._lock:
            if key in self._clients:
                if client_id in self._clients[key]:
                    client_info = self._clients[key][client_id]
                    # Vaciar cola para liberar memoria
                    while not client_info.queue.empty():
                        try:
                            client_info.queue.get_nowait()
                        except:
                            break
                    del self._clients[key][client_id]
                    logger.info(f"Cliente {client_id} desregistrado de stream {key}")
                    
                    if not self._clients[key]:
                        del self._clients[key]

    def update_frame(self, camera_id: int, stream_id: str, frame_data: FrameData) -> None:
        key = (camera_id, stream_id)
        with self._lock:
            if key not in self._clients:
                return
            clients = dict(self._clients[key])

        # ✅ Encoding fuera del lock, síncrono (no en executor)
        jpeg_bytes = self._encode_frame(frame_data)
        if jpeg_bytes is None:
            return

        boundary_frame = self._build_mjpeg_frame(jpeg_bytes)

        # ✅ Distribución directa con drop-oldest, sin executor
        with self._lock:
            for client_id, client_info in list(self._clients.get(key, {}).items()):
                self._send_to_client(client_info, boundary_frame, client_id)

    def _send_to_client(self, client_info: ClientInfo, frame: bytes, client_id: str):
        """
        Gestiona la cola de un cliente específico. 
        Si la cola está llena, aplica la política 'drop oldest'.
        """
        try:
            # Lógica 'Drop Oldest': Si la cola está llena, sacar el más antiguo
            if client_info.queue.full():
                try:
                    client_info.queue.get_nowait()
                    client_info.dropped_frames += 1
                except:
                    pass

            # Insertar el nuevo frame
            client_info.queue.put_nowait(frame)
            client_info.last_access = time.time()

        except Full:
            client_info.dropped_frames += 1
        except Exception as e:
            logger.error(f"Error enviando frame al cliente {client_id}: {e}")    
            
    def generate_stream(self, camera_id: int, stream_id: str, client_id: str):
        """
        Generador para Flask streaming response.
        Yields bytes del stream MJPEG para un stream específico.
        """
        key = (camera_id, stream_id)
        
        # Registrar al cliente y obtener su cola
        queue = self.register_client(camera_id, stream_id, client_id)
        
        if queue is None:
            return
        
        try:
            # Enviar header inicial MJPEG
            yield b'--frame\r\n'
            
            while True:
                try:
                    frame_data = queue.get(timeout=self._camera_timeout)
                    yield frame_data
                    
                except Exception:
                    # Timeout o cola vacía - verificar si el cliente sigue registrado
                    with self._lock:
                        if key not in self._clients or client_id not in self._clients[key]:
                            break
                        
                        # Verificar timeout de inactividad del cliente
                        client_info = self._clients[key][client_id]
                        if time.time() - client_info.last_access > self._camera_timeout:
                            logger.info(f"Timeout de cliente {client_id} en stream {key}")
                            break
                            
        finally:
            self.unregister_client(camera_id, stream_id, client_id)
    
    def get_stats(self, camera_id: Optional[int] = None, stream_id: Optional[str] = None) -> dict:
        """Retorna estadísticas de clientes y frames."""
        with self._lock:
            if camera_id is not None:
                if stream_id is not None:
                    key = (camera_id, stream_id)
                    if key not in self._clients:
                        return {"clients": 0, "dropped_frames": 0}
                    clients = self._clients[key]
                    total_dropped = sum(c.dropped_frames for c in clients.values())
                    return {
                        "clients": len(clients),
                        "dropped_frames": total_dropped,
                        "client_ids": list(clients.keys())
                    }
                else:
                    # Resumen de todos los streams de esa cámara
                    total_clients = 0
                    total_dropped = 0
                    streams = {}
                    for (cam_id, sid), clients in self._clients.items():
                        if cam_id == camera_id:
                            dropped = sum(c.dropped_frames for c in clients.values())
                            total_clients += len(clients)
                            total_dropped += dropped
                            streams[sid] = {"clients": len(clients), "dropped_frames": dropped}
                    return {
                        "camera_id": camera_id,
                        "total_clients": total_clients,
                        "total_dropped_frames": total_dropped,
                        "streams": streams
                    }
            
            # Stats globales
            total_clients = sum(len(c) for c in self._clients.values())
            total_dropped = sum(
                c.dropped_frames 
                for clients in self._clients.values() 
                for c in clients.values()
            )
            return {
                "total_streams": len(self._clients),
                "total_clients": total_clients,
                "total_dropped_frames": total_dropped
            }
    
    def _encode_frame(self, frame_data: FrameData) -> Optional[bytes]:
        try:
            frame = frame_data.frame
            # Redimensionar a 640x360 para streaming
            if frame.shape[1] > 640:
                scale = 640 / frame.shape[1]
                new_size = (640, int(frame.shape[0] * scale))
                frame = cv2.resize(frame, new_size, interpolation=cv2.INTER_LINEAR)
            success, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            return buffer.tobytes() if success else None
        except Exception as e:
            logger.error(f"Error encoding: {e}")
            return None
    
    def _build_mjpeg_frame(self, jpeg_bytes: bytes) -> bytes:
        """Construye el frame MJPEG con headers HTTP multipart."""
        return (
            b'Content-Type: image/jpeg\r\n'
            b'Content-Length: ' + str(len(jpeg_bytes)).encode() + b'\r\n'
            b'\r\n' + 
            jpeg_bytes + 
            b'\r\n--frame\r\n'
        )
    
    def _cleanup_worker(self) -> None:
        """Thread que limpia clientes inactivos periódicamente."""
        while self._running:
            time.sleep(10)  # Cada 10 segundos
            
            try:
                current_time = time.time()
                with self._lock:
                    for key in list(self._clients.keys()):
                        inactive_clients = []
                        for client_id, client_info in self._clients[key].items():
                            if current_time - client_info.last_access > self._camera_timeout:
                                inactive_clients.append(client_id)
                        
                        for client_id in inactive_clients:
                            logger.info(f"Limpiando cliente inactivo {client_id} del stream {key}")
                            self.unregister_client(key[0], key[1], client_id)
                            
                        if not self._clients[key]:
                            del self._clients[key]
                            
            except Exception as e:
                logger.error(f"Error en cleanup worker: {e}")
    
    def _cleanup_stream(self, key: Tuple[int, str]) -> None:
        """Limpia todas las colas de un stream específico."""
        if key in self._clients:
            for client_info in self._clients[key].values():
                while not client_info.queue.empty():
                    try:
                        client_info.queue.get_nowait()
                    except:
                        break


# ============================================================================
# INSTANCIA SINGLETON
# ============================================================================

# Instancia global del streamer (patrón singleton)
mjpeg_streamer = MJPEGStreamer()