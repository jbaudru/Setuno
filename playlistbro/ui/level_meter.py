"""Compact stereo level meter fed by decoded player audio samples."""
from PySide6.QtCore import QRectF, Qt, Slot
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


class StereoLevelMeter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._left_db = -60.0
        self._right_db = -60.0
        self._bg = QColor("#33334d")
        self._colors = [QColor("#8686ac"), QColor("#9a9ac2"), QColor("#ffffff"), QColor("#ef4b4b")]
        self.setFixedWidth(17)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setToolTip("Stereo level (dBFS)")

    @Slot(float, float)
    def set_levels(self, left: float, right: float):
        self._left_db = max(-60.0, min(0.0, float(left)))
        self._right_db = max(-60.0, min(0.0, float(right)))
        self.setToolTip(f"L {self._left_db:.1f} dBFS  |  R {self._right_db:.1f} dBFS")
        self.update()

    def set_theme(self, background: str, accent: str, accent_strong: str, text: str):
        self._bg = QColor(background)
        self._colors = [QColor(accent), QColor(accent_strong), QColor(text), QColor("#ef4b4b")]
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self._bg)
        painter.setPen(Qt.NoPen)
        margin, gap = 2, 1
        bar_width = (self.width() - margin * 2 - gap) // 2
        top, bottom = 6, self.height() - 6
        segment_gap = 1
        segments = 50
        segment_height = max(1, (bottom - top - (segments - 1) * segment_gap) // segments)
        for channel, level_db in enumerate((self._left_db, self._right_db)):
            x = margin + channel * (bar_width + gap)
            active = round(((level_db + 60.0) / 60.0) * segments)
            for index in range(segments):
                ratio = (index + 1) / segments
                if ratio <= 0.68:
                    color = QColor(self._colors[0])
                elif ratio <= 0.82:
                    color = QColor(self._colors[1])
                elif ratio <= 0.93:
                    color = QColor(self._colors[2])
                else:
                    color = QColor(self._colors[3])
                if index >= active:
                    color.setAlpha(38)
                y = bottom - (index + 1) * segment_height - index * segment_gap
                radius = min(2.0, bar_width / 2, segment_height / 2)
                painter.setBrush(color)
                painter.drawRoundedRect(
                    QRectF(x, y, bar_width, segment_height), radius, radius,
                )
        painter.end()
