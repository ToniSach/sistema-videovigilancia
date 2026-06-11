"""
================================================================================
MÓDULO: ui.views.dashboard_view — Pantalla de inicio / panel de control
================================================================================

PROPÓSITO
    Primera pantalla tras el login (índice VIEW_DASHBOARD del content_stack):
    un resumen "de un vistazo" del sistema, al estilo de apps comerciales
    (Reolink / UniFi). Muestra:
      - Saludo personalizado + fecha.
      - Tarjetas KPI: cámaras activas, eventos hoy, carga del servidor
        (CPU/RAM/Disco), tiempo activo.
      - Lista de actividad reciente (últimos eventos; clic → Eventos).
      - Accesos rápidos a las secciones más usadas (En vivo, Grabaciones,
        Eventos, Cámaras).

RESPONSABILIDAD
    - Sondear periódicamente (cada 8s mientras es visible) tres endpoints de
      solo-lectura y volcar los datos en las tarjetas KPI / lista de eventos.
    - NO navega por sí misma: emite señales `open_*` que MainWindow traduce en
      cambios de página. Es una pantalla puramente informativa + lanzadera.

DEPENDENCIAS (endpoints que consume vía api_client)
    - GET /system/health  → KPIs de cámaras sanas, CPU/RAM/Disco, uptime.
    - GET /events/?hours=24&limit=50 → eventos de hoy + sin revisar + actividad.
    - GET /storage/info   → uso de disco y estimación de días restantes.
    NO introduce endpoints nuevos (reutiliza los de otras vistas).

COMPONENTES RELACIONADOS
    - ui/components/glass_card.GlassCard : superficie de las tarjetas (y base de
      la tarjeta KPI interna `_KpiCard`).
    - ui/icons.icon : iconos de los botones de acceso rápido.

PUNTO DE ENTRADA
    La instancia MainWindow._create_main_view (import diferido). MainWindow
    conecta sus 4 señales `open_*` a `_switch_view(...)` y llama a `set_user`
    tras el login para personalizar el saludo.

PIPELINE(S)
    Transversal a #3 Live / #10 Eventos (solo como lanzadera de navegación);
    no participa en la captura ni en el procesamiento, solo agrega métricas.
================================================================================
"""
import logging
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer

from desktop_app.src.config import config
from desktop_app.src.services.api_client import api_client
from desktop_app.src.ui.components.glass_card import GlassCard
from desktop_app.src.ui.icons import icon

logger = logging.getLogger(__name__)

# Nombres en español sin depender de `locale` (poco fiable en Windows).
_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _fecha_es(dt: datetime) -> str:
    """Devuelve la fecha en español, p.ej. 'domingo, 07 de junio de 2026'."""
    try:
        dia = _DIAS_ES[dt.weekday()].capitalize()
        mes = _MESES_ES[dt.month - 1]
        return f"{dia}, {dt.day:02d} de {mes} de {dt.year}"
    except Exception:
        # Nunca debe romper el arranque del panel por un fallo de formato.
        return dt.strftime("%d/%m/%Y")


