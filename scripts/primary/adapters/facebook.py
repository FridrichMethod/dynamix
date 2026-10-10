"""Public Facebook page posts and photos: the Open Graph image of explicit permalinks, read without login.

Each seed item names a permalink (``{"key", "url"}``) on the seed's page (``params.page``, e.g. ``c4cat.dynamix``). One
ordinary GET reads the page Facebook serves to a logged-out reader; its ``og:title``/``og:description`` are the
evidence texts and ``og:image`` (a signed ``*.fbcdn.net`` URL, resized by Facebook per its ``stp``/``ctp`` parameters)
is the file. The ``og:url`` must stay on the seed's page, otherwise the item is held as ``pending``. A permalink that
serves no Open Graph image to a logged-out reader (login wall or an empty shell) is recorded ``blocked`` without
further requests; a refused or challenged request is ``blocked`` as well (lib.net). Nothing is enumerated: the page
timeline is not served to logged-out non-browser clients, so it stays an open list with its reason. Signed image URLs
expire; the URL actually fetched is stored as the record's download URL.
"""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from lib import checks, net

from .. import model
from . import base

HOSTS = ["www.facebook.com", ".fbcdn.net"]
OG_QUALITY = ("Facebook's Open Graph rendition of the uploaded image (re-encoded and resized by Facebook per the URL's "
              "stp/ctp parameters); a platform transform, not the uploaded original.")
_CTP = re.compile(r"[?&]ctp=([a-z]?\d+x\d+)")
_CSTP = re.compile(r"[?&]cstp=([a-z]*\d+x\d+)")


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    page = (seed.get("params") or {}).get("page")
    if not isinstance(page, str) or not re.fullmatch(r"[A-Za-z0-9.]+", page):
        raise ValueError(f"{where}: params.page (the page's path name, e.g. c4cat.dynamix) is required")
    keys = []
    for index, item in enumerate(base.require_items(seed, where, ["key", "url"])):
        problem = checks.http_url(item["url"])
        parts = urlsplit(item["url"])
        if problem or parts.hostname != "www.facebook.com" or not parts.path.startswith(f"/{page}/"):
            raise ValueError(f"{where}: items[{index}].url must be a www.facebook.com/{page}/ permalink")
        if re.search(r"\s", item["key"]):
            raise ValueError(f"{where}: items[{index}].key must not contain whitespace")
        keys.append(item["key"])
    if len(set(keys)) != len(keys):
        raise ValueError(f"{where}: duplicate item keys")


def open_graph(html: str) -> tuple[dict[str, str], str]:
    """(``og:*`` properties, document title) of a page; entities are decoded by the parser."""
    document = base.soup(html)
    values: dict[str, str] = {}
    for tag in document.find_all("meta", attrs={"property": re.compile(r"^og:")}):
        content = tag.get("content")
        if isinstance(content, str) and content.strip():
            values.setdefault(tag["property"], content.strip())
    title = document.title.get_text(" ", strip=True) if document.title else ""
    return values, title


def on_page(url: str | None, page: str) -> bool:
    if not url:
        return False
    parts = urlsplit(url)
    return parts.hostname in ("www.facebook.com", "m.facebook.com", "facebook.com") and (
        parts.path.rstrip("/") == f"/{page}" or parts.path.startswith(f"/{page}/"))


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    page = seed["params"]["page"]
    discovery = base.new_discovery(seed, lists=["seed-permalinks"], evidence_level="page_read",
                                   scope_notes="Explicit permalinks only, read logged out; the Open Graph image of each "
                                               "is the file. The page timeline is not enumerated.")
    for item in seed["items"]:
        key, url = item["key"], item["url"]
        title = item.get("title") or f"Facebook {page} {key}"
        try:
            html, final_url, _ = ctx.client.get_text(url)
        except net.FetchError as error:
            draft = model.error_draft(key, url, title, error, phase="discover")
            if item.get("notes"):
                draft.candidate_extra = {"notes": item["notes"]}
            discovery.candidates.append(draft)
            base.mark_unread(discovery, "seed-permalinks", error)
            continue
        og, document_title = open_graph(html)
        texts = {"og:title": og.get("og:title", ""), "og:description": og.get("og:description", ""),
                 "title": document_title}
        image = og.get("og:image")
        rule = model.find_rule(base.rules(seed), key)
        if not on_page(og.get("og:url") or final_url, page):
            rule = model.hold(rule, f"pending: the permalink resolves to {og.get('og:url') or final_url}, not the "
                                    f"{page} page")
        curated = rule is not None and rule.get("action", "download") in ("exclude", "pending")
        if (not image or checks.url_problem(image)) and curated:  # e.g. a text-only post recorded as excluded
            draft = model.apply_rule(rule, key=key, page_url=url, texts=texts, file_url=None,
                                     default_title=item.get("title") or title, default_quality=ctx.quality,
                                     default_rights=ctx.rights, base_evidence=[url])
            if item.get("notes"):
                draft.candidate_extra["notes"] = item["notes"]
            discovery.candidates.append(draft)
            continue
        if not image or checks.url_problem(image):
            draft = model.Draft(upstream_key=key, page_url=url, title=title, action="blocked", game="unknown",
                                game_evidence=[url], reason=(
                                    "blocked: the permalink serves no Open Graph image to a logged-out reader (login "
                                    "wall or empty page shell); manual access required, not retried"))
            draft.candidate_extra = {name: list(rule[name]) for name in ("song_titles", "illustrators", "composers",
                                                                         "collections") if rule and rule.get(name)}
            if item.get("notes"):
                draft.candidate_extra["notes"] = item["notes"]
            discovery.candidates.append(draft)
            continue
        name = base.last_segment(image)
        ext = name.rsplit(".", 1)[-1] if "." in name else None
        draft = model.apply_rule(
            rule, key=key, page_url=url, texts=texts, file_url=image,
            default_title=item.get("title") or model.clip(og.get("og:title") or document_title or title, 120),
            default_quality=ctx.quality, default_rights=ctx.rights, original_filename=name, base_evidence=[url],
            source_reported=base.reported(url=image, mime=base.mime_for(ext),
                                          width=_int(og.get("og:image:width")), height=_int(og.get("og:image:height"))))
        sizes = ", ".join(filter(None, [f"display size {m.group(1)}" for m in [_CTP.search(image)] if m]
                                 + [f"source bound {m.group(1)}" for m in [_CSTP.search(image)] if m]))
        note = (f"Open Graph image of {url} as served logged out on {ctx.now()[:10]}"
                + (f" (Facebook rendition parameters: {sizes})" if sizes else "") + ".")
        if draft.action == "download":
            draft.meta["transformed"] = True
            draft.meta["quality"]["notes"] = " ".join(filter(None, [draft.meta["quality"].get("notes"), OG_QUALITY]))
            draft.meta["notes"] = " ".join(filter(None, [draft.meta.get("notes"), note]))
        draft.candidate_extra["notes"] = " ".join(filter(None, [item.get("notes"),
                                                                draft.candidate_extra.get("notes"), note]))
        discovery.candidates.append(draft)
    return discovery


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, str) and value.isdigit() else None
