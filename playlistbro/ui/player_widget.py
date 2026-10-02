"""Embedded audio player widget built on QtMultimedia."""
import math
from dataclasses import dataclass

import numpy as np

from PySide6.QtCore import QElapsedTimer, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtMultimedia import QAudioBufferOutput, QAudioFormat, QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QStyle, QStyleOptionSlider,
    QVBoxLayout, QWidget,
)

from ..core.models import Track
from .icon_loader import cover_pixmap, icon
from .library_view import heart_icon
from .waveform_view import _WaveformChart, _WaveformWorker

WAVEFORM_HEIGHT = 120
DEFAULT_TRANSITION_DURATION_MS = 16000
TRANSITION_DURATIONS_SECONDS = (8, 12, 16, 24, 32)
TEMPO_RAMP_FRACTION = 0.2


def _fmt_ms(ms: int) -> str:
    s = int(ms / 1000)
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}"


@dataclass(frozen=True)
class _TransitionPlan:
    outgoing_position_ms: int
    incoming_position_ms: int
    wait_ms: int
    playback_rate: float
    duration_ms: int


def _smoothstep(progress: float) -> float:
    progress = max(0.0, min(1.0, progress))
    return progress * progress * (3.0 - 2.0 * progress)


def _transition_gains(progress: float) -> tuple[float, float]:
    mix = _smoothstep(progress)
    angle = mix * math.pi / 2.0
    return math.cos(angle), math.sin(angle)


def _transition_playback_rate(progress: float, matched_rate: float) -> float:
    progress = max(0.0, min(1.0, progress))
    ramp = TEMPO_RAMP_FRACTION
    if progress < ramp:
        amount = _smoothstep(progress / ramp)
        return 1.0 + (matched_rate - 1.0) * amount
    if progress > 1.0 - ramp:
        amount = _smoothstep((progress - (1.0 - ramp)) / ramp)
        return matched_rate + (1.0 - matched_rate) * amount
    return matched_rate


def _track_grid(track, precise_grid=None):
    if precise_grid:
        try:
            bpm = float(precise_grid["bpm"])
            offset = float(precise_grid["offset"])
            if 20 <= bpm <= 300:
                return bpm, offset % (60.0 / bpm)
        except (KeyError, TypeError, ValueError):
            pass
    bpm = float(getattr(track, "tempo", 0.0) or 0.0)
    duration = float(getattr(track, "duration", 0.0) or 0.0)
    if not 20 <= bpm <= 300:
        return None
    period = 60.0 / bpm
    offset = getattr(track, "beat_offset", None)
    try:
        offset = float(offset) if offset is not None else None
    except (TypeError, ValueError):
        offset = None

    if offset is None:
        peaks = getattr(track, "waveform_peaks", None)
        if peaks is None or len(peaks) < 16:
            peaks = getattr(track, "waveform_low", None)
        if peaks is not None and len(peaks) >= 16 and duration > 0:
            envelope = np.clip(np.asarray(peaks, dtype=float), 0.0, 1.0)
            onset = np.maximum(0.0, np.diff(envelope, prepend=envelope[0]))
            times = np.linspace(0.0, duration, len(envelope))
            candidates = np.linspace(0.0, period, 64, endpoint=False)
            strengths = []
            for candidate in candidates:
                beats = np.arange(candidate, duration, period)
                strengths.append(
                    float(np.interp(beats, times, onset).mean()
                          + 0.5 * np.interp(beats, times, envelope).mean())
                    if len(beats) else 0.0
                )
            offset = float(candidates[int(np.argmax(strengths))])
        else:
            offset = 0.0
    return bpm, offset % period


def _next_beat(seconds: float, grid) -> float:
    bpm, offset = grid
    period = 60.0 / bpm
    beat_number = math.ceil((seconds - offset) / period - 1e-9)
    return max(0.0, offset + beat_number * period)


