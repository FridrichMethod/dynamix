#!/usr/bin/env python3
"""Build the derived WebP previews (assets/thumbs/, data/thumbs.json) without modifying any original file.

    python -I scripts/build_thumbnails.py [--check | --verify | --prune] [--rebuild] [--root PATH]

A preview is made for every archive-tier raster (Pages serves only its preview; the viewer links the original at
`archive_url`) and for every gallery image whose long edge exceeds 480 px; smaller site-tier images and PDFs get none.
Previews are WebP, long edge 480 (never enlarged), quality 75, method 6, upright (EXIF orientation applied), without
EXIF/XMP, colour-managed as in the reference (plus CMYK: an embedded CMYK profile is applied, untagged CMYK gets the
naive device conversion browsers use), first frame of an animation, named `assets/thumbs/<sha256[:16]>.webp` after the
original's SHA-256 and recorded in data/thumbs.json with the encoder and library versions. With the pinned
Pillow/libwebp a rebuild is byte-identical. Originals whose colours a preview cannot keep (another mode, or a profile
that does not match the image) are refused; a run lists every such original and writes nothing.

Safety: a name shared by two originals fails; an existing file that data/thumbs.json does not record for that
original is never overwritten unless its bytes are already identical; --prune deletes only previews data/thumbs.json
lists for an original that no longer needs one, and only when the file still has the recorded SHA-256. Files under
assets/thumbs/ that the manifest does not list are reported and never deleted. Originals are opened read-only.

--check   write nothing; fail unless data/thumbs.json and assets/thumbs/ are exactly what a build would produce
--verify  like --check, and also re-encode every preview and require identical bytes (same Pillow/libwebp)
--prune   build, then delete listed previews whose original left (see above)

Adapted from FridrichMethod/deemo scripts/build_thumbnails.py (Apache-2.0, commit
b14bf0d5f5a05e5382c319f3fd5019a4936820b8); modified for the Dynamix archive in 2026: schema-v1 catalog, archive-tier
previews, collision and ownership checks, manifest-limited pruning.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:  # `python -I` does not put the script's directory on sys.path
    sys.path.insert(0, str(SCRIPTS))

import PIL  # noqa: E402
from PIL import Image, ImageCms, ImageMath, ImageOps, features  # noqa: E402

import build_catalog  # noqa: E402
from lib import manifest, paths, schema  # noqa: E402

REPO_ROOT = SCRIPTS.parent
THUMB_DIR = schema.THUMB_DIR
MANIFEST = build_catalog.THUMBS_MANIFEST
LONG_EDGE = build_catalog.PREVIEW_LONG_EDGE
QUALITY = 75
METHOD = 6  # libwebp's slowest, smallest setting; deterministic like the others
NAME_DIGITS = 16
VERSION_KEYS = ("pillow", "libwebp")
# Modes whose plain conversion to 8-bit RGB(A) keeps their meaning. 16-bit greyscale is scaled down first and CMYK is
# converted as browsers show it (see colour_managed); anything else (32-bit integer or float, YCbCr, Lab, ...) is
# refused rather than previewed with wrong colours.
EIGHT_BIT_MODES = {"1", "L", "LA", "P", "PA", "RGB", "RGBA"}
GREY_MODES = {"1", "L", "LA"}
SIXTEEN_BIT_GREY = {"I;16", "I;16L", "I;16B", "I;16N"}
CMYK = "CMYK"
ALPHA_MODES = {"RGBA", "LA", "PA", "RGBa", "La"}
PREVIEW_NAME = re.compile(r"^[0-9a-f]{16}\.webp$")
DESCRIPTION = ("Derived WebP previews for the archive grid and for archive-tier originals that Pages does not serve; "
               "they are not archive files. Originals are unchanged and listed with their hashes in data/catalog.json.")


class ThumbnailError(ValueError):
    """A preview cannot be produced, written or pruned safely."""


def settings() -> dict:
    return {"format": "WEBP", "long_edge": LONG_EDGE, "quality": QUALITY, "method": METHOD,
            "pillow": PIL.__version__, "libwebp": features.version("webp")}


def stable(encoder: Mapping[str, Any]) -> dict:
    """Encoder settings without library versions."""
    return {key: value for key, value in encoder.items() if key not in VERSION_KEYS}


def thumb_size(width: int, height: int) -> tuple[int, int]:
    """Preview size: long edge scaled down to LONG_EDGE, never enlarged."""
    long_edge = max(width, height)
    if long_edge <= LONG_EDGE:
        return width, height
    return max(1, round(width * LONG_EDGE / long_edge)), max(1, round(height * LONG_EDGE / long_edge))


def thumb_path(sha256: str) -> str:
    return schema.thumb_relpath(sha256)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def originals(root: Path, catalog: Mapping[str, Any] | None = None) -> dict[str, dict]:
    """Originals that need a preview, keyed by SHA-256 (the catalog already merges byte-identical files); fails when
    two of them would share a preview name."""
    if catalog is None:
        catalog = build_catalog.combine(root)
    wanted = {}
    for entry in catalog["assets"]:
        if build_catalog.needs_preview(entry):
            wanted[entry["sha256"]] = {key: entry[key] for key in ("path", "format", "width", "height")}
    names: dict[str, str] = {}
    for sha256, original in sorted(wanted.items()):
        other = names.setdefault(thumb_path(sha256), sha256)
        if other != sha256:
            raise ThumbnailError(f"Thumbnail name collision: {original['path']} and {wanted[other]['path']} both map "
                                 f"to {thumb_path(sha256)}")
    return dict(sorted(wanted.items()))


def eight_bit(image: Image.Image, path: str) -> Image.Image:
    """The image in a mode that converts to RGB(A) exactly, or CMYK (converted by colour_managed). 16-bit greyscale
    is scaled to 8 bits as browsers show it (a plain conversion would clip it to near white); its tRNS grey value
    becomes an alpha channel, transparent exactly where a pixel has that value."""
    if image.mode in SIXTEEN_BIT_GREY:
        wide = image.convert("I")
        grey = wide.point(lambda value: value / 257 + 0.5).convert("L")
        if "transparency" not in image.info:
            return grey
        key = image.info["transparency"]
        if not isinstance(key, int) or isinstance(key, bool):
            raise ThumbnailError(f"Cannot preview {path}: unsupported transparency {key!r} on a {image.mode} image")
        alpha = ImageMath.lambda_eval(lambda args: args["notequal"](args["a"], key) * 255, a=wide).convert("L")
        grey.putalpha(alpha)  # L -> LA
        return grey
    if image.mode not in EIGHT_BIT_MODES and image.mode != CMYK:
        raise ThumbnailError(f"Cannot preview {path}: unsupported image mode {image.mode}")
    return image


def colour_managed(image: Image.Image, path: str) -> tuple[Image.Image, bytes | None]:
    """The image and the ICC profile to embed in its preview. An RGB profile on an RGB/palette image is kept, so the
    preview's colours match the original's. A greyscale profile on a greyscale image and a CMYK profile on a CMYK
    image are applied instead (LittleCMS, default intent, as for greyscale), giving untagged sRGB pixels. Untagged
    CMYK gets Pillow's device conversion, the naive formula browsers also use for untagged CMYK JPEGs (approximate
    colours, like the original in a browser). Any other profile is refused: an RGB preview cannot carry it."""
    icc = image.info.get("icc_profile")
    if not icc:
        return (image.convert("RGB") if image.mode == CMYK else image), None
    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    except (OSError, ImageCms.PyCMSError) as error:
        raise ThumbnailError(f"Cannot preview {path}: unreadable ICC profile ({error})") from error
    space = profile.profile.xcolor_space.strip()  # type: ignore[attr-defined]  # Pillow's CmsProfile
    if space == "RGB" and image.mode not in GREY_MODES and image.mode != CMYK:
        return image, icc
    if space == "CMYK" and image.mode == CMYK:
        try:
            # A fresh sRGB profile on every call, so worker threads never share a LittleCMS profile handle.
            return ImageCms.profileToProfile(image, profile, ImageCms.createProfile("sRGB"), outputMode="RGB"), None
        except ImageCms.PyCMSError as error:
            raise ThumbnailError(f"Cannot preview {path}: its CMYK ICC profile cannot be applied ({error})") \
                from error
    if space != "GRAY" or image.mode not in GREY_MODES:
        raise ThumbnailError(f"Cannot preview {path}: {space} ICC profile on a {image.mode} image")
    alpha = image.mode == "LA" or "transparency" in image.info
    grey = image.convert("LA" if alpha else "L")
    try:
        # A fresh sRGB profile on every call, so worker threads never share a LittleCMS profile handle.
        rgb = ImageCms.profileToProfile(grey.convert("L"), profile, ImageCms.createProfile("sRGB"), outputMode="RGB")
    except ImageCms.PyCMSError as error:
        raise ThumbnailError(f"Cannot preview {path}: its greyscale ICC profile cannot be applied ({error})") \
            from error
    if alpha:
        rgb.putalpha(grey.getchannel("A"))
    return rgb, None


def render(root: Path, original: Mapping[str, Any]) -> tuple[bytes, tuple[int, int]]:
    """One original as a WebP preview (bytes, size). The original is opened read-only with the decoder of its
    recorded format; an animation contributes its first frame; no EXIF or XMP is written."""
    try:
        source = paths.safe_path(root, original["path"], under=paths.ASSET_ROOT)
    except ValueError as error:
        raise ThumbnailError(f"Cannot preview {original['path']}: {error}") from None
    formats = [original["format"]] if original.get("format") else None
    with Image.open(source, formats=formats) as image:
        image.load()  # the first frame of an animated GIF/PNG/WEBP
        # Browsers show originals upright, so the preview applies the EXIF orientation too.
        ImageOps.exif_transpose(image, in_place=True)
        managed, icc_profile = colour_managed(eight_bit(image, original["path"]), original["path"])
        alpha = managed.mode in ALPHA_MODES or "transparency" in managed.info
        target = "RGBA" if alpha else "RGB"
        converted = managed if managed.mode == target else managed.convert(target)
        size = thumb_size(*converted.size)
        # Pillow resamples RGBA premultiplied, so fully transparent pixels do not darken cut-out edges.
        preview = converted if size == converted.size else converted.resize(size, Image.Resampling.LANCZOS,
                                                                             reducing_gap=3.0)
    buffer = io.BytesIO()
    preview.save(buffer, "WEBP", quality=QUALITY, method=METHOD, icc_profile=icc_profile)
    return buffer.getvalue(), preview.size


def read_manifest(root: Path) -> dict | None:
    """data/thumbs.json validated by the catalog builder (None when absent)."""
    return build_catalog.load_thumbs(root)


def preview_file(root: Path, rel: str) -> Path:
    """Absolute path of a preview: ``assets/thumbs/<16 hex>.webp`` without symlinked components."""
    if not rel.startswith(THUMB_DIR + "/") or not PREVIEW_NAME.fullmatch(rel[len(THUMB_DIR) + 1:]):
        raise ThumbnailError(f"Not a preview path: {rel!r}")
    try:
        return paths.safe_path(root, rel, under=THUMB_DIR)
    except ValueError as error:
        raise ThumbnailError(f"Unsafe preview path {rel!r}: {error}") from None


def write_atomic(root: Path, rel: str, data: bytes) -> None:
    target = preview_file(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    preview_file(root, rel)  # re-check: no symlink may have appeared in the new parents
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.chmod(name, 0o644)
        os.replace(name, target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def stored_files(root: Path) -> list[str]:
    """Every file below assets/thumbs/ (dot-files included), sorted; a symlink there is an error."""
    directory = Path(root) / THUMB_DIR
    if directory.is_symlink():
        raise ThumbnailError(f"{THUMB_DIR} is a symlink")
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ThumbnailError(f"symlink below {THUMB_DIR}: {path.relative_to(root).as_posix()}")
        if path.is_file():
            found.append(path.relative_to(root).as_posix())
    return found


def manifest_content(entries: Mapping[str, Mapping[str, Any]]) -> dict:
    return {"schema_version": schema.SCHEMA_VERSION, "description": DESCRIPTION, "encoder": settings(),
            "thumbnails": dict(sorted(entries.items()))}


def workers_for(workers: int | None) -> int:
    # Each worker holds a decoded original (up to about 50 MB), so the pool stays small.
    return workers or min(8, os.cpu_count() or 1)


def _reusable(root: Path, sha256: str, entry: Mapping[str, Any] | None) -> bool:
    if entry is None:
        return False
    file = preview_file(root, entry["path"])
    return file.is_file() and file.stat().st_size == entry["bytes"] and digest(file.read_bytes()) == entry["sha256"]


def _store(root: Path, rel: str, data: bytes, owned: bool) -> None:
    """Write a rendered preview unless an identical file is already there. A different existing file is replaced
    only when data/thumbs.json records it as this original's preview (a damaged or outdated copy)."""
    file = preview_file(root, rel)
    if file.is_file():
        if file.read_bytes() == data:
            return
        if not owned:
            raise ThumbnailError(f"Refusing to overwrite {rel}: data/thumbs.json does not record it as this "
                                 "original's preview (remove the file or restore data/thumbs.json)")
    elif file.exists():
        raise ThumbnailError(f"Refusing to overwrite {rel}: not a regular file")
    write_atomic(root, rel, data)


