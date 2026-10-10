"""Adapter output types and the seed-classification rules shared by every adapter.

An adapter turns one seed source into a :class:`Discovery`: the candidates it found (:class:`Draft`), the lists it
enumerated and whether they are complete. A draft says what the runner should do with the candidate (``download``,
``excluded``, ``blocked``, ``failed`` or ``pending``) and carries the descriptive metadata for the asset record.

Classification is curated data, never inferred from keywords alone. A seed rule may assert game identity, kind and a
song mapping only together with ``evidence_quotes`` that must occur in the text the adapter actually fetched; when a
quote is missing the candidate is downgraded to ``pending`` (and its mapping to ``unmapped``) instead of being admitted
on stale or wrong seed data. Two guards override seeds: text announcing a *rejected submission* excludes the candidate,
and text naming *Dynamix Universe* holds it as ``pending`` unless the rule explicitly allows it.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from lib import paths, schema
from lib.checks import redact_text

ACTIONS = ("download", "excluded", "blocked", "failed", "pending")
RULE_ACTIONS = ("download", "exclude", "pending")
RULE_FIELDS = {
    "match", "title", "role", "game", "action", "reason", "evidence_quotes", "game_evidence", "song_titles",
    "collections", "illustrators", "composers", "mapping_status", "credit_evidence", "variant_group", "variant_role",
    "quality", "rights", "notes", "transformed", "allow_universe_mention", "game_associations", "candidate_notes",
    "primary_evidence", "upstream_revision",
}
REJECTED_RE = re.compile(r"rejected\s+submission", re.IGNORECASE)
UNIVERSE_RE = re.compile(r"dynamix\s*universe", re.IGNORECASE)
QUOTE_MAX = 300


def normalize_text(text: str) -> str:
    """Comparison form: NFKC, casefolded, whitespace collapsed."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def clip(text: str, limit: int = QUOTE_MAX) -> str:
    """A single-line, credential-free excerpt of upstream text for stored evidence."""
    value = " ".join(str(text).split())
    value = redact_text(value, strict=False)
    return value if len(value) <= limit else value[: limit - 1] + "…"


@dataclass
class Draft:
    """One candidate as discovered by an adapter."""

    upstream_key: str
    page_url: str
    title: str
    action: str
    file_url: str | None = None
    role: str | None = None
    game: str = "unknown"
    game_evidence: list[str] = field(default_factory=list)
    source_reported: dict = field(default_factory=schema.make_source_reported)
    reason: str | None = None
    error: Any = None  # lib.net.FetchError / lib.media.MediaError behind a blocked/failed draft
    error_phase: str = "discover"
    original_filename: str | None = None
    meta: dict = field(default_factory=dict)  # asset metadata (see Rule.asset_meta)
    candidate_extra: dict = field(default_factory=dict)  # song_titles, illustrators, composers, collections, notes
    md5_token: str | None = None  # upstream name that is expected to equal the file's MD5 (TapTap), evidence only
    fallback_urls: list[str] = field(default_factory=list)  # tried in order when file_url answers HTTP 404

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"unknown draft action {self.action!r}")


@dataclass
class Discovery:
    """What an adapter found for one source."""

    candidates: list[Draft] = field(default_factory=list)
    lists: list[str] = field(default_factory=lambda: ["seed-items"])
    continuation: dict = field(default_factory=dict)
    api: str | None = None
    scope_notes: str = ""
    errors: list[dict] = field(default_factory=list)
    evidence_level: str = "page_read"
    status: str | None = None  # adapter-determined source status (overrides the computed one)
    notes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return all(token is None for token in self.continuation.values())


def discovery_error(phase: str, url: str | None, message: str) -> dict:
    return {"phase": phase, "url": url, "message": redact_text(" ".join(str(message).split())) or phase}


# --- Seed rules -------------------------------------------------------------------------------------------------------

