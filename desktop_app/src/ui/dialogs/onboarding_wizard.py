"""
Onboarding Wizard — tutorial inicial paso a paso.

Se muestra automáticamente la primera vez que un usuario hace login en
este equipo (usa QSettings para recordar que ya se vio). También se puede
reabrir desde el botón «Tutorial» del sidebar.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QStackedWidget, QWidget, QCheckBox, QSizePolicy,
    QSpacerItem,
)


@dataclass
class Step:
    icon: str
    title: str
    body: str
    hint: Optional[str] = None  # texto extra en cuadro destacado


STEPS: List[Step] = [
    Step(
        icon="",
        title="Bienvenido al NVR",
        body=(
            "Este sistema te permite ver, grabar y recibir alertas de tus "
            "cámaras IP — todo en tu red local, sin enviar nada a internet "
            "salvo Telegram (opcional).\n\n"
            "Este tutorial te muestra lo esencial en menos de un minuto. "
            "Puedes reabrirlo cuando quieras desde el botón «Tutorial» del "
            "menú lateral."
        ),
    ),
    Step(
        icon="",
        title="1. Tu cuenta de administrador",
        body=(
            "La primera vez que abriste la app creaste el usuario "
            "administrador. Con él puedes crear más usuarios en «Usuarios» "
            "y compartir cámaras concretas con cada uno desde «Permisos».\n\n"
            "Cada usuario ve solo las cámaras que le compartes y configura "
            "sus propias notificaciones."
        ),
        hint="Los usuarios también pueden entrar desde el móvil escaneando "
             "el QR o con el código de vinculación."
    ),
    Step(
        icon="",
        title="2. Añadir tus cámaras",
        body=(
            "Ve a «Cámaras» y pulsa «Descubrir Cámaras»: el sistema buscará "
            "automáticamente cámaras ONVIF en tu red.\n\n"
            "Si no aparece, añádela manualmente con su IP (los enlaces RTSP/"
            "ONVIF se autocompletan). Si es de dos lentes, márcala como "
            "«dual-lens» y verás cada lente por separado."
        ),
        hint="Las cámaras se conectan solas en cuanto las añades."
    ),
    Step(
        icon="",
        title="3. Verlas en vivo",
        body=(
            "Ve a «En vivo» para ver los streams. Cambia la disposición "
            "(1×1 / 2×2 / 3×3) y navega entre páginas con «◀ / ▶» o "
            "PageUp/PageDown.\n\n"
            "Doble clic maximiza una cámara. En el panel de cada cámara "
            "tienes PTZ, captura, y los botones de audio «Escuchar» y "
            "«Hablar»."
        ),
        hint="«Escuchar» reproduce el audio de la cámara por tus "
             "auriculares; ajústalo con el control de volumen."
    ),
    Step(
        icon="",
        title="4. Activa la IA (para las alertas)",
        body=(
            "Las notificaciones se generan a partir de lo que DETECTA la IA "
            "(personas, vehículos…). Por eso, primero activa la IA en una "
            "cámara: abre su panel en «En vivo» y pulsa «Activar IA».\n\n"
            "Mientras no haya una cámara con IA activa, no se pueden crear "
            "preferencias de notificación (te lo avisa la propia pantalla)."
        ),
        hint="Solo una cámara usa IA a la vez. Las alertas son de esa cámara."
    ),
    Step(
        icon="",
        title="5. Notificaciones: escritorio vs. móvil",
        body=(
            "En «Notificaciones → Mis preferencias» eliges qué eventos te "
            "interesan, en qué horario y días.\n\n"
            "• En el ESCRITORIO las alertas se ven aquí mismo, en la app.\n"
            "• Para recibirlas en TELEGRAM o en el MÓVIL, hazlo desde la "
            "app móvil (allí vinculas Telegram y eliges esos canales)."
        ),
        hint="El administrador configura el bot de Telegram una sola vez "
             "en «Notificaciones → Configurar bot»."
    ),
    Step(
        icon="",
        title="6. Vincular tu celular",
        body=(
            "Pulsa «Vincular móvil» en el menú lateral: aparece un QR. "
            "Escanéalo desde la app móvil (en la misma red WiFi) y el "
            "teléfono queda autenticado.\n\n"
            "Desde el móvil ves los streams, recibes alertas en la app "
            "(funciona en LAN sin internet), revisas grabaciones y mueves "
            "las cámaras."
        ),
        hint="Las alertas al móvil llegan por la red local; no usan "
             "Firebase ni necesitan internet."
    ),
    Step(
        icon="",
        title="7. Almacenamiento",
        body=(
            "En «Ajustes» defines la carpeta de grabaciones y la cuota máxima "
            "de disco (GB). Al superarse la cuota, el sistema borra "
            "automáticamente lo más antiguo.\n\n"
            "Los cambios se aplican al momento, sin reiniciar el servidor."
        ),
        hint="En «Sistema» ves el espacio usado y los días de grabación "
             "que te quedan."
    ),
    Step(
        icon="",
        title="¡Todo listo!",
        body=(
            "Ya tienes lo esencial. Recuerda:\n\n"
            "• Cada vista tiene un botón de ayuda específica.\n"
            "• Este tutorial se reabre desde «Tutorial» en el menú lateral.\n"
            "• En «Sistema» ves el estado real del servidor.\n\n"
            "Si algo no funciona como esperas, revisa el log del servidor o "
            "la ayuda de la sección."
        ),
    ),
]


class OnboardingWizard(QDialog):
    """Wizard modal con paginación y opción de no volver a mostrar."""

    def __init__(self, parent=None, allow_dont_show_again: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Tutorial del NVR")
        self.setMinimumSize(620, 540)
        self.setModal(True)
        self.setStyleSheet("QDialog { background-color: #0f172a; }")

        self._current = 0
        self._allow_dont_show_again = allow_dont_show_again
        self._dont_show_again = False

        self._setup_ui()
        self._render_step()

    # ------------------------------------------------------------------
    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 18)
        outer.setSpacing(14)

        # Header con progreso (puntitos)
        self._progress_layout = QHBoxLayout()
        self._progress_layout.setSpacing(6)
        self._progress_dots: List[QLabel] = []
        for _ in STEPS:
            dot = QLabel("•")
            dot.setStyleSheet("color: #475569; font-size: 22px;")
            self._progress_layout.addWidget(dot)
            self._progress_dots.append(dot)
        self._progress_layout.addStretch()

        skip_btn = QPushButton("Saltar tutorial")
        skip_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #94a3b8;
                border: none; padding: 4px 8px;
            }
            QPushButton:hover { color: #f1f5f9; }
        """)
        skip_btn.clicked.connect(self._on_skip)
        self._progress_layout.addWidget(skip_btn)
        outer.addLayout(self._progress_layout)

        # Card central con el paso
        self.card = QFrame()
        self.card.setStyleSheet(
            "QFrame { background-color: #1e293b; border-radius: 12px; padding: 24px; }"
        )
        cv = QVBoxLayout(self.card)
        cv.setSpacing(14)

        self.lbl_icon = QLabel("")
        self.lbl_icon.setAlignment(Qt.AlignCenter)
        self.lbl_icon.setStyleSheet("font-size: 56px;")
        cv.addWidget(self.lbl_icon)

        self.lbl_title = QLabel("")
        self.lbl_title.setAlignment(Qt.AlignCenter)
        self.lbl_title.setStyleSheet(
            "color: #f1f5f9; font-size: 22px; font-weight: bold;"
        )
        cv.addWidget(self.lbl_title)

        self.lbl_body = QLabel("")
        self.lbl_body.setWordWrap(True)
        self.lbl_body.setAlignment(Qt.AlignLeft)
        self.lbl_body.setStyleSheet(
            "color: #cbd5e1; font-size: 13px; line-height: 1.6;"
        )
        cv.addWidget(self.lbl_body)

        self.hint_box = QFrame()
        self.hint_box.setStyleSheet(
            "QFrame { background-color: #0f172a; border-left: 3px solid #38bdf8; "
            "border-radius: 4px; padding: 10px; }"
        )
        hv = QVBoxLayout(self.hint_box)
        hv.setContentsMargins(10, 8, 10, 8)
        self.lbl_hint = QLabel("")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet(
            "color: #38bdf8; font-size: 12px;"
        )
        hv.addWidget(self.lbl_hint)
        cv.addWidget(self.hint_box)

        cv.addStretch()

        outer.addWidget(self.card, 1)

        # Footer con navegación
        footer = QHBoxLayout()
        self.chk_dont_show = QCheckBox("No volver a mostrar")
        self.chk_dont_show.setStyleSheet(
            "QCheckBox { color: #94a3b8; font-size: 11px; } "
            "QCheckBox::indicator { width: 14px; height: 14px; }"
        )
        self.chk_dont_show.setVisible(self._allow_dont_show_again)
        footer.addWidget(self.chk_dont_show)
        footer.addStretch()

        self.btn_prev = QPushButton("← Atrás")
        self.btn_prev.setStyleSheet(self._secondary_style())
        self.btn_prev.clicked.connect(self._prev)
        footer.addWidget(self.btn_prev)

        self.btn_next = QPushButton("Siguiente →")
        self.btn_next.setStyleSheet(self._primary_style())
        self.btn_next.clicked.connect(self._next)
        footer.addWidget(self.btn_next)

        outer.addLayout(footer)

    @staticmethod
    def _primary_style() -> str:
        return """
            QPushButton {
                background-color: #38bdf8; color: #0f172a;
                border: none; border-radius: 8px;
                padding: 9px 22px; font-weight: bold;
            }
            QPushButton:hover { background-color: #7dd3fc; }
        """

    @staticmethod
    def _secondary_style() -> str:
        return """
            QPushButton {
                background-color: #1e293b; color: #f1f5f9;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 8px; padding: 9px 18px;
            }
            QPushButton:hover:!disabled { background-color: #334155; }
            QPushButton:disabled { color: #475569; }
        """

    # ------------------------------------------------------------------
    def _render_step(self):
        step = STEPS[self._current]
        self.lbl_icon.setText(step.icon)
        self.lbl_title.setText(step.title)
        self.lbl_body.setText(step.body)
        if step.hint:
            self.lbl_hint.setText(step.hint)
            self.hint_box.show()
        else:
            self.hint_box.hide()

        for i, dot in enumerate(self._progress_dots):
            if i == self._current:
                dot.setStyleSheet("color: #38bdf8; font-size: 22px;")
            elif i < self._current:
                dot.setStyleSheet("color: #0ea5e9; font-size: 22px;")
            else:
                dot.setStyleSheet("color: #475569; font-size: 22px;")

        self.btn_prev.setEnabled(self._current > 0)
        last = self._current == len(STEPS) - 1
        self.btn_next.setText("Finalizar " if last else "Siguiente →")

    def _prev(self):
        if self._current > 0:
            self._current -= 1
            self._render_step()

    def _next(self):
        if self._current == len(STEPS) - 1:
            self._dont_show_again = self.chk_dont_show.isChecked()
            self.accept()
            return
        self._current += 1
        self._render_step()

    def _on_skip(self):
        self._dont_show_again = self.chk_dont_show.isChecked()
        self.reject()

    # ------------------------------------------------------------------
    def dont_show_again(self) -> bool:
        return self._dont_show_again


# ----------------------------------------------------------------------
# Helpers de QSettings para persistir el flag "ya se vio"
# ----------------------------------------------------------------------
def _settings():
    from PySide6.QtCore import QSettings
    return QSettings("NVR", "DesktopApp")


def has_seen_tutorial() -> bool:
    return bool(_settings().value("onboarding/seen", False, type=bool))


def mark_tutorial_seen(seen: bool = True) -> None:
    s = _settings()
    s.setValue("onboarding/seen", seen)
    s.sync()


def maybe_show_onboarding(parent=None) -> None:
    """Muestra el wizard si nunca se ha visto; respeta el flag «no mostrar más»."""
    if has_seen_tutorial():
        return
    show_onboarding(parent=parent, force=False)


def show_onboarding(parent=None, force: bool = False) -> None:
    """Muestra el wizard. Si force=False permite guardar «no mostrar más»."""
    wiz = OnboardingWizard(parent=parent, allow_dont_show_again=not force)
    wiz.exec()
    if wiz.dont_show_again() or not force:
        mark_tutorial_seen(True)
