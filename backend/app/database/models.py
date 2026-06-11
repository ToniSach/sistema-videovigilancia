"""
================================================================================
MÓDULO: models — Esquema central de la base de datos del NVR/VMS
================================================================================

PROPÓSITO
    Definir TODO el esquema relacional del sistema de videovigilancia: las
    entidades persistentes, sus columnas, claves foráneas, índices, restricciones
    de unicidad y relaciones ORM. Es la única fuente de verdad del modelo de
    datos: tanto `db_manager.init_db()` (que ejecuta `Base.metadata.create_all`)
    como las migraciones Alembic se derivan de estas clases.

RESPONSABILIDAD PRINCIPAL
    Mapear el dominio (usuarios, cámaras, permisos, eventos, grabaciones,
    notificaciones, auditoría, tokens) a tablas PostgreSQL usando SQLAlchemy 2.0
    en estilo MODERNO: `DeclarativeBase` + anotaciones tipadas `Mapped[...]` con
    `mapped_column(...)`. Este estilo da type-safety estática y es el que se usa
    para la futura generación de diagramas UML/ER.

MULTI-TENENCIA (modelo de propiedad y compartición)
    - Un `User` (rol admin/user) es DUEÑO de cero o más `Camera` vía
      `Camera.owner_id` (relación 1:N).
    - Una cámara puede COMPARTIRSE con otros usuarios mediante
      `UserCameraPermission`, que materializa una relación N:M entre `User` y
      `Camera` con FLAGS por permiso (ver, PTZ, LEDs, audio, descargar
      grabaciones). Por eso `owner_id` por sí solo NO basta para autorizar: hay
      que consultar siempre `PermissionService` (que lee esta tabla puente).

DEPENDENCIAS IMPORTANTES
    sqlalchemy (Core + ORM) ... tipos de columna, FK, índices, relaciones.
    enum (stdlib) ............. `UserRole`, `EventType` (enumeraciones lógicas;
                                en BD se persisten como String, no como ENUM nativo).
    Este módulo NO importa nada del proyecto: es la capa más baja del backend.

COMPONENTES RELACIONADOS (quién consume el esquema)
    database.connection.DatabaseManager.init_db() → crea las tablas.
    database.repositories.* ........ leen/escriben estas entidades (patrón Repository).
    services.* (EventService, PermissionService, NotificationRouter...) → lógica de negocio.
    api.routes.* ................... serializan vía `to_dict()` hacia el cliente.
    database/migrations/ (Alembic) → versiona cambios de este esquema.

PUNTO DE ENTRADA EN LA ARQUITECTURA
    Capa de persistencia (la más baja). Todo dato que sobrevive a un reinicio
    pasa por aquí. Participa, como destino/origen de datos, en casi todos los
    pipelines: #2 Autenticación (User, RevokedToken), #7/#8 ONVIF/PTZ (Camera),
    #9 IA/detección y #10 Eventos (Event, NotificationLog), #11 Grabación y
    #14 Reproducción histórica (Recording), #13 Notificaciones
    (NotificationPreference/Channel/Day, UserTelegramChat, MobileDevice).

DIAGRAMA DE RELACIONES (ER simplificado)
    ┌──────────────┐ 1      N ┌────────────────────────┐ N      1 ┌──────────────┐
    │     User     │──────────│ UserCameraPermission   │──────────│    Camera    │
    │ (users)      │ owner    │ (puente N:M con flags) │          │ (cameras)    │
    └──────────────┘          └────────────────────────┘          └──────────────┘
        │  │  │  │                                                   │ owner_id │
        │  │  │  │  1                                            N   │          │
        │  │  │  └──────────────── cameras_owned ──────────────────►│          │
        │  │  │                                                      │ 1      N │
        │  │  │                              ┌───────────────────────┴──┐   │
        │  │  │                       events │ Event (events)           │◄──┘
        │  │  │                              └───────────┬──────────────┘
        │  │  │                                          │ 1
        │  │  │                                          │ N
        │  │  │                              ┌───────────┴──────────────┐
        │  │  │                              │ NotificationLog          │
        │  │  │                              │ (notification_logs)      │  user_id ─► User
        │  │  │                              └──────────────────────────┘
        │  │  │  1      N ┌──────────────────────────┐
        │  │  └──────────│ NotificationPreference   │ 1      N ┌────────────────────┐
        │  │             │ (notification_preferences│──────────│ NotificationChannel│
        │  │             │  ; camera_id opcional)   │          └────────────────────┘
        │  │             └────────────┬─────────────┘ 1      N ┌────────────────────┐
        │  │                          └──────────────────────────│ NotificationDay   │
        │  │                                                    └────────────────────┘
        │  │  1      N ┌──────────────┐   1      N ┌────────────────────┐
        │  ├──────────│ MobileDevice │   ├────────│ UserTelegramChat   │
        │  │          └──────────────┘   │        └────────────────────┘
        │  │  1      N ┌──────────────────────────┐  1      N ┌──────────────────┐
        │  ├──────────│ TelegramVerificationCode │  ├────────│ LinkToken        │
        │  │          └──────────────────────────┘  │        └──────────────────┘
        │  │  1      N ┌──────────────┐   1      N ┌──────────────────┐
        │  └──────────│ AuditLog     │   └────────│ RevokedToken     │
        │             └──────────────┘            └──────────────────┘
        Camera 1 ── N Recording (recordings)        SystemConfig (clave-valor, sin FK)
================================================================================
"""
import enum
from datetime import datetime, time, timedelta
from typing import List, Optional

