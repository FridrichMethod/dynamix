"""Archivist reviews of wiki files that page evidence leaves unresolved (``scripts/wikis/reviews.json``).

A review records what an archivist established for one ``File:`` title when templates, captions, list pages,
categories and file usage say nothing (09-frozen-contracts.md, CLI notes): the role seen on visual inspection of the
downloaded bytes, the game and why, songs named by the image or its exact filename, or an exclusion from the frozen
vocabulary. Evidence text is kept verbatim and starts with its basis (``visual_inspection: ...``); a song mapping that
rests on a review is at most ``inferred``.

A review is pinned to the file revisions it looked at: ``sha1`` lists the API SHA-1 values the wikis reported for that
title when it was reviewed, and it applies to a wiki's file only while that wiki still reports one of them. After a
re-upload the review no longer applies and the file falls back to its page evidence (pending), so a reviewer looks
again. Page evidence always wins: a review only supplies a role when no page
gives one (filename conventions aside) and only supplies a game when no page context gives one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from lib import schema

REVIEWS_PATH = Path(__file__).resolve().parent / "reviews.json"
FORMAT = "dynamix-wiki-archivist-reviews/1"
# Exclusions a review may assign; animated captures and video placeholders are detected mechanically.
REVIEW_EXCLUSIONS = tuple(reason for reason in schema.EXCLUSION_REASONS
                          if reason not in ("animated_capture", "video_placeholder"))
REVIEW_GAMES = ("dynamix", "mixed", "dynamix_universe", "not_dynamix")
EVIDENCE_PREFIX = re.compile(r"^[a-z_]+: \S")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_ASSOCIATION = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class Review:
    title: str  # canonical "File:..." title (spaces, not underscores)
    sha1: tuple[str, ...]  # API SHA-1 values (any wiki) of the reviewed revision(s)
    role: str | None  # a kind, an exclusion reason, or None (role not established)
    game: str | None  # "dynamix", "mixed", "dynamix_universe", "not_dynamix" or None (not established)
    evidence: str  # verbatim role evidence, "<basis>: <observation>"
    game_evidence: str = ""  # verbatim game evidence (required when game is set)
    songs: tuple[str, ...] = ()  # exact song titles the image shows or its exact filename names
    associations: tuple[str, ...] = ()  # other games/events of shared artwork (game "mixed")
    pending: str = ""  # why the file stays pending although reviewed (role or game not established)

    def applies_to(self, reported_sha1: str | None) -> bool:
        return bool(reported_sha1) and reported_sha1.lower() in self.sha1

    @property
    def excluded(self) -> bool:
        return self.role in REVIEW_EXCLUSIONS

    def role_text(self) -> str:
        return f"archivist review ({self.title}): {self.evidence}"

    def game_text(self) -> str:
        return f"archivist review ({self.title}): {self.game_evidence}"


def _string(entry: Mapping[str, Any], name: str, label: str, *, required: bool = False) -> str:
    value = entry.get(name, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{label}: {name} must be a{' non-empty' if required else ''} string")
    return value.strip()


def _strings(entry: Mapping[str, Any], name: str, label: str) -> tuple[str, ...]:
    values = entry.get(name, [])
    if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError(f"{label}: {name} must be a list of non-empty strings")
    return tuple(value.strip() for value in values)


def parse(data: Any) -> dict[str, Review]:
    """Validated reviews keyed by ``File:`` title; raises ``ValueError`` on any malformed entry."""
    if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("reviews"), list):
        raise ValueError(f"reviews: expected an object with format {FORMAT!r} and a reviews list")
    result: dict[str, Review] = {}
    for number, entry in enumerate(data["reviews"]):
        label = f"review #{number}"
        if not isinstance(entry, dict):
            raise ValueError(f"{label}: must be an object")
        unknown = set(entry) - {"title", "sha1", "role", "game", "evidence", "game_evidence", "songs",
                                "associations", "pending"}
        if unknown:
            raise ValueError(f"{label}: unknown fields {sorted(unknown)}")
        title = _string(entry, "title", label, required=True)
        label = f"review {title!r}"
        if not title.startswith("File:"):
            raise ValueError(f"{label}: title must start with 'File:'")
        if title in result:
            raise ValueError(f"{label}: duplicate review")
        sha1 = tuple(value.lower() for value in _strings(entry, "sha1", label))
        if not sha1 or not all(_SHA1.match(value) for value in sha1):
            raise ValueError(f"{label}: sha1 must list 40-hex SHA-1 values")
        role = entry.get("role")
        if role is not None and role not in schema.KINDS and role not in REVIEW_EXCLUSIONS:
            raise ValueError(f"{label}: role {role!r} is neither a kind nor a review exclusion")
        game = entry.get("game")
        if game is not None and game not in REVIEW_GAMES:
            raise ValueError(f"{label}: game {game!r} not in {REVIEW_GAMES}")
        evidence = _string(entry, "evidence", label, required=True)
        if not EVIDENCE_PREFIX.match(evidence):
            raise ValueError(f"{label}: evidence must start with its basis, e.g. 'visual_inspection: ...'")
        game_evidence = _string(entry, "game_evidence", label, required=game is not None)
        pending = _string(entry, "pending", label)
        associations = _strings(entry, "associations", label)
        if any(not _ASSOCIATION.match(value) for value in associations):
            raise ValueError(f"{label}: associations must be lowercase [a-z0-9_] names")
        if role == "not_dynamix" and game != "not_dynamix" or role == "universe_only" and game != "dynamix_universe":
            raise ValueError(f"{label}: exclusion {role!r} needs the matching game")
        if role in schema.KINDS and game in ("dynamix", "mixed") and pending:
            raise ValueError(f"{label}: an accepted role and game cannot also be pending")
        if (role is None or (role in schema.KINDS and game not in ("dynamix", "mixed", "dynamix_universe"))) \
                and not pending:
            raise ValueError(f"{label}: a review that establishes no role or no original-game use needs a pending "
                             "reason")
        if game == "mixed" and not associations:
            raise ValueError(f"{label}: game 'mixed' needs associations")
        for text in (evidence, game_evidence, pending):
            if schema.contains_credentials(text, strict=False):
                raise ValueError(f"{label}: evidence must not contain credentials")
        result[title] = Review(title, sha1, role, game, evidence, game_evidence, _strings(entry, "songs", label),
                               associations, pending)
    return result


def load(path: Path | None = None) -> dict[str, Review]:
    """Reviews from ``path`` (default ``scripts/wikis/reviews.json``); an absent file means no reviews."""
    target = path or REVIEWS_PATH
    if not target.is_file():
        return {}
    return parse(json.loads(target.read_text(encoding="utf-8")))


def find(reviews: Mapping[str, Review], title: str, reported_sha1: str | None) -> tuple[Review | None, bool]:
    """``(review, stale)``: the review of ``title`` if the wiki's reported SHA-1 is a reviewed revision; ``stale`` is
    true when a review exists but the reported SHA-1 is not among the reviewed ones (the file changed since)."""
    review = reviews.get(title)
    if review is None:
        return None, False
    if review.applies_to(reported_sha1):
        return review, False
    return None, True
