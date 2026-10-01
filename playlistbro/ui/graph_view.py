"""Interactive song-similarity graph for discovering tracks in the library."""
import math

import numpy as np

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
    QGraphicsSimpleTextItem, QMenu, QPushButton, QVBoxLayout, QWidget,
)

from ..core.database import Database
from ..core.models import Track
from ..core.playlist_engine import track_similarity
from .icon_loader import cover_pixmap, icon
from .stats_widget import palette_shade

BPM_SCALE = 8.0  # scene units per BPM
ENERGY_SCALE = 80.0  # scene units per energy point
NODE_GAP = 3.0
NODE_SPACING = 0.9  # lattice step as a fraction of the largest node diameter
NODE_NUDGE = 0.1  # max shift (in lattice steps) from a slot toward the exact point


class _GraphView(QGraphicsView):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not isinstance(self.itemAt(event.pos()), _TrackNode):
            self.parentWidget()._clear_highlight()
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)
        self.parentWidget()._update_label_visibility()


class _TrackNode(QGraphicsEllipseItem):
    def __init__(self, track: Track, callback, highlight_callback, queue_callback, *args):
        super().__init__(*args)
        self.track = track
        self.callback = callback
        self.highlight_callback = highlight_callback
        self.queue_callback = queue_callback
        self.setAcceptHoverEvents(True)
        self.setToolTip(track.display_name)

    def mousePressEvent(self, event):
        self._press_screen_pos = event.screenPos()
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()
        if (event.button() == Qt.LeftButton
            and (event.screenPos() - self._press_screen_pos).manhattanLength() < 6):
            self.callback(self.track)

    def hoverEnterEvent(self, event):
        self.highlight_callback(self.track.id)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.highlight_callback(None)
        super().hoverLeaveEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu()
        queue_action = menu.addAction("Add to Queue")
        if menu.exec(event.screenPos()) == queue_action and self.queue_callback:
            self.queue_callback(self.track)


