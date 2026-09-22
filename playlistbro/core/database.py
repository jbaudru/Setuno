"""Local JSON persistence layer for tracks and playlists (no external DB)."""
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .models import Track


def default_data_dir() -> Path:
    """Folder next to the app (or project root when run from source) holding data/*.json."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent.parent
    data_dir = base / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


class Database:
    """Thread-safe JSON-file-backed store. Data lives in <app>/data/library.json and playlists.json."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or default_data_dir()
        self.tracks_path = self.data_dir / "library.json"
        self.playlists_path = self.data_dir / "playlists.json"
        self._lock = threading.RLock()
        self._tracks: dict[int, dict] = {}
        self._playlists: dict[int, dict] = {}
        self._next_track_id = 1
        self._next_playlist_id = 1
        self._load()

    # ---------------- persistence ----------------
    def _load(self):
        with self._lock:
            if self.tracks_path.exists():
                try:
                    raw = json.loads(self.tracks_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    raw = []
                self._tracks = {r["id"]: r for r in raw}
                if self._tracks:
                    self._next_track_id = max(self._tracks) + 1
            if self.playlists_path.exists():
                try:
                    raw = json.loads(self.playlists_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    raw = []
                self._playlists = {r["id"]: r for r in raw}
                if self._playlists:
                    self._next_playlist_id = max(self._playlists) + 1
            # Self-heal: older/interrupted scans could leave energy stuck at its
            # placeholder 0.0 even though energy_raw varies. Cheap to recompute on load.
            if self._tracks:
                self.normalize_energy()

    def _save_tracks(self):
        tmp = self.tracks_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(list(self._tracks.values()), separators=(",", ":")),
            encoding="utf-8",
        )
        tmp.replace(self.tracks_path)

    def _save_playlists(self):
        tmp = self.playlists_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(self._playlists.values()), indent=2), encoding="utf-8")
        tmp.replace(self.playlists_path)

    def close(self):
        pass

    # ---------------- tracks ----------------
    def upsert_track(self, t: Track) -> int:
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            existing = next((r for r in self._tracks.values() if r["filepath"] == t.filepath), None)
            if existing:
                track_id = existing["id"]
                added_at = existing.get("added_at", now)
            else:
                track_id = self._next_track_id
                self._next_track_id += 1
                added_at = now
            # Re-analysis (e.g. a rescan) must not clobber a user's manual BPM/key/metadata correction.
            tempo, key_name, camelot = t.tempo, t.key_name, t.camelot
            tempo_manual = bool(existing.get("tempo_manual")) if existing else False
            key_manual = bool(existing.get("key_manual")) if existing else False
            metadata_manual = bool(existing.get("metadata_manual")) if existing else False
            favorite = bool(existing.get("favorite")) if existing else False
            if tempo_manual:
                tempo = existing["tempo"]
            if key_manual:
                key_name, camelot = existing["key_name"], existing["camelot"]
            title, artist, album, genre = t.title, t.artist, t.album, t.genre
            if metadata_manual:
                title, artist, album, genre = (
                    existing["title"], existing["artist"], existing["album"], existing["genre"],
                )
            record = {
                "id": track_id, "filepath": t.filepath, "title": title, "artist": artist,
                "album": album, "genre": genre, "duration": t.duration, "tempo": tempo,
                "key_name": key_name, "camelot": camelot, "energy_raw": t.energy_raw,
                "energy": t.energy, "loudness": t.loudness, "filesize": t.filesize, "mtime": t.mtime, "added_at": added_at,
                "tempo_manual": tempo_manual, "key_manual": key_manual, "metadata_manual": metadata_manual,
                "favorite": favorite,
                "cover_path": t.cover_path, "waveform_low": t.waveform_low, "waveform_high": t.waveform_high,
                "waveform_peaks": t.waveform_peaks,
                "spectral_features": t.spectral_features, "rhythmic_features": t.rhythmic_features,
            }
            self._tracks[track_id] = record
            self._save_tracks()
            return track_id

    def set_favorite(self, track_id: int, favorite: bool):
        with self._lock:
            record = self._tracks.get(track_id)
            if not record:
                return
            record["favorite"] = bool(favorite)
            self._save_tracks()

    def set_manual_tempo(self, track_id: int, bpm: float):
        with self._lock:
            record = self._tracks.get(track_id)
            if not record:
                return
            record["tempo"] = round(float(bpm))
            record["tempo_manual"] = True
            self._save_tracks()

    def set_manual_key(self, track_id: int, key_name: str, camelot: str):
        with self._lock:
            record = self._tracks.get(track_id)
            if not record:
                return
            record["key_name"] = key_name
            record["camelot"] = camelot
            record["key_manual"] = True
            self._save_tracks()

    def update_metadata(self, track_id: int, title: str, artist: str, album: str, genre: str):
        """Manual metadata edit; marked so future rescans don't overwrite it with file tags."""
        with self._lock:
            record = self._tracks.get(track_id)
            if not record:
                return
            record["title"] = title
            record["artist"] = artist
            record["album"] = album
            record["genre"] = genre
            record["metadata_manual"] = True
            self._save_tracks()

    def update_cover(self, track_id: int, cover_path: str):
        """Update the cached cover thumbnail after a manual album-art edit."""
        with self._lock:
            record = self._tracks.get(track_id)
            if not record:
                return
            record["cover_path"] = cover_path
            self._save_tracks()

    def get_all_tracks(self) -> List[Track]:
        with self._lock:
            tracks = [self._record_to_track(r) for r in self._tracks.values()]
        return sorted(tracks, key=lambda t: (t.artist.lower(), t.title.lower()))

    def get_track(self, track_id: int) -> Optional[Track]:
        with self._lock:
            record = self._tracks.get(track_id)
            return self._record_to_track(record) if record else None

    def get_existing_filepaths(self) -> dict:
        with self._lock:
            return {r["filepath"]: r["mtime"] for r in self._tracks.values()}

    def remove_missing(self, existing_paths: set):
        with self._lock:
            to_remove = [tid for tid, r in self._tracks.items() if r["filepath"] not in existing_paths]
            if to_remove:
                for tid in to_remove:
                    del self._tracks[tid]
                self._save_tracks()

    def normalize_energy(self):
        with self._lock:
            rows = list(self._tracks.values())
            if not rows:
                return
            values = [r["energy_raw"] for r in rows]
            lo, hi = min(values), max(values)
            spread = (hi - lo) or 1.0
            for r in rows:
                r["energy"] = round(1 + 9 * ((r["energy_raw"] - lo) / spread), 2)
            self._save_tracks()

    def delete_track(self, track_id: int):
        self.delete_tracks([track_id])

    def delete_tracks(self, track_ids) -> int:
        """Delete multiple tracks with one persistence write."""
        with self._lock:
            ids = set(track_ids)
            removed = 0
            for track_id in ids:
                if track_id not in self._tracks:
                    continue
                del self._tracks[track_id]
                removed += 1
            if removed:
                self._save_tracks()
            return removed

    def update_filepath(self, track_id: int, new_filepath: str):
        """Point an existing track at a new location on disk (e.g. after a relocate)."""
        with self._lock:
            record = self._tracks.get(track_id)
            if not record:
                return
            record["filepath"] = new_filepath
            try:
                record["mtime"] = os.stat(new_filepath).st_mtime
            except OSError:
                pass
            self._save_tracks()

    def _record_to_track(self, r: dict) -> Track:
        return Track(
            id=r["id"], filepath=r["filepath"], title=r.get("title") or "",
            artist=r.get("artist") or "", album=r.get("album") or "", genre=r.get("genre") or "",
            duration=r.get("duration") or 0.0, tempo=r.get("tempo") or 0.0,
            key_name=r.get("key_name") or "", camelot=r.get("camelot") or "",
            energy_raw=r.get("energy_raw") or 0.0, energy=r.get("energy") or 0.0,
            loudness=r.get("loudness") or 0.0,
            filesize=r.get("filesize") or 0, mtime=r.get("mtime") or 0.0,
            added_at=r.get("added_at") or "",
            tempo_manual=bool(r.get("tempo_manual")), key_manual=bool(r.get("key_manual")),
            favorite=bool(r.get("favorite")),
            cover_path=r.get("cover_path") or "",
            waveform_low=r.get("waveform_low") or [], waveform_high=r.get("waveform_high") or [],
            waveform_peaks=r.get("waveform_peaks") or [],
            spectral_features=r.get("spectral_features") or {},
            rhythmic_features=r.get("rhythmic_features") or {},
        )

    # ---------------- playlists ----------------
    def save_playlist(self, name: str, mode: str, params: dict, track_ids: List[int]) -> int:
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            playlist_id = self._next_playlist_id
            self._next_playlist_id += 1
            self._playlists[playlist_id] = {
                "id": playlist_id, "name": name, "created_at": now, "mode": mode,
                "params": params, "track_ids": list(track_ids),
            }
            self._save_playlists()
            return playlist_id

    def list_playlists(self) -> list:
        with self._lock:
            rows = list(self._playlists.values())
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)

    def get_playlist_tracks(self, playlist_id: int) -> List[Track]:
        with self._lock:
            record = self._playlists.get(playlist_id)
            ids = list(record["track_ids"]) if record else []
        tracks = []
        for tid in ids:
            t = self.get_track(tid)
            if t:
                tracks.append(t)
        return tracks

    def delete_playlist(self, playlist_id: int):
        with self._lock:
            if playlist_id in self._playlists:
                del self._playlists[playlist_id]
                self._save_playlists()
