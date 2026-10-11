"""Role and game classification of wiki files from page usage, templates, categories and captions.

Evidence is collected per file *title* on each wiki and pooled across Miraheze and Fandom (Miraheze imported Fandom's
files under the same titles, so a page on either wiki that embeds a title is evidence about that title). Every
decision keeps human-readable evidence strings naming the page, context and quoted caption/field.

Strengths: 3 = the song template's ``Imgsuffix`` field, or the "Package <name>" image of an original-game package
page; 2 = a gallery/link caption, a reviewed list page or section, or a file category; 1 = an image linked to a song
page from an original-game list, an image in a song page's Trivia/Notes section, or the documented size heuristic on
interface pages; 0 = a filename convention, used only to fill a role that page usage leaves open (a file whose only
evidence is its name stays pending with role null). The most specific strength decides; ties between roles named
by equally many pages stay pending. Game evidence never comes from a filename.

Exclusions: video placeholders (``video/*``), animated GIF captures (frame count > 1 or unknown, reversible with
``--include-animated``), Universe-only use, and the exclusion roles (profile photos, wiki/game interface images,
covers of external releases). Everything else without both a role and an original-game (or shared) context stays
``pending`` with a concrete reason.

Game: the union of the games of the contexts that embed the file (song template, game tab, page category/suffix, or
the reviewed original-game page table). A page about both games gives no game by itself. A file whose role comes from
a song page and whose Fandom upload predates the wiki's first Universe-named upload is original-game material
(``pre-Universe upload``).

File description pages: a description that redirects to another file with the same reported SHA-1 is an alias
(``redirect_or_alias``); pages linking to the description page (``[[:File:X]]`` citations, talk threads) are recorded
as context without role or game. Archivist reviews (:mod:`wikis.reviews`, visual inspection of the bytes) fill a role
or game only where page evidence is absent (or break a tie between equally supported page roles), and may replace an
exclusion reason given by page evidence with the exclusion seen in the bytes; a mapping that rests on a review is
``inferred`` at most. Song art named only by the filename convention is mapped (``inferred``) to the one
original-game song whose page embeds it. A caption "Artwork in <BMS event>" (PABAT, BOF) names the artwork of the
song's release in that event: ``external_release_art``.

Songs: the strongest page evidence naming a song for the decided role, and among it the songs named by the most
distinct pages (see :func:`_song_mapping`); a cover-art caption that quotes another original-game song's title names
that song, not the host page's; a file whose role evidence names no song is mapped (``inferred``) to the one
original-game song whose page gallery shows it (:func:`_gallery_song`).

List and chart-announcement graphics (coordinator decision 2026-10-10 as amended, :data:`config.LIST_GRAPHIC_RULE`): an
announcement graphic that displays chart difficulty levels (for one song or several) or whose primary content is a
list of songs or charts is ``reference`` even where a category or caption files it as promotional. Its evidence is a
chart-list/comeback/new-chart/lineup caption, a chart-list file category or an archivist review that saw such a
graphic in the bytes (``list_graphic``); a review that saw neither a list nor level marks (role ``promotional``) keeps
the graphic promotional. The rule replaces only a ``promotional`` role (alone or tied with ``reference``) and is
recorded, with the page evidence it overrides, in the role evidence and the asset notes (:func:`_list_rule`). The
subject of the list captions (or of the file title) is read against the original-game song titles
(:func:`list_subject`) and every song it names is mapped (:func:`_subject_songs`), one song or several; a subject the
titles do not resolve maps nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

from lib import paths, schema

from . import config, wikitext

KINDS = set(schema.KINDS)
EXCLUSION_ROLES = {"person_photo", "site_ui", "game_ui", "external_release_art", "video_placeholder",
                   "out_of_scope_other"}
DOWNLOAD_KINDS = {"song_art", "pack_cover", "character", "background", "part", "promotional", "process",
                  "reference", "gameplay_screenshot"}
MAX_EVIDENCE = 8
# Song templates and the game each establishes: Template:SongOther holds unreleased/cancelled songs (no game).
SONG_TEMPLATES = {"Song": "dynamix", "SongUV": "dynamix_universe", "SongOther": None}
UNIVERSE_NAME = re.compile(r"universe|dynamix\s*uv|(?:^|[\s_(-])UV(?:[\s_).-]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class Evidence:
    source_id: str
    page: str
    url: str
    context: str  # "Template:Song|Imgsuffix", "gallery", "link", "embedded", "file category", "filename", ...
    role: str | None
    strength: int
    game: str | None
    song: str | None = None  # exact song title the context associates with the file
    song_page: str | None = None
    detail: str = ""
    other_game: str | None = None  # "RAVON" for a caption "<song> Artwork in RAVON" (shared artwork)
    list_graphic: bool = False  # the context files it as a list or chart announcement (config.LIST_GRAPHIC_RULE)
    caption: str = ""  # the cleaned gallery/link caption, verbatim (subject of a list graphic, display title)

    def text(self) -> str:
        bits = [self.context]
        if self.detail:
            bits.append(self.detail)
        return f"{self.url} ({'; '.join(bits)})"


@dataclass
class SongRecord:
    """One song template on one wiki page (original ``Song`` or Universe ``SongUV``)."""

    source_id: str
    page: str
    url: str
    revision: int | None
    template: str
    title: str
    imgsuffix: str | None
    imgsuffix_raw: str
    illustrators: list[str]
    composers: list[str]
    illustrator_raw: str
    composer_raw: str
    composer_field: str | None
    illustrator_field: str | None
    categories: list[str]
    number: str = ""
    update: str = ""


@dataclass
class PageInfo:
    source_id: str
    title: str
    url: str
    revision: int | None
    categories: list[str]
    game: str | None
    game_reason: str
    songs: list[SongRecord] = field(default_factory=list)
    package: bool = False
    transcludes: bool = False  # the page text transcludes other articles ({{:Page}}), so fileusage is indirect


@dataclass
class Decision:
    role: str | None
    game: str
    game_evidence: list[str]
    role_evidence: list[str]
    songs: list[str]  # exact song titles (song_art via Imgsuffix/captions/links)
    song_pages: list[str]
    disposition: str  # "download", "excluded" or "pending"
    reason: str
    mapping: str  # "confirmed_wiki", "inferred" or "unmapped"
    mapping_evidence: list[Any] = field(default_factory=list)
    strength: int = -1  # strength of the evidence behind ``role`` (3 template ... 0 filename, -1 none)
    associations: list[str] = field(default_factory=list)  # other games/events using the file (game_associations)
    rule_notes: list[str] = field(default_factory=list)  # asset notes of classification rules that decided the kind


# --- Pages ------------------------------------------------------------------------------------------------------------

def _category_titles(page: Mapping[str, Any]) -> list[str]:
    values = []
    for category in page.get("categories") or []:
        title = category.get("title") if isinstance(category, dict) else category
        if isinstance(title, str) and title not in values:
            values.append(title)
    return values


def _universe_category(title: str) -> bool:
    """``Category:Dynamix Universe``, ``Category:Songs (Dynamix Universe)``, Fandom's ``Category:Dynamix UV`` and
    ``Category:DynamixUV``."""
    return bool(re.search(r"dynamix\s*(?:universe|uv)\b", title, re.IGNORECASE))


def page_game(title: str, categories: Iterable[str], templates: Iterable[str]) -> tuple[str | None, str]:
    """Game of a whole page and why: song templates, ``/Dynamix`` subpage suffix, game categories, or the reviewed
    original-game page table. ``None`` when the page is about both games or neither."""
    templates = set(templates)
    if "Song" in templates and "SongUV" in templates:
        return "mixed", "Template:Song and Template:SongUV tabs"
    if "Song" in templates:
        return "dynamix", "Template:Song"
    if "SongUV" in templates:
        return "dynamix_universe", "Template:SongUV"
    if re.search(r"/Dynamix$", title):
        return "dynamix", "original-game subpage (/Dynamix)"
    if re.search(r"/Dynamix (?:Universe|UV)$", title):
        return "dynamix_universe", "Universe subpage"
    categories = list(categories)
    universe = [category for category in categories if _universe_category(category)]
    original = "Category:Dynamix" in categories
    if universe and original:
        return None, "page categorized for both games"
    if universe:
        return "dynamix_universe", universe[0]
    if original:
        return "dynamix", "Category:Dynamix"
    if title in config.ORIGINAL_GAME_PAGES or title.startswith(config.ORIGINAL_GAME_PREFIXES):
        return "dynamix", "reviewed original-game page"
    return None, "no game evidence on the page"


def song_records(source_id: str, page: Mapping[str, Any], url: str) -> list[SongRecord]:
    content = (page.get("revision") or {}).get("content") or ""
    categories = _category_titles(page)
    records = []
    for template in wikitext.find_templates(content, set(SONG_TEMPLATES)):
        params = template.params
        raw_title = params.get("Title", "")
        raw_title = (page.get("title_expansions") or {}).get(raw_title.strip(), raw_title)  # {{MassacrE}} etc.
        title_param = wikitext.clean_markup(raw_title).replace("\n", " ").strip()
        title = title_param or page["title"]
        if template.name in ("Song", "SongOther"):
            composer_field = "Compound Artist Link" if params.get("Compound Artist Link") else (
                "Artist" if "Artist" in params else None)
            illustrator_field = "Cover Artist" if "Cover Artist" in params else (
                "Cover Art" if "Cover Art" in params else None)
        else:
            composer_field = "Compound Artist Link" if params.get("Compound Artist Link") else (
                "Artist" if "Artist" in params else None)
            illustrator_field = "Illustrator" if "Illustrator" in params else None
        composer_raw = params.get(composer_field, "") if composer_field else ""
        illustrator_raw = params.get(illustrator_field, "") if illustrator_field else ""
        raw_image = params.get("Imgsuffix", "")
        records.append(SongRecord(
            source_id=source_id, page=page["title"], url=url, revision=(page.get("revision") or {}).get("revid"),
            template=template.name, title=title, imgsuffix=wikitext.canonical_file_name(raw_image),
            imgsuffix_raw=raw_image, illustrators=wikitext.credit_names(illustrator_raw),
            composers=wikitext.credit_names(composer_raw, strip_illustrator_notes=True), illustrator_raw=illustrator_raw.strip(),
            composer_raw=composer_raw.strip(), composer_field=composer_field, illustrator_field=illustrator_field,
            categories=categories, number=wikitext.clean_markup(params.get("No.", "")),
            update=wikitext.clean_markup(params.get("Update", ""))))
    return records


def analyze_page(source: config.WikiSource, page: Mapping[str, Any]) -> PageInfo:
    url = page.get("fullurl") or config.page_url(source, page["title"])
    songs = song_records(source.source_id, page, url)
    game, reason = page_game(page["title"], _category_titles(page), [song.template for song in songs])
    categories = _category_titles(page)
    content = (page.get("revision") or {}).get("content") or ""
    return PageInfo(source.source_id, page["title"], url, (page.get("revision") or {}).get("revid"), categories,
                    game, reason, songs, package="Category:Song Package" in categories,
                    transcludes=bool(re.search(r"\{\{\s*:", wikitext.mask_comments(content))))


def _section_role(title: str, sections: list[str]) -> str | None:
    for page, pattern, role in config.SECTION_ROLES:
        if page == title and sections and pattern.search(sections[-1]):
            return role
    for page, pattern, role in config.SECTION_ROLES:  # outer headings ("Background" > "2021")
        if page == title and role in KINDS and any(pattern.search(section) for section in sections[:-1]):
            return role
    return None


def _caption_role(caption: str) -> str | None:
    """Role named by a caption; the first matching pattern decides (a ``None`` role stops: undecidable caption)."""
    for pattern, role in config.CAPTION_ROLES:
        if pattern.search(caption):
            return role
    return None


def page_evidence(source: config.WikiSource, page: Mapping[str, Any], info: PageInfo,
                  files: Mapping[str, Mapping[str, Any]], song_pages: Mapping[str, list[SongRecord]],
                  song_titles: Mapping[str, tuple[str, str]] | None = None) -> list[tuple[str, Evidence]]:
    """``(file title, Evidence)`` for every file reference on an article page. ``song_titles`` (see
    :func:`original_song_titles`) lets a cover-art caption that quotes another original-game song's title name that
    song instead of the host page's."""
    content = (page.get("revision") or {}).get("content") or ""
    found_regions = wikitext.regions(content)
    found_headings = wikitext.headings(content)
    result: list[tuple[str, Evidence]] = []
    imgsuffix = {(song.template, song.imgsuffix): song for song in info.songs if song.imgsuffix}
    for ref in wikitext.file_refs(content):
        file_title = "File:" + ref.name
        if file_title not in files:
            continue  # broken file link or a title another wiki has; usage lists cover real embeddings
        inside = wikitext.regions_at(found_regions, ref.offset)
        region = inside[-1] if inside else None
        region_game = wikitext.region_game(region.label) if region else None
        game = region_game or (info.game if info.game in ("dynamix", "dynamix_universe") else None)
        sections = wikitext.section_path(found_headings, ref.offset, since=region.start if region else 0)
        where = []
        if region is not None:
            where.append(f"tab {region.label!r}")
        if sections:
            where.append("section " + " > ".join(sections))
        if ref.caption:
            where.append(f"caption {ref.caption[:120]!r}")
        if ref.link:
            where.append(f"link={ref.link[:120]}")
        detail = ", ".join(where)
        role, strength, song, song_page, context, other_game = None, 0, None, None, ref.context, None
        listed = False
        if ref.context == "template" and ref.template in SONG_TEMPLATES and ref.param == "Imgsuffix":
            record = imgsuffix.get((ref.template, ref.name))
            role, strength, game = "song_art", 3, SONG_TEMPLATES[ref.template]
            song, song_page = (record.title if record else info.title), info.title
            context = f"Template:{ref.template}|Imgsuffix"
            detail = f"Imgsuffix = {ref.raw[:120]}"
        elif ref.context == "template":
            context = f"Template:{ref.template}|{ref.param}"
            if config.SITE_UI_TEMPLATE_RE.match(f"Template:{ref.template}"):
                role, strength = "site_ui", 1
            elif _page_role(info.title):
                role, strength = _page_role(info.title), 2
        elif (info.package and info.game == "dynamix" and region_game in (None, "dynamix") and ref.caption
              and config.PACKAGE_CAPTION.search(ref.caption)):
            # An original-game package page's own "Package <name>" image: its cover, as specific as an Imgsuffix.
            role, strength, context = "pack_cover", 3, ref.context + PACK_CONTEXT
        else:
            caption_role = _caption_role(ref.caption) if ref.caption else None
            if caption_role is not None:
                role, strength = caption_role, 2
                listed = caption_role == "reference" and bool(config.LIST_CAPTION.search(ref.caption))
                if info.songs:
                    song_page = info.title
                    song = _region_song(info, region_game) if caption_role != "external_release_art" else None
                quoted = _named_song(ref.caption, song_titles) if caption_role == "song_art" else None
                if quoted is not None and quoted[0] not in {record.title for record in info.songs}:
                    # 'Cover Art of "Gallium Phosphide"' in the gallery of another song's page: that song's cover.
                    song, song_page = quoted
                    detail += f", the caption names the cover of another song ({quoted[0]})"
                other = config.OTHER_GAME_ARTWORK.search(ref.caption) if caption_role in KINDS else None
                named = other.group("other").strip().strip("'\"") if other is not None else ""
                if other is not None and config.EXTERNAL_RELEASE_EVENT.search(named):
                    # "Artwork in PABAT! 2017": the song's artwork for a separate (BMS) event release, not Dynamix's.
                    role, song = "external_release_art", None
                    detail += f", artwork of the song's release for {named} (an external release event)"
                elif other is not None:
                    game = "mixed"
                    other_game = named
                    detail += f", artwork of the song for {other_game} (another game/event)"
                elif (game is None and info.game == "mixed" and region is None and ref.context == "gallery"
                      and caption_role in config.MIXED_PAGE_GALLERY_ROLES
                      and not re.search(r"universe|\bUV\b", ref.caption, re.I)):
                    game = "dynamix"
                    detail += ", page-level gallery of a two-game song page (original-game event items)"
            elif info.title in config.SITE_UI_PAGES:
                role, strength = "site_ui", 2
            else:
                role, strength = _list_page_role(info.title, sections, files[file_title])
                if role is not None and (info.title, role) in config.SECTION_NOTES and _section_role(
                        info.title, sections) == role:
                    detail = (detail + ", " if detail else "") + config.SECTION_NOTES[(info.title, role)]
                if role is None and info.songs and sections and config.TRIVIA_SECTIONS.match(sections[-1]):
                    role, strength = "gameplay_screenshot", 1
                    song_page, song = info.title, _region_song(info, region_game)
                    detail = (detail + ", " if detail else "") + "image in a song page's trivia/notes section"
            if role is None and ref.link:
                target = wikitext.canonical_page_title(ref.link)
                records = [record for record in song_pages.get(target, []) if record.template == "Song"]
                if records and (game == "dynamix" or info.game == "dynamix"):
                    role, strength, song, song_page = "song_art", 1, records[0].title, target
                    detail += ", image linked to an original-game song page"
            if role in ("person_photo",) and re.search(r"album|cover\s*art|coverart|-cover", ref.name, re.I):
                role = "external_release_art"
            if role is None and song is None and ref.context == "gallery" and region_game != "dynamix_universe":
                # A gallery image of an original-game song page whose caption gives no role: the page still places
                # the file with its song (used by decide() only when no role evidence names a song; inferred).
                originals = [record for record in info.songs if record.template == "Song"]
                if originals:
                    song, song_page = originals[0].title, info.title
        result.append((file_title, Evidence(source.source_id, info.title, info.url, context, role, strength, game,
                                            song, song_page, detail, other_game, listed and role == "reference",
                                            caption=ref.caption)))
    return result


