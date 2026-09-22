"""Drawn tempo and energy curve editor for playlist generation."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class _CurveCanvas(QWidget):
    def __init__(
        self, title: str, color: str, values=None, value_range=(0, 1),
        x_labels=("Start", "Middle", "End"), bg="#252538", text="#d8d8ec", parent=None,
    ):
        super().__init__(parent)
        self.title = title
        self.color = QColor(color)
        self.bg_color = QColor(bg)
        self.text_color = QColor(text)
        self.value_range = value_range
        self.x_labels = x_labels
        self.values = list(values or [0.5] * 24)
        self.setMinimumHeight(150)
        self.setMouseTracking(True)

    def _set_value(self, position):
        left, top, right, bottom = 44, 24, 12, 25
        plot_width = max(1, self.width() - left - right)
        plot_height = max(1, self.height() - top - bottom)
        index = round((position.x() - left) / plot_width * (len(self.values) - 1))
        value = 1.0 - (position.y() - top) / plot_height
        self.values[max(0, min(len(self.values) - 1, index))] = max(0.0, min(1.0, value))
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._set_value(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._set_value(event.position())

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.bg_color)
        left, top, right, bottom = 44, 24, 12, 25
        width = self.width() - left - right
        height = self.height() - top - bottom
        grid = QColor(self.text_color)
        grid.setAlpha(55)
        painter.setPen(QPen(grid, 1))
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = int(top + height * fraction)
            painter.drawLine(left, y, left + width, y)
        for fraction in (0.0, 0.5, 1.0):
            x = int(left + width * fraction)
            painter.drawLine(x, top, x, top + height)
        painter.setPen(QPen(self.color, 2))
        for index in range(1, len(self.values)):
            x1 = left + (index - 1) / (len(self.values) - 1) * width
            y1 = top + (1 - self.values[index - 1]) * height
            x2 = left + index / (len(self.values) - 1) * width
            y2 = top + (1 - self.values[index]) * height
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))
        painter.setPen(self.text_color)
        painter.drawText(left, 17, self.title)
        minimum, maximum = self.value_range
        for fraction in (0.0, 0.5, 1.0):
            value = maximum - fraction * (maximum - minimum)
            painter.drawText(2, int(top + height * fraction + 4), f"{value:.0f}")
        for fraction, label in zip((0.0, 0.5, 1.0), self.x_labels):
            x = int(left + width * fraction)
            painter.drawText(x - (0 if fraction == 0 else 25), self.height() - 5, label)


class ProfileEditorDialog(QDialog):
    def __init__(
        self, target_label: str, profile=None, tempo_range=(0, 300), energy_range=(0, 10),
        bg="#252538", text="#d8d8ec", parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Custom Playlist Profile")
        self.resize(620, 380)
        profile = profile or {}
        if "tracks" in target_label:
            total = max(1, int(target_label.split()[0]))
            x_labels = ("1", str(max(1, total // 2)), str(total))
        else:
            total = max(1, int(target_label.split()[0]))
            x_labels = ("0 min", f"{total // 2} min", f"{total} min")
        self.tempo = _CurveCanvas(
            "Tempo (BPM)", "#69a6e8", profile.get("tempo"), tempo_range, x_labels, bg, text,
        )
        self.energy = _CurveCanvas(
            "Energy", "#e06c48", profile.get("energy"), energy_range, x_labels, bg, text,
        )
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Draw the desired profile across {target_label}."))
        layout.addWidget(self.tempo)
        layout.addWidget(self.energy)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        apply = QPushButton("Use profile")
        apply.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        layout.addLayout(buttons)

    def profile(self):
        return {"tempo": self.tempo.values, "energy": self.energy.values}