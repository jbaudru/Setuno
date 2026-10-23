"""Export playlists as Rekordbox XML, M3U8, or ordered copies."""
import re
import shutil
from pathlib import Path
from typing import List
from xml.etree import ElementTree as ET

from .models import CUE_COLORS, CUE_LABELS, Track, validate_hot_cues


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def _write_tags(dest: Path, track: Track):
    """Best-effort sync of the copy's embedded tags with the track's current metadata."""
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(dest), easy=True)
        if audio is None:
            return
        if track.title:
            audio["title"] = track.title
        if track.artist:
            audio["artist"] = track.artist
        if track.album:
            audio["album"] = track.album
        if track.genre:
            audio["genre"] = track.genre
        audio.save()
    except Exception:
        pass


def export_m3u(tracks: List[Track], output_path: str, use_relative: bool = False):
    """Write an extended M3U playlist (.m3u or .m3u8) importable by Rekordbox and similar DJ software."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#EXTM3U"]
    for t in tracks:
        lines.append(f"#EXTINF:{int(t.duration)},{t.display_name}")
        path = Path(t.filepath)
        if use_relative:
            try:
                path = path.relative_to(output_path.parent)
            except ValueError:
                pass
        lines.append(str(path))
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return str(output_path)


# Backward-compatible alias
export_m3u8 = export_m3u


def export_rekordbox_xml(tracks: List[Track], output_path: str, playlist_name: str) -> str:
    """Write a Rekordbox XML library with one ordered playlist referencing local audio."""
    if not playlist_name.strip():
        raise ValueError("The playlist name cannot be empty.")
    paths = [Path(track.filepath).resolve() for track in tracks]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            f"{len(missing)} audio file(s) could not be found. "
            "Locate the missing files before exporting:\n" + "\n".join(missing[:5])
        )
    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    ET.SubElement(root, "PRODUCT", Name="Setuno", Version="1.0", Company="Setuno")
    collection = ET.SubElement(root, "COLLECTION")
    ids: dict[Path, str] = {}
    order = []
    for track, path in zip(tracks, paths):
        track_id = ids.get(path)
        if track_id is None:
            track_id = str(len(ids) + 1)
            ids[path] = track_id
            location = path.as_uri()
            if location.startswith("file:///"):
                location = "file://localhost/" + location[len("file:///"):]
            attributes = {
                "TrackID": track_id,
                "Name": track.title or path.stem,
                "Artist": track.artist,
                "Album": track.album,
                "Genre": track.genre,
                "Kind": f"{path.suffix[1:].upper()} File",
                "Size": str(path.stat().st_size),
                "TotalTime": str(int(track.duration)),
                "Location": location,
            }
            if track.tempo > 0:
                attributes["AverageBpm"] = f"{track.tempo:.2f}"
            if track.key_name:
                attributes["Tonality"] = track.key_name.replace(" minor", "m").replace(" major", "")
            element = ET.SubElement(collection, "TRACK", attributes)
            if track.tempo > 0 and track.beat_offset is not None:
                ET.SubElement(
                    element, "TEMPO", Inizio=f"{track.beat_offset:.6f}",
                    Bpm=f"{track.tempo:.2f}", Metro="4/4", Battito="1",
                )
            for cue in validate_hot_cues(track.hot_cues, track.duration):
                slot = cue["slot"]
                color = CUE_COLORS[slot].lstrip("#")
                red, green, blue = (str(int(color[i:i + 2], 16)) for i in (0, 2, 4))
                ET.SubElement(
                    element, "POSITION_MARK", Name=CUE_LABELS[slot], Type="0",
                    Start=f"{cue['time']:.3f}", Num=str(slot),
                    Red=red, Green=green, Blue=blue,
                )
        order.append(track_id)
    collection.set("Entries", str(len(ids)))
    playlists = ET.SubElement(root, "PLAYLISTS")
    folder = ET.SubElement(playlists, "NODE", Type="0", Name="ROOT", Count="1")
    playlist = ET.SubElement(
        folder, "NODE", Name=playlist_name.strip(), Type="1", KeyType="0", Entries=str(len(order)),
    )
    for track_id in order:
        ET.SubElement(playlist, "TRACK", Key=track_id)
    ET.indent(root)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        ET.ElementTree(root).write(temporary, encoding="UTF-8", xml_declaration=True)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return str(output)


def export_ordered_copies(tracks: List[Track], target_folder: str) -> List[str]:
    """Copy files into `target_folder` with a numeric prefix that preserves playlist order."""
    target = Path(target_folder)
    target.mkdir(parents=True, exist_ok=True)
    width = max(2, len(str(len(tracks))))
    results = []
    for i, t in enumerate(tracks, start=1):
        src = Path(t.filepath)
        if not src.exists():
            continue
        dest_name = f"{str(i).zfill(width)} - {_safe_name(t.display_name)}{src.suffix}"
        dest = target / dest_name
        shutil.copy2(src, dest)
        _write_tags(dest, t)
        results.append(str(dest))
    return results
