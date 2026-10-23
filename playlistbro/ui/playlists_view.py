"""Browse and edit saved playlists in list or grid view."""
from uuid import uuid4

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.database import Database
from ..core.playlist_engine import MODE_LABELS
from .icon_loader import cover_pixmap, icon
from .playlist_export import export_playlist

COLUMNS = ["Playlist", "Mode", "Created"]


class _PlaylistSummary(QWidget):
    def __init__(self, record: dict, grid: bool = False):
        super().__init__()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet("QWidget { background: transparent; }")
        size = 144 if grid else 48
        cover = QLabel()
        cover.setFixedSize(size, size)
        cover.setPixmap(cover_pixmap(record.get("cover_path", ""), size))
        self.name_label = QLabel()
        self.name_label.setTextFormat(Qt.PlainText)
        self.name_label.setText(record["name"])
        self.name_label.setToolTip(record["name"])
        self.name_label.setWordWrap(grid)
        font = self.name_label.font()
        font.setBold(True)
        self.name_label.setFont(font)
        count = len(record.get("track_ids", []))
        self.count_label = QLabel(f"{count} song{'s' if count != 1 else ''}")
        font = self.count_label.font()
        font.setPointSizeF(max(6.0, font.pointSizeF() - 1))
        self.count_label.setFont(font)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self.name_label)
        text.addWidget(self.count_label)
        layout = QVBoxLayout(self) if grid else QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addWidget(cover)
        layout.addLayout(text)
        if grid:
            self.name_label.setMaximumHeight(36)
            layout.addStretch(1)
        else:
            text.addStretch(1)


