"""Fixture PNG bytes that do not depend on which zlib the interpreter loads.

Pillow's PNG encoder compresses through whatever ``libz.so.1`` the process has loaded. Stock zlib and zlib-ng (the
system zlib of some distributions and of Homebrew's Python) write different, equally valid streams for the same
pixels, so a fixture PNG encoded on one machine does not regenerate byte for byte on another. The fixture generators
therefore keep the committed bytes of a PNG whose decoded image (mode, size and RGBA pixels) equals the image they
drew, and encode only a drawing that changed. A committed PNG still has to decode to exactly the drawn image, so the
generator checks keep their meaning: a changed drawing, or a committed file edited by hand, still shows up as a
difference.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image


def pixel_key(image: Image.Image) -> str:
    """SHA-256 over the mode, size and RGBA pixels: equal exactly when two images decode to the same picture."""
    digest = hashlib.sha256(f"{image.mode} {image.size[0]}x{image.size[1]}\n".encode("ascii"))
    digest.update(image.convert("RGBA").tobytes())
    return digest.hexdigest()


class PinnedPNG:
    """The committed PNGs under ``root``, indexed by decoded image on first use.

    The index is read before a generator writes anything (both generators encode every file in memory first), so
    regenerating in place still finds the previous bytes."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._known: dict[str, bytes] | None = None

    def _index(self) -> dict[str, bytes]:
        if self._known is None:
            known: dict[str, bytes] = {}
            for path in sorted(self.root.rglob("*.png")):
                data = path.read_bytes()
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
                    key = pixel_key(image)
                if known.setdefault(key, data) != data:
                    raise ValueError(f"committed PNGs decode to the same image with different bytes: {path}")
            self._known = known
        return self._known

    def encode(self, image: Image.Image, **options: object) -> bytes:
        """The committed bytes of this exact image if there are any, else a fresh Pillow encoding with ``options``."""
        known = self._index().get(pixel_key(image))
        if known is not None:
            return known
        buffer = io.BytesIO()
        image.save(buffer, "PNG", **options)
        return buffer.getvalue()
