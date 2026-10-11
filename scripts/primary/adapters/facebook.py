"""Public Facebook page posts and photos: the Open Graph image of explicit permalinks, read without login.

Each seed item names a permalink (``{"key", "url"}``) on the seed's page (``params.page``, e.g. ``c4cat.dynamix``). One
ordinary GET reads the page Facebook serves to a logged-out reader; its ``og:title``/``og:description`` are the
evidence texts and ``og:image`` (a signed ``*.fbcdn.net`` URL, resized by Facebook per its ``stp``/``ctp`` parameters)
is the file. The ``og:url`` must stay on the seed's page, otherwise the item is held as ``pending``. A permalink that
serves no Open Graph image to a logged-out reader (login wall or an empty shell) is recorded ``blocked`` without
further requests; a refused or challenged request is ``blocked`` as well (lib.net). Nothing is enumerated: the page
timeline is not served to logged-out non-browser clients, so it stays an open list with its reason.

Signed image URLs rotate on every read (``oh``, ``oe``, ``_nc_gid``, ``_nc_ohc`` ...): the file's identity is the
``*.fbcdn.net`` path plus the rendition parameters ``stp``/``cstp``/``ctp`` (:func:`file_identity`), so ``--resume``
reuses an archived file whose URL differs only in its signature, and a record keeps the URL, redirect target and time
of the fetch that archived its bytes. ``source_reported.mime`` follows the rendition the URL requests (``stp=dst-jpg``
→ JPEG); the upload's own format (the file name's extension) is named in the notes when it differs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

from lib import checks, net

from .. import model
from . import base

HOSTS = ["www.facebook.com", ".fbcdn.net"]
OG_QUALITY = (
    "Facebook's Open Graph rendition of the uploaded image (re-encoded and resized by Facebook per the URL's "
    "stp/ctp parameters); a platform transform, not the uploaded original."
)
_CTP = re.compile(r"[?&]ctp=([a-z]?\d+x\d+)")
_CSTP = re.compile(r"[?&]cstp=([a-z]*\d+x\d+)")
RENDITION_PARAMS = ("stp", "cstp", "ctp")
_DST = re.compile(r"(?:^|_)dst-(jpg|png|webp)(?:_|$)")
_DST_MIME = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def file_identity(url: str | None) -> tuple | None:
    """(path, rendition parameters) of a ``*.fbcdn.net`` URL — what stays when Facebook re-signs it — or None."""
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return None
    if not (parts.hostname or "").endswith(".fbcdn.net"):
        return None
    query = parse_qs(parts.query)
    return parts.path, tuple((query.get(name) or [None])[0] for name in RENDITION_PARAMS)


def rendition_mime(url: str) -> str | None:
    """MIME type of the rendition the URL requests (``stp=dst-jpg_...``), or None when it requests none."""
    stp = (parse_qs(urlsplit(url).query).get("stp") or [""])[0]
    match = _DST.search(stp)
    return _DST_MIME[match.group(1)] if match else None


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


_REACTIONS = re.compile(r"^[\d.,KM]+\s+reactions?(?:\s*·\s*[\d.,KM]+\s+(?:shares?|comments?))*\s*\|\s*", re.I)


def on_page(url: str | None, page: str, item_url: str) -> bool:
    """Whether the resolved page is the seed page's own content: a URL under ``/<page>/``, or the ``/reel/<id>/`` that
    the page's own ``/videos/<id>`` permalink redirects to (same numeric ID)."""
    if not url:
        return False
    parts = urlsplit(url)
    if parts.hostname not in ("www.facebook.com", "m.facebook.com", "facebook.com"):
        return False
    if parts.path.rstrip("/") == f"/{page}" or parts.path.startswith(f"/{page}/"):
        return True
    reel = re.fullmatch(r"/reel/(\d+)/?", parts.path)
    video = re.search(r"/videos/(?:[^/]+/)?(\d+)/?$", urlsplit(item_url).path)
    return bool(reel and video and reel.group(1) == video.group(1))


def login_wall(url: str | None) -> bool:
    return bool(url) and urlsplit(url).path.rstrip("/") in ("/login", "/login.php")


