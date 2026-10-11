"""Archivist reviews of wiki files that page evidence leaves unresolved (``scripts/wikis/reviews.json``).

A review records what an archivist established for one ``File:`` title when templates, captions, list pages,
categories and file usage say nothing (09-frozen-contracts.md, CLI notes): the role seen on visual inspection of the
downloaded bytes, the game and why, songs named by the image or its exact filename, or an exclusion from the frozen
vocabulary. Evidence text is kept verbatim and starts with its basis (``visual_inspection: ...``); a song mapping that
rests on a review is at most ``inferred``.

A review is pinned to the file revisions it looked at: ``sha1`` lists the API SHA-1 values the wikis reported for that
title when it was reviewed, and it applies to a wiki's file only while that wiki still reports one of them. After a
re-upload the review no longer applies and the file falls back to its page evidence (pending), so a reviewer looks
again. Page evidence wins, with the exceptions below: a review supplies a role only when no page gives one (filename
conventions aside) or to break a tie between equally supported page roles (the mapping is then ``inferred`` at most),
and a game only when no page context gives one; a ``mixed`` review's associations (another game's identity seen in
the bytes) are added to the original game that page context gives. One refinement concerns exclusions only: when
page evidence excludes a file (e.g. as a wiki interface image because the Main Page banner template shows it) and the
review saw another exclusion in the bytes (e.g. a Dynamix Universe announcement), the review's exclusion reason is
recorded; a review never turns a file that page evidence excludes into a download, with one explicit exception:
``official_branding: true`` (a kind as role, an original-game or shared game) records that the bytes are the game's own
branding (its wordmark) that the wiki reuses as an interface image. Page evidence whose only role is ``site_ui`` (the
Main Page header) describes the wiki's use, not the file, so such a review decides the role (mapping ``inferred`` at
most) and the overridden page evidence is recorded. ``animated: "official"`` marks a
GIF the review established as an official animation (not a wiki capture with a larger still), so the default
``animated_capture`` exclusion does not apply.

The coordinator classification decision of 2026-10-10 as amended (09-frozen-contracts.md, "List and
chart-announcement graphics are reference") is the other exception. ``list_graphic: true`` (role ``reference`` only)
records that the bytes show a graphic of that rule (:data:`wikis.config.LIST_GRAPHIC_RULE`): an announcement that
displays chart difficulty levels, for one song or several (a "COMING SOON" or "i'm back!" card with level marks, a
Giga chart unlock), or a list of songs or charts (an event lineup, a "Comeback Tracks" list, a pack/collaboration
lineup card printing several titled jackets). Such a review makes the file ``reference`` even where page evidence of
any strength files it as promotional (a category, or captions on several pages), and that page evidence and the rule
are recorded with the decision; the songs it names are its transcription of the bytes and the whole song mapping (a
song that page evidence names -- a caption, a host song page -- but the bytes do not show is not mapped, and the
decision records it). Conversely a review with role ``promotional`` saw neither a list nor level marks in
the bytes (key art, an event poster, a single collaboration key visual, a pack card with one titled jacket) and keeps
the file promotional against list captions.
A review whose role is null and whose evidence is a ``download_probe:`` (the host refused the bytes, so they could
not be inspected) makes the acquisition retry the download as an inspection probe: a refusal is recorded as a
download Failure, a success is reported for review.

Two further tables, also pinned to reviewed bytes:

- ``notes``: archivist notes appended to a title's candidate and asset notes (e.g. the same picture under another
  title), keyed by title and API SHA-1;
- ``withdrawals``: reviewed decisions that an *archived* file leaves the manifest (record and file deleted, Git
  history keeps them) because complete evidence now excludes it with the reviewed reason. Keyed by title and the
  archived bytes' SHA-256; anything else that stops accepting an archived file stops the run for review.
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
    official_animation: bool = False  # an official animation: animated_capture does not apply
    list_graphic: bool = False  # the bytes show a list or a chart announcement (config.LIST_GRAPHIC_RULE; reference)
    official_branding: bool = False  # the game's own branding reused by the wiki as an interface image (see above)

    @property
    def probe(self) -> bool:
        """The bytes could not be inspected because the host refused them (retry as an inspection download)."""
        return self.role is None and self.evidence.startswith("download_probe:")

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
                                "associations", "pending", "animated", "list_graphic", "official_branding"}
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
        animated = entry.get("animated")
        if animated not in (None, "official"):
            raise ValueError(f"{label}: animated must be 'official' when present")
        if animated and role not in schema.KINDS:
            raise ValueError(f"{label}: animated 'official' needs a kind as role")
        list_graphic = entry.get("list_graphic", False)
        if not isinstance(list_graphic, bool):
            raise ValueError(f"{label}: list_graphic must be true or false")
        if list_graphic and role != "reference":
            raise ValueError(f"{label}: list_graphic needs role 'reference' (the list and chart-announcement rule)")
        branding = entry.get("official_branding", False)
        if not isinstance(branding, bool):
            raise ValueError(f"{label}: official_branding must be true or false")
        if branding and (role not in schema.KINDS or game not in ("dynamix", "mixed")):
            raise ValueError(f"{label}: official_branding needs a kind as role and an original-game or shared game")
        for text in (evidence, game_evidence, pending):
            if schema.contains_credentials(text, strict=False):
                raise ValueError(f"{label}: evidence must not contain credentials")
        result[title] = Review(title, sha1, role, game, evidence, game_evidence, _strings(entry, "songs", label),
                               associations, pending, official_animation=animated == "official",
                               list_graphic=list_graphic, official_branding=branding)
    return result


@dataclass(frozen=True)
class Note:
    """An archivist note for one title, pinned to the reviewed revision(s) by API SHA-1."""

    title: str
    sha1: tuple[str, ...]
    note: str  # "<basis>: <observation>"

    def applies_to(self, reported_sha1: str | None) -> bool:
        return bool(reported_sha1) and reported_sha1.lower() in self.sha1

    def text(self) -> str:
        return f"archivist note ({self.title}): {self.note}"


@dataclass(frozen=True)
class Withdrawal:
    """A reviewed decision that an archived file leaves the manifest (see the module docstring)."""

    title: str
    sha256: tuple[str, ...]  # SHA-256 of the archived bytes the decision is about
    reason: str  # exclusion reason prefix the current evidence must give (frozen vocabulary)
    evidence: str  # "<basis>: <observation>"

    def covers(self, sha256: str, decision: Any) -> bool:
        return (sha256 in self.sha256 and decision.disposition == "excluded"
                and decision.reason.split(":", 1)[0].strip() == self.reason)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _table(data: Any, name: str) -> list:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError(f"reviews: expected an object with format {FORMAT!r}")
    entries = data.get(name, [])
    if not isinstance(entries, list):
        raise ValueError(f"reviews: {name} must be a list")
    return entries


def _entry(entry: Any, number: int, name: str, fields: set[str]) -> tuple[str, str]:
    label = f"{name} #{number}"
    if not isinstance(entry, dict):
        raise ValueError(f"{label}: must be an object")
    unknown = set(entry) - fields
    if unknown:
        raise ValueError(f"{label}: unknown fields {sorted(unknown)}")
    title = _string(entry, "title", label, required=True)
    if not title.startswith("File:"):
        raise ValueError(f"{label}: title must start with 'File:'")
    return title, f"{name} {title!r}"


def _basis_text(entry: Mapping[str, Any], name: str, label: str) -> str:
    text = _string(entry, name, label, required=True)
    if not EVIDENCE_PREFIX.match(text):
        raise ValueError(f"{label}: {name} must start with its basis, e.g. 'visual_inspection: ...'")
    if schema.contains_credentials(text, strict=False):
        raise ValueError(f"{label}: {name} must not contain credentials")
    return text


def parse_notes(data: Any) -> dict[str, Note]:
    result: dict[str, Note] = {}
    for number, entry in enumerate(_table(data, "notes")):
        title, label = _entry(entry, number, "note", {"title", "sha1", "note"})
        if title in result:
            raise ValueError(f"{label}: duplicate note")
        sha1 = tuple(value.lower() for value in _strings(entry, "sha1", label))
        if not sha1 or not all(_SHA1.match(value) for value in sha1):
            raise ValueError(f"{label}: sha1 must list 40-hex SHA-1 values")
        result[title] = Note(title, sha1, _basis_text(entry, "note", label))
    return result


def parse_withdrawals(data: Any) -> dict[str, Withdrawal]:
    result: dict[str, Withdrawal] = {}
    for number, entry in enumerate(_table(data, "withdrawals")):
        title, label = _entry(entry, number, "withdrawal", {"title", "sha256", "reason", "evidence"})
        if title in result:
            raise ValueError(f"{label}: duplicate withdrawal")
        sha256 = tuple(value.lower() for value in _strings(entry, "sha256", label))
        if not sha256 or not all(_SHA256.match(value) for value in sha256):
            raise ValueError(f"{label}: sha256 must list 64-hex SHA-256 values of the archived bytes")
        reason = _string(entry, "reason", label, required=True)
        if reason not in schema.EXCLUSION_REASONS:
            raise ValueError(f"{label}: reason {reason!r} is not a frozen exclusion reason")
        result[title] = Withdrawal(title, sha256, reason, _basis_text(entry, "evidence", label))
    return result


def _read(path: Path | None) -> Any:
    target = path or REVIEWS_PATH
    return json.loads(target.read_text(encoding="utf-8")) if target.is_file() else None


def load_notes(path: Path | None = None) -> dict[str, Note]:
    data = _read(path)
    return {} if data is None else parse_notes(data)


def load_withdrawals(path: Path | None = None) -> dict[str, Withdrawal]:
    data = _read(path)
    return {} if data is None else parse_withdrawals(data)


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