def check_rule(rule: Mapping[str, Any], where: str) -> None:
    unknown = sorted(set(rule) - RULE_FIELDS)
    if unknown:
        raise ValueError(f"{where}: unknown rule field {unknown[0]!r}")
    action = rule.get("action", "download")
    if action not in RULE_ACTIONS:
        raise ValueError(f"{where}: action must be one of {RULE_ACTIONS}")
    if action in ("exclude", "pending") and not rule.get("reason"):
        raise ValueError(f"{where}: an {action} rule needs a reason")
    if action == "exclude" and rule["reason"].split(":", 1)[0].strip() not in schema.EXCLUSION_REASONS:
        raise ValueError(f"{where}: exclusion reason must start with one of {schema.EXCLUSION_REASONS}")
    if rule.get("role") is not None and rule["role"] not in schema.KINDS:
        raise ValueError(f"{where}: role must be one of {schema.KINDS}")
    if action == "download" and rule.get("role") not in schema.KINDS:
        raise ValueError(f"{where}: a download rule needs a role (kind)")
    if rule.get("mapping_status", "unmapped") not in schema.MAPPING_STATUSES:
        raise ValueError(f"{where}: unknown mapping_status")
    if rule.get("mapping_status", "unmapped") != "unmapped" and not rule.get("song_titles"):
        raise ValueError(f"{where}: a mapped rule needs song_titles")
    if rule.get("mapping_status") == "confirmed_primary" and not (rule.get("evidence_quotes")
                                                                  or rule.get("primary_evidence")):
        raise ValueError(f"{where}: confirmed_primary needs evidence_quotes found in the primary post itself (or, "
                         "for a copy of that post's file, primary_evidence quoting it)")
    for index, entry in enumerate(rule.get("primary_evidence", [])):
        if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) and entry[k].strip()
                                                  for k in ("url", "quote")):
            raise ValueError(f"{where}: primary_evidence[{index}] needs url and quote")


def find_rule(rules: Iterable[Mapping[str, Any]], key: str) -> Mapping[str, Any] | None:
    """First rule whose ``match`` regex matches ``key`` (``re.search``); a rule without ``match`` matches anything."""
    for rule in rules:
        pattern = rule.get("match")
        if pattern is None or re.search(pattern, key):
            return rule
    return None


def find_quotes(quotes: Iterable[str], texts: Mapping[str, str], url: str) -> tuple[list[dict], list[str]]:
    """Mapping-evidence entries for the quotes found in ``texts`` ({field: text}) and the list of missing quotes."""
    found, missing = [], []
    normalized = {name: normalize_text(text) for name, text in texts.items() if text}
    for quote in quotes:
        target = normalize_text(quote)
        field_name = next((name for name, text in normalized.items() if target and target in text), None)
        if field_name is None:
            missing.append(quote)
        else:
            found.append({"url": url, "field": field_name, "quote": clip(quote)})
    return found, missing


def guard_texts(texts: Mapping[str, str]) -> tuple[str | None, str | None]:
    """(rejected-submission excerpt, Universe excerpt) found in the fetched texts."""
    rejected = universe = None
    for text in texts.values():
        if not text:
            continue
        match = REJECTED_RE.search(text)
        if match and rejected is None:
            rejected = clip(text[max(0, match.start() - 60): match.end() + 40], 160)
        match = UNIVERSE_RE.search(text)
        if match and universe is None:
            universe = clip(text[max(0, match.start() - 60): match.end() + 40], 160)
    return rejected, universe


