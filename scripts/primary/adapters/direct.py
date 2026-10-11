"""Explicit file URLs on an explicit page (portfolio galleries, album sites, circle pages, press articles).

For pages whose gallery is rendered by JavaScript (Wix pro-gallery, Tumblr themes) the seed names each file directly:
``{"key", "page_url", "file_url"}``. With ``"read_page": true`` the page is fetched once and its title/text become the
evidence that seed rules quote; otherwise rules cannot claim ``confirmed_primary`` and carry their own notes.

Article pages (``params.article_images``): every image of a read page's article body is accounted for, not only the
named files. Each entry ``{"page_url", "key_prefix", "select", "pattern", "og_image"?, "rewrite"?, "attr_urls"?,
"embedded_json"?, "supplied_marker"?}`` reads the page once and collects, in page order, the image URLs inside the CSS
selection ``select`` (``img`` ``src``/``data-src``/``data-original``/``data-lazy-src``/``srcset`` and ``a`` ``href``;
quoted URLs in the attributes named by ``attr_urls``, e.g. an ``onclick`` overlay opener), the images of an embedded
JSON article model (``embedded_json``, below) and, with ``og_image``, the page's ``og:image``. Each URL is normalized
to the uploaded file (query and fragment dropped, then the ordered ``rewrite`` regex substitutions, e.g. a WordPress
``-1024x576`` size suffix) and kept when it fully matches ``pattern``; its key is ``key_prefix`` plus the pattern's
``key`` group (default: the file name without extension). A named seed item with that key or that normalized URL
stands for it; every other image becomes a candidate of its own, classified by the seed rules (unclassified images
stay ``pending``). The URLs the page itself lists for an image are kept as download fallbacks: when the normalized URL
answers HTTP 404 (a rewrite of ours, not an upstream failure) the advertised URL is downloaded instead and the record
says so.

``embedded_json`` reads article bodies that a client-side app renders from a JSON payload (e.g. Next.js
``__NEXT_DATA__``): ``{"script_id", "blocks": "dotted.path", "type_field", "images": {block type: field holding one
image object or a list of them}, "url_field", "caption_field"?, "count": "dotted.path"?}``. Blocks of other types (text,
video, related articles) are ignored; ``count`` names the article's own image count, which must equal the number of
images read.

The list ``article-images`` is complete only when every configured page was read and enumerated: a missing selection
(``selection-not-found``), a selection that holds no matching body image (``selection-empty``; an empty response is
never taken as a complete one), a missing embedded payload (``embedded-json-not-found``) or an image count that
disagrees with the article's own (``image-count-mismatch``) keeps it open. A complete enumeration covers (for
``Discovery.coverage``) only the candidates it produces: keys under the page's ``key_prefix``.

``supplied_marker``: text with which the page itself labels what follows as material supplied by the developer (e.g.
GNN's ``【以下內容為廠商提供資料原文】``). Images after the marker in the selection carry a quality note quoting it;
nothing else is described as developer-supplied.

Titles (``title`` of a seed item or rule first): an image on a read page is titled with the article's headline (``h1``,
else ``og:title``, else ``<title>``), its caption when the page gives one (``figcaption``, ``.wp-caption-text``,
``.pic-desc``, a non-generic ``img`` ``title``/``alt``, the embedded caption), and its position among the page's
article images ``(n/N)``; a named file the article does not show is ``linked file k/K`` of the page. The file name stays
in ``original_filename``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from bs4 import NavigableString

from lib import checks, net

from .. import model
from . import base

HOSTS: list[str] = []  # per seed
ARTICLE_LIST = "article-images"
IMAGE_ATTRS = ("src", "data-src", "data-original", "data-lazy-src")
GENERIC_ALT = {"", "image", "img", "photo", "picture", "圖片", "图片"}
CONFIG_FIELDS = {"page_url", "key_prefix", "select", "pattern", "og_image", "rewrite", "attr_urls", "embedded_json",
                 "supplied_marker"}
EMBEDDED_FIELDS = {"script_id", "blocks", "type_field", "images", "url_field", "caption_field", "count"}
FALLBACK_NOTE = ("Archived from {url}, the URL the page lists for this image: the normalized upload URL {file_url} "
                 "answered HTTP 404.")


def _check_embedded(spec: Any, label: str) -> None:
    if not isinstance(spec, dict) or set(spec) - EMBEDDED_FIELDS:
        raise ValueError(f"{label}.embedded_json must be an object with fields {sorted(EMBEDDED_FIELDS)}")
    for name in ("script_id", "blocks", "type_field", "url_field"):
        if not isinstance(spec.get(name), str) or not spec[name].strip():
            raise ValueError(f"{label}.embedded_json.{name} is required")
    images = spec.get("images")
    if not isinstance(images, dict) or not images or not all(
            isinstance(k, str) and isinstance(v, str) and k and v for k, v in images.items()):
        raise ValueError(f"{label}.embedded_json.images maps block types to image fields")
    for name in ("caption_field", "count"):
        if name in spec and (not isinstance(spec[name], str) or not spec[name].strip()):
            raise ValueError(f"{label}.embedded_json.{name} must be a non-empty string")


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
        re.compile(page["pattern"])
        rewrite = page.get("rewrite", [])
        if not isinstance(rewrite, list) or any(not isinstance(pair, list) or len(pair) != 2
                                                or not all(isinstance(part, str) for part in pair) for pair in rewrite):
            raise ValueError(f"{label}.rewrite must be a list of [pattern, replacement] pairs")
        for pattern, _ in rewrite:
            re.compile(pattern)
        attrs = page.get("attr_urls", [])
        if not isinstance(attrs, list) or not all(isinstance(name, str) and name.strip() for name in attrs):
            raise ValueError(f"{label}.attr_urls must be a list of attribute names")
        if "embedded_json" in page:
            _check_embedded(page["embedded_json"], label)
        if "supplied_marker" in page and (not isinstance(page["supplied_marker"], str)
                                          or not page["supplied_marker"].strip()):
            raise ValueError(f"{label}.supplied_marker must be a non-empty string")


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


def _embedded_images(document: Any, spec: Mapping[str, Any]) -> tuple[list[tuple[str, str]], int | None] | None:
    """([(url, caption)] in block order, the article's own image count or None) from the embedded JSON payload; None
    when the script or the blocks list is missing or unreadable."""
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
    found = []
    for block in blocks:
        if not isinstance(block, dict) or block.get(spec["type_field"]) not in spec["images"]:
            continue
        value = block.get(spec["images"][block[spec["type_field"]]])
        for image in value if isinstance(value, list) else [value]:
            if isinstance(image, dict) and isinstance(image.get(spec["url_field"]), str):
                caption = image.get(spec.get("caption_field") or "")
                found.append((image[spec["url_field"]], " ".join(caption.split()) if isinstance(caption, str) else ""))
    count = _path(data, spec["count"]) if spec.get("count") else None
    return found, count if isinstance(count, int) and not isinstance(count, bool) else None


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


def article_images(html: str, page_url: str, config: Mapping[str, Any]) -> tuple[list[dict], str | None]:
    """([{key, url, advertised, caption, supplied}] in page order, None when the enumeration is complete or the open
    continuation token) for one configured article page."""
    document = base.soup(html)
    pattern = re.compile(config["pattern"])
    rewrite = config.get("rewrite", [])
    found: dict[str, dict] = {}
    body: set[str] = set()
    title = base.headline(document)

    def add(raw: str | None, caption: str = "", *, supplied: bool = False, in_body: bool = True) -> str | None:
        """Record one listed URL; returns its key, or None when it is not an article image."""
        if not raw or not raw.strip():
            return None
        absolute = _without_fragment(base.absolute(page_url, raw))
        url = normalized(absolute, rewrite)
        match = pattern.fullmatch(url)
        if not match:
            return None
        stem = base.last_segment(url).rsplit(".", 1)[0]
        key = config["key_prefix"] + (match.groupdict().get("key") or stem)
        entry = found.setdefault(key, {"key": key, "url": url, "advertised": [], "caption": "", "supplied": False})
        if absolute != url and absolute not in entry["advertised"]:
            entry["advertised"].append(absolute)
        entry["supplied"] = entry["supplied"] or supplied
        if in_body:
            body.add(key)
        # A caption that only repeats the headline or the file name says nothing about the image.
        if caption and not (title and caption.startswith(title)) and not stem.lower().startswith(caption.lower()):
            entry["caption"] = entry["caption"] or caption
        return key

    if config.get("og_image"):  # every og:image the page lists (some CMSs list all of the post's images there)
        for tag in document.find_all("meta", attrs={"property": "og:image"}):
            add(tag.get("content"), in_body=False)
    roots = document.select(config["select"])
    supplied = _after_marker(roots, config.get("supplied_marker"))
    for root in roots:
        for element in root.find_all(True):
            flag = id(element) in supplied
            for name in config.get("attr_urls", []):
                for quoted in re.findall(r"""["']([^"'\s]+)["']""", str(element.get(name) or "")):
                    add(quoted, supplied=flag)
            if element.name == "a":
                add(element.get("href"), supplied=flag)
            elif element.name in ("img", "source"):
                caption = _caption(element) if element.name == "img" else ""
                for name in IMAGE_ATTRS:
                    add(element.get(name), caption, supplied=flag)
                for candidate in str(element.get("srcset") or "").split(","):
                    add(candidate.strip().split(" ")[0], caption, supplied=flag)
    state = None
    spec = config.get("embedded_json")
    if spec:
        embedded = _embedded_images(document, spec)
        if embedded is None:
            state = "embedded-json-not-found"
        else:
            images, count = embedded
            keys = {key for key in (add(url, caption) for url, caption in images) if key}
            if count is not None and count != len(keys):
                state = f"image-count-mismatch:{len(keys)}/{count}"
    if not roots:
        state = "selection-not-found"
    elif not body and state is None:
        state = "selection-empty"
    return list(found.values()), state


def _title(item_title: str | None, page_headline: str, caption: str, position: tuple[int, int] | None,
           fallback: str, linked: tuple[int, int] | None = None) -> str:
    if item_title:
        return item_title
    if not page_headline:
        return fallback
    text = f"{page_headline} — {caption}" if caption and caption not in page_headline else page_headline
    if position is None:  # a named file the article body does not show
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


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    configs = {page["page_url"]: page for page in (seed.get("params") or {}).get("article_images", [])}
    discovery = base.new_discovery(seed, lists=["seed-files", *([ARTICLE_LIST] if configs else [])],
                                   scope_notes=("Explicitly named files, and every article-body image of the "
                                                "configured article pages; nothing else is followed." if configs else
                                                "Explicitly named files only; the page's gallery is not crawled."))
    pages: dict[str, dict] = {}
    read_complete: set[str] = set()
    if configs:
        discovery.coverage[ARTICLE_LIST] = _covering(configs, read_complete)

    def read(page_url: str) -> dict:
        if page_url not in pages:
            try:
                html, _, _ = ctx.client.get_text(page_url)
                document = base.soup(html)
                pages[page_url] = {"html": html, "texts": {"title": document.title.get_text(" ", strip=True)
                                                           if document.title else "",
                                                           "body": base.page_text(base.soup(html))},
                                   "headline": base.headline(document)}
            except net.FetchError as error:
                discovery.errors.append(model.discovery_error("discover", page_url, str(error)))
                base.mark_unread(discovery, "seed-files", error)
                if page_url in configs:
                    base.mark_unread(discovery, ARTICLE_LIST, error)
                pages[page_url] = {"html": None, "texts": {}, "headline": ""}
        return pages[page_url]

    enumerated: dict[str, list[dict]] = {}
    for page_url, config in configs.items():
        page = read(page_url)
        if page["html"] is None:
            continue
        images, state = article_images(page["html"], page_url, config)
        enumerated[page_url] = images
        if state is None:
            read_complete.add(page_url)
            continue
        script = (config.get("embedded_json") or {}).get("script_id")
        detail = {"selection-not-found": f"article selection {config['select']!r} not found on the page",
                  "selection-empty": f"article selection {config['select']!r} holds no image matching the pattern; "
                                     "an empty selection is not a complete enumeration",
                  "embedded-json-not-found": f"embedded article payload {script!r} not found or unreadable",
                  }.get(state, f"article image count disagrees with the page ({state})")
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
        place, image = placed[id(item)]
        linked = None
        if place is not None:
            claimed.add((page_url, image["key"]))
        elif id(item) in unshown.get(page_url, []):
            linked = (unshown[page_url].index(id(item)) + 1, len(unshown[page_url]))
        discovery.candidates.append(_draft(
            ctx, seed, item, key=key, page_url=page_url, file_url=file_url, texts=page["texts"],
            title=_title(item.get("title"), page["headline"], image["caption"] if image else "", place, name, linked),
            image=image, config=configs.get(page_url)))
    for page_url, images in enumerated.items():
        page = pages[page_url]
        for index, image in enumerate(images, start=1):
            if (page_url, image["key"]) in claimed:
                continue
            name = base.last_segment(image["url"])
            item = {"key": image["key"], "page_url": page_url, "file_url": image["url"]}
            discovery.candidates.append(_draft(
                ctx, seed, item, key=image["key"], page_url=page_url, file_url=image["url"], texts=page["texts"],
                title=_title(None, page["headline"], image["caption"], (index, len(images)), name), image=image,
                config=configs[page_url]))
    return discovery


def _draft(ctx: Any, seed: Mapping[str, Any], item: Mapping[str, Any], *, key: str, page_url: str, file_url: str,
           texts: Mapping[str, str], title: str, image: Mapping[str, Any] | None = None,
           config: Mapping[str, Any] | None = None) -> model.Draft:
    name = base.last_segment(file_url)
    ext = name.rsplit(".", 1)[-1] if "." in name else None
    draft = model.apply_rule(
        model.find_rule(base.rules(seed), key), key=key, page_url=page_url, texts=texts,
        file_url=base.stored(file_url), default_title=title, default_quality=ctx.quality,
        default_rights=ctx.rights, original_filename=item.get("original_filename") or name,
        base_evidence=[page_url], source_reported=base.reported(url=file_url, mime=base.mime_for(ext)))
    if item.get("notes") and "notes" not in draft.candidate_extra:
        draft.candidate_extra["notes"] = item["notes"]
    if image is not None:
        draft.fallback_urls = [url for url in image["advertised"] if url != file_url][:3]
        draft.fallback_note = FALLBACK_NOTE
        if image["supplied"] and draft.action == "download" and config and config.get("supplied_marker"):
            quality = draft.meta["quality"]
            quality["notes"] = " ".join(filter(None, [quality.get("notes"), (
                "The page places this image after its label "
                f"'{config['supplied_marker']}', i.e. within material it states the developer supplied.")]))
    return draft