def _window_energy_db(track, start: float, duration: float):
    peaks = getattr(track, "waveform_peaks", None)
    if peaks is None or len(peaks) < 2:
        peaks = getattr(track, "waveform_low", None)
    track_duration = float(getattr(track, "duration", 0.0) or 0.0)
    if peaks is None or len(peaks) < 2 or track_duration <= 0 or duration <= 0:
        return None
    first = max(0, min(len(peaks) - 1, int(start / track_duration * len(peaks))))
    end_time = min(track_duration, start + duration)
    last = max(first + 1, min(len(peaks), math.ceil(end_time / track_duration * len(peaks))))
    envelope = np.asarray(peaks[first:last], dtype=float)
    rms = float(np.sqrt(np.mean(np.square(envelope))))
    loudness = float(getattr(track, "loudness", 0.0) or 0.0)
    return 20.0 * math.log10(max(rms, 1e-6)) + loudness


def _plan_auto_mix(outgoing, incoming, outgoing_position_ms: int,
                   duration_ms: int, energy_match: bool = False,
                   outgoing_grid=None, incoming_grid=None,
                   bpm_sync: bool = True) -> _TransitionPlan:
    if bpm_sync:
        outgoing_grid = outgoing_grid or _track_grid(outgoing)
        incoming_grid = incoming_grid or _track_grid(incoming)
    else:
        outgoing_grid = incoming_grid = None
    outgoing_position = max(0.0, outgoing_position_ms / 1000.0)
    aligned_outgoing = _next_beat(outgoing_position, outgoing_grid) if outgoing_grid else outgoing_position
    wait_ms = max(0, round((aligned_outgoing - outgoing_position) * 1000))
    incoming_position = _next_beat(0.0, incoming_grid) if incoming_grid else 0.0

    outgoing_duration = float(getattr(outgoing, "duration", 0.0) or 0.0)
    incoming_duration = float(getattr(incoming, "duration", 0.0) or 0.0)
    outgoing_bpm = outgoing_grid[0] if outgoing_grid else 0.0
    incoming_bpm = incoming_grid[0] if incoming_grid else 0.0
    playback_rate = max(0.8, min(1.25, outgoing_bpm / incoming_bpm)) if incoming_bpm else 1.0
    requested_seconds = duration_ms / 1000.0
    outgoing_window = min(requested_seconds, max(0.0, outgoing_duration - aligned_outgoing))
    incoming_window = min(requested_seconds * playback_rate, incoming_duration)
    if energy_match and outgoing_window > 0 and incoming_window > 0:
        outgoing_energy = _window_energy_db(outgoing, aligned_outgoing, outgoing_window)
        max_cue = min(incoming_duration - incoming_window, incoming_duration * 0.45)
        if incoming_grid:
            cue_step = 60.0 / incoming_grid[0]
            first_cue = _next_beat(0.0, incoming_grid)
        else:
            cue_step = 0.5
            first_cue = 0.0
        cues = np.arange(first_cue, max_cue + 1e-9, cue_step)
        if outgoing_energy is not None and len(cues):
            cue_energies = [
                _window_energy_db(incoming, float(cue), incoming_window) for cue in cues
            ]
            comparable = [
                (abs(energy - outgoing_energy), float(cue))
                for cue, energy in zip(cues, cue_energies) if energy is not None
            ]
            if comparable:
                incoming_position = min(comparable)[1]

    available_outgoing = max(0.0, outgoing_duration - aligned_outgoing)
    available_incoming = max(0.0, incoming_duration - incoming_position)
    effective_seconds = min(
        requested_seconds,
        available_outgoing,
        available_incoming / playback_rate if playback_rate > 0 else requested_seconds,
    )
    return _TransitionPlan(
        round(aligned_outgoing * 1000),
        round(incoming_position * 1000),
        wait_ms,
        playback_rate,
        max(250, round(effective_seconds * 1000)) if effective_seconds > 0 else duration_ms,
    )


