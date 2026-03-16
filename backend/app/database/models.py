"""
Modelos de base de datos SQLAlchemy 2.0 (DeclarativeBase)
Define todas las entidades del sistema de videovigilancia.
"""
import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Clase base declarativa para todos los modelos."""
    pass


class UserRole(enum.Enum):
    """Enumeración de roles de usuario del sistema."""
    ADMIN = "admin"
    VIEWER = "viewer"


class User(Base):
    """
    Modelo de usuario para autenticación y autorización.
    """
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    def to_dict(self) -> dict:
        """
        Serializa el usuario a diccionario (excluye password_hash por seguridad).
        
        Returns:
            dict: Representación segura del usuario
        """
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
    Almacena configuración de conexión y capacidades hardware.
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
    
    # Relaciones
    events: Mapped[List["Event"]] = relationship(back_populates="camera")
    recordings: Mapped[List["Recording"]] = relationship(back_populates="camera")
    
    def to_dict(self) -> dict:
        """
        Serializa la cámara a diccionario completo.
        
        Returns:
            dict: Configuración completa de la cámara
        """
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
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


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
    Modelo de evento de seguridad (detección de movimiento, IA, etc.).
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
    
    # Relaciones
    camera: Mapped["Camera"] = relationship(back_populates="events")
    
    def to_dict(self) -> dict:
        """
        Serializa el evento a diccionario.
        
        Returns:
            dict: Datos completos del evento
        """
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
    Registra metadatos de archivos de video almacenados.
    """
    __tablename__ = "recordings"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    
    # Relaciones
    camera: Mapped["Camera"] = relationship(back_populates="recordings")
    
    def to_dict(self) -> dict:
        """
        Serializa la grabación a diccionario.
        
        Returns:
            dict: Metadatos de la grabación
        """
        return {
            "id": self.id,
            "camera_id": self.camera_id,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "file_path": self.file_path,
            "file_size_bytes": self.file_size_bytes,
            "duration_seconds": self.duration_seconds
        }


class SystemConfig(Base):
    """
    Modelo de configuración del sistema (clave-valor).
    Almacena settings variables en tiempo de ejecución.
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
        """
        Serializa la configuración a diccionario.
        
        Returns:
            dict: Par clave-valor
        """
        return {
            "id": self.id,
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
