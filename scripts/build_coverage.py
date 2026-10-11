#!/usr/bin/env python3
"""Build data/coverage.json: reconciled source, candidate, artwork, song and byte accounting (schema version 1).

    python -I scripts/build_coverage.py [--check] [--root PATH]

Inputs: data/sources/song-index.json (the dated original-Dynamix denominator), the discovery snapshots
data/discovery/<source-slug>.json, the family manifests and the catalog as scripts/build_catalog.py builds it
(with data/thumbs.json when present; at the repository root data/thumbs.json and the index.html template are
required, as for build_catalog). Every metric is a separate, defined count; record, file, image and song counts are
never interchangeable. Song coverage counts confirmed mappings only; inferred- and conflict-only songs are reported
beside it. The build fails instead of writing when the inputs do not reconcile: per-source disposition totals,
candidate <-> asset/failure links, source counts, asset song IDs against the song index.
Nothing depends on the clock: the report is dated by the song index and the manifests' `fetched_at`.

--check   regenerate in memory and compare with data/coverage.json byte-for-byte (CRLF checkout accepted).
--root    another repository-shaped tree (tests, fixtures).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:  # `python -I` does not put the script's directory on sys.path
    sys.path.insert(0, str(SCRIPTS))

import build_catalog  # noqa: E402
from lib import manifest, paths, schema  # noqa: E402

REPO_ROOT = SCRIPTS.parent
COVERAGE_JSON = "data/coverage.json"
SONG_INDEX = "data/sources/song-index.json"
# Non-snapshot JSON that may sit in data/discovery/ (a worker's proposed song index).
DISCOVERY_EXTRAS = re.compile(r"^song-index(?:\.[a-z0-9-]+)?\.json$")
# Artwork that answers "does this song have art": the song's own illustration, shown in the gallery.
SONG_ART_KINDS = ("song_art",)
# Kinds whose missing illustrator credit is a gap (screenshots and references have no illustrator to credit).
ARTWORK_KINDS = tuple(
    kind for kind in schema.KINDS if kind not in ("store_screenshot", "gameplay_screenshot", "reference")
)
HIGH_RES_LONG_EDGE = 1920
LOW_RES_LONG_EDGE = 1000
LONG_EDGE_BUCKETS = ("lt_1000", "lt_1920", "ge_1920")
ORIGINAL_FAMILIES = ("artists", "official")
ORIGINAL_ORIGINS = ("artist_post", "official")
CONFIRMED_MAPPINGS = ("confirmed_primary", "confirmed_wiki")
RENDITIONS = ("site_original", "unknown", "preview")  # best first
# Rounds to settle data/coverage.json's own size in the staged estimate (it settles in two or three).
SIZE_ROUNDS = 10

DEFINITIONS = {
    "source_asset_records": "Asset records across the family manifests (one per source x file).",
    "unique_files": "Catalog entries: distinct SHA-256 values; exact duplicates across sources count once.",
    "exact_duplicate_records": "source_asset_records minus unique_files.",
    "song_denominator": "Songs of data/sources/song-index.json with in_original_game true (positive original-game "
    "evidence); Universe-only songs are outside it. Variants never multiply it.",
    "song_art": "A catalog gallery entry of kind song_art. It links a song only through its provenance records whose "
    "song_ids name that song, at their mapping_status; the merged song_ids alone link nothing. Confirmed "
    "song art: linked by a record whose mapping_status is confirmed_primary or confirmed_wiki.",
    "with_any_artwork": "Denominator songs with confirmed song art. Every per-song size, rendition, originality and "
    "illustrator metric below counts confirmed song art only.",
    "inferred_only": "Denominator songs without confirmed song art whose song_art links are all inferred.",
    "conflict_only": "Denominator songs without confirmed song art with at least one song_art link whose "
    "mapping_status is conflict (any other link inferred).",
    "missing_art": "Denominator songs without any song_art link. with_any_artwork + inferred_only + conflict_only + "
    "missing_art = denominator.",
    "with_any_linked_asset": "Denominator songs named by any catalog entry of any kind and any mapping status "
    "(screenshots included).",
    "with_confirmed_original": "Denominator songs with song art carrying one provenance record that is from the "
    "artists or official family, origin_status artist_post/official, mapping_status "
    "confirmed_primary naming the song, rendition site_original. Not a claim of an artist "
    "master.",
    "with_long_edge_ge_1920": "Denominator songs with confirmed song art whose decoded long edge is at least 1920 px; "
    "a size bucket only, not evidence of authenticity, full composition or a master.",
    "low_resolution_only": "with_any_artwork songs whose confirmed song art all have a long edge under 1920 px. "
    "best_long_edge buckets the songs by their largest confirmed song art (<1000, <1920, "
    ">=1920 px); a size bucket only.",
    "rendition": "Per provenance record: site_original when quality.source_variant is site_original and the record "
    "is not transformed; preview when source_variant is preview or transformed is true; else unknown.",
    "with_site_original": "with_any_artwork songs with confirmed song art that has a site_original record (the file "
    "as the source serves it at full size; not a claim of an artist master).",
    "variant_unknown": "with_any_artwork songs without a site_original record but with an unknown one.",
    "preview_only": "with_any_artwork songs whose confirmed song art records are all previews or platform transforms. "
    "with_site_original + variant_unknown + preview_only = with_any_artwork.",
    "unknown_illustrator": "with_any_artwork songs whose confirmed song art all have an empty illustrators list.",
    "unknown_illustrator_assets": "Catalog entries of artwork kinds (not screenshots or references) whose merged "
    "illustrators list is empty.",
    "unmapped_assets": "Catalog entries of artwork kinds whose provenance records are all unmapped.",
    "conflict_assets": "Catalog entries with at least one provenance record whose mapping_status is conflict.",
    "inferred_only_assets": "Catalog entries with an inferred record and no confirmed one (an entry that also has "
    "a conflict record is listed in conflict_assets too).",
    "source_reported_mismatches": "Records where an advertised SHA-1, byte count, size or MIME differs from the "
    "saved bytes (comparison false); null comparisons are not mismatches.",
    "staged_site_estimate": "What scripts/prepare_pages.py stages, counted the same way: one original per SHA-256 (the "
    "file named by the url of each catalog entry with site_original true; identical copies "
    "under other sources' paths and archive-tier originals stay out), each distinct preview "
    "named by a thumb, and the generated data/catalog.json, data/catalog.js, index.html (null "
    "when it is not rendered: no template outside the repository root) and data/coverage.json "
    "(this file, LF line ends). It excludes the static page files prepare_pages also stages "
    "(archive.html, README*, LICENSE, NOTICE, .nojekyll, assets/site/, docs/, licenses/, src/, "
    "...), so editing them never makes this report stale; prepare_pages enforces the exact "
    "budget.",
    "successor_not_primary": "Records whose superseded_by names a record that is not the primary (catalog ID) of "
    "its catalog entry; catalog_entry is the entry holding the successor's bytes.",
}


class CoverageError(ValueError):
    """Inputs that do not reconcile or violate the contract; data/coverage.json is not written."""


# --- Inputs -----------------------------------------------------------------------------------------------------------


def load_song_index(root: Path, record_ids: Iterable[str], records: Iterable[Mapping[str, Any]]) -> dict:
    target = paths.safe_path(root, SONG_INDEX)
    if not target.is_file():
        raise CoverageError(f"Missing required input: {SONG_INDEX}")
    try:
        index = manifest.load_json(target)
        schema.validate_song_index(index, record_ids)
        schema.validate_song_links(index, records)
    except ValueError as error:
        raise CoverageError(f"{SONG_INDEX}: {error}") from None
    return index


def load_snapshots(root: Path, sources: list[dict]) -> dict[str, dict]:
    """Validated discovery snapshots of declared sources; a snapshot file of an undeclared source fails."""
    snapshots, expected = {}, {}
    for source in sources:
        rel = paths.discovery_relpath(source["id"])
        expected[rel] = source["id"]
        try:
            snapshot = manifest.load_discovery(root, source["id"])
        except ValueError as error:
            raise CoverageError(f"{rel}: {error}") from None
        if snapshot is not None:
            snapshots[source["id"]] = snapshot
    directory = paths.safe_path(root, paths.DISCOVERY_ROOT)
    if directory.is_dir():
        for file in sorted(directory.glob("*.json")):
            rel = f"{paths.DISCOVERY_ROOT}/{file.name}"
            if rel not in expected and not DISCOVERY_EXTRAS.fullmatch(file.name):
                raise CoverageError(f"{rel} is not the discovery snapshot of a source declared in a family manifest")
    return snapshots


def reconcile(
    manifests: Mapping[str, Mapping[str, Any]], snapshots: Mapping[str, Mapping[str, Any]], records: list[dict]
) -> dict[str, dict[str, int]]:
    """Per-source disposition counts after schema.reconcile; any disagreement fails."""
    counts: dict[str, dict[str, int]] = {}
    for family in schema.FAMILIES:
        own = {
            source_id: snapshot
            for source_id, snapshot in snapshots.items()
            if schema.source_family(source_id) == family
        }
        try:
            counts.update(schema.reconcile(manifests[family], own, other_assets=records))
        except ValueError as error:
            raise CoverageError(
                f"{paths.manifest_relpath(family)} does not reconcile with its discovery snapshots: {error}"
            ) from None
    for source_id, value in counts.items():
        total = len(snapshots[source_id]["candidates"])
        if sum(value.values()) != total:
            raise CoverageError(f"{source_id}: dispositions sum to {sum(value.values())}, not {total} candidates")
    failures = Counter(failure["source_id"] for family in schema.FAMILIES for failure in manifests[family]["failures"])
    for family in schema.FAMILIES:
        for source in manifests[family]["sources"]:
            if source["id"] in snapshots:
                continue
            if "candidate_count" in source or source.get("discovery_complete") is True:
                raise CoverageError(
                    f"source {source['id']!r} declares candidate_count/discovery_complete without a "
                    f"discovery snapshot ({paths.discovery_relpath(source['id'])})"
                )
            if "failure_count" in source and source["failure_count"] != failures[source["id"]]:
                raise CoverageError(
                    f"source {source['id']!r}: failure_count {source['failure_count']} but the "
                    f"manifest has {failures[source['id']]} failures"
                )
    return counts


# --- Metrics ----------------------------------------------------------------------------------------------------------


def _ordered_counts(values: Iterable[str], order: Iterable[str]) -> dict[str, int]:
    counter = Counter(values)
    result = {key: counter.pop(key, 0) for key in order}
    result.update(sorted(counter.items()))
    return result


def _reason(candidate: Mapping[str, Any]) -> str:
    return candidate["reason"].split(":", 1)[0].strip()


def source_metrics(
    sources: list[dict],
    snapshots: Mapping[str, dict],
    counts: Mapping[str, dict],
    records: list[dict],
    failures: list[dict],
    catalog: Mapping[str, Any],
) -> dict:
    record_counts = Counter(record["source_id"] for record in records)
    primary_counts = Counter(asset["source_id"] for asset in catalog["assets"])
    rows: list[dict] = []
    enumeration: dict[str, list[str]] = {"complete": [], "incomplete": [], "not_enumerated": []}
    for source in sorted(sources, key=lambda item: item["id"]):
        source_id, snapshot = source["id"], snapshots.get(source["id"])
        own_failures = [failure for failure in failures if failure["source_id"] == source_id]
        row = {
            "id": source_id,
            "family": source["family"],
            "name": source["name"],
            "status": source["status"],
            "evidence_level": source.get("evidence_level"),
            "discovery": paths.discovery_relpath(source_id) if snapshot else None,
            "discovery_complete": snapshot["discovery_complete"] if snapshot else None,
            "open_continuations": sorted(name for name, token in snapshot["continuation"].items() if token is not None)
            if snapshot
            else None,
            "discovery_errors": len(snapshot["errors"]) if snapshot else None,
            "candidates": len(snapshot["candidates"]) if snapshot else None,
            "by_disposition": counts.get(source_id),
            "exclusion_reasons": _ordered_counts(
                (_reason(c) for c in snapshot["candidates"] if c["disposition"] == "excluded"), ()
            )
            if snapshot
            else None,
            "asset_records": record_counts[source_id],
            "primary_unique_files": primary_counts[source_id],
            "failures": len(own_failures),
            "failures_by_class": _ordered_counts((f["error_class"] for f in own_failures), ()),
        }
        rows.append(row)
        state = "not_enumerated" if snapshot is None else "complete" if snapshot["discovery_complete"] else "incomplete"
        enumeration[state].append(source_id)
    declared = {source["id"] for source in sources}
    return {
        "total": len(sources),
        "by_family": _ordered_counts((source["family"] for source in sources), schema.FAMILIES),
        "by_status": _ordered_counts((source["status"] for source in sources), schema.SOURCE_STATUSES),
        "enumeration": enumeration,
        "registered_without_record": sorted(set(schema.REGISTERED_SOURCE_IDS) - declared),
        "records": rows,
    }


def candidate_metrics(snapshots: Mapping[str, dict], counts: Mapping[str, dict]) -> dict:
    candidates = [candidate for snapshot in snapshots.values() for candidate in snapshot["candidates"]]
    by_disposition = {
        disposition: sum(value[disposition] for value in counts.values()) for disposition in schema.DISPOSITIONS
    }
    if sum(by_disposition.values()) != len(candidates):
        raise CoverageError("candidate dispositions do not reconcile with the candidate total")
    return {
        "total": len(candidates),
        "by_disposition": by_disposition,
        "by_exclusion_reason": _ordered_counts(
            (_reason(c) for c in candidates if c["disposition"] == "excluded"), schema.EXCLUSION_REASONS
        ),
        "by_game": _ordered_counts((c["game"] for c in candidates), schema.GAMES),
    }


def _long_edge(asset: Mapping[str, Any]) -> int:
    """Decoded long edge of a raster entry (callers pass gallery entries only; PDFs have no size)."""
    return max(asset["width"], asset["height"])


def _edge_bucket(edge: int) -> str:
    return "lt_1000" if edge < LOW_RES_LONG_EDGE else "lt_1920" if edge < HIGH_RES_LONG_EDGE else "ge_1920"


def asset_metrics(catalog: Mapping[str, Any], records: list[dict], previews: bool) -> dict:
    assets, summary = catalog["assets"], catalog["summary"]
    groups = Counter(asset["variant_group"] for asset in assets if "variant_group" in asset)
    buckets = Counter(_edge_bucket(_long_edge(asset)) for asset in assets if asset["gallery"])
    return {
        "source_asset_records": summary["source_asset_records"],
        "unique_files": summary["unique_files"],
        "exact_duplicate_records": summary["exact_duplicate_records"],
        "gallery_images": summary["gallery_images"],
        "slideshow_images": summary["slideshow_images"],
        "by_kind": summary["by_kind"],
        "by_format": _ordered_counts((asset["format"] for asset in assets), schema.FORMATS),
        "by_tier": {
            "site": sum(asset["site_original"] for asset in assets),
            "archive": sum(not asset["site_original"] for asset in assets),
        },
        "variant_groups": len(groups),
        "entries_in_variant_groups": sum(groups.values()),
        "entries_with_variants": sum(bool(asset["variants"]) for asset in assets),
        "records_by_upstream_status": _ordered_counts(
            (record.get("upstream_status") or "unset" for record in records), (*schema.UPSTREAM_STATUSES, "unset")
        ),
        "transformed_records": sum(record.get("transformed") is True for record in records),
        "gallery_long_edge": {key: buckets.get(key, 0) for key in LONG_EDGE_BUCKETS},
        "entries_with_preview": sum(asset["thumb"] is not None for asset in assets) if previews else None,
    }


def _rendition(entry: Mapping[str, Any], transformed: set[str]) -> str:
    """``site_original``, ``preview`` or ``unknown`` for one provenance record; a known platform transform (record
    ``transformed`` true) is a preview whatever its source_variant says."""
    return "preview" if entry["asset_id"] in transformed else entry["quality"]["source_variant"]


def _best_rendition(assets: list[dict], transformed: set[str]) -> str:
    """The best rendition (RENDITIONS order) among every provenance record of these entries."""
    found = {_rendition(entry, transformed) for asset in assets for entry in asset["provenance"]}
    return next(kind for kind in RENDITIONS if kind in found)


def _confirmed_original(asset: Mapping[str, Any], song_id: str, transformed: set[str]) -> bool:
    """One record of the entry is the artist's/official file for this song as the source serves it."""
    return any(
        entry["family"] in ORIGINAL_FAMILIES
        and entry["mapping_status"] == "confirmed_primary"
        and entry["quality"]["origin_status"] in ORIGINAL_ORIGINS
        and song_id in entry["song_ids"]
        and _rendition(entry, transformed) == "site_original"
        for entry in asset["provenance"]
    )


