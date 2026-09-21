"""Non-modal waveform preview, shown when a track is clicked in the Library or Playlist Builder."""

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import  QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.analyzer import compute_waveform_peaks
from ..core.models import Track


class MiniWaveform(QWidget):
    """Small clickable waveform preview, used as a list-row cell widget."""

    def __init__(
        self,
        low: list,
        high: list,
        bass_color: str,
        treble_color: str,
        bg_color: str,
        on_clicked=None,
        parent=None,
    ):
        super().__init__(parent)

        self.low = low or []
        self.high = high or []

        self.bass_color = QColor(bass_color)
        self.treble_color = QColor(treble_color)
        self.bg_color = QColor(bg_color)

        self.on_clicked = on_clicked

        self.setMinimumWidth(50)
        self.setFixedHeight(26)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to open the full waveform")

    def set_theme(
        self,
        bass_color: str,
        treble_color: str,
        bg_color: str,
    ):
        self.bass_color = QColor(bass_color)
        self.treble_color = QColor(treble_color)
        self.bg_color = QColor(bg_color)
        self.update()

    @staticmethod
    def _normalize(values: list) -> list:
        """Normalize waveform values while preserving their relative dynamics."""

        if not values:
            return []

        cleaned = [
            max(0.0, min(1.0, float(value or 0.0)))
            for value in values
        ]

        peak = max(cleaned, default=0.0)

        if peak <= 0.0:
            return cleaned

        target_peak = 0.92
        scale = target_peak / peak

        return [
            min(1.0, value * scale)
            for value in cleaned
        ]

    @staticmethod
    def _draw_waveform(
        painter: QPainter,
        values: list,
        color: QColor,
        width: float,
        height: float,
        center: float,
        top_scale: float = 0.45,
    ):
        """Draw a smooth mirrored waveform from amplitude samples."""

        if not values:
            return

        n = len(values)

        if n == 1:
            x_step = width
        else:
            x_step = width / (n - 1)

        path_top = QPainterPath()
        path_bottom = QPainterPath()

        for i, value in enumerate(values):
            amplitude = max(0.0, min(1.0, value))
            amplitude *= top_scale

            y_top = center - amplitude * height
            y_bottom = center + amplitude * height
            x = i * x_step

            if i == 0:
                path_top.moveTo(x, y_top)
                path_bottom.moveTo(x, y_bottom)
            else:
                path_top.lineTo(x, y_top)
                path_bottom.lineTo(x, y_bottom)

        path = QPainterPath(path_top)

        for i in range(n - 1, -1, -1):
            value = max(
                0.0,
                min(1.0, values[i]),
            )

            amplitude = value * top_scale

            x = i * x_step
            y_bottom = center + amplitude * height

            path.lineTo(x, y_bottom)

        path.closeSubpath()

        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPath(path)

    def paintEvent(self, _event):
        painter = QPainter(self)

        painter.setRenderHint(
            QPainter.Antialiasing
        )

        painter.setRenderHint(
            QPainter.SmoothPixmapTransform
        )

        if not self.low and not self.high:
            return

        w = float(self.width())
        h = float(self.height())
        center = h / 2.0

        low = self._normalize(self.low)
        high = self._normalize(self.high)

        self._draw_waveform(
            painter,
            low,
            self.bass_color,
            w,
            h * 0.43,
            center,
            top_scale=0.92,
        )

        self._draw_waveform(
            painter,
            high,
            self.treble_color,
            w,
            h * 0.43,
            center,
            top_scale=0.72,
        )

        center_color = QColor(self.bg_color)
        center_color.setAlpha(45)

        painter.setPen(center_color)

        painter.drawLine(
            0,
            int(center),
            int(w),
            int(center),
        )

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and self.on_clicked
        ):
            self.on_clicked()

        super().mousePressEvent(event)


class _WaveformWorker(QThread):
    done = Signal(str, list)

    def __init__(self, filepath: str):
        super().__init__()
        self.filepath = filepath

    def run(self):
        try:
            peaks = compute_waveform_peaks(
                self.filepath
            )
        except Exception:
            peaks = []

        self.done.emit(
            self.filepath,
            peaks,
        )