def _region_song(info: PageInfo, region_game: str | None) -> str | None:
    """The song a page-level gallery refers to: the original-game song title when the page has one."""
    originals = [song for song in info.songs if song.template == "Song"]
    if region_game == "dynamix_universe":
        universe = [song for song in info.songs if song.template == "SongUV"]
        return universe[0].title if universe else None
    if originals:
        return originals[0].title
    return info.songs[0].title if info.songs else None


_QUOTED = re.compile(r'"([^"\n]+)"|“([^”\n]+)”|「([^」\n]+)」|『([^』\n]+)』')


def original_song_titles(pages: Iterable[PageInfo]) -> dict[str, tuple[str, str]]:
    """Loose key (:func:`wikitext.normalize_key`) -> ``(exact title, page title)`` of every original-game song
    (``Template:Song``); a key shared by different songs is left out (ambiguous)."""
    found: dict[str, set[tuple[str, str]]] = {}
    for info in pages:
        for record in info.songs:
            if record.template == "Song" and wikitext.normalize_key(record.title):
                found.setdefault(wikitext.normalize_key(record.title), set()).add((record.title, info.title))
    result = {}
    for key, values in found.items():
        titles = {title for title, _page in values}
        if len(titles) == 1:
            result[key] = sorted(values)[0]
    return result


