import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QGroupBox

from playlistbro.core.database import Database
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


if __name__ == "__main__":
    unittest.main()