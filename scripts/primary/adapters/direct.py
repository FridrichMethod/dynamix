"""Explicit file URLs on an explicit page (portfolio galleries, album sites, circle pages, press articles).

For pages whose gallery is rendered by JavaScript (Wix pro-gallery, Tumblr themes) the seed names each file directly:
``{"key", "page_url", "file_url"}``. With ``"read_page": true`` the page is fetched once and its title/text become the
evidence that seed rules quote (an item whose page cannot be read is ``failed``/``blocked`` with that read's error and a
Failure record); otherwise rules cannot claim ``confirmed_primary`` and carry their own notes.

Article pages (``params.article_images``): every image of a read page's article body is accounted for, not only the
named files. Each entry ``{"page_url", "key_prefix", "select", "pattern", "og_image"?, "rewrite"?, "attr_urls"?,
"embedded_json"?, "supplied_marker"?, "supplied_note"?}`` reads the page once and collects, in page order, the image
URLs inside the CSS selection ``select`` (``img`` ``src``/``data-src``/``data-original``/``data-lazy-src``/``srcset``,
and ``a`` ``href`` and the quoted URLs in the attributes named by ``attr_urls`` (e.g. an ``onclick`` overlay opener) that
name an image file), the images of an embedded JSON article model (``embedded_json``, below) and, with ``og_image``,
the page's ``og:image``. Each URL is normalized to the uploaded file (query and fragment dropped, then the ordered
``rewrite`` regex substitutions, e.g. a WordPress ``-1024x576`` size suffix) and, when it fully matches ``pattern``, is
an article image: its key is ``key_prefix`` plus the pattern's ``key`` group (default: the file name without extension);
a ``name`` group names the upload's file name when the URL does not end with it (a signed CDN path), else the last path
segment is the ``original_filename``. Every other image URL of the selection (an app-card icon, a lazy-load
placeholder, a comment avatar, a footer widget) is an *other* image keyed ``<key_prefix>other:<host><path>`` and is
classified by the seed rules like any candidate, so nothing inside the selection is dropped unaccounted. A named seed
item with an image's key or normalized URL stands for it; every other image becomes a candidate of its own, classified
by the seed rules (unclassified images stay ``pending``).

The URLs the page itself lists for an image are kept as download fallbacks: when the normalized URL answers HTTP 404 (a
rewrite of ours, not an upstream failure) the listed URL is downloaded instead and the record says so: its
``source_reported.url`` is the listed URL, a rendition keeps its own file name, and the quality is ``unknown`` (a URL
the page lists is not established as the upload). A listed URL on a host the seed does not allow is not tried.

Advertised sizes become ``source_reported.width``/``height`` of the upload they describe: ``og:image:width``/``height``
of an ``og:image`` listed as the upload URL itself, a WordPress attachment's ``data-orig-size`` (its original upload),
and ``embedded_json`` ``width_field``/``height_field`` (e.g. HK01's ``originalWidth``/``originalHeight``).

``embedded_json`` reads article bodies that a client-side app renders from a JSON payload (e.g. Next.js
``__NEXT_DATA__``): ``{"script_id", "blocks": "dotted.path", "type_field", "images": {block type: field holding one
image object or a list of them}, "url_field", "caption_field"?, "width_field"?, "height_field"?, "count": "dotted.path"?,
"extra_images": ["dotted.path", ...]?}``. Blocks of other types (text, video, related articles) are ignored; ``count``
names the article's own image count, which must equal the number of block images read; ``extra_images`` are image
objects outside the blocks (e.g. the article's main image), read for their URL and size but not counted.

The list ``article-images`` is complete only when every configured page was read and enumerated: a missing selection
(``selection-not-found``), a selection that holds no matching body image (``selection-empty``; an empty response is
never taken as a complete one), a missing embedded payload (``embedded-json-not-found``) or an image count that
disagrees with the article's own (``image-count-mismatch``) keeps it open. A complete enumeration covers (for
``Discovery.coverage``) only the candidates it produces: keys under the page's ``key_prefix``; a page that was not
enumerated completely holds those keys (``Discovery.holds``), so their earlier dispositions stay until it is.

``supplied_marker``: text with which the page itself labels what follows as material supplied by the developer (e.g.
GNN's ``【以下內容為廠商提供資料原文】``); ``supplied_note`` (required with it) is the quality note that replaces the source's
default note (which says the page does not state who supplied an image) for the images after the marker, followed by
a sentence quoting the label. A rule's own quality note is kept and only the label sentence is added. Nothing else is
described as developer-supplied.

Titles (``title`` of a seed item or rule first): an image on a read page is titled with the article's headline (``h1``,
else ``og:title``, else ``<title>``), its caption when the page gives one (``figcaption``, ``.wp-caption-text``,
``.pic-desc``, a non-generic ``img`` ``title``/``alt``, the embedded caption), and its position among the page's
article images ``(n/N)``; a named file the article does not show is ``linked file k/K`` of the page and an other image
``other image k/K``. The file name stays in ``original_filename``.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from bs4 import NavigableString

from lib import checks, net

from .. import model
from . import base

HOSTS: list[str] = []  # per seed
ARTICLE_LIST = "article-images"
IMAGE_ATTRS = ("src", "data-src", "data-original", "data-lazy-src")
IMAGE_FILE_RE = re.compile(r"\.(?:jpe?g|png|gif|webp|avif|bmp|svg)$", re.IGNORECASE)  # a link or quoted URL to an image
OTHER = "other:"  # key infix of a selection image that is not an article upload: ``<key_prefix>other:<host><path>``
GENERIC_ALT = {"", "image", "img", "photo", "picture", "圖片", "图片"}
CONFIG_FIELDS = {
    "page_url",
    "key_prefix",
    "select",
    "pattern",
    "og_image",
    "rewrite",
    "attr_urls",
    "embedded_json",
    "supplied_marker",
    "supplied_note",
}
EMBEDDED_FIELDS = {
    "script_id",
    "blocks",
    "type_field",
    "images",
    "url_field",
    "caption_field",
    "count",
    "width_field",
    "height_field",
    "extra_images",
}
FALLBACK_NOTE = (
    "Archived from {url}, the URL the page lists for this image: the normalized upload URL {file_url} "
    "answered HTTP 404."
)
FALLBACK_QUALITY_NOTE = (
    "Not established as the upload: archived from the URL the page lists ({how}) because the "
    "upload URL {file_url} answered HTTP 404."
)
SUPPLIED_LABEL = "The page places this image after its label '{marker}'."


def _check_embedded(spec: Any, label: str) -> None:
    if not isinstance(spec, dict) or set(spec) - EMBEDDED_FIELDS:
        raise ValueError(f"{label}.embedded_json must be an object with fields {sorted(EMBEDDED_FIELDS)}")
    for name in ("script_id", "blocks", "type_field", "url_field"):
        if not isinstance(spec.get(name), str) or not spec[name].strip():
            raise ValueError(f"{label}.embedded_json.{name} is required")
    images = spec.get("images")
    if (
        not isinstance(images, dict)
        or not images
        or not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in images.items())
    ):
        raise ValueError(f"{label}.embedded_json.images maps block types to image fields")
    for name in ("caption_field", "count", "width_field", "height_field"):
        if name in spec and (not isinstance(spec[name], str) or not spec[name].strip()):
            raise ValueError(f"{label}.embedded_json.{name} must be a non-empty string")
    if ("width_field" in spec) != ("height_field" in spec):
        raise ValueError(f"{label}.embedded_json.width_field and height_field go together")
    extra = spec.get("extra_images", [])
    if not isinstance(extra, list) or not all(isinstance(path, str) and path.strip() for path in extra):
        raise ValueError(f"{label}.embedded_json.extra_images must be a list of dotted paths")


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    for index, item in enumerate(base.require_items(seed, where, ["key", "page_url", "file_url"])):
        for name in ("page_url", "file_url"):
            if checks.http_url(item[name]):
                raise ValueError(f"{where}: items[{index}].{name} {checks.http_url(item[name])}")
    if not seed.get("hosts"):
        raise ValueError(f"{where}: hosts are required")
    pages = (seed.get("params") or {}).get("article_images", [])
    if not isinstance(pages, list):
        raise ValueError(f"{where}: params.article_images must be a list")
    seen = set()
    for index, page in enumerate(pages):
        label = f"{where}: params.article_images[{index}]"
        if not isinstance(page, dict) or set(page) - CONFIG_FIELDS:
            raise ValueError(f"{label} must be an object with page_url, key_prefix, select, pattern")
        for name in ("page_url", "key_prefix", "select", "pattern"):
            if not isinstance(page.get(name), str) or not page[name].strip():
                raise ValueError(f"{label}.{name} is required")
        if checks.http_url(page["page_url"]) or page["page_url"] in seen:
            raise ValueError(f"{label}.page_url must be a distinct http(s) URL")
        seen.add(page["page_url"])
        model.check_regex(page["pattern"], f"{label}.pattern")
        rewrite = page.get("rewrite", [])
        if not isinstance(rewrite, list) or any(
            not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(part, str) for part in pair)
            for pair in rewrite
        ):
            raise ValueError(f"{label}.rewrite must be a list of [pattern, replacement] pairs")
        for pair_index, (pattern, _) in enumerate(rewrite):
            model.check_regex(pattern, f"{label}.rewrite[{pair_index}]")
        attrs = page.get("attr_urls", [])
        if not isinstance(attrs, list) or not all(isinstance(name, str) and name.strip() for name in attrs):
            raise ValueError(f"{label}.attr_urls must be a list of attribute names")
        if "embedded_json" in page:
            _check_embedded(page["embedded_json"], label)
        for name in ("supplied_marker", "supplied_note"):
            if name in page and (not isinstance(page[name], str) or not page[name].strip()):
                raise ValueError(f"{label}.{name} must be a non-empty string")
        if ("supplied_marker" in page) != ("supplied_note" in page):
            raise ValueError(
                f"{label}: supplied_marker and supplied_note go together (the note replaces the source's "
                "default quality note for the images after the marker)"
            )


def normalized(url: str, rewrite: list[list[str]]) -> str:
    """``url`` without query and fragment, then the seed's ``rewrite`` substitutions applied in order."""
    parts = urlsplit(url)
    plain = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    for pattern, replacement in rewrite:
        plain = re.sub(pattern, replacement, plain)
    return plain