def _named_song(caption: str, song_titles: Mapping[str, tuple[str, str]] | None) -> tuple[str, str] | None:
    """``(title, page)`` of the one original-game song whose title a caption quotes, else None."""
    if not caption or not song_titles:
        return None
    named = {song_titles[key] for key in (wikitext.normalize_key(next(group for group in match.groups() if group))
                                          for match in _QUOTED.finditer(caption)) if key in song_titles}
    return named.pop() if len(named) == 1 else None


# --- List subjects (the songs a list or chart-announcement graphic names) ---------------------------------------------

# A trailing note of a spelling: "(Dynamix Edit)", "（feat. ...）", "[...]", " ~After Legend~", " -fracture-".
_TRAILING_NOTE = re.compile(r"\s*[(（\[][^()（）\[\]]*[)）\]]\s*$|\s+[~～-][^~～-]*[~～-]\s*$")
_SUBJECT_SPLIT = re.compile("(" + config.SUBJECT_SEPARATOR.pattern + ")")


@dataclass(frozen=True)
class SongNames:
    """Original-game song titles for reading list subjects. ``exact`` maps the loose key
    (:func:`wikitext.normalize_key`) of every known spelling of a song (title, aliases, page title) to its exact title;
    ``loose`` maps the key of a spelling without its trailing note ("Time & Space" for "Time & Space (Dynamix Edit)",
    "Dement" for "Dement ~After Legend~"). Keys shared by different songs are left out (ambiguous)."""

    exact: Mapping[str, str]
    loose: Mapping[str, str]

    @classmethod
    def build(cls, songs: Iterable[tuple[str, Iterable[str]]]) -> "SongNames":
        """From ``(exact title, other spellings)`` pairs."""
        exact: dict[str, set[str]] = {}
        loose: dict[str, set[str]] = {}
        for title, spellings in songs:
            for name in dict.fromkeys([title, *spellings]):
                key = wikitext.normalize_key(name)
                if key:
                    exact.setdefault(key, set()).add(title)
                base = wikitext.normalize_key(_TRAILING_NOTE.sub("", name))
                if base and base != key:
                    loose.setdefault(base, set()).add(title)
        return cls({key: min(titles) for key, titles in exact.items() if len(titles) == 1},
                   {key: min(titles) for key, titles in loose.items() if len(titles) == 1 and key not in exact})

    def resolve(self, text: str) -> tuple[str, bool] | None:
        """``(exact title, whole-spelling match)`` of the one song ``text`` names, else None; without a whole-spelling
        match a trailing note is ignored on either side ("紅 (Kurenai)" -> 紅, "NextTime" -> Next Time (Feat.初音ミク))."""
        key = wikitext.normalize_key(text)
        if key in self.exact:
            return self.exact[key], True
        base = wikitext.normalize_key(_TRAILING_NOTE.sub("", text))
        for candidate in (base, key):
            if candidate in self.exact:
                return self.exact[candidate], False
            if candidate in self.loose:
                return self.loose[candidate], False
        return None


@dataclass(frozen=True)
class SubjectReading:
    text: str
    songs: tuple[tuple[str, bool], ...]  # (exact title, whole-spelling match), in reading order
    complete: bool  # every name resolved
    names: int  # names in the reading (the separated parts when it does not resolve)


def subject_text(text: str) -> str:
    """Subject of a list caption or file title: the text before its first list words (config.SUBJECT_END), '' when it
    has none ("Peony X Xetta Comeback Chart List" -> "Peony X Xetta")."""
    match = config.SUBJECT_END.search(text or "")
    return text[:match.start()].strip(" \t\n-–—:;,(") if match else ""


def read_subject(text: str, names: SongNames) -> SubjectReading:
    """The songs a subject names: the whole text as one title, else its split at config.SUBJECT_SEPARATOR into the
    fewest known titles ("Angel x Angel X The Path" -> Angel x Angel, The Path); unresolved when no split works."""
    whole = names.resolve(text)
    if whole is not None:
        return SubjectReading(text, (whole,), True, 1)
    chunks = _SUBJECT_SPLIT.split(text)
    count = len(chunks) // 2 + 1  # names separated by separators: chunks alternate name, separator, name, ...
    best: list[list[tuple[str, bool]] | None] = [[]] + [None] * count
    for end in range(1, count + 1):
        for start in range(end):
            found = best[start]
            piece = "".join(chunks[2 * start:2 * end - 1]).strip()
            match = names.resolve(piece) if found is not None and piece else None
            if match is not None and (best[end] is None or len(found) + 1 < len(best[end])):
                best[end] = found + [match]
    if best[count] is not None:
        return SubjectReading(text, tuple(best[count]), True, len(best[count]))
    parts = [chunk.strip() for chunk in chunks[0::2] if chunk.strip()]
    resolved = tuple(match for match in (names.resolve(part) for part in parts) if match is not None)
    return SubjectReading(text, resolved, False, len(parts))


