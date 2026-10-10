"""Explicit file URLs on an explicit page (portfolio galleries, album sites, circle pages, press articles).

For pages whose gallery is rendered by JavaScript (Wix pro-gallery, Tumblr themes) the seed names each file directly:
``{"key", "page_url", "file_url"}``. With ``"read_page": true`` the page is fetched once and its title/text become the
evidence that seed rules quote; otherwise rules cannot claim ``confirmed_primary`` and carry their own notes.

Article pages (``params.article_images``): every image of a read page's article body is accounted for, not only the
named files. Each entry ``{"page_url", "key_prefix", "select", "pattern", "og_image"?, "rewrite"?}`` reads the page once
and collects the image URLs inside the CSS selection ``select`` (``img`` ``src``/``data-src``/``data-original``/
``data-lazy-src``/``srcset`` and ``a`` ``href``), plus the page's ``og:image`` when ``og_image`` is true. Each URL is
normalized to the uploaded file (query and fragment dropped, then the ordered ``rewrite`` regex substitutions, e.g. a
WordPress ``-1024x576`` size suffix or a resizing-proxy host) and kept when it fully matches ``pattern``; its key is
``key_prefix`` plus the pattern's ``key`` group (default: the file name without extension). A named seed item with that
key or that normalized URL stands for it; every other image becomes a candidate of its own, classified by the seed rules
(unclassified images stay ``pending``). The list ``article-images`` is complete only when every configured page was read
and its selection found; a page whose selection is missing (layout change) keeps the list open.

Titles (``title`` of a seed item or rule first): an image on a read page is titled with the article's headline (``h1``,
else ``og:title``, else ``<title>``), its caption when the page gives one (``figcaption``, ``.wp-caption-text``,
``.pic-desc``, a non-generic ``img`` ``title``/``alt``), and its position among the page's article images ``(n/N)``.
The file name stays in ``original_filename``.
"""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from lib import checks, net

from .. import model
from . import base

HOSTS: list[str] = []  # per seed
ARTICLE_LIST = "article-images"
IMAGE_ATTRS = ("src", "data-src", "data-original", "data-lazy-src")
GENERIC_ALT = {"", "image", "img", "photo", "picture", "圖片", "图片"}


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
        if not isinstance(page, dict) or set(page) - {"page_url", "key_prefix", "select", "pattern", "og_image",
                                                       "rewrite"}:
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


def normalized(url: str, rewrite: list[list[str]]) -> str:
    """``url`` without query and fragment, then the seed's ``rewrite`` substitutions applied in order."""
    parts = urlsplit(url)
    plain = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    for pattern, replacement in rewrite:
        plain = re.sub(pattern, replacement, plain)
    return plain


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


def article_images(html: str, page_url: str, config: Mapping[str, Any]) -> tuple[list[dict], bool]:
    """([{key, url, caption}] in page order, whether the selection was found) for one configured article page."""
    document = base.soup(html)
    pattern = re.compile(config["pattern"])
    rewrite = config.get("rewrite", [])
    found: dict[str, dict] = {}

    title = base.headline(document)

    def add(raw: str | None, caption: str = "") -> None:
        if not raw or not raw.strip():
            return
        url = normalized(base.absolute(page_url, raw), rewrite)
        match = pattern.fullmatch(url)
        if not match:
            return
        stem = base.last_segment(url).rsplit(".", 1)[0]
        key = config["key_prefix"] + (match.groupdict().get("key") or stem)
        entry = found.setdefault(key, {"key": key, "url": url, "caption": ""})
        # A caption that only repeats the headline or the file name says nothing about the image.
        if caption and not (title and caption.startswith(title)) and not stem.lower().startswith(caption.lower()):
            entry["caption"] = entry["caption"] or caption

    if config.get("og_image"):  # every og:image the page lists (some CMSs list all of the post's images there)
        for tag in document.find_all("meta", attrs={"property": "og:image"}):
            add(tag.get("content"))
    roots = document.select(config["select"])
    for root in roots:
        for element in root.find_all(["img", "source", "a"]):
            caption = _caption(element) if element.name == "img" else ""
            if element.name == "a":
                add(element.get("href"))
                continue
            for name in IMAGE_ATTRS:
                add(element.get(name), caption)
            for candidate in str(element.get("srcset") or "").split(","):
                add(candidate.strip().split(" ")[0], caption)
    return list(found.values()), bool(roots)


