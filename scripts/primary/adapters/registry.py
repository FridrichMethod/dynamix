"""Curated lead registry: leads that have no file this project can or should fetch, recorded without any request.

Used for research-sweep leads that are credit/identity evidence only (video descriptions, profiles, wiki rows, store
listings without unique art), for access-limited pages that were already observed behind a login wall or bot check
(recorded ``blocked`` and never retried automatically), and for leads deliberately held for review (``pending``). Each
item becomes one candidate with the curated disposition and reason; ``downloaded``/``duplicate_bytes``/``failed`` are
not allowed here because they need an actual acquisition attempt. Items keep the lead's own URL as ``page_url`` and,
when the lead named one, its file URL, so the decision stays traceable.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from lib import checks, schema

from .. import model
from . import base

HOSTS: list[str] = []
OFFLINE = True
DISPOSITIONS = ("excluded", "pending", "blocked")
ITEM_FIELDS = {
    "key",
    "page_url",
    "file_url",
    "title",
    "disposition",
    "reason",
    "game",
    "role",
    "game_evidence",
    "song_titles",
    "illustrators",
    "composers",
    "collections",
    "notes",
    "observed",
}


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    keys = []
    for index, item in enumerate(base.require_items(seed, where, ["key", "page_url", "title", "reason"])):
        label = f"{where}: items[{index}]"
        unknown = sorted(set(item) - ITEM_FIELDS)
        if unknown:
            raise ValueError(f"{label}: unknown field {unknown[0]!r}")
        if item.get("disposition") not in DISPOSITIONS:
            raise ValueError(f"{label}: disposition must be one of {DISPOSITIONS}")
        if re.search(r"\s", item["key"]):
            raise ValueError(f"{label}: key must not contain whitespace")
        for name in ("page_url", "file_url"):
            if item.get(name) is not None and checks.http_url(item[name]):
                raise ValueError(f"{label}: {name} {checks.http_url(item[name])}")
        if item["disposition"] == "excluded":
            prefix = item["reason"].split(":", 1)[0].strip()
            if prefix not in schema.EXCLUSION_REASONS:
                raise ValueError(f"{label}: exclusion reason must start with one of {schema.EXCLUSION_REASONS}")
        if model.awaiting_without_file(item):
            raise ValueError(
                f"{label}: an awaiting_download item needs its file_url (a lead with no file to fetch "
                "is unresolved_role)"
            )
        if item.get("game", "unknown") not in schema.GAMES:
            raise ValueError(f"{label}: game must be one of {schema.GAMES}")
        if item.get("role") is not None and item["role"] not in schema.KINDS:
            raise ValueError(f"{label}: role must be one of {schema.KINDS}")
        keys.append(item["key"])
    if len(set(keys)) != len(keys):
        raise ValueError(f"{where}: duplicate registry keys")


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    discovery = base.new_discovery(
        seed,
        lists=["registry"],
        evidence_level=seed.get("evidence_level", "search_index"),
        scope_notes="Curated research-sweep leads recorded without a request (no file to "
        "acquire, or access already observed as limited).",
    )
    for item in seed["items"]:
        reason = item["reason"]
        game = item.get("game", "unknown")
        if item["disposition"] == "excluded":
            game = {"universe_only": "dynamix_universe", "not_dynamix": "not_dynamix"}.get(
                reason.split(":", 1)[0].strip(), game
            )
        notes = " ".join(
            filter(
                None, [item.get("notes"), f"Research observation: {item['observed']}" if item.get("observed") else None]
            )
        )
        draft = model.Draft(
            upstream_key=item["key"],
            page_url=item["page_url"],
            title=item["title"],
            action=item["disposition"],
            file_url=item.get("file_url"),
            role=item.get("role"),
            game=game,
            game_evidence=list(item.get("game_evidence", [])),
            source_reported=base.reported(url=item.get("file_url")),
            reason=reason,
        )
        draft.candidate_extra = {
            name: list(item[name])
            for name in ("song_titles", "illustrators", "composers", "collections")
            if item.get(name)
        }
        if notes:
            draft.candidate_extra["notes"] = model.clip(notes, 1200)
        discovery.candidates.append(draft)
    # A registry is a metadata record of leads, not an access attempt: its own status stays metadata_only even when
    # some of its leads were observed behind a login wall (those candidates are ``blocked``).
    discovery.status = "metadata_only"
    return discovery
