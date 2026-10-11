"""Executable form of the frozen data contracts, schema version 1.

docs/implementation/09-frozen-contracts.md is the prose specification and this module its executable form; if the two
disagree, report it to the coordinator rather than picking one silently. Everything here is pure standard-library code.

Validators raise ``ValueError`` whose message starts with the record kind and ID and names the field, e.g.
``asset 'wikis:miraheze:0123456789abcdef': mapping_status: must be one of ...``. Records are never modified.
Unknown fields are rejected so that a typo (``illustrator`` for ``illustrators``) cannot pass silently.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from . import checks, paths
from .checks import (CREDENTIAL_QUERY_KEYS, DATE_RE, REDACTED, SENSITIVE_QUERY_KEYS,  # noqa: F401
                     TIMESTAMP_RE, contains_credentials, redact_text, redact_url)

Checker = checks.Checker
_nonempty, _text, _boolean, _tristate = checks.nonempty, checks.text, checks.boolean, checks.tristate
_integer, _nullable, _one_of, _const = checks.integer, checks.nullable, checks.one_of, checks.const
_pattern = checks.pattern
_timestamp, _date, _http_url, _clean_text = checks.timestamp, checks.date, checks.http_url, checks.clean_text
_message_text = checks.message_text
_list_of, _object = checks.list_of, checks.obj

SCHEMA_VERSION = 1
GAME = "dynamix"
CATALOG_GAME = "Dynamix"
OWNER = "FridrichMethod"
REPOSITORY = "https://github.com/FridrichMethod/dynamix"
ARCHIVE_URL_PREFIX = REPOSITORY + "/raw/main/"
USER_AGENT = "DynamixArtworkArchive/1.0 (+https://github.com/FridrichMethod/dynamix)"
DEFAULT_RIGHTS_HOLDER = "C4Cat Entertainment Limited and/or the credited artist"
PAGES_BUDGET_BYTES = 950_000_000
MAX_FILE_BYTES = 100 * 1024 * 1024
THUMB_DIR = "assets/thumbs"

# --- Enumerations (order is meaningful where noted) -------------------------------------------------------------------

FAMILIES = paths.FAMILIES  # ("wikis", "artists", "official", "archives")
FAMILY_PRIORITY = ("artists", "official", "wikis", "archives")  # catalog primary-record choice, first wins
# Within a family these sources rank first, in this order (Miraheze is the current wiki and its stored bytes are the
# bytes both English wikis serve; Fandom is the legacy copy); every other source follows them, tied.
PREFERRED_SOURCES = ("wikis:miraheze", "wikis:fandom")
REGISTERED_SOURCE_IDS = (
    "wikis:miraheze", "wikis:fandom", "wikis:atwiki-ja", "wikis:rhythmgame-ja",
    "official:apple-store", "official:google-play", "official:taptap", "official:website", "official:c4cat",
    "official:facebook", "official:x", "archives:hkstp", "archives:4gamer", "archives:wayback",
)
KINDS = ("song_art", "pack_cover", "character", "background", "part", "promotional",
         "store_screenshot", "gameplay_screenshot", "process", "reference")  # catalog ordering
SITE_KINDS = ("song_art", "pack_cover", "character", "background", "part", "promotional", "process")
ARCHIVE_KINDS = ("reference", "gameplay_screenshot", "store_screenshot")  # tier revision 1.1
SLIDESHOW_KINDS = ("song_art", "pack_cover", "promotional", "character")
SLIDESHOW_MIN_LONG_EDGE = 1000
TIERS = ("site", "archive")
GAMES = ("dynamix", "dynamix_universe", "mixed", "unknown", "not_dynamix")  # candidate game association
SOURCE_STATUSES = ("available", "partially_available", "temporarily_unavailable", "manual_access", "metadata_only",
                   "excluded")
EVIDENCE_LEVELS = ("live_api", "page_read", "search_index", "blocked", "not_established")
DISPOSITIONS = ("downloaded", "duplicate_bytes", "excluded", "failed", "blocked", "pending")
# Prefixes of a ``pending`` candidate's reason (09, CLI notes): accepted but not archived yet, or its role or game
# not established.
PENDING_REASONS = ("awaiting_download", "unresolved_role", "unresolved_game")
EXCLUSION_REASONS = ("universe_only", "not_dynamix", "animated_capture", "person_photo", "site_ui", "game_ui",
                     "video_placeholder", "external_release_art", "fanart", "upscaled", "rejected_submission",
                     "redirect_or_alias", "out_of_scope_other")
MAPPING_STATUSES = ("confirmed_primary", "confirmed_wiki", "inferred", "unmapped", "conflict")
MAPPED_STATUSES = ("confirmed_primary", "confirmed_wiki", "inferred", "conflict")  # need mapping_evidence
SOURCE_VARIANTS = ("site_original", "preview", "unknown")
ORIGIN_STATUSES = ("artist_post", "official", "wiki_copy", "repost", "scan", "unknown")
MASTER_STATUSES = ("confirmed", "unverified")
UPSTREAM_STATUSES = ("current", "removed", "superseded", "unknown")
FAILURE_PHASES = ("discover", "download", "decode", "verify")
ERROR_CLASSES = ("http_4xx", "http_5xx", "timeout", "blocked", "challenge", "decode_error", "size_limit",
                 "host_not_allowed", "other")
NON_RETRIABLE_ERROR_CLASSES = ("blocked", "challenge", "host_not_allowed")
CREDIT_ROLES = ("illustrator", "composer", "other")
FORMATS = ("PNG", "JPEG", "WEBP", "GIF", "PDF")
RASTER_FORMATS = ("PNG", "JPEG", "WEBP", "GIF")
FORMAT_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif",
               "PDF": "application/pdf"}
FORMAT_EXT = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif", "PDF": "pdf"}
MIME_ALIASES = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg", "image/x-png": "image/png",
                "application/x-pdf": "application/pdf"}

# --- Identifier patterns ----------------------------------------------------------------------------------------------

_SLUG_PART = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_PLATFORM = r"[a-z0-9]+"  # no hyphen: <platform>-<handle> must split back unambiguously (artists:x:foo-bar)
_HANDLE = r"[a-z0-9_](?:[a-z0-9_.-]*[a-z0-9_])?"
_SOURCE_ID_PATTERN = rf"(?:(?:wikis|official|archives):{_SLUG_PART}|artists:{_PLATFORM}:{_HANDLE})"
SOURCE_ID_RE = re.compile(rf"^{_SOURCE_ID_PATTERN}$")
ASSET_ID_RE = re.compile(rf"^{_SOURCE_ID_PATTERN}:[0-9a-f]{{16}}$")
SONG_ID_RE = re.compile(r"^song:[a-z0-9._-]+$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# --- Small helpers shared by fetchers and builders --------------------------------------------------------------------

def source_family(source_id: str) -> str:
    return paths.source_family(source_id)


def asset_id(source_id: str, sha256: str) -> str:
    """``<source_id>:<sha256[:16]>``."""
    return f"{source_id}:{sha256[:16]}"


def candidate_id(source_id: str, upstream_key: str) -> str:
    """``<source_id>:<upstream key>`` (wiki key: canonical ``File:`` title with spaces as ``_``)."""
    return f"{source_id}:{upstream_key}"


def failure_id(candidate_id_value: str, phase: str) -> str:
    return f"{candidate_id_value}:{phase}"


def check_source_slugs(source_ids: Iterable[str]) -> None:
    """ValueError when two source IDs share a source slug, i.e. a discovery file (``data/discovery`` is one flat
    directory for every family) or, within a family, an asset directory. Call it with every known source ID (e.g.
    ``REGISTERED_SOURCE_IDS`` plus the IDs a fetcher registers); the catalog validator checks all sources."""
    collisions = paths.source_slug_collisions(source_ids)
    if collisions:
        group = collisions[0]
        raise ValueError(f"source IDs {group} share the source slug {paths.source_slug(group[0])!r} "
                         f"({paths.discovery_relpath(group[0])})")


def normalize_mime(mime: str | None) -> str | None:
    if mime is None:
        return None
    base = mime.split(";", 1)[0].strip().lower()
    return MIME_ALIASES.get(base, base)


def tier_for(kind: str, fmt: str) -> str:
    """Storage tier: ``archive`` for reference/gameplay screenshots and any PDF, otherwise ``site``."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind: {kind!r}")
    if fmt not in FORMATS:
        raise ValueError(f"unknown format: {fmt!r}")
    return "archive" if kind in ARCHIVE_KINDS or fmt == "PDF" else "site"


