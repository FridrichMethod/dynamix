"""pixiv artworks through the public ``ajax/illust`` JSON that pixiv's own logged-out artwork page loads.

``www.pixiv.net/ajax/illust/<id>`` gives the title, caption, author and first-page ``urls.original``;
``/ajax/illust/<id>/pages`` lists every page of a multi-page work with its native size. No login, cookie or search is
used, and only explicit artwork IDs from the seed are read. Original files live on ``i.pximg.net``, which refuses
requests that do not carry a pixiv ``Referer`` (hotlink protection, HTTP 403). The project does not forge that header:
the download is attempted once with the ordinary client, recorded as ``blocked`` (manual access), and the host is not
contacted again in the run. Works whose file URLs pixiv withholds from logged-out readers (all ``urls`` null) are
``blocked`` without any file request. Public Internet Archive captures of the same originals are collected by
``archives:wayback``.

Each page is its own candidate ``<artwork id>:p<index>`` (0-based like pixiv's ``_p0``), classified by its own seed rule,
so a multi-page post is never mapped page-by-page from its title alone.
"""

from __future__ import annotations

import html
import re
from typing import Any, Mapping

from lib import net

from .. import model
from . import base

HOSTS = ["www.pixiv.net", "i.pximg.net"]
API = "https://www.pixiv.net/ajax/illust/"
_ORIGINAL = re.compile(r"^https://i\.pximg\.net/img-original/img/[0-9/]+/(\d+)_p(\d+)\.(png|jpg|jpeg|gif)$")
_TAGS = re.compile(r"<[^>]+>")
NO_FILE = "pending: no downloadable file URL was found for this item"


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    for index, item in enumerate(base.require_items(seed, where, ["illust_id"])):
        if not item["illust_id"].isdigit():
            raise ValueError(f"{where}: items[{index}].illust_id must be digits")
    if not str((seed.get("params") or {}).get("user_id") or "").isdigit():
        raise ValueError(f"{where}: params.user_id (the pixiv account's numeric ID) is required")


def caption_text(value: Any) -> str:
    """pixiv caption HTML as plain text (``<br />`` -> space, entities decoded)."""
    if not isinstance(value, str):
        return ""
    return " ".join(html.unescape(_TAGS.sub(" ", value)).split())


def body(data: Any, url: str) -> Any:
    if not isinstance(data, dict) or data.get("error") or "body" not in data:
        message = data.get("message") if isinstance(data, dict) else type(data).__name__
        raise net.FetchError(f"pixiv API error: {model.clip(str(message or 'no body'), 120)}", url=url)
    return data["body"]


def texts_of(info: Mapping[str, Any]) -> dict[str, str]:
    tags = info.get("tags") or {}
    tag_names = [entry.get("tag", "") for entry in tags.get("tags") or [] if isinstance(entry, dict)]
    return {"title": str(info.get("illustTitle") or info.get("title") or ""),
            "caption": caption_text(info.get("illustComment") or info.get("description")),
            "tags": " ".join(name for name in tag_names if name),
            "user": f"{info.get('userName') or ''} {info.get('userId') or ''}".strip()}


def pages_of(info: Mapping[str, Any], pages: Any) -> list[dict]:
    """[{index, url, width, height}] from the pages listing, or the single page of ``info``."""
    found = []
    entries = pages if isinstance(pages, list) and pages else [
        {"urls": info.get("urls") or {}, "width": info.get("width"), "height": info.get("height")}]
    for position, entry in enumerate(entries):
        url = ((entry or {}).get("urls") or {}).get("original") if isinstance(entry, dict) else None
        match = _ORIGINAL.match(url or "")
        index = int(match.group(2)) if match else position
        found.append({"index": index, "url": url if match else None, "width": entry.get("width"),
                      "height": entry.get("height")})
    return found


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    user_id = str(seed["params"]["user_id"])
    discovery = base.new_discovery(seed, lists=["seed-artworks"], api=API, evidence_level="live_api",
                                   scope_notes="Explicit seed artwork IDs via pixiv's public ajax/illust JSON (no "
                                               "login, no search, no profile crawl).")
    for item in seed["items"]:
        illust = item["illust_id"]
        page_url = f"https://www.pixiv.net/artworks/{illust}"
        title = item.get("title") or f"pixiv artwork {illust}"
        try:
            info = body(ctx.client.get_json(f"{API}{illust}")[0], page_url)
            pages = body(ctx.client.get_json(f"{API}{illust}/pages")[0], page_url) if int(
                info.get("pageCount") or 1) > 1 else None
        except net.FetchError as error:
            draft = model.error_draft(f"{illust}:p0", page_url, title, error, phase="discover")
            if item.get("notes"):
                draft.candidate_extra = {"notes": item["notes"]}
            discovery.candidates.append(draft)
            base.mark_unread(discovery, "seed-artworks", error)
            continue
        texts = texts_of(info)
        withheld = not any((info.get("urls") or {}).values())
        owner_ok = str(info.get("userId") or "") == user_id
        posted = info.get("uploadDate") or info.get("createDate")
        for page in pages_of(info, pages):
            key = f"{illust}:p{page['index']}"
            rule = model.find_rule(base.rules(seed), key)
            if not owner_ok:
                rule = model.hold(rule, f"pending: artwork owner {info.get('userId')} is not this source's "
                                        f"account {user_id}")
            name = base.last_segment(page["url"]) if page["url"] else f"{illust}_p{page['index']}"
            ext = name.rsplit(".", 1)[-1] if "." in name else None
            draft = model.apply_rule(
                rule, key=key, page_url=page_url, texts=texts, file_url=page["url"],
                default_title=f"{texts['title'] or title} (p{page['index']})", default_quality=ctx.quality,
                default_rights=ctx.rights, original_filename=name, base_evidence=[page_url],
                source_reported=base.reported(url=page["url"], mime=base.mime_for(ext), width=page["width"],
                                              height=page["height"], timestamp=posted))
            if withheld and draft.action == "pending" and not page["url"] and draft.reason == NO_FILE:
                draft.action = "blocked"
                draft.reason = ("blocked: pixiv withholds this work's file URLs from logged-out readers (login "
                                "required); not attempted")
            if item.get("notes") and "notes" not in draft.candidate_extra:
                draft.candidate_extra["notes"] = item["notes"]
            discovery.candidates.append(draft)
    return discovery