class _WaveformChart(QWidget):
    """DJ-style waveform with zoom, pan, timecode and BPM grid."""

    def __init__(self, bass_color: str, treble_color: str, bg_color: str, parent=None):
        super().__init__(parent)

        self.peaks = []
        self.bpm = None
        self.track_duration = None
        self.beat_offset = 0.0

        self.bass_color = QColor(bass_color)
        self.treble_color = QColor(treble_color)
        self.bg_color = QColor(bg_color)

        self.zoom_factor = 1.0
        self.pan_position = 0.0
        self._dragging = False
        self._drag_start_x = 0.0
        self._drag_start_pan = 0.0

        self.setMinimumHeight(160)
        self.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding,
        )
        self.setMouseTracking(True)
        self.setToolTip(
            "Mouse wheel: zoom\n"
            "Drag: pan\n"
            "Double-click: reset"
        )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_peaks(self, peaks):
        self.peaks = peaks or []
        self.zoom_factor = 1.0
        self.pan_position = 0.0
        self.update()

    def set_bpm(self, bpm):
        try:
            bpm = float(bpm)
        except (TypeError, ValueError):
            bpm = None

        self.bpm = bpm if bpm and 0 < bpm <= 300 else None
        self.update()

    def set_duration(self, duration):
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = None

        self.track_duration = (
            duration if duration and duration > 0 else None
        )
        self.update()

    def set_beat_offset(self, offset):
        try:
            self.beat_offset = float(offset)
        except (TypeError, ValueError):
            self.beat_offset = 0.0
        self.update()

    def set_theme(self, bg, bass_color, treble_color):
        self.bg_color = QColor(bg)
        self.bass_color = QColor(bass_color)
        self.treble_color = QColor(treble_color)
        self.update()

    # ------------------------------------------------------------------
    # Waveform helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(values):
        if not values:
            return []

        values = [
            max(0.0, min(1.0, float(v)))
            for v in values
        ]

        peak = max(values, default=0.0)
        if peak <= 0:
            return values

        scale = 0.92 / peak
        return [min(1.0, v * scale) for v in values]

    @staticmethod
    def _smooth(values, radius=1):
        if len(values) < 3 or radius <= 0:
            return values

        result = []

        for i in range(len(values)):
            a = max(0, i - radius)
            b = min(len(values), i + radius + 1)
            result.append(
                sum(values[a:b]) / (b - a)
            )

        return result

    @staticmethod
    def _resample(values, target):
        """Maximum-preserving downsampling."""
        if not values or len(values) <= target:
            return values

        step = len(values) / target
        result = []

        for i in range(target):
            a = int(i * step)
            b = max(a + 1, int((i + 1) * step))
            result.append(
                max(values[a:min(b, len(values))])
            )

        return result

    def _split(self, values):
        smooth = self._smooth(values, 1)
        low = []
        high = []

        for raw, smoothed in zip(values, smooth):
            detail = abs(raw - smoothed)

            low.append(smoothed)
            high.append(
                max(
                    0.0,
                    min(
                        1.0,
                        smoothed * 0.48 + detail * 2.6,
                    ),
                )
            )

        return low, high

    # ------------------------------------------------------------------
    # Coordinate conversion
    # ------------------------------------------------------------------

    def _visible_range(self):
        total = len(self.peaks)

        if total < 2:
            return 0, total

        if self.zoom_factor <= 1:
            return 0, total

        count = max(
            2,
            int(total / self.zoom_factor),
        )

        max_start = max(0, total - count)

        start = int(
            self.pan_position * max_start
        )

        return start, min(total, start + count)

    def _sample_to_time(self, sample):
        total = len(self.peaks)

        if total < 2 or not self.track_duration:
            return 0.0

        return (
            sample
            / (total - 1)
            * self.track_duration
        )

    def _time_to_sample(self, seconds):
        total = len(self.peaks)

        if total < 2 or not self.track_duration:
            return 0.0

        return (
            seconds
            / self.track_duration
            * (total - 1)
        )

    def _sample_to_x(
        self,
        sample,
        start,
        end,
    ):
        visible = max(1, end - start - 1)

        return (
            (sample - start)
            / visible
            * self.width()
        )

    # ------------------------------------------------------------------
    # Grid
    # ------------------------------------------------------------------

    def _draw_grid(self, painter):
        if (
            not self.peaks
            or not self.bpm
            or not self.track_duration
            or self.track_duration <= 0
        ):
            return

        start, end = self._visible_range()

        if end <= start:
            return

        start_time = self._sample_to_time(start)
        end_time = self._sample_to_time(end - 1)

        beat_length = 60.0 / self.bpm

        first = int(
            (start_time - self.beat_offset)
            / beat_length
        ) - 1

        last = int(
            (end_time - self.beat_offset)
            / beat_length
        ) + 1

        painter.save()

        font = QFont("Segoe UI", 8)
        painter.setFont(font)

        current_beat = 0
        for beat in range(first, last + 1):
            
            time = (
                self.beat_offset
                + beat * beat_length
            )

            if time < start_time or time > end_time:
                continue

            sample = self._time_to_sample(time)
            x = self._sample_to_x(
                sample,
                start,
                end,
            )

            is_bar = beat % 4 == 0

            color = QColor(self.treble_color)
            color.setAlpha(
                80 if is_bar else 28
            )

            painter.setPen(color)

            painter.drawLine(
                int(x),
                0,
                int(x),
                self.height(),
            )

            # Time labels when zoomed or on bar lines.
            if self.zoom_factor >= 10.0 or current_beat % 32 == 0:
                minutes = int(time // 60)
                seconds = int(time % 60)

                label = f"{minutes:02d}:{seconds:02d}"

                text_color = QColor(
                    self.treble_color
                )
                text_color.setAlpha(
                    155 if is_bar else 90
                )

                painter.setPen(text_color)

                painter.drawText(
                    int(x) + 4,
                    14,
                    label,
                )
            current_beat += 1
        painter.restore()

    # ------------------------------------------------------------------
    # Waveform
    # ------------------------------------------------------------------

    def _draw_waveform(self, painter):
        start, end = self._visible_range()

        if end - start < 2:
            return

        values = self._normalize(
            self.peaks[start:end]
        )

        if len(values) < 2:
            return

        # More detail as zoom increases.
        target = max(
            256,
            int(
                self.width()
                * (
                    2.0
                    + min(
                        self.zoom_factor,
                        32.0,
                    ) * 0.75
                )
            ),
        )

        values = self._resample(
            values,
            target,
        )

        # Only smooth when zoomed out.
        if self.zoom_factor <= 1.5:
            values = self._smooth(
                values,
                1,
            )

        low, high = self._split(values)

        n = len(values)
        if n < 2:
            return

        center = self.height() / 2
        height = self.height() * 0.43
        step = self.width() / (n - 1)

        def make_path(data, scale):
            path = QPainterPath()

            for i, value in enumerate(data):
                x = i * step
                y = center - value * scale * height

                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)

            for i in range(n - 1, -1, -1):
                x = i * step
                y = center + data[i] * scale * height
                path.lineTo(x, y)

            path.closeSubpath()
            return path

        painter.setPen(Qt.NoPen)

        painter.setBrush(self.bass_color)
        painter.drawPath(
            make_path(low, 0.92)
        )

        painter.setBrush(self.treble_color)
        painter.drawPath(
            make_path(high, 0.72)
        )

        # Fine upper waveform detail.
        highlight = QColor(self.treble_color)
        highlight.setAlpha(55)

        path = QPainterPath()

        for i, value in enumerate(high):
            x = i * step
            y = (
                center
                - value
                * 0.72
                * height
                * 0.78
            )

            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        painter.setPen(highlight)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, _event):
        painter = QPainter(self)

        try:
            painter.setRenderHint(
                QPainter.Antialiasing
            )
            painter.setRenderHint(
                QPainter.SmoothPixmapTransform
            )

            painter.fillRect(
                self.rect(),
                self.bg_color,
            )

            if not self.peaks:
                return

            self._draw_grid(painter)

            # Center line.
            center_color = QColor(
                self.treble_color
            )
            center_color.setAlpha(40)

            painter.setPen(center_color)

            center = int(
                self.height() / 2
            )

            painter.drawLine(
                0,
                center,
                self.width(),
                center,
            )

            self._draw_waveform(
                painter
            )

        finally:
            painter.end()

    # ------------------------------------------------------------------
    # Zoom / pan
    # ------------------------------------------------------------------

    def wheelEvent(self, event):
        if not self.peaks:
            event.ignore()
            return

        delta = event.angleDelta().y()

        if not delta:
            event.ignore()
            return

        old_zoom = self.zoom_factor
        old_visible = 1.0 / old_zoom

        mouse_ratio = (
            event.position().x()
            / max(1, self.width())
        )

        if delta > 0:
            self.zoom_factor *= 1.25
        else:
            self.zoom_factor /= 1.25

        self.zoom_factor = max(
            1.0,
            min(64.0, self.zoom_factor),
        )

        if self.zoom_factor <= 1.001:
            self.zoom_factor = 1.0
            self.pan_position = 0.0

        else:
            new_visible = (
                1.0 / self.zoom_factor
            )

            old_start = (
                self.pan_position
                * (1.0 - old_visible)
            )

            position = (
                old_start
                + mouse_ratio * old_visible
            )

            new_start = (
                position
                - mouse_ratio * new_visible
            )

            max_start = (
                1.0 - new_visible
            )

            self.pan_position = (
                new_start / max_start
                if max_start > 0
                else 0.0
            )

            self.pan_position = max(
                0.0,
                min(
                    1.0,
                    self.pan_position,
                ),
            )

        self.setCursor(
            Qt.OpenHandCursor
        )
        self.update()
        event.accept()

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and self.zoom_factor > 1.0
        ):
            self._dragging = True
            self._drag_start_x = (
                event.position().x()
            )
            self._drag_start_pan = (
                self.pan_position
            )

            self.setCursor(
                Qt.ClosedHandCursor
            )

            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._dragging
            and self.zoom_factor > 1.0
            and self.width() > 0
        ):
            dx = (
                event.position().x()
                - self._drag_start_x
            )

            visible = (
                1.0 / self.zoom_factor
            )

            max_start = 1.0 - visible

            if max_start > 0:
                self.pan_position = max(
                    0.0,
                    min(
                        1.0,
                        self._drag_start_pan
                        - dx
                        / self.width()
                        * visible
                        / max_start,
                    ),
                )

                self.update()

            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and self._dragging
        ):
            self._dragging = False

            self.setCursor(
                Qt.OpenHandCursor
                if self.zoom_factor > 1.0
                else Qt.ArrowCursor
            )

            event.accept()
            return

        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.zoom_factor = 1.0
            self.pan_position = 0.0
            self.setCursor(Qt.ArrowCursor)
            self.update()
            event.accept()
            return

        super().mouseDoubleClickEvent(event)

    def enterEvent(self, event):
        if self.zoom_factor > 1.0:
            self.setCursor(
                Qt.OpenHandCursor
            )
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self._dragging:
            self.setCursor(
                Qt.ArrowCursor
            )
        super().leaveEvent(event)