def is_gallery(asset: Mapping[str, Any]) -> bool:
    """Raster image whose kind is not ``reference``."""
    return asset["format"] in RASTER_FORMATS and asset["kind"] != "reference"


def is_slideshow(asset: Mapping[str, Any]) -> bool:
    """Gallery image of a slideshow kind with a decoded long edge of at least 1000 px."""
    return (is_gallery(asset) and asset["kind"] in SLIDESHOW_KINDS
            and max(asset["width"], asset["height"]) >= SLIDESHOW_MIN_LONG_EDGE)


def normalize_title(title: str) -> str:
    """Sort/search form of a title: NFKC, casefolded, whitespace collapsed. Never displayed or stored."""
    return " ".join(unicodedata.normalize("NFKC", title).casefold().split())


def catalog_sort_key(asset: Mapping[str, Any]) -> tuple:
    """Catalog asset order: kind (KINDS order), normalized title, ID."""
    return KINDS.index(asset["kind"]), normalize_title(asset["title"]), asset["id"]


def provenance_sort_key(record: Mapping[str, Any]) -> tuple:
    """Primary-record order for one SHA-256 (09 "Catalog presentation rules", Primary record); the minimum is the
    primary: family priority (FAMILY_PRIORITY); PREFERRED_SOURCES within the family (wikis:miraheze, then wikis:fandom,
    then every other source, tied); ``quality.source_variant`` (SOURCE_VARIANTS order: site_original < preview <
    unknown); source ID; asset ID (``id`` or ``asset_id``). Total and deterministic, since an asset ID is unique."""
    family = record.get("family") or source_family(record["source_id"])
    source_id = record["source_id"]
    preferred = PREFERRED_SOURCES.index(source_id) if source_id in PREFERRED_SOURCES else len(PREFERRED_SOURCES)
    return (FAMILY_PRIORITY.index(family), preferred, SOURCE_VARIANTS.index(record["quality"]["source_variant"]),
            source_id, record.get("id") or record.get("asset_id") or "")


def ordered_union(lists: Iterable[Iterable[str]]) -> list[str]:
    """First-seen order union (catalog merge of illustrators/composers/song titles across provenance)."""
    seen: dict[str, None] = {}
    for values in lists:
        for value in values:
            seen.setdefault(value, None)
    return list(seen)


def catalog_url(path: str) -> str:
    return quote(path, safe="/")


def thumb_relpath(sha256: str) -> str:
    return f"{THUMB_DIR}/{sha256[:16]}.webp"


def make_source_reported(**values: Any) -> dict:
    """A ``source_reported`` object; omitted keys are null (not advertised)."""
    unknown = sorted(set(values) - set(SOURCE_REPORTED_FIELDS))
    if unknown:
        raise ValueError(f"source_reported: unknown keys {unknown}")
    return {key: values.get(key) for key in SOURCE_REPORTED_FIELDS}


def make_download_observed(info: Mapping[str, Any], content_type: str | None) -> dict:
    """``download_observed`` from a :func:`lib.media.inspect` result and the response Content-Type."""
    return {"content_type": content_type, "sniffed_format": info["format"], "bytes": info["bytes"],
            "width": info["width"], "height": info["height"], "sha1": info["sha1"], "sha256": info["sha256"]}


