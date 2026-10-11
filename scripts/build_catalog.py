#!/usr/bin/env python3
"""Build data/catalog.json, data/catalog.js and index.html from the family manifests without modifying any bytes.

    python -I scripts/build_catalog.py [--verify] [--check] [--root PATH]

Inputs: data/sources/{artists,official,wikis,archives}.json (schema v1, validated with lib.schema), the media they
name under assets/public/, data/thumbs.json (scripts/build_thumbnails.py) and templates/slideshow.html rendered by
scripts/render_slideshow.py (UI-owned). Records with equal SHA-256 become one catalog entry whose primary record (its
fields, title, path and URL) is the first by the frozen primary-record rule (09 "Catalog presentation rules";
lib.schema.provenance_sort_key): family priority (artists < official < wikis < archives); preferred source within the family
(wikis:miraheze, then wikis:fandom, then every other source); quality.source_variant (site_original < preview <
unknown); source ID; asset ID. Every record is kept in `provenance`, primary first, in that order. Distinct bytes
always stay distinct entries; superseded and removed records stay archived. The generated catalog is checked with
lib.schema.validate_catalog.

--verify  re-hash and fully decode every archived file, check previews by SHA-256, reject files under assets/public/
          that no manifest record names (dot-files such as interrupted `.part` downloads excepted) and reject records
          that break the title rule: a bare upstream file name or stem as title, or confirmed single-song wiki song
          art not titled with that song (see title_problem).
--check   regenerate the outputs in memory and compare them byte-for-byte (a CRLF checkout is accepted) without
          writing anything; exit 1 when one is missing or stale.
--root    build another repository-shaped tree (tests, fixtures). Outside the repository root data/thumbs.json and
          templates/slideshow.html are optional: without them `thumb` stays null and index.html is not generated.

Adapted from FridrichMethod/deemo scripts/build_catalog.py (Apache-2.0, commit
b14bf0d5f5a05e5382c319f3fd5019a4936820b8); modified for the Dynamix archive in 2026: schema-v1 manifests, no legacy
family or song-mapping inputs, contract dedup/provenance/tier rules, slideshow markup delegated to render_slideshow.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable, Mapping

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:  # `python -I` does not put the script's directory on sys.path
    sys.path.insert(0, str(SCRIPTS))

from lib import manifest, media, paths, schema  # noqa: E402

REPO_ROOT = SCRIPTS.parent
CATALOG_JSON, CATALOG_JS, INDEX_HTML = "data/catalog.json", "data/catalog.js", "index.html"
THUMBS_MANIFEST = "data/thumbs.json"
TEMPLATE = "templates/slideshow.html"
JS_GLOBAL = "window.DYNAMIX_CATALOG"
# Previews (scripts/build_thumbnails.py): every archive-tier raster (its original is not served by Pages) and every
# gallery image whose long edge exceeds this; smaller site-tier images are shown from the original.
PREVIEW_LONG_EDGE = 480
MERGED_FIELDS = ("illustrators", "composers", "song_titles", "song_ids", "collections")
THUMB_ENTRY_FIELDS = ("path", "width", "height", "bytes", "sha256")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_REPORTED = 20
# Families whose titles come from the post or page text a record cites, never from its file name (09 "Titles").
TEXT_TITLED_FAMILIES = ("artists", "official", "archives")
# Wiki song art confirmed to show exactly one song with this evidence (confirmed_wiki or stronger) carries that song's
# exact title (09 "Titles").
CONFIRMED_STATUSES = ("confirmed_primary", "confirmed_wiki")
# Quotation marks around a caption quoted in evidence ('A_O', "A_O", “A_O”, ‘A_O’, 「A_O」).
QUOTE_PAIRS = (("'", "'"), ('"', '"'), ("“", "”"), ("‘", "’"), ("「", "」"))


class CatalogError(ValueError):
    """An input, archived file or generated output that does not satisfy the contract."""


# --- Inputs -----------------------------------------------------------------------------------------------------------

def git_environment() -> dict[str, str]:
    """The environment without GIT_* variables, which a hook or linked worktree exports and which would point git
    commands run for another root at the enclosing checkout."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def tracked_files(root: Path, directories: Iterable[str]) -> list[str] | None:
    """Git-tracked files under these directories as sorted POSIX paths, or None when ``root`` is not the top of a Git
    checkout (or Git is unavailable)."""
    environment = git_environment()
    try:
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, check=True, env=environment)
        if Path(top.stdout.strip()).resolve() != Path(root).resolve():
            return None
        listed = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", *directories], capture_output=True,
                                check=True, env=environment)
    except (OSError, subprocess.CalledProcessError):
        return None
    return sorted(name.decode("utf-8") for name in listed.stdout.split(b"\0") if name)


