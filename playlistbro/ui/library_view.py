"""Library tab: folder scanning and full track listing."""
import os
import random
import time
from pathlib import Path

from PySide6.QtCore import QProcess, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListView, QListWidget, QMenu, QMessageBox, QProgressBar, QPushButton, QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem, QToolButton, QTreeView, QVBoxLayout, QWidget,
)

from ..core.database import Database
from ..core.scanner import is_readable_file, scan_folder, is_within_folder
from ..core.models import Track
from .icon_loader import icon, cover_pixmap
from .track_edit import edit_bpm, edit_key, edit_metadata
from .waveform_view import MiniWaveformDelegate, WaveformDialog, reduce_peaks

COLUMNS = ["\u2665", "Title", "Artist", "Album", "Genre", "BPM", "Key", "Camelot", "Energy", "Duration", "Added", "Duplicates", "Waveform"]
FAV_COL = 0
TITLE_COL = 1
BPM_COL = 5
WAVEFORM_COL = len(COLUMNS) - 1
FAVORITE_COLOR = "#e0435c"
UNFAVORITE_COLOR = "#6b6b85"
UNAVAILABLE_COLOR = "#e06c48"
PLAYED_COLOR = "#38a169"


def duplicate_tracks(tracks: list[Track]) -> list[Track]:
    """Return later tracks whose normalized artist/title matches an earlier track."""
    seen = set()
    duplicates = []
    for track in tracks:
        key = (track.artist.strip().casefold(), track.title.strip().casefold())
        if not any(key):
            continue
        if key in seen:
            duplicates.append(track)
        else:
            seen.add(key)
    return duplicates


def show_in_file_explorer(filepath: str):
    if is_readable_file(filepath):
        explorer = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "explorer.exe")
        QProcess.startDetached(explorer, ["/select,", str(Path(filepath).resolve())])

class NumericTableWidgetItem(QTableWidgetItem):
    """QTableWidgetItem that sorts using its numeric UserRole value."""
    
    def __lt__(self, other):
        try:
            return float(self.data(Qt.UserRole)) < float(other.data(Qt.UserRole))
        except (TypeError, ValueError):
            return super().__lt__(other)


FAV_ROLE = Qt.UserRole + 3


def heart_icon(favorite: bool):
    return icon("heart", FAVORITE_COLOR) if favorite else icon("heart-outline")


def set_favorite_item(item: QTableWidgetItem, favorite: bool):
    item.setText("")
    item.setData(FAV_ROLE, bool(favorite))
    item.setData(Qt.UserRole, int(bool(favorite)))
    item.setToolTip("Remove from favorites" if favorite else "Add to favorites")


def make_favorite_item(favorite: bool) -> QTableWidgetItem:
    item = NumericTableWidgetItem("")
    set_favorite_item(item, favorite)
    return item


class FavoriteDelegate(QStyledItemDelegate):
    """Paints a centered heart (filled red when favorite) in the favorite column."""

    SIZE = 14

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        favorite = index.data(FAV_ROLE)
        if favorite is None:
            return
        rect = option.rect
        x = rect.x() + (rect.width() - self.SIZE) // 2
        y = rect.y() + (rect.height() - self.SIZE) // 2
        heart_icon(bool(favorite)).paint(painter, x, y, self.SIZE, self.SIZE)

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
    finished_ok = Signal()

    def __init__(self, folder: str | list[str], db: Database, force: bool = False):
        super().__init__()
        self.folder = folder
        self.db = db
        self.force = force
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        last_progress = 0.0

        def report_progress(done, total, name):
            nonlocal last_progress
            now = time.monotonic()
            if done == total or name.startswith("ERROR ") or now - last_progress >= 0.1:
                self.progress.emit(done, total, name)
                last_progress = now

        scan_folder(
            self.folder, self.db,
            progress_cb=report_progress,
            should_cancel=lambda: self._cancel,
            force=self.force,
        )
        self.finished_ok.emit()


