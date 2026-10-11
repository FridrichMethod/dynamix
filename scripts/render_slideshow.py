"""Render the slideshow page (index.html) from templates/slideshow.html and a catalog.

Frozen interface (docs/implementation/09-frozen-contracts.md, "Slideshow rendering interface"):

    render_slideshow(root: Path, catalog: dict) -> str

reads `root/templates/slideshow.html`, replaces its single `@python-work-area` placeholder with one
`<div class="mySlides fade"><img ...></div>` per catalog asset whose `slideshow` is true, in catalog order, and
HTML-attribute-escapes every value. scripts/build_catalog.py imports it to write index.html; it must not duplicate
the slide markup. Nothing here writes files or changes image bytes.

Adapted from FridrichMethod/deemo scripts/build_catalog.py (render_slideshow, art_layout, slide_* helpers and attr();
Apache-2.0, commit b14bf0d5f5a05e5382c319f3fd5019a4936820b8); modified for the Dynamix archive, 2026. See NOTICE.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.sax.saxutils import escape

from PIL import Image

TEMPLATE = "templates/slideshow.html"
PLACEHOLDER = "@python-work-area"
# Kinds that are often cut-outs on a transparent canvas; their opaque box is measured so the stage can centre the art.
LAYOUT_KINDS = frozenset({"character"})
# An origin token is passed to the page only when it looks like one (quality.origin_status values are snake_case).
TOKEN = re.compile(r"[a-z][a-z_]*")
# Alpha at or below this counts as transparent when the opaque box of a cut-out is measured.
ALPHA_THRESHOLD = 24


def attr(value: object) -> str:
    """A double- or single-quoted attribute value. CR, LF and TAB become character references, so HTML parsing
    (which turns a raw CR into LF) and a byte-exact --check both keep the value as it is."""
    return escape(str(value), {'"': "&quot;", "'": "&#39;", "\n": "&#10;", "\r": "&#13;", "\t": "&#9;"})


def http_url(value: object) -> str | None:
    """The value when it is an absolute http(s) URL, else None (a javascript: or relative page URL is never linked)."""
    if not isinstance(value, str):
        return None
    parts = urlsplit(value.strip())
    return value.strip() if parts.scheme in {"http", "https"} and parts.netloc else None


def site_relative(value: object, field: str, asset_id: str) -> str:
    """A catalog URL the page loads from its own site: relative, inside the site, no scheme or host."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{asset_id}: slideshow {field} is missing")
    if any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError(f"{asset_id}: slideshow {field} holds a space or control character: {value!r}")
    parts = urlsplit(value)
    if (
        parts.scheme
        or parts.netloc
        or value.startswith(("/", "\\"))
        or ".." in unquote(value).replace("\\", "/").split("/")
    ):
        raise ValueError(f"{asset_id}: slideshow {field} is not a site-relative path: {value!r}")
    return value


def safe_path(root: Path, value: object) -> Path | None:
    """`root / value` for a relative path that stays inside root, else None."""
    if not isinstance(value, str) or not value:
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    path = (root / relative).resolve()
    return path if path.is_relative_to(root.resolve()) else None


def records(asset: dict) -> list[dict]:
    """A catalog entry's provenance records, the primary (caption's) record first; a bare record alone."""
    found = asset.get("provenance")
    return found if isinstance(found, list) and found else [asset]


def joined(asset: dict, key: str) -> str:
    """One verbatim name per line: the asset's merged list, or the ordered union of its records' lists; exact
    duplicates (after trimming) appear once. Missing values stay missing (no "Unknown" is written into the data)."""
    lists = [asset.get(key)] if isinstance(asset.get(key), list) else [record.get(key) for record in records(asset)]
    seen, values = set(), []
    for entries in lists:
        for entry in entries or []:
            text = str(entry).strip()
            if text and text not in seen:
                seen.add(text)
                values.append(text)
    return "\n".join(values)


def display_title(asset: dict) -> str:
    """The catalog's human title, verbatim; a record without one (the builder always writes one) is named by its songs,
    then by its upstream file name, then by its ID, as the archive page names it."""
    songs = " / ".join(entry for entry in asset.get("song_titles") or [] if isinstance(entry, str) and entry.strip())
    for value in (asset.get("title"), songs, asset.get("original_filename"), asset.get("id")):
        if isinstance(value, str) and value.strip():
            return value
    return ""