@dataclass(frozen=True)
class Subject:
    """What a list or chart-announcement graphic is about (config.SUBJECT_END): the songs its list captions (or file
    title) name and, when one of them gives a subject, the songs of the original-game song pages whose galleries show
    it."""

    songs: tuple[str, ...]  # exact titles in order: names read from the subjects, then host song pages
    page_named: frozenset[str]  # songs named by page evidence: a caption naming the whole title, or a host song page
    named: tuple[tuple[str, str, str, str, str], ...]  # (song, url, field, quote, source_id) for mapping evidence
    unread: str = ""  # no caption or file title gives a subject although song pages show the graphic (recorded)
    unread_pages: tuple[str, ...] = ()  # then: the songs of the original-game song pages showing it

    def only(self, titles: Iterable[str]) -> "Subject":
        """The subject restricted to ``titles`` (the songs an archivist review saw in the bytes)."""
        keep = set(titles)
        return replace(self, songs=tuple(song for song in self.songs if song in keep),
                       page_named=frozenset(song for song in self.page_named if song in keep),
                       named=tuple(entry for entry in self.named if entry[0] in keep))


def _list_item(item: Evidence) -> bool:
    """Reference evidence that files the graphic as a list or chart announcement: a list caption or chart-list
    category, or a chart-list, comeback or new-chart filename (its subject is read for the song mapping; a filename
    never makes a promotional graphic a reference, see :func:`_list_rule`)."""
    if item.role != "reference" or item.context == REVIEW_CONTEXT:
        return False
    return item.list_graphic or (item.context == "filename" and bool(config.SUBJECT_END.search(item.page)))


def list_subject(file_title: str, items: Iterable[Evidence], names: SongNames | None) -> Subject | None:
    """The subject of a file with list evidence (:func:`_list_item`), or None when it has none. Captions are read
    first; the file title only when no list caption has a subject. Original-game song pages that show the graphic only
    corroborate a subject read from a caption or the file title (a one-name subject the titles do not resolve, such as
    a spelling variant, is their song): a lineup caption without one ("Upcoming Events", "Comeback Tracks", "New
    Charts") names no song however few song pages show it, and the missing subject is recorded."""
    listed = [item for item in items if _list_item(item)]
    if not listed or names is None:
        return None
    sources: list[tuple[Evidence, str, str]] = []  # (evidence item, label, subject)
    for item in listed:
        subject = subject_text(item.caption)
        if subject and all(item.caption != label_item.caption for label_item, _label, _ in sources):
            sources.append((item, f"caption {item.caption[:160]!r}", subject))
    file_title_item = None
    if not sources:
        stem = " ".join(paths.filename_stem(file_title.split(":", 1)[-1]).replace("_", " ").split())
        subject = subject_text(stem)
        if subject:
            file_title_item = listed[0]
            sources.append((file_title_item, f"file title {stem!r}", subject))
    songs: list[str] = []
    page_named: set[str] = set()
    named: list[tuple[str, str, str, str, str]] = []
    for item, label, subject in sources:
        reading = read_subject(subject, names)
        field_name = "file title" if item is file_title_item else item.context
        url = config.page_url(config.SOURCES[_source_key(item.source_id)], file_title) \
            if item is file_title_item else item.url
        for title, whole in reading.songs:
            if title not in songs:
                songs.append(title)
            if whole and item is not file_title_item:
                page_named.add(title)
            named.append((title, url, field_name, f"{label} names the song {title!r}", item.source_id))
    hosts = [item for item in listed if item.song and item.song_page and item.game != "dynamix_universe"]
    unread, unread_pages = "", ()
    if not sources and hosts:
        captions = list(dict.fromkeys(item.caption for item in listed if item.caption))
        unread_pages = tuple(dict.fromkeys(item.song for item in hosts))
        quoted = ", ".join(repr(text[:80]) for text in captions[:2])
        listed_text = f"caption {quoted}" if captions else "the list evidence"
        unread = f"{listed_text} and the file title name no subject before their list words"
        hosts = []  # the host pages are not its subject; see unread_note() for how their songs are mapped
    for item in hosts:
        if item.song not in songs:
            songs.append(item.song)
        page_named.add(item.song)
        named.append((item.song, item.url, item.context, (f"{item.detail}; " if item.detail else "")
                      + f"shown on the page of the original-game song {item.song!r}", item.source_id))
    return Subject(tuple(songs), frozenset(page_named), tuple(named), unread, unread_pages)


def unread_note(subject: Subject, songs: Iterable[str]) -> tuple[str, bool]:
    """``(asset note, mapping rests on placement)`` of a list graphic whose subject was not read
    (``subject.unread``): the songs of the host song pages that the mapping still contains come from that gallery
    placement alone -- no caption or title names them -- so the mapping is at most ``inferred``."""
    placed = [song for song in songs if song in subject.unread_pages]
    pages = list(subject.unread_pages)
    shown = ", ".join(pages[:3]) + (f" and {len(pages) - 3} more" if len(pages) > 3 else "")
    plural = len(pages) > 1
    where = f"the original-game song {'pages' if plural else 'page'} showing it ({shown})"
    if placed:
        text = (f"{where} {'map their songs' if plural else 'maps its song'} by that gallery placement alone "
                f"(mapping inferred), not because a caption or the file title names {'them' if plural else 'it'}")
    else:
        text = f"{where} {'are' if plural else 'is'} not taken as its subject"
    return f"List subject not read: {subject.unread}; {text}.", bool(placed)


def _source_key(source_id: str) -> str:
    return next(key for key, source in config.SOURCES.items() if source.source_id == source_id)


def _page_role(title: str) -> str | None:
    """Role of every image on a reviewed list page (Characters, Artists, ...), else None."""
    role = config.PAGE_ROLES.get(title)
    if role is None:
        for prefix, prefix_role in config.PAGE_ROLE_PREFIXES:
            if title.startswith(prefix):
                role = prefix_role
    return role


def _list_page_role(title: str, sections: list[str], info: Mapping[str, Any]) -> tuple[str | None, int]:
    role = _page_role(title)
    if role is not None:
        return role, 2
    role = _section_role(title, sections)
    if role is not None:  # store sections also list artwork items, so their exclusion roles are only strength 1
        return role, (2 if role in KINDS else 1)
    long_edge = max(info.get("width") or 0, info.get("height") or 0)
    if title in config.UI_PAGES:  # documented size heuristic on interface pages, hence only strength 1
        return ("gameplay_screenshot" if long_edge >= config.UI_SCREENSHOT_MIN_EDGE else "game_ui"), 1
    if title in config.STORE_PAGES and 0 < long_edge <= config.STORE_ICON_MAX_EDGE:
        return "game_ui", 1
    return None, 0


