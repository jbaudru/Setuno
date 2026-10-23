"""Non-modal waveform preview, shown when a track is clicked in the Library or Playlist Builder."""

import numpy as np
from PySide6.QtCore import QObject, QRectF, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..core.analyzer import compute_waveform_and_grid
from ..core.models import CUE_COLORS, CUE_LABELS, HotCue
from .icon_loader import icon
from .theme import PALETTES

MINI_WAVEFORM_POINTS = 96


class _WaveformAppearance(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.enabled = False
        self.palette = PALETTES["light"]
        self.rgb = self._palette_rgb()

    def _palette_rgb(self):
        return [QColor(self.palette[key]).getRgb()[:3]
                for key in ("wave_bass", "wave_mid", "wave_high")]

    def configure(self, theme: str, enabled: bool):
        self.enabled = enabled
        self.palette = PALETTES[theme]
        self.rgb = self._palette_rgb()
        self.changed.emit()


waveform_appearance = _WaveformAppearance()


def reduce_bands(bands, points: int = MINI_WAVEFORM_POINTS) -> tuple:
    if not bands:
        return ()
    values = np.asarray(bands, dtype=float)
    if len(values) > points:
        bounds = np.linspace(0, len(values), points + 1, dtype=int)
        values = np.add.reduceat(values, bounds[:-1], axis=0) / np.diff(bounds)[:, None]
    return tuple(tuple(float(v) for v in row) for row in values)


def _band_color(bands) -> QColor:
    weights = [max(0.0, value) ** 2 for value in bands]
    total = sum(weights)
    if total <= 0:
        return QColor(waveform_appearance.palette["accent_strong"])
    rgb = [
        round(sum(weight * color[channel] for weight, color in zip(weights, waveform_appearance.rgb)) / total)
        for channel in range(3)
    ]
    return QColor(*rgb)

def _paint_cues(painter, cues, width, height, start_time, end_time, mini=False):
    if end_time <= start_time:
        return
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, False)
    font = QFont(painter.font())
    font.setPixelSize(9 if mini else 11)
    font.setBold(True)
    painter.setFont(font)
    occupied = [[], []]
    for slot, time in cues:
        if not start_time <= time <= end_time:
            continue
        x = min(width - 1, max(0.0, (time - start_time) / (end_time - start_time) * width))
        color = QColor(CUE_COLORS[slot])
        painter.setPen(QPen(color, 1 if mini else 2))
        painter.drawLine(int(x), 0, int(x), int(height))
        label_height = 11 if mini else 15
        label_x = min(x, max(0, width - 13))
        lane = slot % 2
        for candidate_lane in (lane, 1 - lane):
            candidate_x = label_x
            for left, right in sorted(occupied[candidate_lane]):
                if candidate_x < right and candidate_x + 13 > left:
                    candidate_x = right + 1
            if candidate_x + 13 <= width:
                lane, label_x = candidate_lane, candidate_x
                break
        occupied[lane].append((label_x, label_x + 13))
        box = QRectF(label_x, lane * label_height, 13, label_height)
        painter.fillRect(box, color)
        painter.setPen(QColor("#101018"))
        painter.drawText(box, Qt.AlignCenter, CUE_LABELS[slot])
    painter.restore()