class SimilarityGraphView(QWidget):
    """Shows tracks as nodes with links to their strongest feature matches."""

    def __init__(self, db: Database, on_play_track=None, on_queue_track=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.on_play_track = on_play_track
        self.on_queue_track = on_queue_track
        self._node_color = QColor("#8686AC")
        self._edge_color = QColor("#6b6b85")
        self._label_color = QColor("#d8d8ec")
        self._bg_color = QColor("#33334d")
        self._nodes = {}
        self._labels = {}
        self._edges = []
        self._selected_id = None
        self._playing_id = None
        self._pulse_phase = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(70)
        self._pulse_timer.timeout.connect(self._pulse_playing)
        self._played_ids: set[int] = set()
        self._dirty = True
        self.scene = QGraphicsScene(self)
        self.view = _GraphView(self.scene)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)

        self.reset_btn = QPushButton()
        self.reset_btn.setIcon(icon("reset"))
        self.reset_btn.setFixedSize(28, 28)
        self.reset_btn.setToolTip("Reset graph view")
        self.reset_btn.clicked.connect(self.reset_view)
        self.count_label = QLabel()
        top = QHBoxLayout()
        top.addWidget(self.reset_btn)
        top.addWidget(self.count_label)
        top.addStretch(1)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(72, 72)
        self.cover_label.setPixmap(cover_pixmap("", 72))
        self.details_label = QLabel("Select a song to play it and inspect its features.")
        self.details_label.setWordWrap(True)
        details = QHBoxLayout()
        details.addWidget(self.cover_label)
        details.addWidget(self.details_label, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)
        layout.addLayout(details)
        self.count_label.setText("Open this tab to build the similarity graph")

    def mark_dirty(self):
        self._dirty = True

    def refresh(self, force=False):
        if not force and not self._dirty:
            return
        tracks = self.db.get_all_tracks()
        self.scene.clear()
        self._nodes.clear()
        self._labels.clear()
        self._edges.clear()
        self._selected_id = None
        self.count_label.setText(f"{len(tracks)} tracks - links show closest feature matches")
        if not tracks:
            self.details_label.setText("Scan music in the Library tab to build the similarity graph.")
            return
        displayed = tracks
        matrix = self._feature_matrix(displayed)
        nearest = self._nearest_candidates(displayed, matrix)
        edge_scores = {
            tuple(sorted((track_id, neighbor_id))): score
            for track_id, candidates in nearest.items()
            for score, neighbor_id in candidates
            if score >= 42
        }
        cluster_by_id = self._clusters(displayed, matrix)
        degree = {track.id: 0 for track in displayed}
        for first_id, second_id in edge_scores:
            degree[first_id] += 1
            degree[second_id] += 1
        radii = {track.id: 5 + min(7, degree[track.id] * 1.2) for track in displayed}
        positions = self._feature_positions(displayed, radii)
        self._draw_axes(displayed)
        for track in displayed:
            node_radius = radii[track.id]
            node = _TrackNode(track, self._select_track, self._highlight,
                              self.on_queue_track,
                              -node_radius, -node_radius, node_radius * 2, node_radius * 2)
            node.setPos(*positions[track.id])
            node.cluster = cluster_by_id[track.id]
            node.base_color = self._cluster_color(node.cluster)
            node.setBrush(QBrush(node.base_color))
            node.setPen(QPen(self._bg_color, 0))
            node.setZValue(1)
            self.scene.addItem(node)
            self._nodes[track.id] = node
            label = QGraphicsSimpleTextItem(self._short_title(track.title))
            label.setFont(QFont("Segoe UI", 7))
            label.setBrush(QBrush(QColor("#38a169") if track.id in self._played_ids else self._label_color))
            label.setAcceptedMouseButtons(Qt.NoButton)
            label.setZValue(2)
            self.scene.addItem(label)
            self._labels[track.id] = label
        for (first_id, second_id), score in edge_scores.items():
            first, second = self._nodes[first_id], self._nodes[second_id]
            line = QGraphicsLineItem()
            color = QColor(self._edge_color)
            color.setAlpha(int(30 + score * 1.6))
            line.setPen(QPen(color, 0.4 + score / 150))
            line.base_alpha = color.alpha()
            line.base_width = 0.4 + score / 150
            line.setZValue(-1)
            self.scene.addItem(line)
            self._edges.append((first_id, second_id, score, line))
        self._update_edges()
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-100, -100, 100, 100))
        self._dirty = False
        self.reset_view()
        self._selected_id = self._playing_id if self._playing_id in self._nodes else None
        self._highlight(self._selected_id)
        if self._selected_id is not None:
            self._center_node(self._nodes[self._selected_id])
            self._pulse_timer.start()
        else:
            self._pulse_timer.stop()

    @staticmethod
    def _feature_matrix(tracks):
        descriptor_keys = (
            "spectral_centroid", "spectral_rolloff", "spectral_flatness", "bass_ratio",
            "low_mid_ratio", "mid_ratio", "high_ratio", "brightness", "harmonicity",
            "onset_density", "onset_variability", "rhythmic_regularity",
        )
        scales = np.array([5000, 8000, 1, 1, 1, 1, 1, 1, 1, 1, 3, 1], dtype=np.float32)
        vectors = []
        for track in tracks:
            features = {**track.spectral_features, **track.rhythmic_features}
            descriptors = [float(features.get(key, 0.0)) for key in descriptor_keys]
            vectors.append([
                float(track.tempo) / 200, float(track.energy) / 10,
                (float(track.loudness) + 60) / 60,
                min(float(track.duration) / 600, 2),
                math.log1p(max(0, track.filesize)) / 20,
                *(np.asarray(descriptors, dtype=np.float32) / scales),
            ])
        return np.asarray(vectors, dtype=np.float32).reshape(len(tracks), -1)

    @staticmethod
    def _nearest_candidates(tracks, matrix):
        """Vectorize broad matching, then exact-score only a small candidate set."""
        if len(tracks) < 2:
            return {track.id: [] for track in tracks}
        nearest = {}
        candidate_count = min(16, len(tracks) - 1)
        for index, track in enumerate(tracks):
            distances = np.sum((matrix - matrix[index]) ** 2, axis=1)
            distances[index] = np.inf
            indices = np.argpartition(distances, candidate_count - 1)[:candidate_count]
            scored = sorted(
                ((track_similarity(track, tracks[other_index]), tracks[other_index].id)
                 for other_index in indices),
                reverse=True,
            )
            nearest[track.id] = scored[:3]
        return nearest

    @staticmethod
    def _clusters(tracks, matrix):
        """K-means on standardized features; cluster ids ordered by mean tempo."""
        n = len(tracks)
        k = max(1, min(8, int(round(math.sqrt(n / 4)))))
        if n <= k:
            return {track.id: index for index, track in enumerate(tracks)}
        data = (matrix - matrix.mean(axis=0)) / (matrix.std(axis=0) + 1e-6)
        rng = np.random.default_rng(7)
        centers = [data[rng.integers(n)]]
        for _ in range(1, k):
            dist = np.min([np.sum((data - c) ** 2, axis=1) for c in centers], axis=0)
            total = dist.sum()
            centers.append(data[rng.choice(n, p=dist / total)] if total > 0 else data[rng.integers(n)])
        centers = np.asarray(centers)
        labels = np.zeros(n, dtype=int)
        for _ in range(25):
            labels = np.argmin(((data[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2), axis=1)
            new_centers = np.asarray([
                data[labels == c].mean(axis=0) if np.any(labels == c) else centers[c] for c in range(k)
            ])
            if np.allclose(new_centers, centers):
                break
            centers = new_centers
        tempos = np.asarray([float(t.tempo) for t in tracks])
        order = sorted(range(k), key=lambda c: tempos[labels == c].mean() if np.any(labels == c) else 0)
        rank = {c: index for index, c in enumerate(order)}
        return {track.id: rank[int(labels[i])] for i, track in enumerate(tracks)}

    def _cluster_color(self, cluster: int) -> QColor:
        return palette_shade(self._node_color, cluster)

    def _feature_positions(self, tracks, radii):
        """Place each node on the free lattice slot closest to its (BPM, energy) point.

        Slots are tighter than a full node diameter and nodes are nudged toward their exact
        point, so large nodes may overlap slightly in exchange for more accurate placement.
        """
        cell = 2 * max(radii.values()) * NODE_SPACING
        nudge = NODE_NUDGE
        occupied: set[tuple[int, int]] = set()
        positions = {}
        # Hubs first so the best-connected songs sit exactly on their coordinates.
        for track in sorted(tracks, key=lambda t: (-radii[t.id], t.id)):
            ax, ay = float(track.tempo) * BPM_SCALE / cell, -float(track.energy) * ENERGY_SCALE / cell
            cx, cy = round(ax), round(ay)
            ring = 0
            best = None
            while best is None or ring <= best[0] + 1:
                for dx in range(-ring, ring + 1):
                    for dy in (-ring, ring) if abs(dx) != ring else range(-ring, ring + 1):
                        slot = (cx + dx, cy + dy)
                        if slot in occupied:
                            continue
                        dist = (slot[0] - ax) ** 2 + (slot[1] - ay) ** 2
                        if best is None or dist < best[1]:
                            best = (ring, dist, slot)
                ring += 1
            sx, sy = best[2]
            occupied.add(best[2])
            px = sx + max(-nudge, min(nudge, ax - sx))
            py = sy + max(-nudge, min(nudge, ay - sy))
            positions[track.id] = (px * cell, py * cell)
        return positions

    def _draw_axes(self, tracks):
        tempos = [float(t.tempo) for t in tracks]
        bpm_lo = int(math.floor(min(tempos) / 10) * 10)
        bpm_hi = int(math.ceil(max(tempos) / 10) * 10)
        left = bpm_lo * BPM_SCALE - 60
        right = bpm_hi * BPM_SCALE + 60
        top = -10.5 * ENERGY_SCALE
        bottom = 0.5 * ENERGY_SCALE
        axis_color = QColor(self._label_color)
        axis_color.setAlpha(150)
        grid_color = QColor(self._label_color)
        grid_color.setAlpha(22)
        font = QFont("Segoe UI", 8)
        axis_pen = QPen(axis_color, 1.2)
        axis_pen.setCosmetic(True)
        grid_pen = QPen(grid_color, 1)
        grid_pen.setCosmetic(True)

        def add_text(text, x, y, align_right=False, align_center=False):
            item = self.scene.addSimpleText(text, font)
            item.setBrush(QBrush(axis_color))
            rect = item.boundingRect()
            dx = -rect.width() if align_right else (-rect.width() / 2 if align_center else 0)
            item.setPos(x + dx, y - rect.height() / 2)
            item.setZValue(-3)
            return item

        self.scene.addLine(left, bottom, right, bottom, axis_pen).setZValue(-3)
        self.scene.addLine(left, top, left, bottom, axis_pen).setZValue(-3)
        step = 10 if bpm_hi - bpm_lo <= 120 else 20
        for bpm in range(bpm_lo, bpm_hi + 1, step):
            x = bpm * BPM_SCALE
            self.scene.addLine(x, top, x, bottom, grid_pen).setZValue(-4)
            self.scene.addLine(x, bottom, x, bottom + 6, axis_pen).setZValue(-3)
            add_text(str(bpm), x, bottom + 16, align_center=True)
        for energy in range(0, 11, 2):
            y = -energy * ENERGY_SCALE
            self.scene.addLine(left, y, right, y, grid_pen).setZValue(-4)
            self.scene.addLine(left - 6, y, left, y, axis_pen).setZValue(-3)
            add_text(str(energy), left - 10, y, align_right=True)
        add_text("BPM", right, bottom + 16, align_right=True)
        add_text("Energy", left - 10, top - 14, align_right=True)

    def _update_edges(self):
        for first_id, second_id, _score, line in self._edges:
            first, second = self._nodes[first_id].pos(), self._nodes[second_id].pos()
            line.setLine(first.x(), first.y(), second.x(), second.y())
        for track_id, label in self._labels.items():
            node = self._nodes[track_id]
            label.setPos(node.x() + node.rect().width() / 2 + 3, node.y() - 4)

    def _highlight(self, hovered_id):
        active_id = hovered_id if hovered_id is not None else self._selected_id
        linked_ids = {active_id} if active_id is not None else set()
        for first_id, second_id, _score, _line in self._edges:
            if active_id in (first_id, second_id):
                linked_ids.update((first_id, second_id))
        for track_id, node in self._nodes.items():
            is_linked = track_id in linked_ids
            node.setOpacity(1.0 if active_id is None or is_linked else 0.18)
            node.setPen(QPen(self._label_color if track_id == active_id else self._bg_color,
                             1.8 if track_id == active_id else 0))
            self._labels[track_id].setOpacity(1.0 if active_id is None or is_linked else 0.12)
        for first_id, second_id, _score, line in self._edges:
            is_linked = active_id is not None and active_id in (first_id, second_id)
            color = QColor(self._edge_color)
            color.setAlpha(255 if is_linked else (line.base_alpha if active_id is None else 12))
            line.setPen(QPen(color, line.base_width * (2.4 if is_linked else 1.0)))
            line.setZValue(-1)
        self._pulse_playing()

    def _clear_highlight(self):
        self._selected_id = None
        self._highlight(None)

    def _pulse_playing(self):
        node = self._nodes.get(self._playing_id)
        if node is None:
            return
        self._pulse_phase += 0.18
        pulse = (math.sin(self._pulse_phase) + 1) / 2
        color = QColor("#38a169")
        color.setAlpha(int(120 + 135 * pulse))
        node.setPen(QPen(color, 2 + 3 * pulse))

    @staticmethod
    def _short_title(title: str) -> str:
        title = title or "Untitled"
        return title if len(title) <= 12 else f"{title[:11]}..."

    def reset_view(self):
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-100, -100, 100, 100))
        self.view.resetTransform()
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
        self._update_label_visibility()

    def _update_label_visibility(self):
        # Titles only once zoomed in enough to read them without piling up.
        visible = len(self._labels) <= 40 or self.view.transform().m11() >= 1.1
        for label in self._labels.values():
            label.setVisible(visible)

    def _center_node(self, node):
        scale = self.view.transform().m11()
        if scale < 1.0:
            self.view.scale(1.0 / scale, 1.0 / scale)
        scale = self.view.transform().m11()
        margin_x = self.view.viewport().width() / (2 * scale) + 30
        margin_y = self.view.viewport().height() / (2 * scale) + 30
        self.scene.setSceneRect(
            self.scene.itemsBoundingRect().adjusted(-margin_x, -margin_y, margin_x, margin_y)
        )
        self.view.centerOn(node)
        self._update_label_visibility()

    def most_similar_neighbor(self, track_id: int):
        candidates = [
            (score, second_id if first_id == track_id else first_id)
            for first_id, second_id, score, _line in self._edges
            if track_id in (first_id, second_id)
        ]
        if not candidates:
            return None
        _score, neighbor_id = max(candidates)
        return self.db.get_track(neighbor_id)

    def set_playing_id(self, track_id: int | None):
        self._playing_id = track_id
        self._selected_id = track_id if track_id in self._nodes else None
        self._highlight(self._selected_id)
        self._pulse_timer.start() if self._selected_id is not None else self._pulse_timer.stop()
        if self._selected_id is not None:
            node = self._nodes[self._selected_id]
            self._show_track_details(node.track)
            self._center_node(node)

    def set_played_ids(self, track_ids):
        self._played_ids = set(track_ids)
        for track_id, label in self._labels.items():
            label.setBrush(QBrush(QColor("#38a169") if track_id in self._played_ids else self._label_color))

    def _select_track(self, track: Track):
        self._selected_id = track.id
        self._highlight(track.id)
        self._show_track_details(track)
        self._center_node(self._nodes[track.id])
        if self.on_play_track:
            self.on_play_track(track)

    def _show_track_details(self, track: Track):
        self.cover_label.setPixmap(cover_pixmap(track.cover_path, 72))
        self.details_label.setText(
            f"<b>{track.display_name}</b><br>"
            f"{track.album or 'Unknown album'} | {track.genre or 'Unknown genre'}<br>"
            f"{track.tempo:.0f} BPM | {track.key_name or 'Unknown key'} | "
            f"Energy {track.energy:.1f} | {track.duration_str}"
        )

    def apply_theme(self, accent: str, edge: str, label: str, bg: str | None = None):
        self._node_color = QColor(accent)
        self._edge_color = QColor(edge)
        self._label_color = QColor(label)
        if bg:
            self._bg_color = QColor(bg)
        for node in self._nodes.values():
            node.base_color = self._cluster_color(node.cluster)
            node.setBrush(QBrush(node.base_color))
        self._dirty = self._dirty or bool(self._nodes)  # axes/edges pick up the new colors on rebuild
        self.set_played_ids(self._played_ids)
        self._highlight(self._selected_id)
        self.reset_btn.setIcon(icon("reset"))