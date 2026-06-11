"""
================================================================================
PAQUETE: ui.views — Pantallas (vistas QWidget) del cliente desktop (PySide6)
================================================================================

Cada módulo de este paquete define una PANTALLA de la app como un QWidget que
MainWindow instancia UNA sola vez y monta en su `content_stack` (un
QStackedWidget). Las vistas no se conocen entre sí: hablan con el backend vía el
singleton `services/api_client` (REST + JWT, async con callback en el hilo de
UI) y se comunican con la app emitiendo señales Qt que MainWindow enruta (ver
las constantes VIEW_* y `_create_main_view` en ui/main_window.py).

Pantallas (índice VIEW_* en main_window):
    login_view ............ autenticación (Pipeline #2)
    dashboard_view ........ inicio / accesos directos
    live_view ............. directo (go2rtc/VLC)
    camera_control_view ... control dedicado de una cámara (PTZ, etc.)
    events_view ........... eventos/alertas (salta a playback)
    playback_view ......... reproducción histórica (Pipeline #14)
    camera_management_view  alta/edición de cámaras
    notifications_view .... preferencias + Telegram (Pipeline #13)
    system_view ........... salud/métricas del servidor
    users_view ............ CRUD de usuarios (admin, Pipeline #2)
    permissions_view ...... permisos por cámara (admin, autorización #2)
    telegram_devices_view . dispositivos Telegram vinculados (admin, #13)
    settings_view ......... ajustes del sistema (almacenamiento/IA/Telegram)

Este paquete no exporta nada explícitamente; cada vista se importa por su ruta
completa desde main_window. Los estilos QSS de tabla/diálogo compartidos viven
en users_view (`_TABLE_STYLE`, `_DIALOG_STYLE`) y los reutilizan las demás
vistas administrativas.
================================================================================
"""