def load_manifests(root: Path, *, strict: bool | None = None) -> dict[str, dict]:
    """Every family manifest, validated. A family without a manifest file gets an empty manifest, unless Git tracks
    that file (a lost manifest must not quietly shrink the catalog); an existing invalid manifest fails. At the
    repository root (``strict``, the default there) a missing manifest also fails when Git cannot be queried (not on
    PATH, "dubious ownership", no checkout): only Git can show that the family was never committed. Other roots
    (fixtures) treat a missing manifest as an empty family when Git cannot tell."""
    strict = is_repository_root(root) if strict is None else strict
    result: dict[str, dict] = {}
    queried, tracked = False, None
    for family in schema.FAMILIES:
        rel = paths.manifest_relpath(family)
        try:
            target = paths.safe_path(root, rel)
        except ValueError as error:
            raise CatalogError(f"{rel}: {error}") from None
        if target.exists() and not target.is_file():
            raise CatalogError(f"{rel}: not a regular file")
        if not target.exists():
            if not queried:
                listed = tracked_files(root, ["data/sources"])
                queried, tracked = True, None if listed is None else set(listed)
            if tracked is None and strict:
                raise CatalogError(f"{rel} is missing and Git cannot be queried at the repository root to confirm "
                                   "that it was never committed: restore it, or run where `git ls-files` works")
            if tracked is not None and rel in tracked:
                raise CatalogError(f"{rel} is tracked by Git but missing on disk: restore it")
            result[family] = manifest.empty_manifest(family)
            continue
        try:
            result[family] = manifest.load_manifest(root, family, missing_ok=False)
        except ValueError as error:
            raise CatalogError(f"{rel}: {error}") from None
    return result


def source_records(manifests: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict], list[dict], list[dict]]:
    """``(sources, asset records with a "family" key, failures)`` across the families, with unique IDs."""
    sources: list[dict] = []
    records: list[dict] = []
    failures: list[dict] = []
    for family in schema.FAMILIES:
        value = manifests[family]
        sources.extend(dict(source) for source in value["sources"])
        records.extend({**asset, "family": family} for asset in value["assets"])
        failures.extend(dict(failure) for failure in value["failures"])
    for kind, items in (("source", sources), ("asset record", records), ("failure", failures)):
        duplicates = sorted(key for key, count in Counter(item["id"] for item in items).items() if count > 1)
        if duplicates:
            raise CatalogError(f"duplicate {kind} ID across manifests: {duplicates[0]!r}")
    try:
        schema.check_source_slugs(source["id"] for source in sources)
    except ValueError as error:
        raise CatalogError(str(error)) from None
    known = {source["id"] for source in sources}
    for record in records:
        if record["source_id"] not in known:
            raise CatalogError(f"asset {record['id']!r}: source {record['source_id']!r} is not declared")
    return sources, records, failures


# --- Titles -----------------------------------------------------------------------------------------------------------

def _evidence_items(record: Mapping[str, Any]) -> list[Any]:
    return [*(record.get("mapping_evidence") or []), *(record.get("credit_evidence") or [])]


def _evidence_texts(record: Mapping[str, Any]) -> list[str]:
    """Every string the record's mapping evidence, credit evidence and notes carry."""
    texts: list[str] = []
    for item in _evidence_items(record):
        values = item.values() if isinstance(item, dict) else [item]
        texts.extend(value for value in values if isinstance(value, str))
    if isinstance(record.get("notes"), str):
        texts.append(record["notes"])
    return texts


def _quoted_in_evidence(record: Mapping[str, Any], text: str) -> bool:
    """The record's evidence quotes ``text`` verbatim: an evidence ``quote`` that is exactly ``text``, or ``text``
    exactly between quotation marks in any evidence string or the notes (a recorded caption such as ``'A_O'``)."""
    if any(isinstance(item, dict) and item.get("quote") == text for item in _evidence_items(record)):
        return True
    quoted = [f"{left}{text}{right}" for left, right in QUOTE_PAIRS]
    return any(form in value for value in _evidence_texts(record) for form in quoted)


