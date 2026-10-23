import math
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QGraphicsLineItem, QGraphicsSimpleTextItem, QLabel

from playlistbro.core.database import Database
from playlistbro.core.models import Track
from playlistbro.core.settings import load_settings
from playlistbro.ui.graph_view import SimilarityGraphView


class GraphLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = Database(Path(self.folder.name))
        self.played = []
        self.view = SimilarityGraphView(self.db, on_play_track=self.played.append)
        self.addCleanup(self.dispose)

    def dispose(self):
        self.view.close()
        self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def seed_groups(self):
        tracks = []
        for group in range(3):
            for index in range(12):
                track = Track(
                    filepath=f"group-{group}-{index}.mp3", title=f"Song {group}-{index}",
                    tempo=85 + group * 45 + index * 0.1, energy=2 + group * 3,
                    loudness=-25 + group * 8, duration=180 + group * 60,
                    spectral_features={"brightness": group / 2, "bass_ratio": 1 - group / 2},
                )
                track.id = self.db.upsert_track(track)
                tracks.append(track)
        return tracks

    def test_clusters_place_similar_tracks_nearby_and_separate_different_groups(self):
        tracks = self.seed_groups()
        matrix = self.view._feature_matrix(tracks)
        clusters = self.view._clusters(tracks, matrix)
        radii = {track.id: 8 for track in tracks}
        positions = self.view._cluster_positions(tracks, matrix, clusters, radii)
        groups = {}
        for track in tracks:
            groups.setdefault(clusters[track.id], []).append(positions[track.id])
        self.assertEqual(len(groups), 3)
        centers = [np.mean(points, axis=0) for points in groups.values()]
        for i, points in enumerate(groups.values()):
            within = max(np.linalg.norm(np.asarray(point) - centers[i]) for point in points)
            between = min(np.linalg.norm(centers[i] - center)
                          for j, center in enumerate(centers) if i != j)
            self.assertLess(within * 2, between)
        self.assertEqual(len(set(positions.values())), len(tracks))
        self.assertEqual(positions, self.view._cluster_positions(tracks, matrix, clusters, radii))

    def test_both_layouts_have_no_axes_or_artwork_details_panel(self):
        self.seed_groups()
        self.view.resize(900, 600)
        self.view.show()
        for mode in ("classical", "cluster"):
            self.view.set_layout_mode(mode)
            self.view.refresh()
            items = self.view.scene.items()
            self.assertEqual(
                sum(isinstance(item, QGraphicsLineItem) for item in items), len(self.view._edges),
            )
            self.assertEqual(
                sum(isinstance(item, QGraphicsSimpleTextItem) for item in items),
                len(self.view._labels),
            )
            self.assertFalse(hasattr(self.view, "cover_label"))
            self.assertFalse(hasattr(self.view, "details_label"))
            self.assertEqual(self.view.findChildren(QLabel), [self.view.count_label])

    def test_switch_preserves_tracks_edges_selection_and_playback_action(self):
        self.seed_groups()
        self.view.refresh()
        classical_positions = {key: node.pos() for key, node in self.view._nodes.items()}
        edges = [(a, b, score) for a, b, score, _ in self.view._edges]
        track = next(iter(self.view._nodes.values())).track
        self.view._select_track(track)
        self.assertEqual(self.played, [track])
        self.view.set_playing_id(track.id)
        self.view.set_layout_mode("cluster")
        self.view.refresh()
        self.assertEqual(set(self.view._nodes), set(classical_positions))
        self.assertEqual([(a, b, score) for a, b, score, _ in self.view._edges], edges)
        self.assertEqual(self.view._selected_id, track.id)
        self.assertEqual(self.view._playing_id, track.id)
        self.assertTrue(self.view._pulse_timer.isActive())
        self.assertEqual(self.played, [track])
        self.assertTrue(any(node.pos() != classical_positions[key]
                            for key, node in self.view._nodes.items()))
        self.view.set_layout_mode("classical")
        self.view.refresh()
        self.assertEqual({key: node.pos() for key, node in self.view._nodes.items()}, classical_positions)

    def test_small_and_identical_libraries_are_finite_in_cluster_mode(self):
        for count in (1, 2, 8, 40):
            tracks = [Track(id=i, tempo=120, energy=5) for i in range(count)]
            matrix = self.view._feature_matrix(tracks)
            clusters = self.view._clusters(tracks, matrix)
            positions = self.view._cluster_positions(
                tracks, matrix, clusters, {track.id: 8 for track in tracks},
            )
            self.assertEqual(len(set(positions.values())), count)
            self.assertTrue(all(math.isfinite(value) for point in positions.values() for value in point))

    def test_playing_pulse_is_large_visible_and_resets_on_track_change(self):
        tracks = self.seed_groups()
        self.view.refresh()
        first, second = tracks[:2]
        self.view.set_playing_id(first.id)
        node = self.view._nodes[first.id]
        self.view._pulse_phase = -math.pi / 2 - 0.18
        self.view._pulse_playing()
        small_scale = node.scale()
        self.view._pulse_phase = math.pi / 2 - 0.18
        self.view._pulse_playing()
        self.assertGreater(node.scale() - small_scale, 0.4)
        halo = self.view._playing_halo
        self.assertIs(halo.parentItem(), node)
        self.assertGreater(halo.rect().width(), node.rect().width())
        self.assertTrue(halo.pen().isCosmetic())
        self.view._highlight(second.id)
        self.assertEqual(node.opacity(), 1.0)
        self.view.set_playing_id(second.id)
        self.assertEqual(node.scale(), 1.0)
        self.assertEqual(node.zValue(), 1)
        self.assertEqual(node.brush().color(), node.base_color)
        self.assertIs(halo.parentItem(), self.view._nodes[second.id])
        self.view.set_playing_id(None)
        self.assertFalse(halo.isVisible())
        self.assertFalse(self.view._pulse_timer.isActive())

    def test_empty_library_and_invalid_layout(self):
        for mode in ("cluster", "classical"):
            self.view.set_layout_mode(mode)
            self.view.refresh()
            self.assertFalse(self.view._nodes)
            self.assertFalse(self.view._dirty)
            self.assertIn("Scan music", self.view.count_label.text())
        with self.assertRaises(ValueError):
            self.view.set_layout_mode("unsupported")

    def test_menu_handler_persists_and_restores_layout(self):
        from playlistbro.ui.main_window import MainWindow

        with patch("playlistbro.core.settings._settings_path",
                   return_value=Path(self.folder.name) / "settings.json"):
            settings = load_settings()
            self.assertEqual(settings["graph_layout"], "classical")
            window = SimpleNamespace(graph_view=self.view, settings=settings)
            MainWindow._set_graph_layout(window, "cluster")
            self.assertEqual(load_settings()["graph_layout"], "cluster")
            self.assertEqual(self.view.layout_mode, "cluster")


if __name__ == "__main__":
    unittest.main()
