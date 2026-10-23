import os
import array
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem, QTableWidget, QTableWidgetItem

from playlistbro.core.analyzer import ANALYSIS_SR, compute_waveform_colors
from playlistbro.core import analyzer
from playlistbro.core.database import Database
from playlistbro.core.models import Track
from playlistbro.core.settings import load_settings, save_settings
from playlistbro.ui.theme import PALETTES
from playlistbro.ui.waveform_view import (
    MiniWaveform, MiniWaveformDelegate, _WaveformChart, _WaveformWorker, _band_color, reduce_bands,
    waveform_appearance,
)


class WaveformBandAnalysisTests(unittest.TestCase):
    def test_bass_mid_high_tones_select_the_correct_band(self):
        time = np.arange(ANALYSIS_SR) / ANALYSIS_SR
        for band, frequency in enumerate((80, 1000, 8000)):
            samples = np.sin(2 * np.pi * frequency * time).astype(np.float32)
            measured = np.asarray(compute_waveform_colors(samples, ANALYSIS_SR, 30))
            self.assertEqual(measured.shape, (30, 3))
            self.assertEqual(int(np.argmax(measured.mean(axis=0))), band)
            other = np.delete(measured.mean(axis=0), band)
            self.assertGreater(measured.mean(axis=0)[band], 20 * float(other.max()))

    def test_band_timeline_tracks_frequency_changes(self):
        time = np.arange(ANALYSIS_SR) / ANALYSIS_SR
        samples = np.concatenate([np.sin(2 * np.pi * hz * time) for hz in (80, 1000, 8000)])
        measured = np.asarray(compute_waveform_colors(samples, ANALYSIS_SR, 90))
        self.assertEqual([int(np.argmax(row)) for row in measured[[15, 45, 75]]], [0, 1, 2])
        reduced = np.asarray(reduce_bands(measured.tolist(), 9))
        self.assertEqual([int(np.argmax(row)) for row in reduced[[1, 4, 7]]], [0, 1, 2])

    def test_silence_empty_and_short_audio_are_finite(self):
        self.assertEqual(compute_waveform_colors(np.zeros(0), ANALYSIS_SR), [])
        for samples in (np.zeros(100), np.ones(1), np.ones(8)):
            measured = np.asarray(compute_waveform_colors(samples, ANALYSIS_SR, 12))
            self.assertEqual(measured.shape, (12, 3))
            self.assertTrue(np.isfinite(measured).all())
            self.assertGreaterEqual(float(measured.min()), 0)
            self.assertLessEqual(float(measured.max()), 1)

    def test_database_round_trip_and_old_tracks_without_band_data(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder))
            bands = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            track_id = db.upsert_track(Track(filepath="colored.mp3", waveform_bands=bands))
            old_id = db.upsert_track(Track(filepath="old.mp3"))
            loaded = Database(Path(folder))
            self.assertEqual(loaded.get_track(track_id).waveform_bands, bands)
            self.assertEqual(loaded.get_track(old_id).waveform_bands, [])

    def test_analysis_colors_use_full_track_not_the_short_analysis_window(self):
        time = np.arange(ANALYSIS_SR) / ANALYSIS_SR
        samples = np.concatenate([np.sin(2 * np.pi * hz * time) for hz in (80, 1000, 8000)])
        decoded = SimpleNamespace(samples=array.array("f", samples))
        bands = []
        with patch("miniaudio.decode_file", return_value=decoded), \
                patch.object(analyzer, "ANALYSIS_MAX_SECONDS", 1):
            window, duration, peaks, start = analyzer._decode_audio("track.wav", bands)
        self.assertEqual(len(window), ANALYSIS_SR)
        self.assertEqual(duration, 3)
        self.assertEqual(start, 1)
        self.assertEqual(len(bands), len(peaks))
        self.assertEqual([int(np.argmax(bands[index])) for index in (400, 1200, 2000)],
                         [0, 1, 2])


class WaveformColorRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.bands = [[1, 0, 0]] * 32 + [[0, 1, 0]] * 32 + [[0, 0, 1]] * 32
        self.addCleanup(waveform_appearance.configure, "light", False)

    def test_high_frequencies_use_main_theme_accent_with_distinct_other_bands(self):
        for theme, palette in PALETTES.items():
            self.assertEqual(palette["wave_high"], palette["accent"], theme)
            colors = [QColor(palette[key]).getRgb()[:3]
                      for key in ("wave_bass", "wave_mid", "wave_high")]
            for index, color in enumerate(colors):
                for other in colors[index + 1:]:
                    self.assertGreater(sum((a - b) ** 2 for a, b in zip(color, other)), 10000, theme)
            for key in ("wave_bass", "wave_mid"):
                color = QColor(palette[key])
                self.assertGreaterEqual(color.hsvSaturationF(), 0.5)
                rgb = color.getRgb()[:3]
                self.assertGreaterEqual(max(rgb) - min(rgb), 100)

    def test_theme_colors_and_monochrome_toggle_in_mini_and_full_waveforms(self):
        for theme, palette in PALETTES.items():
            waveform_appearance.configure(theme, True)
            mini = MiniWaveform(
                [0.8] * 96, palette["accent_strong"], palette["accent"], palette["bg"],
                bands=self.bands,
            )
            chart = _WaveformChart(palette["accent_strong"], palette["accent"], palette["bg"])
            self.addCleanup(mini.close)
            self.addCleanup(chart.close)
            chart.set_peaks([0.8] * 96)
            chart.set_bands(self.bands)
            for widget in (mini, chart):
                widget.resize(300, 200 if widget is chart else 26)
                image = widget.grab().toImage()
                for x, key in ((40, "wave_bass"), (150, "wave_mid"), (260, "wave_high")):
                    self.assertEqual(
                        image.pixelColor(x, widget.height() // 2 - 3),
                        QColor(palette[key]), (theme, key),
                    )
                    self.assertEqual(
                        image.pixelColor(x, widget.height() // 2 - (45 if widget is chart else 7)),
                        QColor(palette["accent"]), (theme, key, "main silhouette"),
                    )
            waveform_appearance.configure(theme, False)
            for widget in (mini, chart):
                image = widget.grab().toImage()
                for x in (40, 150, 260):
                    self.assertEqual(
                        image.pixelColor(x, widget.height() // 2 - 3),
                        QColor(palette["accent_strong"]),
                    )

    def test_mixed_frequency_colors_blend_and_silence_uses_theme_accent(self):
        waveform_appearance.configure("calm", True)
        self.assertEqual(_band_color([1, 0, 0]), QColor(PALETTES["calm"]["wave_bass"]))
        self.assertEqual(_band_color([0, 0, 0]), QColor(PALETTES["calm"]["accent_strong"]))
        mixed = _band_color([1, 1, 1])
        self.assertNotIn(mixed.name(), [PALETTES["calm"][key]
                                       for key in ("wave_bass", "wave_mid", "wave_high")])

    def test_grid_line_density_depends_on_zoom_and_stays_readable(self):
        chart = _WaveformChart("#fff", "#fff", "#000")
        self.addCleanup(chart.close)
        chart.resize(1000, 200)
        chart.set_peaks([0.8] * 2400)
        chart.set_duration(300)
        chart.set_bpm(120)
        counts = []
        for zoom in (1, 4, 16, 64):
            chart.zoom_factor = zoom
            start, end = chart._visible_range()
            duration = chart._sample_to_time(end - 1) - chart._sample_to_time(start)
            px_per_beat = chart.width() * 0.5 / duration
            stride = chart._grid_stride(px_per_beat)
            self.assertGreaterEqual(px_per_beat * stride, 16)
            painter = Mock()
            chart._draw_grid(painter)
            count = painter.drawLine.call_count
            self.assertLessEqual(count, chart.width() // 16 + 2)
            counts.append(count)
        self.assertGreater(chart._grid_stride(1), chart._grid_stride(10))
        self.assertEqual(chart._grid_stride(20), 1)
        self.assertGreater(counts[0], 0)

    def test_dominant_frequency_stays_more_colorful_than_linear_blending(self):
        for theme in PALETTES:
            waveform_appearance.configure(theme, True)
            colors = waveform_appearance.rgb
            linear = [round((3 * colors[0][channel] + colors[1][channel]
                             + colors[2][channel]) / 5) for channel in range(3)]
            actual = _band_color([3, 1, 1]).getRgb()[:3]
            self.assertGreater(max(actual) - min(actual), max(linear) - min(linear), theme)

    def test_delegate_cache_invalidates_on_toggle_and_theme_switch(self):
        table = QTableWidget(1, 1)
        self.addCleanup(table.close)
        delegate = MiniWaveformDelegate("#1677ff", "#2f8bff", "#ffffff", table)
        item = QTableWidgetItem()
        item.setData(delegate.PEAKS_ROLE, tuple([0.8] * 96))
        item.setData(delegate.BANDS_ROLE, reduce_bands(self.bands))
        table.setItem(0, 0, item)
        image = QImage(300, 30, QImage.Format_ARGB32)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 300, 30)
        painter = QPainter(image)
        try:
            delegate.paint(painter, option, table.model().index(0, 0))
        finally:
            painter.end()
        self.assertEqual(len(delegate._cache), 1)
        waveform_appearance.configure("dark", True)
        self.assertFalse(delegate._cache)
        self.assertEqual(_band_color([0, 1, 0]), QColor(PALETTES["dark"]["wave_mid"]))

    def test_zoom_uses_same_track_band_timeline(self):
        waveform_appearance.configure("rekordbox", True)
        palette = PALETTES["rekordbox"]
        chart = _WaveformChart(palette["accent_strong"], palette["accent"], palette["bg"])
        self.addCleanup(chart.close)
        chart.resize(300, 200)
        chart.set_peaks([0.8] * 96)
        chart.set_bands(self.bands)
        chart.zoom_factor = 4
        chart.pan_position = 1.0
        image = chart.grab().toImage()
        self.assertEqual(image.pixelColor(150, 97), QColor(palette["wave_high"]))
        chart.set_duration(100)
        chart.set_detail_peaks(np.full(50000, 0.8))
        chart.zoom_factor = 64
        image = chart.grab().toImage()
        self.assertEqual(image.pixelColor(150, 97), QColor(palette["wave_high"]))
        chart.set_peaks([0.5] * 96)
        self.assertEqual(chart.bands, [])

    def test_menu_handler_persists_setting_and_updates_existing_and_new_widgets(self):
        from playlistbro.ui.main_window import MainWindow

        widget = MiniWaveform([0.8] * 96, "#111111", "#222222", "#ffffff", bands=self.bands)
        self.addCleanup(widget.close)
        with tempfile.TemporaryDirectory() as folder:
            settings_path = Path(folder) / "settings.json"
            with patch("playlistbro.core.settings._settings_path", return_value=settings_path):
                settings = load_settings()
                self.assertFalse(settings["multicolor_waveforms"])
                window = SimpleNamespace(settings=settings, theme="calm")
                MainWindow._set_multicolor_waveforms(window, True)
                self.assertTrue(load_settings()["multicolor_waveforms"])
                self.assertTrue(waveform_appearance.enabled)
                self.assertEqual(waveform_appearance.palette, PALETTES["calm"])
                settings["theme"] = "dark"
                save_settings(settings)
                self.assertTrue(load_settings()["multicolor_waveforms"])
                MainWindow._set_multicolor_waveforms(window, False)
                self.assertFalse(load_settings()["multicolor_waveforms"])

    def test_worker_delivers_full_waveform_and_real_band_data(self):
        samples = np.sin(2 * np.pi * 80 * np.arange(ANALYSIS_SR) / ANALYSIS_SR)
        decoded = SimpleNamespace(samples=array.array("f", samples))
        worker = _WaveformWorker("track.wav", None)
        received_peaks = []
        received_bands = []
        worker.done.connect(lambda *args: received_peaks.append(args))
        worker.bands_ready.connect(lambda *args: received_bands.append(args))
        with patch("miniaudio.decode_file", return_value=decoded):
            worker.run()
        self.assertEqual(received_peaks[0][0], "track.wav")
        self.assertEqual(len(received_peaks[0][1]), 2400)
        self.assertEqual(len(received_bands[0][1]), 2400)
        self.assertEqual(int(np.argmax(np.mean(received_bands[0][1], axis=0))), 0)


if __name__ == "__main__":
    unittest.main()