def _song_links(catalog: Mapping[str, Any], denominator: list[str]) -> tuple[dict[str, list], dict[str, set]]:
    """``(confirmed song art per song, unresolved link statuses per song)``. A song_art entry links a song through
    each provenance record naming it, never through the merged song_ids alone (one conflicting secondary record
    must not make a song covered)."""
    confirmed: dict[str, list[dict]] = {song_id: [] for song_id in denominator}
    unresolved: dict[str, set[str]] = {song_id: set() for song_id in denominator}
    for asset in catalog["assets"]:
        if not (asset["gallery"] and asset["kind"] in SONG_ART_KINDS):
            continue
        for song_id in asset["song_ids"]:
            if song_id not in confirmed:
                continue
            statuses = {entry["mapping_status"] for entry in asset["provenance"] if song_id in entry["song_ids"]}
            if statuses & set(CONFIRMED_MAPPINGS):
                confirmed[song_id].append(asset)
            unresolved[song_id] |= statuses & {"inferred", "conflict"}
    return confirmed, unresolved


def song_metrics(index: Mapping[str, Any], catalog: Mapping[str, Any], transformed: set[str]) -> tuple[dict, dict]:
    """``(songs section, song gaps)`` computed from per-record song_ids and mapping statuses, never from titles.
    ``transformed``: IDs of records whose stored bytes are a known platform transform."""
    songs = index["songs"]
    denominator = [song["id"] for song in songs if song["in_original_game"]]
    inside = set(denominator)
    linked = {song_id for asset in catalog["assets"] for song_id in asset["song_ids"]}
    art, unresolved = _song_links(catalog, denominator)
    with_art = [s for s in denominator if art[s]]
    conflict_only = [s for s in denominator if not art[s] and "conflict" in unresolved[s]]
    inferred_only = [s for s in denominator if not art[s] and unresolved[s] == {"inferred"}]
    missing = [s for s in denominator if not art[s] and not unresolved[s]]
    best_edge = {s: _edge_bucket(max(_long_edge(asset) for asset in art[s])) for s in with_art}
    rendition = {s: _best_rendition(art[s], transformed) for s in with_art}
    confirmed = [s for s in with_art if any(_confirmed_original(asset, s, transformed) for asset in art[s])]
    high = [s for s in with_art if best_edge[s] == "ge_1920"]
    low = [s for s in with_art if best_edge[s] != "ge_1920"]
    by_rendition = {kind: [s for s in with_art if rendition[s] == kind] for kind in RENDITIONS}
    unknown_illustrator = [s for s in with_art if all(not asset["illustrators"] for asset in art[s])]
    if len(with_art) + len(inferred_only) + len(conflict_only) + len(missing) != len(denominator) or sum(
        map(len, by_rendition.values())
    ) != len(with_art):
        raise CoverageError("song coverage buckets do not reconcile with the denominator")
    by_id = {song["id"]: song for song in songs}
    section = {
        "index_as_of": index["as_of"],
        "index_songs": len(songs),
        "denominator": len(denominator),
        "outside_denominator": len(songs) - len(denominator),
        "denominator_removed": sum(by_id[s]["removed"] for s in denominator),
        "denominator_universe_also": sum(by_id[s]["universe_also"] for s in denominator),
        "with_any_artwork": len(with_art),
        "inferred_only": len(inferred_only),
        "conflict_only": len(conflict_only),
        "missing_art": len(missing),
        "with_any_linked_asset": len(inside & linked),
        "with_confirmed_original": len(confirmed),
        "with_long_edge_ge_1920": len(high),
        "low_resolution_only": len(low),
        "best_long_edge": {key: sum(value == key for value in best_edge.values()) for key in LONG_EDGE_BUCKETS},
        "with_site_original": len(by_rendition["site_original"]),
        "variant_unknown": len(by_rendition["unknown"]),
        "preview_only": len(by_rendition["preview"]),
        "unknown_illustrator": len(unknown_illustrator),
    }
    gaps = {
        "missing_art_songs": missing,
        "inferred_only_songs": inferred_only,
        "conflict_only_songs": conflict_only,
        "low_resolution_only_songs": low,
        "preview_only_songs": by_rendition["preview"],
        "variant_unknown_songs": by_rendition["unknown"],
        "unknown_illustrator_songs": unknown_illustrator,
        "linked_outside_denominator": sorted(linked - inside),
    }
    return section, gaps


