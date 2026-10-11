"""Fixture PNG bytes that do not depend on which zlib the interpreter loads.

Pillow's PNG encoder compresses through whatever ``libz.so.1`` the process has loaded. Stock zlib and zlib-ng (the
system zlib of some distributions and of Homebrew's Python) write different, equally valid streams for the same
pixels, so a fixture PNG encoded on one machine does not regenerate byte for byte on another. The fixture generators
therefore keep the committed bytes of a PNG whose decoded image (mode, size, raw pixel data and RGBA rendering)
equals the image they drew, and encode only a drawing that changed, so a changed drawing still shows up as a
difference. A committed file edited by hand shows up through its hash-based file name and the recorded hashes, not
through this comparison; regenerating keeps such an edit when it leaves the pixels unchanged.

The committed tree is therefore an input: changing only the encode options or metadata (compression level,
``optimize``, ``pnginfo``, an ICC profile) of a picture that is already committed has no effect. Delete the committed
PNG first to re-encode it. The first encoding of a new or changed picture still depends on the local zlib.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image


def pixel_key(image: Image.Image) -> str:
    """SHA-256 over the mode, size, raw pixel data and RGBA rendering. Equal keys mean the same mode and size, the same
    stored values (also above 8 bits per channel, which the RGBA rendering alone would clip) and, for palette images,
    the same colours for the indices used."""
    digest = hashlib.sha256(f"{image.mode} {image.size[0]}x{image.size[1]}\n".encode("ascii"))
    raw, rgba = image.tobytes(), image.convert("RGBA").tobytes()
    digest.update(len(raw).to_bytes(8, "big") + raw)
    digest.update(rgba)
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
            known: dict[str, tuple[bytes, Path]] = {}
            for path in sorted(self.root.rglob("*.png")):
                data = path.read_bytes()
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
                    key = pixel_key(image)
                first = known.setdefault(key, (data, path))
                if first[0] != data:
                    raise ValueError("committed PNGs decode to the same image with different bytes: "
                                     f"{first[1]} and {path}")
            self._known = {key: data for key, (data, _) in known.items()}
        return self._known

    def encode(self, image: Image.Image, **options: object) -> bytes:
        """The committed bytes of this exact image if there are any, else a fresh Pillow encoding with ``options``."""
        known = self._index().get(pixel_key(image))
        if known is not None:
            return known
        buffer = io.BytesIO()
        image.save(buffer, "PNG", **options)
        return buffer.getvalue()
