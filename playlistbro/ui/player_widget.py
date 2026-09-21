"""Embedded audio player widget built on QtMultimedia."""
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)

from ..core.models import Track
from .icon_loader import cover_pixmap, icon


def _fmt_ms(ms: int) -> str:
    s = int(ms / 1000)
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}"


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

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(40, 40)
        self.cover_label.setPixmap(cover_pixmap("", 40))

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

        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)

        self.time_label = QLabel("0:00 / 0:00")

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(100)

        slider_col = QVBoxLayout()
        slider_col.setContentsMargins(0, 0, 0, 0)
        slider_col.setSpacing(0)
        slider_col.addWidget(self.title_label)
        slider_col.addWidget(self.position_slider)

        row = QHBoxLayout()
        row.setContentsMargins(10, 4, 10, 4)
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        self.setFixedHeight(56)

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

    def _update_elided_title(self):
        fm = QFontMetrics(self.title_label.font())
        elided = fm.elidedText(self._full_title, Qt.ElideRight, max(self.title_label.width(), 40))
        self.title_label.setText(elided)

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

    def _on_duration_changed(self, dur: int):
        self.position_slider.setRange(0, dur)

    def _on_state_changed(self, state):
        self.play_btn.setIcon(icon("pause") if state == QMediaPlayer.PlayingState else icon("play"))

    def _on_media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.track_finished.emit()

    def apply_theme(self, accent_color: str):
        """Re-tint the accent label and refresh flat icons after a theme switch."""
        self._title_accent = accent_color
        self.title_label.setStyleSheet(f"font-size: 10px; color: {accent_color};")
        is_playing = self.player.playbackState() == QMediaPlayer.PlayingState
        self.play_btn.setIcon(icon("pause") if is_playing else icon("play"))
        self.stop_btn.setIcon(icon("stop"))
        self.prev_btn.setIcon(icon("prev"))
        self.next_btn.setIcon(icon("next"))
        self.volume_label.setPixmap(icon("volume").pixmap(16, 16))
