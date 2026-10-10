"""Live MediaWiki inventories and their classification into per-file plans (one candidate per wiki file).

An :class:`Inventory` holds everything one wiki's API returned (``allimages``, file usage/categories, article
wikitext, GIF frame counts) together with each list's completeness and open continuation. Classification pools the
evidence of both English wikis by file title (Miraheze imported Fandom's files under the same titles), so it always
uses both inventories even when only one source is acquired. Inventories are not committed; the discovery snapshot
keeps the per-candidate result and its evidence strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from lib import manifest, paths, schema

from . import classify, config, mediawiki

LISTS = ("allimages", "fileusage", "pages", "gifmetadata", "expandtemplates")
NOTES_LIMIT = 1500


@dataclass
class Inventory:
    source: config.WikiSource
    started_at: str
    finished_at: str
    files: dict[str, dict] = field(default_factory=dict)
    usage: dict[str, dict] = field(default_factory=dict)
    pages: dict[str, dict] = field(default_factory=dict)
    gif_frames: dict[str, int | None] = field(default_factory=dict)
    continuation: dict[str, dict | None] = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)
    requests: int = 0

    @property
    def complete(self) -> bool:
        return all(self.continuation.get(name, {}) is None for name in LISTS)


def fetch_inventory(client: Any, source: config.WikiSource, *, cache_dir: str | Path | None = None,
                    now=manifest.now_utc) -> Inventory:
    """Enumerate one MediaWiki wiki. Every list follows its continuation to the end; a failed list keeps its open
    continuation and error (the inventory is then incomplete, never silently partial)."""
    api = mediawiki.MediaWikiAPI(client, source.api_url,
                                 cache_dir=Path(cache_dir) / source.key if cache_dir else None)
    inventory = Inventory(source, now(), now())
    results = {"allimages": mediawiki.allimages(api)}
    results["fileusage"] = mediawiki.file_usage(api)
    results["pages"] = mediawiki.content_pages(api)
    gif_titles = sorted(title for title, row in results["allimages"].items.items()
                        if (row.get("mime") or "").lower() == "image/gif")
    results["gifmetadata"] = mediawiki.gif_frames(api, gif_titles)
    results["expandtemplates"] = mediawiki.expand_templates(api, templated_titles(results["pages"].items))
    inventory.files = results["allimages"].items
    inventory.usage = results["fileusage"].items
    expansions = results["expandtemplates"].items
    inventory.pages = {title: {**page, "title_expansions": expansions} if expansions else page
                       for title, page in results["pages"].items.items() if page.get("ns") == 0}
    inventory.gif_frames = results["gifmetadata"].items
    for name in LISTS:
        result = results[name]
        inventory.continuation[name] = None if result.complete else dict(result.continuation or {})
        inventory.errors.extend(result.errors)
    inventory.requests = api.requests
    inventory.finished_at = now()
    return inventory


def templated_titles(pages: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """Song template ``Title`` values that use templates (raw value -> page title), to be expanded by the API."""
    from . import wikitext
    found: dict[str, str] = {}
    for title, page in sorted(pages.items()):
        content = (page.get("revision") or {}).get("content") or ""
        for template in wikitext.find_templates(content, set(classify.SONG_TEMPLATES)):
            raw = template.params.get("Title", "").strip()
            if "{{" in raw:
                found.setdefault(raw, title)
    return found


# --- Plans ------------------------------------------------------------------------------------------------------------

@dataclass
class SongCredit:
    """Credits of one original-game song as the song template states them on one wiki."""

    title: str
    song_id: str
    page_url: str
    source_id: str
    illustrators: list[str]
    composers: list[str]
    illustrator_field: str | None
    composer_field: str | None
    illustrator_raw: str
    composer_raw: str
    imgsuffix: str | None
    collections: list[str]


@dataclass
class Plan:
    """One wiki file: its candidate (without acquisition state) and what an asset record needs."""

    source: config.WikiSource
    title: str  # "File:..." canonical title
    info: dict
    decision: classify.Decision
    candidate: dict
    accepted: bool
    illustrators: list[str] = field(default_factory=list)
    composers: list[str] = field(default_factory=list)
    song_titles: list[str] = field(default_factory=list)
    song_ids: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    credit_evidence: list[dict] = field(default_factory=list)

    @property
    def candidate_id(self) -> str:
        return self.candidate["id"]

    @property
    def kind(self) -> str | None:
        return self.decision.role


def upstream_key(title: str) -> str:
    return title.replace(" ", "_")


def file_name(title: str) -> str:
    return title.split(":", 1)[1] if ":" in title else title


def display_title(title: str) -> str:
    """``File:Cosmic Pulse Chart List.jpg`` -> ``Cosmic Pulse Chart List`` (verbatim otherwise)."""
    return paths.filename_stem(file_name(title))


def download_url(source: config.WikiSource, url: str) -> str:
    """The URL that is actually requested: the API file URL plus the source's extra query (Fandom
    ``format=original``)."""
    if not source.download_query:
        return url
    return url + ("&" if "?" in url else "?") + source.download_query


def source_reported(info: Mapping[str, Any]) -> dict:
    def number(key: str) -> int | None:
        value = info.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    sha1 = info.get("sha1")
    mime = info.get("mime")
    url = info.get("url")
    return schema.make_source_reported(
        url=url if isinstance(url, str) and schema.checks.url_problem(url) is None else None,
        mime=mime if isinstance(mime, str) and mime.strip() else None,
        bytes=number("size"), width=number("width") or None, height=number("height") or None,
        sha1=sha1.lower() if isinstance(sha1, str) and len(sha1) == 40 else None,
        timestamp=info.get("timestamp") if isinstance(info.get("timestamp"), str) else None, revision=None)


def _role_notes(decision: classify.Decision) -> str:
    if not decision.role_evidence:
        return "no role evidence"
    text = "role evidence: " + "; ".join(decision.role_evidence)
    return text if len(text) <= NOTES_LIMIT else text[:NOTES_LIMIT - 3] + "..."


def _clean_list(values: list[str]) -> list[str]:
    result = []
    for value in values:
        value = schema.redact_text(value, strict=False).strip() if isinstance(value, str) else ""
        if value and value not in result:
            result.append(value)
    return result


def base_candidate(source: config.WikiSource, title: str, info: Mapping[str, Any],
                   decision: classify.Decision) -> dict:
    key = upstream_key(title)
    page = info.get("descriptionurl")
    if not isinstance(page, str) or schema.checks.http_url(page) is not None:
        page = config.page_url(source, title)
    reported = source_reported(info)
    candidate = {
        "id": schema.candidate_id(source.source_id, key), "source_id": source.source_id, "upstream_key": key,
        "page_url": page, "file_url": reported["url"], "title": display_title(title) or key,
        "role": decision.role if decision.role in schema.KINDS else None, "game": decision.game,
        "game_evidence": _clean_list(decision.game_evidence), "source_reported": reported,
        "disposition": "excluded" if decision.disposition == "excluded" else "pending",
        "reason": decision.reason or None,
    }
    notes = _role_notes(decision)
    if notes:
        candidate["notes"] = notes
    return candidate


def song_credits(songs: Mapping[str, Any]) -> dict[str, list[SongCredit]]:
    """Exact song title -> credits from the song index entries (see :mod:`wikis.songindex`)."""
    result: dict[str, list[SongCredit]] = {}
    for entry in songs.values():
        for credit in entry.credits:
            result.setdefault(credit.title, []).append(credit)
    return result


def build_plans(inventories: Mapping[str, Inventory], song_index: Any, *, include_animated: bool
                ) -> dict[str, list[Plan]]:
    """Classify every file of every inventory (evidence pooled by title across the wikis)."""
    wikis = {key: classify.collect(inv.source, inv.files, inv.usage, inv.pages, inv.gif_frames)
             for key, inv in inventories.items()}
    threshold = classify.universe_threshold(wikis.values())
    pooled: dict[str, list[classify.Evidence]] = {}
    for wiki in wikis.values():
        for title, items in wiki.evidence.items():
            pooled.setdefault(title, []).extend(items)
    frames: dict[str, int | None] = {}
    for inv in inventories.values():
        for title, value in inv.gif_frames.items():
            if value is not None:
                frames[title] = value
    fandom = inventories.get("fandom")
    plans: dict[str, list[Plan]] = {}
    for key, inv in inventories.items():
        result = []
        for title in sorted(inv.files):
            info = inv.files[title]
            stamp = fandom.files.get(title, {}).get("timestamp") if fandom else None
            decision = classify.decide(title, pooled.get(title, []), info, frames=frames.get(title),
                                       include_animated=include_animated, fandom_timestamp=stamp,
                                       threshold=threshold)
            plan = Plan(inv.source, title, info, decision, base_candidate(inv.source, title, info, decision),
                        accepted=decision.disposition == "download")
            _attach_songs(plan, pooled.get(title, []), song_index)
            result.append(plan)
        plans[key] = result
    return plans


def _attach_songs(plan: Plan, items: list[classify.Evidence], song_index: Any) -> None:
    """Song titles/IDs, composers, collections and (song art only) illustrators for a plan and its candidate."""
    decision = plan.decision
    titles = [title for title in decision.songs if song_index.lookup(title) is not None]
    if decision.role == "pack_cover":
        packs = sorted({item.page for item in items if item.role == "pack_cover" and item.strength >= 3})
        plan.collections = packs
    for title in titles:
        entry = song_index.lookup(title)
        plan.song_titles.append(entry.title)
        plan.song_ids.append(entry.id)
        for value in entry.collections:
            if value not in plan.collections:
                plan.collections.append(value)
        # Composers are credits of the song; the reconciled (Miraheze-first) names, with the template field quoted.
        for name in entry.composers:
            credit = next((c for c in entry.credits if name in c.composers), None)
            if name not in plan.composers and credit is not None:
                plan.composers.append(name)
                plan.credit_evidence.append(_credit("composer", name, credit, credit.composer_field,
                                                    credit.composer_raw))
        # Illustrators only for the song's own cover: this file is the Imgsuffix of its Template:Song on some wiki.
        is_cover = decision.role == "song_art" and any(
            credit.imgsuffix and "File:" + credit.imgsuffix == plan.title for credit in entry.credits)
        for name in entry.illustrators if is_cover else []:
            credit = next((c for c in entry.credits if name in c.illustrators), None)
            if name not in plan.illustrators and credit is not None:
                plan.illustrators.append(name)
                plan.credit_evidence.append(_credit("illustrator", name, credit, credit.illustrator_field,
                                                    credit.illustrator_raw))
    plan.song_titles = list(dict.fromkeys(plan.song_titles))
    plan.song_ids = list(dict.fromkeys(plan.song_ids))
    candidate = plan.candidate
    for field_name in ("song_titles", "illustrators", "composers", "collections"):
        values = getattr(plan, field_name)
        if values:
            candidate[field_name] = list(values)


def _credit(role: str, name: str, credit: SongCredit, field_name: str | None, raw: str) -> dict:
    quote = " ".join(raw.split())
    if len(quote) > 300:
        quote = quote[:297] + "..."
    return {"role": role, "name": name, "url": credit.page_url,
            "field": f"Template:Song|{field_name}" if field_name else None,
            "quote": schema.redact_text(quote, strict=False) or None}


def mapping(plan: Plan) -> tuple[str, list]:
    """``mapping_status`` and evidence for an asset: only songs that are in the song index count."""
    if not plan.song_ids:
        return "unmapped", []
    status, evidence = plan.decision.mapping, plan.decision.mapping_evidence
    if status == "unmapped" or not evidence:
        return "unmapped", []
    cleaned = []
    for item in evidence:
        entry = {key: value for key, value in item.items() if value is not None}
        if entry and entry not in cleaned:
            cleaned.append(entry)
    return status, cleaned