def compute_comparisons(source_reported: Mapping[str, Any], observed: Mapping[str, Any]) -> dict:
    """Tri-state comparisons of advertised versus measured values; null when either side is unknown.

    ``format_match`` compares the advertised MIME type (normalized) with the MIME of the sniffed format.
    """
    reported_sha1 = source_reported.get("sha1")
    reported_bytes = source_reported.get("bytes")
    reported_size = (source_reported.get("width"), source_reported.get("height"))
    observed_size = (observed.get("width"), observed.get("height"))
    reported_mime = normalize_mime(source_reported.get("mime"))
    return {
        "sha1_match": None if reported_sha1 is None else reported_sha1.lower() == observed["sha1"],
        "bytes_match": None if reported_bytes is None else reported_bytes == observed["bytes"],
        "dimensions_match": (None if None in reported_size or None in observed_size
                             else reported_size == observed_size),
        "format_match": None if reported_mime is None else reported_mime == FORMAT_MIME[observed["sniffed_format"]],
    }


def default_rights(*, creator: str | None = None, url: str | None = None, restrictions: str | None = None,
                   holder: str | None = DEFAULT_RIGHTS_HOLDER, license: str | None = None) -> dict:
    """Rights object; ``license`` stays null unless a license is actually established."""
    return {"holder": holder, "creator": creator, "url": url, "license": license, "restrictions": restrictions}


def failure_record(*, source_id: str, candidate_id: str, phase: str, url: str, attempted_at: str, attempts: int,
                   error_class: str, http_status: int | None, retriable: bool, manual: bool, message: str) -> dict:
    """A validated Failure record. The URL is redacted with the stored-URL rule (credential parameters only), the
    message with the strict log rule."""
    record = {"id": failure_id(candidate_id, phase), "source_id": source_id, "candidate_id": candidate_id,
              "phase": phase, "url": redact_url(url, strict=False), "attempted_at": attempted_at, "attempts": attempts,
              "error_class": error_class, "http_status": http_status, "retriable": retriable, "manual": manual,
              "message": redact_text(message) or error_class}
    validate_failure(record)
    return record


def canonical(record: Mapping[str, Any], kind: str) -> dict:
    """A copy of ``record`` with keys in contract order (nested objects too); unknown keys are kept at the end.

    ``kind`` is one of ``source``, ``asset``, ``catalog_asset``, ``failure``, ``candidate``.
    """
    orders = {
        "source": SOURCE_FIELDS + SOURCE_OPTIONAL_FIELDS,
        "asset": ASSET_FIELDS + ASSET_OPTIONAL_FIELDS,
        "catalog_asset": ASSET_FIELDS + ASSET_OPTIONAL_FIELDS + CATALOG_ASSET_FIELDS,
        "failure": FAILURE_FIELDS,
        "candidate": CANDIDATE_FIELDS + CANDIDATE_OPTIONAL_FIELDS,
    }
    nested = {"quality": QUALITY_FIELDS, "rights": RIGHTS_FIELDS, "source_reported": SOURCE_REPORTED_FIELDS,
              "download_observed": DOWNLOAD_OBSERVED_FIELDS, "comparisons": COMPARISON_FIELDS}
    order = orders[kind]
    result = {key: record[key] for key in order if key in record}
    result.update((key, value) for key, value in record.items() if key not in result)
    for key, fields in nested.items():
        if isinstance(result.get(key), dict):
            value = result[key]
            result[key] = {**{field: value[field] for field in fields if field in value}, **value}
    return result


def _evidence_item(value: Any) -> str | None:
    """A mapping-evidence entry: a non-empty string (URL or quote) or an object of scalar strings/numbers."""
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, dict) and value:
        for key, item in value.items():
            if not isinstance(key, str) or not (item is None or isinstance(item, (str, int)) and not isinstance(
                    item, bool)):
                return "object values must be strings, integers or null"
            if isinstance(item, str) and contains_credentials(item, strict=False):
                return "must not contain credentials"
        return None
    return "must be a non-empty string or object"


def _revision(value: Any) -> str | None:
    if value is None or isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool)):
        return None
    return "must be a string, integer or null"


_STRINGS = _list_of(_nonempty)
_SOURCE_ID = _pattern(SOURCE_ID_RE, "a source ID <family>:<slug> (artists:<platform>:<handle>)")
_ASSET_ID = _pattern(ASSET_ID_RE, "an asset ID <source_id>:<sha256[:16]>")
_SONG_ID = _pattern(SONG_ID_RE, "a song ID song:<slug>")
_URL = _http_url
_NULLABLE_URL = _nullable(_http_url)
_NULLABLE_TEXT = _nullable(_nonempty)
_ANY_JSON: Checker = lambda value: None  # noqa: E731 - free-form descriptive values

QUALITY_SPEC = {"source_variant": _one_of(SOURCE_VARIANTS), "origin_status": _one_of(ORIGIN_STATUSES),
                "master_status": _one_of(MASTER_STATUSES), "notes": _text}
RIGHTS_SPEC = {"holder": _NULLABLE_TEXT, "creator": _NULLABLE_TEXT, "url": _NULLABLE_URL, "license": _NULLABLE_TEXT,
               "restrictions": _NULLABLE_TEXT}
SOURCE_REPORTED_SPEC = {"url": _NULLABLE_URL, "mime": _NULLABLE_TEXT, "bytes": _nullable(_integer(0)),
                        "width": _nullable(_integer(0)), "height": _nullable(_integer(0)),
                        "sha1": _nullable(_pattern(_HEX40, "40 lowercase hex digits")), "timestamp": _NULLABLE_TEXT,
                        "revision": _revision}
DOWNLOAD_OBSERVED_SPEC = {"content_type": _nullable(_text), "sniffed_format": _one_of(FORMATS), "bytes": _integer(1),
                          "width": _nullable(_integer(1)), "height": _nullable(_integer(1)),
                          "sha1": _pattern(_HEX40, "40 lowercase hex digits"),
                          "sha256": _pattern(_HEX64, "64 lowercase hex digits")}
COMPARISONS_SPEC = {key: _tristate for key in ("sha1_match", "bytes_match", "dimensions_match", "format_match")}
CREDIT_EVIDENCE_SPEC = {"role": _one_of(CREDIT_ROLES), "name": _nonempty, "url": _NULLABLE_URL,
                        "field": _nullable(_text), "quote": _nullable(_text)}

SOURCE_SPEC = {"id": _SOURCE_ID, "name": _nonempty, "family": _one_of(FAMILIES), "url": _URL, "platform": _nonempty,
               "status": _one_of(SOURCE_STATUSES), "checked_at": _timestamp, "notes": _text}