def _title(item_title: str | None, page_headline: str, caption: str, position: tuple[int, int] | None,
           fallback: str) -> str:
    if item_title:
        return item_title
    if not page_headline:
        return fallback
    text = f"{page_headline} — {caption}" if caption and caption not in page_headline else page_headline
    if position is None:  # a named file the article body does not show: its file name tells the items apart
        suffix = f" ({fallback.rsplit('.', 1)[0]})"
    else:
        suffix = f" ({position[0]}/{position[1]})" if position[1] > 1 else ""
    return model.clip(text, 140 - len(suffix)) + suffix


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    configs = {page["page_url"]: page for page in (seed.get("params") or {}).get("article_images", [])}
    discovery = base.new_discovery(seed, lists=["seed-files", *([ARTICLE_LIST] if configs else [])],
                                   scope_notes=("Explicitly named files, and every article-body image of the "
                                                "configured article pages; nothing else is followed." if configs else
                                                "Explicitly named files only; the page's gallery is not crawled."))
    pages: dict[str, dict] = {}
    read_complete: set[str] = set()
    discovery.coverage[ARTICLE_LIST] = base.listed_on(read_complete)

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
        images, selected = article_images(page["html"], page_url, config)
        if not selected:
            discovery.errors.append(model.discovery_error("discover", page_url, f"article selection "
                                                          f"{config['select']!r} not found on the page"))
            discovery.continuation[ARTICLE_LIST] = discovery.continuation.get(ARTICLE_LIST) or "selection-not-found"
            continue
        enumerated[page_url] = images
        read_complete.add(page_url)

    def position(page_url: str, key: str, url: str | None) -> tuple[tuple[int, int] | None, str]:
        images = enumerated.get(page_url, [])
        for index, image in enumerate(images, start=1):
            if image["key"] == key or (url and image["url"] == url):
                return (index, len(images)), image["caption"]
        return None, ""

    claimed: set[tuple[str, str]] = set()
    for item in seed["items"]:
        page_url, file_url, key = item["page_url"], item["file_url"], item["key"]
        page = read(page_url) if item.get("read_page") else {"texts": {}, "headline": ""}
        name = base.last_segment(file_url)
        place, caption = position(page_url, key, file_url)
        if place is not None:
            claimed.add((page_url, enumerated[page_url][place[0] - 1]["key"]))
        discovery.candidates.append(_draft(ctx, seed, item, key=key, page_url=page_url, file_url=file_url,
                                           texts=page["texts"],
                                           title=_title(item.get("title"), page["headline"], caption, place, name)))
    for page_url, images in enumerated.items():
        page = pages[page_url]
        for index, image in enumerate(images, start=1):
            if (page_url, image["key"]) in claimed:
                continue
            name = base.last_segment(image["url"])
            item = {"key": image["key"], "page_url": page_url, "file_url": image["url"]}
            discovery.candidates.append(_draft(
                ctx, seed, item, key=image["key"], page_url=page_url, file_url=image["url"], texts=page["texts"],
                title=_title(None, page["headline"], image["caption"], (index, len(images)), name)))
    return discovery


def _draft(ctx: Any, seed: Mapping[str, Any], item: Mapping[str, Any], *, key: str, page_url: str, file_url: str,
           texts: Mapping[str, str], title: str) -> model.Draft:
    name = base.last_segment(file_url)
    ext = name.rsplit(".", 1)[-1] if "." in name else None
    draft = model.apply_rule(
        model.find_rule(base.rules(seed), key), key=key, page_url=page_url, texts=texts,
        file_url=base.stored(file_url), default_title=title, default_quality=ctx.quality,
        default_rights=ctx.rights, original_filename=item.get("original_filename") or name,
        base_evidence=[page_url], source_reported=base.reported(url=file_url, mime=base.mime_for(ext)))
    if item.get("notes") and "notes" not in draft.candidate_extra:
        draft.candidate_extra["notes"] = item["notes"]
    return draft