def _without_fragment(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _caption(element: Any) -> str:
    """Caption text the page gives one image element (empty when none): a caption element inside the nearest
    ancestors that hold this image alone, else a non-generic ``title``/``alt``."""
    for parent in list(element.parents)[:3]:
        if len(parent.find_all("img")) != 1:
            break
        found = parent.find("figcaption") or parent.find(class_=re.compile(r"wp-caption-text|pic-desc"))
        if found is not None:
            text = " ".join(found.get_text(" ").split())
            if text:
                return text
    for name in ("title", "alt"):
        value = " ".join(str(element.get(name) or "").split())
        if value.lower() not in GENERIC_ALT and not re.search(r"\.(jpe?g|png|gif|webp)$", value, re.I):
            return value
    return ""


def _path(data: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def _size(width: Any, height: Any) -> tuple[int, int] | None:
    """(width, height) when both are positive integers (or digit strings), else None."""

    def number(value: Any) -> int | None:
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None

    pair = (number(width), number(height))
    return pair if None not in pair else None


def _embedded_images(
    document: Any, spec: Mapping[str, Any]
) -> tuple[list[tuple[str, str, Any]], int | None, list[tuple[str, str, Any]]] | None:
    """([(url, caption, size)] in block order, the article's own image count or None, [(url, caption, size)] of the
    ``extra_images``) from the embedded JSON payload; None when the script or the blocks list is missing or
    unreadable. ``size`` is the advertised (width, height) of the upload, or None."""
    script = document.find("script", id=spec["script_id"])
    if script is None:
        return None
    try:
        data = json.loads(script.string or script.get_text() or "")
    except ValueError:
        return None
    blocks = _path(data, spec["blocks"])
    if not isinstance(blocks, list):
        return None

    def read(image: Any) -> tuple[str, str, Any] | None:
        if not isinstance(image, dict) or not isinstance(image.get(spec["url_field"]), str):
            return None
        caption = image.get(spec.get("caption_field") or "")
        size = (
            _size(image.get(spec["width_field"]), image.get(spec["height_field"])) if spec.get("width_field") else None
        )
        return image[spec["url_field"]], " ".join(caption.split()) if isinstance(caption, str) else "", size

    found = []
    for block in blocks:
        if not isinstance(block, dict) or block.get(spec["type_field"]) not in spec["images"]:
            continue
        value = block.get(spec["images"][block[spec["type_field"]]])
        found.extend(entry for entry in map(read, value if isinstance(value, list) else [value]) if entry)
    extra = [entry for entry in (read(_path(data, path)) for path in spec.get("extra_images", [])) if entry]
    count = _path(data, spec["count"]) if spec.get("count") else None
    return found, count if isinstance(count, int) and not isinstance(count, bool) else None, extra


def _after_marker(roots: list[Any], marker: str | None) -> set[int]:
    """``id()`` of every element of the selection that follows the page's ``marker`` text (document order)."""
    flagged: set[int] = set()
    if not marker:
        return flagged
    seen = False
    for root in roots:
        for node in root.descendants:
            if isinstance(node, NavigableString):
                seen = seen or marker in str(node)
            elif seen:
                flagged.add(id(node))
    return flagged


def _og_images(document: Any) -> list[tuple[str, Any]]:
    """[(og:image URL, its advertised (width, height) or None)] in page order; ``og:image:width``/``height`` belong to
    the ``og:image`` they follow."""
    found: list[list[Any]] = []
    sizes: dict[int, dict[str, Any]] = {}
    for tag in document.find_all("meta", attrs={"property": True}):
        name = str(tag.get("property"))
        if name == "og:image":
            found.append([tag.get("content"), None])
        elif found and name in ("og:image:width", "og:image:height"):
            sizes.setdefault(len(found) - 1, {})[name] = tag.get("content")
    for index, entry in enumerate(found):
        given = sizes.get(index, {})
        entry[1] = _size(given.get("og:image:width"), given.get("og:image:height"))
    return [(url, size) for url, size in found]


_SRCSET_SPACE = "\t\n\f\r "  # ASCII whitespace (HTML)


def srcset_urls(value: str) -> list[str]:
    """The image URLs of a ``srcset`` attribute, parsed as HTML does: a candidate URL runs to whitespace (so it may
    contain commas, e.g. Jetpack's ``?resize=300,169``) without its trailing commas, and its descriptors run to the next
    comma outside parentheses."""
    urls: list[str] = []
    position, end = 0, len(value)
    while True:
        while position < end and (value[position] in _SRCSET_SPACE or value[position] == ","):
            position += 1
        if position >= end:
            return urls
        start = position
        while position < end and value[position] not in _SRCSET_SPACE:
            position += 1
        url = value[start:position]
        if url.endswith(","):  # no descriptors: the comma ends the candidate
            url = url.rstrip(",")
        else:
            in_parens = False
            while position < end and (value[position] != "," or in_parens):
                if value[position] == "(":
                    in_parens = True
                elif value[position] == ")":
                    in_parens = False
                position += 1
        if url:
            urls.append(url)


def _orig_size(element: Any) -> tuple[int, int] | None:
    """The original upload's (width, height) a WordPress attachment ``img`` advertises (``data-orig-size="W,H"``)."""
    parts = str(element.get("data-orig-size") or "").split(",")
    return _size(*parts) if len(parts) == 2 else None


def article_images(
    html: str,
    page_url: str,
    config: Mapping[str, Any],
    *,
    skipped: list[str] | None = None,
    final_url: str | None = None,
) -> tuple[list[dict], list[dict], str | None]:
    """([article image], [other image], None when the enumeration is complete or the open continuation token) for one
    configured article page; each image is ``{key, url, advertised, caption, supplied, name, size}`` in page order.

    Links are resolved as a browser requests them: against the document base (``<base href>``, else ``final_url``, the
    URL the page was served from after redirects, else ``page_url``) and encoded by :func:`base.link_url` (e.g. a
    literal space percent-encoded). An image URL that is still no valid http(s) URL is not keyed: it is appended to
    ``skipped`` and keeps the enumeration open (``urls-skipped-<n>``)."""
    document = base.soup(html)
    pattern = re.compile(config["pattern"])
    rewrite = config.get("rewrite", [])
    found: dict[str, dict] = {}
    others: dict[str, dict] = {}
    body: set[str] = set()
    unusable: list[str] = skipped if skipped is not None else []
    title = base.headline(document)
    resolve = base.document_base(document, final_url or page_url)

    def add(
        raw: str | None,
        caption: str = "",
        *,
        supplied: bool = False,
        in_body: bool = True,
        size: tuple[int, int] | None = None,
        size_of_listed: bool = False,
        image_link: bool = True,
    ) -> str | None:
        """Record one listed URL; returns its key, or None when it names no image. ``size`` is advertised for the
        upload (``size_of_listed``: only for the URL exactly as listed). Outside the article's uploads, an ``a`` link
        or a quoted URL (``image_link`` False) counts only when it names an image file."""
        if not raw or not raw.strip():
            return None
        try:
            absolute = _without_fragment(base.link_url(resolve, raw))
            scheme = urlsplit(absolute).scheme
        except ValueError:  # unparseable, e.g. an unbalanced IPv6 bracket
            unusable.append(raw)
            return None
        if scheme not in ("http", "https"):  # data: placeholders, javascript: links
            return None
        url = normalized(absolute, rewrite)
        match = pattern.fullmatch(url)
        if match:
            stem = base.last_segment(url).rsplit(".", 1)[0]
            key, bucket = config["key_prefix"] + (match.groupdict().get("key") or stem), found
            name = match.groupdict().get("name") or base.last_segment(url)
        elif in_body and (image_link or IMAGE_FILE_RE.search(urlsplit(url).path)):
            parts = urlsplit(url)
            stem, name = base.last_segment(url).rsplit(".", 1)[0], base.last_segment(url)
            key, bucket = f"{config['key_prefix']}{OTHER}{parts.netloc}{parts.path}", others
        else:
            return None
        if checks.url_problem(url):  # keyed and downloaded as this URL: it must be one
            if url not in unusable:
                unusable.append(url)
            return None
        entry = bucket.setdefault(
            key,
            {"key": key, "url": url, "advertised": [], "caption": "", "supplied": False, "name": name, "size": None},
        )
        if absolute != url and absolute not in entry["advertised"] and not checks.url_problem(absolute):
            entry["advertised"].append(absolute)
        entry["supplied"] = entry["supplied"] or supplied
        if size and entry["size"] is None and (absolute == url or not size_of_listed):
            entry["size"] = size
        if in_body and bucket is found:
            body.add(key)
        # A caption that only repeats the headline or the file name says nothing about the image.
        if caption and not (title and caption.startswith(title)) and not stem.lower().startswith(caption.lower()):
            entry["caption"] = entry["caption"] or caption
        return key

    if config.get("og_image"):  # every og:image the page lists (some CMSs list all of the post's images there)
        for url, size in _og_images(document):
            add(url, in_body=False, size=size, size_of_listed=True)
    roots = document.select(config["select"])
    supplied = _after_marker(roots, config.get("supplied_marker"))
    for root in roots:
        for element in root.find_all(True):
            flag = id(element) in supplied
            for name in config.get("attr_urls", []):
                for quoted in re.findall(r"""["']([^"'\s]+)["']""", str(element.get(name) or "")):
                    add(quoted, supplied=flag, image_link=False)
            if element.name == "a":
                add(element.get("href"), supplied=flag, image_link=False)
            elif element.name in ("img", "source"):
                caption = _caption(element) if element.name == "img" else ""
                size = _orig_size(element)
                for name in IMAGE_ATTRS:
                    add(element.get(name), caption, supplied=flag, size=size)
                for candidate in srcset_urls(str(element.get("srcset") or "")):
                    add(candidate, caption, supplied=flag, size=size)
    state = None
    spec = config.get("embedded_json")
    if spec:
        embedded = _embedded_images(document, spec)
        if embedded is None:
            state = "embedded-json-not-found"
        else:
            images, count, extra = embedded
            keys = {key for key in (add(url, caption, size=size) for url, caption, size in images) if key}
            for url, caption, size in extra:
                add(url, caption, in_body=False, size=size)
            if count is not None and count != len(keys):
                state = f"image-count-mismatch:{len(keys)}/{count}"
    if not roots:
        state = "selection-not-found"
    elif not body and state is None:
        state = "selection-empty"
    if unusable and state is None:
        state = f"urls-skipped-{len(unusable)}"
    return list(found.values()), list(others.values()), state


def _title(
    item_title: str | None,
    page_headline: str,
    caption: str,
    position: tuple[int, int] | None,
    fallback: str,
    linked: tuple[int, int] | None = None,
    other: tuple[int, int] | None = None,
) -> str:
    if item_title:
        return item_title
    if not page_headline:
        return fallback
    text = f"{page_headline} — {caption}" if caption and caption not in page_headline else page_headline
    if other is not None:  # an image of the selection that is not one of the article's uploads
        suffix = f" (other image {other[0]}/{other[1]})" if other[1] > 1 else " (other image)"
    elif position is None:  # a named file the article body does not show
        index, total = linked or (1, 1)
        suffix = f" (linked file {index}/{total})" if total > 1 else " (linked file)"
    else:
        suffix = f" ({position[0]}/{position[1]})" if position[1] > 1 else ""
    return model.clip(text, 140 - len(suffix)) + suffix


def _covering(configs: Mapping[str, Mapping[str, Any]], complete: set[str]) -> Any:
    """Coverage predicate: a previous candidate is covered by a complete enumeration of its page only when it is one
    the enumeration produces (its key is under the page's ``key_prefix``); named items and unread pages never are."""

    def covers(candidate: Mapping[str, Any]) -> bool:
        page = candidate.get("page_url")
        return page in complete and str(candidate.get("upstream_key", "")).startswith(configs[page]["key_prefix"])

    return covers


def _holding(configs: Mapping[str, Mapping[str, Any]], complete: set[str]) -> Any:
    """Hold predicate: a previous candidate under a configured page's ``key_prefix`` while that page was not
    enumerated completely (unread, or an open state such as ``selection-not-found``)."""

    def holds(candidate: Mapping[str, Any]) -> bool:
        page = candidate.get("page_url")
        return (
            page in configs
            and page not in complete
            and str(candidate.get("upstream_key", "")).startswith(configs[page]["key_prefix"])
        )

    return holds


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    configs = {page["page_url"]: page for page in (seed.get("params") or {}).get("article_images", [])}
    discovery = base.new_discovery(
        seed,
        lists=["seed-files", *([ARTICLE_LIST] if configs else [])],
        scope_notes=(
            "Explicitly named files, and every article-body image of the "
            "configured article pages; nothing else is followed."
            if configs
            else "Explicitly named files only; the page's gallery is not crawled."
        ),
    )
    pages: dict[str, dict] = {}
    read_complete: set[str] = set()
    if configs:
        discovery.coverage[ARTICLE_LIST] = _covering(configs, read_complete)
        discovery.holds[ARTICLE_LIST] = _holding(configs, read_complete)

    def read(page_url: str) -> dict:
        if page_url not in pages:
            try:
                html, final_url, _ = ctx.client.get_text(page_url)
                document = base.soup(html)
                pages[page_url] = {
                    "html": html,
                    "final_url": final_url if final_url else None,
                    "texts": {
                        "title": document.title.get_text(" ", strip=True) if document.title else "",
                        "body": base.page_text(base.soup(html)),
                    },
                    "headline": base.headline(document),
                }
            except net.FetchError as error:
                discovery.errors.append(model.discovery_error("discover", page_url, str(error)))
                base.mark_unread(discovery, "seed-files", error)
                if page_url in configs:
                    base.mark_unread(discovery, ARTICLE_LIST, error)
                pages[page_url] = {"html": None, "texts": {}, "headline": "", "error": error}
        return pages[page_url]

    enumerated: dict[str, list[dict]] = {}
    others: dict[str, list[dict]] = {}
    for page_url, config in configs.items():
        page = read(page_url)
        if page["html"] is None:
            continue
        skipped: list[str] = []
        images, others[page_url], state = article_images(
            page["html"], page_url, config, skipped=skipped, final_url=page["final_url"]
        )
        enumerated[page_url] = images
        for url in skipped:
            discovery.errors.append(
                model.discovery_error(
                    "discover",
                    page_url,
                    f"image URL skipped, not a valid http(s) URL ({checks.url_problem(url) or 'unparseable'}): "
                    f"{model.clip(repr(url), 160)}",
                )
            )
        if state is None:
            read_complete.add(page_url)
            continue
        script = (config.get("embedded_json") or {}).get("script_id")
        detail = {
            "selection-not-found": f"article selection {config['select']!r} not found on the page",
            "selection-empty": f"article selection {config['select']!r} holds no image matching the pattern; "
            "an empty selection is not a complete enumeration",
            "embedded-json-not-found": f"embedded article payload {script!r} not found or unreadable",
        }.get(state, f"article image count disagrees with the page ({state})")
        if state.startswith("urls-skipped-"):
            detail = f"{len(skipped)} image URL(s) of the page could not be keyed; the enumeration is not complete"
        discovery.errors.append(model.discovery_error("discover", page_url, detail))
        discovery.continuation[ARTICLE_LIST] = discovery.continuation.get(ARTICLE_LIST) or state

    def position(page_url: str, key: str, url: str | None) -> tuple[tuple[int, int] | None, dict | None]:
        images = enumerated.get(page_url, [])
        for index, image in enumerate(images, start=1):
            if image["key"] == key or (url and image["url"] == url):
                return (index, len(images)), image
        return None, None

    placed = {id(item): position(item["page_url"], item["key"], item["file_url"]) for item in seed["items"]}
    unshown: dict[str, list[int]] = {}
    for item in seed["items"]:
        if placed[id(item)][0] is None and item.get("read_page"):
            unshown.setdefault(item["page_url"], []).append(id(item))
    claimed: set[tuple[str, str]] = set()
    for item in seed["items"]:
        page_url, file_url, key = item["page_url"], item["file_url"], item["key"]
        page = read(page_url) if item.get("read_page") else {"texts": {}, "headline": ""}
        name = base.last_segment(file_url)
        if page.get("error") is not None:
            # The page whose text the seed rule quotes could not be read: the item fails (or is blocked) with that
            # error and a Failure record, instead of a rule check against text that was never fetched.
            discovery.candidates.append(
                model.error_draft(
                    key,
                    page_url,
                    _title(item.get("title"), "", "", None, name),
                    page["error"],
                    phase="discover",
                    file_url=base.stored(file_url),
                    source_reported=base.reported(url=file_url),
                )
            )
            continue
        place, image = placed[id(item)]
        linked = None
        if place is not None:
            claimed.add((page_url, image["key"]))
        elif id(item) in unshown.get(page_url, []):
            linked = (unshown[page_url].index(id(item)) + 1, len(unshown[page_url]))
        discovery.candidates.append(
            _draft(
                ctx,
                seed,
                item,
                key=key,
                page_url=page_url,
                file_url=file_url,
                texts=page["texts"],
                title=_title(
                    item.get("title"), page["headline"], image["caption"] if image else "", place, name, linked
                ),
                image=image,
                config=configs.get(page_url),
            )
        )
    for page_url, images in enumerated.items():
        page = pages[page_url]
        for index, image in enumerate(images, start=1):
            if (page_url, image["key"]) in claimed:
                continue
            name = base.last_segment(image["url"])
            item = {"key": image["key"], "page_url": page_url, "file_url": image["url"]}
            discovery.candidates.append(
                _draft(
                    ctx,
                    seed,
                    item,
                    key=image["key"],
                    page_url=page_url,
                    file_url=image["url"],
                    texts=page["texts"],
                    title=_title(None, page["headline"], image["caption"], (index, len(images)), name),
                    image=image,
                    config=configs[page_url],
                )
            )
    named = {(item["page_url"], item["file_url"]) for item in seed["items"]}
    for page_url, images in others.items():  # selection images that are not the article's uploads
        page = pages[page_url]
        listed = [image for image in images if (page_url, image["url"]) not in named]
        for index, image in enumerate(listed, start=1):
            name = base.last_segment(image["url"])
            item = {"key": image["key"], "page_url": page_url, "file_url": image["url"]}
            discovery.candidates.append(
                _draft(
                    ctx,
                    seed,
                    item,
                    key=image["key"],
                    page_url=page_url,
                    file_url=image["url"],
                    texts=page["texts"],
                    title=_title(None, page["headline"], image["caption"], None, name, other=(index, len(listed))),
                    image=image,
                    config=configs[page_url],
                )
            )
    return discovery


def _draft(
    ctx: Any,
    seed: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    key: str,
    page_url: str,
    file_url: str,
    texts: Mapping[str, str],
    title: str,
    image: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> model.Draft:
    name = image["name"] if image is not None else base.last_segment(file_url)
    ext = name.rsplit(".", 1)[-1] if "." in name else None
    size = image["size"] if image is not None and image["url"] == file_url and image["size"] else (None, None)
    draft = model.apply_rule(
        model.find_rule(base.rules(seed), key),
        key=key,
        page_url=page_url,
        texts=texts,
        file_url=base.stored(file_url),
        default_title=title,
        default_quality=ctx.quality,
        default_rights=ctx.rights,
        original_filename=item.get("original_filename") or name,
        base_evidence=[page_url],
        source_reported=base.reported(url=file_url, mime=base.mime_for(ext), width=size[0], height=size[1]),
    )
    if item.get("notes") and "notes" not in draft.candidate_extra:
        draft.candidate_extra["notes"] = item["notes"]
    if image is None:
        return draft
    if image["supplied"] and draft.action == "download" and config and config.get("supplied_marker"):
        draft.meta = {**draft.meta, "quality": _supplied_quality(draft.meta["quality"], ctx.quality, config)}
    draft.fallback_urls = [url for url in image["advertised"] if url != file_url][:3]
    draft.fallback_note = FALLBACK_NOTE
    draft.fallback_adjust = _fallback_adjust(image, file_url, item.get("original_filename"))
    return draft


def _supplied_quality(quality: Mapping[str, Any], default: Mapping[str, Any], config: Mapping[str, Any]) -> dict:
    """Quality of an image the page places after its developer-supplied label: the source's default note (which says
    the page does not state who supplied the image) is replaced by the configured ``supplied_note``; a rule's own note
    is kept. Either way a sentence quotes the label."""
    notes = quality.get("notes") or ""
    if notes == (default.get("notes") or ""):
        notes = config["supplied_note"]
    label = SUPPLIED_LABEL.format(marker=config["supplied_marker"])
    return {**quality, "notes": " ".join(filter(None, [notes, label]))}


def _fallback_adjust(
    image: Mapping[str, Any], file_url: str, named: str | None
) -> Callable[[model.Draft, str], model.Draft]:
    """The ``fallback_adjust`` hook of an article image: archived from ``url``, a URL the page lists, the record names
    that URL as advertised (sizes advertised for the upload do not describe it), keeps a rendition's own file name
    (unless the seed or the pattern names the upload's) and is not called the upload (``source_variant: unknown``)."""

    def adjust(draft: model.Draft, url: str) -> model.Draft:
        parts = urlsplit(url)
        plain = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        rendition = plain != file_url  # the seed's rewrite, not only the query, maps it to the upload
        used = copy.copy(draft)
        own_name = not named and image["name"] == base.last_segment(image["url"])
        if rendition and own_name:
            used.original_filename = base.last_segment(plain)
        ext = used.original_filename.rsplit(".", 1)[-1] if "." in (used.original_filename or "") else None
        used.source_reported = {
            **draft.source_reported,
            "url": base.stored(url),
            "width": None,
            "height": None,
            "mime": base.mime_for(ext),
        }
        if draft.action == "download":
            how = (
                "a rendition or proxy copy that the seed's rewrite maps to the upload"
                if rendition
                else "the upload URL with the page's query"
            )
            note = FALLBACK_QUALITY_NOTE.format(how=how, file_url=base.stored(file_url))
            quality = {**draft.meta["quality"], "source_variant": "unknown"}
            quality["notes"] = " ".join(filter(None, [quality.get("notes"), note]))
            used.meta = {**draft.meta, "quality": quality}
        return used

    return adjust