def render_all(root: Path, pending: list[Mapping[str, Any]], workers: int | None) -> list[tuple[bytes, tuple]]:
    """Render every original (in parallel). When some cannot be previewed, nothing is returned and one error lists
    every such original, so a single run shows all of them."""
    def attempt(original: Mapping[str, Any]) -> tuple[tuple[bytes, tuple] | None, str | None]:
        try:
            return render(root, original), None
        except (ValueError, OSError) as error:  # ThumbnailError, or a decoder error of a damaged original
            message = str(error)
            return None, message if original["path"] in message else f"Cannot preview {original['path']}: {message}"

    with ThreadPoolExecutor(workers_for(workers)) as pool:
        results = list(pool.map(attempt, pending))
    refused = [message for _, message in results if message]
    if refused:
        shown = refused[:build_catalog.MAX_REPORTED]
        more = f"\n... and {len(refused) - len(shown)} more" if len(refused) > len(shown) else ""
        raise ThumbnailError(f"{len(refused)} original(s) cannot be previewed; nothing was written:\n"
                             + "\n".join(shown) + more)
    return [result for result, _ in results]


def build(root: Path, prune: bool = False, rebuild: bool = False, workers: int | None = None,
          catalog: Mapping[str, Any] | None = None) -> dict:
    """Write missing or stale previews and data/thumbs.json; reuse previews whose bytes match the manifest.

    Entries of originals that no longer need a preview stay listed (and their files stay) until ``prune``."""
    root = Path(root)
    current = settings()
    old = read_manifest(root)
    if old and old["encoder"] != current and not rebuild:
        raise ThumbnailError(f"{MANIFEST} was encoded with {old['encoder']}, but this run uses {current}. Install "
                             "requirements.txt, or pass --rebuild to re-encode every thumbnail.")
    listed = dict(old["thumbnails"]) if old else {}
    wanted = originals(root, catalog)
    for sha256 in listed.keys() - wanted.keys():
        for other in wanted:
            if thumb_path(other) == thumb_path(sha256):
                raise ThumbnailError(f"Thumbnail name collision: {wanted[other]['path']} and the listed preview of "
                                     f"{sha256} both map to {thumb_path(other)}")
    entries, pending = {}, []
    for sha256, original in wanted.items():
        if not rebuild and _reusable(root, sha256, listed.get(sha256)):
            entries[sha256] = listed[sha256]
        else:
            pending.append((sha256, original))
    rendered = render_all(root, [original for _, original in pending], workers)
    for (sha256, _), (data, (width, height)) in zip(pending, rendered, strict=True):
        _store(root, thumb_path(sha256), data, owned=sha256 in listed)
        entries[sha256] = {"path": thumb_path(sha256), "width": width, "height": height, "bytes": len(data),
                           "sha256": digest(data)}
    retired = {sha256: entry for sha256, entry in listed.items() if sha256 not in wanted}
    pruned = prune_retired(root, retired) if prune else []
    kept = {} if prune else retired
    content = manifest_content({**entries, **kept})
    target = paths.safe_path(root, MANIFEST)
    if not target.is_file() or target.read_bytes() != manifest.dumps(content).encode("utf-8"):
        manifest.write_json_atomic(target, content)
    referenced = {entry["path"] for entry in content["thumbnails"].values()}
    unlisted = [path for path in stored_files(root) if path not in referenced]
    return {"thumbnails": len(entries), "rendered": len(pending), "reused": len(entries) - len(pending),
            "bytes": sum(entry["bytes"] for entry in entries.values()),
            "retired": sorted(entry["path"] for entry in kept.values()), "pruned": pruned, "unlisted": unlisted}


