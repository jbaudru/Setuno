"""Shared playlist export dialog and import guidance."""
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..core.exporter import _safe_name, export_m3u, export_rekordbox_xml
from ..core.models import Track

XML_FILTER = "Rekordbox XML (*.xml)"
M3U8_FILTER = "M3U8 Playlist (*.m3u8)"
M3U_FILTER = "M3U Playlist (*.m3u)"


def export_playlist(parent, tracks: list[Track], name: str = "Setuno playlist"):
    if not tracks:
        QMessageBox.warning(parent, "Cannot export playlist", "This playlist has no songs to export.")
        return
    path, selected_filter = QFileDialog.getSaveFileName(
        parent, "Export playlist", f"{_safe_name(name) or 'playlist'}.xml",
        ";;".join((XML_FILTER, M3U8_FILTER, M3U_FILTER)),
    )
    if not path:
        return
    suffix = {XML_FILTER: ".xml", M3U8_FILTER: ".m3u8", M3U_FILTER: ".m3u"}[selected_filter]
    output = Path(path).with_suffix(suffix)
    if str(output) != path and output.exists():
        answer = QMessageBox.question(
            parent, "Replace file?", f"'{output.name}' already exists. Replace it?",
        )
        if answer != QMessageBox.Yes:
            return
    try:
        if suffix == ".xml":
            export_rekordbox_xml(tracks, str(output), output.stem)
        else:
            export_m3u(tracks, str(output))
    except (OSError, ValueError) as exc:
        QMessageBox.warning(parent, "Could not export playlist", str(exc))
        return
    if suffix == ".xml":
        instructions = (
            "In Rekordbox:\n"
            "1. In Preferences, find 'rekordbox xml' / 'Imported Library' "
            "(under Advanced > Database, or Bridge in older versions) and select this XML file.\n"
            "2. Show 'rekordbox xml' in the browser sidebar "
            "(enable it in Preferences > View if hidden), then refresh that library.\n"
            "3. Expand Playlists, right-click the exported playlist and choose Import Playlist.\n\n"
            "Song order, metadata, BPM, key and A-H hot cues are included. Audio files are referenced, "
            "not copied: keep them at their current locations. "
            "Rekordbox may still analyze tracks; beat grids are not exported."
        )
    else:
        instructions = (
            "In Rekordbox, choose File > Import > Import Playlist and select this file.\n\n"
            "Audio files are referenced, not copied: keep them at their current locations. "
            "Use Rekordbox XML export to include Setuno's BPM, key and hot cues."
        )
    QMessageBox.information(parent, "Playlist exported", f"Saved to:\n{output}\n\n{instructions}")
