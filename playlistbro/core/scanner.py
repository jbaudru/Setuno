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
    root_folder: str | list[str],
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
    folders = [root_folder] if isinstance(root_folder, str) else root_folder
    files = list(dict.fromkeys(
        filepath for folder in folders for filepath in find_audio_files(folder)
    ))
    existing = db.get_existing_filepaths()
    total = len(files)
    seen = set(files)
    done = 0
    analyzed = 0

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

    workers = max_workers or min(2, (os.cpu_count() or 2))
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
                    if (data.get("title") == Path(filepath).stem
                            or not data.get("artist") or not data.get("album")):
                        from .metadata_lookup import _parse_filename
                        guessed = _parse_filename(Path(filepath))
                        if data.get("title") == Path(filepath).stem and guessed["title"]:
                            data["title"] = guessed["title"]
                        for field in ("artist", "album"):
                            if not data.get(field) and guessed[field]:
                                data[field] = guessed[field]
                    if is_new and (
                        not data.get("artist")
                        or not data.get("album")
                        or not data.get("genre")
                        or not data.get("cover_path")
                    ):
                        from .analyzer import _genre_from_folder, _read_tags
                        from .metadata_lookup import enrich_metadata
                        genre_inferred = not (
                            _read_tags(filepath).get("genre") or _genre_from_folder(filepath)
                        )
                        genre_guess = data.get("genre", "") if genre_inferred else ""
                        if genre_inferred:
                            data["genre"] = ""
                        data = enrich_metadata(data)
                        data["genre"] = data.get("genre") or genre_guess
                    from .metadata_lookup import write_missing_file_metadata
                    if write_missing_file_metadata(filepath, data):
                        stat = os.stat(filepath)
                        data["mtime"] = stat.st_mtime
                        data["filesize"] = stat.st_size
                    track_id = db.upsert_track(Track(**data))
                    analyzed += 1
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

    if analyzed:
        db.normalize_energy()
    db.remove_missing(seen | (set(existing) - set(files)))

