import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QGroupBox

from playlistbro.core.database import Database
from playlistbro.core.models import Track
from playlistbro.core.playlist_engine import MODE_FIXED_ENERGY, MODE_FIXED_TEMPO
from playlistbro.ui.playlist_builder import PlaylistBuilder


class PlaylistBuilderLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_parameters_fit_without_hiding_controls(self):
        with tempfile.TemporaryDirectory() as folder:
            view = PlaylistBuilder(Database(Path(folder)))
            self.addCleanup(view.close)
            form = next(box for box in view.findChildren(QGroupBox)
                        if box.title() == "Playlist parameters")
            view.resize(900, 600)
            view.show()
            self.app.processEvents()

            self.assertLessEqual(view.width(), 900)
            self.assertLessEqual(form.height(), 125)
            self.assertGreater(view.table.height(), 330)
            self.assertTrue(view.generate_btn.isVisible())

            view.length_mode_combo.setCurrentIndex(1)
            self.assertTrue(view.count_spin.isVisible())
            self.assertEqual(view.length_mode_combo.toolTip(), "Number of songs")
            for mode, control in ((MODE_FIXED_TEMPO, view.tempo_fixed_widget),
                                  (MODE_FIXED_ENERGY, view.energy_fixed_widget)):
                view.mode_combo.setCurrentIndex(view.mode_combo.findData(mode))
                self.assertTrue(control.isVisible())
                self.assertEqual(view.mode_combo.toolTip(), view.mode_combo.currentText())

    def test_manually_added_track_is_kept_and_not_duplicated(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder))
            track = Track(filepath="song.mp3", title="Song", duration=180)
            track.id = db.upsert_track(track)
            view = PlaylistBuilder(db)
            self.addCleanup(view.close)

            self.assertTrue(view.add_track_to_playlist(track))
            self.assertEqual(view.get_row_ids(), [track.id])
            self.assertEqual(view.table.item(0, 0).checkState(), Qt.Checked)

            view.add_track_to_playlist(track)
            self.assertEqual(view.get_row_ids(), [track.id])

    def test_remove_duplicates_keeps_first_artist_title_match(self):
        class Cell:
            def __init__(self, value):
                self.value = value

            def data(self, _role):
                return self.value

            def text(self):
                return self.value

        class Table:
            def __init__(self, rows):
                self.rows = rows

            def rowCount(self):
                return len(self.rows)

            def item(self, row, column):
                return self.rows[row].get(column)

        class Stats:
            def update_stats(self, playlist):
                self.playlist = list(playlist)

        class Button:
            def setEnabled(self, enabled):
                self.enabled = enabled

        class View:
            def _populate_table(self, playlist):
                self.populated = list(playlist)

        tracks = [
            Track(id=1, filepath="first.mp3", title="Song", artist="Artist"),
            Track(id=2, filepath="copy.mp3", title=" song ", artist="ARTIST"),
            Track(id=3, filepath="other.mp3", title="Other", artist="Artist"),
        ]
        rows = [
            {0: Cell(track.id), 3: Cell(track.title), 4: Cell(track.artist)}
            for track in tracks
        ]
        view = View()
        view.current_playlist = tracks
        view._locked_ids = {track.id for track in tracks}
        view.table = Table(rows)
        view.stats_widget = Stats()
        view.save_m3u_btn = Button()
        view.export_folder_btn = Button()
        view.save_library_btn = Button()

        self.assertEqual(PlaylistBuilder.remove_duplicates(view), 1)
        self.assertEqual([track.id for track in view.current_playlist], [1, 3])
        self.assertEqual(view._locked_ids, {1, 3})
        self.assertEqual([track.id for track in view.populated], [1, 3])
        self.assertEqual([track.id for track in view.stats_widget.playlist], [1, 3])


if __name__ == "__main__":
    unittest.main()