class _KpiCard(GlassCard):
    """Tarjeta compacta: valor grande + etiqueta + color de acento."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent, border_radius=12)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(2)

        self.lbl_value = QLabel("—")
        self.lbl_value.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 26px; font-weight: bold;"
        )
        self.lbl_label = QLabel(label)
        self.lbl_label.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 12px;"
        )
        self.lbl_sub = QLabel("")
        self.lbl_sub.setStyleSheet("font-size: 11px;")
        lay.addWidget(self.lbl_value)
        lay.addWidget(self.lbl_label)
        lay.addWidget(self.lbl_sub)
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set(self, value: str, sub: str = "", color: str = None):
        self.lbl_value.setText(value)
        if color:
            self.lbl_value.setStyleSheet(
                f"color: {color}; font-size: 26px; font-weight: bold;"
            )
        self.lbl_sub.setText(sub)
        self.lbl_sub.setStyleSheet(
            f"color: {color or config.THEME_TEXT_MUTED}; font-size: 11px;"
        )


class DashboardView(QWidget):
    """
    Pantalla de inicio: resumen del sistema + lanzadera de navegación.

    RESPONSABILIDAD / ROL
        Agregar métricas de salud, eventos y almacenamiento en tarjetas KPI y
        ofrecer accesos directos. No ejecuta lógica de negocio: solo lee y
        navega.

    QUIÉN LA INSTANCIA
        MainWindow._create_main_view (índice VIEW_DASHBOARD del content_stack).

    SEÑALES QT
        EMITE (las escucha MainWindow para conmutar de vista):
          - open_live      → ir a "En vivo".
          - open_events    → ir a "Eventos".
          - open_cameras   → ir a "Cámaras".
          - open_playback  → ir a "Reproducción".
        No escucha señales externas.

    CICLO DE VIDA
        `showEvent` arranca un QTimer de 8s que llama a `_poll`; `hideEvent` lo
        detiene (no sondear en background).

    DEPENDENCIAS
        api_client (GET /system/health, /events/, /storage/info), GlassCard.
    """

    open_live = Signal()
    open_events = Signal()
    open_cameras = Signal()
    open_playback = Signal()

    EVENT_LABELS = {
        "person": "Persona", "vehicle": "Vehículo", "motion": "Movimiento",
        "camera_offline": "Cámara desconectada",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._setup_ui()

    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)

        # Cabecera: saludo + fecha
        head = QVBoxLayout()
        self.lbl_hello = QLabel("Panel de control")
        self.lbl_hello.setStyleSheet(
            f"color: {config.THEME_TEXT}; font-size: 24px; font-weight: bold;"
        )
        self.lbl_date = QLabel(_fecha_es(datetime.now()))
        self.lbl_date.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 13px;")
        head.addWidget(self.lbl_hello)
        head.addWidget(self.lbl_date)
        layout.addLayout(head)

        # KPIs
        kpis = QHBoxLayout()
        kpis.setSpacing(12)
        self.kpi_cams = _KpiCard("Cámaras activas")
        self.kpi_events = _KpiCard("Eventos hoy")
        self.kpi_health = _KpiCard("Carga del servidor")
        self.kpi_uptime = _KpiCard("Tiempo activo")
        for k in (self.kpi_cams, self.kpi_events, self.kpi_health, self.kpi_uptime):
            kpis.addWidget(k)
        layout.addLayout(kpis)

        # Detalle de almacenamiento (uso + días restantes estimados).
        self.lbl_disk_detail = QLabel("Almacenamiento: —")
        self.lbl_disk_detail.setStyleSheet(
            f"color: {config.THEME_TEXT_MUTED}; font-size: 12px;"
        )
        layout.addWidget(self.lbl_disk_detail)

        # Cuerpo: últimos eventos (izq) + accesos rápidos (der)
        body = QHBoxLayout()
        body.setSpacing(16)

        # --- Últimos eventos ---
        events_card = GlassCard()
        ev = QVBoxLayout(events_card)
        ev.setContentsMargins(16, 14, 16, 14)
        ev_head = QHBoxLayout()
        t = QLabel("Actividad reciente")
        t.setStyleSheet(f"color: {config.THEME_ACCENT}; font-weight: bold; font-size: 14px;")
        ev_head.addWidget(t)
        ev_head.addStretch()
        btn_all = QPushButton("Ver todo")
        btn_all.setFlat(True)
        btn_all.setStyleSheet(f"color: {config.THEME_ACCENT}; border: none;")
        btn_all.clicked.connect(self.open_events.emit)
        ev_head.addWidget(btn_all)
        ev.addLayout(ev_head)

        self.events_box = QVBoxLayout()
        self.events_box.setSpacing(6)
        ev.addLayout(self.events_box)
        ev.addStretch()
        body.addWidget(events_card, 2)

        # --- Accesos rápidos ---
        quick_card = GlassCard()
        qk = QVBoxLayout(quick_card)
        qk.setContentsMargins(16, 14, 16, 14)
        qt = QLabel("Accesos rápidos")
        qt.setStyleSheet(f"color: {config.THEME_ACCENT}; font-weight: bold; font-size: 14px;")
        qk.addWidget(qt)
        for text, ic, sig in [
            ("Ver en vivo", "live", self.open_live),
            ("Grabaciones", "playback", self.open_playback),
            ("Eventos", "events", self.open_events),
            ("Cámaras", "cameras", self.open_cameras),
        ]:
            b = QPushButton("  " + text)
            try:
                b.setIcon(icon(ic))
            except Exception:
                pass
            b.setMinimumHeight(40)
            b.setStyleSheet(self._quick_btn_style())
            b.clicked.connect(sig.emit)
            qk.addWidget(b)
        qk.addStretch()
        body.addWidget(quick_card, 1)

        layout.addLayout(body, 1)

    def _quick_btn_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: {config.THEME_SECONDARY};
                color: {config.THEME_TEXT};
                border: 1px solid {config.GLASS_BORDER};
                border-radius: 8px; padding: 8px 12px; text-align: left;
            }}
            QPushButton:hover {{
                background-color: {config.THEME_ACCENT};
                color: {config.THEME_PRIMARY};
            }}
        """

    # ------------------------------------------------------------------
    def _poll(self):
        """Refresca TODOS los KPIs disparando 3 peticiones async en paralelo.

        Propósito: actualizar tarjetas + actividad reciente desde el backend.
        Cada callback corre en el hilo UI y pinta su parte (cámaras/CPU/uptime,
        eventos hoy/sin revisar + lista, almacenamiento). Llamado por: `showEvent`
        y el QTimer de 8s. Llama a: GET /system/health, GET /events/,
        GET /storage/info."""
        # Salud del sistema
        def on_health(resp):
            if not resp.success:
                return
            d = resp.data or {}
            sysd = d.get("system", {})
            cams = d.get("cameras", [])
            healthy = sum(1 for c in cams if c.get("status") == "healthy")
            total = len(cams)
            self.kpi_cams.set(
                f"{healthy}/{total}",
                sub=("Todas OK" if total and healthy == total else f"{total-healthy} inactivas"),
                color=("#22c55e" if total and healthy == total else "#fbbf24"),
            )
            cpu = sysd.get("cpu_percent", 0)
            ram = sysd.get("memory_percent", 0)
            self.kpi_health.set(
                f"{cpu:.0f}%",
                sub=f"RAM {ram:.0f}%  ·  Disco {sysd.get('disk_percent', 0):.0f}%",
                color=("#ef4444" if cpu > 85 else config.THEME_ACCENT),
            )
            self.kpi_uptime.set(self._fmt_uptime(int(sysd.get("uptime_seconds", 0) or 0)),
                                sub="desde el arranque")
        api_client.get("system/health", on_health)

        # Eventos (hoy + sin revisar)
        def on_events(resp):
            if not resp.success:
                return
            events = resp.data or []
            today = datetime.now().date()
            n_today = 0
            n_unread = 0
            for e in events:
                if not e.get("acknowledged", False):
                    n_unread += 1
                ts = e.get("created_at") or e.get("timestamp")
                try:
                    d = datetime.fromisoformat(str(ts).split(".")[0])
                    if d.date() == today:
                        n_today += 1
                except Exception:
                    pass
            # KPI resalta en ámbar si hay eventos sin revisar.
            if n_unread > 0:
                self.kpi_events.set(str(n_today), sub=f"{n_unread} sin revisar",
                                    color="#f59e0b")
            else:
                self.kpi_events.set(str(n_today), sub="todo revisado")
            self._render_events(events[:6])
        api_client.get("events/?hours=24&limit=50", on_events)

        # Almacenamiento: días estimados de grabación restantes.
        def on_storage(resp):
            if not resp.success:
                return
            d = resp.data or {}
            used = float(d.get("recordings_used_gb", 0) or 0)
            total = float(d.get("disk_total_gb", 0) or 0)
            pct = d.get("percent_used", 0)
            free = max(0.0, total - used)
            # Estimación simple: GB/día = used / días_de_grabación. Si no hay
            # histórico, usamos un ritmo aproximado por nº de cámaras activas.
            gb_per_day = getattr(self, "_gb_per_day_hint", 0) or 0
            days_txt = ""
            if gb_per_day > 0:
                days_txt = f"  ·  ~{int(free / gb_per_day)} días restantes"
            self.kpi_disk_extra = f"{used:.0f}/{total:.0f} GB ({pct}%){days_txt}"
            if hasattr(self, "lbl_disk_detail"):
                self.lbl_disk_detail.setText(self.kpi_disk_extra)
        api_client.get("storage/info", on_storage)

    def _render_events(self, events):
        # Limpiar
        while self.events_box.count():
            item = self.events_box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not events:
            empty = QLabel("Sin actividad en las últimas 24 horas.")
            empty.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 12px;")
            self.events_box.addWidget(empty)
            return
        for e in events:
            self.events_box.addWidget(self._event_row(e))

    def _event_row(self, e: dict) -> QFrame:
        row = QFrame()
        row.setStyleSheet(
            "QFrame { background-color: #1e293b; border-radius: 8px; }"
            "QFrame:hover { background-color: #273449; }"
        )
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 8, 10, 8)

        ev_type = e.get("event_type", "?")
        dot = QLabel("●")
        color = {"person": "#22c55e", "vehicle": "#38bdf8",
                 "motion": "#fbbf24", "camera_offline": "#ef4444"}.get(ev_type, "#94a3b8")
        dot.setStyleSheet(f"color: {color}; font-size: 14px;")
        h.addWidget(dot)

        label = self.EVENT_LABELS.get(ev_type, ev_type)
        cam = e.get("camera_name") or f"Cámara {e.get('camera_id', '?')}"
        txt = QLabel(f"<b>{label}</b> · {cam}")
        txt.setStyleSheet(f"color: {config.THEME_TEXT}; font-size: 12px;")
        h.addWidget(txt)
        h.addStretch()

        ts = e.get("created_at") or e.get("timestamp")
        try:
            d = datetime.fromisoformat(str(ts).split(".")[0])
            when = d.strftime("%H:%M")
        except Exception:
            when = ""
        tlbl = QLabel(when)
        tlbl.setStyleSheet(f"color: {config.THEME_TEXT_MUTED}; font-size: 11px;")
        h.addWidget(tlbl)

        row.mousePressEvent = lambda _ev: self.open_events.emit()
        return row

    @staticmethod
    def _fmt_uptime(seconds: int) -> str:
        d, rem = divmod(seconds, 86400)
        hh, rem = divmod(rem, 3600)
        mm, _ = divmod(rem, 60)
        if d > 0:
            return f"{d}d {hh}h"
        if hh > 0:
            return f"{hh}h {mm}m"
        return f"{mm}m"

    def set_user(self, username: str):
        """Personaliza el saludo de cabecera según la hora y el usuario.

        Inputs: `username`. Output: actualiza `lbl_hello` ("Buenos días, X").
        Llamado por: MainWindow._on_login_success tras autenticar."""
        hour = datetime.now().hour
        greet = "Buenos días" if hour < 12 else ("Buenas tardes" if hour < 20 else "Buenas noches")
        self.lbl_hello.setText(f"{greet}, {username}" if username else "Panel de control")

    # ------------------------------------------------------------------
    def showEvent(self, event):
        """Al hacerse visible: refresco inmediato + arranque del polling de 8s."""
        self._poll()
        if not self._timer.isActive():
            self._timer.start(8000)
        super().showEvent(event)

    def hideEvent(self, event):
        """Al ocultarse: detiene el polling para no consumir CPU/red en background."""
        self._timer.stop()
        super().hideEvent(event)