SOURCE_OPTIONAL_SPEC = {"api_url": _NULLABLE_URL, "owner_evidence": _ANY_JSON, "crawl_scope": _ANY_JSON,
                        "rate_policy": _nullable(_text), "discovery_complete": _boolean,
                        "candidate_count": _integer(0), "asset_count": _integer(0), "failure_count": _integer(0),
                        "evidence_level": _one_of(EVIDENCE_LEVELS)}
ASSET_SPEC = {
    "id": _ASSET_ID, "source_id": _SOURCE_ID, "candidate_id": _nonempty, "title": _nonempty, "kind": _one_of(KINDS),
    "game": _const(GAME), "page_url": _URL, "download_url": _URL, "resolved_url": _URL, "path": _nonempty,
    "original_filename": _nonempty, "bytes": _integer(1, MAX_FILE_BYTES),
    "sha256": _pattern(_HEX64, "64 lowercase hex digits"), "sha1": _pattern(_HEX40, "40 lowercase hex digits"),
    "format": _one_of(FORMATS), "mime": _nonempty, "width": _nullable(_integer(1)), "height": _nullable(_integer(1)),
    "fetched_at": _timestamp, "illustrators": _STRINGS, "composers": _STRINGS, "song_titles": _STRINGS,
    "song_ids": _list_of(_SONG_ID), "collections": _STRINGS, "mapping_status": _one_of(MAPPING_STATUSES),
    "mapping_evidence": _list_of(_evidence_item), "quality": _object(QUALITY_SPEC), "rights": _object(RIGHTS_SPEC),
    "source_reported": _object(SOURCE_REPORTED_SPEC), "download_observed": _object(DOWNLOAD_OBSERVED_SPEC),
    "comparisons": _object(COMPARISONS_SPEC),
}
ASSET_OPTIONAL_SPEC = {
    "credit_evidence": _list_of(_object(CREDIT_EVIDENCE_SPEC)), "variant_group": _nonempty,
    "variant_role": _nonempty, "upstream_status": _one_of(UPSTREAM_STATUSES), "superseded_by": _ASSET_ID,
    "game_associations": _list_of(_pattern(re.compile(r"^[a-z0-9_]+$"), "a lowercase game key")),
    "notes": _text, "upstream_revision": _revision, "transformed": _boolean,
}
PROVENANCE_SPEC = {
    "source_id": _SOURCE_ID, "source_name": _nonempty, "family": _one_of(FAMILIES), "page_url": _URL,
    "download_url": _URL, "resolved_url": _URL, "quality": _object(QUALITY_SPEC), "rights": _object(RIGHTS_SPEC),
    "mapping_status": _one_of(MAPPING_STATUSES), "mapping_evidence": _list_of(_evidence_item),
    "credit_evidence": _nullable(_list_of(_object(CREDIT_EVIDENCE_SPEC))), "illustrators": _STRINGS,
    "composers": _STRINGS, "song_titles": _STRINGS, "song_ids": _list_of(_SONG_ID), "collections": _STRINGS,
    "source_reported": _object(SOURCE_REPORTED_SPEC),
    "comparisons": _object(COMPARISONS_SPEC), "upstream_status": _nullable(_one_of(UPSTREAM_STATUSES)),
    "superseded_by": _nullable(_ASSET_ID), "notes": _nullable(_text),
}
CATALOG_ASSET_SPEC = {
    "family": _one_of(FAMILIES), "source_name": _nonempty, "url": _nonempty, "thumb": _nullable(_nonempty),
    "gallery": _boolean, "slideshow": _boolean, "site_original": _boolean, "archive_url": _URL,
    "provenance": _list_of(_object(PROVENANCE_SPEC, {"asset_id": _ASSET_ID})), "variants": _list_of(_ASSET_ID),
}
FAILURE_SPEC = {"id": _nonempty, "source_id": _SOURCE_ID, "candidate_id": _nonempty, "phase": _one_of(FAILURE_PHASES),
                "url": _URL, "attempted_at": _timestamp, "attempts": _integer(1), "error_class": _one_of(ERROR_CLASSES),
                "http_status": _nullable(_integer(100, 599)), "retriable": _boolean, "manual": _boolean,
                "message": _message_text}
CANDIDATE_SPEC = {"id": _nonempty, "source_id": _SOURCE_ID, "upstream_key": _nonempty, "page_url": _URL,
                  "file_url": _NULLABLE_URL, "title": _nonempty, "role": _nullable(_one_of(KINDS)),
                  "game": _one_of(GAMES), "game_evidence": _list_of(_clean_text),
                  "source_reported": _object(SOURCE_REPORTED_SPEC), "disposition": _one_of(DISPOSITIONS)}
CANDIDATE_OPTIONAL_SPEC = {"reason": _nullable(_text), "asset_id": _nullable(_ASSET_ID), "song_titles": _STRINGS,
                           "illustrators": _STRINGS, "composers": _STRINGS, "collections": _STRINGS, "notes": _text}
MANIFEST_SPEC = {"schema_version": _const(SCHEMA_VERSION), "family": _one_of(FAMILIES),
                 "fetched_at": _nullable(_timestamp), "sources": _list_of(_ANY_JSON, unique=False),
                 "assets": _list_of(_ANY_JSON, unique=False), "failures": _list_of(_ANY_JSON, unique=False)}
DISCOVERY_SPEC = {
    "schema_version": _const(SCHEMA_VERSION), "source_id": _SOURCE_ID, "started_at": _timestamp,
    "finished_at": _timestamp,
    "scope": _object({"lists": _STRINGS}, {"api": _NULLABLE_URL, "notes": _text}, extra=True),
    "continuation": lambda value: None if isinstance(value, dict) else "must be an object",
    "discovery_complete": _boolean, "candidates": _list_of(_ANY_JSON, unique=False),
    "errors": _list_of(_object({"phase": _one_of(FAILURE_PHASES), "url": _NULLABLE_URL, "message": _message_text}),
                       unique=False),
}
SONG_SPEC = {"id": _SONG_ID, "title": _nonempty, "aliases": _STRINGS, "composers": _STRINGS, "illustrators": _STRINGS,
             "collections": _STRINGS, "in_original_game": _boolean, "inclusion_evidence": _list_of(_clean_text),
             "universe_also": _boolean, "removed": _boolean,
             "evidence": _list_of(_object({"source_id": _SOURCE_ID, "url": _URL, "revision": _revision})),
             "artwork_asset_ids": _list_of(_ASSET_ID)}
