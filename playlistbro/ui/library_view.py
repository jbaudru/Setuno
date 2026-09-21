"""Library tab: folder scanning and full track listing."""
import random
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QMenu, QMessageBox, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.database import Database
from ..core.scanner import scan_folder, is_within_folder
from ..core.models import Track
from .icon_loader import icon, cover_pixmap
from .track_edit import edit_bpm, edit_key, edit_metadata
from .waveform_view import MiniWaveform, WaveformDialog

COLUMNS = ["Title", "Artist", "Album", "Genre", "BPM", "Key", "Camelot", "Energy", "Duration", "Waveform"]
WAVEFORM_COL = len(COLUMNS) - 1

class NumericTableWidgetItem(QTableWidgetItem):
    """QTableWidgetItem that sorts using its numeric UserRole value."""
    
    def __lt__(self, other):
        try:
            return float(self.data(Qt.UserRole)) < float(other.data(Qt.UserRole))
        except (TypeError, ValueError):
            return super().__lt__(other)

class _FolderManagerDialog(QDialog):
    """Lets the user drop folders from the multi-folder library (tracks stay in place on disk)."""

    def __init__(self, folders: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Library Folders")
        self.resize(480, 300)
        self.list_widget = QListWidget()
        self.list_widget.addItems(folders)
        remove_btn = QPushButton(" Remove Selected")
        remove_btn.clicked.connect(self._remove_selected)
        close_btn = QPushButton(" Close")
        close_btn.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Folders included in this library:"))
        layout.addWidget(self.list_widget)
        row = QHBoxLayout()
        row.addWidget(remove_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def _remove_selected(self):
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def folders(self) -> list[str]:
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]


class ScanWorker(QThread):
    progress = Signal(int, int, str)
    track_ready = Signal(object)
    finished_ok = Signal()

    def __init__(self, folder: str, db: Database, force: bool = False):
        super().__init__()
        self.folder = folder
        self.db = db
        self.force = force
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        scan_folder(
            self.folder, self.db,
            progress_cb=lambda d, t, n: self.progress.emit(d, t, n),
            track_cb=lambda track: self.track_ready.emit(track),
            should_cancel=lambda: self._cancel,
            force=self.force,
        )
        self.finished_ok.emit()


class LibraryView(QWidget):
    def __init__(
        self, db: Database, on_library_changed=None, on_play_track=None,
        get_library_folders=None, set_library_folders=None, parent=None,
    ):
        super().__init__(parent)
        self.db = db
        self.on_library_changed = on_library_changed
        self.on_play_track = on_play_track
        self.get_library_folders = get_library_folders or (lambda: [])
        self.set_library_folders = set_library_folders or (lambda folders: None)
        self.tracks: list[Track] = []
        self.worker: ScanWorker | None = None
        self._refresh_pending = False
        self._pending_folder: str | None = None
        self.playing_track_id: int | None = None
        self._playing_bg = "#8686AC"
        self.waveform_dialog: WaveformDialog | None = None
        self._wave_bg = "#33334d"
        self._wave_bass = "#8686AC"
        self._wave_treble = "#d8d8ec"

        self.scan_btn = QPushButton(" Scan Folder...")
        self.scan_btn.setIcon(icon("folder"))
        self.stop_btn = QPushButton(" Stop")
        self.stop_btn.setIcon(icon("stop"))
        self.stop_btn.setEnabled(False)
        self.manage_folders_btn = QPushButton(" Manage Folders...")
        self.manage_folders_btn.setIcon(icon("folder"))
        self.folder_combo = QComboBox()
        self.folder_combo.addItem("All folders", None)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search title / artist / genre...")
        self.status_label = QLabel("")
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)

        top = QHBoxLayout()
        top.addWidget(self.scan_btn)
        top.addWidget(self.stop_btn)
        top.addWidget(self.manage_folders_btn)
        top.addWidget(self.folder_combo)
        top.addWidget(self.search_edit, 1)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.doubleClicked.connect(self._play_selected)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)
        layout.addWidget(self.table)

        self.scan_btn.clicked.connect(self.choose_folder)
        self.stop_btn.clicked.connect(self.stop_scan)
        self.manage_folders_btn.clicked.connect(self.manage_folders)
        self.folder_combo.currentIndexChanged.connect(self.refresh_table)
        self.search_edit.textChanged.connect(self.refresh_table)

        self._refresh_folder_combo(self.get_library_folders())
        self.refresh_from_db()

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select music folder")
        if not folder:
            return
        force = self._prompt_rescan_mode(folder)
        if force is None:
            return  # user cancelled
        self._pending_folder = folder
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.scan_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.worker = ScanWorker(folder, self.db, force=force)
        self.worker.progress.connect(self._on_progress)
        self.worker.track_ready.connect(self._on_track_ready)
        self.worker.finished_ok.connect(self._on_scan_done)
        self.worker.start()

    def manage_folders(self):
        dlg = _FolderManagerDialog(self.get_library_folders(), self)
        if dlg.exec():
            folders = dlg.folders()
            self.set_library_folders(folders)
            self._refresh_folder_combo(folders)

    def _register_scanned_folder(self, folder: str):
        folders = self.get_library_folders()
        norm = str(Path(folder).resolve()).lower()
        if not any(str(Path(f).resolve()).lower() == norm for f in folders):
            folders = [*folders, folder]
            self.set_library_folders(folders)
        self._refresh_folder_combo(folders)

    def _refresh_folder_combo(self, folders: list[str]):
        current = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem("All folders", None)
        for f in folders:
            self.folder_combo.addItem(f, f)
        idx = self.folder_combo.findData(current)
        self.folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.folder_combo.blockSignals(False)

    def _prompt_rescan_mode(self, folder: str):
        """If `folder` was already scanned before, ask whether to fully rescan or only
        analyze new/changed files. Returns True (force), False (incremental), or None (cancel).
        """
        folder_norm = str(Path(folder).resolve()).lower()
        existing = self.db.get_existing_filepaths()
        already_scanned = any(
            str(Path(p).resolve()).lower().startswith(folder_norm) for p in existing
        )
        if not already_scanned:
            return False
        box = QMessageBox(self)
        box.setWindowTitle("Re-scan folder")
        box.setText(
            "This folder has already been scanned before.\n\n"
            "Do you want to fully re-analyze every file, or only new/changed ones?"
        )
        rescan_btn = box.addButton("Full Rescan", QMessageBox.AcceptRole)
        new_btn = box.addButton("Only New/Changed", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(new_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is rescan_btn:
            return True
        if clicked is new_btn:
            return False
        return None

    def stop_scan(self):
        if self.worker:
            self.worker.cancel()
            self.stop_btn.setEnabled(False)
            self.status_label.setText("Stopping...")

    def _on_progress(self, done, total, name):
        if total:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(done)
        self.status_label.setText(f"Analyzing ({done}/{total}): {name}")

    def _on_track_ready(self, track: Track):
        """Insert/update a track as soon as it's analyzed, so the list fills in live."""
        """
        for i, existing in enumerate(self.tracks):
            if existing.id == track.id:
                self.tracks[i] = track
                break
        else:
            self.tracks.append(track)
        if self.on_library_changed:
            self.on_library_changed()
        self._schedule_refresh()
        """
        None
        
    def _schedule_refresh(self):
        # Coalesce rapid per-track signals (thread pool) into a single table rebuild.
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(150, self._do_scheduled_refresh)

    def _do_scheduled_refresh(self):
        self._refresh_pending = False
        self.refresh_table()

    def _on_scan_done(self):
        self.progress_bar.setVisible(False)
        self.scan_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("Scan complete.")
        if self._pending_folder:
            self._register_scanned_folder(self._pending_folder)
            self._pending_folder = None
        self.refresh_from_db()
        if self.on_library_changed:
            self.on_library_changed()

    def refresh_from_db(self):
        self.tracks = self.db.get_all_tracks()
        self.refresh_table()

    def refresh_table(self):
        query = self.search_edit.text().strip().lower()
        folder_filter = self.folder_combo.currentData()
        rows = self.tracks
        if folder_filter:
            rows = [t for t in rows if is_within_folder(t.filepath, folder_filter)]
        if query:
            rows = [
                t for t in rows
                if query in t.title.lower() or query in t.artist.lower() or query in t.genre.lower()
            ]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, t in enumerate(rows):
            values = [
                t.title, t.artist, t.album, t.genre, f"{t.tempo:.1f}", t.key_name,
                t.camelot, f"{t.energy:.1f}", t.duration_str, "",
            ]
            for c, val in enumerate(values):
                if c == WAVEFORM_COL:
                    continue

                if c == 4:  # BPM
                    item = NumericTableWidgetItem(val)
                    item.setData(Qt.UserRole, float(t.tempo))
                else:
                    item = QTableWidgetItem(val)

                item.setData(1000, t.id)

                if c == 0:
                    item.setIcon(cover_pixmap(t.cover_path, 24))

                self.table.setItem(r, c, item)
            id_item = QTableWidgetItem("")
            id_item.setData(1000, t.id)
            self.table.setItem(r, WAVEFORM_COL, id_item)
            waveform = MiniWaveform(
                t.waveform_low, t.waveform_high, self._wave_bass, self._wave_treble, self._wave_bg,
                on_clicked=lambda track=t: self._show_waveform(track),
            )
            self.table.setCellWidget(r, WAVEFORM_COL, waveform)
        self.table.setSortingEnabled(True)
        self.status_label.setText(f"{len(rows)} track(s) in library")
        self._apply_playing_marker()

    def get_row_ids(self) -> list[int]:
        """Track ids in the current on-screen row order (respects search filter/sorting)."""
        ids = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                ids.append(item.data(1000))
        return ids

    def _apply_playing_marker(self):
        """Highlight the row of the currently playing track (background only).

        Must not touch cell text/data on a sortingEnabled table: editing the sort
        column's content (or toggling sortingEnabled) makes Qt auto re-sort rows,
        which would silently reshuffle row order out from under next/previous playback.
        """
        highlight = QColor(self._playing_bg)
        for r in range(self.table.rowCount()):
            id_item = self.table.item(r, 0)
            if not id_item:
                continue
            is_playing = self.playing_track_id is not None and id_item.data(1000) == self.playing_track_id
            for c in range(self.table.columnCount()):
                cell = self.table.item(r, c)
                if cell:
                    cell.setBackground(highlight if is_playing else QBrush())

    def set_playing_id(self, track_id: int | None):
        self.playing_track_id = track_id
        self._apply_playing_marker()

    def play_row(self, row: int):
        if row < 0 or row >= self.table.rowCount() or not self.on_play_track:
            return
        track_id = self.table.item(row, 0).data(1000)
        track = self.db.get_track(track_id)
        if track:
            self.on_play_track(track)

    def play_next(self):
        ids = self.get_row_ids()
        if not ids:
            return
        if self.playing_track_id in ids:
            idx = ids.index(self.playing_track_id) + 1
            if idx >= len(ids):
                return  # end of list reached
        else:
            idx = 0
        self.play_row(idx)

    def play_previous(self):
        ids = self.get_row_ids()
        if not ids:
            return
        if self.playing_track_id in ids:
            idx = ids.index(self.playing_track_id) - 1
            if idx < 0:
                return  # already at the start
        else:
            idx = 0
        self.play_row(idx)

    def play_random(self):
        if self.table.rowCount() == 0:
            return
        self.play_row(random.randrange(self.table.rowCount()))

    def _play_selected(self):
        self.play_row(self.table.currentRow())

    def _show_waveform(self, track: Track):
        if self.waveform_dialog is None:
            self.waveform_dialog = WaveformDialog(self,
                bg_color=self._wave_bg,
                bass_color=self._wave_bass,
                treble_color=self._wave_treble,
            )
        self.waveform_dialog.load_track(track)

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        item = self.table.item(row, 0)
        track = self.db.get_track(item.data(1000)) if item else None
        if not track:
            return
        menu = QMenu(self)
        bpm_action = menu.addAction("Edit BPM...")
        key_action = menu.addAction("Edit Key...")
        metadata_action = menu.addAction("Edit Metadata...")
        waveform_action = menu.addAction("Show Waveform...")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == bpm_action and edit_bpm(self, self.db, track):
            self.refresh_from_db()
        elif chosen == key_action and edit_key(self, self.db, track):
            self.refresh_from_db()
        elif chosen == metadata_action and edit_metadata(self, self.db, track):
            self.refresh_from_db()
        elif chosen == waveform_action:
            self._show_waveform(track)

    def apply_theme(self, bg: str | None = None, bass: str | None = None, treble: str | None = None):
        """Refresh flat icons (colors are set globally in icon_loader before calling this)."""
        self.scan_btn.setIcon(icon("folder"))
        self.stop_btn.setIcon(icon("stop"))
        self.manage_folders_btn.setIcon(icon("folder"))
        if bg and bass and treble:
            self._wave_bg, self._wave_bass, self._wave_treble = bg, bass, treble
            for r in range(self.table.rowCount()):
                widget = self.table.cellWidget(r, WAVEFORM_COL)
                if isinstance(widget, MiniWaveform):
                    widget.set_theme(bass, treble, bg)