def comparable_name(text: str) -> str:
    """Comparison form of a title or file name: NFKC, casefolded, ``_`` and ``-`` read as spaces, whitespace
    collapsed. 'IMG 0042', 'img_0042' and 'IMG-0042' all compare equal to the stem 'IMG_0042'."""
    folded = unicodedata.normalize("NFKC", text).casefold().replace("_", " ").replace("-", " ")
    return " ".join(folded.split())


def _confirmed_song_title(record: Mapping[str, Any]) -> str | None:
    """The one song a wiki song_art record is confirmed to show (confirmed_wiki or stronger), else None."""
    songs = schema.ordered_union([record.get("song_titles") or []])
    if record["kind"] == "song_art" and record.get("mapping_status") in CONFIRMED_STATUSES and len(songs) == 1:
        return songs[0]
    return None


def title_problem(record: Mapping[str, Any]) -> str | None:
    """Why the record's title breaks the title rule, else None (09 "Catalog presentation rules", Titles: a
    human-readable name; the upstream file name stays in ``original_filename``). Checked by --verify:

    * a title equal to one of the record's exact ``song_titles`` always passes;
    * wikis: song art confirmed (``confirmed_wiki`` or stronger) to show exactly one song carries that song's exact
      title;
    * any family: the title is the upstream file name with its extension, compared ignoring case, ``_``/``-`` and
      spacing (``IMG_0042.JPG``, ``img 0042.jpg`` for ``IMG_0042.jpg``);
    * artists, official, archives: the title is the file stem, compared the same way (``IMG 0042``, ``img_0042``),
      since their titles come from the post or page text they cite;
    * wikis: the title keeps the ``_`` of the stem (case ignored), which the rule's fallback writes as spaces.

    The stem checks pass when the record's evidence quotes the title verbatim (see _quoted_in_evidence): a post,
    page or wiki caption that really is that text (the wiki captions ``File:A_O.jpg`` as ``'A_O'``). A wiki title
    equal to a stem without ``_`` is the rule's own fallback (``File:SummerIslandBG.png`` gives ``SummerIslandBG``)."""
    title, filename = record["title"].strip(), record["original_filename"]
    if title in (record.get("song_titles") or ()):
        return None
    family = record.get("family") or schema.source_family(record["source_id"])
    song = _confirmed_song_title(record) if family == "wikis" else None
    if song is not None:
        return (f"title {title!r} is not the exact title {song!r} of the one song this wiki song art is confirmed "
                f"({record['mapping_status']}) to show")
    name = re.split(r"[\\/]", filename)[-1]
    stem, wanted = paths.filename_stem(filename), comparable_name(title)
    if stem != name and wanted in (comparable_name(name), comparable_name(filename)):
        return f"title {title!r} is the upstream file name {filename!r} (original_filename)"
    if wanted != comparable_name(stem) or _quoted_in_evidence(record, title):
        return None
    if family in TEXT_TITLED_FAMILIES:
        return (f"title {title!r} is the stem of original_filename {filename!r}; {family} titles come from the post "
                "or page text they cite (quote it in the record's evidence when it really is that text)")
    if "_" in title:
        return (f"title {title!r} is the File: name with underscores (original_filename {filename!r}); use the "
                "mapped song's title, the wiki caption (quoted in evidence), or the File: title with spaces")
    return None


def check_titles(records: Iterable[Mapping[str, Any]]) -> None:
    """Fail when any record breaks the title rule (see title_problem)."""
    found = [f"{record['id']}: {problem}" for record in records if (problem := title_problem(record))]
    if found:
        more = f"\n... and {len(found) - MAX_REPORTED} more" if len(found) > MAX_REPORTED else ""
        raise CatalogError(f"{len(found)} record(s) break the title rule: a title is a human-readable name, never the "
                           "bare upstream file name, which stays in original_filename (09-frozen-contracts.md, "
                           "Catalog presentation rules, Titles):\n" + "\n".join(found[:MAX_REPORTED]) + more)


# --- Archived files ---------------------------------------------------------------------------------------------------