def apply_rule(rule: Mapping[str, Any] | None, *, key: str, page_url: str, texts: Mapping[str, str],
               file_url: str | None, default_title: str, default_quality: Mapping[str, Any],
               default_rights: Mapping[str, Any], source_reported: dict | None = None,
               original_filename: str | None = None, base_evidence: Iterable[str] = (),
               unclassified_reason: str = "unclassified: no seed rule for this item; needs review") -> Draft:
    """A :class:`Draft` for one discovered item from its seed rule and the fetched ``texts``.

    No rule -> ``pending`` (never admitted on keywords alone). A download rule needs every ``evidence_quotes`` entry in
    ``texts``; otherwise the item becomes ``pending`` with the missing quotes named. Guards: a rejected-submission
    announcement excludes the item; a Dynamix Universe mention holds it as ``pending`` unless allowed by the rule.
    """
    reported = source_reported or schema.make_source_reported(url=file_url)
    rule = dict(rule or {})
    title = (rule.get("title") or default_title).replace("{default}", default_title).replace(
        "{filename}", original_filename or "")
    found, missing = find_quotes(rule.get("evidence_quotes", []), texts, page_url)
    game_evidence = [*base_evidence, *rule.get("game_evidence", []),
                     *(f"{item['field']}: {item['quote']}" for item in found)]
    draft = Draft(upstream_key=key, page_url=page_url, title=title, action="pending", file_url=file_url,
                  role=rule.get("role"), game=rule.get("game", "unknown"), game_evidence=list(dict.fromkeys(
                      game_evidence)), source_reported=reported, original_filename=original_filename)
    extra = {name: list(rule[name]) for name in ("song_titles", "illustrators", "composers", "collections")
             if rule.get(name)}
    if rule.get("candidate_notes"):
        extra["notes"] = rule["candidate_notes"]
    draft.candidate_extra = extra
    rejected, universe = guard_texts(texts)
    if rejected:
        draft.action, draft.game = "excluded", "not_dynamix"
        draft.reason = f"rejected_submission: {rejected}"
        draft.game_evidence = list(dict.fromkeys([*draft.game_evidence, f"upstream text: {rejected}"]))
        return draft
    if not rule:
        draft.reason = unclassified_reason
        return draft
    action = rule.get("action", "download")
    if action == "exclude":
        draft.action, draft.reason = "excluded", rule["reason"]
        prefix = rule["reason"].split(":", 1)[0].strip()
        draft.game = {"universe_only": "dynamix_universe", "not_dynamix": "not_dynamix"}.get(prefix, draft.game)
        return draft
    if action == "pending":
        draft.reason = rule["reason"]
        return draft
    if missing:
        draft.reason = "pending: seed evidence not found in the fetched content (" + "; ".join(
            clip(quote, 80) for quote in missing) + ")"
        return draft
    if universe and not rule.get("allow_universe_mention"):
        draft.reason = f"pending: upstream text mentions Dynamix Universe; confirm original-game use ({universe})"
        return draft
    if draft.game not in ("dynamix", "mixed"):
        draft.game = "dynamix"
    if not file_url:
        draft.reason = "pending: no downloadable file URL was found for this item"
        return draft
    draft.action = "download"
    draft.meta = asset_meta(rule, found, page_url, default_quality, default_rights)
    return draft


def asset_meta(rule: Mapping[str, Any], found: list[dict], page_url: str, default_quality: Mapping[str, Any],
               default_rights: Mapping[str, Any]) -> dict:
    """Descriptive asset fields from a download rule (the runner adds the measured fields)."""
    song_titles = list(rule.get("song_titles", []))
    status = rule.get("mapping_status", "unmapped") if song_titles else "unmapped"
    quality = {**copy.deepcopy(dict(default_quality)), **rule.get("quality", {})}
    rights = {**copy.deepcopy(dict(default_rights)), **rule.get("rights", {})}
    if rights.get("url") is None:
        rights["url"] = page_url
    meta = {
        "illustrators": list(rule.get("illustrators", [])),
        "composers": list(rule.get("composers", [])),
        "song_titles": song_titles if status != "unmapped" else [],
        "song_ids": [paths.song_id_base(title) for title in song_titles] if status != "unmapped" else [],
        "collections": list(rule.get("collections", [])),
        "mapping_status": status,
        "mapping_evidence": list(found) if status != "unmapped" else [],
        "quality": quality,
        "rights": rights,
    }
    if status != "unmapped":
        meta["mapping_evidence"] += [{"url": entry["url"], "field": "primary_post", "quote": clip(entry["quote"]),
                                      **({"note": entry["note"]} if entry.get("note") else {})}
                                     for entry in rule.get("primary_evidence", [])]
    if status != "unmapped" and not meta["mapping_evidence"]:
        meta["mapping_evidence"] = [{"url": page_url, "note": "seed classification (no quote required)"}]
    for name in ("credit_evidence", "variant_group", "variant_role", "notes", "transformed", "game_associations",
                 "upstream_revision"):
        if name in rule:
            meta[name] = copy.deepcopy(rule[name])
    return meta


def error_draft(key: str, page_url: str, title: str, error: Any, *, phase: str, file_url: str | None = None,
                role: str | None = None, game: str = "unknown", game_evidence: Iterable[str] = (),
                source_reported: dict | None = None) -> Draft:
    """A ``blocked``/``failed`` draft for an item whose discovery or download raised ``error``."""
    error_class = getattr(error, "error_class", "other")
    blocked = error_class in ("blocked", "challenge")
    status = getattr(error, "http_status", None)
    detail = getattr(error, "message", None) or str(error)
    reason = (f"{error_class}: {detail}" + ("" if status is None or str(status) in detail else f" (HTTP {status})"))
    if blocked:
        reason += "; manual access required, not retried"
    return Draft(upstream_key=key, page_url=page_url, title=title, action="blocked" if blocked else "failed",
                 file_url=file_url, role=role, game=game, game_evidence=list(game_evidence),
                 source_reported=source_reported or schema.make_source_reported(url=file_url),
                 reason=clip(reason, 400), error=error, error_phase=phase)