class LibraryView(QWidget):
    STATUS_MAX_WIDTH = 400  # cap how much horizontal space the status text can claim

    def __init__(
        self, db: Database, on_library_changed=None, on_play_track=None,
        get_library_folders=None, set_library_folders=None, on_queue_track=None,
        on_add_to_playlist=None,
        is_track_played=None, get_visible_columns=None, set_visible_columns=None, parent=None,
    ):
        super().__init__(parent)
        self.db = db
        self.on_library_changed = on_library_changed
        self.on_play_track = on_play_track
        self.get_library_folders = get_library_folders or (lambda: [])
        self.set_library_folders = set_library_folders or (lambda folders: None)
        self.on_queue_track = on_queue_track
        self.on_add_to_playlist = on_add_to_playlist
        self.is_track_played = is_track_played or (lambda _track_id: False)
        self.on_favorite_changed = None
        self.get_visible_columns = get_visible_columns or (lambda: None)
        self.set_visible_columns = set_visible_columns or (lambda _columns: None)
        self.tracks: list[Track] = []
        self._search_cache: dict[int, str] = {}
        self._resolved_paths: dict[int, Path] = {}
        self._duplicate_counts: dict[tuple[str, str], int] = {}
        self._matched_tracks: list[Track] = []
        self._matched_query = ""
        self._matched_folder = None
        self._readable: dict[int, bool] = {}
        self._mini_peaks: dict[int, tuple] = {}
        self.worker: ScanWorker | None = None
        self._refresh_pending = False
        self._pending_folders: list[str] = []
        self.playing_track_id: int | None = None
        self._playing_bg = "#8686AC"
        self.waveform_dialog: WaveformDialog | None = None
        self._wave_bg = "#33334d"
        self._wave_bass = "#8686AC"
        self._wave_treble = "#d8d8ec"

        self.scan_btn = QPushButton(" Scan Folders...")
        self.scan_btn.setIcon(icon("folder"))
        self.stop_btn = QPushButton(" Stop")
        self.stop_btn.setIcon(icon("stop"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        self.manage_folders_btn = QPushButton(" Manage Folders...")
        self.manage_folders_btn.setIcon(icon("folder"))
        self.folder_combo = QComboBox()
        self.folder_combo.addItem("All folders", None)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search library...")
        self.search_edit.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self.refresh_table)
        self.status_label = QLabel("")
        self.status_label.setMaximumWidth(self.STATUS_MAX_WIDTH)
        self.columns_btn = QToolButton()
        self.columns_btn.setIcon(icon("columns"))
        self.columns_btn.setToolTip("Choose visible columns")
        self.columns_btn.setAutoRaise(True)
        self.columns_btn.setPopupMode(QToolButton.InstantPopup)
        self.columns_btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
        columns_menu = QMenu(self.columns_btn)
        saved_columns = self.get_visible_columns()
        visible_columns = set(saved_columns) if saved_columns else set(COLUMNS)
        self._column_actions = []
        for column, name in enumerate(COLUMNS):
            label = "Favorite" if column == FAV_COL else name
            action = columns_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(name in visible_columns or column == TITLE_COL)
            action.setEnabled(column != TITLE_COL)
            action.toggled.connect(lambda visible, column=column: self._set_column_visible(column, visible))
            self._column_actions.append(action)
        self.columns_btn.setMenu(columns_menu)
        self.explorer_btn = QToolButton()
        self.explorer_btn.setIcon(icon("folder"))
        self.explorer_btn.setToolTip("Show selected song in File Explorer")
        self.explorer_btn.setEnabled(False)
        self.explorer_btn.clicked.connect(self._show_selected_in_explorer)
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
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setSortIndicator(TITLE_COL, Qt.AscendingOrder)
        self.table.horizontalHeader().sortIndicatorChanged.connect(self._sort_changed)
        self._fav_delegate = FavoriteDelegate(self.table)
        self.table.setItemDelegateForColumn(FAV_COL, self._fav_delegate)
        self.table.horizontalHeader().setStretchLastSection(True)
        self._wave_delegate = MiniWaveformDelegate(self._wave_bass, self._wave_treble, self._wave_bg, self.table)
        self.table.setItemDelegateForColumn(WAVEFORM_COL, self._wave_delegate)
        for col, width in enumerate([30, 220, 100, 100, 100, 60, 70, 70, 70, 100, 145, 75]):
            self.table.setColumnWidth(col, width)
        for column, action in enumerate(self._column_actions):
            self.table.setColumnHidden(column, not action.isChecked())
        self.table.doubleClicked.connect(self._play_selected)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.itemSelectionChanged.connect(self._update_explorer_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.progress_bar)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        status_row.addWidget(self.explorer_btn)
        status_row.addWidget(self.columns_btn)
        layout.addLayout(status_row)
        layout.addWidget(self.table)

        self.scan_btn.clicked.connect(self.choose_folder)
        self.stop_btn.clicked.connect(self.stop_scan)
        self.manage_folders_btn.clicked.connect(self.manage_folders)
        self.folder_combo.currentIndexChanged.connect(self.refresh_table)
        self.search_edit.textChanged.connect(lambda _text: self._search_timer.start())

        self._refresh_folder_combo(self.get_library_folders())
        self.refresh_from_db()

    def _set_status(self, text: str) -> None:
        """Set the status label, eliding long text and keeping the full text as a tooltip."""
        metrics = self.status_label.fontMetrics()
        elided = metrics.elidedText(text, Qt.ElideRight, self.STATUS_MAX_WIDTH)
        self.status_label.setText(elided)
        self.status_label.setToolTip(text if elided != text else "")

    def choose_folder(self):
        dialog = QFileDialog(self, "Select music folders")
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        for view in dialog.findChildren(QListView) + dialog.findChildren(QTreeView):
            view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        if not dialog.exec():
            return
        folders = list(dict.fromkeys(dialog.selectedFiles()))
        if not folders:
            return
        force = self._prompt_rescan_mode(folders)
        if force is None:
            return  # user cancelled
        self._pending_folders = folders
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.scan_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.stop_btn.setVisible(True)
        self.worker = ScanWorker(folders, self.db, force=force)
        self.worker.progress.connect(self._on_progress)
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

    def _prompt_rescan_mode(self, folders: list[str]):
        """If any folder was already scanned before, ask whether to fully rescan or only
        analyze new/changed files. Returns True (force), False (incremental), or None (cancel).
        """
        existing = self.db.get_existing_filepaths()
        already_scanned = any(
            is_within_folder(path, folder)
            for path in existing for folder in folders
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
            self._set_status("Stopping...")

    def _set_column_visible(self, column: int, visible: bool):
        self.table.setColumnHidden(column, not visible)
        self.set_visible_columns([
            COLUMNS[index] for index, action in enumerate(self._column_actions) if action.isChecked()
        ])

    def _on_progress(self, done, total, name):
        if total:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(done)
        self._set_status(f"Analyzing ({done}/{total}): {name}")

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
        self.stop_btn.setVisible(False)
        self._set_status("Scan complete.")
        for folder in self._pending_folders:
            self._register_scanned_folder(folder)
        self._pending_folders = []
        self.refresh_from_db()
        if self.on_library_changed:
            self.on_library_changed()

    def refresh_from_db(self):
        self.tracks = self.db.get_all_tracks()
        self._search_cache = {
            track.id: " ".join((
                track.title, track.artist, track.album, track.genre, track.key_name,
                track.camelot, f"{track.tempo:.0f}", track.filepath,
            )).casefold()
            for track in self.tracks
        }
        self._resolved_paths = {track.id: Path(track.filepath).resolve() for track in self.tracks}
        self._readable = {track.id: is_readable_file(track.filepath) for track in self.tracks}
        self._mini_peaks = {
            track.id: reduce_peaks(track.waveform_peaks or track.waveform_low) for track in self.tracks
        }
        self._duplicate_counts = {}
        for track in self.tracks:
            key = (track.artist.strip().casefold(), track.title.strip().casefold())
            if any(key):
                self._duplicate_counts[key] = self._duplicate_counts.get(key, 0) + 1
        self._matched_query = ""
        self._matched_folder = None
        self._matched_tracks = []
        self.refresh_table()

    def refresh_table(self):
        query = self.search_edit.text().strip().casefold()
        folder_filter = self.folder_combo.currentData()
        rows = self._matched_tracks if (
            query and query.startswith(self._matched_query)
            and folder_filter == self._matched_folder
        ) else self.tracks
        if folder_filter:
            if rows is self.tracks:
                folder_path = Path(folder_filter).resolve()
                rows = [t for t in rows if self._resolved_paths[t.id].is_relative_to(folder_path)]
        if query:
            rows = [t for t in rows if query in self._search_cache.get(t.id, "")]
        column = self.table.horizontalHeader().sortIndicatorSection()
        order = self.table.horizontalHeader().sortIndicatorOrder()
        def sort_value(track):
            if column == FAV_COL:
                return track.favorite
            if column == BPM_COL:
                return track.tempo
            if column == 8:
                return track.energy
            if column == 9:
                return track.duration
            if column == 10:
                return track.added_at or ""
            if column == 11:
                return self._duplicate_counts.get((track.artist.strip().casefold(), track.title.strip().casefold()), 0)
            return (track.title, track.artist, track.album, track.genre, track.key_name, track.camelot)[
                {TITLE_COL: 0, 2: 1, 3: 2, 4: 3, 6: 4, 7: 5}.get(column, 0)
            ].casefold()
        rows = sorted(rows, key=sort_value, reverse=order == Qt.DescendingOrder)
        self._matched_tracks = rows
        self._matched_query = query
        self._matched_folder = folder_filter
        self._render_rows(rows)
        self._set_status(f"Showing {len(rows)} track(s)")

    def _sort_changed(self, _column, _order):
        self.refresh_table()

    def _render_rows(self, rows):
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r in range(len(rows)):
            t = rows[r]
            values = [
                "", t.title, t.artist, t.album, t.genre, f"{t.tempo:.0f}", t.key_name,
                t.camelot, f"{t.energy:.1f}", t.duration_str, (t.added_at or "")[:19].replace("T", " "),
                str(self._duplicate_counts.get((t.artist.strip().casefold(), t.title.strip().casefold()), 0) or ""), "",
            ]
            for c, val in enumerate(values):
                if c == WAVEFORM_COL:
                    continue

                if c == FAV_COL:
                    item = make_favorite_item(t.favorite)
                elif c == BPM_COL:
                    item = NumericTableWidgetItem(val)
                    item.setData(Qt.UserRole, float(t.tempo))
                elif c in (8, 9, 11):
                    item = NumericTableWidgetItem(val)
                    item.setData(Qt.UserRole, (t.energy if c == 8 else t.duration if c == 9 else
                                                self._duplicate_counts.get((t.artist.strip().casefold(), t.title.strip().casefold()), 0)))
                else:
                    item = QTableWidgetItem(val)

                if c == WAVEFORM_COL - 1 and val:
                    item.setToolTip(f"{val} files match this artist and title")

                item.setData(1000, t.id)

                if c == TITLE_COL:
                    item.setIcon(cover_pixmap(t.cover_path, 24))
                    if self.is_track_played(t.id):
                        item.setForeground(QBrush(QColor(PLAYED_COLOR)))
                    elif not self._readable.get(t.id, True):
                        item.setForeground(QBrush(QColor(UNAVAILABLE_COLOR)))

                self.table.setItem(r, c, item)
            id_item = QTableWidgetItem("")
            id_item.setData(1000, t.id)
            id_item.setData(MiniWaveformDelegate.PEAKS_ROLE, self._mini_peaks.get(t.id, ()))
            id_item.setToolTip("Click to open the full waveform")
            self.table.setItem(r, WAVEFORM_COL, id_item)
        self.table.setSortingEnabled(True)
        self.table.setUpdatesEnabled(True)
        self._apply_playing_marker()

    def get_row_ids(self) -> list[int]:
        """Track ids in the current on-screen row order (respects search filter/sorting)."""
        ids = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                ids.append(item.data(1000))
        return ids

    def focus_track(self, track_id: int) -> bool:
        """Select and center a track, clearing filters only when they hide it."""
        row_ids = self.get_row_ids()
        if track_id not in row_ids:
            self.search_edit.clear()
            self._search_timer.stop()
            self.folder_combo.setCurrentIndex(0)
            self.refresh_table()
            row_ids = self.get_row_ids()
        if track_id not in row_ids:
            return False
        row = row_ids.index(track_id)
        self.table.setCurrentCell(row, TITLE_COL)
        self.table.selectRow(row)
        self.table.scrollToItem(
            self.table.item(row, TITLE_COL), QAbstractItemView.PositionAtCenter,
        )
        return True

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

    def refresh_played_state(self):
        tracks = {track.id: track for track in self.tracks}
        for row in range(self.table.rowCount()):
            id_item = self.table.item(row, FAV_COL)
            title_item = self.table.item(row, TITLE_COL)
            if not id_item or not title_item:
                continue
            track = tracks.get(id_item.data(1000))
            if self.is_track_played(id_item.data(1000)):
                title_item.setForeground(QBrush(QColor(PLAYED_COLOR)))
            elif track and not self._readable.get(track.id, True):
                title_item.setForeground(QBrush(QColor(UNAVAILABLE_COLOR)))
            else:
                title_item.setForeground(QBrush())

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

    def _on_cell_clicked(self, row: int, col: int):
        if col == WAVEFORM_COL:
            item = self.table.item(row, WAVEFORM_COL)
            track = self.db.get_track(item.data(1000)) if item else None
            if track:
                self._show_waveform(track)
            return
        if col != FAV_COL:
            return
        item = self.table.item(row, FAV_COL)
        if not item:
            return
        track_id = item.data(1000)
        track = next((t for t in self.tracks if t.id == track_id), None)
        if not track:
            return
        track.favorite = not track.favorite
        self.db.set_favorite(track_id, track.favorite)
        set_favorite_item(item, track.favorite)
        if self.on_favorite_changed:
            self.on_favorite_changed(track_id, track.favorite)

    def set_favorite_state(self, track_id: int, favorite: bool):
        for track in self.tracks:
            if track.id == track_id:
                track.favorite = favorite
        for row in range(self.table.rowCount()):
            item = self.table.item(row, FAV_COL)
            if item and item.data(1000) == track_id:
                set_favorite_item(item, favorite)

    def _show_waveform(self, track: Track):
        if self.waveform_dialog is None:
            self.waveform_dialog = WaveformDialog(self,
                bg_color=self._wave_bg,
                bass_color=self._wave_bass,
                treble_color=self._wave_treble,
            )
        self.waveform_dialog.load_track(track)

    def _selected_track(self):
        rows = self.table.selectionModel().selectedRows()
        item = self.table.item(rows[0].row(), FAV_COL) if rows else None
        return self.db.get_track(item.data(1000)) if item else None

    def _update_explorer_button(self):
        track = self._selected_track()
        self.explorer_btn.setEnabled(bool(track and is_readable_file(track.filepath)))

    def _show_selected_in_explorer(self):
        track = self._selected_track()
        if track:
            show_in_file_explorer(track.filepath)

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        if not self.table.selectionModel().isRowSelected(row, self.table.rootIndex()):
            self.table.selectRow(row)
        item = self.table.item(row, 0)
        track = self.db.get_track(item.data(1000)) if item else None
        if not track:
            return
        selected_rows = sorted(index.row() for index in self.table.selectionModel().selectedRows())
        menu = QMenu(self)
        bpm_action = menu.addAction("Edit BPM...")
        key_action = menu.addAction("Edit Key...")
        metadata_action = menu.addAction("Edit Metadata...")
        waveform_action = menu.addAction("Show Waveform...")
        add_to_playlist_action = None
        if self.on_add_to_playlist:
            add_to_playlist_action = menu.addAction("Add to Playlist Builder")
        queue_action = menu.addAction(
            "Add Selected to Queue" if len(selected_rows) > 1 else "Add to Queue"
        )
        explorer_action = menu.addAction("Show in File Explorer")
        explorer_action.setEnabled(is_readable_file(track.filepath))
        menu.addSeparator()
        remove_action = menu.addAction("Remove from Library")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == bpm_action and edit_bpm(self, self.db, track):
            self.refresh_from_db()
        elif chosen == key_action and edit_key(self, self.db, track):
            self.refresh_from_db()
        elif chosen == metadata_action and edit_metadata(self, self.db, track):
            self.refresh_from_db()
        elif chosen == waveform_action:
            self._show_waveform(track)
        elif add_to_playlist_action is not None and chosen == add_to_playlist_action:
            self.on_add_to_playlist(track)
        elif chosen == explorer_action:
            show_in_file_explorer(track.filepath)
        elif chosen == queue_action and self.on_queue_track:
            selected_tracks = []
            for selected_row in selected_rows:
                selected_item = self.table.item(selected_row, FAV_COL)
                selected_track = self.db.get_track(selected_item.data(1000)) if selected_item else None
                if selected_track:
                    selected_tracks.append(selected_track)
            self.on_queue_track(selected_tracks)
        elif chosen == remove_action:
            selected_ids = {
                self.table.item(selected_row.row(), FAV_COL).data(1000)
                for selected_row in self.table.selectionModel().selectedRows()
                if self.table.item(selected_row.row(), FAV_COL)
            }
            self._remove_tracks([t for t in self.tracks if t.id in selected_ids])

    def _remove_track(self, track: Track):
        self._remove_tracks([track])

    def remove_duplicate_tracks(self) -> int:
        return self._remove_tracks(duplicate_tracks(self.tracks))

    def _remove_tracks(self, tracks: list[Track]) -> int | None:
        if not tracks:
            return 0
        count = len(tracks)
        names = f"{count} tracks" if count > 1 else f"'{tracks[0].display_name}'"
        answer = QMessageBox.question(
            self, "Remove from Library",
            f"Remove {names} from the library?\n\n"
            "The file itself will not be deleted from disk.",
        )
        if answer != QMessageBox.Yes:
            return None
        self.db.delete_tracks(track.id for track in tracks)
        if self.on_library_changed:
            self.on_library_changed()
        else:
            self.refresh_from_db()
        return count

    def apply_theme(self, bg: str | None = None, bass: str | None = None, treble: str | None = None):
        """Refresh flat icons (colors are set globally in icon_loader before calling this)."""
        self.scan_btn.setIcon(icon("folder"))
        self.stop_btn.setIcon(icon("stop"))
        self.manage_folders_btn.setIcon(icon("folder"))
        self.columns_btn.setIcon(icon("columns"))
        if bg and bass and treble:
            self._wave_bg, self._wave_bass, self._wave_treble = bg, bass, treble
            self._wave_delegate.set_theme(bass, treble, bg)
            self.table.viewport().update()