from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, Text, Time, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Clase base declarativa para todos los modelos."""
    pass


class UserRole(enum.Enum):
    """
    Roles de autorización del sistema. Se persiste como String en
    `User.role` (no como ENUM nativo de PostgreSQL); estos valores son la
    referencia canónica usada al emitir/validar el JWT (pipeline #2).
    """
    ADMIN = "admin"
    USER = "user"


class User(Base):
    """
    QUÉ REPRESENTA
        Una cuenta del sistema (operador o administrador) que se autentica con
        usuario/contraseña. Es la raíz de la multi-tenencia: dueño de cámaras y
        sujeto de todos los permisos, preferencias y vínculos de notificación.

    RELACIONES
        1:N  User → Camera                  (cameras_owned; FK Camera.owner_id)
        1:N  User → UserCameraPermission    (permissions; cámaras compartidas)
        1:N  User → MobileDevice            (devices; dispositivos móviles)
        1:N  User → NotificationPreference  (notification_preferences)
        1:N  User → UserTelegramChat        (telegram_chats; chats de Telegram)
        (referenciado además por Event/NotificationLog/AuditLog/RevokedToken
         vía user_id, sin relación ORM inversa explícita en algunos casos).

    CUÁNDO SE ESCRIBE
        Alta de usuario (seed inicial admin en pipeline #1 Inicio, o creación vía
        API de administración). `is_active`/`role` se actualizan al gestionar
        cuentas.
    CUÁNDO SE LEE
        Pipeline #2 Autenticación en CADA login/refresh (verifica password_hash,
        is_active y emite el JWT con el rol). También al autorizar cada petición.
    """
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Relaciones
    cameras_owned: Mapped[List["Camera"]] = relationship(back_populates="owner")
    permissions: Mapped[List["UserCameraPermission"]] = relationship(back_populates="user")
    devices: Mapped[List["MobileDevice"]] = relationship(back_populates="user")
    notification_preferences: Mapped[List["NotificationPreference"]] = relationship(back_populates="user")
    telegram_chats: Mapped[List["UserTelegramChat"]] = relationship(back_populates="user")
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class Camera(Base):
    """
    QUÉ REPRESENTA
        Una cámara IP del NVR: su identidad de red (IP, RTSP/ONVIF, credenciales),
        sus capacidades (IA, PTZ, LEDs, audio, doble lente) y su estado de
        diagnóstico (último error, última conexión). Es la entidad central
        alrededor de la cual giran captura, IA, grabación y eventos.

    RELACIONES
        N:1  Camera → User                  (owner; FK owner_id, nullable)
        1:N  Camera → Event                 (events)
        1:N  Camera → Recording             (recordings)
        1:N  Camera → UserCameraPermission  (user_permissions; compartición N:M)

    CAMPOS DE DIAGNÓSTICO/ROBUSTEZ
        connection_type  — cómo se resolvió la URL: "onvif" / "rtsp_fallback" /
                           "manual" / "unknown".
        last_error_code  — última falla del worker FFmpeg/conexión: "AUTH_FAILED",
                           "CONN_REFUSED", "FROZEN", etc.
        fallback_url     — URL RTSP alternativa cuando ONVIF falló pero RTSP no.
        is_dual_lens     — cámara de lente doble (stream side-by-side que se parte
                           en dos streams lógicos "l1"/"l2"; ver dual_lens_splitter).

    CUÁNDO SE ESCRIBE
        Alta/edición de cámara (pipeline #7 ONVIF al descubrir/configurar).
        El pipeline #4 RTSP/FFmpeg actualiza last_error_code / last_connected_at
        según el resultado de cada (re)conexión.
    CUÁNDO SE LEE
        Pipeline #1 Inicio: `start_all_active()` lee las cámaras `is_active=True`.
        Pipelines #3 Visualización, #5 go2rtc, #8 PTZ, #9 IA y #11 Grabación leen
        URL/credenciales/capacidades para operar cada cámara.
    """
    __tablename__ = "cameras"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    rtsp_url: Mapped[str] = mapped_column(String(500), nullable=False)
    
    onvif_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    password: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    profile_token: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    has_ai: Mapped[bool] = mapped_column(Boolean, default=False)
    has_ptz: Mapped[bool] = mapped_column(Boolean, default=False)
    has_leds: Mapped[bool] = mapped_column(Boolean, default=False)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    is_dual_lens: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution_width: Mapped[int] = mapped_column(Integer, default=1920)
    resolution_height: Mapped[int] = mapped_column(Integer, default=1080)
    fps: Mapped[int] = mapped_column(Integer, default=15)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Nuevos campos para robustez y diagnóstico
    connection_type: Mapped[str] = mapped_column(String(20), default="unknown")
    # Valores: "onvif", "rtsp_fallback", "manual"
    
    last_error_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Valores: "AUTH_FAILED", "CONN_REFUSED", "FROZEN", etc.
    
    last_connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    fallback_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Si ONVIF falló pero RTSP funcionó, guardar aquí la URL alternativa
    
    # NUEVO: Owner de la cámara
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    owner: Mapped[Optional["User"]] = relationship(back_populates="cameras_owned")
    
    # Relaciones existentes
    events: Mapped[List["Event"]] = relationship(back_populates="camera")
    recordings: Mapped[List["Recording"]] = relationship(back_populates="camera")
    user_permissions: Mapped[List["UserCameraPermission"]] = relationship(back_populates="camera")
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "ip_address": self.ip_address,
            "rtsp_url": self.rtsp_url,
            "onvif_url": self.onvif_url,
            "username": self.username,
            "has_password": bool(self.password),
            "profile_token": self.profile_token,
            "is_active": self.is_active,
            "has_ai": self.has_ai,
            "has_ptz": self.has_ptz,
            "has_leds": self.has_leds,
            "has_audio": self.has_audio,
            "is_dual_lens": self.is_dual_lens,
            "resolution_width": self.resolution_width,
            "resolution_height": self.resolution_height,
            "fps": self.fps,
            "owner_id": self.owner_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "connection_type": self.connection_type,
            "last_error_code": self.last_error_code,
            "last_connected_at": self.last_connected_at.isoformat() if self.last_connected_at else None,
            "fallback_url": self.fallback_url
        }


class UserCameraPermission(Base):
    """
    QUÉ REPRESENTA
        La TABLA PUENTE de la multi-tenencia: materializa la relación N:M entre
        `User` y `Camera` para cámaras COMPARTIDAS (no propias). Cada fila otorga
        a un usuario acceso a una cámara con flags GRANULARES de qué puede hacer
        (ver, controlar PTZ/LEDs/audio, descargar grabaciones).

    RELACIONES
        N:1  → User    (user;   FK user_id,   ondelete=CASCADE)
        N:1  → Camera  (camera; FK camera_id, ondelete=CASCADE)
        Restricción UNIQUE(user_id, camera_id): un par usuario-cámara aparece
        una sola vez (los flags se agregan en esa misma fila).

    CUÁNDO SE ESCRIBE
        Al compartir una cámara con otro usuario o editar sus permisos (API de
        administración). El CASCADE borra la fila si se elimina el usuario o la
        cámara.
    CUÁNDO SE LEE
        En CADA autorización de petición sobre una cámara ajena — `PermissionService`
        consulta esta tabla. Atraviesa pipelines #3 Visualización, #8 PTZ,
        #12 Generación de clips y #14 Reproducción histórica.
    """
    __tablename__ = "user_camera_permissions"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)

    can_view: Mapped[bool] = mapped_column(Boolean, default=True)
    can_control_ptz: Mapped[bool] = mapped_column(Boolean, default=False)
    can_control_leds: Mapped[bool] = mapped_column(Boolean, default=False)
    can_control_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    can_download_recordings: Mapped[bool] = mapped_column(Boolean, default=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Relaciones
    user: Mapped["User"] = relationship(back_populates="permissions")
    camera: Mapped["Camera"] = relationship(back_populates="user_permissions")
    
    __table_args__ = (
        UniqueConstraint('user_id', 'camera_id', name='uq_user_camera'),
        Index('idx_user_camera_perms', 'user_id', 'camera_id'),
    )


class EventType(enum.Enum):
    """
    Tipos de evento de seguridad. Se persiste como String en `Event.event_type`.
    Producidos por: detección de movimiento y YOLO (motion/person/vehicle,
    pipeline #9 IA) y watchdog de FFmpeg / monitor de cámaras (camera_offline,
    pipeline #4).
    """
    MOTION = "motion"
    PERSON = "person"
    VEHICLE = "vehicle"
    CAMERA_OFFLINE = "camera_offline"


class Event(Base):
    """
    QUÉ REPRESENTA
        Un evento de seguridad detectado en una cámara (movimiento, persona,
        vehículo, cámara caída), con su confianza, las
        rutas a la evidencia (snapshot/clip) y si ya fue reconocido por un operador.

    RELACIONES
        N:1  → Camera           (camera; FK camera_id, ondelete=CASCADE)
        1:N  → NotificationLog  (notification_logs; envíos derivados del evento)

    ÍNDICES
        (camera_id, created_at), (event_type), (acknowledged, created_at) — para
        listados por cámara, por tipo y de pendientes, todos por fecha.

    CUÁNDO SE ESCRIBE
        Pipeline #10 Eventos: `EventService` persiste el `EventData` que publican
        los workers (movimiento/IA del pipeline #9, watchdog del pipeline #4).
        `clip_path` se rellena después por el pipeline #12 (generación de clips).
        `acknowledged` se actualiza cuando el operador revisa la alerta.
    CUÁNDO SE LEE
        Listados/timeline de eventos en el cliente; el pipeline #13
        Notificaciones lo lee para enrutar y registrar el envío.
    """
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    snapshot_path: Mapped[Optional[str]] = mapped_column(String(500))
    clip_path: Mapped[Optional[str]] = mapped_column(String(500))
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    camera: Mapped["Camera"] = relationship(back_populates="events")
    notification_logs: Mapped[List["NotificationLog"]] = relationship(back_populates="event")
    
    __table_args__ = (
        Index('idx_event_camera_created', 'camera_id', 'created_at'),
        Index('idx_event_type', 'event_type'),
        Index('idx_event_acknowledged', 'acknowledged', 'created_at'),
    )
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "camera_id": self.camera_id,
            "event_type": self.event_type,
            "confidence": self.confidence,
            "snapshot_path": self.snapshot_path,
            "clip_path": self.clip_path,
            "acknowledged": self.acknowledged,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class Recording(Base):
    """
    QUÉ REPRESENTA
        Un segmento de video grabado en disco: cámara, ventana temporal
        (start_time/end_time), ruta del archivo, tamaño y duración. Es el índice
        en BD que mapea el archivo físico para reproducción y rotación.

    RELACIONES
        N:1  → Camera  (camera; FK camera_id, ondelete=CASCADE)
        Índice (camera_id, start_time) — consultas por cámara y rango temporal.

    CUÁNDO SE ESCRIBE
        Pipeline #11 Grabación: el `recording_manager` crea la fila al iniciar un
        segmento y la cierra (end_time/file_size_bytes/duration_seconds) al
        terminarlo. El `StorageManager` la BORRA al rotar por cuota/edad.
    CUÁNDO SE LEE
        Pipeline #14 Reproducción histórica (lista, detalle, timeline y streaming
        del archivo) y el `StorageManager` al calcular uso de disco.

    NOTA DE SERIALIZACIÓN
        `to_dict()` añade alias para la app móvil (`started_at`, `filename`) y
        castea `duration_seconds` a int — ver comentarios en el propio método.
    """
    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    
    camera: Mapped["Camera"] = relationship(back_populates="recordings")
    
    __table_args__ = (
        Index('idx_recording_camera_time', 'camera_id', 'start_time'),
    )
    
    def to_dict(self) -> dict:
        import os as _os
        return {
            "id": self.id,
            "camera_id": self.camera_id,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            # Alias que espera la app móvil (started_at) + nombre de archivo.
            # Antes faltaban → la lista/detalle de grabaciones llegaba con
            # started_at/filename nulos y la móvil fallaba al cargarlas.
            "started_at": self.start_time.isoformat() if self.start_time else None,
            "filename": _os.path.basename(self.file_path) if self.file_path else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "file_path": self.file_path,
            "file_size_bytes": self.file_size_bytes,
            # duration_seconds es Float en BD; la app móvil lo espera como Int
            # (Gson revienta con un decimal en un campo Int). Lo devolvemos como
            # entero para que lista/detalle/timeline parseen bien en el móvil.
            "duration_seconds": int(self.duration_seconds or 0),
        }


class MobileDevice(Base):
    """
    QUÉ REPRESENTA
        Un dispositivo móvil emparejado a un usuario. Guarda el hash del refresh
        token (sesión persistente del móvil) y metadatos (UUID, nombre, plataforma).

        Las notificaciones llegan al móvil por WebSocket en la LAN mientras la
        app está conectada (sin FCM/Firebase, que fue eliminado del proyecto).

    RELACIONES
        N:1  → User  (user; FK user_id). device_uuid es UNIQUE (un alta por equipo).

    CUÁNDO SE ESCRIBE
        Al vincular el móvil (pipeline #2 Autenticación, vía LinkToken/QR) y al
        rotar el refresh token; `last_seen_at` se refresca con la actividad.
    CUÁNDO SE LEE
        En cada refresh de sesión del móvil (valida refresh_token_hash) y al
        listar/gestionar dispositivos del usuario.
    """
    __tablename__ = "mobile_devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    device_uuid: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    device_name: Mapped[str] = mapped_column(String(100))
    platform: Mapped[str] = mapped_column(String(20))  # ios/android

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    refresh_token_hash: Mapped[str] = mapped_column(String(255))
    
    user: Mapped["User"] = relationship(back_populates="devices")
    
    __table_args__ = (
        Index('idx_mobile_device_user', 'user_id'),
        Index('idx_mobile_device_uuid', 'device_uuid'),
    )
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "device_uuid": self.device_uuid,
            "device_name": self.device_name,
            "platform": self.platform,
            "is_active": self.is_active,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class NotificationPreference(Base):
    """
    QUÉ REPRESENTA
        La regla de un usuario para recibir (o no) alertas de un tipo de evento,
        opcionalmente acotada a una cámara y a una franja horaria
        (schedule_start/schedule_end). Cabecera de la preferencia; los canales y
        días viven en tablas hijas normalizadas.

    ALCANCE POR DISPOSITIVO (device_id)
        Las preferencias son POR DISPOSITIVO para el canal in-app (WebSocket):
        cada teléfono tiene su propio juego, aunque compartan usuario.
          - device_id = <id de MobileDevice> → regla del IN-APP de ESE teléfono.
          - device_id = NULL                 → regla "de CUENTA": la usan el
            escritorio y cualquier login sin dispositivo para su in-app, y es la
            ÚNICA que decide TELEGRAM (Telegram va al chat de la cuenta, no a un
            teléfono). El canal 'telegram' solo se honra en filas de cuenta.
        Un teléfono sin fila propia para un evento cae a la fila de cuenta como
        valor por defecto (ver preference_eval.wanted_channels).

    RELACIONES
        N:1  → User                  (user; FK user_id, ondelete=CASCADE)
        N:1  → MobileDevice          (FK device_id, NULLABLE / ondelete=CASCADE;
                                      NULL = preferencia "de cuenta")
        N:1  → Camera                (FK camera_id, NULLABLE / ondelete=SET NULL;
                                      NULL = aplica a TODAS las cámaras del usuario)
        1:N  → NotificationChannel   (channels; cascade all/delete-orphan)
        1:N  → NotificationDay       (days;     cascade all/delete-orphan)
        UNIQUE(user_id, device_id, event_type, camera_id): una regla por combinación.

    CUÁNDO SE ESCRIBE
        Cuando el usuario edita sus preferencias de notificación (API). El backend
        deduce device_id del JWT (el token móvil lo lleva; el de escritorio no).
    CUÁNDO SE LEE
        Pipeline #13 Notificaciones: el `NotificationRouter` evalúa estas reglas
        (habilitado + horario + día + canal) antes de despachar cada alerta.
    """
    __tablename__ = "notification_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mobile_devices.id", ondelete="CASCADE"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    camera_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True, index=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule_start: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    schedule_end: Mapped[Optional[time]] = mapped_column(Time, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relaciones
    user: Mapped["User"] = relationship(back_populates="notification_preferences")
    channels: Mapped[List["NotificationChannel"]] = relationship(back_populates="preference", cascade="all, delete-orphan")
    days: Mapped[List["NotificationDay"]] = relationship(back_populates="preference", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('user_id', 'device_id', 'event_type', 'camera_id', name='uq_user_device_event_camera'),
    )


class NotificationChannel(Base):
    """
    QUÉ REPRESENTA
        Un canal por el que una `NotificationPreference` debe entregar la alerta
        (p.ej. "telegram", "push"). Tabla separada (normalización) para permitir
        VARIOS canales por preferencia sin duplicar la cabecera.

    RELACIONES
        N:1  → NotificationPreference  (preference; FK preference_id)

    CUÁNDO SE ESCRIBE/LEE
        Se escribe al editar la preferencia. Lo lee el pipeline #13 para saber por
        qué medios despachar la notificación del evento.
    """
    __tablename__ = "notification_channels"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    preference_id: Mapped[int] = mapped_column(ForeignKey("notification_preferences.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))  # telegram, push
    
    preference: Mapped["NotificationPreference"] = relationship(back_populates="channels")


class NotificationDay(Base):
    """
    QUÉ REPRESENTA
        Un día de la semana habilitado para una `NotificationPreference`
        (day_of_week: 0=domingo … 6=sábado). Tabla separada (normalización) para
        modelar el conjunto de días sin un campo multivaluado.

    RELACIONES
        N:1  → NotificationPreference  (preference; FK preference_id)

    CUÁNDO SE ESCRIBE/LEE
        Se escribe al editar la preferencia. Lo lee el pipeline #13 para decidir
        si el día actual entra en la ventana de notificación.
    """
    __tablename__ = "notification_days"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    preference_id: Mapped[int] = mapped_column(ForeignKey("notification_preferences.id"), index=True)
    day_of_week: Mapped[int] = mapped_column(Integer)  # 0-6
    
    preference: Mapped["NotificationPreference"] = relationship(back_populates="days")


class TelegramVerificationCode(Base):
    """
    QUÉ REPRESENTA
        Un código de un solo uso y con expiración para vincular la cuenta de un
        usuario con su chat de Telegram (el usuario lo envía al bot para probar
        identidad).

    RELACIONES
        N:1  → User  (FK user_id, sin relación ORM inversa). `code` es UNIQUE.

    CUÁNDO SE ESCRIBE
        Al solicitar la vinculación con Telegram (genera el código).
    CUÁNDO SE LEE
        Pipeline #13 (bootstrap/poller de Telegram): el bot valida el código,
        comprueba expires_at/used y crea el `UserTelegramChat` correspondiente.
    """
    __tablename__ = "telegram_verification_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Dispositivo que solicitó la vinculación (Telegram es POR DISPOSITIVO).
    # NULL = vinculación "de cuenta" (escritorio o login sin dispositivo). Se
    # propaga al UserTelegramChat al verificar el código.
    device_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mobile_devices.id", ondelete="CASCADE"), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_telegram_code', 'code'),
        Index('idx_telegram_user', 'user_id'),
    )


class UserTelegramChat(Base):
    """
    QUÉ REPRESENTA
        Un chat de Telegram ya vinculado a un usuario (destino real de las
        alertas por Telegram). Guarda el telegram_chat_id y, opcionalmente, el
        @username.

    ALCANCE POR DISPOSITIVO (device_id)
        Telegram es POR DISPOSITIVO: cada teléfono vincula su(s) propio(s) chat(s).
          - device_id = <MobileDevice.id> → chat(s) de ESE teléfono.
          - device_id = NULL              → chat(s) "de cuenta" (los que vincula el
            escritorio o un login sin dispositivo).
        Un usuario puede tener varios dispositivos, cada uno con su Telegram.

    RELACIONES
        N:1  → User          (user; FK user_id). SIN unique en user_id: varios chats.
        N:1  → MobileDevice  (FK device_id, NULLABLE / ondelete=CASCADE).

    CUÁNDO SE ESCRIBE
        Tras validar un `TelegramVerificationCode` (pipeline #13); hereda su device_id.
    CUÁNDO SE LEE
        Pipeline #13 al enviar una alerta por Telegram: resuelve a qué chat_id(s)
        activos del (usuario, dispositivo) despachar el mensaje.
    """
    __tablename__ = "user_telegram_chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    device_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mobile_devices.id", ondelete="CASCADE"), nullable=True, index=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(50), index=True)
    telegram_username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relación con usuario
    user: Mapped["User"] = relationship(back_populates="telegram_chats")

    __table_args__ = (
        Index('idx_telegram_chat_user', 'user_id'),
        Index('idx_telegram_chat_id', 'telegram_chat_id'),
    )


class NotificationLog(Base):
    """
    QUÉ REPRESENTA
        El registro de CADA intento de notificación derivado de un evento:
        canal, estado (sent/failed/pending), mensaje de error y, clave para el
        anti-spam, una `cooldown_key` que evita reenviar la misma alerta dentro
        de su ventana de enfriamiento.

    RELACIONES
        N:1  → Event  (event; FK event_id)
        N:1  → User   (FK user_id, destinatario; sin relación ORM inversa)
        Índices en event_id, user_id y cooldown_key.

    CUÁNDO SE ESCRIBE
        Pipeline #13 Notificaciones: al despachar (o fallar) cada alerta.
    CUÁNDO SE LEE
        En el mismo pipeline #13, ANTES de enviar, para comprobar el cooldown
        (¿ya se envió algo con esta cooldown_key recientemente?) y para auditoría.
    """
    __tablename__ = "notification_logs"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    
    channel: Mapped[str] = mapped_column(String(20))  # telegram, push
    status: Mapped[str] = mapped_column(String(20))   # sent, failed, pending
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Anti-spam: cooldown tracking
    cooldown_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    
    event: Mapped["Event"] = relationship(back_populates="notification_logs")
    
    __table_args__ = (
        Index('idx_notification_event', 'event_id'),
        Index('idx_notification_user', 'user_id'),
        Index('idx_notification_cooldown', 'cooldown_key'),
    )


class LinkToken(Base):
    """
    QUÉ REPRESENTA
        Un token de un solo uso y con expiración (UUID) para emparejar un
        dispositivo móvil con una cuenta mediante un código QR mostrado en el
        escritorio/web.

    RELACIONES
        N:1  → User  (FK user_id, sin relación ORM inversa). `token` es UNIQUE.

    CUÁNDO SE ESCRIBE
        Al generar el QR de vinculación (pipeline #2 Autenticación).
    CUÁNDO SE LEE
        Cuando el móvil canjea el token: se valida expires_at/used y se crea el
        `MobileDevice` con su refresh token.
    """
    __tablename__ = "link_tokens"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_link_token', 'token'),
        Index('idx_link_token_user', 'user_id'),
    )


class SystemConfig(Base):
    """
    QUÉ REPRESENTA
        Configuración runtime del sistema en formato clave-valor persistente
        (parámetros ajustables que sobreviven al reinicio, distintos de los del
        `.env`). `key` es UNIQUE; `updated_at` se actualiza con `onupdate`.

    RELACIONES
        Ninguna (tabla independiente, sin FK).

    CUÁNDO SE ESCRIBE
        Al cambiar ajustes desde la administración.
    CUÁNDO SE LEE
        Por los servicios que consultan un parámetro configurable en caliente.
    """
    __tablename__ = "system_config"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, 
        default=datetime.utcnow, 
        onupdate=datetime.utcnow
    )
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
    
class AuditLog(Base):
    """
    QUÉ REPRESENTA
        Una bitácora inmutable de acciones sensibles (login, borrado de
        grabación, cambio de permisos, etc.) con quién, qué recurso, detalles,
        IP y user-agent. Soporte forense y de cumplimiento.

    RELACIONES
        N:1  → User  (FK user_id, NULLABLE — acciones anónimas/sistema; sin
                     relación ORM inversa). Índices (user_id, created_at) y (action).

    CUÁNDO SE ESCRIBE
        En cada acción sensible auditada (transversal a varios pipelines,
        especialmente #2 Autenticación y la administración).
    CUÁNDO SE LEE
        Solo en consultas de auditoría/forense; no participa en el flujo operativo.
    """
    __tablename__ = "audit_logs"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)  # ej: "login", "delete_recording", "change_permission"
    resource_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # ej: "camera", "user", "recording"
    resource_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    
    __table_args__ = (
        Index('idx_audit_user_time', 'user_id', 'created_at'),
        Index('idx_audit_action', 'action'),
    )
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "details": self.details,
            "ip_address": self.ip_address,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class RevokedToken(Base):
    """
    QUÉ REPRESENTA
        Un JWT revocado (por `jti`) que persiste entre reinicios del backend, con
        su expiración y motivo. Respaldo en BD del blocklist en memoria.

    POR QUÉ EXISTE
        El JWTBlocklist en memoria sigue siendo la fuente primaria (rápida).
        Esta tabla es respaldo: al arrancar, el blocklist se rehidrata con los
        tokens revocados que aún no expiraron, evitando que un logout en t=0
        se "olvide" si el backend se reinicia en t=5min.

        Se purgan automáticamente las entradas con expires_at < now (GC en el
        arranque y cada hora). Tabla pequeña: tokens revocados en ventana de
        1 día (acceso) a 30 días (refresh móvil) máximo.

    RELACIONES
        N:1  → User  (FK user_id, NULLABLE; sin relación ORM inversa).
        `jti` es UNIQUE e indexado; `expires_at` indexado para el GC.

    CUÁNDO SE ESCRIBE
        Pipeline #2 Autenticación: al hacer logout o cambiar contraseña.
    CUÁNDO SE LEE
        En el arranque (rehidratación del blocklist) y por el GC horario; la
        verificación por petición la resuelve el blocklist en memoria.
    """
    __tablename__ = "revoked_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # logout, password_change, etc.