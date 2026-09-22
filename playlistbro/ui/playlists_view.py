"""Saved Playlists tab: browse playlists saved from the Playlist Builder and reopen them."""
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.database import Database
from ..core.playlist_engine import MODE_LABELS
from .icon_loader import icon

COLUMNS = ["Name", "Mode", "Songs", "Created"]


class SavedPlaylistsView(QWidget):
    def __init__(self, db: Database, on_open=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.on_open = on_open
        self.playlists: list[dict] = []

        self.open_btn = QPushButton(" Open in Playlist Builder")
        self.open_btn.setIcon(icon("headphones"))
        self.delete_btn = QPushButton(" Delete")
        self.delete_btn.setIcon(icon("stop"))
        self.refresh_btn = QPushButton(" Refresh")
        self.refresh_btn.setIcon(icon("folder"))
        self.open_btn.clicked.connect(self._open_selected)
        self.delete_btn.clicked.connect(self._delete_selected)
        self.refresh_btn.clicked.connect(self.refresh)

        top = QHBoxLayout()
        top.addWidget(self.open_btn)
        top.addWidget(self.delete_btn)
        top.addStretch(1)
        top.addWidget(self.refresh_btn)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self._open_selected)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table)

        self.refresh()

    def refresh(self):
        self.playlists = self.db.list_playlists()
        self.table.setRowCount(len(self.playlists))
        for r, p in enumerate(self.playlists):
            created = (p.get("created_at") or "")[:19].replace("T", " ")
            values = [
                p["name"], MODE_LABELS.get(p["mode"], p["mode"]),
                str(len(p.get("track_ids", []))), created,
            ]
            for c, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setData(1000, p["id"])
                self.table.setItem(r, c, item)

    def _selected_playlist(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.playlists):
            return None
        return self.playlists[row]

    def _open_selected(self):
        record = self._selected_playlist()
        if record and self.on_open:
            self.on_open(record)

    def _delete_selected(self):
        record = self._selected_playlist()
        if not record:
            return
        answer = QMessageBox.question(self, "Delete playlist", f"Delete '{record['name']}'?")
        if answer == QMessageBox.Yes:
            self.db.delete_playlist(record["id"])
            self.refresh()

    def apply_theme(self):
        """Refresh flat icons (colors are set globally in icon_loader before calling this)."""
        self.open_btn.setIcon(icon("headphones"))
        self.delete_btn.setIcon(icon("stop"))
        self.refresh_btn.setIcon(icon("folder"))
