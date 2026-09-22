"""Embedded audio player widget built on QtMultimedia."""
import math

import numpy as np

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtMultimedia import QAudioBufferOutput, QAudioFormat, QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QStyle, QStyleOptionSlider,
    QVBoxLayout, QWidget,
)

from ..core.models import Track
from .icon_loader import cover_pixmap, icon
from .waveform_view import _WaveformChart, _WaveformWorker

WAVEFORM_HEIGHT = 120
CROSSFADE_DURATION_MS = 6000


def _fmt_ms(ms: int) -> str:
    s = int(ms / 1000)
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}"


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


class PlayerWidget(QWidget):
    track_finished = Signal()
    next_requested = Signal()
    prev_requested = Signal()
    crossfade_requested = Signal()
    track_transitioned = Signal(object)
    playing_changed = Signal(bool)
    audio_levels_changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_track: Track | None = None
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
        self._transition_timer.setInterval(50)
        self._transition_timer.timeout.connect(self._advance_crossfade)
        self._transition_track: Track | None = None
        self._transition_started_at = 0
        self._crossfade_requested = False
        self._manual_seeking = False
        self._seek_guard_position = None

        self.title_label = QLabel(self._full_title)
        self._title_accent = "#8686AC"
        self.title_label.setStyleSheet(f"font-size: 10px; color: {self._title_accent};")
        self.title_label.setFixedHeight(14)

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

        self.position_slider = SeekSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)

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
        self._update_crossfade_button(False)

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
        """Start a tempo-matched transition from the current track to `track`."""
        if self.current_track is None or self._transition_timer.isActive():
            return
        target_tempo = track.tempo or self.current_track.tempo
        rate = self.current_track.tempo / target_tempo if target_tempo else 1.0
        self._transition_track = track
        self._transition_player.setPlaybackRate(max(0.8, min(1.25, rate)))
        self._transition_player.setSource(QUrl.fromLocalFile(track.filepath))
        self._transition_output.setVolume(0.0)
        self._transition_player.play()
        self._transition_started_at = 0
        self._transition_timer.start()

    def load_track(self, track: Track, autoplay: bool = True):
        self._cancel_crossfade()
        self._manual_seeking = False
        self._seek_guard_position = None
        self.current_track = track
        self.player.setSource(QUrl.fromLocalFile(track.filepath))
        self._full_title = track.display_name
        self._update_elided_title()
        self.cover_label.setPixmap(cover_pixmap(track.cover_path, 40))
        if autoplay:
            self.player.play()
        self._load_waveform(track)

    def _seek_to_ms(self, position: int):
        if self._transition_timer.isActive():
            self._cancel_crossfade()
        self.player.setPosition(position)

    def _begin_manual_seek(self):
        self._manual_seeking = True
        self._cancel_crossfade()

    def _end_manual_seek(self):
        self._manual_seeking = False
        self._seek_guard_position = self.player.position()

    def _set_volume(self, value: int):
        self.audio_output.setVolume(value / 100)
        if self._transition_timer.isActive():
            self._transition_output.setVolume(value / 100)

    def _on_audio_buffer(self, source_player, buffer):
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
        return self._transition_timer.isActive()

    def _load_waveform(self, track: Track):
        self.waveform_chart.set_peaks(track.waveform_peaks)
        self.waveform_chart.set_playhead(None)
        self.waveform_chart.set_bpm(track.tempo)
        self.waveform_chart.set_duration(track.duration)

        if self.waveform_chart.peaks:
            return

        worker = _WaveformWorker(track.filepath)
        worker.done.connect(self._on_waveform_peaks)
        worker.finished.connect(lambda: self._waveform_workers.discard(worker))
        self._waveform_workers.add(worker)
        worker.start()

    def _on_waveform_peaks(self, filepath: str, peaks: list):
        if self.current_track and filepath == self.current_track.filepath:
            self.waveform_chart.set_peaks(peaks)

    def _seek_to(self, seconds: float):
        if self.current_track is None:
            return
        self._cancel_crossfade()
        position = int(seconds * 1000)
        self._seek_guard_position = position
        self.player.setPosition(position)

    def _update_elided_title(self):
        fm = QFontMetrics(self.title_label.font())
        elided = fm.elidedText(self._full_title, Qt.ElideRight, max(self.title_label.width(), 40))
        self.title_label.setText(elided)

    def _show_large_cover(self):
        if self.current_track is None:
            return
        if self._cover_dialog is None:
            self._cover_dialog = CoverArtDialog(self)
        self._cover_dialog.show_cover(self.current_track.cover_path, self.current_track.display_name)
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

    def _on_position_changed(self, player, pos: int):
        if player is not self.player:
            return
        self.position_slider.setValue(pos)
        self.time_label.setText(f"{_fmt_ms(pos)} / {_fmt_ms(self.player.duration())}")
        self.waveform_chart.set_playhead(pos / 1000.0)
        if self._seek_guard_position is not None:
            if pos <= self._seek_guard_position + 500:
                return
            self._seek_guard_position = None
        if (
            self.crossfade_btn.isChecked()
            and not self._manual_seeking
            and not self._crossfade_requested
            and self.player.duration() > 0
            and self.player.duration() - pos <= CROSSFADE_DURATION_MS
        ):
            self._crossfade_requested = True
            self.crossfade_requested.emit()

    def _on_duration_changed(self, player, dur: int):
        if player is not self.player:
            return
        self.position_slider.setRange(0, dur)

    def _on_state_changed(self, player, state):
        if player is not self.player:
            return
        is_playing = state == QMediaPlayer.PlayingState
        self.play_btn.setIcon(icon("pause") if is_playing else icon("play"))
        self.playing_changed.emit(is_playing)
        if not is_playing and not self._transition_timer.isActive():
            self.audio_levels_changed.emit(-60.0, -60.0)

    def _on_media_status(self, player, status):
        if player is not self.player:
            return
        if status == QMediaPlayer.EndOfMedia:
            self.track_finished.emit()

    def _advance_crossfade(self):
        if self._transition_started_at == 0:
            self._transition_started_at = self._transition_player.position()
        elapsed = max(0, self._transition_player.position() - self._transition_started_at)
        progress = min(1.0, elapsed / CROSSFADE_DURATION_MS)
        volume = self.volume_slider.value() / 100
        self.audio_output.setVolume(volume * (1.0 - progress))
        self._transition_output.setVolume(volume * progress)
        if progress >= 1.0:
            track = self._transition_track
            self._transition_timer.stop()
            self.player.stop()
            self.player, self._transition_player = self._transition_player, self.player
            self.audio_output, self._transition_output = self._transition_output, self.audio_output
            self.current_track = track
            self._manual_seeking = False
            self._seek_guard_position = None
            self.audio_output.setVolume(volume)
            self._transition_track = None
            self._crossfade_requested = False
            self._full_title = track.display_name
            self._update_elided_title()
            self.cover_label.setPixmap(cover_pixmap(track.cover_path, 40))
            self._load_waveform(track)
            self.track_transitioned.emit(track)

    def _cancel_crossfade(self):
        self._transition_timer.stop()
        self._transition_player.stop()
        self._transition_track = None
        self._crossfade_requested = False
        self.audio_output.setVolume(self.volume_slider.value() / 100)

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
        expanded = self.waveform_chart.isVisible()
        self.expand_btn.setIcon(icon("chevron-down") if expanded else icon("chevron-up"))
        if bg and bass and treble:
            self.waveform_chart.set_theme(bg, bass, treble)
