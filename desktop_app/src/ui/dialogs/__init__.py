"""
================================================================================
PAQUETE: desktop_app.src.ui.dialogs — Diálogos modales del cliente desktop
================================================================================

PROPÓSITO
    Agrupar los QDialog que las vistas/main_window abren bajo demanda para
    tareas puntuales (no son pantallas del stack de navegación, sino ventanas
    modales que se abren, completan una acción y se cierran).

CONTENIDO
    onboarding_wizard.py .. tutorial paginado de primer uso (QSettings recuerda
                            si ya se vio); lo abre MainWindow.
    qr_link_dialog.py ..... muestra un QR para vincular un móvil (Pipeline #13).
    telegram_link_dialog .. genera/muestra el código de vinculación de Telegram
                            y hace polling hasta confirmar (Pipeline #13).
    info_dialog.py ........ diálogo de ayuda contextual reutilizable; lo abren
                            help_button.py y varias vistas con secciones de
                            help_texts.py.

DEPENDENCIAS COMUNES
    PySide6 (QDialog) y, salvo info_dialog/onboarding, el singleton
    services/api_client.py para hablar con el backend REST/JWT.
================================================================================
"""
