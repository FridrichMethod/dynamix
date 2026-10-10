"""The raster site icons are project-made: plain geometry drawn here, the same shapes as assets/site/icons/favicon.svg.

`python -I tests/test_layout_icons.py --write` regenerates the PNG/ICO files; the tests check that the committed files
decode to what this geometry draws (pixel comparison, so a different zlib build cannot fail it) and that the SVG
mark carries the same shapes. Nothing here is copied from another project's icons.
"""

import re
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "assets/site/icons"
BACKGROUND, LANE, NOTE = "#20242c", "#5fd4ff", "#ffffff"
# The mark on a 64-unit grid: a rounded square, three lanes (left, right, bottom) and a diamond note.
CORNER = 14
LANES = [(12, 12, 6, 28), (46, 12, 6, 28), (22, 46, 20, 6)]
DIAMOND = [(32, 19), (39, 26), (32, 33), (25, 26)]
# Published raster sizes: name -> edge in pixels.
PNGS = {
    "favicon-16x16.png": 16, "favicon-32x32.png": 32, "apple-touch-icon.png": 180,
    "android-chrome-192x192.png": 192, "android-chrome-512x512.png": 512, "mstile-150x150.png": 150,
}
ICO_SIZES = [16, 32, 48]
SUPERSAMPLE = 8
TOLERANCE = 3


def draw_mark(size):
    """The mark at `size` px, drawn large and reduced with Lanczos for smooth edges (RGBA, transparent corners)."""
    big = size * SUPERSAMPLE
    scale = big / 64
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=round(CORNER * scale), fill=BACKGROUND)
    for x, y, width, height in LANES:
        draw.rounded_rectangle((round(x * scale), round(y * scale), round((x + width) * scale) - 1, round((y + height) * scale) - 1),
                               radius=round(2 * scale), fill=LANE)
    draw.polygon([(round(x * scale), round(y * scale)) for x, y in DIAMOND], fill=NOTE)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def write_icons():
    ICONS.mkdir(parents=True, exist_ok=True)
    for name, size in PNGS.items():
        draw_mark(size).save(ICONS / name, optimize=True)
    # Each ICO frame is drawn at its own size (append_images) rather than scaled down from the largest one.
    largest = draw_mark(max(ICO_SIZES))
    largest.save(ICONS / "favicon.ico", sizes=[(size, size) for size in ICO_SIZES],
                 append_images=[draw_mark(size) for size in ICO_SIZES if size != max(ICO_SIZES)])


def max_difference(first, second):
    difference = ImageChops.difference(first.convert("RGBA"), second.convert("RGBA"))
    return max(high for _, high in difference.getextrema())


class IconTests(unittest.TestCase):
    def test_png_icons_match_the_geometry(self):
        for name, size in PNGS.items():
            with self.subTest(icon=name):
                with Image.open(ICONS / name) as image:
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.size, (size, size))
                    self.assertLessEqual(max_difference(image, draw_mark(size)), TOLERANCE)

    def test_ico_holds_each_size(self):
        with Image.open(ICONS / "favicon.ico") as icon:
            self.assertEqual(icon.format, "ICO")
            self.assertEqual(sorted(icon.info["sizes"]), [(size, size) for size in ICO_SIZES])
            for size in ICO_SIZES:
                icon.size = (size, size)
                frame = icon.copy()
                self.assertLessEqual(max_difference(frame, draw_mark(size)), TOLERANCE, size)

    def test_svg_mark_has_the_same_shapes(self):
        svg = (ICONS / "favicon.svg").read_text(encoding="utf-8")
        self.assertIn(f'rx="{CORNER}" fill="{BACKGROUND}"', svg)
        rects = [tuple(int(value) for value in match) for match in re.findall(r'<rect x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)"', svg)]
        self.assertEqual(rects, LANES)
        self.assertIn("M32 19 39 26 32 33 25 26Z", svg)

    def test_svg_icons_are_plain_local_geometry(self):
        """No embedded raster, script, external reference or editor metadata in the project-made SVGs."""
        for path in sorted(ICONS.glob("*.svg")):
            with self.subTest(icon=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertTrue(text.startswith('<svg xmlns="http://www.w3.org/2000/svg"'))
                for forbidden in ("<image", "<script", "href=", "data:", "<metadata", "sodipodi", "inkscape", "Capa_1", "<foreignObject", "url("):
                    self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    if "--write" in sys.argv:
        write_icons()
        print(f"Wrote {len(PNGS)} PNG icons and favicon.ico to {ICONS.relative_to(ROOT)}")
    else:
        unittest.main()
