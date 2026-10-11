"""Adapter output types and the seed-classification rules shared by every adapter.

An adapter turns one seed source into a :class:`Discovery`: the candidates it found (:class:`Draft`), the lists it
enumerated and whether they are complete. A draft says what the runner should do with the candidate (``download``,
``excluded``, ``blocked``, ``failed`` or ``pending``) and carries the descriptive metadata for the asset record.

Classification is curated data, never inferred from keywords alone. A seed rule may assert game identity, kind and a
song mapping only together with ``evidence_quotes`` that must occur in the text the adapter actually fetched; when a
quote is missing the candidate is downgraded to ``pending`` (and its mapping to ``unmapped``) instead of being admitted
on stale or wrong seed data. Two guards override seeds: text announcing a *rejected submission* excludes the candidate,
and text naming *Dynamix Universe* holds it as ``pending`` unless the rule explicitly allows it.

A candidate's ``game`` is the rule's only once the rule admits it (or a curated exclude/pending rule states it); a
candidate held by a guard is ``unknown`` (``dynamix_universe``/``mixed`` when the Universe guard matched). Admission
also checks the asserted mapping and credits against the fetched text: ``confirmed_primary`` needs every mapped song
title in the primary text (or in a ``primary_evidence`` quote), otherwise it becomes ``inferred``; each credited name
gets a ``credit_evidence`` entry quoting the field where a label of its own role leads to it (:func:`locate_credit`),
or an entry without a quote citing the rule's ``credit_source`` when the fetched text does not state it with that role.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from lib import paths, schema
from lib.checks import redact_text

ACTIONS = ("download", "excluded", "blocked", "failed", "pending")
RULE_ACTIONS = ("download", "exclude", "pending")
RULE_FIELDS = {
    "match",
    "title",
    "role",
    "game",
    "action",
    "reason",
    "evidence_quotes",
    "game_evidence",
    "song_titles",
    "collections",
    "illustrators",
    "composers",
    "mapping_status",
    "credit_evidence",
    "variant_group",
    "variant_role",
    "quality",
    "rights",
    "notes",
    "transformed",
    "allow_universe_mention",
    "game_associations",
    "candidate_notes",
    "primary_evidence",
    "upstream_revision",
    "withdraw_archived",
    "credit_source",
    "mapping_evidence",
    "title_aliases",
    "archive_under",
}
ADMITTED_GAMES = ("dynamix", "mixed")  # the only games a download rule may assert (schema: accepted candidates)
REJECTED_RE = re.compile(r"rejected\s+submission", re.IGNORECASE)
UNIVERSE_RE = re.compile(r"dynamix\s*universe", re.IGNORECASE)
COLLABORATION_RE = re.compile(r"collab|コラボ|联动|聯動", re.IGNORECASE)
QUOTE_MAX = 300
EXCERPT_CONTEXT = 40
# 09 (CLI notes): ``awaiting_download`` is an admitted candidate with a known file URL that is not archived yet; an
# item with no file to fetch is ``unresolved_role`` (no file establishes what it shows).
NO_FILE_REASON = "unresolved_role: no downloadable file URL was found for this item"


def awaiting_without_file(candidate: Mapping[str, Any]) -> bool:
    """Whether a ``pending`` candidate says ``awaiting_download`` although it names no file URL to fetch."""
    return (
        candidate.get("disposition") == "pending"
        and not candidate.get("file_url")
        and str(candidate.get("reason") or "").split(":", 1)[0].strip() == "awaiting_download"
    )


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
    # Note recorded when a fallback URL was archived (``{url}``: the URL used, ``{file_url}``: the 404 one).
    fallback_note: str | None = None
    # Adapter hook: the draft describing the bytes of an archived fallback URL (``(draft, url) -> draft``), e.g. the
    # URL the page advertised as ``source_reported.url``, that rendition's file name and a quality that does not call
    # it the upload. Applied on the acquisition that archives the fallback and on every later refresh of that record.
    fallback_adjust: Callable[[Draft, str], Draft] | None = None
    # Curated download rule: the same file is attached to the candidate with this upstream key of the same source,
    # whose text names what the file shows; that candidate holds the record and this one is ``duplicate_bytes``.
    archive_under: str | None = None
    withdraw_archived: bool = False  # curated exclude/pending rule: remove an earlier archived record of this candidate
    # Adapter hook: whether an archived download URL names this draft's file although it differs from ``file_url``
    # (a rotating signature of the same CDN file). ``--resume`` then reuses the archive. A ``fallback_urls`` entry always
    # names the draft's file (the runner checks those itself).
    same_file: Callable[[str], bool] | None = None
    # Adapter hook: an ``awaiting_download: ...`` reason when a completed download is not the file the draft describes
    # (e.g. the Internet Archive redirected a raw capture request to another capture); the bytes are then not archived.
    check_download: Callable[[Any], str | None] | None = None

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
    # List name -> predicate on a previous candidate record: whether a complete read of that list (an upstream
    # enumeration, not explicit seed items) would list the candidate again. The runner marks an archived candidate
    # that every covering list, read completely, no longer lists as removed upstream.
    coverage: dict[str, Callable[[Mapping[str, Any]], bool]] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return all(token is None for token in self.continuation.values())


def discovery_error(phase: str, url: str | None, message: str) -> dict:
    return {"phase": phase, "url": url, "message": redact_text(" ".join(str(message).split())) or phase}


# --- Seed rules -------------------------------------------------------------------------------------------------------

LIST_FIELDS = (
    "evidence_quotes",
    "song_titles",
    "illustrators",
    "composers",
    "collections",
    "game_evidence",
    "game_associations",
)


def _https(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("https://") and not any(c.isspace() for c in value)


def _check_credit_sources(rule: Mapping[str, Any], action: str, where: str) -> None:
    """``credit_source`` is ``{"url": https URL, "note"?: str}``; a curated ``credit_evidence`` entry names a role, a
    name and the https URL that states it. A download rule that credits names must cite a source for every name: a
    ``credit_source`` (used for names its fetched text does not state with their role) or a curated entry per name."""
    source = rule.get("credit_source")
    if source is not None and not (
        isinstance(source, dict)
        and set(source) <= {"url", "note"}
        and _https(source.get("url"))
        and isinstance(source.get("note", ""), str)
    ):
        raise ValueError(f"{where}: credit_source must be an object with an https url (and an optional note)")
    curated = set()
    for index, entry in enumerate(rule.get("credit_evidence", [])):
        if not (
            isinstance(entry, dict)
            and entry.get("role") in schema.CREDIT_ROLES
            and isinstance(entry.get("name"), str)
            and entry["name"].strip()
            and _https(entry.get("url"))
            and all(entry.get(k) is None or isinstance(entry[k], str) and entry[k].strip() for k in ("field", "quote"))
            and set(entry) <= {"role", "name", "url", "field", "quote"}
        ):
            raise ValueError(
                f"{where}: credit_evidence[{index}] needs a role, a name and an https url (field and quote optional)"
            )
        curated.add((entry["role"], entry["name"]))
    credited = [
        *(("illustrator", name) for name in rule.get("illustrators", [])),
        *(("composer", name) for name in rule.get("composers", [])),
    ]
    if action == "download" and source is None and any(credit not in curated for credit in credited):
        raise ValueError(
            f"{where}: a download rule that credits names needs a credit_source (or a curated "
            "credit_evidence entry with a url for every name)"
        )


def check_rule(rule: Mapping[str, Any], where: str) -> None:
    unknown = sorted(set(rule) - RULE_FIELDS)
    if unknown:
        raise ValueError(f"{where}: unknown rule field {unknown[0]!r}")
    for name in LIST_FIELDS:  # a string here would be iterated per character (one-letter "quotes" and credits)
        value = rule.get(name, [])
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            raise ValueError(f"{where}: {name} must be a list of non-empty strings")
    action = rule.get("action", "download")
    if action not in RULE_ACTIONS:
        raise ValueError(f"{where}: action must be one of {RULE_ACTIONS}")
    if "game" in rule and rule["game"] not in schema.GAMES:
        raise ValueError(f"{where}: game must be one of {schema.GAMES}")
    if action == "download" and rule.get("game", schema.GAME) not in ADMITTED_GAMES:
        raise ValueError(
            f"{where}: a download rule admits original-game files only; game must be one of "
            f"{ADMITTED_GAMES} (use an exclude or pending rule for {rule['game']!r})"
        )
    if "withdraw_archived" in rule and (rule["withdraw_archived"] is not True or action == "download"):
        raise ValueError(f"{where}: withdraw_archived must be true and only on an exclude or pending rule")
    if "archive_under" in rule and (
        action != "download" or not isinstance(rule["archive_under"], str) or not rule["archive_under"].strip()
    ):
        raise ValueError(f"{where}: archive_under names an upstream key and is only allowed on a download rule")
    if "{filename}" in str(rule.get("title", "")):
        raise ValueError(f"{where}: a title is human text from the cited page, not the file name ({{filename}})")
    _check_credit_sources(rule, action, where)
    for index, entry in enumerate(rule.get("mapping_evidence", [])):
        if not (
            isinstance(entry, dict)
            and isinstance(entry.get("url"), str)
            and entry["url"].startswith("http")
            and any(isinstance(entry.get(k), str) and entry[k].strip() for k in ("quote", "note"))
        ):
            raise ValueError(f"{where}: mapping_evidence[{index}] needs a url and a quote or note")
    aliases = rule.get("title_aliases", {})
    if not isinstance(aliases, dict) or any(
        title not in rule.get("song_titles", [])
        or not isinstance(names, list)
        or not all(isinstance(name, str) and name.strip() for name in names)
        for title, names in aliases.items()
    ):
        raise ValueError(f"{where}: title_aliases maps a song title of the rule to a list of alias strings")
    if action in ("exclude", "pending") and not rule.get("reason"):
        raise ValueError(f"{where}: an {action} rule needs a reason")
    if action == "pending" and rule["reason"].split(":", 1)[0].strip() not in schema.PENDING_REASONS:
        raise ValueError(f"{where}: a pending reason must start with one of {schema.PENDING_REASONS}")
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
    if rule.get("mapping_status") == "confirmed_primary" and not (
        rule.get("evidence_quotes") or rule.get("primary_evidence")
    ):
        raise ValueError(
            f"{where}: confirmed_primary needs evidence_quotes found in the primary post itself (or, "
            "for a copy of that post's file, primary_evidence quoting it)"
        )
    if rule.get("variant_role") and not rule.get("variant_group"):
        raise ValueError(f"{where}: variant_role requires variant_group")
    if rule.get("game_associations") and schema.GAME not in rule["game_associations"]:
        raise ValueError(f"{where}: game_associations must include {schema.GAME!r}")
    if (
        action == "download"
        and not rule.get("game_associations")
        and any(COLLABORATION_RE.search(name) for name in rule.get("collections", []))
    ):
        raise ValueError(
            f"{where}: a collaboration collection needs game_associations naming the partner (so the "
            "C4Cat default rights holder is not applied to partner art)"
        )
    for index, entry in enumerate(rule.get("primary_evidence", [])):
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(k), str) and entry[k].strip() for k in ("url", "quote")
        ):
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


def _term_pattern(term: str) -> re.Pattern[str] | None:
    """Case-insensitive pattern for ``term`` (NFKC, flexible whitespace); an ASCII alphanumeric edge must not touch
    another ASCII alphanumeric, so ``pan`` does not match inside ``Japan`` nor ``WING`` inside ``drawing``."""
    words = unicodedata.normalize("NFKC", term).split()
    if not words:
        return None
    body = r"\s+".join(re.escape(word) for word in words)
    joined = " ".join(words)
    start = r"(?<![0-9A-Za-z])" if joined[0].isascii() and joined[0].isalnum() else ""
    end = r"(?![0-9A-Za-z])" if joined[-1].isascii() and joined[-1].isalnum() else ""
    return re.compile(start + body + end, re.IGNORECASE)


_ASCII_WORD = r"(?<![0-9A-Za-z]){}(?![0-9A-Za-z])"
# ``曲`` / ``楽曲`` directly followed by a picture word is an illustration label (``曲絵``, ``楽曲イラスト``: the song's
# artwork), never a joint composer+illustrator label.
_SONG_PICTURE = r"(?!絵|イラスト|ジャケ|画)"
ROLE_TERMS = {  # labels that state a credit's role, in the languages the sources use (BGA/movie credits are videos)
    "illustrator": re.compile(
        "|".join(
            [
                # ``cover`` alone is also a vocal/remix cover ("Cover by X"): only ``Cover:`` or ``Cover art/illustration/
                # image/design/picture`` labels the artwork.
                _ASCII_WORD.format(
                    r"(?:illust\w*|art\s*works?|art|jackets?|cover\s*(?:image|design)s?|"
                    r"covers?(?=\s*(?:[:：=]|art|illust|pic))|visual\w*|drawn|drawing|painted|pic(?:ture)?\s*by)"
                ),
                "イラストレーター",
                "イラストレーション",
                "イラスト",
                "ジャケット",
                "ジャケ写",
                "ジャケ",
                "アートワーク",
                "絵師",
                "絵",
                "插画",
                "插圖",
                "画师",
                "美术",
                "封面",
            ]
        ),
        re.IGNORECASE,
    ),
    "composer": re.compile(
        "|".join(
            [
                _ASCII_WORD.format(r"(?:music\w*|compos\w*|arrang\w*|track\s*by|sound)"),
                "作編曲",
                "作曲",
                "編曲",
                "编曲",
                "楽曲提供",
                f"楽曲{_SONG_PICTURE}",
                "音楽",
                "音乐",
                f"曲{_SONG_PICTURE}",
            ]
        ),
        re.IGNORECASE,
    ),
}
# What may stand between a credit label and the credited name: separators (``: ``, `` by ``, ``は``, ``@``), a joined
# label word (``Composition/Vocal:``) and earlier names of the same list (``Composed by A and B``, ``A & B``, ``A X B``).
# Anything else (``music package "Kplecraft Collection"``, ``Illustration by A B:``) means the label credits someone else.
_LABEL_GAP = re.compile(
    r"(?:\s*/\s*[A-Za-z]+)*[\s:：=\-–—・@＠]*(?:by|は|が)?[\s:：=\-–—@＠]*"
    r"(?:[^\n:：。．!！?？「」『』\"“”]{1,40}?(?:\s+(?:and|x|×|vs\.?|feat\.?)\s+|\s*[&＆,、/×]\s*)@?)?",
    re.IGNORECASE,
)
# A credit label is read back from the name to the nearest of these (a new line or list bullet starts a new credit).
_SEGMENT_BREAK = re.compile(r"[\n\r|｜◆◇■□●○★☆▼▽▶►•]|-{3,}|={3,}")
_LABEL_JOIN = re.compile(r"^(?:[\s&/+＆・,、]|and|と|及び)*$", re.IGNORECASE)
LABEL_WINDOW = 120


def locate_term(term: str, texts: Mapping[str, str]) -> tuple[str, str] | None:
    """(field, excerpt with context) of the first text naming ``term`` as a whole word, None when none does."""
    pattern = _term_pattern(term)
    if pattern is None:
        return None
    for name, text in texts.items():
        if not text:
            continue
        value = unicodedata.normalize("NFKC", text)
        for match in pattern.finditer(value):
            return name, _excerpt(value, match.start(), match.end())
    return None


def _excerpt(value: str, start: int, end: int, context: int = EXCERPT_CONTEXT) -> str:
    first, last = max(0, start - context), end + context
    return clip(("…" if first else "") + value[first:last] + ("…" if last < len(value) else ""), 160)


def label_roles(before: str) -> tuple[set[str], int | None]:
    """Roles stated by the credit label that precedes a name: the text since the last segment break is searched for role
    terms; the last term found is the label, together with terms joined to it only by ``&``, ``/``, ``and`` ... (a
    joint label such as ``Music&Artwork``). Only a label that the name follows directly or in a list of names
    (``_LABEL_GAP``) counts. Returns (roles, label start offset in ``before``) or (set(), None)."""
    breaks = list(_SEGMENT_BREAK.finditer(before))
    offset = breaks[-1].end() if breaks else 0
    segment = before[offset:]
    if not segment.strip() and breaks:  # "Illustration:" on its own line, the name on the next one
        previous = before[: breaks[-1].start()]
        earlier = list(_SEGMENT_BREAK.finditer(previous))
        start = earlier[-1].end() if earlier else 0
        if previous[start:].rstrip().endswith((":", "：")):
            offset, segment = start, before[start:]
    found = sorted(
        (
            (match.start(), match.end(), role)
            for role, pattern in ROLE_TERMS.items()
            for match in pattern.finditer(segment)
        ),
        key=lambda item: (item[0], -item[1]),
    )
    if not found:
        return set(), None
    group = [found[-1]]
    if not _LABEL_GAP.fullmatch(segment[group[0][1] :]):
        return set(), None
    for item in reversed(found[:-1]):
        if item[1] <= group[-1][0] and _LABEL_JOIN.match(segment[item[1] : group[-1][0]]):
            group.append(item)
        elif item[1] <= group[-1][0]:
            break
    return {item[2] for item in group}, offset + group[-1][0]


def locate_credit(name: str, texts: Mapping[str, str], role: str) -> tuple[str, str] | None:
    """(field, quote) of the first occurrence of ``name`` (whole word) whose preceding credit label states ``role``
    (``Artwork: X``, ``Composed by X``, ``イラストはXさん``); None when no occurrence does. An occurrence after a label of
    the other role only (``Music: X`` for an illustrator), or after no label at all (a title ``Artist - Song``), is no
    evidence for ``role``. The quote runs from the label to just after the name."""
    pattern = _term_pattern(name)
    if pattern is None or role not in ROLE_TERMS:
        return None
    for field_name, text in texts.items():
        if not text:
            continue
        value = unicodedata.normalize("NFKC", text)
        for match in pattern.finditer(value):
            window = max(0, match.start() - LABEL_WINDOW)
            roles, label = label_roles(value[window : match.start()])
            if role in roles:
                start, end = min(window + label, max(0, match.start() - EXCERPT_CONTEXT)), match.end() + EXCERPT_CONTEXT
                return field_name, clip(
                    ("…" if start else "") + value[start:end] + ("…" if end < len(value) else ""), 200
                )
    return None


def hold(rule: Mapping[str, Any] | None, reason: str) -> dict:
    """An adapter override that holds a candidate as ``pending`` (e.g. a post by another account): the rule's song
    and credit fields stay visible on the candidate, but its game claim and any withdrawal do not apply."""
    return {**(rule or {}), "action": "pending", "reason": reason, "game": "unknown", "withdraw_archived": False}


def guard_texts(texts: Mapping[str, str]) -> tuple[str | None, str | None]:
    """(rejected-submission excerpt, Universe excerpt) found in the fetched texts."""
    rejected = universe = None
    for text in texts.values():
        if not text:
            continue
        match = REJECTED_RE.search(text)
        if match and rejected is None:
            rejected = clip(text[max(0, match.start() - 60) : match.end() + 40], 160)
        match = UNIVERSE_RE.search(text)
        if match and universe is None:
            universe = clip(text[max(0, match.start() - 60) : match.end() + 40], 160)
    return rejected, universe


def apply_rule(
    rule: Mapping[str, Any] | None,
    *,
    key: str,
    page_url: str,
    texts: Mapping[str, str],
    file_url: str | None,
    default_title: str,
    default_quality: Mapping[str, Any],
    default_rights: Mapping[str, Any],
    source_reported: dict | None = None,
    original_filename: str | None = None,
    base_evidence: Iterable[str] = (),
    unclassified_reason: str = "unresolved_role: no seed rule for this item; needs review",
) -> Draft:
    """A :class:`Draft` for one discovered item from its seed rule and the fetched ``texts``.

    No rule -> ``pending`` (never admitted on keywords alone). A download rule needs every ``evidence_quotes`` entry in
    ``texts``; otherwise the item becomes ``pending`` with the missing quotes named. Guards: a rejected-submission
    announcement excludes the item; a Dynamix Universe mention holds it as ``pending`` unless allowed by the rule.
    The rule's ``game`` applies only when the rule admits the item or is itself a curated exclude/pending rule; an item
    held by a guard is ``unknown`` (or ``dynamix_universe``/``mixed`` after a Universe mention).
    """
    reported = source_reported or schema.make_source_reported(url=file_url)
    rule = dict(rule or {})
    title = (rule.get("title") or default_title).replace("{default}", default_title)
    found, missing = find_quotes(rule.get("evidence_quotes", []), texts, page_url)
    game_evidence = [
        *base_evidence,
        *rule.get("game_evidence", []),
        *(f"{item['field']}: {item['quote']}" for item in found),
    ]
    draft = Draft(
        upstream_key=key,
        page_url=page_url,
        title=title,
        action="pending",
        file_url=file_url,
        role=rule.get("role"),
        game="unknown",
        game_evidence=list(dict.fromkeys(game_evidence)),
        source_reported=reported,
        original_filename=original_filename,
    )
    extra = {
        name: list(rule[name]) for name in ("song_titles", "illustrators", "composers", "collections") if rule.get(name)
    }
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
    if action in ("exclude", "pending"):  # curated decisions: the rule's own game statement applies
        draft.withdraw_archived = bool(rule.get("withdraw_archived"))
        draft.game = rule.get("game", "unknown")
        draft.reason = rule["reason"]
        if action == "exclude":
            draft.action = "excluded"
            prefix = rule["reason"].split(":", 1)[0].strip()
            draft.game = {"universe_only": "dynamix_universe", "not_dynamix": "not_dynamix"}.get(prefix, draft.game)
        return draft
    if missing:
        draft.reason = (
            "unresolved_game: seed evidence not found in the fetched content ("
            + "; ".join(clip(quote, 80) for quote in missing)
            + ")"
        )
        return draft
    if universe and not rule.get("allow_universe_mention"):
        # The fetched text names the sequel: original-game use is unconfirmed until a curator decides.
        draft.game = "mixed" if found else "dynamix_universe"
        draft.reason = (
            f"unresolved_game: upstream text mentions Dynamix Universe; confirm original-game use ({universe})"
        )
        return draft
    draft.game = rule.get("game", schema.GAME)  # check_rule allows only dynamix/mixed on a download rule
    if not file_url:
        draft.reason = NO_FILE_REASON
        return draft
    draft.action = "download"
    draft.archive_under = rule.get("archive_under")
    draft.meta = asset_meta(rule, found, page_url, default_quality, default_rights, texts)
    return draft


def _title_supported(names: Iterable[str], texts: Mapping[str, str], primary_quotes: Iterable[str]) -> bool:
    """Whether the fetched text or a primary-post quote names the song by its title or a declared alias."""
    quotes = [normalize_text(quote) for quote in primary_quotes]
    return any(locate_term(name, texts) or any(normalize_text(name) in quote for quote in quotes) for name in names)


def mapping_fields(
    rule: Mapping[str, Any], found: list[dict], page_url: str, texts: Mapping[str, str]
) -> tuple[str, list, list[str]]:
    """(mapping_status, mapping_evidence, notes) for a download rule.

    The fetched text that names a mapped song title (or one of its ``title_aliases``, e.g. the index alias
    ``MassacrE`` of ``MassacrE || 3rc4224M``) is quoted as evidence. ``confirmed_primary`` asserts that the primary
    post itself maps the file to the song, so every title must be named by the fetched text or by a
    ``primary_evidence`` quote; otherwise the status becomes ``inferred`` and a note says which title is unsupported.
    """
    song_titles = list(rule.get("song_titles", []))
    status = rule.get("mapping_status", "unmapped") if song_titles else "unmapped"
    if status == "unmapped":
        return status, [], []
    evidence, notes = list(found), []
    primary = list(rule.get("primary_evidence", []))
    aliases = rule.get("title_aliases", {})
    for title in song_titles:
        hit = next(filter(None, (locate_term(name, texts) for name in [title, *aliases.get(title, [])])), None)
        if hit and not any(entry.get("field") == hit[0] and entry.get("quote") == hit[1] for entry in evidence):
            evidence.append({"url": page_url, "field": hit[0], "quote": hit[1]})
    if status == "confirmed_primary":
        unsupported = [
            title
            for title in song_titles
            if not _title_supported([title, *aliases.get(title, [])], texts, [entry["quote"] for entry in primary])
        ]
        if unsupported:
            status = "inferred"
            notes.append(
                "Mapping recorded as inferred, not confirmed_primary: the primary text does not name "
                + ", ".join(f"“{title}”" for title in unsupported)
                + "."
            )
    evidence += [
        {
            "url": entry["url"],
            "field": "primary_post",
            "quote": clip(entry["quote"]),
            **({"note": entry["note"]} if entry.get("note") else {}),
        }
        for entry in primary
    ]
    evidence += [
        {key: (clip(value) if key == "quote" else value) for key, value in entry.items()}
        for entry in rule.get("mapping_evidence", [])
    ]
    if not evidence:
        evidence = [{"url": page_url, "note": "seed classification (no quote required)"}]
    return status, evidence, notes


def credit_fields(rule: Mapping[str, Any], page_url: str, texts: Mapping[str, str]) -> tuple[list[dict], list[str]]:
    """(credit_evidence, notes): one entry per credited name. A curated ``credit_evidence`` entry wins; otherwise the
    fetched field that states the name **with its role** (:func:`locate_credit`) is quoted. A name the fetched text
    does not state for that role (absent, or only in a title or under the other role's label) gets an entry without a
    quote that cites ``credit_source`` (the page the seed credit comes from, e.g. the wiki song page), and a note says
    so; composer and illustrator never borrow each other's evidence. Without a ``credit_source`` such an entry has no
    URL, which ``--verify`` reports (``check_rule`` requires the source on every download rule that credits names)."""
    curated = [
        {
            "role": entry["role"],
            "name": entry["name"],
            "url": entry.get("url"),
            "field": entry.get("field"),
            "quote": clip(entry["quote"]) if entry.get("quote") else None,
        }
        for entry in rule.get("credit_evidence", [])
    ]
    named = {(entry["role"], entry["name"]) for entry in curated}
    source = rule.get("credit_source") or {}
    entries, unstated = list(curated), []
    for role, names in (("illustrator", rule.get("illustrators", [])), ("composer", rule.get("composers", []))):
        for name in names:
            if (role, name) in named:
                continue
            hit = locate_credit(name, texts, role)
            if hit:
                entries.append({"role": role, "name": name, "url": page_url, "field": hit[0], "quote": hit[1]})
            else:
                entries.append({"role": role, "name": name, "url": source.get("url"), "field": None, "quote": None})
                unstated.append(f"{name} ({role})")
    notes = []
    if unstated:
        origin = " ".join(filter(None, [source.get("note"), source.get("url")])) or "no cited source"
        notes.append(
            "Credits the fetched text does not state with their role, cited from "
            + origin
            + ": "
            + ", ".join(unstated)
            + "."
        )
    return entries, notes


def asset_meta(
    rule: Mapping[str, Any],
    found: list[dict],
    page_url: str,
    default_quality: Mapping[str, Any],
    default_rights: Mapping[str, Any],
    texts: Mapping[str, str] | None = None,
) -> dict:
    """Descriptive asset fields from a download rule (the runner adds the measured fields)."""
    texts = texts or {}
    status, evidence, notes = mapping_fields(rule, found, page_url, texts)
    credits, credit_notes = credit_fields(rule, page_url, texts)
    song_titles = list(rule.get("song_titles", []))
    quality = {**copy.deepcopy(dict(default_quality)), **rule.get("quality", {})}
    rights = {**copy.deepcopy(dict(default_rights)), **rule.get("rights", {})}
    partners = [game for game in rule.get("game_associations", []) if game != schema.GAME]
    if partners and "holder" not in rule.get("rights", {}):
        # Collaboration / multi-game art: the C4Cat default holder applies only when a cited source supports it,
        # which a rule states with an explicit rights.holder (e.g. the partner the page names).
        rights["holder"] = None
    if partners and rights.get("holder") is None:
        notes.append(
            "Rights holder not recorded: the art is also associated with "
            + ", ".join(partners)
            + " and no cited source names its holder, so the C4Cat default is not applied."
        )
    if rights.get("url") is None:
        rights["url"] = page_url
    if rights.get("creator") is None and rule.get("illustrators"):
        rights["creator"] = ", ".join(rule["illustrators"])  # the credited illustrator(s), verbatim
    meta = {
        "illustrators": list(rule.get("illustrators", [])),
        "composers": list(rule.get("composers", [])),
        "song_titles": song_titles if status != "unmapped" else [],
        "song_ids": [paths.song_id_base(title) for title in song_titles] if status != "unmapped" else [],
        "collections": list(rule.get("collections", [])),
        "mapping_status": status,
        "mapping_evidence": evidence,
        "quality": quality,
        "rights": rights,
    }
    if credits:
        meta["credit_evidence"] = credits
    for name in ("variant_group", "variant_role", "notes", "transformed", "game_associations", "upstream_revision"):
        if name in rule:
            meta[name] = copy.deepcopy(rule[name])
    generated = [*notes, *credit_notes]
    if generated:
        meta["notes"] = " ".join(filter(None, [meta.get("notes"), *generated]))
    return meta


def error_draft(
    key: str,
    page_url: str,
    title: str,
    error: Any,
    *,
    phase: str,
    file_url: str | None = None,
    role: str | None = None,
    game: str = "unknown",
    game_evidence: Iterable[str] = (),
    source_reported: dict | None = None,
) -> Draft:
    """A ``blocked``/``failed`` draft for an item whose discovery or download raised ``error``."""
    error_class = getattr(error, "error_class", "other")
    blocked = error_class in ("blocked", "challenge")
    status = getattr(error, "http_status", None)
    detail = getattr(error, "message", None) or str(error)
    reason = f"{error_class}: {detail}" + ("" if status is None or str(status) in detail else f" (HTTP {status})")
    if blocked:
        reason += "; manual access required, not retried"
    return Draft(
        upstream_key=key,
        page_url=page_url,
        title=title,
        action="blocked" if blocked else "failed",
        file_url=file_url,
        role=role,
        game=game,
        game_evidence=list(game_evidence),
        source_reported=source_reported or schema.make_source_reported(url=file_url),
        reason=clip(reason, 400),
        error=error,
        error_phase=phase,
    )