def asset_gaps(catalog: Mapping[str, Any], snapshots: Mapping[str, dict]) -> dict:
    artwork = [asset for asset in catalog["assets"] if asset["kind"] in ARTWORK_KINDS]
    candidates = [c for source_id in sorted(snapshots) for c in snapshots[source_id]["candidates"]]

    def statuses(asset: Mapping[str, Any]) -> set[str]:
        return {record["mapping_status"] for record in asset["provenance"]}

    return {
        "unknown_illustrator_assets": [asset["id"] for asset in artwork if not asset["illustrators"]],
        "unmapped_assets": [asset["id"] for asset in artwork if statuses(asset) == {"unmapped"}],
        "conflict_assets": [asset["id"] for asset in catalog["assets"] if "conflict" in statuses(asset)],
        "inferred_only_assets": [
            asset["id"]
            for asset in catalog["assets"]
            if "inferred" in statuses(asset) and not statuses(asset) & {"confirmed_primary", "confirmed_wiki"}
        ],
        "pending_candidates": [c["id"] for c in candidates if c["disposition"] == "pending"],
        "blocked_candidates": [c["id"] for c in candidates if c["disposition"] == "blocked"],
        "failed_candidates": [c["id"] for c in candidates if c["disposition"] == "failed"],
    }


def mismatches(
    index: Mapping[str, Any], records: list[dict], snapshots: Mapping[str, dict], catalog: Mapping[str, Any]
) -> dict:
    by_id = {record["id"]: record for record in records}
    entry_of = {entry["asset_id"]: asset["id"] for asset in catalog["assets"] for entry in asset["provenance"]}
    successors = [
        {
            "asset_id": record["id"],
            "superseded_by": record["superseded_by"],
            "catalog_entry": entry_of.get(record["superseded_by"]),
        }
        for record in sorted(records, key=lambda item: item["id"])
        if record.get("superseded_by") and entry_of.get(record["superseded_by"]) != record["superseded_by"]
    ]
    reported = []
    for record in sorted(records, key=lambda item: item["id"]):
        fields = [field for field, value in record["comparisons"].items() if value is False]
        if fields:
            reported.append({"asset_id": record["id"], "source_id": record["source_id"], "fields": fields})
    disagreements = []
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(record["sha256"], []).append(record)
    for sha256, group in sorted(groups.items()):
        for field in ("kind", "variant_group"):
            values = {record["id"]: record.get(field) for record in sorted(group, key=lambda item: item["id"])}
            if len(set(values.values())) > 1:
                disagreements.append({"sha256": sha256, "field": field, "values": values})
    artwork = []
    for song in index["songs"]:
        linked = sorted(record["id"] for record in records if song["id"] in record["song_ids"])
        listed = sorted(song["artwork_asset_ids"])
        if linked != listed:
            artwork.append(
                {
                    "song_id": song["id"],
                    "index_only": sorted(set(listed) - set(linked)),
                    "records_only": sorted(set(linked) - set(listed)),
                }
            )
    other_source = [
        candidate["id"]
        for source_id in sorted(snapshots)
        for candidate in snapshots[source_id]["candidates"]
        if candidate["disposition"] == "duplicate_bytes"
        and candidate.get("asset_id") in by_id
        and by_id[candidate["asset_id"]]["source_id"] != source_id
    ]
    return {
        "source_reported": reported,
        "transformed_records": sorted(record["id"] for record in records if record.get("transformed") is True),
        "record_disagreements": disagreements,
        "song_index_disagreements": [
            {"song_id": item["song_id"], "field": item["field"]} for item in index["disagreements"]
        ],
        "song_index_artwork": artwork,
        "duplicate_bytes_other_source": other_source,
        "successor_not_primary": successors,
    }


