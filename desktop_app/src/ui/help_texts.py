"""
Textos de ayuda contextual de cada vista de la app.

Cada entrada es una tupla (título, lista_de_secciones), donde cada sección
es (encabezado, descripción). Se usan en InfoDialog desde el botón "" de
cada vista principal.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# Tipo alias
HelpContent = Tuple[str, List[Tuple[str, str]]]


HELP: Dict[str, HelpContent] = {
    "live_view": (
        "Ayuda — Cámaras en vivo",
        [
            ("¿Qué ves aquí?",
             "El streaming en directo de tus cámaras. Cada recuadro es una "
             "cámara o un lente (si la cámara es dual-lens, ves los dos "
             "lentes por separado, L1 y L2)."),
            ("Cambiar disposición",
             "Usa el menú «Vista» para elegir entre 1×1 (una cámara grande), "
             "2×2 (cuatro), o 3×3 (nueve). Cambiar de disposición no "
             "reconecta los streams; es instantáneo."),
            ("◀ ▶  Paginación",
             "Si tienes más cámaras que cuadros en la vista (p.ej. 6 cámaras "
             "en 2×2 = 2 páginas), usa los botones ◀ Anterior / Siguiente ▶ "
             "o las teclas PageUp/PageDown para navegar."),
            ("Acciones",
             "Clic izquierdo selecciona. Doble clic maximiza. Clic derecho "
             "abre un menú con PTZ, captura y maximizar/restaurar. El "
             "engranaje abre la vista de control completo."),
            ("Si una cámara aparece roja",
             "Significa que perdió conexión. El backend reintenta sola; si "
             "el problema persiste, revisa la cámara en «Cámaras» o el log "
             "del servidor."),
        ],
    ),

    "events_view": (
        "Ayuda — Eventos",
        [
            ("¿Qué es un evento?",
             "Cada vez que la IA detecta algo (persona, vehículo, "
             "movimiento) se crea un evento con timestamp, cámara, "
             "confianza y un snapshot del momento."),
            ("Filtros",
             "Filtra por cámara, tipo de evento y rango de horas. Por "
             "defecto verás los últimos eventos de las últimas 24 horas."),
            ("Reproducir el momento",
             "Doble-clic sobre un evento te lleva a la reproducción del "
             "momento exacto (10 segundos antes y después)."),
            ("Recibir notificaciones",
             "Ve a «Notificaciones» para configurar qué eventos quieres "
             "recibir por Telegram o push móvil y en qué horario."),
        ],
    ),

    "camera_management_view": (
        "Ayuda — Cámaras",
        [
            ("Añadir una cámara",
             "Si tu cámara es ONVIF compatible, pulsa «Descubrir» y se "
             "buscarán automáticamente en tu red. Si no, añádela "
             "manualmente con su URL RTSP."),
            ("Descubrimiento automático",
             "El descubrimiento usa WS-Discovery (multicast) y prueba "
             "credenciales comunes. Si tu router bloquea multicast, activa "
             "«subnet scan» en las opciones."),
            ("Dual-lens",
             "Si la cámara tiene dos lentes (visión panorámica), márcala "
             "como dual-lens. El sistema dividirá el stream en L1 y L2 "
             "y los mostrará como cámaras independientes en la vista en "
             "vivo."),
            ("Si pierdes conexión",
             "El backend intenta reconectar automáticamente con backoff "
             "(1s, 2s, 4s…). Si tras 10 intentos sigue fallando, revisa "
             "la cámara y reinicia desde aquí con «Probar conexión»."),
        ],
    ),

    "settings_view": (
        "Ayuda — Configuración",
        [
            ("Almacenamiento",
             "El límite máximo de grabaciones en GB. Cuando se alcanza, "
             "se borran automáticamente las más antiguas. Por defecto 1 GB "
             "para que no llenes el disco en pruebas."),
            ("Telegram",
             "Para enviar notificaciones por Telegram necesitas crear un "
             "bot con @BotFather, copiar el token aquí y luego cada usuario "
             "se vincula desde «Notificaciones → Vincular Telegram»."),
            ("IA",
             "Confianza mínima (0-1) para considerar una detección válida. "
             "Sensibilidad de movimiento controla qué tan rápido se activa "
             "la IA. Sube los valores si tienes muchos falsos positivos."),
            ("FFmpeg",
             "Resolución y FPS del stream. Bajarlos reduce CPU y ancho de "
             "banda pero también calidad. Para dual-lens hay parámetros "
             "específicos del stream completo (antes del split)."),
            ("Probar Telegram",
             "El botón «Enviar evento de prueba» dispara un evento "
             "simulado en una cámara para verificar que las notificaciones "
             "se envían correctamente."),
        ],
    ),

    "users_view": (
        "Ayuda — Usuarios",
        [
            ("Roles",
             "Admin: puede todo (crear cámaras, usuarios, ver y borrar). "
             "Usuario: sólo ve y opera lo que el admin le permita "
             "explícitamente."),
            ("Añadir usuario",
             "Crea cuentas para otras personas que vayan a usar el sistema. "
             "Cada uno tendrá su propio login, sus propias notificaciones "
             "y su propio móvil vinculado."),
            ("Permisos por cámara",
             "Ve a «Permisos» para asignar qué cámaras puede ver/controlar "
             "cada usuario. Granular: view, control PTZ, control LEDs, "
             "control audio, descargar grabaciones."),
        ],
    ),

    "permissions_view": (
        "Ayuda — Permisos",
        [
            ("¿Cómo funcionan?",
             "Cada combinación usuario×cámara tiene 5 permisos: ver, PTZ, "
             "LEDs, audio bidireccional, descargar grabaciones. Marca las "
             "casillas que necesite cada usuario."),
            ("Admin",
             "El admin tiene todos los permisos sobre todas las cámaras "
             "automáticamente. No necesita configurarse."),
            ("Sin permisos = sin acceso",
             "Si un usuario no tiene ningún permiso sobre una cámara, no "
             "aparece en su vista en vivo, no recibe sus notificaciones y "
             "no puede descargar sus grabaciones."),
        ],
    ),

    "playback_view": (
        "Ayuda — Reproducción",
        [
            ("¿Qué hay aquí?",
             "Todas las grabaciones que tu sistema ha hecho: las CONTINUAS "
             "(segmentos de 2 minutos sin parar mientras la cámara esté "
             "activa) y los CLIPS DE EVENTOS (10s antes + 10s después de "
             "una detección)."),
            ("Cómo usarlo",
             "1) Elige la cámara en el desplegable.\n"
             "2) Elige la fecha (por defecto hoy).\n"
             "3) Pulsa «Cargar Timeline» — verás todos los segmentos del "
             "día en una línea de tiempo.\n"
             "4) Haz click en cualquier segmento para reproducirlo."),
            ("Almacenamiento",
             "El sistema borra automáticamente las grabaciones más antiguas "
             "cuando se alcanza el límite de almacenamiento (configurable "
             "en Sistema → Configuración → Almacenamiento, por defecto 1 GB). "
             "Si quieres conservar una grabación importante, descárgala."),
            ("Descarga / exportación",
             "Mientras reproduces una grabación, el botón «Exportar» te "
             "permite guardar una copia local del MP4."),
            ("Si no aparece nada",
             "Verifica que la cámara haya estado activa en la fecha "
             "seleccionada y que tengas permiso «view» sobre ella. Las "
             "grabaciones continuas se crean automáticamente cuando "
             "AUTO_START_RECORDING=true (por defecto)."),
        ],
    ),

    "system_view": (
        "Ayuda — Sistema",
        [
            ("Estado",
             "Monitor en tiempo real de CPU, RAM, disco, FPS por cámara, "
             "y estado de cada worker. Si algo está rojo, hay un problema."),
            ("Hardware",
             "Detecta automáticamente la tarjeta gráfica (GPU) y la usa para "
             "la detección de objetos si está disponible. Si no, usa el "
             "procesador (CPU) con menor rendimiento."),
            ("Workers",
             "Cada cámara tiene un worker FFmpeg que captura el RTSP. "
             "«Running» = OK. «Reconnecting» = está reintentando. «Error» "
             "= revisa cámara/red."),
        ],
    ),
}


def get_help(view_key: str) -> HelpContent:
    """Devuelve (título, secciones) para una vista. Si no hay, devuelve genérico."""
    return HELP.get(view_key, (
        "Ayuda",
        [("ℹ", "No hay ayuda específica para esta vista.")]
    ))
