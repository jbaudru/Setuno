"""Generate assets/icon.ico (Setuno crossed-arrows logo) for the Windows executable. Run once: python scripts/generate_icon.py"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"

BG = (51, 51, 77, 255)         # #33334d
RING = (80, 80, 129, 255)      # #505081
ARROW_A = (134, 134, 172, 255)  # #8686AC
ARROW_B = (216, 216, 236, 255)  # light accent for contrast


def _arrow(d: ImageDraw.ImageDraw, p1, p2, color, width, head_len):
    d.line([p1, p2], fill=color, width=width)
    angle = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    spread = math.radians(28)
    for sign in (-1, 1):
        a = angle + math.pi - sign * spread
        hx = p2[0] + head_len * math.cos(a)
        hy = p2[1] + head_len * math.sin(a)
        d.line([p2, (hx, hy)], fill=color, width=width)


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = size / 2
    r = size / 2 - size * 0.03

    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BG)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=RING, width=max(1, size // 32))

    margin = size * 0.28
    width = max(2, size // 16)
    head = size * 0.11
    # two crossing arrows: the "shuffle" symbol
    _arrow(d, (margin, margin), (size - margin, size - margin), ARROW_A, width, head)
    _arrow(d, (margin, size - margin), (size - margin, margin), ARROW_B, width, head)
    return img


sizes = [16, 24, 32, 48, 64, 128, 256]
images = [draw_icon(s) for s in sizes]
OUT.parent.mkdir(parents=True, exist_ok=True)
images[-1].save(OUT, format="ICO", sizes=[(s, s) for s in sizes])
print(f"Wrote {OUT}")

