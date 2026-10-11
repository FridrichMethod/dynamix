"""Variant links between the two English wikis' records of one ``File:`` title (07-data-contracts.md: different
bytes remain separate archive variants; 09-frozen-contracts.md: ``variant_group``/``variant_role``).

Miraheze imported Fandom's files under the same titles, so the current records of a title on both wikis normally hold
the same bytes and the catalog merges them by SHA-256. When their bytes differ (e.g. Fandom's CDN serves a platform
transform of the upload that both wikis' APIs record), both records stay, and each carries the variant group
``wiki-file:<File: title>`` so the catalog lists the other one under ``variants``; ``variant_role`` says what the bytes
are. The links are derived from the records alone and recomputed whenever the manifest is written, so they never go
stale; ``--verify`` checks them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from lib import schema

from . import config

GROUP_PREFIX = "wiki-file:"
FIELDS = ("variant_group", "variant_role")
_NAMES = {source.source_id: source.name for source in config.SOURCES.values()}


def file_title(asset: Mapping[str, Any]) -> str:
    """``File:...`` title of a wiki record (its candidate ID is ``<source_id>:<File: title>``)."""
    return asset["candidate_id"][len(asset["source_id"]) + 1 :]


def role(asset: Mapping[str, Any]) -> str:
    """What the record's bytes are, relative to the upload its wiki's API records."""
    name = _NAMES.get(asset["source_id"], asset["source_id"])
    if asset.get("transformed"):
        return f"platform transform of the upload, served by {name}; not the uploaded bytes (see quality notes)"
    if asset["comparisons"].get("sha1_match") is True:
        return f"the upload, served by {name} with the SHA-1 its API records"
    return f"bytes served by {name} that differ from the upload its API records (cause not established)"


def link(assets: Iterable[Mapping[str, Any]]) -> list[dict]:
    """``assets`` with variant fields set on the current records of every title whose current records on the wikis
    hold different bytes, and removed everywhere else (removed/superseded revisions keep their own chains)."""
    records = [dict(asset) for asset in assets]
    by_title: dict[str, list[dict]] = {}
    for record in records:
        if record.get("upstream_status") not in ("removed", "superseded"):
            by_title.setdefault(file_title(record), []).append(record)
    linked = {
        record["id"]: title
        for title, group in by_title.items()
        if len({item["sha256"] for item in group}) > 1 and len({item["source_id"] for item in group}) > 1
        for record in group
    }
    result = []
    for record in records:
        for name in FIELDS:
            record.pop(name, None)
        title = linked.get(record["id"])
        if title is not None:
            record["variant_group"] = GROUP_PREFIX + title
            record["variant_role"] = role(record)
        result.append(schema.canonical(record, "asset"))
    return result
