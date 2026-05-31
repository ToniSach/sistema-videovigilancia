"""
Botón de ayuda contextual reutilizable.

Uso:
    from desktop_app.src.ui.components.help_button import HelpButton
    header.addWidget(HelpButton("notifications_view", parent=self))
"""
from PySide6.QtWidgets import QPushButton


class HelpButton(QPushButton):
    """Botón que abre el InfoDialog con la ayuda de una vista."""

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
        from desktop_app.src.ui.dialogs.info_dialog import InfoDialog
        from desktop_app.src.ui.help_texts import get_help
        title, sections = get_help(self._view_key)
        InfoDialog(title=title, sections=sections, parent=self).exec()
