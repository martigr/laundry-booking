"""Draw the home screen icons (laundry/static/icons/*.png) from the logo in templates/_logo.html.

The app has no build step, so the PNGs are committed; run this only when the logo or the accent
colour changes. Needs Pillow, which is not in requirements-dev.txt (scripts/screenshots.py uses it too):

    pip install pillow
    python scripts/icons.py

- icon-192.png, icon-512.png: rounded square for browsers that show the icon as is (manifest "any")
- icon-maskable-512.png: full-bleed, logo inside the inner 60 %, Android crops it to its own shape
- apple-touch-icon.png: 180 px, full-bleed and opaque, iOS rounds the corners itself
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'laundry' / 'static' / 'icons'

ACCENT = (0x1D, 0x5F, 0xD1, 255)  # --accent in style.css
WHITE = (255, 255, 255, 255)
SCALE = 4  # draw larger, then downscale for smooth edges


def draw_icon(size, logo_share, rounded):
    """Accent square with the white washing machine of _logo.html (viewBox 24) in its centre."""
    big = size * SCALE
    img = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 0.22, fill=ACCENT)
    else:
        d.rectangle((0, 0, big, big), fill=ACCENT)

    unit = big * logo_share / 24  # one unit of the 24×24 logo
    off = (big - 24 * unit) / 2

    def p(x, y):
        return off + x * unit, off + y * unit

    stroke = round(1.8 * unit)
    h = stroke / 2  # SVG strokes are centred on the path, Pillow draws them inside the box
    x0, y0 = p(4, 2.5)
    x1, y1 = p(20, 21.5)
    d.rounded_rectangle((x0 - h, y0 - h, x1 + h, y1 + h), radius=2.5 * unit + h, outline=WHITE, width=stroke)
    cx, cy = p(12, 13.5)
    r = 4.5 * unit + h
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=WHITE, width=stroke)
    for bx0, bx1 in [(7.5, 8.5), (11, 12)]:  # the two buttons: short lines with round caps
        (ax, ay), (bx, _) = p(bx0, 6), p(bx1, 6)
        d.rounded_rectangle((ax - h, ay - h, bx + h, ay + h), radius=h, fill=WHITE)
    return img.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    icons = {
        'icon-192.png': draw_icon(192, 0.62, rounded=True),
        'icon-512.png': draw_icon(512, 0.62, rounded=True),
        'icon-maskable-512.png': draw_icon(512, 0.56, rounded=False),
        'apple-touch-icon.png': draw_icon(180, 0.6, rounded=False).convert('RGB'),
    }
    for name, img in icons.items():
        img.save(OUT / name, optimize=True)
        print(f'{name}: {img.size[0]}×{img.size[1]}, {(OUT / name).stat().st_size} bytes')


if __name__ == '__main__':
    main()