def usage_evidence(source: config.WikiSource, file_title: str, usage: Mapping[str, Any],
                   pages: Mapping[str, PageInfo], direct_pages: set[str]) -> list[Evidence]:
    """Evidence from ``prop=fileusage`` and the file's own categories: embeddings the wikitext scan did not see
    (template-built names, other namespaces) and category roles."""
    found = []
    for entry in usage.get("fileusage") or []:
        title, namespace = entry.get("title"), entry.get("ns")
        if not isinstance(title, str) or title in direct_pages:
            continue
        url = config.page_url(source, title)
        if namespace == 0 and title in pages:
            info = pages[title]
            # On a page that transcludes other articles the file may come from the transcluded page (parsed on its
            # own), so the aggregator's game is no evidence.
            game = info.game if info.game in ("dynamix", "dynamix_universe") and not info.transcludes else None
            role = "site_ui" if title in config.SITE_UI_PAGES else _page_role(title)
            # The page of exactly one original-game song (not an aggregator): the song a filename-only song_art file
            # belongs to (used by decide() only when no role evidence names a song; mapping at most inferred).
            originals = [song for song in info.songs if song.template == "Song"]
            song = originals[0].title if len(originals) == 1 and not info.transcludes else None
            found.append(Evidence(source.source_id, title, info.url, "embedded", role, 2 if role else 0, game,
                                  song=song, song_page=title if song else None,
                                  detail=f"page game: {info.game or 'none'} ({info.game_reason}); not referenced "
                                         "directly in the page text (transcluded or template-built)"))
        elif namespace == 10:
            role = "site_ui" if config.SITE_UI_TEMPLATE_RE.match(title) else None
            found.append(Evidence(source.source_id, title, url, "embedded in template", role, 1 if role else 0, None))
        else:
            found.append(Evidence(source.source_id, title, url, f"embedded in namespace {namespace}", None, 0, None,
                                  detail="user/forum/non-article page"))
    for category in _category_titles(usage):
        role = config.FILE_CATEGORY_ROLES.get(category)
        if role is not None:
            found.append(Evidence(source.source_id, file_title, config.page_url(source, file_title), "file category",
                                  role, 2, None, detail=category,
                                  list_graphic=category in config.LIST_CATEGORIES and role == "reference"))
    return found


# Miraheze's import summary on every file imported from Fandom (2026-08-09 maintenance script).
IMPORT_BOILERPLATE = re.compile(r"^\s*(?:==\s*Summary\s*==\s*)?Importing (?:images|files) from "
                                r"https?://dynamixc4cat\.fandom\.com\S*\s*(?:\(\[\[phorge:T\d+\|T\d+\]\]\))?\s*$", re.I)
_REDIRECT = re.compile(r"^\s*#REDIRECT\s*\[\[\s*:?\s*(?:file|image)\s*:([^\]|#]+)", re.I)
_DESCRIPTION_NOISE = re.compile(r"\[\[\s*Category\s*:[^\]]*\]\]|^\s*==+[^=\n]*==+\s*$|\{\{[^{}]*\}\}|/\*[^*]*\*/",
                                re.I | re.M)


def description_text(text: str | None) -> str:
    """Human-written text of a file description page or upload comment: Miraheze's import boilerplate, category
    links, headings, templates (licensing boxes) and edit-summary placeholders removed; '' when nothing remains."""
    if not text or IMPORT_BOILERPLATE.match(text):
        return ""
    cleaned = _DESCRIPTION_NOISE.sub(" ", wikitext.mask_comments(text))
    return " ".join(cleaned.split())


def redirect_target(text: str | None) -> str | None:
    """``File:`` title a file description page redirects to (``#REDIRECT [[File:X]]``), else None."""
    match = _REDIRECT.match(text or "")
    if not match:
        return None
    name = wikitext.canonical_file_name(match.group(1))
    return "File:" + name if name else None


def filepage_evidence(source: config.WikiSource, file_title: str, filepage: Mapping[str, Any],
                      pages: Mapping[str, PageInfo]) -> list[Evidence]:
    """Pages that link to the file's description page (``[[:File:X]]``, e.g. a citation in a song's Trivia). A link is
    not a use of the image, so it gives neither a role nor a game; the linking page and its game are recorded for
    reviewers. Links from talk/user/forum pages are recorded the same way."""
    found = []
    for entry in filepage.get("linkshere") or []:
        title, namespace = entry.get("title"), entry.get("ns")
        if not isinstance(title, str):
            continue
        if entry.get("redirect"):
            found.append(Evidence(source.source_id, title, config.page_url(source, title), "file page alias", None, 0,
                                  None, detail=f"{title} redirects to this file page"))
        elif namespace == 0 and title in pages:
            info = pages[title]
            found.append(Evidence(source.source_id, title, info.url, "linked from article", None, 0, None,
                                  detail=f"links to the file page; page game: {info.game or 'none'} "
                                         f"({info.game_reason})"))
        else:
            found.append(Evidence(source.source_id, title, config.page_url(source, title),
                                  f"linked from namespace {namespace}", None, 0, None,
                                  detail="user/talk/forum/file page link"))
    return found


def review_evidence(source: config.WikiSource, file_title: str, review: Any) -> list[Evidence]:
    """Evidence items of an applicable archivist review (one per named song), so the mapping machinery sees its songs
    (as ``inferred``: strength 0)."""
    url = config.page_url(source, file_title)
    songs = list(review.songs) or [None]
    return [Evidence(source.source_id, file_title, url, "archivist review", review.role, 0, None, song=song,
                     detail=review.evidence, list_graphic=review.list_graphic) for song in songs]


def filename_evidence(source: config.WikiSource, file_title: str) -> list[Evidence]:
    name = file_title.split(":", 1)[1]
    for pattern, role in config.FILENAME_ROLES:
        if pattern.search(name):
            return [Evidence(source.source_id, file_title, config.page_url(source, file_title), "filename", role, 0,
                             None, detail=f"filename convention /{pattern.pattern}/")]
    return []


# --- Wiki-level collection --------------------------------------------------------------------------------------------

@dataclass
class WikiEvidence:
    source: config.WikiSource
    files: dict[str, dict]
    pages: dict[str, PageInfo]
    songs: list[SongRecord]
    evidence: dict[str, list[Evidence]]
    gif_frames: dict[str, int | None]
    filepages: dict[str, dict] = field(default_factory=dict)


def collect(source: config.WikiSource, files: Mapping[str, Mapping[str, Any]],
            usage: Mapping[str, Mapping[str, Any]], raw_pages: Mapping[str, Mapping[str, Any]],
            gif_frames: Mapping[str, int | None] | None = None,
            filepages: Mapping[str, Mapping[str, Any]] | None = None) -> WikiEvidence:
    pages = {title: analyze_page(source, page) for title, page in raw_pages.items()}
    by_page_title: dict[str, list[SongRecord]] = {}
    songs: list[SongRecord] = []
    for info in pages.values():
        for record in info.songs:
            songs.append(record)
            by_page_title.setdefault(info.title, []).append(record)
    evidence: dict[str, list[Evidence]] = {title: [] for title in files}
    direct: dict[str, set[str]] = {title: set() for title in files}
    titles = original_song_titles(pages.values())
    for title, page in sorted(raw_pages.items()):
        for file_title, item in page_evidence(source, page, pages[title], files, by_page_title, titles):
            evidence[file_title].append(item)
            direct[file_title].add(title)
    filepages = filepages or {}
    for file_title in files:
        evidence[file_title].extend(usage_evidence(source, file_title, usage.get(file_title, {}), pages,
                                                   direct[file_title]))
        evidence[file_title].extend(filepage_evidence(source, file_title, filepages.get(file_title, {}), pages))
        evidence[file_title].extend(filename_evidence(source, file_title))
    return WikiEvidence(source, {title: dict(info) for title, info in files.items()}, pages, songs, evidence,
                        dict(gif_frames or {}), {title: dict(page) for title, page in filepages.items()})


def universe_threshold(wikis: Iterable[WikiEvidence], fandom_key: str = "fandom") -> str | None:
    """Earliest Fandom upload of a file whose name mentions Dynamix Universe (None if none): before it the wiki had
    no Universe material, so an upload predating it was made for the original game. Usage is not used here because
    old files are reused on Universe pages (``File:Crane.jpg``, uploaded 2015, is now used only by a Universe page).
    Miraheze timestamps are 2026 import times and are not used."""
    earliest = None
    for wiki in wikis:
        if wiki.source.key != fandom_key:
            continue
        for title in wiki.evidence:
            if UNIVERSE_NAME.search(title):
                stamp = wiki.files[title].get("timestamp")
                if stamp and (earliest is None or stamp < earliest):
                    earliest = stamp
    return earliest


