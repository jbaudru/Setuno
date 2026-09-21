"""Playlist builder tab: filters, generation controls, ordered results, export & stats."""

import random

from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QGridLayout,
)

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ..core.database import Database

from ..core.exporter import export_m3u, export_ordered_copies

from ..core.playlist_engine import (
    MODE_LABELS,
    ALL_MODES,
    MODE_FIXED_TEMPO,
    MODE_FIXED_ENERGY,
    generate_playlist,
)

from ..core.relocator import find_missing, relocate_tracks
from ..core.scanner import is_within_folder

from .icon_loader import icon, cover_pixmap
from .library_view import ScanWorker
from .stats_widget import StatsWidget
from .track_edit import edit_bpm, edit_key, edit_metadata
from .waveform_view import MiniWaveform, WaveformDialog


RESULT_COLUMNS = [
    "#",
    "Title",
    "Artist",
    "BPM",
    "Key",
    "Camelot",
    "Energy",
    "Duration",
    "Waveform",
]

WAVEFORM_COL = len(RESULT_COLUMNS) - 1

TEMPO_FIXED_TOLERANCE = 4.0  # +/- BPM window applied around a fixed tempo value
ENERGY_FIXED_TOLERANCE = 1.0  # +/- window applied around a fixed energy value


class _ReorderableTable(QTableWidget):
    """Results table supporting drag-and-drop row reordering."""

    def __init__(self, *args, on_reordered=None, **kwargs):
        super().__init__(*args, **kwargs)

        self._on_reordered = on_reordered

        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)

    def dropEvent(self, event):
        super().dropEvent(event)

        if self._on_reordered:
            self._on_reordered()


