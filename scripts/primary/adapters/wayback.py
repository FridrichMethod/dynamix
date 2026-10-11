"""Internet Archive Wayback Machine captures listed through the CDX API and fetched raw with ``id_``.

Each seed item is one CDX query (``url`` + ``match_type`` + filters). Pages of ``limit`` rows are followed with
``showResumeKey`` until the API returns no resume key; an interrupted listing keeps its key in ``continuation`` and the
discovery stays incomplete. Paging is bounded: a resume key returned twice or more than ``max_pages`` pages (default
200) stop the listing with the pending key kept open. Captures are grouped by (URL key, content digest): the earliest
capture of each distinct payload is one candidate ``<host/path>@<timestamp>``. A query that lists no capture although the
previous snapshot holds captures it matched is not a complete listing: its list stays open (``empty-answer``) with a
discovery error, so no archived capture changes status on an empty response (07 §Integrity). The state persists over
further empty answers (the excluded or pending captures are no longer in the snapshot by then) until a listing is read.
``web.archive.org/web/<ts>id_/<original>`` returns the archived bytes without replay rewriting; the replay page is the
``page_url``.

Provenance: ``source_reported`` holds the original URL, the archived MIME type, the capture time and the CDX digest
(base32 SHA-1 of the payload, converted to hex). CDX ``length`` is the compressed WARC record size, not the file size,
so it is noted but not compared. A download is archived only when it is that capture's payload
(:func:`capture_check`): a response redirected to another timestamp, or bytes whose SHA-1 is not the CDX digest, are
held as ``pending`` instead of being recorded under the capture's time.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from lib import checks, net

from .. import model
from . import base

HOSTS = ["web.archive.org"]
CDX = "https://web.archive.org/cdx/search/cdx"
FIELDS = "urlkey,timestamp,original,mimetype,statuscode,digest,length"
MATCH_TYPES = ("exact", "prefix", "host", "domain")
MAX_PAGES = 200  # per CDX query; a seed item may lower or raise it with ``max_pages``


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    for index, item in enumerate(base.require_items(seed, where, ["name", "url"])):
        if item.get("match_type", "exact") not in MATCH_TYPES:
            raise ValueError(f"{where}: items[{index}].match_type must be one of {MATCH_TYPES}")
        pages = item.get("max_pages", MAX_PAGES)
        if not isinstance(pages, int) or isinstance(pages, bool) or pages < 1:
            raise ValueError(f"{where}: items[{index}].max_pages must be a positive integer")


def digest_hex(digest: str | None) -> str | None:
    """CDX ``digest`` (base32 SHA-1) as 40 lowercase hex digits, or None when it is not one."""
    if not isinstance(digest, str) or len(digest) != 32:
        return None
    try:
        raw = base64.b32decode(digest.upper())
    except binascii.Error, ValueError:
        return None
    return raw.hex() if len(raw) == 20 else None


_RAW_CAPTURE = re.compile(r"^https?://web\.archive\.org/web/(\d{14})id_/")


def capture_check(stamp: str, sha1_hex: str | None) -> Callable[[Any], str | None]:
    """``Draft.check_download`` for a raw ``id_`` capture request: the response must come from that very capture (the
    Internet Archive redirects a request for a timestamp it does not hold to the nearest other capture) and, when the
    CDX API listed a digest, its bytes must be that payload. Otherwise the reason the download is held."""

    def check(result: Any) -> str | None:
        final = getattr(result, "final_url", None) or ""
        match = _RAW_CAPTURE.match(final)
        if not match or match.group(1) != stamp:
            served = f"capture {match.group(1)}" if match else f"{checks.redact_url(final)!r}"
            return (
                f"awaiting_download: the Internet Archive answered the raw request for capture {stamp} with "
                f"{served}; not the seeded capture, so nothing was archived"
            )
        if sha1_hex and getattr(result, "sha1", None) != sha1_hex:
            return (
                f"awaiting_download: the bytes received for capture {stamp} (SHA-1 "
                f"{getattr(result, 'sha1', None)}) are not the payload the CDX API lists for it (digest SHA-1 "
                f"{sha1_hex}); held for review"
            )
        return None

    return check


def iso_timestamp(stamp: str) -> str | None:
    if len(stamp) != 14 or not stamp.isdigit():
        return None
    return f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[8:10]}:{stamp[10:12]}:{stamp[12:14]}Z"


def capture_key(original: str, timestamp: str) -> str:
    """``<host><path>[?query]@<timestamp>`` with the scheme and a default port removed."""
    parts = urlsplit(original)
    host = (parts.hostname or "").lower()
    port = parts.port
    if port and not (parts.scheme == "http" and port == 80 or parts.scheme == "https" and port == 443):
        host = f"{host}:{port}"
    path = parts.path or "/"
    return f"{host}{path}{'?' + parts.query if parts.query else ''}@{timestamp}".replace(" ", "%20")


def cdx_rows(ctx: Any, item: Mapping[str, Any], resume: str | None) -> tuple[list[dict], str | None, int]:
    """One CDX page as dicts, the resume key for the next page (None at the end) and the number of rows of unexpected
    shape (neither header-sized nor the empty separator and one-string resume-key rows that end a page), which the
    caller reports. A first row that is not a header of field names with ``timestamp`` and ``original`` raises
    ValueError, so the listing counts as interrupted instead of as an empty complete page."""
    params: dict[str, Any] = {
        "url": item["url"],
        "matchType": item.get("match_type", "exact"),
        "output": "json",
        "fl": FIELDS,
        "showResumeKey": "true",
        "limit": str(item.get("page_size", 500)),
    }
    if item.get("filters"):
        params["filter"] = list(item["filters"])
    if resume:
        params["resumeKey"] = resume
    data, _ = ctx.client.get_json(CDX, params)
    if not isinstance(data, list):
        raise ValueError("CDX response is not a JSON list")
    if not data:
        return [], None, 0
    header = data[0]
    if (
        not isinstance(header, list)
        or not all(isinstance(name, str) for name in header)
        or not {"timestamp", "original"} <= set(header)
    ):
        raise ValueError(f"CDX response has no header row of field names: {model.clip(repr(header), 80)}")
    rows, next_key, malformed = [], None, 0
    for row in data[1:]:
        if isinstance(row, list) and len(row) == len(header):
            rows.append(dict(zip(header, row, strict=True)))  # equal lengths: checked on the line above
        elif isinstance(row, list) and len(row) == 1 and isinstance(row[0], str):
            next_key = row[0]  # after an empty separator row
        elif row != []:  # the empty separator before the resume key is expected
            malformed += 1
    return rows, next_key, malformed


def _cdx_form(url: str) -> tuple[str, str]:
    """``(host, path?query)`` of an original or seed URL the way the CDX index compares them: no scheme, lower case,
    no leading ``www.`` and no default port."""
    text = url if "://" in url else f"http://{url}"
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return "", ""
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    if port and port not in (80, 443):
        host = f"{host}:{port}"
    return host, ((parts.path or "/") + (f"?{parts.query}" if parts.query else "")).casefold()


def matches_item(item: Mapping[str, Any], original: str) -> bool:
    """Whether the CDX query ``item`` (url + match_type) covers the capture of ``original``."""
    want_host, want_rest = _cdx_form(item["url"])
    host, rest = _cdx_form(original)
    if not host:
        return False
    match_type = item.get("match_type", "exact")
    if match_type == "domain":
        return host == want_host or host.endswith(f".{want_host}")
    if host != want_host:
        return False
    return match_type == "host" or (rest.startswith(want_rest) if match_type == "prefix" else rest == want_rest)


def previously_listed(previous: Mapping[str, Any] | None, item: Mapping[str, Any]) -> int:
    """How many candidates of the previous snapshot are captures the query ``item`` covers."""
    count = 0
    for candidate in (previous or {}).get("candidates", []):
        original = (candidate.get("source_reported") or {}).get("url")
        if isinstance(original, str) and matches_item(item, original):
            count += 1
    return count


def unique_captures(rows: list[dict]) -> list[dict]:
    """Earliest capture of every (urlkey, digest), ordered by key."""
    best: dict[tuple[str, str], dict] = {}
    for row in rows:
        group = (row.get("urlkey", ""), row.get("digest", ""))
        if group not in best or row.get("timestamp", "") < best[group].get("timestamp", ""):
            best[group] = row
    return sorted(best.values(), key=lambda row: (row.get("urlkey", ""), row.get("timestamp", "")))


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    items = seed["items"]
    discovery = base.new_discovery(
        seed,
        lists=[item["name"] for item in items],
        api=CDX,
        evidence_level="live_api",
        scope_notes="Explicit CDX queries; one candidate per distinct archived payload "
        "(earliest capture). Pages linking elsewhere are not followed.",
    )
    for item in items:
        rows: list[dict] = []
        malformed = 0  # rows of unexpected shape: listed, but no capture can be read from them
        resume: str | None = None  # the key of the page being requested; kept open when paging stops early
        seen: set[str] = set()
        limit = item.get("max_pages", MAX_PAGES)
        try:
            for _ in range(limit):
                page, next_key, bad = cdx_rows(ctx, item, resume)
                rows.extend(page)
                malformed += bad
                if not next_key:
                    resume = None
                    break
                if next_key in seen:
                    resume = next_key
                    raise ValueError(f"CDX returned resume key {model.clip(next_key, 40)!r} again; paging stopped")
                seen.add(next_key)
                resume = next_key
            else:
                raise ValueError(f"paging stopped after {limit} pages (max_pages); the listing continues")
        except (net.FetchError, ValueError) as error:
            discovery.errors.append(
                model.discovery_error("discover", CDX, f"{item['name']}: CDX listing interrupted: {error}")
            )
            discovery.continuation[item["name"]] = resume or "not-started"
            if isinstance(error, net.BlockedError):
                discovery.evidence_level = "blocked"
        else:
            previous = getattr(ctx, "previous", None)
            listed_before = previously_listed(previous, item) if not rows else 0
            # Captures without archived bytes (excluded, pending, failed) are not carried into the snapshot of a run
            # whose listing was empty, so an earlier empty answer keeps the list open on its own: it stays open until
            # a listing is read again (07 §Integrity: never infer completeness from an empty response).
            empty_before = not rows and ((previous or {}).get("continuation") or {}).get(item["name"]) == "empty-answer"
            if listed_before or empty_before:
                held = (
                    f"the previous snapshot holds {listed_before} capture(s) of this query"
                    if listed_before
                    else "the previous run got an empty answer too, after this query had listed captures"
                )
                discovery.errors.append(
                    model.discovery_error(
                        "discover",
                        CDX,
                        f"{item['name']}: the CDX API listed no capture, although {held}; an empty answer is not "
                        "taken as the complete listing",
                    )
                )
                discovery.continuation[item["name"]] = "empty-answer"
        skipped = malformed
        if malformed:
            discovery.errors.append(
                model.discovery_error(
                    "discover", CDX, f"{item['name']}: {malformed} CDX row(s) of unexpected shape skipped"
                )
            )
        for row in unique_captures(rows):
            original, stamp = row.get("original", ""), row.get("timestamp", "")
            if not base.stored(original) or iso_timestamp(stamp) is None:
                skipped += 1
                discovery.errors.append(
                    model.discovery_error(
                        "discover",
                        None,
                        f"{item['name']}: unusable CDX row skipped (original "
                        f"{model.clip(repr(original), 80)}, timestamp {model.clip(repr(stamp), 20)})",
                    )
                )
                continue
            key = capture_key(original, stamp)
            rule = model.find_rule(base.rules(seed), key)
            texts = {"original": original}
            note = (
                f"Wayback capture {stamp} of {original} (CDX mimetype {row.get('mimetype')}, status "
                f"{row.get('statuscode')}, WARC record length {row.get('length')})."
            )
            draft = model.apply_rule(
                rule,
                key=key,
                page_url=f"https://web.archive.org/web/{stamp}/{original}",
                texts=texts,
                file_url=f"https://web.archive.org/web/{stamp}id_/{original}",
                default_title=f"{base.last_segment(original)} (archived {stamp[:8]})",
                default_quality=ctx.quality,
                default_rights=ctx.rights,
                original_filename=base.last_segment(original),
                base_evidence=[f"capture of {original}"],
                source_reported=base.reported(
                    url=original,
                    mime=row.get("mimetype") or None,
                    sha1=digest_hex(row.get("digest")),
                    timestamp=iso_timestamp(stamp),
                ),
            )
            if draft.action == "download":
                draft.meta["upstream_revision"] = stamp
                draft.meta["notes"] = " ".join(filter(None, [draft.meta.get("notes"), note]))
                draft.check_download = capture_check(stamp, digest_hex(row.get("digest")))
            draft.candidate_extra.setdefault("notes", note)
            discovery.candidates.append(draft)
        if skipped and discovery.continuation.get(item["name"]) is None:
            # A listed capture without a candidate: the enumeration is not fully accounted for.
            discovery.continuation[item["name"]] = f"rows-skipped-{skipped}"
    return discovery