def _short(values: list[str]) -> list[str]:
    unique = list(dict.fromkeys(values))
    if len(unique) > MAX_EVIDENCE:
        return unique[:MAX_EVIDENCE] + [f"... and {len(unique) - MAX_EVIDENCE} more"]
    return unique


LINK_CONTEXTS = ("linked from article", "file page alias")
REVIEW_CONTEXT = "archivist review"
PACK_CONTEXT = " on a Category:Song Package page"  # suffix of the context of a package page's own cover


def caption_context(context: str) -> bool:
    """Evidence contexts that carry the wiki's own caption of the file: a gallery entry or a ``[[File:...]]`` link,
    including a package page's own cover."""
    return context.removesuffix(PACK_CONTEXT) in ("gallery", "link")


def _is_link(item: Evidence) -> bool:
    return item.context in LINK_CONTEXTS or item.context.startswith("linked from namespace")


def decide(file_title: str, items: list[Evidence], info: Mapping[str, Any], *, frames: int | None,
           include_animated: bool, fandom_timestamp: str | None, threshold: str | None, review: Any = None,
           review_stale: bool = False, alias: tuple[str, bool] | None = None,
           song_names: SongNames | None = None) -> Decision:
    """Role, game and planned disposition for one file title from pooled evidence.

    ``review`` is an applicable :class:`wikis.reviews.Review` (its evidence items carry the context
    ``archivist review``); it supplies the role only when no page evidence of strength >= 1 does (or breaks a tie
    between equally supported page roles) and the game only when no page context gives one; a ``mixed`` review adds
    its shared-game associations to a game that page context gives. ``review_stale`` marks a review of an earlier
    revision. ``alias`` is ``(target title, same reported SHA-1)`` for a file whose description page redirects to
    another file. ``song_names`` (the original-game song titles) lets the subject of a list or chart-announcement
    graphic be read, so that every song it names is mapped (:func:`list_subject`).
    """
    mime = (info.get("mime") or "").lower()
    if mime.startswith("video/"):
        return Decision(None, "unknown", [f"API mime {mime}"], [f"API mime {mime}"], [], [], "excluded",
                        f"video_placeholder: {mime} entry (embedded video, not a stored file)", "unmapped")
    if alias is not None and alias[1]:
        note = f"file description page redirects to {alias[0]}, which reports the same SHA-1"
        return Decision(None, "unknown", [], [note], [], [], "excluded", f"redirect_or_alias: {note}", "unmapped")
    page_items = [item for item in items if item.context != REVIEW_CONTEXT]
    games = {item.game for item in page_items if item.game}
    game_items = [item.text() for item in page_items if item.game]
    role_items = [item for item in page_items if item.role]
    top = max((item.strength for item in role_items), default=-1)
    leaders = _majority([item for item in role_items if item.strength == top])
    role_evidence = _short([f"{item.role} <- {item.text()}" for item in sorted(role_items, key=lambda i: -i.strength)])
    # A review refines an exclusion that page evidence gives (wiki chrome usage of a Universe announcement, a
    # placeholder on a profile list) to the exclusion seen in the bytes; it never turns an exclusion into a download.
    refines = (review is not None and review.role is not None and review.role not in KINDS and len(leaders) == 1
               and leaders[0] in EXCLUSION_ROLES and review.role != leaders[0])
    # The game's own branding that the wiki shows as interface (Main Page header): site_ui describes the wiki's use.
    branding = (review is not None and review.official_branding and review.role in KINDS
                and {item.role for item in role_items if item.strength >= 1} == {"site_ui"})
    # A whole game screen that a page about a game feature shows: game_ui describes the page's topic, not the file.
    screen = (review is not None and review.whole_screen and review.role == "gameplay_screenshot"
              and {item.role for item in role_items if item.strength >= 1} == {"game_ui"})
    reviewed_role = review is not None and review.role is not None and (
        top < 1 or (len(leaders) > 1 and review.role in leaders) or refines or branding or screen)
    rule_notes: list[str] = []
    if reviewed_role:
        leaders, top = [review.role], max(top, 0)
        role_evidence = _short([f"{review.role} <- {review.role_text()}"] + role_evidence)
    if branding:
        overridden = "; ".join(_clip_text(item.text()) for item in role_items if item.role == "site_ui")
        rule_notes.append(f"Kind {review.role} by the archivist review: official branding (the game's own wordmark) "
                          f"that the wiki reuses as an interface image; the site_ui page evidence describes that use, "
                          f"not the file: {overridden}.")
    if screen:
        overridden = "; ".join(_clip_text(item.text()) for item in role_items if item.role == "game_ui")
        rule_notes.append(f"Kind {review.role} by the archivist review: a whole game screen that a page about a game "
                          f"feature shows; the game_ui page evidence describes the page's topic, not the file: "
                          f"{overridden}.")
    subject = list_subject(file_title, page_items, song_names)
    listed = _list_rule(page_items, leaders, review, reviewed_role)
    if listed is not None:
        entry, note, by_review = listed
        leaders, top = ["reference"], max(top, 0)
        reviewed_role = reviewed_role or by_review  # a mapping that rests on the review is inferred at most
        role_evidence = _short([f"reference <- {entry}"] + role_evidence)
        rule_notes.append(note)
    role = leaders[0] if len(leaders) == 1 else None
    # An archivist review that saw the list or chart announcement in the bytes and names its songs transcribes them
    # (see wikis.reviews): the mapping holds exactly its songs. A song that page evidence names (a caption's subject, a
    # host song page, a gallery item) but the review does not is left out and recorded -- a caption may name the pair
    # of a two-song comeback whose halves are separate images -- and a song it names that no page evidence maps is
    # added; either change rests on the review (inferred).
    transcribed = (review is not None and review.list_graphic and review.role == role == "reference"
                   and bool(review.songs))
    mapped_items, unseen = _transcription(items, role, review.songs if transcribed else None)
    mapped_page_items = [item for item in mapped_items if item.context != REVIEW_CONTEXT]
    songs, mapping, mapping_evidence = _song_mapping(mapped_items, role, reviewed_role=reviewed_role)
    song_pages = list(dict.fromkeys(item.song_page for item in items if item.song_page))
    song_page_roles = any(item.song_page for item in role_items)
    unreleased = any(item.context == "Template:SongOther|Imgsuffix" for item in items)
    if not games and role in DOWNLOAD_KINDS and song_page_roles and not unreleased and threshold \
            and fandom_timestamp and fandom_timestamp < threshold:
        games = {"dynamix"}
        game_items.append(f"pre-Universe upload: Fandom upload {fandom_timestamp} precedes the wiki's first "
                          f"Universe-named upload ({threshold}); role from a song page")
    reviewed_game = not games and review is not None and review.game is not None
    if reviewed_game:
        games = {review.game}
        game_items.append(review.game_text())
    # A review that saw another game's identity in the bytes (game "mixed" with associations) adds it to the original
    # game that page context gives: the file is shared, not moved to the other game.
    shared = (not reviewed_game and review is not None and review.game == "mixed" and bool(review.associations)
              and bool(games) and games <= {"dynamix", "mixed"})
    if shared:
        games = games | {"mixed"}
        game_items.append(review.game_text() + " (shared-game identity added to the page evidence)")
    if games == {"dynamix"}:
        game = "dynamix"
    elif games == {"dynamix_universe"}:
        game = "dynamix_universe"
    elif games == {"not_dynamix"}:
        game = "not_dynamix"
    elif games:
        game = "mixed"
    else:
        game = "unknown"
    if role == "song_art" and not songs:
        songs, mapping, mapping_evidence = _embedding_song(mapped_page_items, songs, mapping, mapping_evidence)
    if role in KINDS and not songs:
        songs, mapping, mapping_evidence = _gallery_song(mapped_page_items, songs, mapping, mapping_evidence)
    # The songs a list subject names join the mapping, one song or several (a transcription keeps only its own).
    if subject is not None and role == "reference" and subject.songs:
        wanted = subject.only(review.songs) if transcribed else subject
        songs, mapping, mapping_evidence = _subject_songs(wanted, songs, mapping, mapping_evidence,
                                                          reviewed_role=reviewed_role)
        unseen += [song for song in subject.songs if song not in wanted.songs and song not in unseen]
    if unseen:
        mapping, mapping_evidence = _with_review_evidence(items, mapping_evidence)
        them = "them" if len(unseen) > 1 else "it"
        rule_notes.append(f"Not mapped although page evidence names {them}: {', '.join(map(repr, unseen))}; the "
                          f"archivist review ({review.title}) transcribes the songs the bytes show and does not list "
                          f"{them}.")
    if transcribed and any(song not in songs for song in review.songs):
        songs = songs + [song for song in review.songs if song not in songs]
        mapping, mapping_evidence = _with_review_evidence(items, mapping_evidence)
    if subject is not None and subject.unread and role == "reference":
        note, placed = unread_note(subject, songs)
        rule_notes.append(note)
        if placed and mapping == "confirmed_wiki":
            mapping = "inferred"
    associations = {"dynamix_universe" for item in page_items if item.game == "dynamix_universe"} | {
        re.sub(r"[^a-z0-9]+", "_", item.other_game.casefold()).strip("_") for item in page_items
        if item.other_game and re.sub(r"[^a-z0-9]+", "_", item.other_game.casefold()).strip("_")}
    if (reviewed_game or shared) and game == "mixed":
        associations |= set(review.associations)
    base = dict(game=game, game_evidence=_short(game_items), role_evidence=role_evidence, songs=songs,
                song_pages=song_pages, mapping=mapping, mapping_evidence=mapping_evidence, strength=top,
                associations=sorted(associations), rule_notes=rule_notes)
    if len(leaders) > 1:
        return Decision(None, **base, disposition="excluded" if game == "dynamix_universe" else "pending",
                        reason=("universe_only: Universe-only usage; conflicting roles " + ", ".join(leaders))
                        if game == "dynamix_universe" else
                        f"unresolved_role: conflicting {_strength_name(top)} evidence for {', '.join(leaders)}"
                        + (f"; {review.role_text()}; {review.pending}" if review is not None and review.pending else "")
                        + _stale(review_stale))
    kind = role if role in KINDS else None
    if reviewed_role and role in ("not_dynamix", "universe_only"):
        wanted = "not_dynamix" if role == "not_dynamix" else "dynamix_universe"
        if game != wanted:  # the review contradicts page context: look again instead of overriding the pages
            return Decision(None, **base, disposition="pending",
                            reason=f"unresolved_game: the archivist review says {role} but page context gives "
                                   f"{game}; review again")
        return Decision(None, **base, disposition="excluded", reason=f"{role}: {review.role_text()}")
    if game == "dynamix_universe":
        return Decision(kind, **base, disposition="excluded",
                        reason="universe_only: " + (review.game_text() if reviewed_game else
                                                    "used only in Dynamix Universe contexts"))
    if game == "not_dynamix":
        return Decision(None, **base, disposition="excluded", reason=f"not_dynamix: {review.game_text()}")
    if reviewed_role and role not in KINDS:
        return Decision(None, **base, disposition="excluded", reason=f"{role}: {review.role_text()}")
    if role in EXCLUSION_ROLES:
        labels = {"person_photo": "composer/artist/charter profile image", "site_ui": "wiki interface image",
                  "game_ui": "game interface element (icon, button, grade, badge)",
                  "external_release_art": "cover art of an external music release",
                  "out_of_scope_other": "not game artwork"}
        return Decision(None, **base, disposition="excluded", reason=f"{role}: {labels.get(role, role)}")
    if kind is None:
        if review is not None and review.pending:
            reason = f"unresolved_role: {review.role_text()}; {review.pending}"
        else:
            reason = _unresolved_role(page_items) + _stale(review_stale)
        probe = review is not None and review.probe and not role_items
        return Decision(None, **base, disposition="probe" if probe else "pending", reason=reason)
    official = review is not None and review.official_animation
    if mime == "image/gif" and (frames is None or frames > 1) and not include_animated and not official:
        what = "animated GIF" if frames else "GIF with unknown frame count"
        return Decision(kind, **base, disposition="excluded",
                        reason=f"animated_capture: {what} ({frames or '?'} frames); use --include-animated to keep")
    if game == "unknown":
        # A filename convention alone never classifies a file: the hint stays in the reason only.
        if review is not None and review.pending:
            why = review.pending
        elif unreleased:
            why = "cover of an unreleased/cancelled song (Template:SongOther); in-game use not established"
        else:
            why = "no page establishes original-game use"
        source = "archivist review" if reviewed_role else _strength_name(top)
        return Decision(kind if top > 0 or reviewed_role else None, **base, disposition="pending",
                        reason=f"unresolved_game: {kind} role ({source}) but {why}" + _stale(review_stale))
    if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        return Decision(kind, **base, disposition="excluded",
                        reason=f"out_of_scope_other: unsupported media type {mime or 'unknown'}")
    return Decision(kind, **base, disposition="download", reason="")


