"""Format sniffing, decode verification and atomic placement of downloaded media.

Formats are identified from the bytes, never from a URL suffix or a Content-Type: Fandom, for example, serves WEBP
through ``.png`` URLs. Raster files are opened with Pillow restricted to the sniffed format, verified, then reopened
and fully decoded (every frame of an animation). PDFs get a signature and ``%%EOF`` sanity check; their width and
height are ``None`` as the contract requires. Stored bytes are never modified.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from PIL import Image, ImageFile

from . import manifest, paths, schema

UNKNOWN = "unknown"
HEAD_BYTES = 64
PDF_TAIL_BYTES = 1024
_READ_CHUNK = 1024 * 1024


class MediaError(ValueError):
    """Undecodable, unsupported or mismatched content (Failure ``error_class`` ``decode_error``)."""

    error_class = "decode_error"
    retriable = False
    manual = False
    http_status = None

    def failure(self, *, source_id: str, candidate_id: str, url: str, phase: str = "decode",
                attempted_at: str | None = None, attempts: int = 1) -> dict:
        return schema.failure_record(source_id=source_id, candidate_id=candidate_id, phase=phase, url=url,
                                     attempted_at=attempted_at or manifest.now_utc(), attempts=attempts,
                                     error_class=self.error_class, http_status=None, retriable=False, manual=False,
                                     message=str(self))


def sniff(head: bytes) -> tuple[str, str | None, str | None]:
    """``(format, mime, ext)`` from the first bytes of a file; ``("unknown", None, None)`` when unrecognized."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        fmt = "PNG"
    elif head.startswith(b"\xff\xd8\xff"):
        fmt = "JPEG"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        fmt = "WEBP"
    elif head[:6] in (b"GIF87a", b"GIF89a"):
        fmt = "GIF"
    elif head.startswith(b"%PDF-"):
        fmt = "PDF"
    else:
        return UNKNOWN, None, None
    return fmt, schema.FORMAT_MIME[fmt], schema.FORMAT_EXT[fmt]


def _digest(path: Path) -> tuple[int, str, str, bytes]:
    sha256, sha1, size, head = hashlib.sha256(), hashlib.sha1(), 0, b""
    with open(path, "rb") as stream:
        while chunk := stream.read(_READ_CHUNK):
            if len(head) < HEAD_BYTES:
                head += chunk[: HEAD_BYTES - len(head)]
            sha256.update(chunk)
            sha1.update(chunk)
            size += len(chunk)
    return size, sha256.hexdigest(), sha1.hexdigest(), head


def _check_pdf(path: Path, size: int) -> None:
    with open(path, "rb") as stream:
        stream.seek(max(0, size - PDF_TAIL_BYTES))
        tail = stream.read()
    if b"%%EOF" not in tail:
        raise MediaError(f"PDF without %%EOF trailer near the end: {path.name}")


# Pillow formats accepted for a signature. Pillow's JPEG factory returns an ``MpoImageFile`` (format ``MPO``) for any
# JPEG carrying an MPF APP2 segment (many camera/phone files); it is still a JPEG file, stored and reported as JPEG.
_PILLOW_FORMATS = {"JPEG": ("JPEG", "MPO")}


def _decode_raster(path: Path, fmt: str) -> dict[str, Any]:
    if ImageFile.LOAD_TRUNCATED_IMAGES:
        raise MediaError("refusing to verify images while PIL.ImageFile.LOAD_TRUNCATED_IMAGES is enabled")
    try:
        with Image.open(path, formats=[fmt]) as image:
            if image.format not in _PILLOW_FORMATS.get(fmt, (fmt,)):
                raise MediaError(f"Pillow reads {image.format}, signature says {fmt}: {path.name}")
            width, height = image.size  # first frame; for MPO the primary picture
            mode = image.mode
            frames = int(getattr(image, "n_frames", 1))
            # A multi-picture JPEG holds separate pictures (e.g. a preview), not an animation.
            animated = image.format != "MPO" and bool(getattr(image, "is_animated", False))
            image.verify()
        with Image.open(path, formats=[fmt]) as image:
            for frame in range(frames):
                image.seek(frame)
                image.load()
    except MediaError:
        raise
    except (OSError, SyntaxError, ValueError, EOFError, Image.DecompressionBombError) as error:
        raise MediaError(f"cannot decode {fmt} {path.name}: {type(error).__name__}: {error}") from None
    if width < 1 or height < 1:
        raise MediaError(f"image has no pixels: {path.name}")
    return {"width": width, "height": height, "mode": mode, "frames": frames, "animated": animated}