def check_record_file(root: Path, record: Mapping[str, Any], verify: bool) -> None:
    """The record's file is a regular file inside ``assets/public/<family>/`` (no symlinked component) with the
    recorded size; with ``verify`` its bytes, hashes, sniffed format and decoded dimensions match the record."""
    if verify:
        media.verify_asset_file(root, record)
        return
    try:
        file = paths.safe_path(root, record["path"], under=f"{paths.ASSET_ROOT}/{record['family']}")
    except ValueError as error:
        raise CatalogError(f"{record['id']}: unsafe path: {error}") from None
    if not file.is_file():
        raise CatalogError(f"{record['id']}: missing file {record['path']}")
    if file.stat().st_size != record["bytes"]:
        raise CatalogError(f"{record['id']}: {record['path']} has {file.stat().st_size} bytes, the record says "
                           f"{record['bytes']}")


def default_workers() -> int:
    return min(8, os.cpu_count() or 1)


def check_files(root: Path, records: list[dict], verify: bool, workers: int | None = None) -> None:
    """Check every record's file (decoding in parallel: Pillow releases the GIL); report problems in record order."""
    def problem(record: dict) -> str | None:
        try:
            check_record_file(root, record, verify)
        except (ValueError, OSError) as error:
            message = str(error)
            return message if message.startswith(record["id"] + ":") else f"{record['id']}: {message}"
        return None

    with ThreadPoolExecutor(workers or default_workers()) as pool:
        found = [message for message in pool.map(problem, records) if message]
    if found:
        more = f"\n... and {len(found) - MAX_REPORTED} more" if len(found) > MAX_REPORTED else ""
        raise CatalogError("archived files do not match their records:\n" + "\n".join(found[:MAX_REPORTED]) + more)


def orphan_files(root: Path, referenced: set[str]) -> list[str]:
    """Files under assets/public/ that no record names (sorted). Dot-files (interrupted downloads, Git metadata) are
    not orphans; a symlink anywhere below assets/public/ is an error."""
    base = Path(root) / paths.ASSET_ROOT
    if base.is_symlink():
        raise CatalogError(f"{paths.ASSET_ROOT} is a symlink")
    if not base.is_dir():
        return []
    found = []
    for directory, dirnames, filenames in os.walk(base):
        for name in dirnames + filenames:
            if (Path(directory) / name).is_symlink():
                rel = (Path(directory) / name).relative_to(root).as_posix()
                raise CatalogError(f"symlink below {paths.ASSET_ROOT}: {rel}")
        dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
        for name in filenames:
            if name.startswith("."):
                continue
            rel = (Path(directory) / name).relative_to(root).as_posix()
            if rel not in referenced:
                found.append(rel)
    return sorted(found)


# --- Catalog ----------------------------------------------------------------------------------------------------------

def needs_preview(asset: Mapping[str, Any]) -> bool:
    """A derived preview is needed for every archive-tier raster (Pages does not serve its original) and for gallery
    images whose long edge exceeds PREVIEW_LONG_EDGE. PDFs never get one."""
    if asset["format"] not in schema.RASTER_FORMATS:
        return False
    if schema.tier_for(asset["kind"], asset["format"]) == "archive":
        return True
    return schema.is_gallery(asset) and max(asset["width"], asset["height"]) > PREVIEW_LONG_EDGE


def provenance_entry(record: Mapping[str, Any], names: Mapping[str, str]) -> dict:
    """One source record as a catalog provenance entry (contract fields; absent optional fields are null)."""
    entry = {"asset_id": record["id"], "source_id": record["source_id"], "source_name": names[record["source_id"]],
             "family": record["family"]}
    for field in schema.PROVENANCE_FIELDS[3:]:
        entry[field] = record.get(field)
    return entry


