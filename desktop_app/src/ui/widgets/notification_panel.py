
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, 
    QScrollArea, QFrame, QHBoxLayout
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont
from collections import deque

class NotificationItem(QFrame):
    view_requested = Signal(dict)

    def __init__(self, event_data: dict, parent=None):
        super().__init__(parent)
        self.event_data = event_data
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setMaximumHeight(80)

        acknowledged = event_data.get("acknowledged", False)
        if acknowledged:
            self.setStyleSheet(
                "NotificationItem { background-color: #f0f0f0; "
                "border-radius: 5px; border: 1px solid #cccccc; margin: 2px; }"
            )
        else:
            self.setStyleSheet(
                "NotificationItem { background-color: #ffe6e6; "
                "border-radius: 5px; border: 1px solid #ff9999; margin: 2px; }"
            )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)

        # Emoji based on event type
        event_type = event_data.get("event_type", "unknown")
        emoji_map = {
            "person": "🚨",
            "vehicle": "🚗",
            "motion": "📹",
            "camera_offline": "⚠️",
            "tampering": "🔴"
        }
        emoji = emoji_map.get(event_type, "🔔")

        icon_label = QLabel(emoji)
        icon_label.setFont(QFont("Segoe UI Emoji", 16))
        layout.addWidget(icon_label)

        # Text info
        camera_name = event_data.get("camera_name", "Desconocida")
        created_at = event_data.get("created_at", "")
        time_str = str(created_at) if created_at else ""

        text = f"{camera_name}\\n{event_type}\\n{time_str}"
        text_label = QLabel(text)
        layout.addWidget(text_label, stretch=1)

        # View button if has snapshot
        if event_data.get("snapshot_path"):
            view_btn = QPushButton("Ver")
            view_btn.clicked.connect(self._on_view)
            layout.addWidget(view_btn)

    def _on_view(self):
        self.view_requested.emit(self.event_data)

    def set_acknowledged(self):
        self.setStyleSheet(
            "NotificationItem { background-color: #f0f0f0; "
            "border-radius: 5px; border: 1px solid #cccccc; margin: 2px; }"
        )
        self.event_data["acknowledged"] = True

class NotificationPanel(QWidget):
    event_view_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._events: deque = deque(maxlen=50)
        self._unread_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("Notificaciones")
        header_layout.addWidget(title)

        self.badge_label = QLabel("0")
        self.badge_label.setStyleSheet(
            "background-color: red; color: white; font-weight: bold; "
            "border-radius: 10px; padding: 2px 6px; font-size: 10px;"
        )
        self.badge_label.setVisible(False)
        header_layout.addWidget(self.badge_label)
        header_layout.addStretch()

        # Mark all read button
        mark_read_btn = QPushButton("Marcar todas como leídas")
        mark_read_btn.clicked.connect(self.mark_all_read)
        header_layout.addWidget(mark_read_btn)

        layout.addLayout(header_layout)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(300)

        self.notifications_container = QWidget()
        self.notifications_layout = QVBoxLayout(self.notifications_container)
        self.notifications_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.notifications_layout.setSpacing(5)
        self.notifications_layout.setContentsMargins(5, 5, 5, 5)

        scroll.setWidget(self.notifications_container)
        layout.addWidget(scroll)

        # Spacer at bottom
        self.notifications_layout.addStretch()

    def add_notification(self, event_data: dict):
        self._events.append(event_data)

        # Create widget
        item = NotificationItem(event_data)
        item.view_requested.connect(self.event_view_requested.emit)

        # Insert at top (before the stretch)
        count = self.notifications_layout.count()
        if count > 0:
            # Insert before the last item (stretch)
            self.notifications_layout.insertWidget(count - 1, item)
        else:
            self.notifications_layout.addWidget(item)

        # Remove oldest widget if deque is full (deque auto-removes, but we need to remove widget)
        if len(self._events) == 50:
            # The oldest widget is at index 0
            old_item = self.notifications_layout.itemAt(0)
            if old_item and old_item.widget():
                old_widget = old_item.widget()
                if old_widget and old_widget != item:
                    old_widget.deleteLater()

        # Update unread count
        if not event_data.get("acknowledged"):
            self._unread_count += 1
            self._update_badge()

    def mark_all_read(self):
        self._unread_count = 0
        self._update_badge()

        # Update all items
        for i in range(self.notifications_layout.count()):
            item = self.notifications_layout.itemAt(i)
            if item and item.widget():
                widget = item.widget()
                if isinstance(widget, NotificationItem):
                    widget.set_acknowledged()

    def _update_badge(self):
        if self._unread_count > 0:
            self.badge_label.setText(str(self._unread_count))
            self.badge_label.setVisible(True)
        else:
            self.badge_label.setVisible(False)
