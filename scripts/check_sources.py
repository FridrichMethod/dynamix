"""Manual, read-only comparison of live source metadata with the committed discovery snapshots.

    python -I scripts/check_sources.py --report PATH [--summary PATH] [--root PATH] [--source ID ...] [--max-pages N]

For each source with a cheap read-only adapter (the MediaWiki ``list=allimages`` metadata of ``wikis:miraheze`` and
``wikis:fandom``; no file is downloaded) the live listing is compared with ``data/discovery/<source-slug>.json``:

* **added**: live files without a saved candidate (when the saved snapshot is itself incomplete, they may simply lie
  beyond its saved continuation, and the report says so);
* **changed**: saved candidates whose live SHA-1, size, dimensions, MIME type or upload timestamp differ from the
  saved ``source_reported`` values (a value the snapshot did not record is not comparable and never a change);
* **removed**: saved candidates absent from a live listing that was enumerated **completely** (every continuation
  followed without an error and every entry parsed). When the live listing is incomplete (HTTP error, timeout, block,
  API error, an entry without a usable title, page limit) missing candidates are only reported as *not seen*, never as
  removed;
* **aliases not listed**: saved file redirects and aliases (``excluded`` with reason ``redirect_or_alias``), which
  ``allimages`` never lists, so their absence is expected and never a removal.

``differences`` in the summary means added, changed or removed files only; *not seen* is reported separately. Every
other saved snapshot is listed with its saved completeness and marked as not checked live. Nothing in the checkout is
modified: the report paths must lie outside it, and no snapshot, manifest or asset is written. Network access uses
the shared polite client (host allowlist, 1 s spacing, bounded retries). Upstream strings are untrusted: the Markdown
report shows each one as an inline code span and links only https file pages on the source's own host.

Exit status: 0 when the report was written and every selected source was enumerated completely (whatever it found);
3 when the report was written but a live check was incomplete or its saved snapshot unusable, so the run is not
mistaken for a clean check; 1 (or argparse's 2) for invalid arguments, with no report.

The report helpers ``code()`` and ``safe_link()`` are adapted from FridrichMethod/deemo scripts/check_sources.py
(Apache-2.0, commit b14bf0d5f5a05e5382c319f3fd5019a4936820b8); the rest of this script was written for this project.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote, urlsplit

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:  # `python -I` does not put the script's directory on sys.path
    sys.path.insert(0, str(SCRIPTS))

from lib import manifest, net, paths, schema  # noqa: E402

ROOT = SCRIPTS.parent


@dataclass(frozen=True)
class MediaWiki:
    api: str
    host: str


MEDIAWIKI_SOURCES = {
    "wikis:miraheze": MediaWiki("https://dynamix.miraheze.org/w/api.php", "dynamix.miraheze.org"),
    "wikis:fandom": MediaWiki("https://dynamixc4cat.fandom.com/api.php", "dynamixc4cat.fandom.com"),
}
ALLIMAGES_PARAMS = {"action": "query", "list": "allimages", "aiprop": "timestamp|url|size|sha1|mime",
                    "ailimit": "500", "format": "json", "formatversion": "2"}
FILE_PREFIX = "File:"
COMPARED_FIELDS = ("sha1", "bytes", "width", "height", "mime", "timestamp")
INTEGER_FIELDS = ("bytes", "width", "height")
DEFAULT_MAX_PAGES = 200  # 100,000 files at 500 per page; reaching it leaves the listing incomplete
MAX_TEXT = 300
ROW_LIMIT = 100
INCOMPLETE_STATUS = 3  # the report was written, but at least one selected live check is incomplete
ALIAS_REASON = "redirect_or_alias"  # an excluded file redirect or alias: allimages lists only real files
# Control characters and line/paragraph separators would end a table row; bidirectional controls reorder text.
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
BIDI_CONTROL = re.compile(r"[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
URL_SAFE = ":/?#[]@!$&'()*+,;=%"

ClientFactory = Callable[[list[str]], Any]


def default_client(hosts: list[str]) -> net.PoliteClient:
    return net.PoliteClient(hosts)


@dataclass
class Listing:
    """A live enumeration: ``complete`` only when the API returned no further continuation and nothing failed."""

    files: dict[str, dict] = field(default_factory=dict)
    complete: bool = False
    pages: int = 0
    continuation: dict | None = None
    errors: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _text(value: Any) -> str | None:
    return value[:MAX_TEXT] if isinstance(value, str) and value else None


def _integer(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def parse_image(image: Any) -> dict | None:
    """One ``allimages`` entry as ``{key, title, page_url, url, sha1, bytes, width, height, mime, timestamp}``."""
    if not isinstance(image, dict):
        return None
    title, name = image.get("title"), image.get("name")
    if isinstance(title, str) and title.startswith(FILE_PREFIX):
        key = title.replace(" ", "_")
    elif isinstance(name, str) and name:
        key = FILE_PREFIX + name.replace(" ", "_")
    else:
        return None
    return {"key": key, "title": key[len(FILE_PREFIX):].replace("_", " "), "page_url": _text(image.get("descriptionurl")),
            "url": _text(image.get("url")), "sha1": _text(image.get("sha1")), "bytes": _integer(image.get("size")),
            "width": _integer(image.get("width")), "height": _integer(image.get("height")),
            "mime": _text(image.get("mime")), "timestamp": _text(image.get("timestamp"))}


def _error(url: str, message: str, error_class: str = "other") -> dict:
    return {"phase": "discover", "url": url, "error_class": error_class, "message": schema.redact_text(message)}


def _read_page(listing: Listing, data: Any, api: str) -> dict | None:
    """Add one response page; returns the next continuation, ``{}`` at the end, or None after an error."""
    if not isinstance(data, dict):
        listing.errors.append(_error(api, "response is not a JSON object"))
        return None
    if "error" in data:
        problem = data["error"] if isinstance(data["error"], dict) else {}
        listing.errors.append(_error(api, f"API error {_text(problem.get('code'))}: {_text(problem.get('info'))}"))
        return None
    images = (data.get("query") or {}).get("allimages") if isinstance(data.get("query"), dict) else None
    if not isinstance(images, list):
        listing.errors.append(_error(api, "response has no query.allimages list"))
        return None
    unreadable = 0
    for image in images:
        record = parse_image(image)
        if record is None:
            unreadable += 1  # some file is listed but cannot be identified: its saved candidate must not look removed
        elif record["key"] in listing.files:
            listing.notes.append(f"{record['key']} was listed twice; the first entry is kept")
        else:
            listing.files[record["key"]] = record
    if unreadable:
        listing.errors.append(_error(api, f"page {listing.pages}: {unreadable} allimages "
                                          f"{'entry has' if unreadable == 1 else 'entries have'} no usable title or "
                                          "name, so the listing is incomplete"))
    following = data.get("continue", {})
    if not isinstance(following, dict) or not all(isinstance(v, str) for v in following.values()):
        listing.errors.append(_error(api, "continuation is not an object of strings"))
        return None
    return following


def enumerate_allimages(client: Any, wiki: MediaWiki, max_pages: int) -> Listing:
    """Follow ``list=allimages`` continuations until the API reports none, an error occurs or the page limit."""
    listing, continuation = Listing(), {}
    while listing.pages < max_pages:
        try:
            data, _ = client.get_json(wiki.api, {**ALLIMAGES_PARAMS, **continuation})
        except net.FetchError as error:
            listing.errors.append(_error(error.url or wiki.api, str(error), error.error_class))
            break
        except ValueError as error:  # e.g. a continuation token the client refuses to put into a URL
            listing.errors.append(_error(wiki.api, f"request refused: {error}"))
            break
        listing.pages += 1
        following = _read_page(listing, data, wiki.api)
        if following is None:
            break
        if not following:  # enumerated to the end; complete unless an entry could not be read
            listing.complete, listing.continuation = not listing.errors, None
            return listing
        if following == continuation:
            listing.errors.append(_error(wiki.api, "continuation did not advance"))
            break
        continuation = following
    else:
        listing.errors.append(_error(wiki.api, f"stopped after --max-pages {max_pages}; continuation kept"))
    listing.continuation = continuation or None
    return listing


def _normalize(field_name: str, value: Any) -> Any:
    if value is None:
        return None
    if field_name == "mime":
        return schema.normalize_mime(value)
    if field_name == "sha1":
        return value.lower()
    if field_name == "timestamp":
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else value
        except ValueError:
            return value
    return value


def differences(saved: Mapping[str, Any], live: Mapping[str, Any]) -> dict[str, list]:
    """Fields recorded on both sides whose normalized values differ: ``{field: [saved, live]}``."""
    changed = {}
    for name in COMPARED_FIELDS:
        before, after = saved.get(name), live.get(name)
        if before is not None and after is not None and _normalize(name, before) != _normalize(name, after):
            changed[name] = [before, after]
    return changed


def _saved_row(candidate: Mapping[str, Any]) -> dict:
    return {"key": candidate["upstream_key"], "title": candidate["title"], "page_url": candidate["page_url"],
            "disposition": candidate["disposition"], "asset_id": candidate.get("asset_id"),
            "saved": {name: candidate["source_reported"].get(name) for name in COMPARED_FIELDS}}


def listed_by_allimages(candidate: Mapping[str, Any]) -> bool:
    """Whether ``list=allimages`` is expected to list this saved candidate: every current file, but never a file
    redirect or alias (excluded as ``redirect_or_alias``)."""
    reason = candidate.get("reason")
    prefix = reason.split(":", 1)[0].strip() if isinstance(reason, str) else ""
    return not (candidate["disposition"] == "excluded" and prefix == ALIAS_REASON)


def compare(snapshot: Mapping[str, Any], listing: Listing) -> dict:
    saved = {c["upstream_key"]: c for c in snapshot["candidates"] if c["upstream_key"].startswith(FILE_PREFIX)}
    live = listing.files
    added = [dict(live[key]) for key in sorted(live) if key not in saved]
    changed, unchanged, without_metadata = [], 0, 0
    for key in sorted(live.keys() & saved.keys()):
        reported = saved[key]["source_reported"]
        if all(reported.get(name) is None for name in COMPARED_FIELDS):
            without_metadata += 1
        diff = differences(reported, live[key])
        if diff:
            changed.append({**_saved_row(saved[key]), "page_url": live[key]["page_url"] or saved[key]["page_url"],
                            "changes": diff, "live": {name: live[key][name] for name in COMPARED_FIELDS}})
        else:
            unchanged += 1
    absent = [key for key in sorted(saved) if key not in live]
    missing = [_saved_row(saved[key]) for key in absent if listed_by_allimages(saved[key])]
    aliases = [_saved_row(saved[key]) for key in absent if not listed_by_allimages(saved[key])]
    removed, not_seen = (missing, []) if listing.complete else ([], missing)
    return {
        "added": added, "changed": changed, "removed": removed, "not_seen": not_seen, "aliases_not_listed": aliases,
        "counts": {"saved_candidates": len(snapshot["candidates"]), "saved_files": len(saved),
                   "not_comparable": len(snapshot["candidates"]) - len(saved), "live_files": len(live),
                   "added": len(added), "changed": len(changed), "removed": len(removed), "not_seen": len(not_seen),
                   "aliases_not_listed": len(aliases), "unchanged": unchanged,
                   "saved_without_metadata": without_metadata},
    }


def baseline_info(rel: str, snapshot: Mapping[str, Any]) -> dict:
    return {"path": rel, "discovery_complete": snapshot["discovery_complete"], "finished_at": snapshot["finished_at"],
            "candidates": len(snapshot["candidates"]), "errors": len(snapshot["errors"]),
            "dispositions": schema.disposition_counts(snapshot["candidates"])}


def load_baseline(root: Path, source_id: str) -> tuple[dict | None, str | None]:
    try:
        snapshot = manifest.load_discovery(root, source_id)
    except (OSError, ValueError) as error:
        return None, f"saved snapshot is unreadable or invalid: {error}"
    return (snapshot, None) if snapshot is not None else (None, "no saved snapshot")


def check_mediawiki(root: Path, source_id: str, client_factory: ClientFactory, max_pages: int) -> dict:
    wiki = MEDIAWIKI_SOURCES[source_id]
    rel = paths.discovery_relpath(source_id)
    entry: dict[str, Any] = {"source_id": source_id, "adapter": "mediawiki-allimages", "api": wiki.api, "host": wiki.host}
    snapshot, problem = load_baseline(root, source_id)
    if snapshot is None:
        return {**entry, "status": "baseline_error", "reason": f"{rel}: {problem}; nothing was compared"}
    entry["baseline"] = baseline_info(rel, snapshot)
    with client_factory([wiki.host]) as client:
        listing = enumerate_allimages(client, wiki, max_pages)
    notes = list(dict.fromkeys(listing.notes))
    saved_api = snapshot["scope"].get("api") if isinstance(snapshot["scope"], dict) else None
    if saved_api and saved_api != wiki.api:
        notes.append(f"saved scope.api {saved_api} differs from the checked endpoint {wiki.api}")
    if not snapshot["discovery_complete"]:
        notes.append("the saved snapshot is incomplete, so 'added' files may only lie beyond its saved continuation")
    return {**entry, "status": "compared" if listing.complete else "incomplete",
            "live": {"complete": listing.complete, "pages": listing.pages, "files": len(listing.files),
                     "continuation": listing.continuation, "errors": listing.errors},
            **compare(snapshot, listing), "notes": notes}


def other_snapshots(root: Path, checked: Iterable[str]) -> list[dict]:
    """Saved discovery snapshots without a live adapter (or not selected): listed, never compared."""
    entries, checked = [], set(checked)
    directory = paths.safe_path(root, paths.DISCOVERY_ROOT)
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        rel = f"{paths.DISCOVERY_ROOT}/{path.name}"
        if path.is_symlink():
            entries.append({"source_id": None, "status": "baseline_error", "reason": f"{rel}: symlink not followed"})
            continue
        try:
            data = manifest.load_json(path)
        except (OSError, ValueError) as error:
            entries.append({"source_id": None, "status": "baseline_error", "reason": f"{rel}: {error}"})
            continue
        if not isinstance(data, dict) or "candidates" not in data or not isinstance(data.get("source_id"), str):
            continue  # e.g. data/discovery/song-index.wikis.json is not a discovery snapshot
        if data["source_id"] in checked:
            continue
        snapshot, problem = load_baseline(root, data["source_id"])
        if snapshot is None:
            entries.append({"source_id": data["source_id"], "status": "baseline_error", "reason": f"{rel}: {problem}"})
            continue
        reason = ("no read-only live adapter; check this source manually" if data["source_id"] not in MEDIAWIKI_SOURCES
                  else "not selected with --source")
        entries.append({"source_id": data["source_id"], "status": "not_checked", "reason": reason,
                        "baseline": baseline_info(rel, snapshot)})
    return entries


def run(root: Path, *, sources: Iterable[str] = tuple(MEDIAWIKI_SOURCES), client_factory: ClientFactory = default_client,
        max_pages: int = DEFAULT_MAX_PAGES, now: str | None = None) -> dict:
    root = Path(root).resolve()
    selected = [source_id for source_id in MEDIAWIKI_SOURCES if source_id in set(sources)]
    results = [check_mediawiki(root, source_id, client_factory, max_pages) for source_id in selected]
    results += other_snapshots(root, selected)
    compared = [entry for entry in results if "counts" in entry]
    totals = {name: sum(entry["counts"][name] for entry in compared)
              for name in ("added", "changed", "removed", "not_seen", "aliases_not_listed", "unchanged")}
    return {"schema_version": schema.SCHEMA_VERSION, "generated_at": now or manifest.now_utc(), "read_only": True,
            "complete": bool(selected) and all(entry["status"] == "compared" for entry in results[:len(selected)]),
            "differences": any(totals[name] for name in ("added", "changed", "removed")),
            "not_seen": totals["not_seen"], "totals": totals, "sources": results}


# ---------------------------------------------------------------- Markdown report (untrusted text as code spans)

def code(value: Any) -> str:
    """Untrusted text as one inline code span that stays inside its table cell (no mentions, links, HTML, rows).

    The fence is one backtick longer than any backtick run inside; a pipe is escaped because GitHub splits table
    cells before it parses code spans.
    """
    text = BIDI_CONTROL.sub("", CONTROL.sub(" ", str(value))).replace("|", "\\|")
    if not text.strip():
        return "?"
    fence = "`" * (max(map(len, re.findall("`+", text)), default=0) + 1)
    pad = " " if text[0] == "`" or text[-1] == "`" else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def safe_link(url: Any, host: str | None) -> str | None:
    """The URL, percent-encoded for a Markdown link target, only when it is https on ``host`` itself."""
    if not isinstance(url, str) or not host:
        return None
    try:
        parts = urlsplit(url)
        trusted = (parts.scheme == "https" and parts.hostname == host and parts.port is None
                   and not parts.username and not parts.password)
    except ValueError:
        return None
    return quote(url, safe=URL_SAFE) if trusted else None


def _file_cell(row: Mapping[str, Any], host: str | None) -> str:
    text, target = code(row["title"]), safe_link(row.get("page_url"), host)
    return f"[{text}](<{target}>)" if target else text


def _value(field_name: str, value: Any) -> str:
    if value is None:
        return "?"
    return code(str(value)[:12] if field_name == "sha1" else value)


def _table(title: str, rows: list[dict], columns: tuple[str, ...], cells: Callable[[dict], list[str]]) -> list[str]:
    if not rows:
        return []
    lines = [f"#### {title} ({len(rows)})", "", "| " + " | ".join(columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    lines += ["| " + " | ".join(cells(row)) + " |" for row in rows[:ROW_LIMIT]]
    if len(rows) > ROW_LIMIT:
        lines += ["", f"{len(rows) - ROW_LIMIT} more are listed only in the --summary JSON."]
    return lines + [""]


def _source_section(entry: Mapping[str, Any]) -> list[str]:
    host = entry.get("host")
    lines = [f"### {code(entry['source_id'] or 'unknown source')}: {entry['status']}", ""]
    if "reason" in entry:
        lines += [code(entry["reason"]), ""]
    baseline = entry.get("baseline")
    if baseline:
        lines += [f"- Saved snapshot {code(baseline['path'])}: {baseline['candidates']} candidates, "
                  f"discovery_complete {code(baseline['discovery_complete'])}, finished {code(baseline['finished_at'])}"]
    live = entry.get("live")
    if live:
        lines += [f"- Live listing: {live['files']} files in {live['pages']} pages, complete {code(live['complete'])}"
                  + (f", continuation {code(json.dumps(live['continuation'], ensure_ascii=False))}"
                     if live["continuation"] else "")]
        lines += [f"- Error ({code(error['error_class'])}): {code(error['message'])}" for error in live["errors"]]
    lines += [f"- Note: {code(note)}" for note in entry.get("notes", [])]
    lines.append("")
    if "counts" in entry:
        counts = entry["counts"]
        lines += [("| added | changed | removed | not seen (incomplete) | aliases not listed | unchanged | "
                   "not comparable |"),
                  "| --- | --- | --- | --- | --- | --- | --- |",
                  f"| {counts['added']} | {counts['changed']} | {counts['removed']} | {counts['not_seen']} | "
                  f"{counts['aliases_not_listed']} | {counts['unchanged']} | {counts['not_comparable']} |", ""]
        lines += _table("Added", entry["added"], ("File", "Size", "Dimensions", "SHA-1", "Uploaded"),
                        lambda row: [_file_cell(row, host), _value("bytes", row["bytes"]),
                                     f"{_value('width', row['width'])}×{_value('height', row['height'])}",
                                     _value("sha1", row["sha1"]), _value("timestamp", row["timestamp"])])
        lines += _table("Changed", entry["changed"], ("File", "Saved disposition", "Changes (saved → live)"),
                        lambda row: [_file_cell(row, host), code(row["disposition"]), "; ".join(
                            f"{name} {_value(name, old)} → {_value(name, new)}"
                            for name, (old, new) in row["changes"].items())])
        lines += _table("Removed (absent from a complete live listing)", entry["removed"],
                        ("File", "Saved disposition", "Asset"),
                        lambda row: [_file_cell(row, host), code(row["disposition"]), _value("asset", row["asset_id"])])
        lines += _table("Not seen (live listing incomplete; not a removal)", entry["not_seen"],
                        ("File", "Saved disposition"),
                        lambda row: [_file_cell(row, host), code(row["disposition"])])
        lines += _table("Redirects and aliases not listed (expected; not a removal)", entry["aliases_not_listed"],
                        ("File", "Saved disposition"),
                        lambda row: [_file_cell(row, host), code(row["disposition"])])
    return lines


def render(report: Mapping[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "## Source metadata check", "",
        f"Generated {code(report['generated_at'])}. Read-only: no snapshot, manifest or asset was changed, and no "
        "file was downloaded.", "",
        f"- Live checks complete: {code(report['complete'])}"
        + ("" if report["complete"] else " (**incomplete**: some saved files could not be checked; see the errors "
           "below)"),
        f"- Upstream differences (added, changed or removed): {code(report['differences'])}",
        f"- Totals: {totals['added']} added · {totals['changed']} changed · {totals['removed']} removed · "
        f"{totals['not_seen']} not seen (incomplete listing) · {totals['aliases_not_listed']} redirects/aliases not "
        f"listed · {totals['unchanged']} unchanged", "",
        "A file is reported as removed only when the live listing was enumerated completely; a timeout, block, HTTP "
        "or API error, an entry without a usable title, or the page limit leaves the listing incomplete, and missing "
        "files are then only *not seen*. Saved file redirects and aliases are never in the listing. "
        "Archived bytes are never deleted because of this report; review it, then refresh snapshots and downloads "
        "with the fetchers (e.g. `fetch_wikis.py --resume`) and commit the reviewed result.", "",
    ]
    for entry in report["sources"]:
        lines += _source_section(entry)
    return "\n".join(lines).rstrip("\n") + "\n"


def _outside(value: Path, root: Path) -> Path:
    resolved = value.resolve()
    if resolved == root or resolved.is_relative_to(root):
        raise ValueError(f"{value} is inside the checkout; write reports elsewhere (the check is read-only)")
    return resolved


def main(argv: list[str] | None = None, *, client_factory: ClientFactory = default_client) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", required=True, type=Path, help="Markdown report (outside the checkout)")
    parser.add_argument("--summary", type=Path, help="full JSON comparison (outside the checkout)")
    parser.add_argument("--root", type=Path, default=ROOT, help="checkout whose data/discovery is compared")
    parser.add_argument("--source", action="append", choices=sorted(MEDIAWIKI_SOURCES),
                        help="check only this source live (repeatable; default: all with an adapter)")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="API pages per source")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.max_pages < 1:
            raise ValueError("--max-pages must be at least 1")
        report_path = _outside(args.report, root)
        summary_path = _outside(args.summary, root) if args.summary else None
        if summary_path == report_path:
            raise ValueError("--report and --summary must be different files")
        report = run(root, sources=args.source or tuple(MEDIAWIKI_SOURCES), client_factory=client_factory,
                     max_pages=args.max_pages)
    except ValueError as error:
        print(f"check_sources: {error}", file=sys.stderr)
        return 1
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render(report), encoding="utf-8")
    if summary_path:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"complete": report["complete"], "differences": report["differences"],
                      "not_seen": report["not_seen"], "totals": report["totals"]}, ensure_ascii=False))
    if not report["complete"]:
        print("check_sources: the report was written, but a live check was incomplete (see its errors); "
              f"exit status {INCOMPLETE_STATUS}", file=sys.stderr)
        return INCOMPLETE_STATUS
    return 0


if __name__ == "__main__":
    sys.exit(main())
