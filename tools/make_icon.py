"""Draws bootstrap/synergyscan.ico: the Sinergy Flow mark, yellow on charcoal.

The geometry is the same as the logo SVG in web/index.html (viewBox 72x48).
Run: python tools/make_icon.py
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

YELLOW, DARK, SS = "#FFCD00", "#231F20", 4   # SS: supersampling for smooth edges
SIZE = 256 * SS
SCALE = 2.9 * SS                              # logo units -> pixels
OX, OY = (SIZE - 72 * SCALE) / 2, (SIZE - 48 * SCALE) / 2


def px(x, y):
    return (OX + x * SCALE, OY + y * SCALE)


def mark_polys():
    """Polygons for one half of the mark (a U with a gap in its left stem)."""
    def rect(x0, y0, x1, y1):
        return [px(x0, y0), px(x1, y0), px(x1, y1), px(x0, y1)]
    polys = [rect(1, 2, 7, 7), rect(1, 13, 7, 14), rect(41, 2, 47, 14)]
    n = 48
    outer = [px(24 + 23 * math.cos(math.pi * i / n), 14 + 23 * math.sin(math.pi * i / n)) for i in range(n + 1)]
    inner = [px(24 + 17 * math.cos(math.pi * i / n), 14 + 17 * math.sin(math.pi * i / n)) for i in range(n + 1)]
    polys.append(outer + inner[::-1])
    return polys


img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=SIZE // 6, fill=DARK)
for poly in mark_polys():
    d.polygon(poly, fill=YELLOW)
    # second half is the first rotated 180 degrees about the logo's centre
    d.polygon([(2 * px(36, 24)[0] - x, 2 * px(36, 24)[1] - y) for x, y in poly], fill=YELLOW)

out = Path(__file__).resolve().parent.parent / "bootstrap" / "synergyscan.ico"
img.resize((256, 256), Image.LANCZOS).save(
    out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", out)
