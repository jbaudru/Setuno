"""Manual BPM/key/metadata override dialogs, shared by the Library and Playlist Builder tables."""
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QInputDialog, QLineEdit,
)

from ..core.camelot import KEY_TO_CAMELOT
from ..core.database import Database
from ..core.models import Track

KEY_CHOICES = sorted(KEY_TO_CAMELOT.keys())


def edit_bpm(parent, db: Database, track: Track) -> bool:
    """Prompt for a manual BPM correction. Returns True if the track was updated."""
    value, ok = QInputDialog.getDouble(
        parent, "Edit BPM", f"BPM for '{track.display_name}':",
        track.tempo, 30.0, 300.0, 1,
    )
    if not ok:
        return False
    db.set_manual_tempo(track.id, value)
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
    """Title/Artist/Album/Genre editor. Pressing Enter in any field accepts (saves) the dialog."""

    def __init__(self, track: Track, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Metadata")
        self.title_edit = QLineEdit(track.title)
        self.artist_edit = QLineEdit(track.artist)
        self.album_edit = QLineEdit(track.album)
        self.genre_edit = QLineEdit(track.genre)

        form = QFormLayout(self)
        form.addRow("Title:", self.title_edit)
        form.addRow("Artist:", self.artist_edit)
        form.addRow("Album:", self.album_edit)
        form.addRow("Genre:", self.genre_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Ok).setDefault(True)  # Enter in any field triggers this
        form.addRow(buttons)


def edit_metadata(parent, db: Database, track: Track) -> bool:
    """Prompt for Title/Artist/Album/Genre edits (Enter saves). Returns True if updated."""
    dlg = _MetadataDialog(track, parent)
    if dlg.exec() != QDialog.Accepted:
        return False
    db.update_metadata(
        track.id, dlg.title_edit.text().strip(), dlg.artist_edit.text().strip(),
        dlg.album_edit.text().strip(), dlg.genre_edit.text().strip(),
    )
    return True
