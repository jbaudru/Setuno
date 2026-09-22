"""Interactive song-similarity graph for discovering tracks in the library."""
import math

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
    QGraphicsSimpleTextItem, QMenu, QPushButton, QVBoxLayout, QWidget,
)

from ..core.database import Database
from ..core.models import Track
from ..core.playlist_engine import track_similarity
from .icon_loader import cover_pixmap, icon


class _GraphView(QGraphicsView):
    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)


class _TrackNode(QGraphicsEllipseItem):
    def __init__(self, track: Track, callback, moved_callback, highlight_callback, queue_callback, *args):
        super().__init__(*args)
        self.track = track
        self.callback = callback
        self.moved_callback = moved_callback
        self.highlight_callback = highlight_callback
        self.queue_callback = queue_callback
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsEllipseItem.ItemIsMovable)
        self.setFlag(QGraphicsEllipseItem.ItemSendsGeometryChanges)
        self.setToolTip(track.display_name)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.callback(self.track)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.moved_callback()

    def hoverEnterEvent(self, event):
        self.highlight_callback(self.track.id)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.highlight_callback(None)
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsEllipseItem.ItemPositionHasChanged:
            self.moved_callback()
        return super().itemChange(change, value)

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
        self._cluster_colors = ["#e06c48", "#48b5a8", "#d3a541", "#d86890", "#69a6e8", "#9aac55"]
        self._nodes = {}
        self._labels = {}
        self._edges = []
        self._selected_id = None
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
        nearest = self._nearest_candidates(displayed)
        edge_scores = {
            tuple(sorted((track_id, neighbor_id))): score
            for track_id, candidates in nearest.items()
            for score, neighbor_id in candidates
            if score >= 42
        }
        cluster_by_id = self._clusters(displayed, edge_scores)
        positions = self._cluster_positions(displayed, cluster_by_id)
        degree = {track.id: 0 for track in displayed}
        for first_id, second_id in edge_scores:
            degree[first_id] += 1
            degree[second_id] += 1
        for track in displayed:
            node_radius = 6 + min(12, degree[track.id] * 1.5)
            node = _TrackNode(track, self._select_track, self._update_edges, self._highlight,
                              self.on_queue_track,
                              -node_radius, -node_radius, node_radius * 2, node_radius * 2)
            node.setPos(*positions[track.id])
            node.base_color = QColor(self._cluster_colors[cluster_by_id[track.id] % len(self._cluster_colors)])
            node.setBrush(QBrush(node.base_color))
            node.setPen(QPen(Qt.white, 0))
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

    @staticmethod
    def _nearest_candidates(tracks):
        """Vectorize broad matching, then exact-score only a small candidate set."""
        if len(tracks) < 2:
            return {track.id: [] for track in tracks}
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
        matrix = np.asarray(vectors, dtype=np.float32)
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

    def _clusters(self, tracks, edge_scores):
        parent = {track.id: track.id for track in tracks}
        def find(track_id):
            while parent[track_id] != track_id:
                parent[track_id] = parent[parent[track_id]]
                track_id = parent[track_id]
            return track_id
        for (first_id, second_id), score in edge_scores.items():
            if score >= 58:
                parent[find(first_id)] = find(second_id)
        groups = {find(track.id) for track in tracks}
        group_index = {group: index for index, group in enumerate(sorted(groups))}
        return {track.id: group_index[find(track.id)] for track in tracks}

    def _cluster_positions(self, tracks, cluster_by_id):
        groups = {}
        for track in tracks:
            groups.setdefault(cluster_by_id[track.id], []).append(track)
        ordered_groups = sorted(groups.values(), key=lambda group: (-len(group), group[0].id))
        positions = {}
        for group_index, group in enumerate(ordered_groups):
            angle = group_index * 2.399963229728653
            distance = 85 + 95 * math.sqrt(group_index)
            center_x = math.cos(angle) * distance
            center_y = math.sin(angle) * distance
            for index, track in enumerate(sorted(group, key=lambda item: item.display_name.casefold())):
                member_angle = index * 2.399963229728653
                radius = 18 + 16 * math.sqrt(index)
                positions[track.id] = (
                    center_x + math.cos(member_angle) * radius,
                    center_y + math.sin(member_angle) * radius,
                )
        return positions

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
            node.setPen(QPen(Qt.white, 1.8 if track_id == active_id else 0))
            self._labels[track_id].setOpacity(1.0 if active_id is None or is_linked else 0.12)
        for first_id, second_id, _score, line in self._edges:
            is_linked = active_id is not None and active_id in (first_id, second_id)
            color = QColor(self._edge_color)
            color.setAlpha(255 if is_linked else (line.base_alpha if active_id is None else 12))
            line.setPen(QPen(color, line.base_width * (2.4 if is_linked else 1.0)))
            line.setZValue(-1)

    @staticmethod
    def _short_title(title: str) -> str:
        title = title or "Untitled"
        return title if len(title) <= 12 else f"{title[:11]}..."

    def reset_view(self):
        self.view.resetTransform()
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

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
        self._selected_id = track_id if track_id in self._nodes else None
        self._highlight(self._selected_id)
        if self._selected_id is not None:
            node = self._nodes[self._selected_id]
            self._show_track_details(node.track)
            self.view.centerOn(node)

    def set_played_ids(self, track_ids):
        self._played_ids = set(track_ids)
        for track_id, label in self._labels.items():
            label.setBrush(QBrush(QColor("#38a169") if track_id in self._played_ids else self._label_color))

    def _select_track(self, track: Track):
        self._selected_id = track.id
        self._highlight(track.id)
        self._show_track_details(track)
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

    def apply_theme(self, accent: str, edge: str, label: str):
        self._node_color = QColor(accent)
        self._edge_color = QColor(edge)
        self._label_color = QColor(label)
        self.set_played_ids(self._played_ids)
        self.reset_btn.setIcon(icon("reset"))