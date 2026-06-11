"""
================================================================================
MÓDULO: desktop_app.ui.dialogs.info_dialog — Diálogo de ayuda reutilizable
================================================================================

PROPÓSITO
    QDialog modal genérico que muestra ayuda contextual de una sección como una
    lista de bloques `(título, texto)` con estilo coherente con el resto de la
    app. NO habla con el backend: todo el contenido es estático y se lo pasa
    quien lo abre.

RESPONSABILIDAD
    Solo presentación: pintar la cabecera, las secciones dentro de un scroll y
    un botón «Entendido» que cierra. No tiene lógica de red ni de estado.

DEPENDENCIAS
    PySide6 (QDialog, QScrollArea…). Sin api_client.

COMPONENTES RELACIONADOS
    - components/help_button.py — el botón «?» de cada vista que lo instancia.
    - help_texts.py — diccionario de secciones por vista (la fuente del texto).
    - views/live_view.py, views/notifications_view.py — lo abren directamente.

QUIÉN LO ABRE
    help_button.py (botón «?» de cada vista) y algunas vistas que arman sus
    propias secciones; siempre vía `InfoDialog(...).exec()`.

EJEMPLO DE USO
    InfoDialog(
        title="Ayuda — Cámaras",
        sections=[
            ("¿Qué es esta vista?", "Aquí ves y administras…"),
            ("Añadir una cámara",   "Pulsa el botón…"),
        ],
        parent=self,
    ).exec()
================================================================================
"""
from __future__ import annotations

from typing import List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QFrame,
)


class InfoDialog(QDialog):
    """Diálogo modal de solo lectura con secciones de ayuda.

    ROL
        Ventana modal que renderiza `sections` y se cierra con «Entendido».
        Es reutilizable: el mismo widget sirve para la ayuda de cualquier vista,
        cambiando únicamente `title` y `sections`.

    QUIÉN LO INSTANCIA
        help_button.py y las vistas (live_view, notifications_view…).

    RESULTADO
        No devuelve datos; es informativo. Se usa con `.exec()` (modal) y su
        código de retorno (Accepted) no se consume.

    DEPENDENCIAS
        Ninguna externa: todo el contenido llega por parámetros.
    """

    def __init__(self, title: str, sections: List[Tuple[str, str]], parent=None):
        # title: título de la ventana y cabecera visible.
        # sections: lista de pares (encabezado, cuerpo); el cuerpo admite saltos
        #   de línea y se pinta con word-wrap dentro de un scroll vertical.
        # parent: widget padre para herencia de modalidad/centrado.
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 480)
        self.setModal(True)
        self.setStyleSheet("QDialog { background-color: #0f172a; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(14)

        # Cabecera
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            "color: #f1f5f9; font-size: 18px; font-weight: bold;"
        )
        outer.addWidget(title_lbl)

        # Separador
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: rgba(255,255,255,0.1);")
        outer.addWidget(sep)

        # Secciones en scroll
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { background-color: transparent; border: none; }"
        )
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setSpacing(16)
        cl.setContentsMargins(0, 0, 0, 0)

        for sec_title, sec_text in sections:
            cl.addWidget(self._section(sec_title, sec_text))

        cl.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # Botón cerrar
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Entendido")
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #38bdf8; color: #0f172a;
                border: none; border-radius: 6px;
                padding: 8px 20px; font-weight: bold;
            }
            QPushButton:hover { background-color: #7dd3fc; }
        """)
        btn_close.setMinimumHeight(36)
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        outer.addLayout(btn_row)

    @staticmethod
    def _section(title: str, body: str) -> QWidget:
        # Construye un bloque de ayuda (encabezado azul + cuerpo con word-wrap)
        # como QWidget independiente para apilarlo en el layout del scroll.
        w = QWidget()
        l = QVBoxLayout(w)
        l.setSpacing(4)
        l.setContentsMargins(0, 0, 0, 0)

        t = QLabel(title)
        t.setStyleSheet(
            "color: #38bdf8; font-size: 14px; font-weight: bold;"
        )
        l.addWidget(t)

        b = QLabel(body)
        b.setWordWrap(True)
        b.setStyleSheet(
            "color: #cbd5e1; font-size: 12px; line-height: 1.5;"
        )
        l.addWidget(b)
        return w