SONG_INDEX_SPEC = {"schema_version": _const(SCHEMA_VERSION), "generated_from": _STRINGS, "as_of": _date,
                   "songs": _list_of(_ANY_JSON, unique=False),
                   "disagreements": _list_of(_object({"song_id": _SONG_ID, "field": _nonempty,
                                                      "values": _object({}, extra=True)}), unique=False)}
CATALOG_SPEC = {"schema_version": _const(SCHEMA_VERSION), "owner": _const(OWNER), "game": _const(CATALOG_GAME),
                "repository": _const(REPOSITORY), "snapshots": _object({}, extra=True),
                "sources": _list_of(_ANY_JSON, unique=False), "assets": _list_of(_ANY_JSON, unique=False),
                "failures": _list_of(_ANY_JSON, unique=False), "summary": _ANY_JSON}


# Field order: the spec dicts above list fields in contract order, which canonical() and the docs reuse.
SOURCE_FIELDS, SOURCE_OPTIONAL_FIELDS = tuple(SOURCE_SPEC), tuple(SOURCE_OPTIONAL_SPEC)
ASSET_FIELDS, ASSET_OPTIONAL_FIELDS = tuple(ASSET_SPEC), tuple(ASSET_OPTIONAL_SPEC)
CATALOG_ASSET_FIELDS, PROVENANCE_FIELDS = tuple(CATALOG_ASSET_SPEC), tuple(PROVENANCE_SPEC)
FAILURE_FIELDS = tuple(FAILURE_SPEC)
CANDIDATE_FIELDS, CANDIDATE_OPTIONAL_FIELDS = tuple(CANDIDATE_SPEC), tuple(CANDIDATE_OPTIONAL_SPEC)
QUALITY_FIELDS, RIGHTS_FIELDS = tuple(QUALITY_SPEC), tuple(RIGHTS_SPEC)
SOURCE_REPORTED_FIELDS, DOWNLOAD_OBSERVED_FIELDS = tuple(SOURCE_REPORTED_SPEC), tuple(DOWNLOAD_OBSERVED_SPEC)
COMPARISON_FIELDS, CREDIT_EVIDENCE_FIELDS = tuple(COMPARISONS_SPEC), tuple(CREDIT_EVIDENCE_SPEC)
SONG_FIELDS = tuple(SONG_SPEC)


# --- Record-level validation ------------------------------------------------------------------------------------------

def _label(kind: str, record: Any) -> str:
    if isinstance(record, dict) and isinstance(record.get("id"), str):
        return f"{kind} {record['id']!r}"
    return kind


def _fail(label: str, field: str, problem: str) -> None:
    raise ValueError(f"{label}: {field}: {problem}")


def _check_fields(label: str, record: Any, required: Mapping[str, Checker],
                  optional: Mapping[str, Checker] | None = None) -> None:
    if not isinstance(record, dict):
        raise ValueError(f"{label}: must be an object")
    optional = optional or {}
    for key in record:
        if key not in required and key not in optional:
            _fail(label, key, "unknown field")
    for key, checker in required.items():
        if key not in record:
            _fail(label, key, "missing required field")
        problem = checker(record[key])
        if problem:
            _fail(label, key, problem)
    for key, checker in optional.items():
        if key in record:
            problem = checker(record[key])
            if problem:
                _fail(label, key, problem)


def _check_family(label: str, source_id: str, family: str | None) -> str:
    actual = source_family(source_id)
    if family is not None and actual != family:
        _fail(label, "source_id", f"belongs to family {actual!r}, not {family!r}")
    return actual


def _check_unique_sorted(label: str, field: str, records: list[dict], key: str = "id") -> None:
    values = [record[key] for record in records]
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        _fail(label, field, f"duplicate {key} {duplicates[0]!r}")
    if values != sorted(values):
        _fail(label, field, f"must be sorted by {key}")


def validate_source(source: Any, family: str | None = None, *, catalog: bool = False) -> None:
    """A Source record; ``catalog=True`` also requires ``asset_count`` (catalog ``sources[]``)."""
    label = _label("source", source)
    optional = dict(SOURCE_OPTIONAL_SPEC)
    required = dict(SOURCE_SPEC)
    if catalog:
        required["asset_count"] = optional.pop("asset_count")
    _check_fields(label, source, required, optional)
    actual = _check_family(label, source["id"], family)
    if source["family"] != actual:
        _fail(label, "family", f"must match the ID prefix {actual!r}")


def _check_media(label: str, asset: Mapping[str, Any]) -> None:
    fmt = asset["format"]
    if asset["mime"] != FORMAT_MIME[fmt]:
        _fail(label, "mime", f"must be {FORMAT_MIME[fmt]!r} for format {fmt}")
    if fmt == "PDF":
        for field in ("width", "height"):
            if asset[field] is not None:
                _fail(label, field, "must be null for a PDF")
    else:
        for field in ("width", "height"):
            if asset[field] is None:
                _fail(label, field, f"must be a positive integer for raster format {fmt}")


def _check_path(label: str, asset: Mapping[str, Any], family: str) -> None:
    expected = paths.asset_relpath(family, paths.source_slug(asset["source_id"]), asset["original_filename"],
                                   asset["sha256"], FORMAT_EXT[asset["format"]])
    if asset["path"] != expected:
        _fail(label, "path", f"must be {expected!r} (contract path from family, source, original_filename, "
                             f"sha256 and sniffed format)")