def generated_sizes(root: Path, catalog: Mapping[str, Any]) -> dict[str, int | None]:
    """Byte sizes of the generated site files build_catalog.py writes for this catalog (UTF-8, LF line ends):
    data/catalog.json, data/catalog.js and index.html (rendered like build_catalog does: required at the repository
    root, None elsewhere without templates/slideshow.html)."""
    index = build_catalog.render_index(root, catalog, required=build_catalog.is_repository_root(root))
    return {
        "catalog_json": len(manifest.dumps(catalog).encode("utf-8")),
        "catalog_script": len(build_catalog.catalog_script(catalog).encode("utf-8")),
        "index_html": None if index is None else len(index.encode("utf-8")),
    }


def staged_originals(catalog: Mapping[str, Any]) -> dict[str, int]:
    """``{url path: bytes}`` of the originals prepare_pages stages: the file each site-tier catalog entry names. The
    catalog holds one entry per SHA-256, so identical copies kept under other sources' paths are never counted."""
    return {asset["path"]: asset["bytes"] for asset in catalog["assets"] if asset["site_original"]}


def staged_previews(catalog: Mapping[str, Any], thumbs: Mapping[str, Any]) -> dict[str, int]:
    """``{preview path: bytes}`` of every distinct preview a catalog entry names (prepare_pages stages each once)."""
    return {
        asset["thumb"]: thumbs["thumbnails"][asset["sha256"]]["bytes"] for asset in catalog["assets"] if asset["thumb"]
    }


