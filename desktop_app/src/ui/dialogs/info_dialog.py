"""
InfoDialog — diálogo reutilizable para mostrar ayuda contextual de cada
sección. Recibe una lista de secciones `(título, texto)` y las pinta en un
formato consistente con el resto de la app.

Uso:
    InfoDialog(
        title="Ayuda — Cámaras",
        sections=[
            ("¿Qué es esta vista?", "Aquí ves y administras…"),
            ("Añadir una cámara",   "Pulsa el botón…"),
        ],
        parent=self,
    ).exec()
"""
from __future__ import annotations

from typing import List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QFrame,
)


class InfoDialog(QDialog):
    """Diálogo modal con secciones de ayuda."""

    def __init__(self, title: str, sections: List[Tuple[str, str]], parent=None):
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
