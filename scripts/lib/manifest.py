"""Deterministic JSON I/O and record-list helpers for manifests, discovery snapshots and the song index.

JSON is written as the contract requires: UTF-8, ``ensure_ascii=False``, ``indent=2``, a trailing newline, record
arrays sorted by ``id`` and record keys in contract order. Writes are atomic (temporary file in the same directory,
fsync, ``os.replace``), so an interrupted run never leaves a truncated file. Inputs are never mutated.
"""

from __future__ import annotations

import difflib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths, schema

_RECORD_KINDS = {"sources": "source", "assets": "asset", "failures": "failure"}


def now_utc() -> str:
    """Current UTC time as ``YYYY-MM-DDTHH:MM:SSZ`` (only for acquisition timestamps, never in offline builds)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name} is not valid JSON")


def loads(text: str | bytes) -> Any:
    """Strict JSON: duplicate keys and NaN/Infinity are errors."""
    return json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)


def load_json(path: str | os.PathLike[str]) -> Any:
    """Read UTF-8 JSON strictly (see :func:`loads`); a BOM is an error."""
    data = Path(path).read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"{path}: UTF-8 byte-order mark is not allowed")
    return loads(data.decode("utf-8"))


def dumps(obj: Any) -> str:
    """Contract JSON text: UTF-8 characters unescaped, 2-space indent, trailing newline, no NaN."""
    return json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def write_json_atomic(path: str | os.PathLike[str], obj: Any) -> None:
    """Write :func:`dumps` output via a unique temporary file in the target directory and ``os.replace``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dumps(obj).encode("utf-8")
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o644)
        os.replace(name, target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def compare_json(path: str | os.PathLike[str], obj: Any, *, context: int = 2, limit: int = 40) -> str | None:
    """``--check`` helper: None when the file holds exactly :func:`dumps(obj)`, else a short unified diff."""
    target = Path(path)
    expected = dumps(obj)
    if not target.is_file():
        return f"{target}: missing (expected {len(expected.encode('utf-8'))} bytes)"
    actual = target.read_bytes().decode("utf-8", errors="replace")
    if actual == expected:
        return None
    diff = list(
        difflib.unified_diff(
            actual.splitlines(),
            expected.splitlines(),
            f"{target} (on disk)",
            f"{target} (regenerated)",
            n=context,
            lineterm="",
        )
    )
    if not diff:  # only trailing-newline or line-ending differences
        diff = [f"{target}: differs in line endings or trailing newline"]
    return "\n".join(diff[:limit] + ([f"... {len(diff) - limit} more diff lines"] if len(diff) > limit else []))


def empty_manifest(family: str) -> dict:
    """A valid family manifest with no records and ``fetched_at`` null (no successful acquisition yet)."""
    if family not in schema.FAMILIES:
        raise ValueError(f"unknown family: {family!r}")
    return {
        "schema_version": schema.SCHEMA_VERSION,
        "family": family,
        "fetched_at": None,
        "sources": [],
        "assets": [],
        "failures": [],
    }


def sort_records(records: Iterable[Mapping[str, Any]], kind: str | None = None) -> list[dict]:
    """Records sorted by ``id``; with ``kind`` (see :func:`lib.schema.canonical`) keys are put in contract order."""
    ordered = sorted(records, key=lambda record: record["id"])
    return [schema.canonical(record, kind) if kind else dict(record) for record in ordered]


def sort_manifest(manifest: Mapping[str, Any]) -> dict:
    """A copy with ``sources``/``assets``/``failures`` sorted by ID and keys in contract order."""
    result = {key: manifest[key] for key in ("schema_version", "family", "fetched_at") if key in manifest}
    for field, kind in _RECORD_KINDS.items():
        result[field] = sort_records(manifest.get(field, []), kind)
    result.update((key, value) for key, value in manifest.items() if key not in result)
    return result


def sort_discovery(snapshot: Mapping[str, Any]) -> dict:
    """A copy with candidates sorted by ID (contract key order) and errors in a stable order."""
    result = dict(snapshot)
    result["candidates"] = sort_records(snapshot.get("candidates", []), "candidate")
    result["errors"] = sorted(
        snapshot.get("errors", []),
        key=lambda error: (error.get("phase") or "", error.get("url") or "", error.get("message") or ""),
    )
    return result


def sort_song_index(index: Mapping[str, Any]) -> dict:
    """A copy with songs sorted by ID and disagreements by (song ID, field)."""
    result = dict(index)
    result["songs"] = sort_records(index.get("songs", []))
    result["disagreements"] = sorted(
        index.get("disagreements", []), key=lambda item: (item.get("song_id", ""), item.get("field", ""))
    )
    return result


def upsert(records: Iterable[Mapping[str, Any]], record: Mapping[str, Any]) -> list[dict]:
    """A new list sorted by ID in which ``record`` replaces the record with the same ID (or is added)."""
    kept = [dict(item) for item in records if item["id"] != record["id"]]
    return sorted([*kept, dict(record)], key=lambda item: item["id"])


def disposition_counts(candidates: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Counts per disposition in contract order, zeros included (see :func:`lib.schema.disposition_counts`)."""
    return schema.disposition_counts(candidates)


def load_manifest(root: str | os.PathLike[str], family: str, *, missing_ok: bool = True) -> dict:
    """Read and validate ``data/sources/<family>.json`` under ``root``; an absent file gives :func:`empty_manifest`
    when ``missing_ok``."""
    target = paths.safe_path(root, paths.manifest_relpath(family))
    if not target.is_file():
        if missing_ok:
            return empty_manifest(family)
        raise FileNotFoundError(target)
    manifest = load_json(target)
    schema.validate_manifest(manifest, family)
    return manifest


def load_discovery(root: str | os.PathLike[str], source_id: str) -> dict | None:
    """Read and validate ``data/discovery/<source-slug>.json``; None when absent."""
    target = paths.safe_path(root, paths.discovery_relpath(source_id))
    if not target.is_file():
        return None
    snapshot = load_json(target)
    schema.validate_discovery(snapshot, source_id)
    return snapshot