class WaveformDialog(QDialog):
    """Reusable non-modal waveform preview."""

    def __init__(
        self,
        parent=None,
        bg_color=str,
        bass_color=str,
        treble_color=str,
    ):
        super().__init__(parent)

        self.setWindowTitle("Waveform")
        self.resize(900, 300)
        self.setMinimumSize(500, 220)

        self._current_filepath = ""
        self._active_workers = set()

        self.title_label = QLabel("")
        self.title_label.setStyleSheet(
            "font-weight: bold;"
        )

        self.chart = _WaveformChart(
            bg_color=bg_color,
            bass_color=bass_color,
            treble_color=treble_color,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self.title_label)
        layout.addWidget(self.chart)

    @staticmethod
    def _get_track_bpm(track):
        for attr in (
            "bpm",
            "tempo",
            "beats_per_minute",
        ):
            if not hasattr(track, attr):
                continue

            value = getattr(track, attr)

            if callable(value):
                try:
                    value = value()
                except Exception:
                    continue

            try:
                value = float(value)
            except (TypeError, ValueError):
                continue

            if 20.0 <= value <= 300.0:
                return value

        return None

    @staticmethod
    def _get_track_duration(track):
        for attr in (
            "duration",
            "duration_seconds",
            "length",
            "length_seconds",
        ):
            if not hasattr(track, attr):
                continue

            value = getattr(track, attr)

            if callable(value):
                try:
                    value = value()
                except Exception:
                    continue

            try:
                value = float(value)
            except (TypeError, ValueError):
                continue

            if value > 0:
                return value

        return None

    def load_track(self, track):
        self._current_filepath = track.filepath

        self.setWindowTitle(
            f"Waveform - {track.display_name}"
        )

        self.title_label.setText(
            track.display_name
        )

        bpm = self._get_track_bpm(track)
        duration = self._get_track_duration(track)

        self.chart.set_bpm(bpm)
        self.chart.set_duration(duration)

        # Replace with your actual beat-grid offset if available.
        beat_offset = getattr(
            track,
            "beat_offset",
            0.0,
        )

        self.chart.set_beat_offset(
            beat_offset
        )

        self.chart.set_peaks([])

        worker = _WaveformWorker(
            track.filepath
        )

        worker.done.connect(
            self._on_peaks
        )

        worker.finished.connect(
            lambda: self._active_workers.discard(
                worker
            )
        )

        self._active_workers.add(worker)
        worker.start()

        if not self.isVisible():
            self.show()

        self.raise_()
        self.activateWindow()

    def _on_peaks(self, filepath, peaks):
        if filepath == self._current_filepath:
            self.chart.set_peaks(peaks)

    def apply_theme(self, bg, color):
        base = QColor(color)

        treble = QColor(base)
        treble.setRed(
            min(255, int(base.red() * 1.25 + 20))
        )
        treble.setGreen(
            min(255, int(base.green() * 1.25 + 20))
        )
        treble.setBlue(
            min(255, int(base.blue() * 1.25 + 20))
        )

        self.chart.set_theme(
            bg,
            color,
            treble.name(),
        )