def prune_retired(root: Path, retired: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Delete the listed previews of originals that no longer need one. Every file must still be the recorded
    derived file (same SHA-256); otherwise nothing is deleted and the run fails."""
    targets = []
    for sha256, entry in sorted(retired.items()):
        file = preview_file(root, entry["path"])
        if file.exists() and (not file.is_file() or digest(file.read_bytes()) != entry["sha256"]):
            raise ThumbnailError(f"Refusing to prune {entry['path']}: its bytes are not the preview recorded for "
                                 f"{sha256}")
        targets.append((entry["path"], file))
    removed = []
    for rel, file in targets:
        if file.is_file():
            file.unlink()
            removed.append(rel)
    return removed


def check_entry(root: Path, sha256: str, entry: Mapping[str, Any],
                original: Mapping[str, Any] | None) -> tuple[list[str], bytes | None]:
    """Problems with one recorded preview, and its bytes when they are intact."""
    path = entry["path"]
    file = preview_file(root, path)
    if not file.is_file():
        return [f"Missing thumbnail file: {path}: run scripts/build_thumbnails.py"], None
    data = file.read_bytes()
    if len(data) != entry["bytes"] or digest(data) != entry["sha256"]:
        return [f"Thumbnail size or SHA-256 differs from {MANIFEST}: {path}"], None
    try:
        with Image.open(io.BytesIO(data)) as image:
            format_name, size, info = image.format, image.size, dict(image.info)
            image.load()
    except Exception as error:  # noqa: BLE001 - any decoder failure is a broken preview
        return [f"Thumbnail does not decode: {path}: {error}"], None
    found = []
    if format_name != "WEBP":
        found.append(f"Thumbnail is {format_name}, not WEBP: {path}")
    if [*size] != [entry["width"], entry["height"]]:
        found.append(f"Dimension mismatch: {path} is {size[0]}x{size[1]}, {MANIFEST} says "
                     f"{entry['width']}x{entry['height']}")
    if original:
        # Either orientation: an EXIF-rotated original is previewed upright.
        expected = thumb_size(original["width"], original["height"])
        if sorted(size) != sorted(expected):
            found.append(f"Dimension mismatch: {path} is {size[0]}x{size[1]}, expected {expected[0]}x{expected[1]} "
                         f"for {original['path']}")
    if "exif" in info or "xmp" in info:
        found.append(f"Thumbnail carries EXIF or XMP metadata: {path}")
    return found, data


def problems(root: Path, reencode: bool = False, workers: int | None = None,
             catalog: Mapping[str, Any] | None = None) -> list[str]:
    """Everything that differs from what a build would produce; empty when the previews are current. With
    ``reencode`` every preview is also rendered again from its original and must match byte for byte."""
    root = Path(root)
    manifest_value = read_manifest(root)
    if manifest_value is None:
        return [f"{MANIFEST} is missing: run scripts/build_thumbnails.py"]
    current, recorded = settings(), manifest_value["encoder"]
    found = []
    if stable(recorded) != stable(current):
        found.append(f"Encoder settings changed: {MANIFEST} has {stable(recorded)}, the script uses "
                     f"{stable(current)}: run scripts/build_thumbnails.py --rebuild")
    if reencode and recorded != current:
        found.append(f"Byte-identical re-encoding needs Pillow {recorded.get('pillow')} with libwebp "
                     f"{recorded.get('libwebp')} (running Pillow {current['pillow']} with libwebp "
                     f"{current['libwebp']}); install requirements.txt")
        reencode = False
    wanted, thumbnails = originals(root, catalog), manifest_value["thumbnails"]
    found.extend(f"No thumbnail for {wanted[sha256]['path']}: run scripts/build_thumbnails.py"
                 for sha256 in sorted(wanted.keys() - thumbnails.keys()))
    found.extend(f"Thumbnail {thumbnails[sha256]['path']} belongs to no current original: run "
                 "scripts/build_thumbnails.py --prune" for sha256 in sorted(thumbnails.keys() - wanted.keys()))
    intact = []
    for sha256, entry in sorted(thumbnails.items()):
        entry_problems, data = check_entry(root, sha256, entry, wanted.get(sha256))
        found.extend(entry_problems)
        if data is not None and sha256 in wanted:
            intact.append((wanted[sha256], entry["path"], data))
    referenced = {entry["path"] for entry in thumbnails.values()}
    found.extend(f"Unlisted file in {THUMB_DIR}: {path}: not a preview recorded in {MANIFEST}; remove it"
                 for path in stored_files(root) if path not in referenced)
    if reencode:
        with ThreadPoolExecutor(workers_for(workers)) as pool:
            fresh = list(pool.map(lambda item: render(root, item[0])[0], intact))
        found.extend(f"Re-encoding {original['path']} does not reproduce {path}"
                     for (original, path, data), again in zip(intact, fresh, strict=True) if again != data)
    return found


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="write nothing; fail unless the previews are current")
    mode.add_argument("--verify", action="store_true", help="like --check, and re-encode every preview")
    mode.add_argument("--prune", action="store_true", help="build, then delete listed previews whose original left")
    parser.add_argument("--rebuild", action="store_true", help="re-encode every preview (after an encoder change)")
    parser.add_argument("--workers", type=int, help="images decoded in parallel (default: up to 8)")
    parser.add_argument("--root", type=Path, default=root or REPO_ROOT,
                        help="repository-shaped tree (default: %(default)s)")
    args = parser.parse_args(argv)
    if (args.check or args.verify) and args.rebuild:
        parser.error("--rebuild writes files; it cannot be combined with --check or --verify")
    try:
        if args.check or args.verify:
            found = problems(args.root, reencode=args.verify, workers=args.workers)
            if found:
                print("\n".join(found), file=sys.stderr)
                return 1
            thumbnails = (read_manifest(args.root) or {"thumbnails": {}})["thumbnails"]
            print(json.dumps({"thumbnails": len(thumbnails), "bytes": sum(e["bytes"] for e in thumbnails.values()),
                              "reencoded": args.verify}, indent=2))
            return 0
        summary = build(args.root, prune=args.prune, rebuild=args.rebuild, workers=args.workers)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
