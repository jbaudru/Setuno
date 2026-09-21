"""Export generated playlists to disk: M3U8 files or ordered copies in a target folder."""
import re
import shutil
from pathlib import Path
from typing import List

from .models import Track


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


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
        results.append(str(dest))
    return results
