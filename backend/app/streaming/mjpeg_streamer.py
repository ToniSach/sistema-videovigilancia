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
from concurrent.futures import ThreadPoolExecutor
from queue import Queue, Full
from dataclasses import dataclass

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

        # Encoding adaptativo:
        #   - Solo 1 encode en vuelo por stream (drop-newest si encoder ocupado).
        #   - Esto evita el backlog del executor que causaba latencia de varios segundos.
        #   - Estadísticas de tiempo de encode para auto-ajustar resolución/quality.
        self._encoding_in_flight: Dict[Tuple[int, str], bool] = {}
        self._encode_times: Dict[Tuple[int, str], list[float]] = {}
        # Timestamps absolutos de send (para calcular fps REAL en wall-clock,
        # distinto de la capacidad teórica del encoder).
        self._send_times: Dict[Tuple[int, str], list[float]] = {}
        # Cap de frecuencia de encoding por stream. La cámara puede entregar
        # 60fps (passthrough), pero el cliente pull-based consume a 15fps. Si
        # encodeáramos los 60 frames estaríamos quemando 4× CPU sin que el
        # cliente lo vea. Forzamos un intervalo mínimo entre encodes para
        # alinear con el FPS efectivo del cliente y bajar el frame_age.
        self._last_encode_t: Dict[Tuple[int, str], float] = {}
        try:
            from backend.app.config import settings as _settings
            target_fps = float(getattr(_settings, "MJPEG_TARGET_FPS", 15.0))
        except Exception:
            target_fps = 15.0
        self._min_encode_interval = 1.0 / max(target_fps, 1.0)
        # Edad del frame en el momento del send (now - frame_data.timestamp).
        # Mide la latencia ACUMULADA desde que el worker capturó el frame
        # hasta que el encoder lo envía al cliente. Si esto es >500ms hay
        # un cuello de botella en el backend; si es <500ms y aun así el
        # cliente lo ve lento, el delay viene de la cámara (GOP) o de la red.
        self._frame_age_samples: Dict[Tuple[int, str], list[float]] = {}
        # Throttle del WARNING "encode lento": último ts por (key) para no
        # spamear cuando hay backpressure sostenida (1 log cada 60s).
        self._last_slow_warning_t: Dict[Tuple[int, str], float] = {}
        self._dropped_for_backlog: Dict[Tuple[int, str], int] = {}

        # POOL DEDICADO para encoding JPEG. AISLADO del GlobalExecutor.
        # Motivo: GlobalExecutor también ejecuta tareas largas y bloqueantes
        # (HTTP a Telegram ~2s, cv2.imwrite snapshot ~30ms, ffmpeg splice
        # subprocess ~1-3s). Cuando la IA fires muchos eventos seguidos
        # (cooldown bajo + escena con movimiento), las 50 workers globales
        # quedan ocupadas en HTTP/disco y la tarea de encode MJPEG espera
        # en cola → cliente recibe frames en ráfagas → "salta segundos".
        # Pool dedicado de 4 hilos: suficiente para ~4 streams en paralelo
        # encoding ~20ms cada uno = 200fps cap teórico, > que cualquier
        # caso real. Si todos los hilos están ocupados, el drop-newest
        # natural del in_flight flag protege la cola.
        self._encode_pool = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="MJPEGEncoder"
        )

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

        # Apagar el pool dedicado de encoders (no esperar tareas en vuelo,
        # son frames ya obsoletos al cerrar)
        try:
            self._encode_pool.shutdown(wait=False)
        except Exception:
            pass

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
        """
        Encolar 1 encoding por stream. Si ya hay uno en vuelo, DESCARTAMOS el
        frame nuevo (drop-newest). Esto mantiene la latencia baja: el cliente
        siempre verá el frame "más recientemente encodificable", no uno viejo
        rescatado de una cola atrasada.
        """
        key = (camera_id, stream_id)
        now = time.time()

        with self._lock:
            if key not in self._clients or not self._clients[key]:
                return
            if self._encoding_in_flight.get(key, False):
                # Encoder ocupado — descartamos este frame
                self._dropped_for_backlog[key] = self._dropped_for_backlog.get(key, 0) + 1
                return
            # Throttle a MJPEG_TARGET_FPS (default 15fps): si encodeamos a
            # 60fps cuando el cliente solo lee 15fps, gastamos 4× CPU en
            # vano y el frame_age sube por contención del executor.
            last_t = self._last_encode_t.get(key, 0.0)
            if now - last_t < self._min_encode_interval:
                return
            self._last_encode_t[key] = now
            self._encoding_in_flight[key] = True

        # Liberado el lock, lanzar encoding en el pool DEDICADO (no global).
        # Si el pool dedicado se llenara (cosa rara con 4 workers y ~20ms
        # encode), submit lanza RuntimeError; lo capturamos para no romper.
        try:
            self._encode_pool.submit(self._encode_and_distribute, key, frame_data)
        except RuntimeError:
            with self._lock:
                self._encoding_in_flight[key] = False
            logger.warning(f"[MJPEG {key}] encoder pool rechazó task")

    def _encode_and_distribute(self, key, frame_data: FrameData) -> None:
        """
        Ejecutado en worker thread del GlobalExecutor.
        Mide el tiempo de encode + edad del frame para diagnóstico de latencia.
        """
        t0 = time.time()
        try:
            jpeg_bytes = self._encode_frame(frame_data)
            if jpeg_bytes is None:
                return

            boundary_frame = self._build_mjpeg_frame(jpeg_bytes)

            with self._lock:
                clients = list(self._clients.get(key, {}).items())

            for client_id, client_info in clients:
                self._send_to_client(client_info, boundary_frame, client_id)
        except Exception as e:
            logger.error(f"Error en encoding MJPEG: {e}")
        finally:
            elapsed = time.time() - t0
            send_time = time.time()
            # Edad del frame = cuánto tiempo pasó desde que el worker FFmpeg
            # lo capturó hasta que terminamos de enviarlo al cliente. Este
            # número es la latencia REAL del backend.
            try:
                frame_age = send_time - float(frame_data.timestamp)
            except Exception:
                frame_age = 0.0

            with self._lock:
                self._encoding_in_flight[key] = False
                tlist = self._encode_times.setdefault(key, [])
                tlist.append(elapsed)
                if len(tlist) > 30:
                    del tlist[0]
                # Muestras de edad del frame
                alist = self._frame_age_samples.setdefault(key, [])
                alist.append(frame_age)
                if len(alist) > 30:
                    del alist[0]
                # Muestras de timestamp REAL de envío para calcular fps verdadero
                stlist = self._send_times.setdefault(key, [])
                stlist.append(send_time)
                if len(stlist) > 30:
                    del stlist[0]
                # Log periódico de estadísticas (cada 30 frames encodificados)
                if len(tlist) == 30:
                    avg_ms = (sum(tlist) / len(tlist)) * 1000
                    max_ms = max(tlist) * 1000
                    dropped = self._dropped_for_backlog.get(key, 0)
                    # fps REAL = 30 / (último_send - primer_send), no la
                    # capacidad teórica del encoder. Antes usábamos
                    # `30 / sum(encode_times)`, lo cual con encodes de 16ms
                    # daba "60 fps" aunque el encoder corriera a 15 fps real.
                    if len(stlist) >= 2 and stlist[-1] > stlist[0]:
                        fps_encoded = (len(stlist) - 1) / (stlist[-1] - stlist[0])
                    else:
                        fps_encoded = 0
                    age_avg_ms = (sum(alist) / len(alist)) * 1000 if alist else 0
                    age_max_ms = (max(alist) * 1000) if alist else 0
                    # Stats internas: solo si hay problemas (alto encode time,
                    # alta edad del frame, o drops). Sino DEBUG.
                    # WARNING throttled a 1/60s.
                    if max_ms > 50 or dropped > 0 or age_max_ms > 500:
                        now = time.time()
                        last_t = self._last_slow_warning_t.get(key, 0)
                        if now - last_t >= 60:
                            self._last_slow_warning_t[key] = now
                            logger.warning(
                                f"[MJPEG {key}] encode lento/drops/lag: "
                                f"encode_avg={avg_ms:.0f}ms encode_max={max_ms:.0f}ms "
                                f"frame_age_avg={age_avg_ms:.0f}ms "
                                f"frame_age_max={age_max_ms:.0f}ms "
                                f"fps≈{fps_encoded:.1f} dropped={dropped}"
                            )
                    else:
                        logger.debug(
                            f"[MJPEG {key}] encode={avg_ms:.0f}ms "
                            f"age={age_avg_ms:.0f}ms fps≈{fps_encoded:.1f}"
                        )

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

        IMPORTANTE: el cliente DEBE haber sido registrado previamente vía
        register_client() por el endpoint HTTP. Aquí sólo consumimos la cola
        ya existente. Antes esta función llamaba a register_client otra vez
        y eso dejaba huérfana la primera cola (encoder escribía allí, nadie
        leía) provocando trabones visibles.
        """
        key = (camera_id, stream_id)

        with self._lock:
            client_info = self._clients.get(key, {}).get(client_id)
        if client_info is None:
            logger.warning(
                f"generate_stream: cliente {client_id} no registrado en {key}"
            )
            return
        queue = client_info.queue
        
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
    
    def get_latency_stats(self, camera_id: int, stream_id: str = "main") -> dict:
        """
        Devuelve métricas de latencia para diagnosticar dónde se acumula
        el delay. Pensado para el endpoint /cameras/<id>/latency.

        - encode_ms: cuánto tarda cv2.imencode (CPU del backend).
        - frame_age_ms: now - frame_data.timestamp en el send.
          Esto es la latencia REAL desde captura hasta envío.
          Si <500ms el backend está fluido; si >500ms hay backlog interno.
        - dropped: frames descartados por encoder saturado.
        - clients: clientes HTTP activos.

        El delay PERCIBIDO por el usuario también incluye la latencia de
        red TCP + parsing del cliente + render Qt, que no medimos aquí.
        """
        key = (camera_id, stream_id)
        with self._lock:
            tlist = list(self._encode_times.get(key, []))
            alist = list(self._frame_age_samples.get(key, []))
            n_clients = len(self._clients.get(key, {}))
            dropped = self._dropped_for_backlog.get(key, 0)

        def _stats(xs):
            if not xs:
                return {"samples": 0, "avg_ms": None, "p50_ms": None, "max_ms": None}
            xs_sorted = sorted(xs)
            n = len(xs_sorted)
            p50 = xs_sorted[n // 2]
            return {
                "samples": n,
                "avg_ms": round(sum(xs_sorted) / n * 1000, 1),
                "p50_ms": round(p50 * 1000, 1),
                "max_ms": round(max(xs_sorted) * 1000, 1),
            }

        return {
            "camera_id": camera_id,
            "stream_id": stream_id,
            "active_clients": n_clients,
            "encode": _stats(tlist),
            "frame_age": _stats(alist),
            "dropped_for_backlog": dropped,
        }

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
    
    # Parámetros configurables vía variables de entorno (settings)
    # Buscan el equilibrio calidad/latencia para 1 lente de cámara dual.
    DEFAULT_MAX_WIDTH = 960    # ancho máximo del JPEG (px)
    DEFAULT_QUALITY = 85        # calidad JPEG (0-100)

    def _encode_frame(self, frame_data: FrameData) -> Optional[bytes]:
        """
        Encode adaptativo. Para minimizar latencia mantenemos la resolución
        moderada (max 960px ancho) y JPEG calidad 85. Si el cliente quiere más
        calidad puede pedirla vía query string en el futuro.
        """
        try:
            from backend.app.config import settings
            max_w = getattr(settings, "MJPEG_MAX_WIDTH", self.DEFAULT_MAX_WIDTH)
            quality = getattr(settings, "MJPEG_QUALITY", self.DEFAULT_QUALITY)

            frame = frame_data.frame
            h, w = frame.shape[:2]
            if w > max_w:
                scale = max_w / w
                new_size = (max_w, int(h * scale))
                # INTER_AREA produce mejor calidad al downsample que INTER_LINEAR
                frame = cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)
            success, buffer = cv2.imencode(
                ".jpg", frame,
                [
                    cv2.IMWRITE_JPEG_QUALITY, int(quality),
                    cv2.IMWRITE_JPEG_OPTIMIZE, 0,    # 0 = más rápido (no optimize huffman)
                    cv2.IMWRITE_JPEG_PROGRESSIVE, 0,
                ],
            )
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

    def cleanup_stream(self, camera_id: int, stream_id: str) -> None:
        """
        API pública: cierra todos los clientes de un stream y libera recursos.
        Se llama cuando una cámara se detiene/elimina para no dejar generators
        bloqueados en queue.get() apuntando a una cámara que ya no produce.
        """
        key = (camera_id, stream_id)
        with self._lock:
            if key not in self._clients:
                return
            client_ids = list(self._clients[key].keys())
            # Vaciar colas para desbloquear cualquier generator dormido
            for client_id in client_ids:
                client_info = self._clients[key][client_id]
                while not client_info.queue.empty():
                    try:
                        client_info.queue.get_nowait()
                    except Exception:
                        break
            # Borrar el bucket completo de clientes y estado de encoding.
            # Los generators saldrán solos en la próxima iteración al ver
            # que su client_id ya no está en _clients.
            del self._clients[key]
            self._encoding_in_flight.pop(key, None)
            self._encode_times.pop(key, None)
            self._last_slow_warning_t.pop(key, None)
        if client_ids:
            logger.info(
                f"cleanup_stream {key}: {len(client_ids)} cliente(s) cerrados"
            )


# ============================================================================
# INSTANCIA SINGLETON
# ============================================================================

# Instancia global del streamer (patrón singleton).
# Lee el límite de clientes por stream desde settings (configurable en .env
# con MAX_MJPEG_CLIENTS_PER_CAMERA, default 25).
def _build_mjpeg_streamer():
    try:
        from backend.app.config import settings
        max_clients = int(getattr(settings, "MAX_MJPEG_CLIENTS_PER_CAMERA", 25))
    except Exception:
        max_clients = 25
    return MJPEGStreamer(max_clients_per_camera=max_clients)


mjpeg_streamer = _build_mjpeg_streamer()