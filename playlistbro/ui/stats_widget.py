"""Statistics panel: tempo curve, energy curve and genre breakdown for a playlist.

Rendered with plain QPainter (no matplotlib/pandas/pillow chain) to keep the
packaged app small and portable.
"""
import math
from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..core.playlist_engine import playlist_stats

Y_TICKS = 4

_HUE_STEPS = (0, 16, -16, 32, -32, 48, -48)
_LIGHT_STEPS = (0, -40, 40)


def palette_shade(accent: QColor, index: int) -> QColor:
    """A color close to the theme accent: small hue and lightness steps around it."""
    hue = max(0, accent.hslHue())
    sat = max(60, accent.hslSaturation())
    light = min(200, max(70, accent.lightness()))
    hue = (hue + _HUE_STEPS[index % len(_HUE_STEPS)]) % 360
    light += _LIGHT_STEPS[(index // len(_HUE_STEPS)) % len(_LIGHT_STEPS)]
    return QColor.fromHsl(hue, sat, min(215, max(55, light)))


class _CurveChart(QWidget):
    """Simple line/bar chart for a single numeric series, with axis ticks and value labels."""

    hovered = Signal(int)

    def __init__(self, title: str, color: str, kind: str = "line", x_label: str = "", y_label: str = "", parent=None):
        super().__init__(parent)
        self.title = title
        self.color = QColor(color)
        self.kind = kind
        self.x_label = x_label
        self.y_label = y_label
        self.values: list[float] = []
        self.hovered_index = -1
        self.bg_color = QColor("#33334d")
        self.axis_color = QColor("#8686AC")
        self.title_color = QColor("#ffffff")
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

    def set_values(self, values: list[float]):
        self.values = values
        self.hovered_index = -1
        self.update()

    def mouseMoveEvent(self, event):
        plot_left, plot_right = 46, self.width() - 10
        if not self.values or not (plot_left <= event.position().x() <= plot_right and
                                   22 <= event.position().y() <= self.height() - 40):
            index = -1
        else:
            ratio = (event.position().x() - plot_left) / max(1, plot_right - plot_left)
            index = min(len(self.values) - 1, max(0, int(ratio * len(self.values)) if self.kind == "bar"
                                                 else round(ratio * (len(self.values) - 1))))
        if index != self.hovered_index:
            self.hovered_index = index
            self.hovered.emit(index)
            self.setToolTip(f"Track {index + 1}: {self.values[index]:.1f} {self.y_label}" if index >= 0 else "")
            self.update()

    def leaveEvent(self, event):
        if self.hovered_index != -1:
            self.hovered_index = -1
            self.hovered.emit(-1)
            self.update()
        super().leaveEvent(event)

    def set_color(self, color: str):
        self.color = QColor(color)
        self.update()

    def set_theme(self, bg: str, axis: str, title: str):
        self.bg_color = QColor(bg)
        self.axis_color = QColor(axis)
        self.title_color = QColor(title)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self.bg_color)

        painter.setPen(QPen(self.title_color))
        painter.drawText(QRectF(0, 2, self.width(), 18), Qt.AlignHCenter, self.title)

        axis_font = painter.font()
        axis_font.setPointSize(8)
        painter.setFont(axis_font)
        fm = QFontMetrics(axis_font)
        margin_top, margin_bottom, margin_left, margin_right = 22, 40, 46, 10
        plot_h = max(1, self.height() - margin_top - margin_bottom)
        plot_w = max(1, self.width() - margin_left - margin_right)

        painter.setPen(QPen(self.axis_color))
        painter.drawLine(margin_left, margin_top, margin_left, margin_top + plot_h)
        painter.drawLine(margin_left, margin_top + plot_h, margin_left + plot_w, margin_top + plot_h)

        if self.x_label:
            painter.setPen(QPen(self.title_color))
            painter.drawText(
                QRectF(margin_left, self.height() - 14, plot_w, 12),
                Qt.AlignHCenter | Qt.AlignVCenter, self.x_label,
            )
        if self.y_label:
            painter.save()
            painter.setPen(QPen(self.title_color))
            painter.translate(10, margin_top + plot_h / 2 + 20)
            painter.rotate(-90)
            painter.drawText(QRectF(-40, -7, 80, 12), Qt.AlignHCenter | Qt.AlignVCenter, self.y_label)
            painter.restore()

        if not self.values:
            return
        lo, hi = min(self.values), max(self.values)
        if hi - lo < 1e-6:
            hi = lo + 1.0
        n = len(self.values)

        # y-axis ticks with values (label column sits to the right of the rotated y title)
        painter.setPen(QPen(self.axis_color))
        for i in range(Y_TICKS + 1):
            frac = i / Y_TICKS
            y = margin_top + plot_h - frac * plot_h
            value = lo + frac * (hi - lo)
            painter.drawLine(margin_left - 3, int(y), margin_left, int(y))
            label = f"{value:.0f}"
            painter.drawText(
                QRectF(18, y - 6, margin_left - 22, 12), Qt.AlignRight | Qt.AlignVCenter, label,
            )

        # x-axis ticks: first, middle, last track number
        tick_positions = sorted({0, n // 2, n - 1}) if n > 1 else [0]
        for i in tick_positions:
            x = margin_left + (i / max(1, n - 1)) * plot_w if n > 1 else margin_left + plot_w / 2
            painter.drawLine(int(x), margin_top + plot_h, int(x), margin_top + plot_h + 3)
            label = str(i + 1)
            w = fm.horizontalAdvance(label)
            painter.drawText(
                QRectF(x - w / 2 - 4, margin_top + plot_h + 4, w + 8, 12),
                Qt.AlignHCenter | Qt.AlignVCenter, label,
            )

        painter.setClipRect(QRectF(margin_left, margin_top, plot_w, plot_h))

        def y_at(v):
            return margin_top + plot_h - ((v - lo) / (hi - lo)) * plot_h

        if self.kind == "bar":
            slot_w = plot_w / n
            bar_w = slot_w * 0.7
            painter.setBrush(self.color)
            painter.setPen(Qt.NoPen)
            for i, v in enumerate(self.values):
                x = margin_left + slot_w * (i + 0.5) - bar_w / 2
                y = y_at(v)
                painter.drawRect(QRectF(x, y, bar_w, margin_top + plot_h - y))
        else:
            def x_at(i):
                return margin_left + (i / max(1, n - 1)) * plot_w if n > 1 else margin_left + plot_w / 2

            pen = QPen(self.color)
            pen.setWidthF(2.0)
            painter.setPen(pen)
            points = [(x_at(i), y_at(v)) for i, v in enumerate(self.values)]
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))
            painter.setBrush(self.color)
            painter.setPen(Qt.NoPen)
            for x, y in points:
                painter.drawEllipse(QRectF(x - 2.5, y - 2.5, 5, 5))

        if 0 <= self.hovered_index < n:
            x = (margin_left + (self.hovered_index + 0.5) / n * plot_w if self.kind == "bar"
                 else margin_left + self.hovered_index / max(1, n - 1) * plot_w if n > 1
                 else margin_left + plot_w / 2)
            y = y_at(self.values[self.hovered_index])
            painter.setPen(QPen(self.title_color, 1, Qt.DashLine))
            painter.drawLine(int(x), margin_top, int(x), margin_top + plot_h)
            painter.setPen(QPen(self.color, 2))
            painter.setBrush(self.bg_color)
            painter.drawEllipse(QRectF(x - 5, y - 5, 10, 10))


class _GenrePie(QWidget):
    """Simple genre-breakdown pie chart with a color-coded legend."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.genres: dict[str, int] = {}
        self.bg_color = QColor("#33334d")
        self.title_color = QColor("#ffffff")
        self.accent_color = QColor("#8686AC")
        self.hovered_genre = -1
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

    def set_genres(self, genres: dict[str, int]):
        self.genres = genres
        self.hovered_genre = -1
        self.update()

    def set_theme(self, bg: str, title: str, accent: str):
        self.bg_color = QColor(bg)
        self.title_color = QColor(title)
        self.accent_color = QColor(accent)
        self.update()

    def _pie_rect(self):
        side = max(48, min(self.width() * 0.42, self.height() - 48, 150))
        return QRectF(8, 28, side, side)

    def mouseMoveEvent(self, event):
        total = sum(self.genres.values())
        rect = self._pie_rect()
        x, y = event.position().x(), event.position().y()
        index = -1
        legend_x = rect.right() + 12
        visible_count = max(1, int((self.height() - 32) / 18))
        if total and x >= legend_x and 28 <= y < 28 + 18 * min(len(self.genres), visible_count):
            index = int((y - 28) // 18)
            if len(self.genres) > visible_count and index == visible_count - 1:
                index = -1
        elif total and rect.width() > 0:
            dx = (x - rect.center().x()) / (rect.width() / 2)
            dy = (y - rect.center().y()) / (rect.height() / 2)
            if dx * dx + dy * dy <= 1:
                clockwise = (90 - math.degrees(math.atan2(-dy, dx))) % 360
                position = clockwise / 360 * total
                for candidate, count in enumerate(self.genres.values()):
                    position -= count
                    if position < 0:
                        index = candidate
                        break
        if index != self.hovered_genre:
            self.hovered_genre = index
            if index >= 0:
                name, count = list(self.genres.items())[index]
                self.setToolTip(f"{name}: {count} tracks ({count / total:.0%})")
            else:
                self.setToolTip("")
            self.update()

    def leaveEvent(self, event):
        self.hovered_genre = -1
        self.setToolTip("")
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self.bg_color)
        painter.setPen(QPen(self.title_color))
        painter.drawText(QRectF(0, 2, self.width(), 18), Qt.AlignHCenter, "Genres")

        total = sum(self.genres.values())
        if total <= 0:
            return

        rect = self._pie_rect()

        start_angle = 90 * 16
        colors = []
        for i, (name, count) in enumerate(self.genres.items()):
            span = -int(360 * 16 * (count / total))
            color = palette_shade(self.accent_color, i)
            colors.append(color)
            painter.setBrush(color)
            painter.setPen(QPen(self.title_color, 2) if i == self.hovered_genre else Qt.NoPen)
            painter.drawPie(rect, start_angle, span)
            start_angle += span

        legend_y = 28
        legend_x = rect.right() + 12
        available = max(20, self.width() - legend_x - 18)
        visible_count = max(1, int((self.height() - 32) / 18))
        shown = visible_count - 1 if len(self.genres) > visible_count else len(self.genres)
        for (name, count), color in list(zip(self.genres.items(), colors))[:shown]:
            pct = 100 * count / total
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawRect(QRectF(legend_x, legend_y + 3, 10, 10))
            painter.setPen(QPen(self.title_color))
            painter.drawText(
                QRectF(legend_x + 14, legend_y, available, 16),
                Qt.AlignVCenter,
                painter.fontMetrics().elidedText(f"{name} ({count}, {pct:.0f}%)", Qt.ElideRight, int(available)),
            )
            legend_y += 18
        if shown < len(self.genres):
            painter.drawText(QRectF(legend_x, legend_y, available, 16),
                             Qt.AlignVCenter, f"+{len(self.genres) - shown} more")


class StatsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.tempo_chart = _CurveChart(
            "Tempo (BPM)", "#8686AC", kind="line", x_label="Track #", y_label="BPM",
        )
        self.energy_chart = _CurveChart(
            "Energy", "#505081", kind="bar", x_label="Track #", y_label="Energy (1-10)",
        )
        self.genre_chart = _GenrePie()

        self.summary_label = QLabel("No playlist generated yet.")
        self.summary_label.setStyleSheet("color: #8686AC; font-weight: bold;")
        self._tracks = []
        self._summary_text = self.summary_label.text()
        self.tempo_chart.hovered.connect(self._hover_track)
        self.energy_chart.hovered.connect(self._hover_track)

        charts = QHBoxLayout()
        charts.addWidget(self.tempo_chart)
        charts.addWidget(self.energy_chart)
        charts.addWidget(self.genre_chart)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary_label)
        layout.addLayout(charts)

    def update_stats(self, tracks):
        self._tracks = tracks
        stats = playlist_stats(tracks)
        if stats["count"] == 0:
            self.summary_label.setText("No playlist generated yet.")
            self.tempo_chart.set_values([])
            self.energy_chart.set_values([])
            self.genre_chart.set_genres({})
            self._summary_text = self.summary_label.text()
            return

        total_min = stats["total_duration"] / 60
        self.summary_label.setText(
            f"{stats['count']} tracks · {total_min:.1f} min · "
            f"avg tempo {stats['avg_tempo']} BPM · avg energy {stats['avg_energy']}/10"
        )
        self._summary_text = self.summary_label.text()
        self.tempo_chart.set_values([t.tempo for t in tracks])
        self.energy_chart.set_values([t.energy for t in tracks])
        self.genre_chart.set_genres(stats["genres"])

    def _hover_track(self, index: int):
        for chart in (self.tempo_chart, self.energy_chart):
            if chart.hovered_index != index:
                chart.hovered_index = index
                chart.update()
        if 0 <= index < len(self._tracks):
            track = self._tracks[index]
            self.summary_label.setText(
                f"{index + 1}. {track.display_name} · {track.tempo:.1f} BPM · {track.energy:.1f}/10"
            )
        else:
            self.summary_label.setText(self._summary_text)

    def apply_theme(self, bg: str, axis: str, title: str, accent: str):
        self.tempo_chart.set_theme(bg, axis, title)
        self.tempo_chart.set_color(accent)
        self.energy_chart.set_theme(bg, axis, title)
        energy_color = QColor(accent)
        energy_color = energy_color.darker(135) if QColor(bg).lightness() > 140 else energy_color.lighter(125)
        self.energy_chart.set_color(energy_color.name())
        self.genre_chart.set_theme(bg, title, accent)
        self.summary_label.setStyleSheet(f"color: {accent}; font-weight: bold;")
