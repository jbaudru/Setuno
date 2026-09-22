"""Embedded audio player widget built on QtMultimedia."""
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QStyle, QStyleOptionSlider,
    QVBoxLayout, QWidget,
)

from ..core.models import Track
from .icon_loader import cover_pixmap, icon
from .waveform_view import _WaveformChart, _WaveformWorker

WAVEFORM_HEIGHT = 120


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_track: Track | None = None
        self._full_title = "No track loaded"
        self.on_request_track = None  # called when Play is pressed with nothing loaded yet

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.8)

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
        self.position_slider.sliderMoved.connect(self.player.setPosition)
        self.volume_slider.valueChanged.connect(lambda v: self.audio_output.setVolume(v / 100))
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_state_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status)

    def load_track(self, track: Track, autoplay: bool = True):
        self.current_track = track
        self.player.setSource(QUrl.fromLocalFile(track.filepath))
        self._full_title = track.display_name
        self._update_elided_title()
        self.cover_label.setPixmap(cover_pixmap(track.cover_path, 40))
        if autoplay:
            self.player.play()
        self._load_waveform(track)

    def _load_waveform(self, track: Track):
        self.waveform_chart.set_peaks([])
        self.waveform_chart.set_playhead(None)
        self.waveform_chart.set_bpm(track.tempo)
        self.waveform_chart.set_duration(track.duration)

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
        self.player.setPosition(int(seconds * 1000))

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
        self.player.stop()

    def _on_position_changed(self, pos: int):
        self.position_slider.setValue(pos)
        self.time_label.setText(f"{_fmt_ms(pos)} / {_fmt_ms(self.player.duration())}")
        self.waveform_chart.set_playhead(pos / 1000.0)

    def _on_duration_changed(self, dur: int):
        self.position_slider.setRange(0, dur)

    def _on_state_changed(self, state):
        self.play_btn.setIcon(icon("pause") if state == QMediaPlayer.PlayingState else icon("play"))

    def _on_media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.track_finished.emit()

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
        self.volume_label.setPixmap(icon("volume").pixmap(16, 16))
        expanded = self.waveform_chart.isVisible()
        self.expand_btn.setIcon(icon("chevron-down") if expanded else icon("chevron-up"))
        if bg and bass and treble:
            self.waveform_chart.set_theme(bg, bass, treble)