RULE_TEXT_LIMIT = 400


def _clip_text(text: str) -> str:
    return text if len(text) <= RULE_TEXT_LIMIT else text[:RULE_TEXT_LIMIT - 3] + "..."


def _list_rule(page_items: list[Evidence], leaders: list[str], review: Any, reviewed_role: bool
               ) -> tuple[str, str, bool] | None:
    """``(role evidence entry, asset note, rests on the review)`` when the list and chart-announcement rule
    (:data:`config.LIST_GRAPHIC_RULE`) decides that the file is ``reference``, else None.

    It applies when its evidence exists (a page item flagged ``list_graphic`` -- a chart-list, comeback, new-chart or
    lineup caption, or a chart-list category -- or an applicable review with ``list_graphic``) and the roles otherwise
    decided are ``promotional`` alone or tied with ``reference``, whether the graphic is about one song or several;
    the asset note quotes the overridden promotional page evidence (the role evidence lists it after the rule's entry).
    A review that saw neither a list nor level marks in the bytes (role promotional) blocks the rule. A file whose
    ``reference`` role a list review decided (no page role evidence) records the rule too, without an override."""
    reviewed_list = review is not None and review.list_graphic
    if review is not None and review.role == "promotional":
        return None
    page_lists = [item for item in page_items if item.list_graphic and item.role == "reference"]
    if reviewed_list and reviewed_role and leaders == ["reference"]:
        # The review's own role evidence entry follows in the role evidence; the entry only names it.
        basis, overridden, named = [review.role_text()], [], f"the archivist review ({review.title})"
    elif (page_lists or reviewed_list) and "promotional" in leaders and set(leaders) <= {"promotional", "reference"}:
        basis = [item.text() for item in page_lists] or [review.role_text()]
        overridden, named = [item.text() for item in page_items if item.role == "promotional"], None
    else:
        return None
    basis_text = "; ".join(_clip_text(text) for text in list(dict.fromkeys(basis))[:2])
    overridden_text = "; ".join(_clip_text(text) for text in list(dict.fromkeys(overridden))[:3])
    entry = f"{config.LIST_GRAPHIC_RULE}; list evidence: {named or basis_text}"
    note = (f"Kind reference by the {config.LIST_GRAPHIC_RULE} (09-frozen-contracts.md, coordinator classification "
            f"decisions). List evidence: {basis_text}.")
    if overridden:  # the overridden items follow in the role evidence; the asset note quotes them
        entry += "; it overrides the promotional page evidence listed here"
        note += f" Promotional page evidence it overrides: {overridden_text}."
    return entry, note, not page_lists