class ClickableLabel(QLabel):
    """QLabel that emits `clicked` on a left-button press."""

    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class CoverArtDialog(QDialog):
    """Simple popup showing the current track's album art at a larger size."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.image_label)

    def show_cover(self, cover_path: str, title: str):
        self.setWindowTitle(title or "Album Cover")
        pixmap = cover_pixmap(cover_path, 400)
        self.image_label.setPixmap(pixmap)
        self.resize(pixmap.width(), pixmap.height())


class SeekSlider(QSlider):
    """QSlider that jumps straight to the clicked position instead of paging."""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.sliderPressed.emit()
            option = QStyleOptionSlider()
            self.initStyleOption(option)

            groove = self.style().subControlRect(
                QStyle.CC_Slider, option, QStyle.SC_SliderGroove, self,
            )
            handle = self.style().subControlRect(
                QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self,
            )

            if self.orientation() == Qt.Horizontal:
                pos = event.position().x() - handle.width() / 2
                span = groove.width() - handle.width()
                offset = groove.x()
            else:
                pos = event.position().y() - handle.height() / 2
                span = groove.height() - handle.height()
                offset = groove.y()

            value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), int(pos - offset), max(1, int(span)),
            )
            self.setValue(value)
            self.sliderMoved.emit(value)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class PlayerWidget(QWidget):
    track_finished = Signal()
    next_requested = Signal()
    prev_requested = Signal()
    crossfade_requested = Signal()
    track_transitioned = Signal(object)
    playing_changed = Signal(bool)
    audio_levels_changed = Signal(float, float)
    favorite_toggled = Signal(object)
    track_title_clicked = Signal(object)

    def __init__(self, parent=None, *, auto_mix: bool = True, bpm_sync: bool = True,
                 energy_match: bool = False,
                 transition_duration_ms: int = DEFAULT_TRANSITION_DURATION_MS):
        super().__init__(parent)
        self.current_track: Track | None = None
        self.display_track: Track | None = None
        self._full_title = "No track loaded"
        self.on_request_track = None  # called when Play is pressed with nothing loaded yet

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.8)
        self._transition_player = QMediaPlayer(self)
        self._transition_output = QAudioOutput(self)
        self._transition_player.setAudioOutput(self._transition_output)
        self._audio_buffer_output = QAudioBufferOutput(self)
        self._transition_buffer_output = QAudioBufferOutput(self)
        self.player.setAudioBufferOutput(self._audio_buffer_output)
        self._transition_player.setAudioBufferOutput(self._transition_buffer_output)
        self._deck_levels = {self.player: (0.0, 0.0), self._transition_player: (0.0, 0.0)}
        primary_player = self.player
        transition_player = self._transition_player
        self._audio_buffer_output.audioBufferReceived.connect(
            lambda buffer, player=primary_player: self._on_audio_buffer(player, buffer)
        )
        self._transition_buffer_output.audioBufferReceived.connect(
            lambda buffer, player=transition_player: self._on_audio_buffer(player, buffer)
        )
        self._transition_timer = QTimer(self)
        self._transition_timer.setInterval(20)
        self._transition_timer.timeout.connect(self._advance_crossfade)
        self._transition_start_timer = QTimer(self)
        self._transition_start_timer.setSingleShot(True)
        self._transition_start_timer.timeout.connect(self._begin_crossfade)
        self._transition_elapsed = QElapsedTimer()
        requested_duration = int(transition_duration_ms)
        if requested_duration not in {seconds * 1000 for seconds in TRANSITION_DURATIONS_SECONDS}:
            requested_duration = DEFAULT_TRANSITION_DURATION_MS
        self.transition_duration_ms = requested_duration
        self.bpm_sync = bool(bpm_sync)
        self.energy_match = bool(energy_match)
        self._transition_plan = None
        self._transition_pending_start = False
        self._transition_position_ready = False
        self._transition_track: Track | None = None
        self._crossfade_requested = False
        self._manual_seeking = False
        self._seek_guard_position = None

        self.title_label = ClickableLabel(self._full_title)
        self._title_accent = "#8686AC"
        self.title_label.setStyleSheet(f"font-size: 10px; color: {self._title_accent};")
        self.title_label.setFixedHeight(14)
        self.title_label.setCursor(Qt.PointingHandCursor)
        self.title_label.setToolTip("Click to find this track in the Library")
        self.title_label.clicked.connect(self._on_title_clicked)

        self.cover_label = ClickableLabel()
        self.cover_label.setFixedSize(40, 40)
        self.cover_label.setPixmap(cover_pixmap("", 40))
        self.cover_label.setCursor(Qt.PointingHandCursor)
        self.cover_label.setToolTip("Click to view larger cover")
        self.cover_label.clicked.connect(self._show_large_cover)
        self._cover_dialog: CoverArtDialog | None = None

        self.play_btn = QPushButton()
        self.play_btn.setIcon(icon("play"))
        self.play_btn.setFlat(True)
        self.play_btn.setFixedSize(28, 28)
        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(icon("stop"))
        self.stop_btn.setFlat(True)
        self.stop_btn.setFixedSize(28, 28)
        self.prev_btn = QPushButton()
        self.prev_btn.setIcon(icon("prev"))
        self.prev_btn.setFlat(True)
        self.prev_btn.setFixedSize(28, 28)
        self.next_btn = QPushButton()
        self.next_btn.setIcon(icon("next"))
        self.next_btn.setFlat(True)
        self.next_btn.setFixedSize(28, 28)
        self.like_btn = QPushButton()
        self.like_btn.setFlat(True)
        self.like_btn.setFixedSize(22, 22)
        self.like_btn.setIconSize(QSize(16, 16))
        self.like_btn.setCursor(Qt.PointingHandCursor)
        self.like_btn.setStyleSheet("QPushButton { border: none; background: transparent; padding: 0; }")
        self.like_btn.setEnabled(False)
        self.like_btn.clicked.connect(self._toggle_favorite)
        self._update_like_button()

        self.position_slider = SeekSlider(Qt.Horizontal)
        self.position_slider.setObjectName("playerProgressSlider")
        self.position_slider.setProperty("transitionBlink", "off")
        self.position_slider.setRange(0, 0)
        self._progress_blink_timer = QTimer(self)
        self._progress_blink_timer.setInterval(320)
        self._progress_blink_timer.timeout.connect(self._toggle_progress_blink)
        self._progress_blink_on = False

        self.time_label = QLabel("0:00 / 0:00")

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(100)

        self.crossfade_btn = QPushButton()
        self.crossfade_btn.setCheckable(True)
        self.crossfade_btn.setIcon(icon("crossfade"))
        self.crossfade_btn.setFlat(True)
        self.crossfade_btn.setFixedSize(28, 28)
        self.crossfade_btn.setAccessibleName("Auto-crossfade")
        self.crossfade_btn.toggled.connect(self._update_crossfade_button)
        self.crossfade_btn.setChecked(auto_mix)
        self._update_crossfade_button(auto_mix)

        self.expand_btn = QPushButton()
        self.expand_btn.setIcon(icon("chevron-up"))
        self.expand_btn.setFlat(True)
        self.expand_btn.setFixedSize(20, 28)
        self.expand_btn.setToolTip("Show waveform")
        self.expand_btn.clicked.connect(self._toggle_extended)

        self.waveform_chart = _WaveformChart(
            bg_color="#33334d", bass_color="#8686AC", treble_color="#d8d8ec",
        )
        self.waveform_chart.setFixedHeight(WAVEFORM_HEIGHT)
        self.waveform_chart.setVisible(False)
        self.waveform_chart.seek_requested.connect(self._seek_to)
        self._waveform_workers: set = set()

        slider_col = QVBoxLayout()
        slider_col.setContentsMargins(0, 0, 0, 0)
        slider_col.setSpacing(0)
        slider_col.addWidget(self.title_label)
        slider_col.addWidget(self.position_slider)

        row = QHBoxLayout()
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(4)
        row.addWidget(self.cover_label)
        row.addWidget(self.prev_btn)
        row.addWidget(self.play_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.next_btn)
        row.addLayout(slider_col, 1)
        row.addWidget(self.like_btn)
        row.addWidget(self.time_label)
        self.volume_label = QLabel()
        self.volume_label.setPixmap(icon("volume").pixmap(16, 16))
        row.addWidget(self.volume_label)
        row.addWidget(self.volume_slider)
        row.addWidget(self.crossfade_btn)
        row.addWidget(self.expand_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(row)
        layout.addWidget(self.waveform_chart)
        self._collapsed_height = 56
        self.setFixedHeight(self._collapsed_height)

        self.play_btn.clicked.connect(self.toggle_play)
        self.stop_btn.clicked.connect(self.stop)
        self.prev_btn.clicked.connect(self.prev_requested.emit)
        self.next_btn.clicked.connect(self.next_requested.emit)
        self.position_slider.sliderMoved.connect(self._seek_to_ms)
        self.position_slider.sliderPressed.connect(self._begin_manual_seek)
        self.position_slider.sliderReleased.connect(self._end_manual_seek)
        self.volume_slider.valueChanged.connect(self._set_volume)
        for player in (self.player, self._transition_player):
            player.positionChanged.connect(lambda pos, p=player: self._on_position_changed(p, pos))
            player.durationChanged.connect(lambda dur, p=player: self._on_duration_changed(p, dur))
            player.playbackStateChanged.connect(lambda state, p=player: self._on_state_changed(p, state))
            player.mediaStatusChanged.connect(lambda status, p=player: self._on_media_status(p, status))

    def crossfade_to(self, track: Track):
        """Queue a beat-aligned, level-matched transition to `track`."""
        if self.current_track is None or self.is_crossfading:
            return
        current_grid = _track_grid(self.current_track, self.waveform_chart._precise_grid)
        plan = _plan_auto_mix(
            self.current_track, track, self.player.position(), self.transition_duration_ms,
            energy_match=self.energy_match, outgoing_grid=current_grid,
            bpm_sync=self.bpm_sync,
        )
        self._transition_plan = plan
        self._transition_track = track
        self._transition_pending_start = False
        self._transition_position_ready = False
        self._transition_player.setPlaybackRate(1.0)
        self._transition_player.setSource(QUrl.fromLocalFile(track.filepath))
        self._transition_output.setVolume(0.0)
        if plan.wait_ms:
            self._transition_start_timer.start(plan.wait_ms)
        else:
            self._begin_crossfade()

    def _begin_crossfade(self):
        if self._transition_track is None or self._transition_plan is None:
            return
        if not self._transition_position_ready:
            self._transition_pending_start = True
            return
        self._transition_pending_start = False
        self._transition_player.play()
        self._transition_elapsed.start()
        self._transition_timer.start()
        self._set_progress_blink(True)

    def _refresh_progress_blink_style(self):
        style = self.position_slider.style()
        style.unpolish(self.position_slider)
        style.polish(self.position_slider)
        self.position_slider.update()

    def _set_progress_blink(self, active: bool):
        if active:
            self._progress_blink_on = True
            self.position_slider.setProperty("transitionBlink", "on")
            self._progress_blink_timer.start()
        else:
            self._progress_blink_timer.stop()
            self._progress_blink_on = False
            self.position_slider.setProperty("transitionBlink", "off")
        self._refresh_progress_blink_style()

    def _toggle_progress_blink(self):
        self._progress_blink_on = not self._progress_blink_on
        self.position_slider.setProperty(
            "transitionBlink", "on" if self._progress_blink_on else "off",
        )
        self._refresh_progress_blink_style()

    def load_track(self, track: Track, autoplay: bool = True):
        self._cancel_crossfade()
        self._manual_seeking = False
        self._seek_guard_position = None
        self.current_track = track
        self.player.setSource(QUrl.fromLocalFile(track.filepath))
        self._set_display_track(track)
        if autoplay:
            self.player.play()
        self._load_waveform(track)

    def _set_display_track(self, track: Track | None):
        self.display_track = track
        self._full_title = track.display_name if track else "No track loaded"
        self._update_elided_title()
        self.cover_label.setPixmap(cover_pixmap(track.cover_path, 40) if track else cover_pixmap("", 40))
        self._update_like_button()

    def _on_title_clicked(self):
        track = self.display_track or self.current_track
        if track is not None:
            self.track_title_clicked.emit(track)

    def _seek_to_ms(self, position: int):
        if self.is_crossfading:
            self._cancel_crossfade()
        self.player.setPosition(position)

    def _begin_manual_seek(self):
        self._manual_seeking = True
        self._cancel_crossfade()

    def _end_manual_seek(self):
        self._manual_seeking = False
        self._seek_guard_position = self.player.position()
        self._maybe_request_crossfade(self._seek_guard_position, self.player.duration())

    def _set_volume(self, value: int):
        self.audio_output.setVolume(value / 100)
        if self._transition_timer.isActive():
            self._transition_output.setVolume(value / 100)

    def _on_audio_buffer(self, source_player, buffer):
        # Buffers can still arrive after stop/pause; they must not relight the meter.
        if source_player.playbackState() != QMediaPlayer.PlayingState:
            return
        if not buffer.isValid() or buffer.sampleCount() == 0:
            return
        sample_format = buffer.format().sampleFormat()
        dtype_scale = {
            QAudioFormat.UInt8: (np.uint8, 128.0, 128.0),
            QAudioFormat.Int16: (np.int16, 0.0, 32768.0),
            QAudioFormat.Int32: (np.int32, 0.0, 2147483648.0),
            QAudioFormat.Float: (np.float32, 0.0, 1.0),
        }.get(sample_format)
        if dtype_scale is None:
            return
        dtype, offset, scale = dtype_scale
        samples = (np.frombuffer(bytes(buffer.constData()), dtype=dtype).astype(np.float32) - offset) / scale
        channels = max(1, buffer.format().channelCount())
        usable = len(samples) - len(samples) % channels
        if usable <= 0:
            return
        frames = samples[:usable].reshape(-1, channels)
        rms = np.sqrt(np.mean(np.square(frames), axis=0))
        left = float(rms[0])
        right = float(rms[1] if channels > 1 else rms[0])
        self._deck_levels[source_player] = (left, right)
        active = self._deck_levels.get(self.player, (0.0, 0.0))
        active_gain = self.audio_output.volume()
        if self._transition_timer.isActive():
            incoming = self._deck_levels.get(self._transition_player, (0.0, 0.0))
            incoming_gain = self._transition_output.volume()
        else:
            incoming = (0.0, 0.0)
            incoming_gain = 0.0
        left_rms = math.sqrt((active[0] * active_gain) ** 2 + (incoming[0] * incoming_gain) ** 2)
        right_rms = math.sqrt((active[1] * active_gain) ** 2 + (incoming[1] * incoming_gain) ** 2)
        self.audio_levels_changed.emit(
            max(-60.0, 20.0 * math.log10(max(left_rms, 1e-3))),
            max(-60.0, 20.0 * math.log10(max(right_rms, 1e-3))),
        )

    @property
    def is_crossfading(self) -> bool:
        return (
            self._transition_timer.isActive()
            or self._transition_start_timer.isActive()
            or self._transition_pending_start
        )

    def _load_waveform(self, track: Track):
        self.waveform_chart.set_beat_grid(None)
        self.waveform_chart.set_peaks(track.waveform_peaks)
        self.waveform_chart.set_playhead(None)
        self.waveform_chart.set_bpm(track.tempo)
        self.waveform_chart.set_beat_offset(track.beat_offset)
        self.waveform_chart.set_duration(track.duration)

        worker = _WaveformWorker(track.filepath, track.tempo)
        worker.done.connect(self._on_waveform_peaks)
        worker.finished.connect(lambda: self._waveform_workers.discard(worker))
        self._waveform_workers.add(worker)
        worker.start()

    def _on_waveform_peaks(self, filepath: str, peaks: list, grid=None, detail=None):
        track = self.display_track or self.current_track
        if track and filepath == track.filepath:
            if len(self.waveform_chart.peaks) < 2400 and peaks:
                self.waveform_chart.set_peaks(peaks)
            self.waveform_chart.set_detail_peaks(detail)
            self.waveform_chart.set_beat_grid(grid)

    def _seek_to(self, seconds: float):
        if self.current_track is None:
            return
        self._cancel_crossfade()
        position = int(seconds * 1000)
        duration = self.player.duration()
        if duration > 0:
            position = min(position, duration)
        self._seek_guard_position = position
        self.position_slider.setValue(position)
        self.time_label.setText(f"{_fmt_ms(position)} / {_fmt_ms(duration)}")
        self.waveform_chart.set_playhead(position / 1000.0)
        self.player.setPosition(position)
        self._maybe_request_crossfade(position, duration)

    def _update_elided_title(self):
        fm = QFontMetrics(self.title_label.font())
        elided = fm.elidedText(self._full_title, Qt.ElideRight, max(self.title_label.width(), 40))
        self.title_label.setText(elided)

    def _show_large_cover(self):
        track = self.display_track or self.current_track
        if track is None:
            return
        if self._cover_dialog is None:
            self._cover_dialog = CoverArtDialog(self)
        self._cover_dialog.show_cover(track.cover_path, track.display_name)
        self._cover_dialog.show()
        self._cover_dialog.raise_()
        self._cover_dialog.activateWindow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elided_title()

    def toggle_play(self):
        if self.current_track is None:
            if self.on_request_track:
                self.on_request_track()
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self):
        self._cancel_crossfade()
        self.player.stop()
        self._deck_levels = {self.player: (0.0, 0.0), self._transition_player: (0.0, 0.0)}
        self.audio_levels_changed.emit(-60.0, -60.0)

    def _on_position_changed(self, player, pos: int):
        if player is self._transition_player:
            if self.is_crossfading and self.display_track is self._transition_track:
                self.waveform_chart.set_playhead(pos / 1000.0)
            return
        if player is not self.player:
            return
        self._sync_progress_from_player(player, update_waveform=not self.is_crossfading)
        if self._seek_guard_position is not None:
            if pos <= self._seek_guard_position + 500:
                return
            self._seek_guard_position = None
        self._maybe_request_crossfade(pos, self.player.duration())

    def _maybe_request_crossfade(self, position_ms: int, duration_ms: int) -> bool:
        if (
            not self.crossfade_btn.isChecked()
            or self._manual_seeking
            or self._crossfade_requested
            or duration_ms <= 0
            or not self._is_playing()
        ):
            return False
        grid = _track_grid(self.current_track, self.waveform_chart._precise_grid)
        beat_margin_ms = round(60000 / grid[0]) if grid else 0
        if (
            duration_ms - position_ms <= self.transition_duration_ms + beat_margin_ms
        ):
            self._crossfade_requested = True
            self.crossfade_requested.emit()
            return True
        return False

    def _is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlayingState

    def _sync_progress_from_player(self, player, update_waveform=True):
        position = player.position()
        duration = player.duration()
        self.position_slider.setRange(0, duration)
        self.position_slider.setValue(position)
        self.time_label.setText(f"{_fmt_ms(position)} / {_fmt_ms(duration)}")
        if update_waveform:
            self.waveform_chart.set_playhead(position / 1000.0)

    def _show_transition_track(self):
        track = self._transition_track
        if track is None:
            return
        if self.display_track is not track:
            self._set_display_track(track)
            self._load_waveform(track)
        self.waveform_chart.set_playhead(self._transition_player.position() / 1000.0)

    def _on_duration_changed(self, player, dur: int):
        if player is not self.player:
            return
        self.position_slider.setRange(0, dur)
        self._maybe_request_crossfade(self.player.position(), dur)

    def _on_state_changed(self, player, state):
        if player is self._transition_player:
            if state == QMediaPlayer.PlayingState and self._transition_track is not None:
                self._show_transition_track()
            return
        if player is not self.player:
            return
        is_playing = state == QMediaPlayer.PlayingState
        self.play_btn.setIcon(icon("pause") if is_playing else icon("play"))
        self.playing_changed.emit(is_playing)
        if is_playing:
            self._maybe_request_crossfade(player.position(), player.duration())
        if not is_playing and not self.is_crossfading:
            self.audio_levels_changed.emit(-60.0, -60.0)

    def _on_media_status(self, player, status):
        if player is self._transition_player:
            if (
                status == QMediaPlayer.MediaStatus.LoadedMedia
                and self._transition_plan is not None
                and not self._transition_position_ready
            ):
                self._transition_player.setPosition(self._transition_plan.incoming_position_ms)
                self._transition_position_ready = True
                self._show_transition_track()
                if self._transition_pending_start:
                    self._begin_crossfade()
            return
        if player is not self.player:
            return
        if status == QMediaPlayer.EndOfMedia:
            self.track_finished.emit()

    def _advance_crossfade(self):
        elapsed = self._transition_elapsed.elapsed()
        duration_ms = self._transition_plan.duration_ms if self._transition_plan else self.transition_duration_ms
        progress = min(1.0, elapsed / duration_ms)
        volume = self.volume_slider.value() / 100
        outgoing_gain, incoming_gain = _transition_gains(progress)
        self.audio_output.setVolume(volume * outgoing_gain)
        self._transition_output.setVolume(volume * incoming_gain)
        if self._transition_plan and progress < 1.0:
            self._transition_player.setPlaybackRate(
                _transition_playback_rate(progress, self._transition_plan.playback_rate)
            )
        if progress >= 1.0:
            track = self._transition_track
            if self.display_track is not track:
                self._show_transition_track()
            self._transition_timer.stop()
            self._set_progress_blink(False)
            outgoing_player = self.player
            outgoing_output = self.audio_output
            self.player = self._transition_player
            self._transition_player = outgoing_player
            self.audio_output = self._transition_output
            self._transition_output = outgoing_output
            self.current_track = track
            self._manual_seeking = False
            self._seek_guard_position = None
            self.audio_output.setVolume(volume)
            self._transition_track = None
            self._transition_plan = None
            self._transition_pending_start = False
            self._transition_position_ready = False
            self._transition_elapsed.invalidate()
            self._crossfade_requested = False
            outgoing_player.stop()
            self._sync_progress_from_player(self.player)
            self.track_transitioned.emit(track)

    def _toggle_favorite(self):
        track = self.display_track or self.current_track
        if track is None:
            return
        track.favorite = not track.favorite
        self._update_like_button()
        self.favorite_toggled.emit(track)

    def set_favorite_state(self, track_id: int, favorite: bool):
        """Sync the like button when the current track is (un)liked elsewhere."""
        track = self.display_track or self.current_track
        if track is not None and track.id == track_id:
            track.favorite = favorite
            self._update_like_button()

    def _update_like_button(self):
        track = self.display_track or self.current_track
        liked = bool(track and track.favorite)
        self.like_btn.setEnabled(track is not None)
        self.like_btn.setIcon(heart_icon(liked))
        self.like_btn.setToolTip("Remove from favorites" if liked else "Add to favorites")

    def _cancel_crossfade(self):
        self._transition_start_timer.stop()
        self._transition_timer.stop()
        self._set_progress_blink(False)
        self._transition_player.stop()
        self._transition_player.setPlaybackRate(1.0)
        self._transition_track = None
        self._transition_plan = None
        self._transition_pending_start = False
        self._transition_position_ready = False
        self._transition_elapsed.invalidate()
        self._crossfade_requested = False
        self.audio_output.setVolume(self.volume_slider.value() / 100)
        if self.current_track is not None and self.display_track is not self.current_track:
            self._set_display_track(self.current_track)
            self._load_waveform(self.current_track)

    def _update_crossfade_button(self, checked: bool):
        if checked:
            self.crossfade_btn.setIcon(icon("crossfade", "#ffffff"))
            self.crossfade_btn.setStyleSheet(
                f"background-color: {self._title_accent}; border: 2px solid {self._title_accent}; "
                "border-radius: 4px;"
            )
            self.crossfade_btn.setToolTip("Auto-crossfade: On")
        else:
            self.crossfade_btn.setIcon(icon("crossfade"))
            self.crossfade_btn.setStyleSheet(
                f"background-color: transparent; border: 1px solid {self._title_accent}; "
                "border-radius: 4px;"
            )
            self.crossfade_btn.setToolTip("Auto-crossfade: Off")

    def _toggle_extended(self):
        expanded = not self.waveform_chart.isVisible()
        self.waveform_chart.setVisible(expanded)
        self.expand_btn.setIcon(icon("chevron-down") if expanded else icon("chevron-up"))
        self.expand_btn.setToolTip("Hide waveform" if expanded else "Show waveform")
        self.setFixedHeight(
            self._collapsed_height + WAVEFORM_HEIGHT if expanded else self._collapsed_height
        )

    def apply_theme(
        self, accent_color: str,
        bg: str | None = None, bass: str | None = None, treble: str | None = None,
    ):
        """Re-tint the accent label and refresh flat icons after a theme switch."""
        self._title_accent = accent_color
        self.title_label.setStyleSheet(f"font-size: 10px; color: {accent_color};")
        is_playing = self.player.playbackState() == QMediaPlayer.PlayingState
        self.play_btn.setIcon(icon("pause") if is_playing else icon("play"))
        self.stop_btn.setIcon(icon("stop"))
        self.prev_btn.setIcon(icon("prev"))
        self.next_btn.setIcon(icon("next"))
        self._update_crossfade_button(self.crossfade_btn.isChecked())
        self.volume_label.setPixmap(icon("volume").pixmap(16, 16))
        self._update_like_button()
        expanded = self.waveform_chart.isVisible()
        self.expand_btn.setIcon(icon("chevron-down") if expanded else icon("chevron-up"))
        if bg and bass and treble:
            self.waveform_chart.set_theme(bg, bass, treble)
