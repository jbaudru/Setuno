import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QMessageBox, QStyleOptionViewItem, QTableWidget, QTableWidgetItem

from playlistbro.core.database import Database
from playlistbro.core.exporter import export_rekordbox_xml
from playlistbro.core.models import CUE_COLORS, CUE_LABELS, Track, validate_hot_cues
from playlistbro.ui.player_widget import PlayerWidget, WAVEFORM_HEIGHT
from playlistbro.ui.waveform_view import MiniWaveform, MiniWaveformDelegate, _WaveformChart


def dispose_widget(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class HotCuePersistenceTests(unittest.TestCase):
    def test_cues_survive_reload_and_rescan_and_return_independent_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder))
            track = Track(filepath="song.mp3", duration=180)
            track_id = db.upsert_track(track)
            cues = [{"slot": 0, "time": 0.0}, {"slot": 7, "time": 123.456}]
            db.set_hot_cues(track_id, cues)
            db.upsert_track(track)
            loaded = Database(Path(folder)).get_track(track_id)
            self.assertEqual(loaded.hot_cues, cues)
            loaded.hot_cues[0]["time"] = 5
            self.assertEqual(db.get_track(track_id).hot_cues, cues)
            db.set_hot_cues(track_id, [])
            self.assertEqual(Database(Path(folder)).get_track(track_id).hot_cues, [])

    def test_validation_rejects_duplicate_slots_invalid_slots_and_times(self):
        for cues in (
            [{"slot": 8, "time": 1}], [{"slot": -1, "time": 1}],
            [{"slot": 0, "time": -1}], [{"slot": 0, "time": float("nan")}],
            [{"slot": 0, "time": float("inf")}], [{"slot": 0, "time": 101}],
            [{"slot": 0, "time": 1}, {"slot": 0, "time": 2}],
        ):
            with self.assertRaises(ValueError):
                validate_hot_cues(cues, 100)

    def test_failed_write_rolls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder))
            track_id = db.upsert_track(Track(filepath="song.mp3"))
            with patch.object(Path, "replace", side_effect=OSError("Disk full")):
                with self.assertRaises(OSError):
                    db.set_hot_cues(track_id, [{"slot": 0, "time": 1}])
            self.assertEqual(db.get_track(track_id).hot_cues, [])
            self.assertEqual(Database(Path(folder)).get_track(track_id).hot_cues, [])

    def test_rekordbox_exports_all_eight_hot_cues_with_matching_colors(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "track.mp3"
            audio.touch()
            track = Track(filepath=str(audio), duration=180, hot_cues=[
                {"slot": slot, "time": slot * 10 + 0.123} for slot in range(8)
            ])
            output = Path(folder) / "export.xml"
            export_rekordbox_xml([track, track], str(output), "Set")
            marks = ET.parse(output).findall("COLLECTION/TRACK/POSITION_MARK")
            self.assertEqual(len(marks), 8)
            for slot, mark in enumerate(marks):
                self.assertEqual(mark.get("Name"), CUE_LABELS[slot])
                self.assertEqual(mark.get("Num"), str(slot))
                self.assertEqual(mark.get("Type"), "0")
                self.assertEqual(float(mark.get("Start")), slot * 10 + 0.123)
                rgb = QColor(CUE_COLORS[slot]).getRgb()[:3]
                self.assertEqual(tuple(int(mark.get(key)) for key in ("Red", "Green", "Blue")), rgb)

    def test_cue_save_does_not_serialize_or_rewrite_waveform_library(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder))
            track_id = db.upsert_track(Track(filepath="song.mp3", waveform_bands=[[1, 0, 0]] * 2400))
            original = db.tracks_path.read_bytes()
            with patch.object(db, "_save_tracks", side_effect=AssertionError("Library rewrite")):
                db.set_hot_cues(track_id, [{"slot": 0, "time": 5}])
            self.assertEqual(db.tracks_path.read_bytes(), original)
            self.assertLess(db.cues_path.stat().st_size, 100)
            self.assertEqual(Database(Path(folder)).get_track(track_id).hot_cues, [{"slot": 0, "time": 5}])

    def test_removal_overrides_legacy_embedded_cues(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder))
            track_id = db.upsert_track(Track(filepath="song.mp3", hot_cues=[{"slot": 0, "time": 5}]))
            db.set_hot_cues(track_id, [])
            self.assertEqual(Database(Path(folder)).get_track(track_id).hot_cues, [])


class HotCuePlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = Database(Path(self.folder.name))
        track_id = self.db.upsert_track(Track(filepath="song.mp3", duration=180))
        self.track = self.db.get_track(track_id)
        self.widget = PlayerWidget(db=self.db, auto_mix=False)
        self.addCleanup(dispose_widget, self.widget)
        self.widget.current_track = self.track
        self.widget._set_display_track(self.track)
        self.widget.waveform_chart.set_peaks([0.8] * 100)
        self.widget.waveform_chart.set_duration(180)

    def test_eight_buttons_set_exact_position_jump_replace_remove_and_persist(self):
        self.assertEqual([button.text() for button in self.widget.cue_buttons], list(CUE_LABELS))
        changed = []
        self.widget.cues_changed.connect(changed.append)
        with patch.object(self.widget.player, "position", return_value=12345):
            for button in self.widget.cue_buttons:
                button.click()
        self.assertEqual(len(self.track.hot_cues), 8)
        self.assertTrue(all(cue["time"] == 12.345 for cue in self.track.hot_cues))
        self.assertEqual(len(changed), 8)
        with patch.object(self.widget.player, "setPosition") as seek, \
                patch.object(self.widget.player, "play") as play, \
                patch.object(self.widget.player, "pause") as pause:
            self.widget.cue_buttons[7].click()
        seek.assert_called_once_with(12345)
        play.assert_not_called()
        pause.assert_not_called()
        with patch.object(self.widget.player, "position", return_value=45678):
            self.widget._save_cue(0)
        self.assertEqual(self.track.hot_cues[0]["time"], 45.678)
        self.widget._save_cue(7, remove=True)
        self.assertEqual([cue["slot"] for cue in self.track.hot_cues], list(range(7)))
        loaded = Database(Path(self.folder.name)).get_track(self.track.id)
        self.assertEqual(loaded.hot_cues, self.track.hot_cues)
        self.assertEqual(self.widget.waveform_chart.cues[0], (0, 45.678))

    def test_expansion_places_cue_buttons_below_waveform_and_collapses_together(self):
        self.widget.resize(900, self.widget.height())
        self.widget.show()
        self.widget._toggle_extended()
        self.app.processEvents()
        self.assertTrue(self.widget.cue_panel.isVisible())
        self.assertGreaterEqual(self.widget.cue_panel.y(),
                                self.widget.waveform_chart.y() + WAVEFORM_HEIGHT)
        self.assertEqual(self.widget.height(), 56 + WAVEFORM_HEIGHT + 40)
        self.widget._toggle_extended()
        self.assertTrue(self.widget.cue_panel.isHidden())
        self.assertTrue(self.widget.waveform_chart.isHidden())
        self.assertEqual(self.widget.height(), 56)

    def test_automix_uses_varispeed_and_cancelling_clears_tempo_recovery(self):
        for deck in (self.widget.player, self.widget._transition_player):
            if hasattr(deck, "pitchCompensation"):
                self.assertFalse(deck.pitchCompensation())
        self.widget.player.setPlaybackRate(1.2)
        self.widget._tempo_restore_timer.start()
        self.widget._cancel_crossfade()
        self.assertFalse(self.widget._tempo_restore_timer.isActive())
        self.assertEqual(self.widget.player.playbackRate(), 1.0)

    def test_remove_cross_clears_slot_without_jumping_and_allows_setting_again(self):
        button = self.widget.cue_buttons[0]
        self.assertTrue(button.remove_btn.isHidden())
        empty_style = button.styleSheet()
        with patch.object(self.widget.player, "position", return_value=12500):
            button.click()
        self.assertFalse(button.remove_btn.isHidden())
        self.assertNotEqual(button.styleSheet(), empty_style)
        with patch.object(self.widget.player, "setPosition") as seek:
            button.remove_btn.click()
        seek.assert_not_called()
        self.assertTrue(button.remove_btn.isHidden())
        self.assertEqual(self.track.hot_cues, [])
        self.assertEqual(button.styleSheet(), empty_style)
        self.assertEqual(Database(Path(self.folder.name)).get_track(self.track.id).hot_cues, [])
        with patch.object(self.widget.player, "position", return_value=25000):
            button.click()
        self.assertEqual(self.track.hot_cues, [{"slot": 0, "time": 25.0}])

    def test_remove_cross_stays_top_right_and_has_transparent_background(self):
        self.widget.show()
        self.widget._toggle_extended()
        self.widget._save_cue(0)
        button = self.widget.cue_buttons[0]
        for width in (900, 1200):
            self.widget.resize(width, self.widget.height())
            self.app.processEvents()
            remove = button.remove_btn
            self.assertEqual(remove.width(), 22)
            self.assertEqual(remove.height(), 22)
            self.assertEqual(remove.x() + remove.width(), button.width() - 2)
            self.assertEqual(remove.y(), 2)
            self.assertIn("background: transparent", remove.styleSheet())
            image = button.grab().toImage()
            self.assertEqual(image.pixelColor(remove.x() + 1, remove.y() + 1),
                             QColor(CUE_COLORS[0]))

    def test_player_grid_shows_only_timecodes_but_preview_keeps_bar_labels(self):
        self.assertEqual(self.widget.waveform_chart._grid_label(12, 1, 25), "01:25")
        preview = _WaveformChart("#fff", "#fff", "#000")
        self.addCleanup(dispose_widget, preview)
        self.assertEqual(preview._grid_label(12, 1, 25), "12 \u00b7 01:25")

    def test_save_error_does_not_change_buttons_track_or_markers(self):
        with patch.object(self.db, "set_hot_cues", side_effect=OSError("Disk full")), \
                patch.object(QMessageBox, "warning") as warning:
            self.widget.cue_buttons[0].click()
        warning.assert_called_once()
        self.assertEqual(self.track.hot_cues, [])
        self.assertEqual(self.widget.waveform_chart.cues, ())
        self.assertIn("Set cue A", self.widget.cue_buttons[0].toolTip())

    def test_track_switch_clears_button_state_and_uses_other_track_cues(self):
        self.widget._save_cue(0)
        other = Track(id=2, filepath="other.mp3", hot_cues=[{"slot": 2, "time": 5.25}])
        self.widget.current_track = other
        self.widget._set_display_track(other)
        self.assertIn("Set cue A", self.widget.cue_buttons[0].toolTip())
        self.assertIn("5.250s", self.widget.cue_buttons[2].toolTip())
        self.widget._set_display_track(None)
        self.widget.current_track = None
        self.widget._refresh_cue_buttons()
        self.assertTrue(all(not button.isEnabled() for button in self.widget.cue_buttons))

    def test_context_menu_replace_remove_and_cancel(self):
        for choice in ("Set at current position", "Remove cue", None):
            with patch("playlistbro.ui.player_widget.QMenu") as menu_type:
                menu = menu_type.return_value
                actions = [Mock(), Mock()]
                menu.addAction.side_effect = actions
                menu.exec.return_value = actions[0 if choice == "Set at current position" else 1] if choice else None
                with patch.object(self.widget, "_save_cue") as save:
                    self.widget._cue_menu(0, QPoint())
                if choice:
                    if choice == "Remove cue":
                        save.assert_called_once_with(0, remove=True)
                    else:
                        save.assert_called_once_with(0)
                else:
                    save.assert_not_called()

    def test_crossfade_uses_displayed_incoming_deck_for_cue_positions_and_jumps(self):
        incoming_id = self.db.upsert_track(Track(filepath="incoming.mp3", duration=180))
        incoming = self.db.get_track(incoming_id)
        self.widget._transition_track = incoming
        self.widget._set_display_track(incoming)
        with patch.object(self.widget.player, "position", return_value=99000), \
                patch.object(self.widget._transition_player, "position", return_value=5000):
            self.widget.cue_buttons[0].click()
        self.assertEqual(incoming.hot_cues, [{"slot": 0, "time": 5.0}])
        self.assertEqual(self.track.hot_cues, [])
        with patch.object(self.widget._transition_player, "setPosition") as incoming_seek, \
                patch.object(self.widget.player, "setPosition") as outgoing_seek:
            self.widget.cue_buttons[0].click()
        incoming_seek.assert_called_once_with(5000)
        outgoing_seek.assert_not_called()


class HotCueRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_large_and_mini_markers_draw_color_and_label_without_peaks(self):
        cues = [{"slot": 0, "time": 25.0}]
        mini = MiniWaveform([], "#fff", "#fff", "#000", cues=cues, duration=100)
        chart = _WaveformChart("#fff", "#fff", "#000")
        self.addCleanup(dispose_widget, mini)
        self.addCleanup(dispose_widget, chart)
        chart.set_duration(100)
        chart.set_cues(cues)
        for widget in (mini, chart):
            widget.resize(300, 160 if widget is chart else 26)
            image = widget.grab().toImage()
            self.assertEqual(image.pixelColor(75, 23), QColor(CUE_COLORS[0]))
            label_pixels = [image.pixelColor(x, y).name() for x in range(77, 85) for y in range(2, 10)]
            self.assertIn("#101018", label_pixels)

    def test_delegate_cue_cache_key_changes_when_cues_are_edited(self):
        table = QTableWidget(1, 1)
        self.addCleanup(dispose_widget, table)
        delegate = MiniWaveformDelegate("#fff", "#fff", "#000", table)
        item = QTableWidgetItem()
        item.setData(delegate.CUES_ROLE, ((0, 25.0),))
        item.setData(delegate.DURATION_ROLE, 100.0)
        table.setItem(0, 0, item)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 300, 30)
        for time in (25.0, 50.0):
            item.setData(delegate.CUES_ROLE, ((0, time),))
            image = QImage(300, 30, QImage.Format_ARGB32)
            painter = QPainter(image)
            try:
                delegate.paint(painter, option, table.model().index(0, 0))
            finally:
                painter.end()
        self.assertEqual(len(delegate._cache), 2)

    def test_zoom_hides_out_of_range_cues_and_positions_visible_cue(self):
        chart = _WaveformChart("#fff", "#fff", "#000")
        self.addCleanup(dispose_widget, chart)
        chart.resize(300, 160)
        chart.set_peaks([0.8] * 101)
        chart.set_duration(100)
        chart.set_cues([{"slot": 0, "time": 25}, {"slot": 7, "time": 87.5}])
        chart.zoom_factor = 4
        chart.pan_position = 1
        image = chart.grab().toImage()
        self.assertEqual(image.pixelColor(150, 50), QColor(CUE_COLORS[7]))

    def test_live_cue_edits_update_all_list_views_without_resetting_selection(self):
        from playlistbro.ui.library_view import LibraryView, WAVEFORM_COL as LIBRARY_WAVEFORM
        from playlistbro.ui.playlist_builder import PlaylistBuilder, WAVEFORM_COL as BUILDER_WAVEFORM
        from playlistbro.ui.queue_view import QueueView, WAVEFORM_COL as QUEUE_WAVEFORM
        from playlistbro.ui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder))
            track_id = db.upsert_track(Track(filepath="song.mp3", duration=100, waveform_peaks=[0.8] * 96))
            track = db.get_track(track_id)
            library = LibraryView(db)
            builder = PlaylistBuilder(db)
            queue = QueueView(db)
            for widget in (library, builder, queue):
                self.addCleanup(dispose_widget, widget)
            builder.add_track_to_playlist(db.get_track(track_id))
            queue.add_track(db.get_track(track_id))
            for widget in (library, builder, queue):
                widget.table.selectRow(0)
            dialog = SimpleNamespace(_current_filepath="song.mp3",
                                     chart=SimpleNamespace(set_cues=Mock()))
            library.waveform_dialog = dialog
            player = SimpleNamespace(current_track=track, display_track=track, _transition_track=None)
            window = SimpleNamespace(
                library_view=library, playlist_builder=builder, queue_view=queue,
                player=player, graph_view=SimpleNamespace(mark_dirty=Mock()),
            )
            track.hot_cues = [{"slot": 0, "time": 25}]
            db.set_hot_cues(track_id, track.hot_cues)
            MainWindow._on_cues_changed(window, track)
            self.assertEqual(library.table.item(0, LIBRARY_WAVEFORM).data(
                MiniWaveformDelegate.CUES_ROLE), ((0, 25),))
            self.assertEqual(builder.table.cellWidget(0, BUILDER_WAVEFORM).cues, ((0, 25),))
            self.assertEqual(queue.table.cellWidget(0, QUEUE_WAVEFORM).cues, ((0, 25),))
            self.assertEqual(builder.current_playlist[0].hot_cues, track.hot_cues)
            for widget in (library, builder, queue):
                self.assertEqual(widget.table.currentRow(), 0)
            dialog.chart.set_cues.assert_called_once_with(track.hot_cues)
            library.waveform_dialog = None


if __name__ == "__main__":
    unittest.main()