def art_layout(root: Path, asset: dict) -> dict:
    """Stage layout hints for a cut-out on a transparent canvas: data-art-box is the box of the visibly opaque pixels
    (alpha above ALPHA_THRESHOLD) as "x y w h" fractions of the canvas, so the stage sizes and centres the art rather
    than the canvas; data-bleed names the edges the art is cut at (opaque along more than 2% of that edge), so the
    stage can soften the cut. Opaque pictures, other kinds and preview-only slides get none. These are presentation
    hints only: a missing or undecodable file yields no hint here and is reported by the catalog's own verification."""
    if asset.get("kind") not in LAYOUT_KINDS or asset.get("site_original") is not True:
        return {}
    path = safe_path(root, asset.get("path"))
    if path is None or not path.is_file():
        return {}
    try:
        with Image.open(path) as image:
            if "A" not in image.getbands() and "transparency" not in image.info:
                return {}
            alpha = image.convert("RGBA").getchannel("A")
    except (OSError, ValueError, Image.DecompressionBombError):
        return {}
    opaque = alpha.point([0] * (ALPHA_THRESHOLD + 1) + [255] * (255 - ALPHA_THRESHOLD))
    width, height = opaque.size
    box = opaque.getbbox()
    if not box:
        return {}
    fields = {}
    if box != (0, 0, width, height):
        left, top, right, bottom = box
        fractions = (left / width, top / height, (right - left) / width, (bottom - top) / height)
        fields["data-art-box"] = " ".join(f"{round(value, 4):g}" for value in fractions)
    bands = {
        "left": (0, 0, 2, height),
        "top": (0, 0, width, 2),
        "right": (width - 2, 0, width, height),
        "bottom": (0, height - 2, width, height),
    }
    cut = []
    for edge, band in bands.items():
        strip = opaque.crop(band)
        if strip.histogram()[255] > 0.02 * strip.width * strip.height:
            cut.append(edge)
    if cut and len(cut) < 4:
        fields["data-bleed"] = " ".join(cut)
    return fields


def slide_media(asset: dict) -> dict:
    """What the slide shows and what its "Original file" link opens. A slideshow asset is always site-tier in a valid
    catalog (its original is published); should one be archive-only, the slide shows its preview and the link opens
    the repository copy, so the page never points at a file the site does not serve. Neither available is an error."""
    asset_id = str(asset.get("id"))
    if asset.get("site_original") is True:
        url = site_relative(asset.get("url"), "url", asset_id)
        return {"data-src": url, "data-original": url}
    repository = http_url(asset.get("archive_url"))
    if not asset.get("thumb") or not repository:
        raise ValueError(f"{asset_id}: slideshow asset is archive-only and lacks a preview or an archive_url")
    return {
        "data-src": site_relative(asset.get("thumb"), "thumb", asset_id),
        "data-original": repository,
        "data-original-external": "true",
        "data-preview-only": "true",
    }


def slide_origin(asset: dict) -> dict:
    """data-origin: quality.origin_status of the record the caption credits (the primary record), which the slideshow
    names next to the kind (a wiki copy, a repost, ...)."""
    quality = records(asset)[0].get("quality") or asset.get("quality") or {}
    token = quality.get("origin_status") if isinstance(quality, dict) else None
    return {"data-origin": token} if isinstance(token, str) and TOKEN.fullmatch(token) else {}


def slide_fields(asset: dict, layout: dict) -> dict:
    width, height = asset.get("width"), asset.get("height")
    if not (isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0):
        raise ValueError(f"{asset.get('id')}: slideshow asset has no pixel dimensions")
    fields = {
        "class": "dynamix-draw",
        **slide_media(asset),
        "data-id": asset["id"],
        "data-title": display_title(asset),
        "data-source": asset.get("source_name") or asset.get("source_id") or "",
        "data-source-id": asset.get("source_id") or "",
        "data-family": asset.get("family") or "",
    }
    page = http_url(asset.get("page_url"))
    if page:
        fields["data-page"] = page
    fields.update({"data-size": f"{width} × {height}", "data-kind": asset.get("kind") or ""})
    fields.update(slide_origin(asset))
    fields.update(layout)
    for attribute, key in (
        ("data-illustrators", "illustrators"),
        ("data-composers", "composers"),
        ("data-songs", "song_titles"),
        ("data-collections", "collections"),
    ):
        value = joined(asset, key)
        if value:
            fields[attribute] = value
    fields["alt"] = fields["data-title"]
    return fields


def render_slideshow(root: Path, catalog: dict) -> str:
    """index.html for `catalog`: the template with one slide per `slideshow` asset, in catalog order."""
    root = Path(root)
    template = (root / TEMPLATE).read_text(encoding="utf-8-sig")
    if template.count(PLACEHOLDER) != 1:
        raise ValueError(f"{TEMPLATE} must contain {PLACEHOLDER!r} exactly once, found {template.count(PLACEHOLDER)}")
    assets = [asset for asset in catalog.get("assets", []) if asset.get("slideshow") is True]
    # Decoding cut-outs dominates rendering; Pillow decodes outside the GIL, so threads share the work.
    with ThreadPoolExecutor() as pool:
        layouts = list(pool.map(lambda asset: art_layout(root, asset), assets))
    slides = []
    # strict: layouts is pool.map over assets, so the two lists always have the same length.
    for asset, layout in zip(assets, layouts, strict=True):
        attributes = " ".join(f'{key}="{attr(value)}"' for key, value in slide_fields(asset, layout).items())
        slides.append(f'        <div class="mySlides fade"><img {attributes}></div>')
    return template.replace(PLACEHOLDER, "\n".join(slides))