def _group_by_sha(records: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(record["sha256"], []).append(record)
    for sha256, group in groups.items():
        for field in ("bytes", "sha1", "format", "width", "height", "kind"):  # one file, one kind (09, wave 4)
            values = {json.dumps(record[field]) for record in group}
            if len(values) > 1:
                ids = ", ".join(record["id"] for record in group)
                raise CatalogError(f"records with SHA-256 {sha256} disagree on {field}: {ids}")
    return groups


def catalog_entry(group: list[dict], names: Mapping[str, str]) -> dict:
    """One deduplicated catalog asset from the records sharing a SHA-256: the primary record's fields (title, path,
    URL included) plus every record as provenance, primary first (schema.provenance_sort_key)."""
    ordered = sorted(group, key=schema.provenance_sort_key)
    primary = ordered[0]
    entry = {key: value for key, value in primary.items() if key != "family"}
    for field in MERGED_FIELDS:
        entry[field] = schema.ordered_union(record[field] for record in ordered)
    url = schema.catalog_url(primary["path"])
    entry.update({
        "family": primary["family"], "source_name": names[primary["source_id"]], "url": url, "thumb": None,
        "gallery": schema.is_gallery(primary), "slideshow": schema.is_slideshow(primary),
        "site_original": schema.tier_for(primary["kind"], primary["format"]) == "site",
        "archive_url": schema.ARCHIVE_URL_PREFIX + url,
        "provenance": [provenance_entry(record, names) for record in ordered], "variants": [],
    })
    return entry


def _with_variants(assets: list[dict]) -> list[dict]:
    groups: dict[str, list[str]] = {}
    for asset in assets:
        if "variant_group" in asset:
            groups.setdefault(asset["variant_group"], []).append(asset["id"])
    return [{**asset, "variants": sorted(other for other in groups.get(asset.get("variant_group") or "", [])
                                         if other != asset["id"])} for asset in assets]


def summarize(sources: list[dict], assets: list[dict], failures: list[dict]) -> dict:
    """Catalog summary; ``by_family`` counts source records and ``by_kind`` unique files (zeros kept, contract
    order)."""
    records = [record for asset in assets for record in asset["provenance"]]
    families = Counter(record["family"] for record in records)
    kinds = Counter(asset["kind"] for asset in assets)
    total = sum(asset["bytes"] for asset in assets)
    site = sum(asset["bytes"] for asset in assets if asset["site_original"])
    return {
        "source_count": len(sources), "source_asset_records": len(records), "unique_files": len(assets),
        "gallery_images": sum(asset["gallery"] for asset in assets),
        "slideshow_images": sum(asset["slideshow"] for asset in assets),
        "exact_duplicate_records": len(records) - len(assets),
        "by_family": {family: families.get(family, 0) for family in schema.FAMILIES},
        "by_kind": {kind: kinds.get(kind, 0) for kind in schema.KINDS},
        "failure_count": len(failures), "downloaded_bytes": total, "site_original_bytes": site,
        "archive_only_bytes": total - site,
    }


def combine(root: Path, *, verify: bool = False, manifests: Mapping[str, Mapping[str, Any]] | None = None,
            workers: int | None = None) -> dict:
    """The catalog without previews (``thumb`` null): validated inputs, checked files, SHA-256 dedup, provenance,
    variants, deterministic order and summary. ``verify`` also rejects records titled with their file name,
    re-hashes/decodes every file and rejects orphans."""
    root = Path(root)
    manifests = load_manifests(root) if manifests is None else manifests
    sources, records, failures = source_records(manifests)
    if verify:
        check_titles(records)
    check_files(root, records, verify, workers)
    if verify:
        orphans = orphan_files(root, {record["path"] for record in records})
        if orphans:
            shown = ", ".join(orphans[:MAX_REPORTED]) + (" ..." if len(orphans) > MAX_REPORTED else "")
            raise CatalogError(f"{len(orphans)} file(s) under {paths.ASSET_ROOT}/ named by no manifest record: {shown}")
    names = {source["id"]: source["name"] for source in sources}
    assets = [catalog_entry(group, names) for group in _group_by_sha(records).values()]
    assets = sorted(_with_variants(assets), key=schema.catalog_sort_key)
    assets = [schema.canonical(asset, "catalog_asset") for asset in assets]
    counts = Counter(record["source_id"] for record in records)
    catalog_sources = []
    for source in sorted(sources, key=lambda item: item["id"]):
        if "asset_count" in source and source["asset_count"] != counts[source["id"]]:
            raise CatalogError(f"source {source['id']!r}: asset_count {source['asset_count']} but the manifest has "
                               f"{counts[source['id']]} records")
        catalog_sources.append(schema.canonical({**source, "asset_count": counts[source["id"]]}, "source"))
    failures = sorted(failures, key=lambda failure: failure["id"])
    return {
        "schema_version": schema.SCHEMA_VERSION, "owner": schema.OWNER, "game": schema.CATALOG_GAME,
        "repository": schema.REPOSITORY,
        "snapshots": {family: manifests[family]["fetched_at"] for family in schema.FAMILIES},
        "sources": catalog_sources, "assets": assets, "failures": failures,
        "summary": summarize(catalog_sources, assets, failures),
    }


# --- Previews ---------------------------------------------------------------------------------------------------------

def _thumb_entry_problem(sha256: str, entry: Any) -> str | None:
    if not _HEX64.fullmatch(sha256):
        return f"key {sha256!r} is not a lowercase SHA-256"
    if not isinstance(entry, dict) or sorted(entry) != sorted(THUMB_ENTRY_FIELDS):
        return f"{sha256}: entry must have exactly the fields {', '.join(THUMB_ENTRY_FIELDS)}"
    if entry["path"] != schema.thumb_relpath(sha256):
        return f"{sha256}: path must be {schema.thumb_relpath(sha256)!r}, not {entry['path']!r}"
    for field in ("width", "height", "bytes"):
        if not isinstance(entry[field], int) or isinstance(entry[field], bool) or entry[field] < 1:
            return f"{sha256}: {field} must be a positive integer"
    if not isinstance(entry["sha256"], str) or not _HEX64.fullmatch(entry["sha256"]):
        return f"{sha256}: sha256 must be a lowercase SHA-256"
    return None


def load_thumbs(root: Path) -> dict | None:
    """data/thumbs.json validated (None when absent): ``schema_version`` 1, ``description``, ``encoder`` and
    ``thumbnails`` keyed by the original's full SHA-256, each entry naming ``assets/thumbs/<sha256[:16]>.webp``."""
    target = paths.safe_path(root, THUMBS_MANIFEST)
    if not target.exists():
        return None
    try:
        value = manifest.load_json(target)
    except ValueError as error:
        raise CatalogError(f"{THUMBS_MANIFEST}: {error}") from None
    if not isinstance(value, dict) or sorted(value) != ["description", "encoder", "schema_version", "thumbnails"]:
        raise CatalogError(f"{THUMBS_MANIFEST}: needs exactly schema_version, description, encoder, thumbnails")
    if value["schema_version"] != schema.SCHEMA_VERSION or not isinstance(value["encoder"], dict) \
            or not isinstance(value["thumbnails"], dict):
        raise CatalogError(f"{THUMBS_MANIFEST}: schema_version 1 with an encoder object and a thumbnails object")
    for sha256, entry in value["thumbnails"].items():
        problem = _thumb_entry_problem(sha256, entry)
        if problem:
            raise CatalogError(f"{THUMBS_MANIFEST}: {problem}")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def attach_thumbnails(root: Path, catalog: Mapping[str, Any], *, verify: bool = False,
                      required: bool = True) -> dict:
    """A copy of the catalog whose entries that need a preview point at it (``thumb``). The repository build requires
    data/thumbs.json and a current preview for every such entry; ``required=False`` (a fixture without previews)
    leaves ``thumb`` null when the manifest is absent. ``url`` always stays the original."""
    thumbs = load_thumbs(root)
    if thumbs is None:
        if required:
            raise CatalogError(f"Missing required input: {THUMBS_MANIFEST}: run scripts/build_thumbnails.py")
        return dict(catalog)
    assets = []
    for asset in catalog["assets"]:
        if not needs_preview(asset):
            assets.append(asset)
            continue
        entry = thumbs["thumbnails"].get(asset["sha256"])
        if entry is None:
            raise CatalogError(f"{THUMBS_MANIFEST} has no preview for {asset['path']}: run "
                               "scripts/build_thumbnails.py")
        try:
            file = paths.safe_path(root, entry["path"], under=schema.THUMB_DIR)
        except ValueError as error:
            raise CatalogError(f"{THUMBS_MANIFEST}: {error}") from None
        if not file.is_file() or file.stat().st_size != entry["bytes"]:
            raise CatalogError(f"Missing or stale thumbnail {entry['path']}: run scripts/build_thumbnails.py")
        if verify and file_sha256(file) != entry["sha256"]:
            raise CatalogError(f"Thumbnail SHA-256 mismatch: {entry['path']}")
        assets.append({**asset, "thumb": schema.thumb_relpath(asset["sha256"])})
    return {**catalog, "assets": assets}


# --- Outputs ----------------------------------------------------------------------------------------------------------

def catalog_script(catalog: Mapping[str, Any]) -> str:
    """``window.DYNAMIX_CATALOG = <JSON>;``: compact JSON in which every ``<`` (so ``</script>`` and ``<!--``) and
    U+2028/U+2029 are written as ``\\u`` escapes; the value parses back to the same catalog."""
    payload = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    payload = payload.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return f"{JS_GLOBAL} = {payload};\n"


def load_renderer():
    """``render_slideshow`` from scripts/render_slideshow.py (UI-owned), imported only when index.html is built."""
    try:
        from render_slideshow import render_slideshow  # type: ignore[import-not-found]
    except ImportError as error:
        raise CatalogError(f"index.html needs scripts/render_slideshow.py (render_slideshow): {error}") from None
    return render_slideshow


def render_index(root: Path, catalog: Mapping[str, Any], *, required: bool = True) -> str | None:
    """index.html from templates/slideshow.html; None when the template is absent and not ``required``."""
    template = paths.safe_path(root, TEMPLATE)
    if not template.is_file():
        if required:
            raise CatalogError(f"Missing required input: {TEMPLATE}")
        return None
    text = load_renderer()(Path(root), catalog)
    if not isinstance(text, str) or "\r" in text:
        raise CatalogError("render_slideshow must return text without carriage returns")
    return text


def render_outputs(root: Path, catalog: Mapping[str, Any], *, index_required: bool = True) -> dict[str, str]:
    """Every generated file as text, keyed by its repository-relative path (index.html only when rendered)."""
    outputs = {CATALOG_JSON: manifest.dumps(catalog), CATALOG_JS: catalog_script(catalog)}
    index = render_index(root, catalog, required=index_required)
    if index is not None:
        outputs[INDEX_HTML] = index
    return outputs


def stale_outputs(root: Path, outputs: Mapping[str, str]) -> list[str]:
    """Outputs that are missing or differ from the regenerated text. A file checked out with CRLF line ends matches;
    any other difference, including a lone CR, does not."""
    stale = []
    for rel, content in outputs.items():
        target = paths.safe_path(root, rel)
        try:
            current = target.read_bytes().decode("utf-8").replace("\r\n", "\n") if target.is_file() else None
        except UnicodeDecodeError:
            current = None
        if current != content:
            stale.append(rel)
    return stale


def write_text_atomic(root: Path, rel: str, content: str) -> None:
    """Write UTF-8 text with LF line ends through a temporary file in the target directory and ``os.replace``."""
    target = paths.safe_path(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o644)
        os.replace(name, target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def is_repository_root(root: Path) -> bool:
    return Path(root).resolve() == REPO_ROOT.resolve()


def build(root: Path, *, verify: bool = False, check: bool = False, workers: int | None = None) -> tuple[dict, list]:
    """Build (or with ``check`` only compare) the outputs; returns ``(catalog, stale output paths)``."""
    root = Path(root)
    strict = is_repository_root(root)
    catalog = combine(root, verify=verify, workers=workers)
    catalog = attach_thumbnails(root, catalog, verify=verify, required=strict)
    try:
        schema.validate_catalog(catalog)
    except ValueError as error:
        raise CatalogError(f"generated catalog violates the contract: {error}") from None
    outputs = render_outputs(root, catalog, index_required=strict)
    if not strict:
        for rel, reason in ((THUMBS_MANIFEST, "previews"), (TEMPLATE, INDEX_HTML)):
            if not (root / rel).exists():
                print(f"note: {rel} is absent under --root; {reason} skipped", file=sys.stderr)
    if check:
        return catalog, stale_outputs(root, outputs)
    for rel, content in outputs.items():
        write_text_atomic(root, rel, content)
    return catalog, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--verify", action="store_true", help="re-hash and decode every archived file and preview, "
                                                              "and reject orphans under assets/public/")
    parser.add_argument("--check", action="store_true", help="compare regenerated outputs without writing")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository-shaped tree (default: %(default)s)")
    parser.add_argument("--workers", type=int, help="files decoded in parallel by --verify (default: up to 8)")
    args = parser.parse_args(argv)
    try:
        catalog, stale = build(args.root, verify=args.verify, check=args.check, workers=args.workers)
    except (CatalogError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if stale:
        for rel in stale:
            print(f"Out-of-date generated file: {rel}", file=sys.stderr)
        return 1
    print(json.dumps(catalog["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
