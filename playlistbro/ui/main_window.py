"""Main application window: tabs, menu, embedded player docked at the bottom."""
from PySide6.QtGui import QActionGroup, QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QStatusBar, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget, QDialog, QLabel, QPushButton, QHBoxLayout
from PySide6.QtCore import Qt

from ..core.database import Database
from ..core.settings import load_settings, save_settings
from . import icon_loader
from .icon_loader import app_icon
from .library_view import LibraryView
from .level_meter import StereoLevelMeter
from .graph_view import SimilarityGraphView
from .player_widget import PlayerWidget
from .playlist_builder import PlaylistBuilder
from .playlists_view import SavedPlaylistsView
from .queue_view import QueueView
from .theme import PALETTES, build_stylesheet, icon_color


class QueueTabButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__("Queue", parent)
        self.setObjectName("queueTabButton")
        self.setCheckable(True)
        self.setMinimumWidth(104)
        self.badge = QLabel(self)
        self.badge.setObjectName("queueBadge")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.badge.hide()

    def set_count(self, count: int):
        if count <= 0:
            self.badge.hide()
            return
        self.badge.setText(str(count) if count < 100 else "99+")
        width = max(18, self.badge.fontMetrics().horizontalAdvance(self.badge.text()) + 8)
        self.badge.setFixedSize(width, 18)
        self.badge.show()
        self._position_badge()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_badge()

    def _position_badge(self):
        self.badge.move(self.width() - self.badge.width() - 8, 3)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Setuno")
        self.setWindowIcon(app_icon())
        self.resize(1200, 800)

        self.db = Database()
        self.settings = load_settings()
        self.settings.pop("played_track_ids", None)
        self.played_history_ids = []
        self.played_track_ids = set()
        self._resume_source = None
        self._resume_track_id = None
        self.theme = self.settings.get("theme", "dark")
        icon_loader.set_theme_color(icon_color(self.theme))
        self.active_source: str | None = None  # 'library' or 'builder': where the current track came from

        self.player = PlayerWidget()
        self.level_meter = StereoLevelMeter()
        self.player.audio_levels_changed.connect(self.level_meter.set_levels)
        self.player.track_finished.connect(self._on_track_finished)
        self.player.next_requested.connect(self._on_next_requested)
        self.player.prev_requested.connect(self._on_prev_requested)
        self.player.crossfade_requested.connect(self._on_crossfade_requested)
        self.player.track_transitioned.connect(self._on_track_transitioned)
        self.player.on_request_track = self._play_random_from_active_tab

        self.queue_view = QueueView(
            self.db, is_track_played=lambda track_id: track_id in self.played_track_ids,
            on_play_track=self._play_from_queue,
        )
        self.library_view = LibraryView(
            self.db,
            on_library_changed=self._on_library_changed,
            on_play_track=lambda t: self._on_track_played("library", t),
            get_library_folders=lambda: self.settings.get("library_folders", []),
            set_library_folders=self._set_library_folders,
            on_queue_track=self._add_to_queue,
            is_track_played=lambda track_id: track_id in self.played_track_ids,
            get_visible_columns=lambda: self.settings.get("library_visible_columns"),
            set_visible_columns=self._set_library_visible_columns,
        )
        self.playlist_builder = PlaylistBuilder(
            self.db, on_play_track=lambda t: self._on_track_played("builder", t),
            on_library_changed=self._on_library_changed,
            on_queue_track=self._add_to_queue,
            is_track_played=lambda track_id: track_id in self.played_track_ids,
        )
        self.saved_playlists_view = SavedPlaylistsView(self.db, on_open=self._open_saved_playlist)
        self.graph_view = SimilarityGraphView(
            self.db, on_play_track=lambda t: self._on_track_played("graph", t),
            on_queue_track=self._add_to_queue,
        )
        self.graph_view.set_played_ids(self.played_track_ids)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.library_view, "Library")
        self.tabs.addTab(self.playlist_builder, "Playlist Builder")
        self.tabs.addTab(self.saved_playlists_view, "Saved Playlists")
        self.tabs.addTab(self.graph_view, "Similarity Graph")
        queue_index = self.tabs.addTab(self.queue_view, "Queue")
        self.tabs.tabBar().setTabVisible(queue_index, False)
        self.queue_tab_btn = QueueTabButton()
        self.queue_tab_btn.clicked.connect(self._show_queue_tab)
        self.queue_view.count_changed.connect(self.queue_tab_btn.set_count)
        self.queue_tab_btn.set_count(len(self.queue_view))
        queue_corner = QWidget()
        queue_corner_layout = QHBoxLayout(queue_corner)
        queue_corner_layout.setContentsMargins(0, 0, 6, 0)
        queue_corner_layout.setSpacing(0)
        queue_corner_layout.addWidget(self.queue_tab_btn)
        self.tabs.setCornerWidget(queue_corner, Qt.TopRightCorner)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        workspace = QHBoxLayout()
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(2)
        workspace.addWidget(self.tabs, 1)
        workspace.addWidget(self.level_meter)
        workspace.setStretch(0, 1)
        workspace.setStretch(1, 0)
        layout.addLayout(workspace, 1)
        layout.addWidget(self.player, 0)
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar())
        self._build_menu()
        self._build_tray_controls()
        self._apply_theme(self.theme, persist=False)

    def _build_menu(self):
        menu = self.menuBar().addMenu("&File")
        scan_action = menu.addAction("Scan Folder...")
        scan_action.triggered.connect(self.library_view.choose_folder)
        menu.addSeparator()
        exit_action = menu.addAction("Exit")
        exit_action.triggered.connect(self.close)

        view_menu = self.menuBar().addMenu("&View")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        self.dark_action = view_menu.addAction("Dark theme")
        self.dark_action.setCheckable(True)
        self.light_action = view_menu.addAction("Light theme")
        self.light_action.setCheckable(True)
        self.rekordbox_action = view_menu.addAction("Rekordbox theme")
        self.rekordbox_action.setCheckable(True)
        self.calm_action = view_menu.addAction("Calm theme")
        self.calm_action.setCheckable(True)
        theme_group.addAction(self.dark_action)
        theme_group.addAction(self.light_action)
        theme_group.addAction(self.rekordbox_action)
        theme_group.addAction(self.calm_action)
        self.dark_action.setChecked(self.theme == "dark")
        self.light_action.setChecked(self.theme == "light")
        self.rekordbox_action.setChecked(self.theme == "rekordbox")
        self.calm_action.setChecked(self.theme == "calm")
        self.dark_action.triggered.connect(lambda: self._apply_theme("dark"))
        self.light_action.triggered.connect(lambda: self._apply_theme("light"))
        self.rekordbox_action.triggered.connect(lambda: self._apply_theme("rekordbox"))
        self.calm_action.triggered.connect(lambda: self._apply_theme("calm"))

        help_menu = self.menuBar().addMenu("&Help")
        about_action = help_menu.addAction("About")
        about_action.triggered.connect(self._show_about)

    def _build_tray_controls(self):
        self.tray_icon = QSystemTrayIcon(app_icon(), self)
        self.tray_icon.setToolTip("Setuno")
        tray_menu = QMenu(self)
        show_action = tray_menu.addAction("Show Setuno")
        show_action.triggered.connect(self._show_from_tray)
        tray_menu.addSeparator()
        previous_action = tray_menu.addAction("Previous")
        previous_action.triggered.connect(self._on_prev_requested)
        self.tray_play_action = tray_menu.addAction("Play")
        self.tray_play_action.triggered.connect(self.player.toggle_play)
        next_action = tray_menu.addAction("Next")
        next_action.triggered.connect(self._on_next_requested)
        stop_action = tray_menu.addAction("Stop")
        stop_action.triggered.connect(self.player.stop)
        self.player.playing_changed.connect(self._update_tray_play_action)
        tray_menu.addSeparator()
        quit_action = tray_menu.addAction("Exit")
        quit_action.triggered.connect(self.close)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(
            lambda reason: self._show_from_tray()
            if reason == QSystemTrayIcon.DoubleClick else None
        )
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon.show()

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _update_tray_play_action(self, is_playing: bool):
        self.tray_play_action.setText("Pause" if is_playing else "Play")

    def _apply_theme(self, mode: str, persist: bool = True):
        self.theme = mode
        palette = PALETTES[mode]
        QApplication.instance().setStyleSheet(build_stylesheet(mode))
        icon_loader.set_theme_color(palette["icon"])
        self.library_view.apply_theme(palette["bg"], palette["accent_strong"], palette["accent"])
        self.playlist_builder.apply_theme(palette["bg"], palette["accent_strong"], palette["accent"])
        self.saved_playlists_view.apply_theme()
        self.playlist_builder.stats_widget.apply_theme(
            palette["bg"], palette["accent"], palette["text"], palette["accent_strong"],
        )
        self.graph_view.apply_theme(
            palette["accent_strong"], palette["accent"], palette["muted_text"],
        )
        self.queue_view.apply_theme(
            palette["bg"], palette["accent_strong"], palette["accent"],
        )
        self.level_meter.set_theme(
            palette["bg"], palette["accent"], palette["accent_strong"], palette["text"],
        )
        if self.library_view.waveform_dialog:
            self.library_view.waveform_dialog.apply_theme(palette["bg"], palette["accent_strong"])
        if self.playlist_builder.waveform_dialog:
            self.playlist_builder.waveform_dialog.apply_theme(palette["bg"], palette["accent_strong"])
        self.player.apply_theme(
            palette["accent_strong"], palette["bg"], palette["accent_strong"], palette["accent"],
        )
        if persist:
            self.settings["theme"] = mode
            save_settings(self.settings)

    def _on_tab_changed(self, index: int):
        self.queue_tab_btn.setChecked(self.tabs.widget(index) is self.queue_view)
        if self.tabs.widget(index) is self.saved_playlists_view:
            self.saved_playlists_view.refresh()
        elif self.tabs.widget(index) is self.graph_view:
            self.graph_view.refresh()

    def _show_queue_tab(self):
        self.tabs.setCurrentWidget(self.queue_view)
        self.queue_tab_btn.setChecked(True)

    def _open_saved_playlist(self, record: dict):
        self.playlist_builder.load_saved_playlist(record)
        self.tabs.setCurrentWidget(self.playlist_builder)

    def _on_library_changed(self):
        self.library_view.refresh_from_db()
        self.playlist_builder.refresh_genres()
        self.queue_view.refresh()
        self.graph_view.mark_dirty()
        self.statusBar().showMessage("Library updated.", 5000)

    def _set_library_folders(self, folders: list[str]):
        self.settings["library_folders"] = folders
        save_settings(self.settings)

    def _set_library_visible_columns(self, columns: list[str]):
        self.settings["library_visible_columns"] = columns
        save_settings(self.settings)

    def _play_from_queue(self, track):
        current_id = self.player.current_track.id if self.player.current_track else None
        if self._resume_source is None:
            self._resume_source = self.active_source
            self._resume_track_id = current_id
        self._start_track(self.active_source, track)

    def _on_track_played(self, source: str, track):
        """A track was picked from `source` ('library' or 'builder'): load it and mark it as playing."""
        self._resume_source = None
        self._resume_track_id = None
        self._start_track(source, track)

    def _start_track(self, source: str | None, track) -> bool:
        self.active_source = source
        self.player.load_track(track)
        self._record_played(track)
        self._mark_playing(track.id)
        return True

    def _record_played(self, track):
        self.played_track_ids.add(track.id)
        self.played_history_ids.append(track.id)
        self.library_view.refresh_played_state()
        self.playlist_builder.refresh_played_state()
        self.graph_view.set_played_ids(self.played_track_ids)
        self.queue_view.refresh()

    def _add_to_queue(self, tracks):
        tracks = tracks if isinstance(tracks, (list, tuple)) else [tracks]
        added = self.queue_view.add_tracks(tracks)
        if added:
            self.statusBar().showMessage(f"Queued {added} track(s).", 5000)
        else:
            self.statusBar().showMessage("Selected tracks are already in the queue.", 5000)

    def _mark_playing(self, track_id: int):
        self.library_view.set_playing_id(track_id if self.active_source == "library" else None)
        self.playlist_builder.set_playing_id(track_id if self.active_source == "builder" else None)
        self.graph_view.set_playing_id(track_id if self.active_source == "graph" else None)

    def _next_from_source(self, source, current_id):
        if source == "library":
            ids = self.library_view.get_row_ids()
        elif source == "builder":
            ids = self.playlist_builder.get_row_ids()
        elif source == "graph":
            return self.graph_view.most_similar_neighbor(current_id)
        else:
            return None
        if current_id not in ids or ids.index(current_id) + 1 >= len(ids):
            return None
        return self.db.get_track(ids[ids.index(current_id) + 1])

    def _next_track(self):
        current_id = self.player.current_track.id if self.player.current_track else None
        queued = self.queue_view.pop_next()
        if queued:
            if self._resume_source is None:
                self._resume_source = self.active_source
                self._resume_track_id = current_id
            return queued, self.active_source
        if self._resume_source is not None:
            source, resume_id = self._resume_source, self._resume_track_id
            self._resume_source = None
            self._resume_track_id = None
            return self._next_from_source(source, resume_id), source
        return self._next_from_source(self.active_source, current_id), self.active_source

    def _play_next(self):
        track, source = self._next_track()
        if track:
            self._start_track(source, track)

    def _on_crossfade_requested(self):
        next_track, source = self._next_track()
        if not next_track:
            return
        self.active_source = source
        self._record_played(next_track)
        self.player.crossfade_to(next_track)

    def _on_track_transitioned(self, track):
        self._mark_playing(track.id)

    def _on_track_finished(self):
        if self.player.is_crossfading:
            return
        self._play_next()

    def _on_next_requested(self):
        if self.active_source:
            self._play_next()
        else:
            self._play_random_from_active_tab()

    def _on_prev_requested(self):
        if self.active_source == "library":
            self.library_view.play_previous()
        elif self.active_source == "builder":
            self.playlist_builder.play_previous()
        else:
            self._play_random_from_active_tab()

    def _play_random_from_active_tab(self):
        current_widget = self.tabs.currentWidget()
        if current_widget is self.library_view:
            self.active_source = "library"
            self.library_view.play_random()
        elif current_widget is self.playlist_builder:
            self.active_source = "builder"
            self.playlist_builder.play_random()

    def _show_about(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("About Setuno")
        dialog.setWindowIcon(app_icon())
        dialog.setFixedWidth(450)

        layout = QVBoxLayout(dialog)

        # Logo
        logo = QLabel()
        pixmap = QPixmap("assets/icon.png")
        pixmap = pixmap.scaled(
            100,
            100,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        logo.setPixmap(pixmap)
        logo.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo)

        # Description
        text = QLabel(
            "<h2>Setuno</h2>"
            "<h3>v1.1.0</h3>"
            "<p>"
            "A local, offline companion for DJs, radio hosts, and playlist curators. "
            "It scans your music folders, analyzes each track's tempo, musical key and "
            "energy, and builds ordered playlists for a target set length or track count."
            "</p>"
            "<p>"
            "With harmonic (Camelot wheel) mixing, an embedded player, live stats, "
            "and export to Rekordbox-compatible <code>.m3u/.m3u8</code> or ordered "
            "folder copies."
            "</p>"
            "<p>Created by J. Baudru (Bonoob).</p>"
        )

        text.setWordWrap(True)
        text.setAlignment(Qt.AlignCenter)
        layout.addWidget(text)

        # Spotify link
        spotify = QLabel(
            '<a href="https://open.spotify.com/artist/0RfywI6fl3H2428q0oxUI4?si=3cjS436bQHa67v1qZ7ZclQ">'
            "Spotify"
            "</a>"
        )
        spotify.setOpenExternalLinks(True)
        spotify.setAlignment(Qt.AlignCenter)
        layout.addWidget(spotify)

        # Close button
        buttons = QHBoxLayout()
        buttons.addStretch()

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        buttons.addWidget(close_button)

        buttons.addStretch()
        layout.addLayout(buttons)

        dialog.exec()

    def closeEvent(self, event):
        for view in (self.library_view, self.playlist_builder):
            dialog = view.waveform_dialog
            if dialog:
                for worker in list(dialog._active_workers):
                    worker.wait(2000)
        self.db.close()
        super().closeEvent(event)
