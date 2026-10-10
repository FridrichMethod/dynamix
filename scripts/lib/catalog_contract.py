"""Catalog-level contract checks (``data/catalog.json``), split from :mod:`lib.schema` to keep both modules small.

Use :func:`lib.schema.validate_catalog`; this module holds its implementation.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from .schema import (ARCHIVE_URL_PREFIX, CATALOG_SPEC, FAMILIES, KINDS, _check_fields, _fail, _integer, _label,
                     _nullable, _object, _timestamp, catalog_sort_key, catalog_url, is_gallery, is_slideshow,
                     ordered_union, provenance_sort_key, source_family, thumb_relpath, tier_for, validate_asset,
                     validate_failure, validate_source)


def _check_catalog_asset(asset: Mapping[str, Any], source_names: Mapping[str, str]) -> None:
    label = _label("catalog asset", asset)
    family = source_family(asset["source_id"])
    if asset["source_id"] not in source_names:
        _fail(label, "source_id", "is not listed in catalog sources")
    expected = {
        "family": family, "source_name": source_names.get(asset["source_id"]), "url": catalog_url(asset["path"]),
        "gallery": is_gallery(asset), "slideshow": is_slideshow(asset),
        "site_original": tier_for(asset["kind"], asset["format"]) == "site",
        "archive_url": ARCHIVE_URL_PREFIX + catalog_url(asset["path"]),
    }
    for field, value in expected.items():
        if asset[field] != value:
            _fail(label, field, f"must be {value!r}")
    if asset["thumb"] not in (None, thumb_relpath(asset["sha256"])):
        _fail(label, "thumb", f"must be null or {thumb_relpath(asset['sha256'])!r}")
    provenance = asset["provenance"]
    if not provenance:
        _fail(label, "provenance", "must list at least the primary record")
    for record in provenance:
        if record["family"] != source_family(record["source_id"]):
            _fail(label, "provenance", f"family of {record['source_id']!r} must be {record['family']!r}")
        if source_names.get(record["source_id"]) != record["source_name"]:
            _fail(label, "provenance", f"source_name of {record['source_id']!r} differs from its source")
    keys = [provenance_sort_key(record) for record in provenance]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        _fail(label, "provenance", "must be unique and ordered by family priority, source ID, asset ID")
    if provenance[0]["source_id"] != asset["source_id"]:
        _fail(label, "provenance", "the first entry must be the primary record")
    for field in ("illustrators", "composers", "song_titles"):
        merged = ordered_union(record[field] for record in provenance)
        if asset[field] != merged:
            _fail(label, field, f"must be the ordered union across provenance {merged!r}")


SUMMARY_FIELDS = ("source_count", "source_asset_records", "unique_files", "gallery_images", "slideshow_images",
                  "exact_duplicate_records", "by_family", "by_kind", "failure_count", "downloaded_bytes",
                  "site_original_bytes", "archive_only_bytes")


def _expected_summary(catalog: Mapping[str, Any]) -> dict:
    assets = catalog["assets"]
    records = [record for asset in assets for record in asset["provenance"]]
    site = sum(asset["bytes"] for asset in assets if asset["site_original"])
    total = sum(asset["bytes"] for asset in assets)
    return {
        "source_count": len(catalog["sources"]), "source_asset_records": len(records), "unique_files": len(assets),
        "gallery_images": sum(asset["gallery"] for asset in assets),
        "slideshow_images": sum(asset["slideshow"] for asset in assets),
        "exact_duplicate_records": len(records) - len(assets),
        "by_family": dict(Counter(record["family"] for record in records)),
        "by_kind": dict(Counter(asset["kind"] for asset in assets)), "failure_count": len(catalog["failures"]),
        "downloaded_bytes": total, "site_original_bytes": site, "archive_only_bytes": total - site,
    }


def _check_summary(catalog: Mapping[str, Any]) -> None:
    summary = catalog["summary"]
    _check_fields("catalog summary", summary, {
        **{field: _integer(0) for field in SUMMARY_FIELDS},
        "by_family": _object({}, {family: _integer(0) for family in FAMILIES}),
        "by_kind": _object({}, {kind: _integer(0) for kind in KINDS}),
    })
    for field, value in _expected_summary(catalog).items():
        actual = summary[field]
        if isinstance(value, dict):
            actual = {key: count for key, count in actual.items() if count}
        if actual != value:
            _fail("catalog summary", field, f"{summary[field]!r} but the catalog has {value!r}")


def validate_catalog(catalog: Any) -> None:
    """``data/catalog.json``: top level, sources, failures, catalog assets, order, variants, summary arithmetic."""
    _check_fields("catalog", catalog, CATALOG_SPEC)
    for family, fetched_at in catalog["snapshots"].items():
        if family not in FAMILIES or _nullable(_timestamp)(fetched_at):
            _fail("catalog", "snapshots", f"{family!r} must be a family with a timestamp or null")
    for source in catalog["sources"]:
        validate_source(source, catalog=True)
    source_ids = [source["id"] for source in catalog["sources"]]
    if len(set(source_ids)) != len(source_ids):
        _fail("catalog", "sources", "duplicate source ID")
    for failure in catalog["failures"]:
        validate_failure(failure)
    source_names = {source["id"]: source["name"] for source in catalog["sources"]}
    assets = catalog["assets"]
    for asset in assets:
        validate_asset(asset, catalog=True)
        _check_catalog_asset(asset, source_names)
    for field in ("id", "sha256"):
        values = [asset[field] for asset in assets]
        if len(set(values)) != len(values):
            _fail("catalog", "assets", f"duplicate {field} (assets are deduplicated by sha256)")
    if [catalog_sort_key(asset) for asset in assets] != sorted(catalog_sort_key(asset) for asset in assets):
        _fail("catalog", "assets", "must be ordered by kind, normalized title, ID")
    groups: dict[str, list[str]] = {}
    for asset in assets:
        if "variant_group" in asset:
            groups.setdefault(asset["variant_group"], []).append(asset["id"])
    for asset in assets:
        expected = sorted(i for i in groups.get(asset.get("variant_group"), []) if i != asset["id"])
        if asset["variants"] != expected:
            _fail(_label("catalog asset", asset), "variants", f"must be {expected!r}")
    _check_summary(catalog)
