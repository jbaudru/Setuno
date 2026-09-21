"""Flat SVG icon loader for the themable Qt UI (works from source and frozen exe)."""
import re
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

if getattr(sys, "frozen", False):
    _ICONS_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)) / "ui" / "icons"
else:
    _ICONS_DIR = Path(__file__).resolve().parent / "icons"

_FILL_RE = re.compile(r'fill="#[0-9a-fA-F]{3,6}"')

_current_color = "#ffffff"
_cache: dict[tuple, QIcon] = {}


def set_theme_color(color: str):
    """Set the fill color applied to all flat icons (called on theme switch)."""
    global _current_color
    _current_color = color
    _cache.clear()


def _recolored_svg_bytes(name: str, color: str) -> bytes:
    path = _ICONS_DIR / f"{name}.svg"
    svg = path.read_text(encoding="utf-8")
    svg = _FILL_RE.sub(f'fill="{color}"', svg)
    return svg.encode("utf-8")


def icon(name: str, color: str | None = None) -> QIcon:
    """Return the flat QIcon for `name` (without extension), recolored for the active theme."""
    fill = color or _current_color
    key = (name, fill)
    if key not in _cache:
        svg_bytes = _recolored_svg_bytes(name, fill)
        renderer = QSvgRenderer(QByteArray(svg_bytes))
        image = QImage(64, 64, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        _cache[key] = QIcon(QPixmap.fromImage(image))
    return _cache[key]


def app_icon() -> QIcon:
    # The app logo (crossed-arrows) keeps its own colors regardless of theme.
    path = _ICONS_DIR / "app_icon.svg"
    return QIcon(str(path))


_cover_placeholder_cache: dict[int, QPixmap] = {}


def _cover_placeholder(size: int) -> QPixmap:
    """A small vinyl-disk pixmap used when a track has no embedded album art."""
    if size not in _cover_placeholder_cache:
        renderer = QSvgRenderer(str(_ICONS_DIR / "album.svg"))
        image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        _cover_placeholder_cache[size] = QPixmap.fromImage(image)
    return _cover_placeholder_cache[size]


def cover_pixmap(cover_path: str, size: int = 32) -> QPixmap:
    """Load a track's cached album-art thumbnail, or a vinyl-disk placeholder if it has none."""
    if cover_path:
        pixmap = QPixmap(cover_path)
        if not pixmap.isNull():
            return pixmap.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    return _cover_placeholder(size)

