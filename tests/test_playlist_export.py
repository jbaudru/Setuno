import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from playlistbro.core.exporter import export_rekordbox_xml
from playlistbro.core.models import Track
from playlistbro.ui.playlist_export import (
    M3U8_FILTER, M3U_FILTER, XML_FILTER, export_playlist,
)


class RekordboxExportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.audio = self.root / "DJ \u00e9 & #1.mp3"
        self.audio.write_bytes(b"test audio")
        self.track = Track(
            filepath=str(self.audio), title="Song & <Mix>", artist="DJ \u00e9",
            album="Album", genre="House", duration=182.7, tempo=125.25, key_name="A minor",
        )
        self.output = self.root / "playlist.xml"

    def test_xml_structure_metadata_and_encoded_local_file_location(self):
        result = export_rekordbox_xml([self.track], str(self.output), "Night & <Day>")
        self.assertEqual(result, str(self.output))
        self.assertTrue(self.output.read_bytes().startswith(b"<?xml"))
        root = ET.parse(self.output).getroot()
        self.assertEqual(root.tag, "DJ_PLAYLISTS")
        self.assertEqual(root.get("Version"), "1.0.0")
        self.assertEqual(root.find("PRODUCT").get("Name"), "Setuno")
        self.assertEqual(root.find("COLLECTION").get("Entries"), "1")
        track = root.find("COLLECTION/TRACK")
        for key, value in {
            "TrackID": "1", "Name": self.track.title, "Artist": self.track.artist,
            "Album": "Album", "Genre": "House", "AverageBpm": "125.25",
            "Tonality": "Am", "TotalTime": "182", "Size": "10",
        }.items():
            self.assertEqual(track.get(key), value)
        self.assertEqual(
            track.get("Location"),
            self.audio.as_uri().replace("file:///", "file://localhost/", 1),
        )
        self.assertIn("%23", track.get("Location"))
        self.assertIn("%C3%A9", track.get("Location"))
        self.assertIsNone(track.find("TEMPO"))
        folder = root.find("PLAYLISTS/NODE")
        self.assertEqual(folder.attrib, {"Type": "0", "Name": "ROOT", "Count": "1"})
        playlist = folder.find("NODE")
        self.assertEqual(playlist.attrib, {
            "Name": "Night & <Day>", "Type": "1", "KeyType": "0", "Entries": "1",
        })
        self.assertEqual(playlist.find("TRACK").get("Key"), "1")

    def test_duplicate_tracks_share_collection_entry_but_preserve_playlist_order(self):
        other_path = self.root / "other.flac"
        other_path.touch()
        other = Track(filepath=str(other_path), title="Other", key_name="C major")
        export_rekordbox_xml([other, self.track, other], str(self.output), "Set")
        root = ET.parse(self.output).getroot()
        self.assertEqual(root.find("COLLECTION").get("Entries"), "2")
        self.assertEqual(root.find("COLLECTION/TRACK").get("Tonality"), "C")
        self.assertIsNone(root.find("COLLECTION/TRACK").get("AverageBpm"))
        self.assertEqual(
            [t.get("Key") for t in root.findall("PLAYLISTS/NODE/NODE/TRACK")],
            ["1", "2", "1"],
        )

    def test_measured_beat_grid_is_exported_to_rekordbox(self):
        self.track.beat_offset = 0.137123
        export_rekordbox_xml([self.track], str(self.output), "Set")
        tempo = ET.parse(self.output).find("COLLECTION/TRACK/TEMPO")
        self.assertEqual(tempo.attrib, {
            "Inizio": "0.137123", "Bpm": "125.25", "Metro": "4/4", "Battito": "1",
        })

    def test_missing_audio_does_not_overwrite_existing_export(self):
        self.output.write_text("Existing export")
        self.audio.unlink()
        with self.assertRaises(FileNotFoundError):
            export_rekordbox_xml([self.track], str(self.output), "Set")
        self.assertEqual(self.output.read_text(), "Existing export")

    def test_empty_name_rejected(self):
        with self.assertRaises(ValueError):
            export_rekordbox_xml([self.track], str(self.output), " ")
        self.assertFalse(self.output.exists())

    def test_failed_replace_keeps_original_and_cleans_temporary_file(self):
        self.output.write_text("Existing export")
        with patch.object(Path, "replace", side_effect=OSError("Permission denied")):
            with self.assertRaises(OSError):
                export_rekordbox_xml([self.track], str(self.output), "Set")
        self.assertEqual(self.output.read_text(), "Existing export")
        self.assertFalse(self.output.with_suffix(".xml.tmp").exists())


class PlaylistExportDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        audio = self.root / "song.mp3"
        audio.touch()
        self.tracks = [Track(filepath=str(audio), title="Song", tempo=120)]

    def test_format_selection_exports_xml_m3u8_and_m3u_with_correct_extension(self):
        for selected_filter, suffix in (
            (XML_FILTER, ".xml"), (M3U8_FILTER, ".m3u8"), (M3U_FILTER, ".m3u"),
        ):
            output = self.root / ("Set" + suffix)
            with patch("playlistbro.ui.playlist_export.QFileDialog.getSaveFileName",
                       return_value=(str(self.root / "Set"), selected_filter)), \
                    patch.object(QMessageBox, "information") as message:
                export_playlist(None, self.tracks, "Set")
            self.assertTrue(output.exists())
            message.assert_called_once()
            if suffix == ".xml":
                self.assertEqual(ET.parse(output).find("PLAYLISTS/NODE/NODE").get("Name"), "Set")
                self.assertIn("Import Playlist", message.call_args.args[2])
            else:
                self.assertTrue(output.read_text(encoding="utf-8").startswith("#EXTM3U"))

    def test_cancel_and_empty_playlist_do_not_write_files(self):
        with patch("playlistbro.ui.playlist_export.QFileDialog.getSaveFileName",
                   return_value=("", "")), patch.object(QMessageBox, "information") as info:
            export_playlist(None, self.tracks)
            info.assert_not_called()
        with patch.object(QMessageBox, "warning") as warning:
            export_playlist(None, [])
            warning.assert_called_once()
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_missing_file_reports_error_instead_of_success(self):
        Path(self.tracks[0].filepath).unlink()
        output = self.root / "Set.xml"
        with patch("playlistbro.ui.playlist_export.QFileDialog.getSaveFileName",
                   return_value=(str(output), XML_FILTER)), \
                patch.object(QMessageBox, "warning") as warning, \
                patch.object(QMessageBox, "information") as info:
            export_playlist(None, self.tracks)
        warning.assert_called_once()
        info.assert_not_called()
        self.assertFalse(output.exists())

    def test_extension_correction_does_not_overwrite_without_confirmation(self):
        output = self.root / "Set.xml"
        output.write_text("Existing")
        with patch("playlistbro.ui.playlist_export.QFileDialog.getSaveFileName",
                   return_value=(str(self.root / "Set.m3u"), XML_FILTER)), \
                patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            export_playlist(None, self.tracks)
        self.assertEqual(output.read_text(), "Existing")

    def test_builder_uses_shared_export_flow(self):
        from playlistbro.ui.playlist_builder import PlaylistBuilder

        view = SimpleNamespace(current_playlist=self.tracks, _ensure_files_available=lambda: True)
        with patch("playlistbro.ui.playlist_builder.export_playlist") as export:
            PlaylistBuilder.save_m3u(view)
        export.assert_called_once_with(view, self.tracks)


if __name__ == "__main__":
    unittest.main()
