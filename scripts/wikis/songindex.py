"""Proposed song index (``data/discovery/song-index.wikis.json``) from the wikis' song templates and Japanese lists.

Songs are grouped by English wiki page title (Miraheze and Fandom share titles); the exact title is the original
``Template:Song`` ``Title`` (Miraheze first), and other spellings become aliases. ``in_original_game`` needs positive
evidence: an original ``Template:Song`` on a page that is not ``Category:Unreleased``, or a row of the Japanese
original-game wiki's lists (atwiki ``dynamix2ch``). The wikiwiki rhythm-game summary is only a corroborating lead: its
rows add evidence and disagreements but never inclusion. ``Template:SongOther`` pages (unreleased/cancelled songs) and
pages with only ``Template:SongUV`` (Universe songs) are kept with ``in_original_game`` false. Composer and illustrator credits
are kept separately and verbatim (``N/A`` and similar mean unknown). Japanese rows are matched to English songs by a
loose title key (NFKC, casefold, alphanumerics); unmatched rows become their own entries and disagreements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from lib import manifest, paths, schema

from . import classify, config, jawikis, wikitext
from .discover import Inventory, SongCredit

UNRELEASED_CATEGORY = "Category:Unreleased"
SUBMISSION_TRACK = re.compile(r"^Category:(.+?) Submission Track$")
COLLAB_SONGS = re.compile(r"^Category:(.+?) Songs$")
NOT_COLLAB = {"Submission", "Event", "Free", "Premium", "Collaboration", "Wave Test", "Special", "Vocaloid",
              "Paid"}
REMOVED_LIST_LABEL = "削除曲"
# A trailing edit/version label ("(DNX edit)", "-Dynamix Edition-", "(Dynamix Ver)"): the lists abbreviate it
# differently, so a Japanese row without an exact match is matched to a unique English song ignoring it.
EDIT_SUFFIX = re.compile(r"\s*(?:[(（\[][^)）\]]*\b(?:dnx|dynamix|edit|ver|version|edition)\b[^)）\]]*[)）\]]|"
                         r"[-–~～]\s*[^-–~～]*\b(?:dnx|dynamix|edit|ver|version|edition)\b[^-–~～]*[-–~～])\s*$",
                         re.IGNORECASE)


def base_key(title: str) -> str:
    """Loose title key without a trailing edit/version label ("" when nothing else remains)."""
    return wikitext.normalize_key(EDIT_SUFFIX.sub("", title))
ENGLISH_KEYS = ("miraheze", "fandom")


@dataclass
class SongEntry:
    key: str
    title: str
    aliases: list[str] = field(default_factory=list)
    composers: list[str] = field(default_factory=list)
    illustrators: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    in_original_game: bool = False
    inclusion_evidence: list[str] = field(default_factory=list)
    universe_also: bool = False
    removed: bool = False
    evidence: list[dict] = field(default_factory=list)
    credits: list[SongCredit] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)  # every exact spelling that maps here (title first)
    id: str = ""


@dataclass
class SongIndex:
    entries: dict[str, SongEntry]
    disagreements: list[dict]
    by_title: dict[str, SongEntry] = field(default_factory=dict)

    def lookup(self, title: str | None) -> SongEntry | None:
        return self.by_title.get(title) if title else None

    def to_json(self, *, as_of: str, generated_from: list[str],
                artwork: Mapping[str, list[str]] | None = None) -> dict:
        songs = []
        for entry in self.entries.values():
            songs.append({
                "id": entry.id, "title": entry.title, "aliases": entry.aliases, "composers": entry.composers,
                "illustrators": entry.illustrators, "collections": entry.collections,
                "in_original_game": entry.in_original_game, "inclusion_evidence": entry.inclusion_evidence,
                "universe_also": entry.universe_also, "removed": entry.removed, "evidence": entry.evidence,
                "artwork_asset_ids": sorted((artwork or {}).get(entry.id, [])),
            })
        index = {"schema_version": schema.SCHEMA_VERSION, "generated_from": generated_from, "as_of": as_of,
                 "songs": songs, "disagreements": self.disagreements}
        return manifest.sort_song_index(index)


def _add(values: list[str], items: Iterable[str]) -> None:
    for item in items:
        if item and item not in values:
            values.append(item)


def category_collections(categories: Iterable[str]) -> list[str]:
    """Pack/event/collaboration names from original-game song categories (Universe categories ignored)."""
    result: list[str] = []
    for category in categories:
        if classify._universe_category(category):
            continue
        if category in config.COLLECTION_CATEGORIES:
            _add(result, [config.COLLECTION_CATEGORIES[category]])
        elif category == "Category:Submission Songs":
            _add(result, ["Open Submission"])
        elif SUBMISSION_TRACK.match(category):
            _add(result, [f"Open Submission ({SUBMISSION_TRACK.match(category).group(1)})"])
        elif COLLAB_SONGS.match(category) and COLLAB_SONGS.match(category).group(1) not in NOT_COLLAB:
            _add(result, [f"Collaboration: {COLLAB_SONGS.match(category).group(1)}"])
    return result


def package_members(inventory: Inventory, pages: Mapping[str, classify.PageInfo]) -> dict[str, list[str]]:
    """Song page title -> original-game package names, from ``[[File:...|link=Song]]`` entries on package pages."""
    members: dict[str, list[str]] = {}
    for title, info in pages.items():
        if not info.package or info.game != "dynamix":
            continue
        name = re.sub(r"/Dynamix$", "", title)
        content = (inventory.pages[title].get("revision") or {}).get("content") or ""
        for ref in wikitext.file_refs(content):
            if ref.link:
                target = wikitext.canonical_page_title(ref.link)
                if target and target != title:
                    _add(members.setdefault(target, []), [name])
    return members


def _credit(source: config.WikiSource, record: classify.SongRecord, collections: list[str]) -> SongCredit:
    return SongCredit(title=record.title, song_id="", page_url=record.url, source_id=source.source_id,
                      illustrators=list(record.illustrators), composers=list(record.composers),
                      illustrator_field=record.illustrator_field, composer_field=record.composer_field,
                      illustrator_raw=record.illustrator_raw, composer_raw=record.composer_raw,
                      imgsuffix=record.imgsuffix, collections=list(collections))


def _joined(values: Iterable[str]) -> str:
    values = list(values)
    return "; ".join(values) if values else "(none)"


def build(inventories: Mapping[str, Inventory], ja_lists: Mapping[str, list[jawikis.ListPage]]) -> SongIndex:
    entries: dict[str, SongEntry] = {}
    disagreements: list[tuple[str, str, dict]] = []  # (entry key, field, values)
    per_wiki: dict[str, dict[str, dict]] = {}  # entry key -> source key -> {"song": record, "uv": record, ...}
    for key in ENGLISH_KEYS:
        inventory = inventories.get(key)
        if inventory is None:
            continue
        source = inventory.source
        pages = {title: classify.analyze_page(source, page) for title, page in inventory.pages.items()}
        packages = package_members(inventory, pages)
        for title, info in sorted(pages.items()):
            if not info.songs:
                continue
            originals = [song for song in info.songs if song.template == "Song"]
            universe = [song for song in info.songs if song.template == "SongUV"]
            others = [song for song in info.songs if song.template == "SongOther"]  # unreleased/cancelled
            unreleased = UNRELEASED_CATEGORY in info.categories
            entry = entries.get(title)
            if entry is None:
                main = (originals or others or universe)[0].title
                entry = entries[title] = SongEntry(key=title, title=main)
            collections = category_collections(info.categories) + [
                name for name in packages.get(title, []) if name not in category_collections(info.categories)]
            per_wiki.setdefault(title, {})[key] = {"original": originals, "universe": universe, "other": others,
                                                  "unreleased": unreleased, "url": info.url,
                                                  "collections": collections}
            _add(entry.titles, [song.title for song in originals + others + universe] + [title])
            entry.evidence.append({"source_id": source.source_id, "url": info.url, "revision": info.revision})
            for song in originals + others:
                entry.credits.append(_credit(source, song, collections))
            if originals and not unreleased:
                labels = ["Template:Song"] + [category for category in info.categories
                                              if category in config.COLLECTION_CATEGORIES or category == "Category:Dynamix"]
                _add(entry.inclusion_evidence, [f"{info.url} ({', '.join(labels)})"])
                entry.in_original_game = True
            if universe or any(classify._universe_category(category) for category in info.categories):
                entry.universe_also = True
    for entry in entries.values():
        _reconcile_english(entry, per_wiki.get(entry.key, {}), disagreements)
    _match_japanese(entries, ja_lists, disagreements)
    return _finish(entries, disagreements)


def _reconcile_english(entry: SongEntry, wikis: Mapping[str, dict], disagreements: list) -> None:
    """Title/credits/collections from Miraheze, else Fandom; differences become disagreements."""
    first = next((wikis[key]["original"] for key in ENGLISH_KEYS if key in wikis and wikis[key]["original"]), None)
    first_wiki = next((wikis[key] for key in ENGLISH_KEYS if key in wikis and wikis[key]["original"]), None)
    if first is None:  # Template:SongOther only (unreleased): credits still describe the song
        first = next((wikis[key]["other"] for key in ENGLISH_KEYS if key in wikis and wikis[key]["other"]), None)
        first_wiki = next((wikis[key] for key in ENGLISH_KEYS if key in wikis and wikis[key]["other"]), None)
    if first is not None:
        record = first[0]
        entry.title = record.title
        entry.composers = list(record.composers)
        entry.illustrators = list(record.illustrators)
        entry.collections = list(first_wiki["collections"])
    if entry.title not in entry.titles:
        entry.titles.insert(0, entry.title)
    else:
        entry.titles.remove(entry.title)
        entry.titles.insert(0, entry.title)
    entry.aliases = [title for title in entry.titles if title != entry.title]
    if all(key in wikis and wikis[key]["original"] for key in ENGLISH_KEYS):
        m, f = (wikis[key]["original"][0] for key in ENGLISH_KEYS)
        for name, mv, fv in (("title", [m.title], [f.title]), ("composers", m.composers, f.composers),
                             ("illustrators", m.illustrators, f.illustrators),
                             ("imgsuffix", [m.imgsuffix or ""], [f.imgsuffix or ""])):
            if mv != fv:
                disagreements.append((entry.key, name, {"wikis:miraheze": _joined(mv), "wikis:fandom": _joined(fv)}))
    if any(wiki["original"] and wiki["unreleased"] for wiki in wikis.values()):
        entry.inclusion_evidence = []
        entry.in_original_game = False


def _match_japanese(entries: dict[str, SongEntry], ja_lists: Mapping[str, list[jawikis.ListPage]],
                    disagreements: list) -> None:
    """Match Japanese rows to songs. Only the atwiki original-game lists are positive inclusion evidence; the
    wikiwiki rhythm-game summary is a corroborating lead (its rows add evidence and disagreements, never
    ``in_original_game``)."""
    keys: dict[str, SongEntry] = {}
    bases: dict[str, list[SongEntry]] = {}
    for entry in entries.values():
        for title in entry.titles:
            keys.setdefault(wikitext.normalize_key(title), entry)
            if base_key(title) and entry not in bases.setdefault(base_key(title), []):
                bases[base_key(title)].append(entry)
    for source_key in ("atwiki-ja", "rhythmgame-ja"):
        source = config.SOURCES[source_key]
        grants = source_key == "atwiki-ja"
        for page in ja_lists.get(source_key, []):
            for row in page.rows:
                loose = wikitext.normalize_key(row.title)
                if not loose:
                    continue
                entry = keys.get(loose)
                label = f"{page.url} ({source.name}: {page.label})"
                if entry is None and len(bases.get(base_key(row.title), [])) == 1:
                    entry = bases[base_key(row.title)][0]
                    disagreements.append((entry.key, "title", {
                        "wikis:miraheze": entry.title,
                        source.source_id: f"{row.title} (matched ignoring the edit/version label)"}))
                created = entry is None
                if created:
                    entry = entries[f"ja:{loose}"] = SongEntry(key=f"ja:{loose}", title=row.title)
                    entry.titles.append(row.title)
                    entry.composers = wikitext.credit_names(row.credit)
                    keys[loose] = entry
                    disagreements.append((entry.key, "presence", {
                        source.source_id: f"listed in {page.label}",
                        "wikis:miraheze": "no song page with Template:Song/SongUV found",
                        "wikis:fandom": "no song page with Template:Song/SongUV found"}))
                if not any(item["source_id"] == source.source_id and item["url"] == page.url
                           for item in entry.evidence):
                    entry.evidence.append({"source_id": source.source_id, "url": page.url, "revision": None})
                if row.title not in entry.titles:
                    entry.titles.append(row.title)
                    if row.title != entry.title:
                        _add(entry.aliases, [row.title])
                if page.label == REMOVED_LIST_LABEL:
                    entry.removed = True
                    if not created:
                        disagreements.append((entry.key, "release_status", {
                            source.source_id: f"listed in {page.label} (removed or never implemented)",
                            "wikis:miraheze": "Category:Unreleased / Template:SongOther"
                            if not entry.in_original_game else "original-game song page"}))
                    continue
                if not entry.in_original_game and not created and entry.universe_also:
                    disagreements.append((entry.key, "in_original_game", {
                        source.source_id: f"listed in {page.label}" + ("" if grants else " (lead only)"),
                        "wikis:miraheze": "Template:SongUV only (Dynamix Universe)"}))
                if grants:
                    entry.in_original_game = True
                    _add(entry.inclusion_evidence, [label])
                if not created and grants:
                    names = wikitext.credit_names(row.credit)
                    if names and entry.composers and {wikitext.normalize_key(n) for n in names} != {
                            wikitext.normalize_key(n) for n in entry.composers}:
                        disagreements.append((entry.key, "composers", {
                            "wikis:miraheze": _joined(entry.composers), source.source_id: _joined(names)}))
                    if row.title != entry.title and wikitext.normalize_key(row.title) == wikitext.normalize_key(
                            entry.title):
                        disagreements.append((entry.key, "title", {"wikis:miraheze": entry.title,
                                                                   source.source_id: row.title}))


def _finish(entries: dict[str, SongEntry], disagreements: list) -> SongIndex:
    ordered = sorted(entries.values(), key=lambda entry: (entry.title, entry.key))
    ids = paths.assign_song_ids([entry.title for entry in ordered])
    by_title: dict[str, SongEntry] = {}
    for entry, song_id in zip(ordered, ids):
        entry.id = song_id
        for credit in entry.credits:
            credit.song_id = song_id
        for title in entry.titles:
            by_title.setdefault(title, entry)
    by_key = {entry.key: entry for entry in ordered}
    seen = set()
    result = []
    for key, name, values in disagreements:
        item = {"song_id": by_key[key].id, "field": name, "values": values}
        marker = repr(item)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return SongIndex({entry.id: entry for entry in ordered}, result, by_title)


def artwork_ids(assets: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Song ID -> current song-art asset IDs."""
    result: dict[str, list[str]] = {}
    for asset in assets:
        if asset.get("kind") != "song_art" or asset.get("upstream_status") in ("removed", "superseded"):
            continue
        for song_id in asset.get("song_ids", []):
            _add(result.setdefault(song_id, []), [asset["id"]])
    return result
