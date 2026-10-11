"""Offline ``fetch_wikis.py --verify``: bytes, records, dispositions and files agree; no network access.

Checks: the family manifest and every wiki discovery snapshot validate; :func:`lib.schema.reconcile` (source counts,
candidate <-> asset links, failures, removed/superseded revisions); each asset file exists inside
``assets/public/wikis`` with the recorded bytes, SHA-256/SHA-1, sniffed format/MIME and decoded dimensions; no file
under ``assets/public/wikis`` lacks a record (leftover ``*.part`` temp files are reported separately); the song index
proposal validates and every asset ``song_id`` names an index song whose title is among the asset's ``song_titles``;
the variant links between the two wikis' records of a title (:mod:`wikis.variants`) are the ones the records imply.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from lib import manifest, media, paths, schema

from . import config, outputs, variants

FAMILY = "wikis"


def run(root: Path, *, log: Callable[[str], None] = print) -> int:
    problems: list[str] = []
    try:
        data = manifest.load_manifest(root, FAMILY, missing_ok=True)
    except (ValueError, OSError) as error:
        log(f"FAIL manifest: {error}")
        return 1
    snapshots = {}
    for source in config.SOURCES.values():
        try:
            snapshot = manifest.load_discovery(root, source.source_id)
        except (ValueError, OSError) as error:
            problems.append(f"discovery {source.source_id}: {error}")
            continue
        if snapshot is not None:
            snapshots[source.source_id] = snapshot
    known_sources = {source["id"] for source in data["sources"]}
    for source_id in sorted(set(snapshots) - known_sources):
        problems.append(f"discovery {source_id}: no source record in data/sources/wikis.json")
    for source_id in sorted(known_sources - set(snapshots)):
        problems.append(f"source {source_id}: no discovery snapshot")
    counts = {}
    try:
        counts = schema.reconcile(data, {key: value for key, value in snapshots.items() if key in known_sources})
    except ValueError as error:
        problems.append(f"reconcile: {error}")
    for asset in data["assets"]:
        try:
            media.verify_asset_file(root, asset)
        except media.MediaError as error:
            problems.append(f"file: {error}")
    linked = {asset["id"]: asset for asset in variants.link(data["assets"])}
    for asset in data["assets"]:
        if {name: asset.get(name) for name in variants.FIELDS} != {
            name: linked[asset["id"]].get(name) for name in variants.FIELDS
        }:
            problems.append(f"asset {asset['id']}: variant_group/variant_role do not match the wiki variant links")
    problems.extend(_orphans(root, data["assets"], log))
    problems.extend(_song_index(root, data["assets"]))
    for source_id, value in sorted(counts.items()):
        total = sum(value.values())
        log(f"{source_id}: {total} candidates = " + " + ".join(f"{name} {n}" for name, n in value.items()))
    log(
        f"assets: {len(data['assets'])} records, {len({a['sha256'] for a in data['assets']})} unique SHA-256, "
        f"{sum(a['bytes'] for a in data['assets'])} bytes; failures: {len(data['failures'])}"
    )
    for problem in problems:
        log(f"FAIL {problem}")
    log("verify: OK" if not problems else f"verify: {len(problems)} problem(s)")
    return 1 if problems else 0


def _orphans(root: Path, assets: list[dict], log: Callable[[str], None]) -> list[str]:
    base = root / paths.ASSET_ROOT / FAMILY
    if not base.is_dir():
        return []
    recorded = {asset["path"] for asset in assets}
    problems = []
    for file in sorted(base.rglob("*")):
        if file.is_symlink():
            problems.append(f"symlink under {paths.ASSET_ROOT}/{FAMILY}: {file.relative_to(root).as_posix()}")
            continue
        if not file.is_file():
            continue
        rel = file.relative_to(root).as_posix()
        if file.name.startswith(".") and file.name.endswith(".part"):
            log(f"warning: leftover temporary download {rel} (an interrupted or running fetch)")
            continue
        if rel not in recorded:
            problems.append(f"orphan file without a manifest record: {rel}")
    return problems


def _song_index(root: Path, assets: list[dict]) -> list[str]:
    target = root / outputs.SONG_INDEX_PATH
    if not target.is_file():
        return []
    try:
        index = manifest.load_json(target)
        schema.validate_song_index(index, {asset["id"] for asset in assets})
        schema.validate_song_links(index, assets)
    except (ValueError, OSError) as error:
        return [f"song index proposal: {error}"]
    return []
