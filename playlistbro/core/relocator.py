"""Recover tracks whose files were moved/renamed by searching a user-chosen folder."""
import os
from pathlib import Path
from typing import Dict, List

from .database import Database
from .models import Track


def find_missing(tracks: List[Track]) -> List[Track]:
    """Return the subset of `tracks` whose file no longer exists on disk."""
    return [t for t in tracks if not Path(t.filepath).exists()]


def relocate_tracks(tracks: List[Track], search_folder: str, db: Database) -> Dict[int, str]:
    """Search `search_folder` recursively for files matching the missing tracks' names.

    Matches on filename only (case-insensitive), since the file may have moved to a
    different folder. Updates `db` and the given `Track` objects in place.
    Returns {track_id: new_filepath} for tracks that were found.
    """
    wanted = {Path(t.filepath).name.lower(): t for t in tracks if not Path(t.filepath).exists()}
    if not wanted:
        return {}
    found: Dict[int, str] = {}
    for dirpath, _dirnames, filenames in os.walk(search_folder):
        for name in filenames:
            key = name.lower()
            track = wanted.get(key)
            if track and track.id not in found:
                new_path = str(Path(dirpath) / name)
                found[track.id] = new_path
                track.filepath = new_path
        if len(found) == len(wanted):
            break
    for track_id, new_path in found.items():
        db.update_filepath(track_id, new_path)
    return found
