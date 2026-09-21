"""Statistics panel: tempo curve, energy curve and genre breakdown for a playlist.

Rendered with plain QPainter (no matplotlib/pandas/pillow chain) to keep the
packaged app small and portable.
"""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..core.playlist_engine import playlist_stats

GENRE_COLORS = [
    "#FF6B35",  # House              - orange
    "#C77DFF",  # Deep House         - purple
    "#F4A261",  # Tech House         - warm orange
    "#E9C46A",  # Progressive House  - gold
    "#E63946",  # Techno              - red
    "#6D597A",  # Minimal Techno      - muted violet
    "#9B2226",  # Hard Techno         - dark red
    "#00B4D8",  # Trance              - cyan
    "#0077B6",  # Drum & Bass         - blue
    "#2A9D8F",  # UK Garage           - teal
    "#52B788",  # Breakbeat           - green
    "#8338EC",  # Dubstep             - electric purple
    "#3A86FF",  # Hip-Hop / R&B       - blue
    "#8D99AE",  # Downtempo           - grey-blue
    "#90BE6D",  # Ambient             - soft green
    "#FF4D6D",  # Pop                 - pink
    "#577590",  # Rock                - slate blue
]

Y_TICKS = 4


class _CurveChart(QWidget):
    """Simple line/bar chart for a single numeric series, with axis ticks and value labels."""

    def __init__(self, title: str, color: str, kind: str = "line", x_label: str = "", y_label: str = "", parent=None):
        super().__init__(parent)
        self.title = title
        self.color = QColor(color)
        self.kind = kind
        self.x_label = x_label
        self.y_label = y_label
        self.values: list[float] = []
        self.bg_color = QColor("#33334d")
        self.axis_color = QColor("#8686AC")
        self.title_color = QColor("#ffffff")
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_values(self, values: list[float]):
        self.values = values
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


class _GenrePie(QWidget):
    """Simple genre-breakdown pie chart with a color-coded legend."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.genres: dict[str, int] = {}
        self.bg_color = QColor("#33334d")
        self.title_color = QColor("#ffffff")
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_genres(self, genres: dict[str, int]):
        self.genres = genres
        self.update()

    def set_theme(self, bg: str, title: str):
        self.bg_color = QColor(bg)
        self.title_color = QColor(title)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self.bg_color)
        painter.setPen(QPen(self.title_color))
        painter.drawText(QRectF(0, 2, self.width(), 18), Qt.AlignHCenter, "Genres")

        total = sum(self.genres.values())
        if total <= 0:
            return

        legend_h = 16 * len(self.genres) + 8
        side = min(self.width(), self.height() - 24 - legend_h) - 10
        side = max(30, side)
        rect = QRectF((self.width() - side) / 2, 24, side, side)

        painter.setPen(Qt.NoPen)
        start_angle = 90 * 16
        colors = []
        for i, (name, count) in enumerate(self.genres.items()):
            span = -int(360 * 16 * (count / total))
            color = QColor(GENRE_COLORS[i % len(GENRE_COLORS)])
            colors.append(color)
            painter.setBrush(color)
            painter.drawPie(rect, start_angle, span)
            start_angle += span

        legend_y = rect.bottom() + 6
        for (name, count), color in zip(self.genres.items(), colors):
            pct = 100 * count / total
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawRect(QRectF(8, legend_y + 3, 10, 10))
            painter.setPen(QPen(self.title_color))
            painter.drawText(
                QRectF(22, legend_y, self.width() - 26, 16),
                Qt.AlignVCenter, f"{name} ({count}, {pct:.0f}%)",
            )
            legend_y += 16


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

        charts = QHBoxLayout()
        charts.addWidget(self.tempo_chart)
        charts.addWidget(self.energy_chart)
        charts.addWidget(self.genre_chart)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary_label)
        layout.addLayout(charts)

    def update_stats(self, tracks):
        stats = playlist_stats(tracks)
        if stats["count"] == 0:
            self.summary_label.setText("No playlist generated yet.")
            self.tempo_chart.set_values([])
            self.energy_chart.set_values([])
            self.genre_chart.set_genres({})
            return

        total_min = stats["total_duration"] / 60
        self.summary_label.setText(
            f"{stats['count']} tracks · {total_min:.1f} min · "
            f"avg tempo {stats['avg_tempo']} BPM · avg energy {stats['avg_energy']}/10"
        )
        self.tempo_chart.set_values([t.tempo for t in tracks])
        self.energy_chart.set_values([t.energy for t in tracks])
        self.genre_chart.set_genres(stats["genres"])

    def apply_theme(self, bg: str, axis: str, title: str, accent: str):
        self.tempo_chart.set_theme(bg, axis, title)
        self.energy_chart.set_theme(bg, axis, title)
        self.genre_chart.set_theme(bg, title)
        self.summary_label.setStyleSheet(f"color: {accent}; font-weight: bold;")
