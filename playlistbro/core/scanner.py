"""Recursive folder scanning + incremental library analysis, parallelized with a thread pool."""
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from .analyzer import SUPPORTED_EXTENSIONS, analyze_file
from .database import Database
from .models import Track


def is_readable_file(filepath: str) -> bool:
    """Whether a stored track path is still a readable regular file."""
    try:
        path = Path(filepath)
        return path.is_file() and os.access(path, os.R_OK)
    except OSError:
        return False


def find_audio_files(root_folder: str):
    for dirpath, _dirnames, filenames in os.walk(root_folder):
        for name in filenames:
            if Path(name).suffix.lower() in SUPPORTED_EXTENSIONS:
                yield str(Path(dirpath) / name)


def is_within_folder(filepath: str, folder: str) -> bool:
    """True if `filepath` lives anywhere under `folder` (used to filter playlists by folder)."""
    try:
        Path(filepath).resolve().relative_to(Path(folder).resolve())
        return True
    except ValueError:
        return False


def scan_folder(
    root_folder: str,
    db: Database,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    track_cb: Optional[Callable[[Track], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    max_workers: Optional[int] = None,
    force: bool = False,
):
    """Scan `root_folder` recursively, analyzing new/changed files in parallel via a thread pool.

    progress_cb(done, total, current_filename) reports overall progress.
    track_cb(track) fires as soon as each track is analyzed, for live/dynamic UI updates.
    should_cancel() may return True to abort early.
    force=True re-analyzes every file, even ones already up to date in the library.
    """
    files = list(find_audio_files(root_folder))
    existing = db.get_existing_filepaths()
    total = len(files)
    seen = set(files)
    done = 0

    to_analyze = []
    for filepath in files:
        try:
            mtime = os.stat(filepath).st_mtime
        except OSError:
            continue
        if not force and filepath in existing and abs(existing[filepath] - mtime) < 1.0:
            done += 1
            if progress_cb:
                progress_cb(done, total, Path(filepath).name)
            continue
        to_analyze.append((filepath, filepath not in existing))

    workers = max_workers or min(8, (os.cpu_count() or 4))
    if to_analyze:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="analyze") as pool:
            futures = {
                pool.submit(analyze_file, filepath): (filepath, is_new)
                for filepath, is_new in to_analyze
            }
            for future in as_completed(futures):
                filepath, is_new = futures[future]
                if should_cancel and should_cancel():
                    for f in futures:
                        f.cancel()
                    break
                done += 1
                try:
                    data = future.result()
                    if is_new and (
                        not data.get("artist")
                        or not data.get("album")
                        or not data.get("genre")
                        or not data.get("cover_path")
                    ):
                        from .metadata_lookup import enrich_metadata
                        data = enrich_metadata(data)
                    track_id = db.upsert_track(Track(**data))
                    db.normalize_energy()
                    if track_cb:
                        updated = db.get_track(track_id)
                        if updated:
                            track_cb(updated)
                except Exception as exc:  # keep scanning even if one file fails
                    if progress_cb:
                        progress_cb(done, total, f"ERROR {Path(filepath).name}: {exc}")
                    continue
                if progress_cb:
                    progress_cb(done, total, Path(filepath).name)

    db.remove_missing(seen | (set(existing) - set(files)))

