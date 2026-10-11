"""pixiv artworks through the public ``ajax/illust`` JSON that pixiv's own logged-out artwork page loads.

``www.pixiv.net/ajax/illust/<id>`` gives the title, caption, author and first-page ``urls.original``;
``/ajax/illust/<id>/pages`` lists every page of a multi-page work with its native size. No login, cookie or search is
used, and only explicit artwork IDs from the seed are read. Original files live on ``i.pximg.net``, which refuses
requests that do not carry a pixiv ``Referer`` (hotlink protection, HTTP 403). The project does not forge that header:
the download is attempted once with the ordinary client, recorded as ``blocked`` (manual access), and the host is not
contacted again in the run. Works whose file URLs pixiv withholds from logged-out readers (all ``urls`` null) are
``blocked`` without any file request.

Internet Archive captures. A seed item may name, per page, a raw Wayback capture of pixiv's original file
(``"wayback": [{"page": 0, "timestamp": "<14 digits>", "original": "https://i.pximg.net/img-original/..."}]``; the
ArchiveTeam pixiv crawl of 2022-12 holds many). For such a page the candidate keeps the artist's post as its
``page_url`` and downloads ``web.archive.org/web/<timestamp>id_/<original>`` (raw bytes, no replay rewriting) instead of
contacting ``i.pximg.net``. The capture is checked first with one exact CDX query: a capture that the CDX API does not
list with HTTP status 200 at that timestamp is held as ``pending``; the CDX digest (base32 SHA-1 of the archived
payload) becomes ``source_reported.sha1``. The download must come from exactly that capture and, when the digest is
known, be that payload (``wayback.capture_check``): a redirect to another capture (the Internet Archive serves the
nearest one when a timestamp is missing, e.g. after a failed CDX check) or other bytes are held as ``pending``, never
archived under the seeded capture time. The original URL must belong to the same work and page, and must equal the URL pixiv itself lists for the page
when pixiv lists one (a re-upload moves the URL; the old capture is then held for review). ``source_reported`` keeps
pixiv's original URL, advertised page size and post date; the capture time is the record's ``upstream_revision`` and is
stated in the notes. The same captures may also be listed by ``archives:wayback``; identical bytes merge in the catalog.

Each page is its own candidate ``<artwork id>:p<index>`` (0-based like pixiv's ``_p0``), classified by its own seed rule,
so a multi-page post is never mapped page-by-page from its title alone.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from typing import Any

from lib import net

from .. import model
from . import base
from .wayback import capture_check, digest_hex, iso_timestamp

HOSTS = ["www.pixiv.net", "i.pximg.net", "web.archive.org"]
API = "https://www.pixiv.net/ajax/illust/"
CDX = "https://web.archive.org/cdx/search/cdx"
CDX_FIELDS = "timestamp,original,mimetype,statuscode,digest,length"
_ORIGINAL = re.compile(r"^https://i\.pximg\.net/img-original/img/[0-9/]+/(\d+)_p(\d+)\.(png|jpg|jpeg|gif)$")
_TAGS = re.compile(r"<[^>]+>")
NO_FILE = model.NO_FILE_REASON
WAYBACK_LIST = "wayback-captures"
WAYBACK_QUALITY = (
    "pixiv original upload (i.pximg.net img-original) as archived by the Internet Archive (raw id_ "
    "capture); bytes as pixiv served them at capture time. No request was sent to i.pximg.net."
)


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    for index, item in enumerate(base.require_items(seed, where, ["illust_id"])):
        if not item["illust_id"].isdigit():
            raise ValueError(f"{where}: items[{index}].illust_id must be digits")
        captures = item.get("wayback", [])
        if not isinstance(captures, list):
            raise ValueError(f"{where}: items[{index}].wayback must be a list")
        pages = set()
        for position, capture in enumerate(captures):
            label = f"{where}: items[{index}].wayback[{position}]"
            if not isinstance(capture, dict) or set(capture) - {"page", "timestamp", "original"}:
                raise ValueError(f"{label} must be an object with page, timestamp and original")
            page = capture.get("page")
            if not isinstance(page, int) or isinstance(page, bool) or page < 0 or page in pages:
                raise ValueError(f"{label}.page must be a distinct non-negative integer")
            pages.add(page)
            if iso_timestamp(str(capture.get("timestamp", ""))) is None:
                raise ValueError(f"{label}.timestamp must be a 14-digit Wayback timestamp")
            match = _ORIGINAL.match(str(capture.get("original", "")))
            if not match or match.group(1) != item["illust_id"] or int(match.group(2)) != page:
                raise ValueError(f"{label}.original must be the i.pximg.net original of page {page} of this work")
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
    return {
        "title": str(info.get("illustTitle") or info.get("title") or ""),
        "caption": caption_text(info.get("illustComment") or info.get("description")),
        "tags": " ".join(name for name in tag_names if name),
        "user": f"{info.get('userName') or ''} {info.get('userId') or ''}".strip(),
    }


def pages_of(info: Mapping[str, Any], pages: Any) -> list[dict]:
    """[{index, url, width, height}] from the pages listing, or the single page of ``info``."""
    found = []
    entries = (
        pages
        if isinstance(pages, list) and pages
        else [{"urls": info.get("urls") or {}, "width": info.get("width"), "height": info.get("height")}]
    )
    for position, entry in enumerate(entries):
        url = ((entry or {}).get("urls") or {}).get("original") if isinstance(entry, dict) else None
        match = _ORIGINAL.match(url or "")
        index = int(match.group(2)) if match else position
        found.append(
            {"index": index, "url": url if match else None, "width": entry.get("width"), "height": entry.get("height")}
        )
    return found


def capture_url(capture: Mapping[str, Any]) -> str:
    return f"https://web.archive.org/web/{capture['timestamp']}id_/{capture['original']}"


def cdx_capture(ctx: Any, capture: Mapping[str, Any]) -> dict | None:
    """The CDX row of exactly this capture with HTTP status 200, or None when the CDX API lists no such capture."""
    stamp = str(capture["timestamp"])
    data, _ = ctx.client.get_json(
        CDX,
        {
            "url": capture["original"],
            "matchType": "exact",
            "output": "json",
            "fl": CDX_FIELDS,
            "from": stamp,
            "to": stamp,
        },
    )
    if not isinstance(data, list):
        raise ValueError("CDX response is not a JSON list")
    if not data:
        return None
    header = data[0]
    for row in data[1:]:
        if isinstance(row, list) and len(row) == len(header):
            entry = dict(zip(header, row, strict=True))  # equal lengths: checked on the line above
            if entry.get("timestamp") == stamp and entry.get("statuscode") == "200":
                return entry
    return None


def wayback_page(
    ctx: Any, discovery: model.Discovery, capture: Mapping[str, Any], listed_url: str | None
) -> tuple[str | None, str | None, dict | None]:
    """(file URL, hold reason, CDX row) for a page archived by the Internet Archive."""
    original = capture["original"]
    if listed_url and listed_url != original:
        return (
            None,
            (
                f"unresolved_role: pixiv now lists {listed_url} for this page, not the archived original "
                f"{original} (re-upload?); the capture needs review"
            ),
            None,
        )
    try:
        row = cdx_capture(ctx, capture)
    except (net.FetchError, ValueError) as error:
        # The capture itself is still fetched (its bytes are checked on download); the open list makes the next run
        # repeat the CDX check.
        discovery.errors.append(
            model.discovery_error("discover", CDX, f"CDX check of {original} at {capture['timestamp']} failed: {error}")
        )
        base.mark_unread(discovery, WAYBACK_LIST, error)
        return capture_url(capture), None, None
    if row is None:
        return (
            None,
            (f"unresolved_role: the Wayback CDX API lists no HTTP 200 capture of {original} at {capture['timestamp']}"),
            None,
        )
    return capture_url(capture), None, row


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    user_id = str(seed["params"]["user_id"])
    archived = any(item.get("wayback") for item in seed["items"])
    discovery = base.new_discovery(
        seed,
        lists=["seed-artworks", *([WAYBACK_LIST] if archived else [])],
        api=API,
        evidence_level="live_api",
        scope_notes="Explicit seed artwork IDs via pixiv's public ajax/illust JSON (no "
        "login, no search, no profile crawl)"
        + (
            "; originals of the listed pages from their Internet Archive raw captures (one exact CDX check each)."
            if archived
            else "."
        ),
    )
    for item in seed["items"]:
        illust = item["illust_id"]
        page_url = f"https://www.pixiv.net/artworks/{illust}"
        title = item.get("title") or f"pixiv artwork {illust}"
        try:
            info = body(ctx.client.get_json(f"{API}{illust}")[0], page_url)
            pages = (
                body(ctx.client.get_json(f"{API}{illust}/pages")[0], page_url)
                if int(info.get("pageCount") or 1) > 1
                else None
            )
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
        captures = {capture["page"]: capture for capture in item.get("wayback", [])}
        listing = pages_of(info, pages)
        listed = {page["index"] for page in listing}
        for index in sorted(set(captures) - listed):  # withheld or single-page listings omit archived pages
            listing.append({"index": index, "url": None, "width": None, "height": None})
        for page in sorted(listing, key=lambda entry: entry["index"]):
            key = f"{illust}:p{page['index']}"
            rule = model.find_rule(base.rules(seed), key)
            if not owner_ok:
                rule = model.hold(
                    rule, f"unresolved_game: artwork owner {info.get('userId')} is not this source's account {user_id}"
                )
            capture = captures.get(page["index"])
            file_url, row = page["url"], None
            if capture is not None:
                file_url, hold, row = wayback_page(ctx, discovery, capture, page["url"])
                if hold:
                    rule = model.hold(rule, hold)
            original = capture["original"] if capture is not None else page["url"]
            name = base.last_segment(original) if original else f"{illust}_p{page['index']}"
            ext = name.rsplit(".", 1)[-1] if "." in name else None
            draft = model.apply_rule(
                rule,
                key=key,
                page_url=page_url,
                texts=texts,
                file_url=file_url,
                default_title=f"{texts['title'] or title} (p{page['index']})",
                default_quality=ctx.quality,
                default_rights=ctx.rights,
                original_filename=name,
                base_evidence=[page_url],
                source_reported=base.reported(
                    url=original,
                    mime=base.mime_for(ext),
                    width=page["width"],
                    height=page["height"],
                    timestamp=posted,
                    sha1=digest_hex((row or {}).get("digest")),
                ),
            )
            if withheld and draft.action == "pending" and not file_url and draft.reason == NO_FILE:
                draft.action = "blocked"
                draft.reason = (
                    "blocked: pixiv withholds this work's file URLs from logged-out readers (login "
                    "required); not attempted"
                )
            if capture is not None and draft.action == "download":
                stamp = str(capture["timestamp"])
                draft.check_download = capture_check(stamp, digest_hex((row or {}).get("digest")))
                draft.meta["upstream_revision"] = stamp
                draft.meta["quality"]["notes"] = WAYBACK_QUALITY
                cdx = (
                    f"Wayback CDX: status 200, digest {row['digest']}, WARC record length {row.get('length')}"
                    if row
                    else "Wayback CDX check not completed this run"
                )
                draft.meta["notes"] = " ".join(
                    filter(
                        None,
                        [
                            draft.meta.get("notes"),
                            (
                                f"Bytes from the Internet Archive capture of {capture['original']} made {iso_timestamp(stamp)} "
                                f"({cdx}); the post page is the artist's own pixiv work {illust}. pixiv's advertised page size "
                                "and post date are kept in source_reported."
                            ),
                        ],
                    )
                )
            if item.get("notes") and "notes" not in draft.candidate_extra:
                draft.candidate_extra["notes"] = item["notes"]
            discovery.candidates.append(draft)
    return discovery