def _check_observed(label: str, asset: Mapping[str, Any]) -> None:
    observed = asset["download_observed"]
    for field in ("bytes", "width", "height", "sha1", "sha256"):
        if observed[field] != asset[field]:
            _fail(label, f"download_observed.{field}", f"{observed[field]!r} differs from {field} {asset[field]!r}")
    if observed["sniffed_format"] != asset["format"]:
        _fail(label, "download_observed.sniffed_format", f"differs from format {asset['format']!r}")
    expected = compute_comparisons(asset["source_reported"], observed)
    if asset["comparisons"] != expected:
        _fail(label, "comparisons", f"must be {expected} (compute_comparisons of source_reported and "
                                    f"download_observed)")


def _check_relations(label: str, asset: Mapping[str, Any], *, merged: bool) -> None:
    status = asset["mapping_status"]
    if status in MAPPED_STATUSES and not asset["mapping_evidence"]:
        _fail(label, "mapping_evidence", f"must not be empty when mapping_status is {status!r}")
    if status == "unmapped" and asset["song_ids"] and not merged:
        _fail(label, "song_ids", "must be empty when mapping_status is 'unmapped'")
    upstream, successor = asset.get("upstream_status"), asset.get("superseded_by")
    if successor is not None and upstream != "superseded":
        _fail(label, "superseded_by", "is only allowed with upstream_status 'superseded'")
    if successor is not None and successor == asset["id"]:
        _fail(label, "superseded_by", "must name another asset")
    if "variant_role" in asset and "variant_group" not in asset:
        _fail(label, "variant_role", "requires variant_group")
    if "game_associations" in asset and GAME not in asset["game_associations"]:
        _fail(label, "game_associations", f"must include {GAME!r}")


def validate_asset(asset: Any, family: str | None = None, *, catalog: bool = False) -> None:
    """An Asset record (one per source x file). ``catalog=True`` validates a catalog asset: the primary record's fields
    plus the catalog fields, with merged song lists (use :func:`validate_catalog` for the cross-record checks)."""
    label = _label("catalog asset" if catalog else "asset", asset)
    required = {**ASSET_SPEC, **CATALOG_ASSET_SPEC} if catalog else ASSET_SPEC
    _check_fields(label, asset, required, ASSET_OPTIONAL_SPEC)
    actual = _check_family(label, asset["source_id"], family)
    if asset["id"] != asset_id(asset["source_id"], asset["sha256"]):
        _fail(label, "id", f"must be {asset_id(asset['source_id'], asset['sha256'])!r}")
    if not asset["candidate_id"].startswith(asset["source_id"] + ":") or asset["candidate_id"] == asset[
            "source_id"] + ":":
        _fail(label, "candidate_id", f"must start with {asset['source_id'] + ':'!r}")
    _check_media(label, asset)
    _check_path(label, asset, actual)
    _check_observed(label, asset)
    _check_relations(label, asset, merged=catalog)


def validate_failure(failure: Any, family: str | None = None) -> None:
    label = _label("failure", failure)
    _check_fields(label, failure, FAILURE_SPEC)
    _check_family(label, failure["source_id"], family)
    if not failure["candidate_id"].startswith(failure["source_id"] + ":"):
        _fail(label, "candidate_id", f"must start with {failure['source_id'] + ':'!r}")
    if failure["id"] != failure_id(failure["candidate_id"], failure["phase"]):
        _fail(label, "id", f"must be {failure_id(failure['candidate_id'], failure['phase'])!r}")
    status, error_class = failure["http_status"], failure["error_class"]
    ranges = {"http_4xx": (400, 499), "http_5xx": (500, 599)}
    if error_class in ranges and (status is None or not ranges[error_class][0] <= status <= ranges[error_class][1]):
        _fail(label, "http_status", f"must be in {ranges[error_class]} for error_class {error_class!r}")
    if error_class in NON_RETRIABLE_ERROR_CLASSES and failure["retriable"]:
        _fail(label, "retriable", f"must be false for error_class {error_class!r}")


def _upstream_key_problem(key: str) -> str | None:
    if any(char.isspace() and char.isascii() or unicodedata.category(char) == "Cc" for char in key):
        return "must not contain ASCII whitespace or control characters"
    return None


def validate_candidate(candidate: Any, source_id: str | None = None) -> None:
    label = _label("candidate", candidate)
    _check_fields(label, candidate, CANDIDATE_SPEC, CANDIDATE_OPTIONAL_SPEC)
    if source_id is not None and candidate["source_id"] != source_id:
        _fail(label, "source_id", f"must be {source_id!r}")
    problem = _upstream_key_problem(candidate["upstream_key"])
    if problem:
        _fail(label, "upstream_key", problem)
    if candidate["id"] != candidate_id(candidate["source_id"], candidate["upstream_key"]):
        _fail(label, "id", f"must be {candidate_id(candidate['source_id'], candidate['upstream_key'])!r}")
    disposition, reason = candidate["disposition"], candidate.get("reason")
    target = candidate.get("asset_id")
    if disposition != "downloaded" and not (isinstance(reason, str) and reason.strip()):
        _fail(label, "reason", f"is required for disposition {disposition!r}")
    if disposition in ("downloaded", "duplicate_bytes"):
        if target is None:
            _fail(label, "asset_id", f"is required for disposition {disposition!r}")
        if candidate["game"] not in ("dynamix", "mixed"):
            _fail(label, "game", f"must be 'dynamix' or 'mixed' for disposition {disposition!r}")
        if disposition == "downloaded" and not target.startswith(candidate["source_id"] + ":"):
            _fail(label, "asset_id", "a downloaded candidate's asset belongs to the same source")
    elif target is not None:
        _fail(label, "asset_id", f"must be absent or null for disposition {disposition!r}")
    if disposition == "pending" and reason.split(":", 1)[0].strip() not in PENDING_REASONS:
        _fail(label, "reason", f"must start with a pending reason ({', '.join(PENDING_REASONS)})")
    if disposition == "excluded":
        prefix = reason.split(":", 1)[0].strip()
        if prefix not in EXCLUSION_REASONS:
            _fail(label, "reason", f"must start with an exclusion reason ({', '.join(EXCLUSION_REASONS)})")
        required_game = {"universe_only": "dynamix_universe", "not_dynamix": "not_dynamix"}.get(prefix)
        if required_game and candidate["game"] != required_game:
            _fail(label, "game", f"must be {required_game!r} for exclusion reason {prefix!r}")


