"""
Paquete raíz del cliente de escritorio (PySide6) del sistema NVR/VMS.

Proceso independiente que consume el backend Flask por REST/JWT y reproduce el
directo por RTSP/go2rtc con VLC. Estructura:
    main.py ........ arranque Qt (QApplication + MainWindow)
    config.py ...... constantes del cliente (API_BASE_URL, timeouts, tema)
    services/ ...... api_client (HTTP singleton JWT+async) y playback_service
    models/ ........ DTOs locales (User, Camera, Recording) que espejan la API
    ui/ ............ main_window (shell de navegación), views/, components/,
                     dialogs/, theme/icons/help_texts

El arranque y la arquitectura completa están documentados en
desktop_app/src/main.py.
"""