def byte_metrics(
    catalog: Mapping[str, Any],
    records: list[dict],
    thumbs: Mapping[str, Any] | None,
    generated: Mapping[str, int | None],
    coverage_bytes: int,
) -> dict:
    """Byte totals; ``generated`` from generated_sizes, ``coverage_bytes`` the size of data/coverage.json itself."""
    assets = catalog["assets"]
    site = sum(staged_originals(catalog).values())
    total = sum(asset["bytes"] for asset in assets)
    previews = None if thumbs is None else sum(staged_previews(catalog, thumbs).values())
    files = {**generated, "coverage_json": coverage_bytes}
    staged = site + (previews or 0) + sum(size for size in files.values() if size is not None)
    return {
        "records_by_family": {
            family: sum(r["bytes"] for r in records if r["family"] == family) for family in schema.FAMILIES
        },
        "unique_by_primary_family": {
            family: sum(a["bytes"] for a in assets if a["family"] == family) for family in schema.FAMILIES
        },
        "unique_total": total,
        "by_tier": {"site": site, "archive": total - site},
        "previews": previews,
        **files,
        "staged_site_estimate": staged,
        "pages_budget": schema.PAGES_BUDGET_BYTES,
        "within_pages_budget": staged <= schema.PAGES_BUDGET_BYTES,
    }