def readable_title(document_title: str, og_title: str | None) -> str:
    """The page's own title text without Facebook's reaction counters ('33 reactions | ...')."""
    title = _REACTIONS.sub("", " ".join((document_title or "").split()))
    return title if title and title != "Facebook" else (og_title or "")


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    page = seed["params"]["page"]
    discovery = base.new_discovery(
        seed,
        lists=["seed-permalinks"],
        evidence_level="page_read",
        scope_notes="Explicit permalinks only, read logged out; the Open Graph image of each "
        "is the file. The page timeline is not enumerated.",
    )
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
        texts = {
            "og:title": og.get("og:title", ""),
            "og:description": og.get("og:description", ""),
            "title": document_title,
        }
        image = og.get("og:image")
        rule = model.find_rule(base.rules(seed), key)
        if login_wall(final_url) or login_wall(og.get("og:url")):
            draft = model.Draft(
                upstream_key=key,
                page_url=url,
                title=title,
                action="blocked",
                game="unknown",
                game_evidence=[url],
                reason=(
                    "blocked: the permalink redirects a logged-out reader to "
                    "the login page; manual access required, not retried"
                ),
            )
            draft.candidate_extra = {
                name: list(rule[name])
                for name in ("song_titles", "illustrators", "composers", "collections")
                if rule and rule.get(name)
            }
            if item.get("notes"):
                draft.candidate_extra["notes"] = item["notes"]
            discovery.candidates.append(draft)
            continue
        if not on_page(og.get("og:url") or final_url, page, url):
            rule = model.hold(
                rule, f"unresolved_game: the permalink resolves to {og.get('og:url') or final_url}, not the {page} page"
            )
        curated = rule is not None and rule.get("action", "download") in ("exclude", "pending")
        if (not image or checks.url_problem(image)) and curated:  # e.g. a text-only post recorded as excluded
            draft = model.apply_rule(
                rule,
                key=key,
                page_url=url,
                texts=texts,
                file_url=None,
                default_title=item.get("title") or title,
                default_quality=ctx.quality,
                default_rights=ctx.rights,
                base_evidence=[url],
            )
            if item.get("notes"):
                draft.candidate_extra["notes"] = item["notes"]
            discovery.candidates.append(draft)
            continue
        if not image or checks.url_problem(image):
            draft = model.Draft(
                upstream_key=key,
                page_url=url,
                title=title,
                action="blocked",
                game="unknown",
                game_evidence=[url],
                reason=(
                    "blocked: the permalink serves no Open Graph image to a logged-out reader (login "
                    "wall or empty page shell); manual access required, not retried"
                ),
            )
            draft.candidate_extra = {
                name: list(rule[name])
                for name in ("song_titles", "illustrators", "composers", "collections")
                if rule and rule.get(name)
            }
            if item.get("notes"):
                draft.candidate_extra["notes"] = item["notes"]
            discovery.candidates.append(draft)
            continue
        name = base.last_segment(image)
        ext = name.rsplit(".", 1)[-1] if "." in name else None
        requested = rendition_mime(image)
        draft = model.apply_rule(
            rule,
            key=key,
            page_url=url,
            texts=texts,
            file_url=image,
            default_title=item.get("title")
            or model.clip(readable_title(document_title, og.get("og:title")) or title, 120),
            default_quality=ctx.quality,
            default_rights=ctx.rights,
            original_filename=name,
            base_evidence=[url],
            source_reported=base.reported(
                url=image,
                mime=requested or base.mime_for(ext),
                width=_int(og.get("og:image:width")),
                height=_int(og.get("og:image:height")),
            ),
        )
        identity = file_identity(image)
        if identity is not None:
            draft.same_file = lambda archived, identity=identity: file_identity(archived) == identity
        sizes = ", ".join(
            filter(
                None,
                [f"display size {m.group(1)}" for m in [_CTP.search(image)] if m]
                + [f"source bound {m.group(1)}" for m in [_CSTP.search(image)] if m],
            )
        )
        # No date here: a refresh that reuses the archive keeps fetched_at, which dates the archived fetch.
        note = (
            f"Open Graph image of {url} as served to a logged-out reader"
            + (f" (Facebook rendition parameters: {sizes})" if sizes else "")
            + "."
        )
        uploaded = base.mime_for(ext)
        if requested and uploaded and uploaded != requested:
            note += (
                f" The upload's file name ends in .{ext} (its uploaded format); the URL's stp parameter requests "
                f"a {requested.split('/')[1].upper()} rendition, which is what source_reported.mime records."
            )
        if draft.action == "download":
            draft.meta["transformed"] = True
            draft.meta["quality"]["notes"] = " ".join(filter(None, [draft.meta["quality"].get("notes"), OG_QUALITY]))
            draft.meta["notes"] = " ".join(filter(None, [draft.meta.get("notes"), note]))
        # Item and rule notes; the generated note only when there are none (asset notes already carry it once).
        notes = " ".join(filter(None, [item.get("notes"), draft.candidate_extra.get("notes")]))
        draft.candidate_extra["notes"] = notes or note
        discovery.candidates.append(draft)
    return discovery


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, str) and value.isdigit() else None
