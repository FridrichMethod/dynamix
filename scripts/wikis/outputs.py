"""Discovery snapshots, source records and the song index proposal, validated before every atomic write."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from lib import manifest, paths, schema

from . import config, discover, jawikis

RATE_POLICY = ("one request at a time per host, at least 1 s between requests, Retry-After honored up to 60 s, at "
               "most 3 attempts for 429/5xx/timeouts, no retry on 401/403/challenges (project defaults, not provider "
               "limits)")
SONG_INDEX_PATH = "data/discovery/song-index.wikis.json"
GENERATED_FROM = [paths.discovery_relpath(source.source_id) for source in config.SOURCES.values()]
MEDIAWIKI_LISTS = {
    "allimages": "list=allimages (every file, current revision: url, size, mime, sha1, timestamp)",
    "fileusage": "generator=allimages + prop=fileusage|categories (pages embedding each file; file categories)",
    "pages": "generator=allpages, namespace 0, non-redirects + current wikitext, revision IDs and categories",
    "gifmetadata": "prop=imageinfo&iiprop=metadata for GIF files (frame counts)",
    "expandtemplates": "action=expandtemplates for song Title values built from templates",
}


def mediawiki_snapshot(inventory: discover.Inventory, candidates: list[dict], *, pooled_with: list[str]) -> dict:
    source = inventory.source
    snapshot = {
        "schema_version": schema.SCHEMA_VERSION, "source_id": source.source_id,
        "started_at": inventory.started_at, "finished_at": inventory.finished_at,
        "scope": {"api": source.api_url, "lists": [MEDIAWIKI_LISTS[name] for name in discover.LISTS],
                  "notes": ("One candidate per file of list=allimages. Roles and games come from song templates "
                            "(Template:Song = original game, Template:SongUV = Dynamix Universe), page usage, "
                            "gallery/link captions, reviewed list pages, file categories and, only when nothing "
                            "else applies, filename conventions; evidence is pooled by file title with "
                            + ", ".join(pooled_with) + ". Accepted candidates that are not downloaded yet are "
                            "'pending' with reason 'awaiting_download'."),
                  "file_count": len(inventory.files), "article_count": len(inventory.pages),
                  "requests": inventory.requests},
        "continuation": {name: inventory.continuation.get(name, {}) for name in discover.LISTS},
        "discovery_complete": inventory.complete,
        "candidates": candidates,
        "errors": [_error(error) for error in inventory.errors],
    }
    return _validated_discovery(snapshot)


def html_snapshot(source: config.WikiSource, pages: list[jawikis.ListPage], *, started_at: str,
                  finished_at: str) -> dict:
    read = [page for page in pages if page.error is None]
    snapshot = {
        "schema_version": schema.SCHEMA_VERSION, "source_id": source.source_id,
        "started_at": started_at, "finished_at": finished_at,
        "scope": {"api": None, "lists": [f"{page.label} <{page.url}>" for page in pages],
                  "notes": (f"Metadata-only: {len(read)}/{len(pages)} song-list pages read and parsed for the "
                            "original-game song index; file attachments were not enumerated, so this source has no "
                            "file candidates and its discovery is not complete as an image inventory."),
                  "pages": [{"label": page.label, "url": page.url, "final_url": page.final_url,
                             "last_modified": page.last_modified, "rows": len(page.rows)} for page in pages],
                  "song_rows": [[row.title, row.credit, row.list_label] for page in pages for row in page.rows]},
        "continuation": {"list_pages": None if len(read) == len(pages) else {"unread": [
            page.url for page in pages if page.error is not None]}},
        "discovery_complete": False,
        "candidates": [],
        "errors": [_error(page.error) for page in pages if page.error is not None],
    }
    return _validated_discovery(snapshot)


def _error(error: Mapping[str, Any]) -> dict:
    url = error.get("url")
    url = schema.redact_url(url, strict=True) if isinstance(url, str) and schema.checks.url_problem(url) is None \
        else None
    return {"phase": error.get("phase") or "discover", "url": url,
            "message": schema.redact_text(str(error.get("message") or "error"))[:500]}


def _validated_discovery(snapshot: dict) -> dict:
    result = manifest.sort_discovery(snapshot)
    schema.validate_discovery(result, snapshot["source_id"])
    return result


def mediawiki_source(source: config.WikiSource, snapshot: Mapping[str, Any], assets: Iterable[Mapping[str, Any]],
                     failures: Iterable[Mapping[str, Any]], *, metadata_only: bool) -> dict:
    counts = schema.disposition_counts(snapshot["candidates"])
    complete = snapshot["discovery_complete"]
    blocked = counts["blocked"] > 0
    if not snapshot["candidates"] and snapshot["errors"]:
        status = "temporarily_unavailable"
    elif not complete or blocked:
        status = "partially_available"
    else:
        status = "available"
    asset_count = sum(1 for asset in assets if asset["source_id"] == source.source_id)
    failure_count = sum(1 for failure in failures if failure["source_id"] == source.source_id)
    summary = ", ".join(f"{name} {value}" for name, value in counts.items() if value)
    notes = (f"{source.notes} Last discovery: {len(snapshot['candidates'])} files ({summary}); enumeration "
             f"{'complete' if complete else 'INCOMPLETE (see the snapshot continuation/errors)'}."
             + (" Metadata-only run: accepted files are pending download." if metadata_only and counts["pending"]
                else ""))
    record = {
        "id": source.source_id, "name": source.name, "family": "wikis", "url": source.url,
        "platform": source.platform, "status": status, "checked_at": snapshot["finished_at"], "notes": notes,
        "api_url": source.api_url, "crawl_scope": "every file of list=allimages; namespace-0 articles; file usage",
        "rate_policy": RATE_POLICY, "discovery_complete": complete, "candidate_count": len(snapshot["candidates"]),
        "asset_count": asset_count, "failure_count": failure_count, "evidence_level": "live_api",
    }
    schema.validate_source(record, "wikis")
    return schema.canonical(record, "source")


def html_source(source: config.WikiSource, snapshot: Mapping[str, Any], pages: list[jawikis.ListPage],
                failures: Iterable[Mapping[str, Any]]) -> dict:
    errors = [page.error for page in pages if page.error is not None]
    blocked = any("blocked" in (error.get("message") or "") or "challenge" in (error.get("message") or "")
                  for error in errors)
    read = len(pages) - len(errors)
    if read == 0:
        status = "manual_access" if blocked else "temporarily_unavailable"
        level = "blocked" if blocked else "not_established"
    else:
        status, level = "metadata_only", "page_read"
    rows = sum(len(page.rows) for page in pages)
    record = {
        "id": source.source_id, "name": source.name, "family": "wikis", "url": source.url,
        "platform": source.platform, "status": status, "checked_at": snapshot["finished_at"],
        "notes": f"{source.notes} Last read: {read}/{len(pages)} list pages, {rows} song rows.",
        "crawl_scope": "registered song-list pages only: " + ", ".join(page.url for page in pages),
        "rate_policy": RATE_POLICY, "discovery_complete": False, "candidate_count": 0, "asset_count": 0,
        "failure_count": sum(1 for failure in failures if failure["source_id"] == source.source_id),
        "evidence_level": level,
    }
    schema.validate_source(record, "wikis")
    return schema.canonical(record, "source")


def write_json(root: Path, rel: str, data: Any) -> None:
    manifest.write_json_atomic(paths.safe_path(root, rel), data)
