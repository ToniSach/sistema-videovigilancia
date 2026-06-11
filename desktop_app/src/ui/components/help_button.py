"""
================================================================================
MÓDULO: ui.components.help_button — Botón "?" de ayuda contextual
================================================================================

PROPÓSITO
    Botón "?" reutilizable que, al pulsarse, abre un InfoDialog con el texto de
    ayuda asociado a una vista concreta. Permite poner ayuda contextual en la
    cabecera de cualquier pantalla con una sola línea.

RESPONSABILIDAD
    Guardar la clave de la vista (view_key), aplicar su estilo "?" y, en el
    click, recuperar el texto de ayuda y mostrarlo en un diálogo modal.

DEPENDENCIAS
    PySide6 (QPushButton), help_texts.get_help (texto por clave de vista, import
    diferido), dialogs.info_dialog.InfoDialog (presentación, import diferido).

COMPONENTES RELACIONADOS
    ui/help_texts.py (catálogo de textos de ayuda), ui/dialogs/info_dialog.py
    (diálogo que los muestra).

DÓNDE SE USA
    En la cabecera de las vistas (p.ej. notifications_view), añadiéndolo al layout.

USO
    from desktop_app.src.ui.components.help_button import HelpButton
    header.addWidget(HelpButton("notifications_view", parent=self))
================================================================================
"""
from PySide6.QtWidgets import QPushButton


class HelpButton(QPushButton):
    """Botón "?" que abre el InfoDialog con la ayuda de una vista.

    Rol: punto de entrada a la ayuda contextual de una pantalla.
    Quién la instancia/consume: las vistas, que lo añaden a su cabecera.
    Señales Qt: usa su propio clicked (interno) → _open_help; no expone señales.
    Dependencias: help_texts.get_help, InfoDialog.

    Parámetros:
        view_key: clave de la vista en help_texts (selecciona qué ayuda mostrar).
    """

    def __init__(self, view_key: str, parent=None):
        super().__init__("", parent)
        self._view_key = view_key
        self.setMaximumWidth(36)
        self.setToolTip("Ayuda")
        self.setStyleSheet("""
            QPushButton {
                background-color: transparent; color: #94a3b8;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 6px; padding: 4px 8px; font-size: 14px;
            }
            QPushButton:hover { color: #38bdf8; border-color: #38bdf8; }
        """)
        self.clicked.connect(self._open_help)

    def _open_help(self):
        """Recupera la ayuda de la vista y la muestra en un InfoDialog modal.

        Inputs: ninguno (usa self._view_key).
        Outputs: ninguno (abre el diálogo).
        Llamado por: el clicked del propio botón.
        Llama a: help_texts.get_help y InfoDialog.exec.
        """
        from desktop_app.src.ui.dialogs.info_dialog import InfoDialog
        from desktop_app.src.ui.help_texts import get_help
        title, sections = get_help(self._view_key)
        InfoDialog(title=title, sections=sections, parent=self).exec()
