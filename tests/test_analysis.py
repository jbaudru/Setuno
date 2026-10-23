import importlib.util
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from playlistbro.core import analyzer, scanner
from playlistbro.core.database import Database
from playlistbro.core.models import Track


class AnalysisTests(unittest.TestCase):
    @staticmethod
    def drum_track(bpm, duration=90, offset=0.137):
        sr = analyzer.ANALYSIS_SR
        samples = np.zeros(round(sr * duration), dtype=np.float32)
        rng = np.random.default_rng(7)
        for index, beat in enumerate(np.arange(offset, duration, 60 / bpm)):
            if index % 17 == 8:
                continue
            position = round(beat * sr)
            length = min(round(sr * 0.05), len(samples) - position)
            t = np.arange(length) / sr
            samples[position:position + length] += np.sin(2 * np.pi * 75 * t) * np.exp(-60 * t)
            hat = round((beat + 30 / bpm) * sr)
            length = min(250, len(samples) - hat)
            if length > 0:
                samples[hat:hat + length] += rng.normal(0, 0.1, length) * np.exp(-np.arange(length) / 40)
        return samples

    def test_full_track_grid_precision_with_missing_beats_and_offbeat_hats(self):
        for bpm in (90, 127.37, 174):
            with self.subTest(bpm=bpm):
                samples = self.drum_track(bpm, duration=180)
                start = time.perf_counter()
                grid = analyzer.compute_beat_grid(samples, analyzer.ANALYSIS_SR, bpm * 1.003)
                self.assertIsNotNone(grid)
                self.assertLess(time.perf_counter() - start, 2.0)
                self.assertAlmostEqual(grid["bpm"], bpm, delta=0.01)
                period = 60 / bpm
                for seconds in (0, 180):
                    phase_error = ((grid["offset"] - 0.137
                                    + seconds * (grid["bpm"] / bpm - 1)
                                    + period / 2) % period) - period / 2
                    self.assertLess(abs(phase_error), 0.012)

    def test_grid_rejects_silence_and_invalid_inputs(self):
        silence = np.zeros(analyzer.ANALYSIS_SR * 8, dtype=np.float32)
        for bpm in (0, float("nan"), float("inf"), 128):
            self.assertIsNone(analyzer.compute_beat_grid(silence, analyzer.ANALYSIS_SR, bpm))
        noise = np.random.default_rng(42).normal(0, 0.1, analyzer.ANALYSIS_SR * 30).astype(np.float32)
        self.assertIsNone(analyzer.compute_beat_grid(noise, analyzer.ANALYSIS_SR, 128))

    def test_numpy_fallback_tempos_use_actual_lag_correlation(self):
        for bpm in (90, 128, 174):
            envelope = np.zeros(6000)
            period = 60 * analyzer.ANALYSIS_SR / (analyzer.TEMPO_HOP_SIZE * bpm)
            positions = np.rint(np.arange(0, len(envelope), period)).astype(int)
            envelope[positions[positions < len(envelope)]] = 1
            self.assertAlmostEqual(
                analyzer._estimate_tempo(envelope, analyzer.ANALYSIS_SR, analyzer.TEMPO_HOP_SIZE),
                bpm, delta=1.0,
            )

    def test_manual_tempo_is_not_refined_or_redetected(self):
        samples = self.drum_track(128, duration=12)
        with patch.object(analyzer, "_estimate_tempo_librosa") as detect:
            tempo, offset = analyzer._analyze_timing(samples, tempo_override=128.0)
        detect.assert_not_called()
        self.assertEqual(tempo, 128.0)
        self.assertIsNotNone(offset)

    @unittest.skipUnless(importlib.util.find_spec("librosa"), "librosa is optional")
    def test_timing_key_reanalysis_decodes_once_and_never_writes_audio(self):
        samples = self.drum_track(128, duration=12)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pulse.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(analyzer.ANALYSIS_SR)
                output.writeframes((samples * 16000).astype(np.int16).tobytes())
            before = path.read_bytes()
            result = analyzer.analyze_timing_and_key(str(path))
            self.assertEqual(set(result), {"tempo", "beat_offset", "key_name", "camelot"})
            self.assertAlmostEqual(result["tempo"], 128, delta=0.02)
            self.assertIsNotNone(result["beat_offset"])
            self.assertEqual(path.read_bytes(), before)

    @unittest.skipUnless(importlib.util.find_spec("librosa"), "librosa is optional")
    def test_key_detection_rejects_percussive_noise(self):
        noise = np.random.default_rng(42).normal(0, 0.1, 22050 * 8).astype(np.float32)
        self.assertEqual(analyzer._detect_key_librosa(noise, 22050), "")

    @unittest.skipUnless(importlib.util.find_spec("librosa"), "librosa is optional")
    def test_detuned_major_and_minor_keys_in_all_twelve_roots(self):
        sr = 22050
        t = np.arange(sr) / sr
        for mode, third in (("major", 4), ("minor", 3)):
            for root, name in enumerate(analyzer.NOTE_NAMES):
                with self.subTest(key=name, mode=mode):
                    chunks = []
                    for degree in (0, 5, 7, 0):
                        notes = (root + degree, root + degree + third, root + degree + 7)
                        chunks.append(sum(np.sin(2 * np.pi * 440 * 2 ** ((48 + note - 69 + 0.27) / 12) * t)
                                          for note in notes) / 3)
                    samples = np.concatenate(chunks).astype(np.float32)
                    self.assertEqual(analyzer._detect_key_librosa(samples, sr),
                                     analyzer.normalize_key_name(name, mode))

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