def inspect(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Measure a file: ``format, mime, ext, width, height, mode, frames, animated, bytes, sha256, sha1``.

    Raises :class:`MediaError` for an unknown signature, an undecodable/truncated raster, a Pillow/signature
    disagreement or a PDF without trailer. PDFs report ``width``/``height``/``mode``/``frames`` as ``None``. A
    multi-picture JPEG (Pillow ``MPO``) is a JPEG: ``format`` ``JPEG``, the primary picture's size, ``frames`` the
    number of pictures and ``animated`` false. ``animated`` is true only for animated GIF/PNG/WEBP.
    """
    file = Path(path)
    size, sha256, sha1, head = _digest(file)
    if size == 0:
        raise MediaError(f"empty file: {file.name}")
    fmt, mime, ext = sniff(head)
    if fmt == UNKNOWN:
        raise MediaError(f"unrecognized file signature {head[:8]!r}: {file.name}")
    if fmt == "PDF":
        _check_pdf(file, size)
        details: dict[str, Any] = {"width": None, "height": None, "mode": None, "frames": None, "animated": False}
    else:
        details = _decode_raster(file, fmt)
    return {"format": fmt, "mime": mime, "ext": ext, **details, "bytes": size, "sha256": sha256, "sha1": sha1}


def finalize_download(result: Any, root: str | os.PathLike[str],
                      rel_path: str | Callable[[Mapping[str, Any]], str]) -> dict[str, Any]:
    """Inspect a :class:`lib.net.DownloadResult` temp file and atomically ``os.replace`` it to ``rel_path``.

    ``rel_path`` may be a callable receiving the inspected dict (so the name can use the sniffed extension and
    SHA-256, e.g. ``lambda info: paths.asset_relpath(family, slug, name, info["sha256"], info["ext"])``). The path
    must stay inside ``root`` under ``assets/`` without symlinks and end in the sniffed extension. An existing file
    with identical bytes is kept (idempotent resume); different bytes are never overwritten. On any failure the temp
    file is deleted. Returns the inspected dict plus ``path``.
    """
    temp = Path(result.temp_path)
    try:
        info = inspect(temp)
        if (info["sha256"], info["bytes"]) != (result.sha256, result.bytes):
            raise MediaError(f"temporary file changed after download: {temp.name}")
        rel = rel_path(info) if callable(rel_path) else rel_path
        if PurePosixPath(rel).suffix != f".{info['ext']}":
            raise MediaError(f"{rel!r} does not end in the sniffed extension .{info['ext']}")
        destination = paths.safe_path(root, rel, under="assets")
        destination.parent.mkdir(parents=True, exist_ok=True)
        paths.safe_path(root, rel, under="assets")  # re-check: no symlink may have appeared in the new parents
        if destination.exists():
            existing = _digest(destination)
            if existing[1] != info["sha256"]:
                raise MediaError(f"refusing to overwrite {rel} with different bytes")
            temp.unlink()
        else:
            os.chmod(temp, 0o644)
            os.replace(temp, destination)
        return {**info, "path": rel}
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def verify_asset_file(root: str | os.PathLike[str], asset: Mapping[str, Any]) -> dict[str, Any]:
    """Offline ``--verify`` of one Asset record: the file at ``path`` exists inside ``root`` and its measured bytes,
    hashes, sniffed format/MIME and decoded dimensions equal the record. Returns the inspected dict."""
    family = schema.source_family(asset["source_id"])
    try:
        file = paths.safe_path(root, asset["path"], under=f"{paths.ASSET_ROOT}/{family}")
    except ValueError as error:
        raise MediaError(f"{asset['id']}: unsafe path: {error}") from None
    if not file.is_file():
        raise MediaError(f"{asset['id']}: missing file {asset['path']}")
    info = inspect(file)
    for field in ("bytes", "sha256", "sha1", "format", "mime", "width", "height"):
        if info[field] != asset[field]:
            raise MediaError(f"{asset['id']}: {field} is {info[field]!r} on disk but {asset[field]!r} in the record")
    if PurePosixPath(asset["path"]).suffix != f".{info['ext']}":
        raise MediaError(f"{asset['id']}: extension does not match sniffed format {info['format']}")
    return info