def with_byte_metrics(report: Mapping[str, Any], byte_metrics_for: Callable[[int], dict]) -> dict:
    """``report`` with its ``bytes`` section, whose staged estimate counts data/coverage.json's own size: iterate
    from 0 until the serialized size is the size the section names. Every size field grows with that size (and
    within_pages_budget only turns from true to false), so the sizes never decrease and settle in a few rounds."""
    size = 0
    for _ in range(SIZE_ROUNDS):
        result = {**report, "bytes": byte_metrics_for(size)}
        written = len(manifest.dumps(result).encode("utf-8"))
        if written == size:
            return result
        size = written
    raise CoverageError("the size of data/coverage.json did not settle")


# --- Build ------------------------------------------------------------------------------------------------------------


def build(root: Path) -> dict:
    """The coverage report for ``root``; raises CoverageError/CatalogError when the inputs do not reconcile."""
    root = Path(root)
    manifests = build_catalog.load_manifests(root)
    sources, records, failures = build_catalog.source_records(manifests)
    catalog = build_catalog.combine(root, manifests=manifests)
    thumbs = build_catalog.load_thumbs(root)
    if thumbs is not None:
        catalog = build_catalog.attach_thumbnails(root, catalog)
    elif build_catalog.is_repository_root(root):  # the staged-site estimate needs the previews
        raise CoverageError(f"Missing required input: {build_catalog.THUMBS_MANIFEST}: run scripts/build_thumbnails.py")
    index = load_song_index(root, (record["id"] for record in records), records)
    snapshots = load_snapshots(root, sources)
    counts = reconcile(manifests, snapshots, records)
    transformed = {record["id"] for record in records if record.get("transformed") is True}
    songs, song_gaps = song_metrics(index, catalog, transformed)
    generated = generated_sizes(root, catalog)
    report = {
        "schema_version": schema.SCHEMA_VERSION,
        "game": schema.CATALOG_GAME,
        "as_of": index["as_of"],
        "inputs": {
            "song_index": SONG_INDEX,
            "manifests": {family: manifests[family]["fetched_at"] for family in schema.FAMILIES},
            "discovery": {
                source_id: {
                    "path": paths.discovery_relpath(source_id),
                    "finished_at": snapshots[source_id]["finished_at"],
                }
                for source_id in sorted(snapshots)
            },
            "thumbs": build_catalog.THUMBS_MANIFEST if thumbs is not None else None,
        },
        "definitions": DEFINITIONS,
        "sources": source_metrics(sources, snapshots, counts, records, failures, catalog),
        "candidates": candidate_metrics(snapshots, counts),
        "assets": asset_metrics(catalog, records, thumbs is not None),
        "songs": songs,
        "gaps": {**song_gaps, **asset_gaps(catalog, snapshots)},
        "mismatches": mismatches(index, records, snapshots, catalog),
    }
    return with_byte_metrics(report, lambda size: byte_metrics(catalog, records, thumbs, generated, size))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__
    )
    parser.add_argument("--check", action="store_true", help="compare with data/coverage.json without writing")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository-shaped tree (default: %(default)s)")
    args = parser.parse_args(argv)
    try:
        coverage = build(args.root)
        text = manifest.dumps(coverage)
        if args.check:
            if build_catalog.stale_outputs(args.root, {COVERAGE_JSON: text}):
                print(f"Out-of-date generated file: {COVERAGE_JSON}", file=sys.stderr)
                return 1
        else:
            build_catalog.write_text_atomic(args.root, COVERAGE_JSON, text)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "songs": coverage["songs"],
                "candidates": coverage["candidates"]["by_disposition"],
                "bytes": coverage["bytes"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
