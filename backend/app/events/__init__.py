"""
Paquete `events` — Bus central de eventos del NVR (pipeline #10).

Reexporta la API pública del bus para importarla como
`from backend.app.events import event_manager`. Toda la lógica vive en
event_manager.py; aquí sólo se expone el singleton y sus tipos.
"""
from .event_manager import EventManager, EventData, event_manager

__all__ = ["EventManager", "EventData", "event_manager"]
