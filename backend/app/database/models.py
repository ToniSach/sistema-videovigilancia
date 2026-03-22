"""
Modelos de base de datos SQLAlchemy 2.0 (DeclarativeBase)
Define todas las entidades del sistema de videovigilancia.
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
    """Enumeración de roles de usuario del sistema."""
    ADMIN = "admin"
    USER = "user"


class User(Base):
    """
    Modelo de usuario para autenticación y autorización.
    """
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Relaciones (ya estaban correctas)
    cameras_owned: Mapped[List["Camera"]] = relationship(back_populates="owner")
    permissions: Mapped[List["UserCameraPermission"]] = relationship(back_populates="user")
    devices: Mapped[List["MobileDevice"]] = relationship(back_populates="user")
    notification_preferences: Mapped[List["NotificationPreference"]] = relationship(back_populates="user")
    telegram_chats: Mapped[List["UserTelegramChat"]] = relationship(back_populates="user")  # Referencia a "user"
    
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
    Modelo de cámara de videovigilancia.
    """
    __tablename__ = "cameras"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    rtsp_url: Mapped[str] = mapped_column(String(500), nullable=False)
    onvif_url: Mapped[str] = mapped_column(String(500))
    username: Mapped[str] = mapped_column(String(100))
    password: Mapped[str] = mapped_column(String(100))
    profile_token: Mapped[str] = mapped_column(String(100))
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
            "password": self.password,
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
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class UserCameraPermission(Base):
    """
    Permisos granulares de usuario sobre cámara específica.
    """
    __tablename__ = "user_camera_permissions"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), index=True)
    
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
    )


class EventType(enum.Enum):
    """Tipos de eventos de seguridad soportados."""
    MOTION = "motion"
    PERSON = "person"
    VEHICLE = "vehicle"
    CAMERA_OFFLINE = "camera_offline"
    CAMERA_RECONNECTED = "camera_reconnected"
    TAMPERING = "tampering"


class Event(Base):
    """
    Modelo de evento de seguridad.
    """
    __tablename__ = "events"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    snapshot_path: Mapped[Optional[str]] = mapped_column(String(500))
    clip_path: Mapped[Optional[str]] = mapped_column(String(500))
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    camera: Mapped["Camera"] = relationship(back_populates="events")
    notification_logs: Mapped[List["NotificationLog"]] = relationship(back_populates="event")
    
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
    Modelo de grabación de video.
    """
    __tablename__ = "recordings"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    
    camera: Mapped["Camera"] = relationship(back_populates="recordings")
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "camera_id": self.camera_id,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "file_path": self.file_path,
            "file_size_bytes": self.file_size_bytes,
            "duration_seconds": self.duration_seconds
        }


class MobileDevice(Base):
    """
    Dispositivos móviles registrados para notificaciones push.
    """
    __tablename__ = "mobile_devices"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    device_uuid: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    device_name: Mapped[str] = mapped_column(String(100))
    platform: Mapped[str] = mapped_column(String(20))  # ios/android
    
    fcm_token: Mapped[str] = mapped_column(Text)
    fcm_token_updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    refresh_token_hash: Mapped[str] = mapped_column(String(255))
    
    user: Mapped["User"] = relationship(back_populates="devices")
    
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
    Preferencias de notificación por usuario y tipo de evento.
    """
    __tablename__ = "notification_preferences"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    camera_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cameras.id"), nullable=True, index=True)
    
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule_start: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    schedule_end: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Relaciones
    user: Mapped["User"] = relationship(back_populates="notification_preferences")
    channels: Mapped[List["NotificationChannel"]] = relationship(back_populates="preference", cascade="all, delete-orphan")
    days: Mapped[List["NotificationDay"]] = relationship(back_populates="preference", cascade="all, delete-orphan")
    
    __table_args__ = (
        UniqueConstraint('user_id', 'event_type', 'camera_id', name='uq_user_event_camera'),
    )


class NotificationChannel(Base):
    """
    Canales de notificación para una preferencia (telegram, push).
    Tabla separada para permitir múltiples canales por preferencia.
    """
    __tablename__ = "notification_channels"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    preference_id: Mapped[int] = mapped_column(ForeignKey("notification_preferences.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))  # telegram, push
    
    preference: Mapped["NotificationPreference"] = relationship(back_populates="channels")


class NotificationDay(Base):
    """
    Días de la semana para notificaciones (0=domingo, 6=sábado).
    Tabla separada para normalización.
    """
    __tablename__ = "notification_days"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    preference_id: Mapped[int] = mapped_column(ForeignKey("notification_preferences.id"), index=True)
    day_of_week: Mapped[int] = mapped_column(Integer)  # 0-6
    
    preference: Mapped["NotificationPreference"] = relationship(back_populates="days")


class TelegramVerificationCode(Base):
    """
    Códigos temporales para vinculación con Telegram.
    """
    __tablename__ = "telegram_verification_codes"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserTelegramChat(Base):
    """
    Chats de Telegram vinculados a usuarios.
    NOTA: Sin unique en user_id para permitir múltiples chats por usuario.
    """
    __tablename__ = "user_telegram_chats"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(50), index=True)
    telegram_username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # ✅ CORRECCIÓN: Agregar esta relación para completar back_populates
    user: Mapped["User"] = relationship(back_populates="telegram_chats")


class NotificationLog(Base):
    """
    Log de notificaciones enviadas para auditoría y control de cooldown.
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


class LinkToken(Base):
    """
    Tokens temporales para vinculación de dispositivos vía QR.
    """
    __tablename__ = "link_tokens"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SystemConfig(Base):
    """
    Configuración del sistema (clave-valor).
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