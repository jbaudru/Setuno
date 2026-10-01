import importlib.util
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from playlistbro.core import analyzer, scanner
from playlistbro.core.database import Database
from playlistbro.core.models import Track


class AnalysisTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("librosa"), "librosa is optional")
    def test_known_pulse_tempos(self):
        sample_rate = 22050
        for bpm in (90, 128, 174):
            with self.subTest(bpm=bpm):
                samples = np.zeros(sample_rate * 12, dtype=np.float32)
                samples[::round(sample_rate * 60 / bpm)] = 1
                estimate = analyzer._estimate_tempo_librosa(samples, sample_rate)
                self.assertIsNotNone(estimate)
                self.assertAlmostEqual(estimate, bpm, delta=3)

    @unittest.skipUnless(importlib.util.find_spec("librosa"), "librosa is optional")
    def test_beat_grid_fits_tempo_and_phase(self):
        sample_rate = 22050
        samples = np.zeros(sample_rate * 12, dtype=np.float32)
        beats = np.arange(0.14, 12, 60 / 128)
        samples[np.rint(beats * sample_rate).astype(int)] = 1
        grid = {}
        tempo = analyzer._estimate_tempo_librosa(samples, sample_rate, beat_grid=grid)
        self.assertAlmostEqual(tempo, 128, delta=0.5)
        self.assertAlmostEqual(grid["offset"], 0.14, delta=0.04)

    def test_full_waveform_includes_last_transient(self):
        samples = np.zeros(1005, dtype=np.float32)
        samples[-1] = 1
        peaks = analyzer._downsample_waveform_peaks(samples, 600)
        self.assertEqual(len(peaks), 600)
        self.assertEqual(peaks[-1], 1.0)

    def test_manual_bpm_invalidates_measured_grid(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder))
            track_id = db.upsert_track(Track(filepath="song.mp3", tempo=128.02, beat_offset=0.156))
            self.assertAlmostEqual(db.get_track(track_id).beat_offset, 0.156)
            db.set_manual_tempo(track_id, 130)
            self.assertIsNone(db.get_track(track_id).beat_offset)

    @unittest.skipUnless(importlib.util.find_spec("librosa"), "librosa is optional")
    def test_major_chord_progression(self):
        sample_rate = 22050
        time = np.arange(sample_rate * 2) / sample_rate
        chords = (
            (261.63, 329.63, 392.0),
            (349.23, 440.0, 523.25),
            (392.0, 493.88, 587.33),
            (261.63, 329.63, 392.0),
        )
        samples = np.concatenate([
            sum(np.sin(2 * np.pi * frequency * time) for frequency in chord) / 3
            for chord in chords
        ]).astype(np.float32)
        self.assertEqual(analyzer._detect_key_librosa(samples, sample_rate), "C major")

    def test_silence_and_genre_provenance(self):
        self.assertEqual(analyzer._detect_key(np.zeros(12)), "")
        self.assertEqual(analyzer._estimate_genre({"genre": "", "_silent": True}, 120), "")
        folder = analyzer._genre_from_folder("/Music/Bass House/Track.mp3")
        self.assertEqual(folder, "Bass House")
        self.assertEqual(analyzer._estimate_genre({"genre": "", "_folder_genre": folder}, 128), folder)
        self.assertEqual(
            analyzer._estimate_genre({"genre": "Tech House", "_folder_genre": folder}, 128),
            "Tech House",
        )

    def test_silent_result_is_track_compatible(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "silent.wav"
            path.touch()
            with patch.object(
                analyzer, "_decode_audio", return_value=(np.zeros(22050, dtype=np.float32), 1.0, [0.0] * 2400, 0.0)
            ), patch.object(analyzer, "_extract_cover", return_value=""):
                track = Track(**analyzer.analyze_file(str(path)))
            self.assertEqual((track.tempo, track.key_name, track.genre), (0, "", ""))

    def test_scan_uses_two_analysis_threads(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("first.mp3", "second.mp3"):
                (Path(folder) / name).touch()
            barrier = threading.Barrier(2)
            workers = set()

            def analyze(filepath):
                workers.add(threading.current_thread().name)
                barrier.wait(timeout=5)
                return {"filepath": filepath, "title": Path(filepath).stem,
                        "artist": "Artist", "album": "Album", "genre": "House", "cover_path": "cover"}

            db = Mock()
            db.get_existing_filepaths.return_value = {}
            with patch.object(scanner, "analyze_file", side_effect=analyze):
                scanner.scan_folder(folder, db, max_workers=2)
            self.assertEqual(len(workers), 2)
            self.assertEqual(db.upsert_track.call_count, 2)

    @unittest.skipUnless(importlib.util.find_spec("soundfile"), "audio fixtures need soundfile")
    def test_scan_stores_file_stats_after_tag_write(self):
        import soundfile as sf
        from mutagen import File as MutagenFile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "song.wav"
            sf.write(str(path), np.zeros(22050, dtype=np.float32), 22050)
            data = {"filepath": str(path), "title": "Song", "artist": "Artist", "album": "Album",
                    "genre": "House", "cover_path": "unavailable.jpg", "mtime": 0, "filesize": 0}
            db = Mock()
            db.get_existing_filepaths.return_value = {}
            with patch.object(scanner, "analyze_file", return_value=data):
                scanner.scan_folder(folder, db, max_workers=1)
            stored = db.upsert_track.call_args.args[0]
            self.assertEqual(str(MutagenFile(path).tags["TALB"]), "Album")
            self.assertEqual(stored.mtime, path.stat().st_mtime)
            self.assertEqual(stored.filesize, path.stat().st_size)

    def test_online_genre_replaces_only_an_inferred_genre(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "song.mp3"
            path.touch()
            db = Mock()
            db.get_existing_filepaths.return_value = {}
            data = {"filepath": str(path), "title": "song", "artist": "Artist",
                    "album": "Album", "cover_path": "", "genre": "House"}
            with patch.object(scanner, "analyze_file", return_value=data), patch(
                "playlistbro.core.metadata_lookup.enrich_metadata",
                side_effect=lambda metadata: dict(metadata, genre="Electronic"),
            ):
                scanner.scan_folder(folder, db, max_workers=1)
            self.assertEqual(db.upsert_track.call_args.args[0].genre, "Electronic")

    @unittest.skipUnless(importlib.util.find_spec("soundfile"), "audio fixtures need soundfile")
    def test_missing_file_metadata_preserves_existing_tags(self):
        import soundfile as sf
        from mutagen import File as MutagenFile
        from mutagen.id3 import TALB
        from playlistbro.core.metadata_lookup import write_missing_file_metadata

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "cover.jpg"
            image.write_bytes(bytes.fromhex("ffd8ffd9"))
            samples = np.sin(2 * np.pi * 440 * np.arange(44100) / 22050).astype(np.float32)
            with patch.object(analyzer, "default_data_dir", return_value=root):
                for suffix in (".mp3", ".wav", ".flac", ".ogg"):
                    with self.subTest(suffix=suffix):
                        path = root / ("song" + suffix)
                        sf.write(str(path), samples, 22050)
                        if suffix == ".wav":
                            audio = MutagenFile(path)
                            audio.add_tags()
                            audio.tags.add(TALB(encoding=3, text=""))
                            audio.save()
                        self.assertTrue(write_missing_file_metadata(
                            str(path), {"title": "Original", "album": "Album", "cover_path": str(image)}
                        ))
                        self.assertFalse(write_missing_file_metadata(
                            str(path), {"title": "Replacement", "album": "Replacement", "cover_path": str(image)}
                        ))
                        audio = MutagenFile(path)
                        if suffix in (".mp3", ".wav"):
                            self.assertEqual(str(audio.tags["TALB"]), "Album")
                            self.assertEqual(str(audio.tags["TIT2"]), "Original")
                            self.assertTrue(audio.tags.getall("APIC"))
                        else:
                            self.assertEqual(audio["album"], ["Album"])
                            self.assertEqual(audio["title"], ["Original"])
                            self.assertTrue(audio.pictures if suffix == ".flac" else audio.get("metadata_block_picture"))


if __name__ == "__main__":
    unittest.main()