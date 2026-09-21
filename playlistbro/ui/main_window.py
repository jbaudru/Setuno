"""Main application window: tabs, menu, embedded player docked at the bottom."""
from PySide6.QtGui import QActionGroup, QDesktopServices, QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QStatusBar, QTabWidget, QVBoxLayout, QWidget, QDialog, QLabel, QPushButton, QHBoxLayout
from PySide6.QtCore import Qt

from ..core.database import Database
from ..core.settings import load_settings, save_settings
from . import icon_loader
from .icon_loader import app_icon
from .library_view import LibraryView
from .player_widget import PlayerWidget
from .playlist_builder import PlaylistBuilder
from .playlists_view import SavedPlaylistsView
from .theme import PALETTES, build_stylesheet, icon_color


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Setuno")
        self.setWindowIcon(app_icon())
        self.resize(1200, 800)

        self.db = Database()
        self.settings = load_settings()
        self.theme = self.settings.get("theme", "dark")
        icon_loader.set_theme_color(icon_color(self.theme))
        self.active_source: str | None = None  # 'library' or 'builder': where the current track came from

        self.player = PlayerWidget()
        self.player.track_finished.connect(self._on_track_finished)
        self.player.next_requested.connect(self._on_next_requested)
        self.player.prev_requested.connect(self._on_prev_requested)
        self.player.on_request_track = self._play_random_from_active_tab

        self.library_view = LibraryView(
            self.db,
            on_library_changed=self._on_library_changed,
            on_play_track=lambda t: self._on_track_played("library", t),
            get_library_folders=lambda: self.settings.get("library_folders", []),
            set_library_folders=self._set_library_folders,
        )
        self.playlist_builder = PlaylistBuilder(
            self.db, on_play_track=lambda t: self._on_track_played("builder", t),
            on_library_changed=self._on_library_changed, 
        )
        self.saved_playlists_view = SavedPlaylistsView(self.db, on_open=self._open_saved_playlist)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.library_view, "Library")
        self.tabs.addTab(self.playlist_builder, "Playlist Builder")
        self.tabs.addTab(self.saved_playlists_view, "Saved Playlists")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.player, 0)
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar())
        self._build_menu()
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
        theme_group.addAction(self.dark_action)
        theme_group.addAction(self.light_action)
        theme_group.addAction(self.rekordbox_action)
        self.dark_action.setChecked(self.theme == "dark")
        self.light_action.setChecked(self.theme == "light")
        self.rekordbox_action.setChecked(self.theme == "rekordbox")
        self.dark_action.triggered.connect(lambda: self._apply_theme("dark"))
        self.light_action.triggered.connect(lambda: self._apply_theme("light"))
        self.rekordbox_action.triggered.connect(lambda: self._apply_theme("rekordbox"))

        help_menu = self.menuBar().addMenu("&Help")
        about_action = help_menu.addAction("About")
        about_action.triggered.connect(self._show_about)

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
        if self.library_view.waveform_dialog:
            self.library_view.waveform_dialog.apply_theme(palette["bg"], palette["accent_strong"])
        if self.playlist_builder.waveform_dialog:
            self.playlist_builder.waveform_dialog.apply_theme(palette["bg"], palette["accent_strong"])
        self.player.apply_theme(palette["accent_strong"])
        if persist:
            self.settings["theme"] = mode
            save_settings(self.settings)

    def _on_tab_changed(self, index: int):
        if self.tabs.widget(index) is self.saved_playlists_view:
            self.saved_playlists_view.refresh()

    def _open_saved_playlist(self, record: dict):
        self.playlist_builder.load_saved_playlist(record)
        self.tabs.setCurrentWidget(self.playlist_builder)

    def _on_library_changed(self):
        self.library_view.refresh_from_db()
        self.playlist_builder.refresh_genres()
        self.statusBar().showMessage("Library updated.", 5000)

    def _set_library_folders(self, folders: list[str]):
        self.settings["library_folders"] = folders
        save_settings(self.settings)

    def _on_track_played(self, source: str, track):
        """A track was picked from `source` ('library' or 'builder'): load it and mark it as playing."""
        self.active_source = source
        self.player.load_track(track)
        self.library_view.set_playing_id(track.id if source == "library" else None)
        self.playlist_builder.set_playing_id(track.id if source == "builder" else None)

    def _on_track_finished(self):
        if self.active_source == "library":
            self.library_view.play_next()
        elif self.active_source == "builder":
            self.playlist_builder.play_next()

    def _on_next_requested(self):
        if self.active_source == "library":
            self.library_view.play_next()
        elif self.active_source == "builder":
            self.playlist_builder.play_next()
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
