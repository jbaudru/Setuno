"""Visible playback queue with removal controls."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.database import Database
from ..core.scanner import is_readable_file
from .icon_loader import cover_pixmap
from .library_view import (
    BPM_COL, COLUMNS, FAV_COL, FAVORITE_COLOR, NumericTableWidgetItem,
    TITLE_COL, UNAVAILABLE_COLOR, UNFAVORITE_COLOR, WAVEFORM_COL,
)
from .waveform_view import MiniWaveform

PLAYED_COLOR = "#38a169"


class QueueView(QWidget):
    count_changed = Signal(int)

    def __init__(self, db: Database, is_track_played=None, on_play_track=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.is_track_played = is_track_played or (lambda _track_id: False)
        self.on_play_track = on_play_track
        self._track_ids: list[int] = []
        self._wave_bg = "#33334d"
        self._wave_bass = "#8686AC"
        self._wave_treble = "#d8d8ec"

        self.remove_btn = QPushButton("Remove selected")
        self.clear_btn = QPushButton("Clear queue")
        self.remove_btn.clicked.connect(self.remove_selected)
        self.clear_btn.clicked.connect(self.clear)

        controls = QHBoxLayout()
        controls.addWidget(self.remove_btn)
        controls.addWidget(self.clear_btn)
        controls.addStretch(1)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self._play_selected)
        for column, width in enumerate([30, 220, 100, 100, 100, 60, 70, 70, 70, 100, 145, 75]):
            self.table.setColumnWidth(column, width)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.table)
        self.refresh()

    def add_track(self, track) -> bool:
        return self.add_tracks([track]) == 1

    def add_tracks(self, tracks) -> int:
        queued_ids = set(self._track_ids)
        added = 0
        for track in tracks:
            if track.id is None or track.id in queued_ids:
                continue
            self._track_ids.append(track.id)
            queued_ids.add(track.id)
            added += 1
        if added:
            self.refresh()
        return added

    def pop_next(self):
        while self._track_ids:
            track_id = self._track_ids.pop(0)
            track = self.db.get_track(track_id)
            self.refresh()
            if track:
                return track
        return None

    def _play_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._track_ids) or not self.on_play_track:
            return
        track_id = self._track_ids.pop(row)
        track = self.db.get_track(track_id)
        self.refresh()
        if track:
            self.on_play_track(track)

    def remove_selected(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self._track_ids):
                del self._track_ids[row]
        self.refresh()

    def clear(self):
        self._track_ids.clear()
        self.refresh()

    def refresh(self):
        tracks = [self.db.get_track(track_id) for track_id in self._track_ids]
        tracks = [track for track in tracks if track]
        self._track_ids = [track.id for track in tracks]
        duplicate_counts = {}
        for track in self.db.get_all_tracks():
            key = (track.artist.strip().casefold(), track.title.strip().casefold())
            if any(key):
                duplicate_counts[key] = duplicate_counts.get(key, 0) + 1
        self.table.setRowCount(len(tracks))
        for row, track in enumerate(tracks):
            values = [
                "", track.title, track.artist, track.album, track.genre, f"{track.tempo:.0f}",
                track.key_name, track.camelot, f"{track.energy:.1f}", track.duration_str,
                (track.added_at or "")[:19].replace("T", " "),
                str(duplicate_counts.get((track.artist.strip().casefold(), track.title.strip().casefold()), 0) or ""),
                "",
            ]
            for column, value in enumerate(values):
                if column == WAVEFORM_COL:
                    continue
                if column == FAV_COL:
                    item = QTableWidgetItem("\u2665" if track.favorite else "\u2661")
                    item.setForeground(QBrush(QColor(FAVORITE_COLOR if track.favorite else UNFAVORITE_COLOR)))
                    item.setTextAlignment(Qt.AlignCenter)
                elif column == BPM_COL:
                    item = NumericTableWidgetItem(value)
                    item.setData(Qt.UserRole, float(track.tempo))
                else:
                    item = QTableWidgetItem(value)
                item.setData(1000, track.id)
                if column == TITLE_COL:
                    item.setIcon(cover_pixmap(track.cover_path, 24))
                    if self.is_track_played(track.id):
                        item.setForeground(QBrush(QColor(PLAYED_COLOR)))
                    elif not is_readable_file(track.filepath):
                        item.setForeground(QBrush(QColor(UNAVAILABLE_COLOR)))
                self.table.setItem(row, column, item)
            id_item = QTableWidgetItem("")
            id_item.setData(1000, track.id)
            self.table.setItem(row, WAVEFORM_COL, id_item)
            self.table.setCellWidget(
                row, WAVEFORM_COL,
                MiniWaveform(
                    track.waveform_peaks or track.waveform_low,
                    self._wave_bass, self._wave_treble, self._wave_bg,
                ),
            )
        self.count_changed.emit(len(self._track_ids))

    def apply_theme(self, bg: str, bass: str, treble: str):
        self._wave_bg, self._wave_bass, self._wave_treble = bg, bass, treble
        for row in range(self.table.rowCount()):
            waveform = self.table.cellWidget(row, WAVEFORM_COL)
            if isinstance(waveform, MiniWaveform):
                waveform.set_theme(bass, treble, bg)

    def __len__(self):
        return len(self._track_ids)