def _paint_bands(painter, values, bands, first_x, last_x, center, height, start=0.0, end=1.0):
    """Keep the main silhouette theme-accented, with a smaller frequency-colored core."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, False)
    n = len(values)
    step = (last_x - first_x) / max(1, n - 1)
    accent = QColor(waveform_appearance.palette["accent"])
    for i, value in enumerate(values):
        position = start + (end - start) * i / max(1, n - 1)
        index = min(len(bands) - 1, max(0, round(position * (len(bands) - 1))))
        color = _band_color(bands[index])
        amplitude = value * height * 0.92
        painter.fillRect(
            QRectF(first_x + i * step, center - amplitude, max(1.0, step + 0.5), amplitude * 2),
            accent,
        )
        painter.fillRect(
            QRectF(first_x + i * step, center - amplitude * 0.35,
                   max(1.0, step + 0.5), amplitude * 0.7),
            color,
        )
    painter.restore()


def reduce_peaks(peaks, points: int = MINI_WAVEFORM_POINTS) -> tuple:
    """Max-preserving low-resolution copy of a waveform, normalized for mini previews."""
    if not peaks:
        return ()
    values = np.clip(np.asarray(peaks, dtype=float), 0.0, 1.0)
    if len(values) > points:
        bounds = np.linspace(0, len(values), points + 1, dtype=int)
        values = np.maximum.reduceat(values, bounds[:-1])
    peak = float(values.max())
    if peak > 0:
        values = np.minimum(1.0, values * (0.92 / peak))
    return tuple(round(float(v), 3) for v in values)


def _paint_mini(painter: QPainter, values, width: float, height: float, bass: QColor, treble: QColor, bg: QColor, bands=()):
    if not values:
        return
    center = height / 2.0
    if waveform_appearance.enabled and bands:
        _paint_bands(painter, values, bands, 0, width, center, height * 0.43)
        return
    low = [
        sum(values[max(0, i - 2):i + 3]) / (min(len(values), i + 3) - max(0, i - 2))
        for i in range(len(values))
    ]
    MiniWaveform._draw_waveform(painter, values, bass, width, height * 0.43, center, top_scale=0.92)
    MiniWaveform._draw_waveform(painter, low, bass, width, height * 0.43, center, top_scale=0.68)
    center_color = QColor(bg)
    center_color.setAlpha(45)
    painter.setPen(center_color)
    painter.drawLine(0, int(center), int(width), int(center))


class MiniWaveformDelegate(QStyledItemDelegate):
    """Paints cached low-res waveforms in a table column (no per-row widgets)."""

    PEAKS_ROLE = Qt.UserRole + 1
    BANDS_ROLE = Qt.UserRole + 2
    CUES_ROLE = Qt.UserRole + 3
    DURATION_ROLE = Qt.UserRole + 4
    _MAX_CACHE = 5000

    def __init__(self, bass_color: str, treble_color: str, bg_color: str, parent=None):
        super().__init__(parent)
        self._cache: dict[tuple, QPixmap] = {}
        self.set_theme(bass_color, treble_color, bg_color)
        waveform_appearance.changed.connect(self._appearance_changed)

    def _appearance_changed(self):
        self._cache.clear()
        parent = self.parent()
        if parent is not None:
            parent.viewport().update()

    def set_theme(self, bass_color: str, treble_color: str, bg_color: str):
        self.bass_color = QColor(bass_color)
        self.treble_color = QColor(treble_color)
        self.bg_color = QColor(bg_color)
        self._cache.clear()

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        values = index.data(self.PEAKS_ROLE)
        bands = index.data(self.BANDS_ROLE) or ()
        cues = index.data(self.CUES_ROLE) or ()
        duration = index.data(self.DURATION_ROLE) or 0
        if not values and not cues:
            return
        rect = option.rect.adjusted(3, 2, -3, -2)
        height = min(26, rect.height())
        if rect.width() < 4 or height < 4:
            return
        ratio = painter.device().devicePixelRatioF() if painter.device() else 1.0
        key = (values, bands, cues, duration, rect.width(), height, ratio)
        pixmap = self._cache.get(key)
        if pixmap is None:
            if len(self._cache) >= self._MAX_CACHE:
                self._cache.clear()
            pixmap = QPixmap(int(rect.width() * ratio), int(height * ratio))
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.transparent)
            pix_painter = QPainter(pixmap)
            pix_painter.setRenderHint(QPainter.Antialiasing)
            _paint_mini(pix_painter, values, rect.width(), height, self.bass_color, self.treble_color, self.bg_color, bands)
            _paint_cues(pix_painter, cues, rect.width(), height, 0, duration, mini=True)
            pix_painter.end()
            self._cache[key] = pixmap
        painter.drawPixmap(rect.x(), rect.y() + (rect.height() - height) // 2, pixmap)


class MiniWaveform(QWidget):
    """Small clickable waveform preview, used as a list-row cell widget."""

    def __init__(
        self,
        peaks: list,
        bass_color: str,
        treble_color: str,
        bg_color: str,
        on_clicked=None,
        parent=None,
        bands=(),
        cues=(),
        duration=0.0,
    ):
        super().__init__(parent)

        self.peaks = reduce_peaks(peaks)
        self.bands = reduce_bands(bands)
        self.cues = tuple((cue["slot"], cue["time"]) for cue in cues)
        self.duration = duration
        waveform_appearance.changed.connect(self.update)

        self.bass_color = QColor(bass_color)
        self.treble_color = QColor(treble_color)
        self.bg_color = QColor(bg_color)

        self.on_clicked = on_clicked

        self.setMinimumWidth(50)
        self.setFixedHeight(26)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to open the full waveform")

    def set_cues(self, cues: list[HotCue]):
        self.cues = tuple((cue["slot"], cue["time"]) for cue in cues)
        self.update()

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

        _paint_mini(
            painter, self.peaks, float(self.width()), float(self.height()),
            self.bass_color, self.treble_color, self.bg_color, self.bands,
        )
        _paint_cues(painter, self.cues, self.width(), self.height(), 0, self.duration, mini=True)
        painter.end()

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and self.on_clicked
        ):
            self.on_clicked()

        super().mousePressEvent(event)


class _WaveformWorker(QThread):
    done = Signal(str, list, object, object)
    bands_ready = Signal(str, list)

    def __init__(self, filepath: str, bpm: float | None, *, refine_tempo: bool = True):
        super().__init__()
        self.filepath = filepath
        self.bpm = bpm
        self.refine_tempo = refine_tempo

    def run(self):
        try:
            bands = []
            peaks, grid, detail = compute_waveform_and_grid(
                self.filepath, self.bpm, waveform_bands=bands, refine_tempo=self.refine_tempo,
            )
        except Exception:
            peaks, grid, detail = [], None, None
            bands = []

        self.done.emit(
            self.filepath,
            peaks,
            grid,
            detail,
        )
        self.bands_ready.emit(self.filepath, bands)


class _WaveformChart(QWidget):
    """DJ-style waveform with zoom, pan, timecode and BPM grid."""

    seek_requested = Signal(float)  # emitted with the clicked time (seconds)

    def __init__(self, bass_color: str, treble_color: str, bg_color: str, parent=None):
        super().__init__(parent)

        self.peaks = []
        self.bands = []
        self.cues = ()
        waveform_appearance.changed.connect(self.update)
        self.detail_peaks = None
        self._normalized = []
        self.bpm = None
        self.track_duration = None
        self.beat_offset = None
        self.playhead_time = None
        self._grid_offset = None
        self._grid_bpm = None
        self._bar_phase = 0
        self.show_bar_numbers = True
        self._precise_grid = None

        self.bass_color = QColor(bass_color)
        self.treble_color = QColor(treble_color)
        self.bg_color = QColor(bg_color)

        self.zoom_factor = 1.0
        self.pan_position = 0.0
        self._dragging = False
        self._drag_start_x = 0.0
        self._drag_start_pan = 0.0
        self._press_moved = False

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
        self.bands = []
        self.detail_peaks = None
        self._normalized = self._normalize(self.peaks)
        self.zoom_factor = 1.0
        self.pan_position = 0.0
        self._sync_grid()
        self.update()

    def set_bands(self, bands):
        self.bands = bands or []
        self.update()

    def set_cues(self, cues: list[HotCue]):
        self.cues = tuple((cue["slot"], cue["time"]) for cue in cues)
        self.update()

    def set_detail_peaks(self, detail):
        """High-resolution peaks (same 0-1 scale as `peaks`) used when zoomed in."""
        self.detail_peaks = detail if detail is not None and len(detail) > len(self.peaks) else None
        self.update()

    def set_bpm(self, bpm):
        try:
            bpm = float(bpm)
        except (TypeError, ValueError):
            bpm = None

        self.bpm = bpm if bpm and 0 < bpm <= 300 else None
        self._sync_grid()
        self.update()

    def set_duration(self, duration):
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = None

        self.track_duration = (
            duration if duration and duration > 0 else None
        )
        self._sync_grid()
        self.update()

    def set_beat_offset(self, offset):
        try:
            self.beat_offset = None if offset is None else float(offset)
        except (TypeError, ValueError):
            self.beat_offset = None
        self._sync_grid()
        self.update()

    def set_beat_grid(self, grid):
        """Use a full-resolution grid ({'bpm', 'offset'} on a downbeat), or None for the peak-based estimate."""
        self._precise_grid = grid
        self._sync_grid()
        self.update()

    def _sync_grid(self):
        """Lock the beat grid phase and bar downbeat onto the waveform peaks."""
        self._grid_offset = None
        self._grid_bpm = self.bpm
        self._bar_phase = 0

        if self._precise_grid and self.track_duration:
            self._grid_bpm = float(self._precise_grid["bpm"])
            self._grid_offset = float(self._precise_grid["offset"])
            return

        if len(self.peaks) < 16 or not self.bpm or not self.track_duration:
            return

        env = np.clip(np.asarray(self.peaks, dtype=float), 0.0, 1.0)
        onset = np.maximum(0.0, np.diff(env, prepend=env[0]))
        dt = self.track_duration / (len(env) - 1)
        period = 60.0 / self.bpm
        times = np.arange(len(env)) * dt

        def strength(phase, step=period):
            beats = np.arange(phase, self.track_duration, step)
            if len(beats) == 0:
                return 0.0
            return float(
                np.interp(beats, times, onset).sum()
                + 0.5 * np.interp(beats, times, env).sum()
            ) / len(beats)

        offset = self.beat_offset
        if offset is None:
            candidates = np.linspace(0.0, period, 64, endpoint=False)
            offset = float(candidates[int(np.argmax([strength(c) for c in candidates]))])
        else:
            offset = offset % period

        # The bar starts on whichever of the 4 beats carries the strongest peaks.
        bar_scores = [strength(offset + b * period, 4 * period) for b in range(4)]
        self._grid_offset = offset
        self._bar_phase = int(np.argmax(bar_scores))

    def set_playhead(self, seconds):
        """Show a playback-position marker, or hide it when `seconds` is None."""
        try:
            self.playhead_time = None if seconds is None else max(0.0, float(seconds))
        except (TypeError, ValueError):
            self.playhead_time = None
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

        if total < 2 or self.zoom_factor <= 1:
            return 0, total

        span = (total - 1) / self.zoom_factor
        start = self.pan_position * max(0.0, (total - 1) - span)
        return start, start + span + 1

    def _max_zoom(self):
        if self.detail_peaks is None or not self.track_duration:
            return 64.0
        # Allow zooming down to roughly a quarter second across the view.
        return max(64.0, min(4000.0, self.track_duration / 0.25))

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
        visible = max(1e-6, end - start - 1)

        return (
            (sample - start)
            / visible
            * self.width()
        )

    # ------------------------------------------------------------------
    # Grid
    # ------------------------------------------------------------------

    @staticmethod
    def _grid_stride(px_per_beat):
        stride = 1
        while px_per_beat * stride < 16:
            stride *= 2
        return stride

    def _draw_grid(self, painter):
        if (
            not self.peaks
            or not self._grid_bpm
            or self._grid_offset is None
            or not self.track_duration
            or self.track_duration <= 0
        ):
            return

        start, end = self._visible_range()

        if end <= start:
            return

        start_time = self._sample_to_time(start)
        end_time = self._sample_to_time(end - 1)

        beat_length = 60.0 / self._grid_bpm
        offset = self._grid_offset

        first = int(
            (start_time - offset)
            / beat_length
        ) - 1

        last = int(
            (end_time - offset)
            / beat_length
        ) + 1

        painter.save()

        font = QFont("Segoe UI")
        font.setPixelSize(9)
        painter.setFont(font)
        fm = QFontMetrics(font)

        light_background = self.bg_color.lightness() > 160
        grid_base = QColor("#1E3A8A") if light_background else QColor(self.treble_color)
        pill_bg = QColor(self.bg_color)
        pill_bg.setAlpha(200)

        # Pick a label stride (in beats, snapped to a power of two) so that
        # consecutive timecodes stay at least a label-width apart no matter
        # the zoom level: sparse when the whole track is visible, and
        # progressively denser (down to one label per beat) while zooming in.
        total_beats = max(1, last - first)
        px_per_beat = self.width() * beat_length / max(beat_length, end_time - start_time)
        grid_stride = self._grid_stride(px_per_beat)
        min_label_spacing = fm.horizontalAdvance("000 · 00:00" if self.show_bar_numbers else "00:00") + 14

        label_stride = 4
        while px_per_beat * label_stride < min_label_spacing and label_stride < total_beats:
            label_stride *= 2

        first_visible_beat = first + (self._bar_phase - first) % grid_stride
        for beat in range(first_visible_beat, last + 1, grid_stride):

            time = (
                offset
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

            bar_beat = beat - self._bar_phase
            is_bar = bar_beat % 4 == 0
            color = QColor(grid_base)
            color.setAlpha(
                (200 if is_bar else 90) if light_background else (150 if is_bar else 55)
            )

            painter.setPen(QPen(color, 1.4 if is_bar else 0.7))

            painter.drawLine(
                int(x),
                0,
                int(x),
                self.height(),
            )

            # Timecode density adapts to the current zoom (see label_stride above).
            bar_number = bar_beat // 4 + 1
            if is_bar and bar_beat % label_stride == 0 and bar_number >= 1:
                minutes = int(time // 60)
                seconds = int(time % 60)

                label = self._grid_label(bar_number, minutes, seconds)

                rect = QRectF(
                    int(x) + 3,
                    3,
                    fm.horizontalAdvance(label) + 8,
                    fm.height() + 2,
                )
                painter.setPen(Qt.NoPen)
                painter.setBrush(pill_bg)
                painter.drawRoundedRect(rect, 3, 3)

                text_color = QColor(grid_base)
                text_color.setAlpha(230 if light_background else 190)
                painter.setPen(text_color)
                painter.drawText(rect, Qt.AlignCenter, label)
        painter.restore()

    # ------------------------------------------------------------------
    # Waveform
    # ------------------------------------------------------------------

    def _grid_label(self, bar_number, minutes, seconds):
        timecode = f"{minutes:02d}:{seconds:02d}"
        return f"{bar_number} · {timecode}" if self.show_bar_numbers else timecode

    def _draw_waveform(self, painter):
        start, end = self._visible_range()

        if end - start < 2:
            return

        # More detail as zoom increases (and a higher baseline than before
        # so the fully-zoomed-out view keeps sharp peaks too).
        target = max(400, self.width() * 2)
        width = self.width()

        if self.detail_peaks is not None and (end - start) < width and self.track_duration:
            scale = 0.92 / (max(self.peaks) or 1.0)
            n_detail = len(self.detail_peaks)
            t0, t1 = self._sample_to_time(start), self._sample_to_time(end - 1)
            d0 = max(0, int(t0 / self.track_duration * n_detail) - 1)
            d1 = min(n_detail, int(t1 / self.track_duration * n_detail) + 2)
            values = np.minimum(1.0, self.detail_peaks[d0:d1] * scale).tolist()
            sample_per_detail = self.track_duration / n_detail
            first_x = self._sample_to_x(self._time_to_sample(d0 * sample_per_detail), start, end)
            last_x = self._sample_to_x(self._time_to_sample((d1 - 1) * sample_per_detail), start, end)
        else:
            i0, i1 = int(start), min(len(self.peaks), int(np.ceil(end)))
            values = self._normalized[i0:i1]
            first_x = self._sample_to_x(i0, start, end)
            last_x = self._sample_to_x(i1 - 1, start, end)

        if len(values) < 2:
            return

        values = self._resample(
            values,
            target,
        )
        if waveform_appearance.enabled and self.bands:
            total = max(1, len(self.peaks) - 1)
            color_start, color_end = start / total, min(1.0, (end - 1) / total)
            if self.detail_peaks is not None and (end - start) < width and self.track_duration:
                color_start = d0 / max(1, n_detail - 1)
                color_end = (d1 - 1) / max(1, n_detail - 1)
            _paint_bands(
                painter, values, self.bands, first_x, last_x,
                self.height() / 2, self.height() * 0.43,
                color_start, color_end,
            )
            return

        low = self._smooth(values, 2)

        n = len(values)
        if n < 2:
            return

        center = self.height() / 2
        height = self.height() * 0.43
        step = (last_x - first_x) / (n - 1)

        def make_path(data, scale):
            path = QPainterPath()

            for i, value in enumerate(data):
                x = first_x + i * step
                y = center - value * scale * height

                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)

            for i in range(n - 1, -1, -1):
                x = first_x + i * step
                y = center + data[i] * scale * height
                path.lineTo(x, y)

            path.closeSubpath()
            return path

        painter.setPen(Qt.NoPen)

        painter.setBrush(self.bass_color)
        painter.drawPath(
            make_path(values, 0.92)
        )

        painter.setBrush(self.bass_color)
        painter.drawPath(
            make_path(low, 0.68)
        )

        # Fine upper waveform detail.
        highlight = QColor(self.bass_color)
        highlight.setAlpha(55)

        path = QPainterPath()

        for i, value in enumerate(values):
            x = first_x + i * step
            y = (
                center
                - value
                * 0.92
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

    def _draw_playhead(self, painter):
        """Draw a marker at the current playback position, if visible."""
        if (
            self.playhead_time is None
            or not self.peaks
            or not self.track_duration
        ):
            return

        start, end = self._visible_range()

        if end <= start:
            return

        start_time = self._sample_to_time(start)
        end_time = self._sample_to_time(end - 1)

        if self.playhead_time < start_time or self.playhead_time > end_time:
            return

        sample = self._time_to_sample(self.playhead_time)
        x = self._sample_to_x(sample, start, end)

        marker_color = QColor(255, 255, 255, 235)

        pen = QPen(marker_color)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.drawLine(int(x), 0, int(x), self.height())

        painter.setPen(Qt.NoPen)
        painter.setBrush(marker_color)

        handle = QPainterPath()
        handle.moveTo(x - 5, 0)
        handle.lineTo(x + 5, 0)
        handle.lineTo(x, 7)
        handle.closeSubpath()
        painter.drawPath(handle)

    def _emit_seek_at(self, x):
        """Translate a click's x position into a track time and emit `seek_requested`."""
        start, end = self._visible_range()

        if end <= start:
            return

        visible = max(1e-6, end - start - 1)
        sample = start + (x / max(1, self.width())) * visible
        time = self._sample_to_time(sample)

        if self.track_duration:
            time = max(0.0, min(self.track_duration, time))

        self.seek_requested.emit(time)

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
                _paint_cues(painter, self.cues, self.width(), self.height(),
                            0, self.track_duration or 0)
                return

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

            self._draw_grid(painter)

            self._draw_playhead(
                painter
            )
            start, end = self._visible_range()
            _paint_cues(
                painter, self.cues, self.width(), self.height(),
                self._sample_to_time(start), self._sample_to_time(end - 1),
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
            min(self._max_zoom(), self.zoom_factor),
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
            and self.peaks
        ):
            self._press_moved = False

            if self.zoom_factor > 1.0:
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

            if abs(dx) > 3:
                self._press_moved = True

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
            and (self._dragging or self.peaks)
        ):
            was_click = not self._press_moved

            self._dragging = False

            self.setCursor(
                Qt.OpenHandCursor
                if self.zoom_factor > 1.0
                else Qt.ArrowCursor
            )

            if was_click and self.peaks and self.track_duration:
                self._emit_seek_at(
                    event.position().x()
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
    """Reusable non-modal waveform preview.

    Clicking inside the chart seeks and plays a preview using its own
    QMediaPlayer, independent from (and able to overlap with) the app's
    main player.
    """

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
        self._grid_cache: dict[tuple, dict] = {}

        self.preview_player = QMediaPlayer(self)
        self.preview_audio = QAudioOutput(self)
        self.preview_player.setAudioOutput(self.preview_audio)
        self.preview_audio.setVolume(0.8)
        self.preview_player.positionChanged.connect(self._on_preview_position)
        self.preview_player.playbackStateChanged.connect(self._on_preview_state)

        self.title_label = QLabel("")
        self.title_label.setStyleSheet(
            "font-weight: bold;"
        )

        self.preview_play_btn = QPushButton()
        self.preview_play_btn.setIcon(icon("play"))
        self.preview_play_btn.setFlat(True)
        self.preview_play_btn.setFixedSize(28, 28)
        self.preview_play_btn.setToolTip("Play/pause this preview (click the waveform to seek)")
        self.preview_play_btn.clicked.connect(self._toggle_preview)

        self.chart = _WaveformChart(
            bg_color=bg_color,
            bass_color=bass_color,
            treble_color=treble_color,
        )
        self.chart.seek_requested.connect(self._seek_preview)

        header = QHBoxLayout()
        header.addWidget(self.preview_play_btn)
        header.addWidget(self.title_label, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(header)
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
        self.preview_player.stop()
        self.chart.set_playhead(None)
        self._current_filepath = track.filepath

        self.setWindowTitle(
            f"Waveform - {track.display_name}"
        )

        self.title_label.setText(
            track.display_name
        )

        bpm = self._get_track_bpm(track)
        duration = self._get_track_duration(track)
        grid_key = (track.filepath, bpm, not track.tempo_manual)

        self.chart.set_beat_grid(self._grid_cache.get(grid_key))
        self.chart.set_bpm(bpm)
        self.chart.set_duration(duration)

        beat_offset = getattr(track, "beat_offset", None)

        self.chart.set_beat_offset(
            beat_offset
        )

        self.chart.set_peaks(getattr(track, "waveform_peaks", []))
        self.chart.set_bands(track.waveform_bands)
        self.chart.set_cues(track.hot_cues)

        worker = _WaveformWorker(
            track.filepath,
            bpm,
            refine_tempo=not track.tempo_manual,
        )

        worker.done.connect(
            self._on_peaks
        )
        worker.bands_ready.connect(self._on_bands)

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

    def _on_peaks(self, filepath, peaks, grid, detail=None):
        worker = self.sender()
        if grid and isinstance(worker, _WaveformWorker):
            self._grid_cache[(filepath, worker.bpm, worker.refine_tempo)] = grid
        if filepath == self._current_filepath:
            if len(self.chart.peaks) < 2400 and peaks:
                self.chart.set_peaks(peaks)
            self.chart.set_detail_peaks(detail)
            self.chart.set_beat_grid(grid)

    def _on_bands(self, filepath, bands):
        if filepath == self._current_filepath:
            self.chart.set_bands(bands)

    def _seek_preview(self, seconds: float):
        if not self._current_filepath:
            return
        source = self.preview_player.source()
        if source.isEmpty() or source.toLocalFile() != self._current_filepath:
            self.preview_player.setSource(QUrl.fromLocalFile(self._current_filepath))
        self.preview_player.setPosition(int(seconds * 1000))
        self.preview_player.play()

    def _toggle_preview(self):
        if not self._current_filepath:
            return
        if self.preview_player.playbackState() == QMediaPlayer.PlayingState:
            self.preview_player.pause()
        else:
            if self.preview_player.source().isEmpty():
                self.preview_player.setSource(QUrl.fromLocalFile(self._current_filepath))
            self.preview_player.play()

    def _on_preview_position(self, pos_ms: int):
        self.chart.set_playhead(pos_ms / 1000.0)

    def _on_preview_state(self, state):
        is_playing = state == QMediaPlayer.PlayingState
        self.preview_play_btn.setIcon(icon("pause") if is_playing else icon("play"))

    def closeEvent(self, event):
        self.preview_player.stop()
        super().closeEvent(event)

    def apply_theme(self, bg, color):
        is_playing = self.preview_player.playbackState() == QMediaPlayer.PlayingState
        self.preview_play_btn.setIcon(icon("pause") if is_playing else icon("play"))

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
