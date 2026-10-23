import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox

from playlistbro.core.database import Database
from playlistbro.core.models import Track
from playlistbro.core.playlist_engine import MODE_FIXED_TEMPO
from playlistbro.ui.playlists_view import SavedPlaylistsView, _PlaylistEditDialog
from playlistbro.ui.theme import build_stylesheet


class SavedPlaylistsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = Database(Path(self.folder.name))
        self.playlist_id = self.db.save_playlist(
            "Evening set", MODE_FIXED_TEMPO, {"tempo": 125}, [1, 2],
        )
        self.opened = []
        self.view = SavedPlaylistsView(self.db, on_open=self.opened.append)
        self.addCleanup(self.view.close)

    def test_both_layouts_show_cover_name_and_small_song_count(self):
        self.view.resize(900, 600)
        self.view.show()
        self.app.processEvents()
        summaries = (
            self.view.table.cellWidget(0, 0),
            self.view.grid.itemWidget(self.view.grid.item(0)),
        )
        for summary in summaries:
            self.assertEqual(summary.name_label.text(), "Evening set")
            self.assertEqual(summary.count_label.text(), "2 songs")
            self.assertLess(summary.count_label.font().pointSizeF(),
                            summary.name_label.font().pointSizeF())
        self.assertEqual(self.view.table.item(0, 0).data(Qt.UserRole), self.playlist_id)
        self.view.set_view_mode("grid")
        self.app.processEvents()
        self.assertIs(self.view.pages.currentWidget(), self.view.grid)
        self.assertGreater(self.view.grid.visualItemRect(self.view.grid.item(0)).height(), 200)

    def test_summary_text_background_is_transparent_in_both_layouts(self):
        self.view.resize(900, 600)
        self.view.show()
        for theme in ("dark", "light", "rekordbox"):
            self.view.setStyleSheet(build_stylesheet(theme))
            for mode in ("list", "grid"):
                self.view.set_view_mode(mode)
                self.app.processEvents()
                summary = (self.view.table.cellWidget(0, 0) if mode == "list"
                           else self.view.grid.itemWidget(self.view.grid.item(0)))
                image = summary.grab().toImage()
                background = image.pixelColor(1, 1)
                for label in (summary.name_label, summary.count_label):
                    point = label.pos()
                    color = image.pixelColor(
                        point.x() + label.width() - 2, point.y() + label.height() - 2,
                    )
                    self.assertEqual(color, background, (theme, mode, label.text()))

    def test_saved_playlist_export_passes_name_and_tracks(self):
        for filepath in ("first.mp3", "second.mp3"):
            self.db.upsert_track(Track(filepath=filepath))
        self.view.table.selectRow(0)
        with patch("playlistbro.ui.playlists_view.export_playlist") as export:
            self.view.export_btn.click()
        parent, tracks, name = export.call_args.args
        self.assertIs(parent, self.view)
        self.assertEqual(name, "Evening set")
        self.assertEqual([track.id for track in tracks], [1, 2])

    def test_saved_playlist_export_warns_about_removed_library_tracks(self):
        self.view.table.selectRow(0)
        with patch("playlistbro.ui.playlists_view.export_playlist") as export, \
                patch.object(QMessageBox, "warning") as warning:
            self.view.export_btn.click()
        warning.assert_called_once()
        export.assert_not_called()

    def test_switch_and_refresh_preserve_selection_and_open_action(self):
        self.view.table.selectRow(0)
        modes = []
        self.view.view_mode_changed.connect(modes.append)
        for mode in ("grid", "list", "grid"):
            self.view.set_view_mode(mode)
            self.view.refresh()
            self.assertEqual(self.view._selected_playlist()["id"], self.playlist_id)
            self.view._open_selected()
        self.assertEqual(modes, ["grid", "list", "grid"])
        self.assertEqual([p["id"] for p in self.opened], [self.playlist_id] * 3)

    def test_toolbar_uses_icons_and_plain_open_label(self):
        self.assertEqual(self.view.open_btn.text(), "Open")
        self.assertTrue(self.view.open_btn.icon().isNull())
        for button in (self.view.view_btn, self.view.refresh_btn, self.view.delete_btn):
            self.assertEqual(button.text(), "")
            self.assertFalse(button.icon().isNull())
            self.assertTrue(button.toolTip())
            self.assertTrue(button.accessibleName())
        self.view.table.selectRow(0)
        modes = []
        self.view.view_mode_changed.connect(modes.append)
        self.assertEqual(self.view.view_btn.toolTip(), "Switch to grid view")
        self.view.view_btn.click()
        self.assertIs(self.view.pages.currentWidget(), self.view.grid)
        self.assertEqual(self.view.view_btn.toolTip(), "Switch to list view")
        self.view.apply_theme()
        self.assertTrue(self.view.open_btn.icon().isNull())
        self.view.view_btn.click()
        self.assertIs(self.view.pages.currentWidget(), self.view.table)
        self.assertEqual(modes, ["grid", "list"])
        self.view.refresh_btn.click()
        self.view.open_btn.click()
        self.assertEqual(self.opened[0]["id"], self.playlist_id)

    def test_edit_persists_name_and_managed_cover_without_changing_playlist(self):
        self.view.table.selectRow(0)
        original = dict(self.db.list_playlists()[0])
        source = Path(self.folder.name) / "source.png"
        image = QImage(32, 48, QImage.Format_RGB32)
        image.fill(QColor("red"))
        self.assertTrue(image.save(str(source)))
        dialog = _PlaylistEditDialog(original)
        self.addCleanup(dialog.close)
        dialog.name_edit.setText("  Renamed set  ")
        dialog.cover_change_path = str(source)
        with patch("playlistbro.ui.playlists_view._PlaylistEditDialog", return_value=dialog), \
                patch.object(dialog, "exec", return_value=QDialog.Accepted):
            self.view._edit_selected()
        source.unlink()
        record = Database(Path(self.folder.name)).list_playlists()[0]
        self.assertEqual(record["name"], "Renamed set")
        self.assertTrue(Path(record["cover_path"]).is_file())
        self.assertFalse(QImage(record["cover_path"]).isNull())
        for key in ("id", "created_at", "mode", "params", "track_ids"):
            self.assertEqual(record[key], original[key])
        self.assertEqual(self.view._selected_playlist()["id"], self.playlist_id)
        self.assertEqual(self.view.grid.itemWidget(self.view.grid.item(0)).name_label.text(),
                         "Renamed set")
        self.db.update_playlist(self.playlist_id, "Rename only")
        self.assertEqual(self.db.list_playlists()[0]["cover_path"], record["cover_path"])
        self.db.update_playlist(self.playlist_id, "No cover", "")
        self.assertEqual(Database(Path(self.folder.name)).list_playlists()[0]["cover_path"], "")

    def test_invalid_name_and_cover_are_rejected(self):
        record = self.db.list_playlists()[0]
        dialog = _PlaylistEditDialog(record)
        self.addCleanup(dialog.close)
        dialog.name_edit.setText("  ")
        self.assertFalse(dialog.buttons.button(QDialogButtonBox.Ok).isEnabled())
        with self.assertRaises(ValueError):
            self.db.update_playlist(self.playlist_id, " ")
        with patch("playlistbro.ui.playlists_view.QFileDialog.getOpenFileName",
                   return_value=(str(Path(self.folder.name) / "bad.png"), "")), \
                patch.object(QMessageBox, "warning") as warning:
            dialog._choose_cover()
        warning.assert_called_once()
        self.assertIsNone(dialog.cover_change_path)
        self.assertEqual(self.db.list_playlists()[0], record)

    def test_cancel_does_not_modify_playlist(self):
        self.view.table.selectRow(0)
        original = dict(self.db.list_playlists()[0])
        with patch.object(_PlaylistEditDialog, "exec", return_value=QDialog.Rejected):
            self.view._edit_selected()
        self.assertEqual(self.db.list_playlists()[0], original)

    def test_failed_save_rolls_back_record_and_reports_error(self):
        self.view.table.selectRow(0)
        original = dict(self.db.list_playlists()[0])
        dialog = _PlaylistEditDialog(original)
        self.addCleanup(dialog.close)
        dialog.name_edit.setText("New name")
        with patch("playlistbro.ui.playlists_view._PlaylistEditDialog", return_value=dialog), \
                patch.object(dialog, "exec", return_value=QDialog.Accepted), \
                patch.object(self.db, "_save_playlists", side_effect=OSError("Disk full")), \
                patch.object(QMessageBox, "warning") as warning:
            self.view._edit_selected()
        warning.assert_called_once()
        self.assertEqual(self.db.list_playlists()[0], original)

    def test_delete_works_in_grid_and_empty_actions_are_disabled(self):
        self.view.set_view_mode("grid")
        self.view.grid.setCurrentRow(0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.view._delete_selected()
        self.assertEqual(Database(Path(self.folder.name)).list_playlists(), [])
        self.assertEqual(self.view.table.rowCount(), 0)
        self.assertEqual(self.view.grid.count(), 0)
        for button in (self.view.open_btn, self.view.edit_btn, self.view.delete_btn,
                       self.view.export_btn):
            self.assertFalse(button.isEnabled())

    def test_empty_and_single_song_counts(self):
        for ids, expected in (([], "0 songs"), ([1], "1 song")):
            playlist_id = self.db.save_playlist("Count", MODE_FIXED_TEMPO, {}, ids)
            self.view.refresh()
            row = next(i for i, p in enumerate(self.view.playlists) if p["id"] == playlist_id)
            self.assertEqual(self.view.table.cellWidget(row, 0).count_label.text(), expected)
            self.assertEqual(
                self.view.grid.itemWidget(self.view.grid.item(row)).count_label.text(), expected,
            )


if __name__ == "__main__":
    unittest.main()