class PlaylistBuilder(QWidget):

    def __init__(
        self,
        db: Database,
        on_play_track=None,
        on_library_changed=None,
        parent=None,
    ):
        super().__init__(parent)

        self.db = db
        self.on_play_track = on_play_track
        self.on_library_changed = on_library_changed

        self.current_playlist = []
        self.folder_filter: str | None = None
        self.folder_scan_worker: ScanWorker | None = None
        self.playing_track_id: int | None = None
        self.waveform_dialog: WaveformDialog | None = None

        self._wave_bg = "#33334d"
        self._wave_bass = "#8686AC"
        self._wave_treble = "#d8d8ec"

        # ---------------------------------------------------------
        # Folder filter
        # ---------------------------------------------------------

        self.folder_filter_btn = QPushButton(" Filter by Folder...")
        self.folder_filter_btn.setIcon(icon("folder"))
        self.folder_filter_btn.clicked.connect(self._choose_folder_filter)

        self.folder_filter_label = QLabel("All library")

        self.folder_filter_clear_btn = QPushButton(" Clear")
        self.folder_filter_clear_btn.setEnabled(False)
        self.folder_filter_clear_btn.clicked.connect(self._clear_folder_filter)

        self.folder_scan_progress = QProgressBar()
        self.folder_scan_progress.setVisible(False)

        # ---------------------------------------------------------
        # Playlist controls
        # ---------------------------------------------------------

        self.length_mode_combo = QComboBox()
        self.length_mode_combo.addItem("Set length", "duration")
        self.length_mode_combo.addItem("Number of songs", "count")
        self.length_mode_combo.currentIndexChanged.connect(
            self._update_field_modes
        )

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(0, 480)
        self.duration_spin.setValue(60)
        self.duration_spin.setSuffix(" min")
        self.duration_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)

        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 999)
        self.count_spin.setValue(10)
        self.count_spin.setSuffix(" songs")
        self.count_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)

        self.mode_combo = QComboBox()

        for m in ALL_MODES:
            self.mode_combo.addItem(MODE_LABELS[m], m)

        self.mode_combo.currentIndexChanged.connect(
            self._update_field_modes
        )

        # ---------------------------------------------------------
        # Tempo
        # ---------------------------------------------------------

        self.tempo_fixed = QDoubleSpinBox()
        self.tempo_fixed.setRange(0, 300)
        self.tempo_fixed.setValue(120)
        self.tempo_fixed.setSuffix(" BPM")
        self.tempo_fixed.setButtonSymbols(QAbstractSpinBox.NoButtons)

        self.tempo_min = QDoubleSpinBox()
        self.tempo_min.setRange(0, 300)
        self.tempo_min.setValue(0)
        self.tempo_min.setButtonSymbols(QAbstractSpinBox.NoButtons)

        self.tempo_max = QDoubleSpinBox()
        self.tempo_max.setRange(0, 300)
        self.tempo_max.setValue(300)
        self.tempo_max.setButtonSymbols(QAbstractSpinBox.NoButtons)

        self.tempo_range_widget = QWidget()

        tempo_range_layout = QHBoxLayout(self.tempo_range_widget)
        tempo_range_layout.setContentsMargins(0, 0, 0, 0)
        tempo_range_layout.setSpacing(5)

        tempo_range_layout.addWidget(self.tempo_min)
        tempo_range_layout.addWidget(QLabel("-"))
        tempo_range_layout.addWidget(self.tempo_max)

        self.tempo_fixed_widget = QWidget()

        tempo_fixed_layout = QHBoxLayout(self.tempo_fixed_widget)
        tempo_fixed_layout.setContentsMargins(0, 0, 0, 0)
        tempo_fixed_layout.setSpacing(5)

        tempo_fixed_layout.addWidget(self.tempo_fixed)

        # ---------------------------------------------------------
        # Energy
        # ---------------------------------------------------------

        self.energy_fixed = QDoubleSpinBox()
        self.energy_fixed.setRange(0, 10)
        self.energy_fixed.setValue(5)
        self.energy_fixed.setButtonSymbols(QAbstractSpinBox.NoButtons)

        self.energy_min = QDoubleSpinBox()
        self.energy_min.setRange(0, 10)
        self.energy_min.setValue(0)
        self.energy_min.setButtonSymbols(QAbstractSpinBox.NoButtons)

        self.energy_max = QDoubleSpinBox()
        self.energy_max.setRange(0, 10)
        self.energy_max.setValue(10)
        self.energy_max.setButtonSymbols(QAbstractSpinBox.NoButtons)

        self.energy_range_widget = QWidget()

        energy_range_layout = QHBoxLayout(self.energy_range_widget)
        energy_range_layout.setContentsMargins(0, 0, 0, 0)
        energy_range_layout.setSpacing(5)

        energy_range_layout.addWidget(self.energy_min)
        energy_range_layout.addWidget(QLabel("-"))
        energy_range_layout.addWidget(self.energy_max)

        self.energy_fixed_widget = QWidget()

        energy_fixed_layout = QHBoxLayout(self.energy_fixed_widget)
        energy_fixed_layout.setContentsMargins(0, 0, 0, 0)
        energy_fixed_layout.setSpacing(5)

        energy_fixed_layout.addWidget(self.energy_fixed)

        # ---------------------------------------------------------
        # Other options
        # ---------------------------------------------------------

        self.genre_combo = QComboBox()
        self.genre_combo.addItem("All genres", None)

        self.harmonic_check = QCheckBox("Harmonic mixing")
        self.harmonic_check.setChecked(True)

        self.generate_btn = QPushButton("Generate")
        self.generate_btn.setObjectName("generateButton")
        self.generate_btn.clicked.connect(self.generate)

        # ---------------------------------------------------------
        # Playlist parameters layout
        # ---------------------------------------------------------

        filters_box = QGroupBox("Playlist parameters")

        params = QGridLayout()
        params.setContentsMargins(10, 8, 10, 8)
        params.setHorizontalSpacing(10)
        params.setVerticalSpacing(6)

        # Target
        target_label = QLabel("Target")

        target_layout = QHBoxLayout()
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.setSpacing(6)

        target_layout.addWidget(self.length_mode_combo)
        target_layout.addWidget(self.duration_spin)
        target_layout.addWidget(self.count_spin)

        target_widget = QWidget()
        target_widget.setLayout(target_layout)

        params.addWidget(target_label, 0, 0)
        params.addWidget(target_widget, 0, 1, 1, 3)

        # Matching
        matching_label = QLabel("Matching")

        matching_layout = QHBoxLayout()
        matching_layout.setContentsMargins(0, 0, 0, 0)
        matching_layout.setSpacing(6)

        matching_layout.addWidget(self.mode_combo)
        matching_layout.addWidget(self.genre_combo)

        matching_widget = QWidget()
        matching_widget.setLayout(matching_layout)

        params.addWidget(matching_label, 1, 0)
        params.addWidget(matching_widget, 1, 1, 1, 3)

        # Tempo
        tempo_label = QLabel("Tempo")

        tempo_layout = QHBoxLayout()
        tempo_layout.setContentsMargins(0, 0, 0, 0)
        tempo_layout.setSpacing(6)

        tempo_layout.addWidget(self.tempo_range_widget)
        tempo_layout.addWidget(self.tempo_fixed_widget)

        tempo_widget = QWidget()
        tempo_widget.setLayout(tempo_layout)

        params.addWidget(tempo_label, 2, 0)
        params.addWidget(tempo_widget, 2, 1, 1, 3)

        # Energy
        energy_label = QLabel("Energy")

        energy_layout = QHBoxLayout()
        energy_layout.setContentsMargins(0, 0, 0, 0)
        energy_layout.setSpacing(6)

        energy_layout.addWidget(self.energy_range_widget)
        energy_layout.addWidget(self.energy_fixed_widget)

        energy_widget = QWidget()
        energy_widget.setLayout(energy_layout)

        params.addWidget(energy_label, 3, 0)
        params.addWidget(energy_widget, 3, 1, 1, 3)

        # Bottom options + Generate
        options_layout = QHBoxLayout()
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(12)

        options_layout.addWidget(self.harmonic_check)
        options_layout.addStretch()

        self.generate_btn.setMinimumHeight(32)
        self.generate_btn.setMinimumWidth(110)
        self.generate_btn.setSizePolicy(
            QSizePolicy.Fixed,
            QSizePolicy.Fixed,
        )

        options_layout.addWidget(self.generate_btn)

        options_widget = QWidget()
        options_widget.setLayout(options_layout)

        params.addWidget(options_widget, 4, 0, 1, 4)

        params.setColumnMinimumWidth(0, 75)
        params.setColumnStretch(1, 1)
        params.setColumnStretch(2, 1)
        params.setColumnStretch(3, 1)

        filters_box.setLayout(params)

        # ---------------------------------------------------------
        # Results table
        # ---------------------------------------------------------

        self.table = _ReorderableTable(
            0,
            len(RESULT_COLUMNS),
            on_reordered=self._on_table_reordered,
        )

        self.table.setHorizontalHeaderLabels(RESULT_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )

        self.table.doubleClicked.connect(self._play_selected)

        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(
            self._show_context_menu
        )

        # ---------------------------------------------------------
        # Export / save controls
        # ---------------------------------------------------------

        self.save_m3u_btn = QPushButton(" Save as .m3u8")
        self.save_m3u_btn.setIcon(icon("save"))

        self.export_folder_btn = QPushButton(
            " Export ordered copies..."
        )
        self.export_folder_btn.setIcon(icon("export"))

        self.save_library_btn = QPushButton(" Save to library")
        self.save_library_btn.setIcon(icon("star"))

        for b in (
            self.save_m3u_btn,
            self.export_folder_btn,
            self.save_library_btn,
        ):
            b.setEnabled(False)

        self.save_m3u_btn.clicked.connect(self.save_m3u)
        self.export_folder_btn.clicked.connect(self.export_folder)
        self.save_library_btn.clicked.connect(self.save_to_library)

        actions = QHBoxLayout()
        actions.addWidget(self.save_m3u_btn)
        actions.addWidget(self.export_folder_btn)
        actions.addWidget(self.save_library_btn)
        actions.addStretch()

        results_box = QVBoxLayout()
        results_box.addWidget(self.table)
        results_box.addLayout(actions)

        results_widget = QWidget()
        results_widget.setLayout(results_box)

        # ---------------------------------------------------------
        # Analytics
        # ---------------------------------------------------------

        self.stats_widget = StatsWidget()

        self.stats_toggle_btn = QPushButton(" \u25b8  Analytics")
        self.stats_toggle_btn.setCheckable(True)
        self.stats_toggle_btn.setChecked(False)
        self.stats_toggle_btn.setFlat(True)
        self.stats_toggle_btn.setStyleSheet(
            "text-align: left; font-weight: bold;"
        )
        self.stats_toggle_btn.clicked.connect(self._toggle_stats)

        self.stats_widget.setVisible(False)

        analytics_box = QVBoxLayout()
        analytics_box.setContentsMargins(0, 0, 0, 0)
        analytics_box.addWidget(self.stats_toggle_btn)
        analytics_box.addWidget(self.stats_widget)

        analytics_widget = QWidget()
        analytics_widget.setLayout(analytics_box)

        # ---------------------------------------------------------
        # Results / analytics splitter
        # ---------------------------------------------------------

        splitter = QSplitter(Qt.Vertical)

        splitter.addWidget(results_widget)
        splitter.addWidget(analytics_widget)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        self.splitter = splitter

        # ---------------------------------------------------------
        # Main layout
        # ---------------------------------------------------------

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Folder filter stays outside the playlist parameters.
        folder_row = QHBoxLayout()
        folder_row.setContentsMargins(0, 0, 0, 0)
        folder_row.setSpacing(6)

        folder_row.addWidget(self.folder_filter_btn)
        folder_row.addWidget(self.folder_filter_label, 1)
        folder_row.addWidget(self.folder_filter_clear_btn)
        folder_row.addWidget(self.folder_scan_progress)

        layout.addLayout(folder_row)
        layout.addWidget(filters_box)
        layout.addWidget(splitter, 1)

        self.refresh_genres()
        self._update_field_modes()

    def _toggle_stats(self, checked: bool):
        self.stats_widget.setVisible(checked)
        arrow = "\u25be" if checked else "\u25b8"
        self.stats_toggle_btn.setText(f" {arrow}  Analytics")

    def _update_field_modes(self):
        mode = self.mode_combo.currentData()
        tempo_fixed = mode == MODE_FIXED_TEMPO
        energy_fixed = mode == MODE_FIXED_ENERGY
        self.tempo_fixed_widget.setVisible(tempo_fixed)
        self.tempo_range_widget.setVisible(not tempo_fixed)
        self.energy_fixed_widget.setVisible(energy_fixed)
        self.energy_range_widget.setVisible(not energy_fixed)

        by_count = self.length_mode_combo.currentData() == "count"
        self.duration_spin.setVisible(not by_count)
        self.count_spin.setVisible(by_count)

    def _tempo_range(self):
        if self.mode_combo.currentData() == MODE_FIXED_TEMPO:
            v = self.tempo_fixed.value()
            return (max(0, v - TEMPO_FIXED_TOLERANCE), v + TEMPO_FIXED_TOLERANCE)
        return (self.tempo_min.value(), self.tempo_max.value())

    def _energy_range(self):
        if self.mode_combo.currentData() == MODE_FIXED_ENERGY:
            v = self.energy_fixed.value()
            return (max(0, v - ENERGY_FIXED_TOLERANCE), v + ENERGY_FIXED_TOLERANCE)
        return (self.energy_min.value(), self.energy_max.value())

    def refresh_genres(self):
        genres = sorted({t.genre for t in self.db.get_all_tracks() if t.genre})
        self.genre_combo.clear()
        self.genre_combo.addItem("All genres", None)
        for g in genres:
            self.genre_combo.addItem(g, g)

    def _choose_folder_filter(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder to build the playlist from")
        if not folder:
            return
        self.folder_filter_btn.setEnabled(False)
        self.folder_filter_clear_btn.setEnabled(False)
        self.folder_scan_progress.setVisible(True)
        self.folder_scan_progress.setValue(0)
        self.folder_filter_label.setText(f"Scanning {folder} ...")
        # Incremental scan: analyzes only new/changed files and saves them to the library for later use.
        self.folder_scan_worker = ScanWorker(folder, self.db, force=False)
        self.folder_scan_worker.progress.connect(self._on_folder_scan_progress)
        self.folder_scan_worker.finished_ok.connect(lambda: self._on_folder_scan_done(folder))
        self.folder_scan_worker.start()

    def _on_folder_scan_progress(self, done, total, name):
        if total:
            self.folder_scan_progress.setMaximum(total)
            self.folder_scan_progress.setValue(done)
        self.folder_filter_label.setText(f"Scanning ({done}/{total}): {name}")

    def _on_folder_scan_done(self, folder: str):
        self.folder_filter = folder
        self.folder_scan_progress.setVisible(False)
        self.folder_filter_btn.setEnabled(True)
        self.folder_filter_clear_btn.setEnabled(True)
        self.folder_filter_label.setText(folder)
        self.refresh_genres()
        if self.on_library_changed:
            self.on_library_changed()

    def _clear_folder_filter(self):
        self.folder_filter = None
        self.folder_filter_label.setText("All library")
        self.folder_filter_clear_btn.setEnabled(False)

    def generate(self):
        library = self.db.get_all_tracks()
        if not library:
            QMessageBox.warning(self, "Empty library", "Scan a folder in the Library tab first.")
            return
        if self.folder_filter:
            library = [t for t in library if is_within_folder(t.filepath, self.folder_filter)]
            if not library:
                QMessageBox.warning(
                    self, "No tracks found",
                    "No analyzed tracks were found in the selected folder.",
                )
                return
        mode = self.mode_combo.currentData()
        genre = self.genre_combo.currentData()
        genres = [genre] if genre else None
        by_count = self.length_mode_combo.currentData() == "count"
        playlist = generate_playlist(
            library, mode, 0 if by_count else self.duration_spin.value(),
            genres=genres,
            tempo_range=self._tempo_range(),
            energy_range=self._energy_range(),
            harmonic_mixing=self.harmonic_check.isChecked(),
            track_count=self.count_spin.value() if by_count else None,
        )
        self.current_playlist = playlist
        self._populate_table(playlist)
        self.stats_widget.update_stats(playlist)
        enabled = bool(playlist)
        for b in (self.save_m3u_btn, self.export_folder_btn, self.save_library_btn):
            b.setEnabled(enabled)
        if not playlist:
            QMessageBox.information(self, "No matches", "No tracks match these filters.")

    def _populate_table(self, playlist):
        self.table.setRowCount(len(playlist))
        for r, t in enumerate(playlist):
            values = [
                str(r + 1), t.title, t.artist, f"{t.tempo:.1f}", t.key_name,
                t.camelot, f"{t.energy:.1f}", t.duration_str, "",
            ]
            for c, val in enumerate(values):
                if c == WAVEFORM_COL:
                    continue
                item = QTableWidgetItem(val)
                item.setData(1000, t.id)
                if c == 1:
                    item.setIcon(cover_pixmap(t.cover_path, 24))
                self.table.setItem(r, c, item)
            waveform = MiniWaveform(
                t.waveform_low, t.waveform_high, self._wave_bass, self._wave_treble, self._wave_bg,
                on_clicked=lambda track=t: self._show_waveform(track),
            )
            self.table.setCellWidget(r, WAVEFORM_COL, waveform)
        self._apply_playing_marker()

    def get_row_ids(self) -> list[int]:
        """Track ids in the current on-screen row order."""
        ids = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                ids.append(item.data(1000))
        return ids

    def _apply_playing_marker(self):
        """Bold the row of the currently playing track and prefix its title with ▶."""
        title_col = 1
        for r in range(self.table.rowCount()):
            id_item = self.table.item(r, 0)
            title_item = self.table.item(r, title_col)
            if not id_item or not title_item:
                continue
            is_playing = self.playing_track_id is not None and id_item.data(1000) == self.playing_track_id
            base_title = title_item.text().lstrip("\u25b6 ")
            title_item.setText(f"\u25b6 {base_title}" if is_playing else base_title)
            for c in range(self.table.columnCount()):
                cell = self.table.item(r, c)
                if cell:
                    font = QFont(cell.font())
                    font.setBold(is_playing)
                    cell.setFont(font)

    def set_playing_id(self, track_id: int | None):
        self.playing_track_id = track_id
        self._apply_playing_marker()

    def _on_table_reordered(self):
        """Re-sync `current_playlist` order after the user drags a row to a new position."""
        id_to_track = {t.id: t for t in self.current_playlist}
        new_order = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            tid = item.data(1000) if item else None
            track = id_to_track.get(tid)
            if track:
                new_order.append(track)
        if len(new_order) != len(self.current_playlist):
            return  # drop didn't produce a clean 1:1 row mapping; leave state untouched
        self.current_playlist = new_order
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                item.setText(str(r + 1))
        self.stats_widget.update_stats(self.current_playlist)

    def play_row(self, row: int):
        if row < 0 or row >= len(self.current_playlist) or not self.on_play_track:
            return
        self.on_play_track(self.current_playlist[row])

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
        if not self.current_playlist:
            return
        self.play_row(random.randrange(len(self.current_playlist)))

    def _play_selected(self):
        self.play_row(self.table.currentRow())

    def _show_waveform(self, track):
        if self.waveform_dialog is None:
            self.waveform_dialog = WaveformDialog(self)
        self.waveform_dialog.load_track(track)

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self.current_playlist):
            return
        track = self.current_playlist[row]
        menu = QMenu(self)
        bpm_action = menu.addAction("Edit BPM...")
        key_action = menu.addAction("Edit Key...")
        metadata_action = menu.addAction("Edit Metadata...")
        waveform_action = menu.addAction("Show Waveform...")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == bpm_action and edit_bpm(self, self.db, track):
            updated = self.db.get_track(track.id)
            if updated:
                self.current_playlist[row] = updated
                self._populate_table(self.current_playlist)
        elif chosen == key_action and edit_key(self, self.db, track):
            updated = self.db.get_track(track.id)
            if updated:
                self.current_playlist[row] = updated
                self._populate_table(self.current_playlist)
        elif chosen == metadata_action and edit_metadata(self, self.db, track):
            updated = self.db.get_track(track.id)
            if updated:
                self.current_playlist[row] = updated
                self._populate_table(self.current_playlist)
        elif chosen == waveform_action:
            self._show_waveform(track)

    def _ensure_files_available(self) -> bool:
        """Offer to relocate any moved/renamed files before exporting. Returns False to abort."""
        missing = find_missing(self.current_playlist)
        if not missing:
            return True
        answer = QMessageBox.question(
            self, "Missing files",
            f"{len(missing)} file(s) in this playlist could not be found on disk "
            "(they may have been moved or renamed).\n\n"
            "Locate them by searching a folder now?",
        )
        if answer != QMessageBox.Yes:
            return True  # proceed anyway; exporter will just skip/copy what it can
        search_folder = QFileDialog.getExistingDirectory(self, "Select folder to search for missing files")
        if not search_folder:
            return True
        found = relocate_tracks(missing, search_folder, self.db)
        still_missing = len(missing) - len(found)
        self._populate_table(self.current_playlist)
        if still_missing:
            QMessageBox.information(
                self, "Relocation results",
                f"Found {len(found)} of {len(missing)} file(s). "
                f"{still_missing} file(s) remain missing and may fail to export.",
            )
        return True

    def save_m3u(self):
        if not self._ensure_files_available():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save playlist", "playlist.m3u",
            "M3U Playlist (*.m3u);;M3U8 Playlist (*.m3u8)",
        )
        if path:
            export_m3u(self.current_playlist, path)
            QMessageBox.information(self, "Saved", f"Playlist saved to:\n{path}\n\nImportable in Rekordbox and similar DJ software.")

    def export_folder(self):
        if not self._ensure_files_available():
            return
        folder = QFileDialog.getExistingDirectory(self, "Select destination folder")
        if folder:
            files = export_ordered_copies(self.current_playlist, folder)
            QMessageBox.information(self, "Exported", f"{len(files)} file(s) copied to:\n{folder}")

    def save_to_library(self):
        name, ok = QInputDialog.getText(self, "Playlist name", "Name:")
        if not ok or not name.strip():
            return
        mode = self.mode_combo.currentData()
        by_count = self.length_mode_combo.currentData() == "count"
        params = {
            "length_mode": "count" if by_count else "duration",
            "duration_minutes": self.duration_spin.value(),
            "track_count": self.count_spin.value() if by_count else None,
            "tempo_range": list(self._tempo_range()),
            "energy_range": list(self._energy_range()),
            "harmonic_mixing": self.harmonic_check.isChecked(),
        }
        ids = [t.id for t in self.current_playlist]
        self.db.save_playlist(name.strip(), mode, params, ids)
        QMessageBox.information(self, "Saved", f"Playlist '{name}' saved to library.")

    def load_saved_playlist(self, record: dict):
        """Load a previously saved playlist (from the Saved Playlists tab) into the builder."""
        track_ids = record.get("track_ids", [])
        tracks = [self.db.get_track(tid) for tid in track_ids]
        tracks = [t for t in tracks if t]
        self.current_playlist = tracks
        self._populate_table(tracks)
        self.stats_widget.update_stats(tracks)

        idx = self.mode_combo.findData(record.get("mode"))
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)

        params = record.get("params", {})
        by_count = params.get("length_mode") == "count" and params.get("track_count")
        length_idx = self.length_mode_combo.findData("count" if by_count else "duration")
        if length_idx >= 0:
            self.length_mode_combo.setCurrentIndex(length_idx)
        if by_count:
            self.count_spin.setValue(params["track_count"])
        elif params.get("duration_minutes"):
            self.duration_spin.setValue(params["duration_minutes"])

        tempo_range = params.get("tempo_range")
        if tempo_range:
            self.tempo_min.setValue(tempo_range[0])
            self.tempo_max.setValue(tempo_range[1])
        energy_range = params.get("energy_range")
        if energy_range:
            self.energy_min.setValue(energy_range[0])
            self.energy_max.setValue(energy_range[1])
        self.harmonic_check.setChecked(params.get("harmonic_mixing", True))
        self._update_field_modes()

        enabled = bool(tracks)
        for b in (self.save_m3u_btn, self.export_folder_btn, self.save_library_btn):
            b.setEnabled(enabled)

    def apply_theme(self, bg: str | None = None, bass: str | None = None, treble: str | None = None):
        """Refresh flat icons (colors are set globally in icon_loader before calling this)."""
        self.folder_filter_btn.setIcon(icon("folder"))
        self.save_m3u_btn.setIcon(icon("save"))
        self.export_folder_btn.setIcon(icon("export"))
        self.save_library_btn.setIcon(icon("star"))
        if bg and bass and treble:
            self._wave_bg, self._wave_bass, self._wave_treble = bg, bass, treble
            for r in range(self.table.rowCount()):
                widget = self.table.cellWidget(r, WAVEFORM_COL)
                if isinstance(widget, MiniWaveform):
                    widget.set_theme(bass, treble, bg)