class _PlaylistEditDialog(QDialog):
    def __init__(self, record: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit playlist")
        self.cover_change_path: str | None = None
        self.name_edit = QLineEdit(record["name"])
        self.cover_preview = QLabel()
        self.cover_preview.setFixedSize(96, 96)
        self.cover_preview.setPixmap(cover_pixmap(record.get("cover_path", ""), 96))
        change_btn = QPushButton("Change Cover...")
        change_btn.clicked.connect(self._choose_cover)
        remove_btn = QPushButton("Remove Cover")
        remove_btn.clicked.connect(self._remove_cover)
        cover_buttons = QVBoxLayout()
        cover_buttons.addWidget(change_btn)
        cover_buttons.addWidget(remove_btn)
        cover_buttons.addStretch(1)
        cover_row = QHBoxLayout()
        cover_row.addWidget(self.cover_preview)
        cover_row.addLayout(cover_buttons)
        form = QFormLayout(self)
        form.addRow("Name:", self.name_edit)
        form.addRow("Cover:", cover_row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.name_edit.textChanged.connect(self._update_save_button)
        self._update_save_button()
        form.addRow(self.buttons)

    def _update_save_button(self):
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.name_edit.text().strip()))

    def _choose_cover(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose playlist cover", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not path:
            return
        if QImage(path).isNull():
            QMessageBox.warning(self, "Invalid cover", "The selected image could not be opened.")
            return
        self.cover_change_path = path
        self.cover_preview.setPixmap(cover_pixmap(path, 96))

    def _remove_cover(self):
        self.cover_change_path = ""
        self.cover_preview.setPixmap(cover_pixmap("", 96))


class SavedPlaylistsView(QWidget):
    view_mode_changed = Signal(str)

    def __init__(self, db: Database, on_open=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.on_open = on_open
        self.playlists: list[dict] = []

        self.open_btn = QPushButton("Open")
        self.open_btn.setToolTip("Open in Playlist Builder")
        self.delete_btn = QPushButton()
        self.delete_btn.setToolTip("Delete playlist")
        self.delete_btn.setAccessibleName("Delete playlist")
        self.edit_btn = QPushButton("Edit playlist...")
        self.export_btn = QPushButton("Export...")
        self.export_btn.setToolTip("Export for Rekordbox or as M3U/M3U8")
        self.export_btn.clicked.connect(self._export_selected)
        self.refresh_btn = QPushButton()
        self.refresh_btn.setToolTip("Refresh playlists")
        self.refresh_btn.setAccessibleName("Refresh playlists")
        self.open_btn.clicked.connect(self._open_selected)
        self.delete_btn.clicked.connect(self._delete_selected)
        self.edit_btn.clicked.connect(self._edit_selected)
        self.refresh_btn.clicked.connect(self.refresh)
        self.view_btn = QPushButton()
        self.view_btn.setCheckable(True)
        self.view_btn.toggled.connect(self._change_view_mode)
        for button in (self.delete_btn, self.refresh_btn, self.view_btn):
            button.setFixedWidth(32)
        self.apply_theme()

        top = QHBoxLayout()
        top.addWidget(self.open_btn)
        top.addWidget(self.delete_btn)
        top.addWidget(self.edit_btn)
        top.addWidget(self.export_btn)
        top.addStretch(1)
        top.addWidget(self.view_btn)
        top.addWidget(self.refresh_btn)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self._open_selected)
        self.table.setColumnWidth(0, 280)
        self.table.verticalHeader().setDefaultSectionSize(64)
        self.table.itemSelectionChanged.connect(self._update_actions)

        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setWrapping(True)
        self.grid.setGridSize(QSize(176, 224))
        self.grid.setSpacing(8)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.itemDoubleClicked.connect(self._open_selected)
        self.grid.itemSelectionChanged.connect(self._update_actions)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.table)
        self.pages.addWidget(self.grid)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.pages)

        self.refresh()

    def refresh(self):
        selected = self._selected_playlist()
        selected_id = selected["id"] if selected else None
        self.playlists = self.db.list_playlists()
        self.table.clearContents()
        self.grid.clear()
        self.table.setRowCount(len(self.playlists))
        for r, p in enumerate(self.playlists):
            created = (p.get("created_at") or "")[:19].replace("T", " ")
            values = [
                p["name"], MODE_LABELS.get(p["mode"], p["mode"]),
                created,
            ]
            for c, val in enumerate(values):
                item = QTableWidgetItem("" if c == 0 else val)
                item.setData(Qt.UserRole, p["id"])
                if c == 0:
                    item.setData(Qt.AccessibleTextRole, p["name"])
                self.table.setItem(r, c, item)
            self.table.setCellWidget(r, 0, _PlaylistSummary(p))
            item = QListWidgetItem()
            item.setData(Qt.UserRole, p["id"])
            item.setData(Qt.AccessibleTextRole, p["name"])
            item.setSizeHint(QSize(168, 216))
            item.setToolTip(f"{p['name']}\n{values[1]}\n{created}")
            self.grid.addItem(item)
            self.grid.setItemWidget(item, _PlaylistSummary(p, grid=True))
        self._select_id(selected_id)
        self._update_actions()

    def set_view_mode(self, mode: str):
        self.view_btn.setChecked(mode == "grid")

    def _change_view_mode(self):
        selected = self._selected_playlist()
        self.pages.setCurrentIndex(1 if self.view_btn.isChecked() else 0)
        self._select_id(selected["id"] if selected else None)
        self._update_actions()
        self._update_view_icon()
        self.view_mode_changed.emit("grid" if self.view_btn.isChecked() else "list")

    def _update_view_icon(self):
        grid = self.view_btn.isChecked()
        self.view_btn.setIcon(icon("list" if grid else "grid"))
        label = "Switch to list view" if grid else "Switch to grid view"
        self.view_btn.setToolTip(label)
        self.view_btn.setAccessibleName(label)

    def _select_id(self, playlist_id):
        for row, record in enumerate(self.playlists):
            if record["id"] == playlist_id:
                self.table.selectRow(row)
                self.grid.setCurrentRow(row)
                break

    def _update_actions(self):
        enabled = self._selected_playlist() is not None
        for button in (self.open_btn, self.delete_btn, self.edit_btn, self.export_btn):
            button.setEnabled(enabled)

    def _export_selected(self):
        record = self._selected_playlist()
        if not record:
            return
        tracks = self.db.get_playlist_tracks(record["id"])
        if len(tracks) != len(record.get("track_ids", [])):
            QMessageBox.warning(
                self, "Cannot export playlist",
                "Some songs in this playlist are no longer in the library. "
                "Open the playlist in the Playlist Builder to review it before exporting.",
            )
            return
        export_playlist(self, tracks, record["name"])

    def _selected_playlist(self):
        item = (self.grid.currentItem() if self.pages.currentWidget() is self.grid
                else self.table.item(self.table.currentRow(), 0))
        if item is not None:
            return next((p for p in self.playlists if p["id"] == item.data(Qt.UserRole)), None)
        return None

    def _edit_selected(self):
        record = self._selected_playlist()
        if not record:
            return
        dialog = _PlaylistEditDialog(record, self)
        if dialog.exec() != QDialog.Accepted:
            return
        cover_path = dialog.cover_change_path
        new_cover = None
        try:
            if cover_path:
                image = QImage(cover_path)
                if image.isNull():
                    raise ValueError("The selected image could not be opened.")
                covers = self.db.data_dir / "covers"
                covers.mkdir(parents=True, exist_ok=True)
                new_cover = covers / f"playlist_{uuid4().hex}.png"
                if not image.save(str(new_cover), "PNG"):
                    raise OSError("The playlist cover could not be saved.")
                cover_path = str(new_cover)
            self.db.update_playlist(record["id"], dialog.name_edit.text(), cover_path)
        except (OSError, ValueError, KeyError) as exc:
            if new_cover is not None:
                new_cover.unlink(missing_ok=True)
            QMessageBox.warning(self, "Could not save playlist", str(exc))
            return
        self.refresh()

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
        self.delete_btn.setIcon(icon("trash"))
        self.refresh_btn.setIcon(icon("reset"))
        self._update_view_icon()