def _transcription(items: list[Evidence], role: str | None, transcribed: Iterable[str] | None
                   ) -> tuple[list[Evidence], list[str]]:
    """``(items for the song mapping, songs left out)``: without a transcription (``transcribed`` None) every item;
    with one (the songs a list review saw in the bytes), the page items naming another song are dropped, and the
    songs they would have mapped for ``role`` are returned so that the decision records them."""
    if transcribed is None:
        return items, []
    keep = set(transcribed)

    def other(item: Evidence) -> bool:
        return bool(item.song) and item.context != REVIEW_CONTEXT and item.song not in keep
    unseen = [item.song for item in items if other(item) and item.role == role and item.context != "embedded"]
    return [item for item in items if not other(item)], list(dict.fromkeys(unseen))


def _subject_songs(subject: Subject, songs: list[str], mapping: str, evidence: list[Any], *, reviewed_role: bool
                   ) -> tuple[list[str], str, list[Any]]:
    """Adds the songs a list or chart-announcement graphic's subject names (one song or several) to its mapping. The
    mapping stays ``confirmed_wiki`` only when every added song is named by page evidence (a caption naming the whole
    title, or a song page showing the graphic); a song read from the file title or from a partial spelling is
    ``inferred``."""
    extra = [song for song in subject.songs if song not in songs]
    if not extra:
        return songs, mapping, evidence
    confirmed = not reviewed_role and all(song in subject.page_named for song in extra) and (
        not songs or mapping == "confirmed_wiki")
    added = []
    for song in extra:
        url, field_name, quote, source_id = next(entry[1:] for entry in subject.named if entry[0] == song)
        added.append({"url": url, "field": field_name, "quote": quote[:300], "source_id": source_id})
    room = max(0, MAX_EVIDENCE - len(added))
    return songs + extra, "confirmed_wiki" if confirmed else "inferred", (evidence[:room] + added)[:MAX_EVIDENCE]


def _with_review_evidence(items: list[Evidence], evidence: list[Any]) -> tuple[str, list[Any]]:
    """``(inferred, mapping evidence)`` of a mapping an archivist review changed: the review is quoted last."""
    reviewed = next((item for item in items if item.context == REVIEW_CONTEXT), None)
    if reviewed is None:
        return "inferred", evidence
    entry = {"url": reviewed.url, "field": REVIEW_CONTEXT, "quote": reviewed.detail[:300] or None,
             "source_id": reviewed.source_id}
    kept = [item for item in evidence if item != entry]
    return "inferred", (kept[:MAX_EVIDENCE - 1] + [entry])


def _gallery_song(items: list[Evidence], songs: list[str], mapping: str, evidence: list[Any]
                  ) -> tuple[list[str], str, list[Any]]:
    """A file whose role evidence names no song: the one original-game song whose page gallery shows it (a caption
    such as 'Used "Justice Justice" Background Parts' gives no role by itself), as ``inferred``. Only called when
    :func:`_song_mapping` found no song, so no gallery item names a song for the decided role."""
    hosts = [item for item in items if item.context == "gallery" and item.song and item.game != "dynamix_universe"]
    titles = sorted({item.song for item in hosts})
    if len(titles) != 1:
        return songs, mapping, evidence
    found = [{"url": item.url, "field": "gallery",
              "quote": (f"{item.detail}; " if item.detail else "") + f"in the gallery of the original-game song "
                       f"{item.song!r}; role from other evidence", "source_id": item.source_id} for item in hosts]
    return titles, "inferred", [dict(entry, quote=entry["quote"][:300]) for entry in found[:MAX_EVIDENCE]]


def _embedding_song(items: list[Evidence], songs: list[str], mapping: str, evidence: list[Any]
                    ) -> tuple[list[str], str, list[Any]]:
    """A song_art file that no role evidence maps: the one original-game song whose page embeds it (fileusage of a
    page with a single ``Template:Song``, e.g. a cover the template builds from another field), as ``inferred``."""
    hosts = [item for item in items if item.context == "embedded" and item.song and item.game == "dynamix"]
    titles = sorted({item.song for item in hosts})
    if len(titles) != 1:
        return songs, mapping, evidence
    found = [{"url": item.url, "field": "embedded",
              "quote": f"embedded by the page of the original-game song {item.song!r} (its only Template:Song); "
                       "role from the filename convention", "source_id": item.source_id} for item in hosts]
    return titles, "inferred", found[:MAX_EVIDENCE]


def _stale(stale: bool) -> str:
    return "; an archivist review of an earlier revision no longer applies (review again)" if stale else ""


def _unresolved_role(items: list[Evidence]) -> str:
    embedded = sorted({item.page for item in items if item.context != "filename" and not _is_link(item)})
    linked = sorted({item.page for item in items if _is_link(item)})

    def listed(values: list[str]) -> str:
        return ", ".join(values[:5]) + (f" and {len(values) - 5} more" if len(values) > 5 else "")
    if not embedded and not linked:
        return "unresolved_role: not embedded on any page of either wiki and no role evidence"
    if not embedded:
        return f"unresolved_role: not embedded on any page; its file page is linked from {listed(linked)}"
    reason = f"unresolved_role: no caption/template/list-page role evidence (used on {listed(embedded)})"
    return reason + (f"; file page linked from {listed(linked)}" if linked else "")


def _majority(items: list[Evidence]) -> list[str]:
    """Roles named by the most distinct page titles among same-strength evidence (Miraheze and Fandom copies of one
    page count once); several roles when tied."""
    pages: dict[str, set[str]] = {}
    for item in items:
        pages.setdefault(item.role, set()).add(item.page)
    if not pages:
        return []
    best = max(len(titles) for titles in pages.values())
    return sorted(role for role, titles in pages.items() if len(titles) == best)


def _strength_name(strength: int) -> str:
    return {3: "template", 2: "caption/list-page/category", 1: "list-link/section", 0: "filename-only"}.get(
        strength, "no")


def _song_mapping(items: list[Evidence], role: str | None, *, reviewed_role: bool
                  ) -> tuple[list[str], str, list[Any]]:
    """``(songs, mapping status, mapping evidence)`` of a file with role ``role``.

    Page evidence naming a song for the role decides: its strongest items, and among them the songs named by the
    most distinct pages (Miraheze and Fandom copies of a page count once), so a single conflicting ``link=`` target
    on one list page does not add a second song or its credits. The mapping is ``confirmed_wiki`` when that evidence
    is a template, caption, list page or category (strength >= 2), else ``inferred``; song art named for several songs
    by equally many pages below template strength is a ``conflict`` (one cover, contradicting pages; two songs'
    templates naming one file are a shared cover). An archivist review's songs
    count only when the review decided the role or no page evidence names a song for it; the mapping is then
    ``inferred`` and quotes the review ("embedded" items map filename-only song art only, see _embedding_song)."""
    named = [item for item in items if item.song and item.role == role and item.context != "embedded"]
    page = [item for item in named if item.context != REVIEW_CONTEXT]
    winners: list[str] = []
    top = -1
    if page:
        top = max(item.strength for item in page)
        pages: dict[str, set[str]] = {}
        for item in page:
            if item.strength == top:
                pages.setdefault(item.song, set()).add(item.page)
        best = max(len(titles) for titles in pages.values())
        winners = [song for song, titles in pages.items() if len(titles) == best]
    review = [item for item in named if item.context == REVIEW_CONTEXT] if (reviewed_role or not page) else []
    chosen_page = [item for item in page if item.song in winners]
    songs = list(dict.fromkeys(item.song for item in chosen_page + review))
    if not songs:
        return [], "unmapped", []
    confirmed = [item for item in chosen_page if item.strength >= 2]
    shown = (confirmed or chosen_page)[:MAX_EVIDENCE - min(len(review), MAX_EVIDENCE // 2)] + review
    evidence = [{"url": item.url, "field": item.context, "quote": item.detail[:300] or None,
                 "source_id": item.source_id} for item in shown[:MAX_EVIDENCE]]
    status = "confirmed_wiki" if confirmed and not review and not reviewed_role else "inferred"
    if role == "song_art" and len(winners) > 1 and top < 3:
        status = "conflict"
    return songs, status, evidence
