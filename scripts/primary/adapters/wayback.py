"""Internet Archive Wayback Machine captures listed through the CDX API and fetched raw with ``id_``.

Each seed item is one CDX query (``url`` + ``match_type`` + filters). Pages of ``limit`` rows are followed with
``showResumeKey`` until the API returns no resume key; an interrupted listing keeps its key in ``continuation`` and the
discovery stays incomplete. Captures are grouped by (URL key, content digest): the earliest capture of each distinct
payload is one candidate ``<host/path>@<timestamp>``. ``web.archive.org/web/<ts>id_/<original>`` returns the archived
bytes without replay rewriting; the replay page is the ``page_url``.

Provenance: ``source_reported`` holds the original URL, the archived MIME type, the capture time and the CDX digest
(base32 SHA-1 of the payload, converted to hex) so ``sha1_match`` shows whether the bytes received are the archived
payload. CDX ``length`` is the compressed WARC record size, not the file size, so it is noted but not compared.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Mapping
from urllib.parse import urlsplit

from lib import net

from .. import model
from . import base

HOSTS = ["web.archive.org"]
CDX = "https://web.archive.org/cdx/search/cdx"
FIELDS = "urlkey,timestamp,original,mimetype,statuscode,digest,length"
MATCH_TYPES = ("exact", "prefix", "host", "domain")


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    for index, item in enumerate(base.require_items(seed, where, ["name", "url"])):
        if item.get("match_type", "exact") not in MATCH_TYPES:
            raise ValueError(f"{where}: items[{index}].match_type must be one of {MATCH_TYPES}")


def digest_hex(digest: str | None) -> str | None:
    """CDX ``digest`` (base32 SHA-1) as 40 lowercase hex digits, or None when it is not one."""
    if not isinstance(digest, str) or len(digest) != 32:
        return None
    try:
        raw = base64.b32decode(digest.upper())
    except (binascii.Error, ValueError):
        return None
    return raw.hex() if len(raw) == 20 else None


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


def cdx_rows(ctx: Any, item: Mapping[str, Any], resume: str | None) -> tuple[list[dict], str | None]:
    """One CDX page as dicts and the resume key for the next page (None at the end)."""
    params: dict[str, Any] = {"url": item["url"], "matchType": item.get("match_type", "exact"), "output": "json",
                              "fl": FIELDS, "showResumeKey": "true", "limit": str(item.get("page_size", 500))}
    if item.get("filters"):
        params["filter"] = list(item["filters"])
    if resume:
        params["resumeKey"] = resume
    data, _ = ctx.client.get_json(CDX, params)
    if not isinstance(data, list):
        raise ValueError("CDX response is not a JSON list")
    if not data:
        return [], None
    header, rows, next_key = data[0], [], None
    for row in data[1:]:
        if isinstance(row, list) and len(row) == len(header):
            rows.append(dict(zip(header, row)))
        elif isinstance(row, list) and len(row) == 1 and isinstance(row[0], str):
            next_key = row[0]  # after an empty separator row
    return rows, next_key


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
    discovery = base.new_discovery(seed, lists=[item["name"] for item in items], api=CDX, evidence_level="live_api",
                                   scope_notes="Explicit CDX queries; one candidate per distinct archived payload "
                                               "(earliest capture). Pages linking elsewhere are not followed.")
    for item in items:
        rows: list[dict] = []
        resume: str | None = None
        try:
            while True:
                page, resume = cdx_rows(ctx, item, resume)
                rows.extend(page)
                if not resume:
                    break
        except (net.FetchError, ValueError) as error:
            discovery.errors.append(model.discovery_error("discover", CDX, f"{item['name']}: CDX listing "
                                                                           f"interrupted: {error}"))
            discovery.continuation[item["name"]] = resume or "not-started"
            if isinstance(error, net.BlockedError):
                discovery.evidence_level = "blocked"
        for row in unique_captures(rows):
            original, stamp = row.get("original", ""), row.get("timestamp", "")
            if not base.stored(original) or iso_timestamp(stamp) is None:
                discovery.errors.append(model.discovery_error("discover", None,
                                                              f"{item['name']}: unusable CDX row skipped"))
                continue
            key = capture_key(original, stamp)
            rule = model.find_rule(base.rules(seed), key)
            texts = {"original": original}
            note = (f"Wayback capture {stamp} of {original} (CDX mimetype {row.get('mimetype')}, status "
                    f"{row.get('statuscode')}, WARC record length {row.get('length')}).")
            draft = model.apply_rule(
                rule, key=key, page_url=f"https://web.archive.org/web/{stamp}/{original}", texts=texts,
                file_url=f"https://web.archive.org/web/{stamp}id_/{original}",
                default_title=f"{base.last_segment(original)} (archived {stamp[:8]})", default_quality=ctx.quality,
                default_rights=ctx.rights, original_filename=base.last_segment(original),
                base_evidence=[f"capture of {original}"],
                source_reported=base.reported(url=original, mime=row.get("mimetype") or None,
                                              sha1=digest_hex(row.get("digest")), timestamp=iso_timestamp(stamp)))
            if draft.action == "download":
                draft.meta["upstream_revision"] = stamp
                draft.meta["notes"] = " ".join(filter(None, [draft.meta.get("notes"), note]))
            draft.candidate_extra.setdefault("notes", note)
            discovery.candidates.append(draft)
    return discovery
