"""
Tema global (QSS) de la app de escritorio.

Aplica UN stylesheet a toda la QApplication para que CADA widget tenga un
aspecto cuidado y coherente (botones, inputs, desplegables, tablas, scrollbars,
menús, checkboxes, sliders, diálogos…), no solo los que cada vista estiliza a
mano. Las hojas de estilo inline de cada vista siguen teniendo prioridad sobre
estas reglas (especificidad), así que esto es ADITIVO: mejora todo lo que hoy
se ve "por defecto de Qt" sin romper lo ya estilizado.

Diseño: "dark premium" (slate + acento cian), coherente con la app móvil.
"""
from __future__ import annotations

# Paleta (coincide con desktop_app/src/config.py y la móvil)
PRIMARY      = "#0f172a"   # fondo base
PRIMARY_DARK = "#0b1220"
SURFACE      = "#1e293b"   # tarjetas / inputs
SURFACE_2    = "#273449"   # hover / variantes
ELEVATED     = "#223049"
BORDER       = "#2a3a52"
BORDER_SOFT  = "rgba(255,255,255,0.08)"
ACCENT       = "#38bdf8"
ACCENT_DARK  = "#0ea5e9"
ACCENT_2     = "#818cf8"
TEXT         = "#f1f5f9"
TEXT_MUTED   = "#94a3b8"
DANGER       = "#ef4444"
SUCCESS      = "#22c55e"


def build_stylesheet() -> str:
    """Devuelve el QSS global de la aplicación (dark premium)."""
    return f"""
/* ===== Base ===== */
QWidget {{
    color: {TEXT};
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background-color: {PRIMARY};
}}
QToolTip {{
    background-color: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 8px;
}}

/* ===== Botones ===== */
QPushButton {{
    background-color: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 14px;
}}
QPushButton:hover {{
    background-color: {SURFACE_2};
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: {ELEVATED};
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
    background-color: {PRIMARY_DARK};
    border-color: {BORDER};
}}
/* Botón de acción primaria: setProperty("accent", True) en el widget */
QPushButton[accent="true"] {{
    background-color: {ACCENT};
    color: {PRIMARY_DARK};
    border: none;
    font-weight: bold;
}}
QPushButton[accent="true"]:hover {{ background-color: {ACCENT_DARK}; }}
/* Botón peligro: setProperty("danger", True) */
QPushButton[danger="true"] {{
    background-color: {DANGER};
    color: white;
    border: none;
}}

/* ===== Inputs ===== */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit {{
    background-color: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 10px;
    selection-background-color: {ACCENT};
    selection-color: {PRIMARY_DARK};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit::placeholder {{ color: {TEXT_MUTED}; }}

/* ===== ComboBox ===== */
QComboBox {{
    background-color: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 10px;
    min-height: 18px;
}}
QComboBox:hover {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 22px;
    border: none;
}}
QComboBox::down-arrow {{
    image: none;
    width: 8px; height: 8px;
    border-left: 2px solid {TEXT_MUTED};
    border-bottom: 2px solid {TEXT_MUTED};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    selection-background-color: {ACCENT};
    selection-color: {PRIMARY_DARK};
    outline: 0;
    padding: 4px;
}}

/* ===== Checkbox / Radio ===== */
QCheckBox, QRadioButton {{ spacing: 8px; color: {TEXT}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 18px; height: 18px;
    border: 1px solid {BORDER};
    background-color: {SURFACE};
}}
QCheckBox::indicator {{ border-radius: 5px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}

/* ===== Scrollbars ===== */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {SURFACE_2};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {ACCENT_DARK}; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {SURFACE_2};
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {ACCENT_DARK}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ===== Tablas / Listas / Árboles ===== */
QTableView, QTableWidget, QTreeView, QListView, QListWidget {{
    background-color: {SURFACE};
    alternate-background-color: {ELEVATED};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 10px;
    gridline-color: {BORDER};
    selection-background-color: {ACCENT};
    selection-color: {PRIMARY_DARK};
    outline: 0;
}}
QTableView::item, QListWidget::item {{ padding: 6px; }}
QTableView::item:selected, QListWidget::item:selected {{
    background-color: {ACCENT};
    color: {PRIMARY_DARK};
}}
QHeaderView::section {{
    background-color: {PRIMARY_DARK};
    color: {TEXT_MUTED};
    padding: 8px;
    border: none;
    border-right: 1px solid {BORDER};
    font-weight: bold;
}}
QTableCornerButton::section {{ background-color: {PRIMARY_DARK}; border: none; }}

/* ===== Tabs ===== */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_MUTED};
    padding: 9px 16px;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:hover {{ color: {TEXT}; }}

/* ===== Sliders ===== */
QSlider::groove:horizontal {{
    height: 6px; border-radius: 3px;
    background: {SURFACE_2};
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    background: {ACCENT};
    width: 16px; height: 16px;
    margin: -6px 0; border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT_DARK}; }}

/* ===== ProgressBar ===== */
QProgressBar {{
    background-color: {SURFACE};
    border: none; border-radius: 6px;
    text-align: center; color: {TEXT};
    height: 12px;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 6px; }}

/* ===== Menús ===== */
QMenuBar {{ background-color: {PRIMARY_DARK}; color: {TEXT}; }}
QMenuBar::item:selected {{ background: {SURFACE_2}; }}
QMenu {{
    background-color: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background-color: {ACCENT}; color: {PRIMARY_DARK}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

/* ===== GroupBox ===== */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 14px;
    padding-top: 8px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {ACCENT};
}}

/* ===== Scroll area / contenedores transparentes ===== */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""


def apply_theme(app) -> None:
    """Aplica el stylesheet global a la QApplication."""
    try:
        app.setStyleSheet(build_stylesheet())
    except Exception:
        # El tema NUNCA debe impedir que arranque la app.
        pass