def disposition_counts(candidates: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Counts per disposition in DISPOSITIONS order (zeros included)."""
    counts = Counter(candidate["disposition"] for candidate in candidates)
    unknown = sorted(set(counts) - set(DISPOSITIONS))
    if unknown:
        raise ValueError(f"unknown disposition {unknown[0]!r}")
    return {disposition: counts.get(disposition, 0) for disposition in DISPOSITIONS}


def validate_discovery(snapshot: Any, source_id: str | None = None) -> dict[str, int]:
    """A discovery snapshot. Returns disposition counts, which always reconcile with the candidate total."""
    label = f"discovery {snapshot.get('source_id')!r}" if isinstance(snapshot, dict) else "discovery"
    _check_fields(label, snapshot, DISCOVERY_SPEC)
    if source_id is not None and snapshot["source_id"] != source_id:
        _fail(label, "source_id", f"must be {source_id!r}")
    if snapshot["finished_at"] < snapshot["started_at"]:
        _fail(label, "finished_at", "must not be before started_at")
    if snapshot["discovery_complete"]:
        open_lists = sorted(name for name, token in snapshot["continuation"].items() if token is not None)
        if open_lists:
            _fail(label, "continuation", f"list {open_lists[0]!r} still has a continuation token, so discovery is "
                                         "not complete")
    for candidate in snapshot["candidates"]:
        validate_candidate(candidate, snapshot["source_id"])
    _check_unique_sorted(label, "candidates", snapshot["candidates"])
    counts = disposition_counts(snapshot["candidates"])
    if sum(counts.values()) != len(snapshot["candidates"]):  # defensive: every candidate has exactly one disposition
        _fail(label, "candidates", "dispositions do not reconcile with the candidate total")
    return counts


def validate_manifest(manifest: Any, family: str | None = None) -> None:
    """A family manifest ``data/sources/<family>.json`` including all of its records."""
    _check_fields("manifest", manifest, MANIFEST_SPEC)
    actual = manifest["family"]
    label = f"manifest {actual!r}"
    if family is not None and actual != family:
        _fail(label, "family", f"must be {family!r}")
    for source in manifest["sources"]:
        validate_source(source, actual)
    _check_unique_sorted(label, "sources", manifest["sources"])
    source_ids = {source["id"] for source in manifest["sources"]}
    try:
        check_source_slugs(source_ids)
    except ValueError as error:
        _fail(label, "sources", str(error))
    for field, validate in (("assets", validate_asset), ("failures", validate_failure)):
        for record in manifest[field]:
            validate(record, actual)
            if record["source_id"] not in source_ids:
                _fail(_label(field[:-1], record), "source_id", f"is not a source of {label}")
        _check_unique_sorted(label, field, manifest[field])
    by_id = {asset["id"]: asset for asset in manifest["assets"]}
    for asset in manifest["assets"]:
        if asset.get("superseded_by") is not None:
            if asset["superseded_by"] not in by_id:
                _fail(_label("asset", asset), "superseded_by", "names no asset of this manifest")
            _supersession_end(asset, by_id)
    asset_paths = [asset["path"] for asset in manifest["assets"]]
    if len(set(asset_paths)) != len(asset_paths):
        _fail(label, "assets", "two records share a path")
    collisions = paths.casefold_collisions(asset_paths)
    if collisions:
        _fail(label, "assets", f"case-fold path collision {collisions[0]}")
    if manifest["assets"]:
        latest = max(asset["fetched_at"] for asset in manifest["assets"])
        if manifest["fetched_at"] is None or manifest["fetched_at"] < latest:
            _fail(label, "fetched_at", f"must be the latest acquisition time (>= {latest})")


def reconcile(manifest: Mapping[str, Any], snapshots: Mapping[str, Mapping[str, Any]], *,
              other_assets: Iterable[Mapping[str, Any]] = ()) -> dict[str, dict[str, int]]:
    """Cross-check a validated family manifest with its validated discovery snapshots (``{source_id: snapshot}``).

    Checks source counts, candidate <-> asset links, failure <-> candidate links and that a complete discovery accounts
    for every current asset. ``other_assets`` are records of other manifests that ``duplicate_bytes`` may name.
    Returns ``{source_id: disposition counts}``.
    """
    label = f"manifest {manifest['family']!r}"
    sources = {source["id"]: source for source in manifest["sources"]}
    assets = {asset["id"]: asset for asset in manifest["assets"]}
    known = set(assets) | {asset["id"] for asset in other_assets}
    result = {}
    for source_id, snapshot in sorted(snapshots.items()):
        if snapshot["source_id"] != source_id or source_id not in sources:
            _fail(label, "sources", f"discovery snapshot {source_id!r} has no matching source")
        source, candidates = sources[source_id], {c["id"]: c for c in snapshot["candidates"]}
        own_assets = [asset for asset in manifest["assets"] if asset["source_id"] == source_id]
        own_failures = [failure for failure in manifest["failures"] if failure["source_id"] == source_id]
        checks = (("candidate_count", len(candidates)), ("asset_count", len(own_assets)),
                  ("failure_count", len(own_failures)), ("discovery_complete", snapshot["discovery_complete"]))
        for field, actual in checks:
            if field in source and source[field] != actual:
                _fail(_label("source", source), field, f"{source[field]!r} but the data has {actual!r}")
        for candidate in candidates.values():
            _reconcile_candidate(candidate, assets, known)
        for asset in own_assets:
            _reconcile_asset(asset, candidates.get(asset["candidate_id"]), snapshot["discovery_complete"], assets)
        failed = {failure["candidate_id"] for failure in own_failures}
        for failure in own_failures:
            candidate = candidates.get(failure["candidate_id"])
            if failure["phase"] == "discover":
                continue
            if candidate is None and snapshot["discovery_complete"]:
                _fail(_label("failure", failure), "candidate_id", "missing from a complete discovery")
            if candidate is not None and candidate["disposition"] not in ("failed", "blocked"):
                _fail(_label("failure", failure), "candidate_id", f"candidate disposition is "
                                                                  f"{candidate['disposition']!r}, not failed/blocked")
        for candidate in candidates.values():
            if candidate["disposition"] == "failed" and candidate["id"] not in failed:
                _fail(_label("candidate", candidate), "disposition", "'failed' needs a Failure record")
        result[source_id] = disposition_counts(candidates.values())
    return result


def _supersession_end(asset: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
    """The last asset of the ``superseded_by`` chain starting at ``asset`` (``asset`` itself without a successor)."""
    seen = {asset["id"]}
    current = asset
    while current.get("superseded_by") is not None:
        successor = by_id.get(current["superseded_by"])
        if successor is None:
            _fail(_label("asset", current), "superseded_by", "names no asset of this manifest")
        if successor["id"] in seen:
            _fail(_label("asset", asset), "superseded_by", "chain forms a cycle")
        seen.add(successor["id"])
        current = successor
    return current


def _reconcile_asset(asset: Mapping[str, Any], candidate: Mapping[str, Any] | None, complete: bool,
                     assets: Mapping[str, Mapping[str, Any]]) -> None:
    """One archived asset against the candidate it was downloaded for.

    MediaWiki keeps the ``File:`` title (hence the candidate ID) across re-uploads, so earlier revisions share the
    candidate of the current one: they stay archived with ``upstream_status`` ``superseded`` (``superseded_by`` chain
    ending at the asset the candidate now names, or no successor when the new revision is not archived) or
    ``removed``.
    """
    label = _label("asset", asset)
    upstream = asset.get("upstream_status")
    if candidate is None:
        if complete and upstream not in ("removed", "superseded"):
            _fail(label, "candidate_id", "missing from a complete discovery; mark the asset upstream_status "
                                         "'removed' or 'superseded'")
        return
    target, disposition = candidate.get("asset_id"), candidate["disposition"]
    if disposition == "downloaded" and target == asset["id"]:
        if upstream in ("removed", "superseded"):
            _fail(label, "upstream_status", f"is {upstream!r}, but its candidate names this asset as downloaded")
        return
    if upstream == "removed":
        return
    if upstream == "superseded":
        end = _supersession_end(asset, assets)
        if end["id"] == target and end is not asset:
            return
        if end.get("upstream_status") == "superseded" and disposition not in ("downloaded", "duplicate_bytes"):
            return  # the newest archived revision is superseded by one that was not archived (failed/blocked/...)
        _fail(label, "superseded_by", f"chain ends at {end['id']!r}, but its candidate {candidate['id']!r} is "
                                      f"{disposition!r} with asset_id {target!r}")
    _fail(label, "candidate_id", "its candidate is not 'downloaded' with this asset_id (an earlier revision kept "
                                 "under the same candidate needs upstream_status 'superseded' or 'removed')")


def _reconcile_candidate(candidate: Mapping[str, Any], assets: Mapping[str, Mapping[str, Any]],
                         known: set[str]) -> None:
    label = _label("candidate", candidate)
    target = candidate.get("asset_id")
    if candidate["disposition"] == "downloaded":
        asset = assets.get(target)
        if asset is None or asset["candidate_id"] != candidate["id"]:
            _fail(label, "asset_id", f"{target!r} is not an asset recorded for this candidate")
        if candidate["role"] != asset["kind"]:
            _fail(label, "role", f"{candidate['role']!r} differs from the asset kind {asset['kind']!r}")
    elif candidate["disposition"] == "duplicate_bytes" and target not in known:
        _fail(label, "asset_id", f"{target!r} names no recorded asset")


def validate_song_index(index: Any, known_asset_ids: Iterable[str] | None = None) -> None:
    """``data/sources/song-index.json`` (or a proposed ``song-index.<family>.json``)."""
    _check_fields("song index", index, SONG_INDEX_SPEC)
    known = None if known_asset_ids is None else set(known_asset_ids)
    for song in index["songs"]:
        label = _label("song", song)
        _check_fields(label, song, SONG_SPEC)
        base = paths.song_id_base(song["title"])
        if not re.fullmatch(re.escape(base) + r"(?:-[2-9]|-[1-9][0-9]+)?", song["id"]):
            _fail(label, "id", f"must be {base!r} or {base!r} plus a -N collision suffix")
        if song["in_original_game"] and not song["inclusion_evidence"]:
            _fail(label, "inclusion_evidence", "in_original_game requires positive evidence")
        if known is not None:
            missing = sorted(set(song["artwork_asset_ids"]) - known)
            if missing:
                _fail(label, "artwork_asset_ids", f"unknown asset {missing[0]!r}")
    _check_unique_sorted("song index", "songs", index["songs"])
    song_ids = {song["id"] for song in index["songs"]}
    for disagreement in index["disagreements"]:
        if disagreement["song_id"] not in song_ids:
            _fail("song index", "disagreements", f"unknown song {disagreement['song_id']!r}")


def validate_song_links(index: Mapping[str, Any], assets: Iterable[Mapping[str, Any]]) -> None:
    """Cross-check asset (or catalog asset) ``song_ids`` against a validated song index: every ID names a song of the
    index, and that song's exact title or one of its aliases is among the asset's ``song_titles``. This catches IDs
    a worker derived from a different title set than the reconciled index."""
    songs = {song["id"]: song for song in index["songs"]}
    for asset in assets:
        label = _label("asset", asset)
        for song_id in asset["song_ids"]:
            song = songs.get(song_id)
            if song is None:
                _fail(label, "song_ids", f"{song_id!r} is not in the song index")
            if not {song["title"], *song["aliases"]} & set(asset["song_titles"]):
                _fail(label, "song_ids", f"{song_id!r} is the song {song['title']!r}, which is not among "
                                         f"song_titles {asset['song_titles']!r}")


# --- Catalog ----------------------------------------------------------------------------------------------------------

def validate_catalog(catalog: Any) -> None:
    """``data/catalog.json``: top level, sources, failures, catalog assets, ordering, variants and summary arithmetic.

    Implemented in :mod:`lib.catalog_contract` (kept separate because only the coordinator's builder produces it).
    """
    from . import catalog_contract  # local import: catalog_contract builds on this module

    catalog_contract.validate_catalog(catalog)
