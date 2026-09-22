"""Manual BPM/key/metadata override dialogs, shared by the Library and Playlist Builder tables."""
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from ..core.analyzer import clear_cover_art, set_cover_art
from ..core.camelot import KEY_TO_CAMELOT
from ..core.database import Database
from ..core.models import Track
from .icon_loader import cover_pixmap

KEY_CHOICES = sorted(KEY_TO_CAMELOT.keys())


def edit_bpm(parent, db: Database, track: Track) -> bool:
    """Prompt for a manual BPM correction. Returns True if the track was updated."""
    value, ok = QInputDialog.getInt(
        parent, "Edit BPM", f"BPM for '{track.display_name}':",
        round(track.tempo), 30, 300, 1,
    )
    if not ok:
        return False
    db.set_manual_tempo(track.id, float(value))
    return True


def edit_key(parent, db: Database, track: Track) -> bool:
    """Prompt for a manual musical-key correction. Returns True if the track was updated."""
    current = track.key_name if track.key_name in KEY_CHOICES else KEY_CHOICES[0]
    key_name, ok = QInputDialog.getItem(
        parent, "Edit Key", f"Key for '{track.display_name}':",
        KEY_CHOICES, KEY_CHOICES.index(current), False,
    )
    if not ok:
        return False
    db.set_manual_key(track.id, key_name, KEY_TO_CAMELOT.get(key_name, ""))
    return True


class _MetadataDialog(QDialog):
    """Title/Artist/Album/Genre/cover editor. Enter in a text field accepts (saves) the dialog."""

    def __init__(self, track: Track, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Metadata")
        self.track = track
        # None = no change, "" = clear cover, path = embed new cover
        self.cover_change_path: str | None = None

        self.title_edit = QLineEdit(track.title)
        self.artist_edit = QLineEdit(track.artist)
        self.album_edit = QLineEdit(track.album)
        self.genre_edit = QLineEdit(track.genre)

        self.cover_preview = QLabel()
        self.cover_preview.setFixedSize(96, 96)
        self.cover_preview.setPixmap(cover_pixmap(track.cover_path, 96))

        change_cover_btn = QPushButton("Change Cover...")
        change_cover_btn.clicked.connect(self._choose_cover)
        clear_cover_btn = QPushButton("Remove Cover")
        clear_cover_btn.clicked.connect(self._clear_cover)

        cover_buttons = QVBoxLayout()
        cover_buttons.addWidget(change_cover_btn)
        cover_buttons.addWidget(clear_cover_btn)
        cover_buttons.addStretch(1)

        cover_row = QHBoxLayout()
        cover_row.addWidget(self.cover_preview)
        cover_row.addLayout(cover_buttons)
        cover_row.addStretch(1)

        form = QFormLayout(self)
        form.addRow("Cover:", cover_row)
        form.addRow("Title:", self.title_edit)
        form.addRow("Artist:", self.artist_edit)
        form.addRow("Album:", self.album_edit)
        form.addRow("Genre:", self.genre_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Ok).setDefault(True)  # Enter in any field triggers this
        form.addRow(buttons)

    def _choose_cover(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose cover image", "", "Images (*.png *.jpg *.jpeg)",
        )
        if not path:
            return
        self.cover_change_path = path
        self.cover_preview.setPixmap(cover_pixmap(path, 96))

    def _clear_cover(self):
        self.cover_change_path = ""
        self.cover_preview.setPixmap(cover_pixmap("", 96))


def edit_metadata(parent, db: Database, track: Track) -> bool:
    """Prompt for Title/Artist/Album/Genre/cover edits (Enter saves). Returns True if updated."""
    dlg = _MetadataDialog(track, parent)
    if dlg.exec() != QDialog.Accepted:
        return False
    db.update_metadata(
        track.id, dlg.title_edit.text().strip(), dlg.artist_edit.text().strip(),
        dlg.album_edit.text().strip(), dlg.genre_edit.text().strip(),
    )
    if dlg.cover_change_path == "":
        db.update_cover(track.id, clear_cover_art(track.filepath))
    elif dlg.cover_change_path:
        db.update_cover(track.id, set_cover_art(track.filepath, dlg.cover_change_path))
    return True
