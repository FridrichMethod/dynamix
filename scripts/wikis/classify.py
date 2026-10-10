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
or game only where page evidence is absent; their song mappings are ``inferred``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from lib import schema

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
                  files: Mapping[str, Mapping[str, Any]], song_pages: Mapping[str, list[SongRecord]]
                  ) -> list[tuple[str, Evidence]]:
    """``(file title, Evidence)`` for every file reference on an article page."""
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
            role, strength, context = "pack_cover", 3, f"{ref.context} on a Category:Song Package page"
        else:
            caption_role = _caption_role(ref.caption) if ref.caption else None
            if caption_role is not None:
                role, strength = caption_role, 2
                if info.songs:
                    song_page = info.title
                    song = _region_song(info, region_game) if caption_role != "external_release_art" else None
                other = config.OTHER_GAME_ARTWORK.search(ref.caption) if caption_role in KINDS else None
                if other is not None:
                    game = "mixed"
                    other_game = other.group("other").strip().strip("'\"")
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
        result.append((file_title, Evidence(source.source_id, info.title, info.url, context, role, strength, game,
                                            song, song_page, detail, other_game)))
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
            found.append(Evidence(source.source_id, title, info.url, "embedded", role, 2 if role else 0, game,
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
                                  role, 2, None, detail=category))
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
                     detail=review.evidence) for song in songs]


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
    for title, page in sorted(raw_pages.items()):
        for file_title, item in page_evidence(source, page, pages[title], files, by_page_title):
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


def _is_link(item: Evidence) -> bool:
    return item.context in LINK_CONTEXTS or item.context.startswith("linked from namespace")


def decide(file_title: str, items: list[Evidence], info: Mapping[str, Any], *, frames: int | None,
           include_animated: bool, fandom_timestamp: str | None, threshold: str | None, review: Any = None,
           review_stale: bool = False, alias: tuple[str, bool] | None = None) -> Decision:
    """Role, game and planned disposition for one file title from pooled evidence.

    ``review`` is an applicable :class:`wikis.reviews.Review` (its evidence items carry the context
    ``archivist review``); it supplies the role only when no page evidence of strength >= 1 does (or breaks a tie
    between equally supported page roles) and the game only when no page context gives one. ``review_stale`` marks a
    review of an earlier revision. ``alias`` is ``(target title, same reported SHA-1)`` for a file whose description
    page redirects to another file.
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
    reviewed_role = review is not None and review.role is not None and (
        top < 1 or (len(leaders) > 1 and review.role in leaders))
    if reviewed_role:
        leaders, top = [review.role], max(top, 0)
        role_evidence = _short([f"{review.role} <- {review.role_text()}"] + role_evidence)
    role = leaders[0] if len(leaders) == 1 else None
    songs = list(dict.fromkeys(item.song for item in items if item.song and item.role == role))
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
    mapping, mapping_evidence = _mapping(items, role)
    associations = {"dynamix_universe" for item in page_items if item.game == "dynamix_universe"} | {
        re.sub(r"[^a-z0-9]+", "_", item.other_game.casefold()).strip("_") for item in page_items
        if item.other_game and re.sub(r"[^a-z0-9]+", "_", item.other_game.casefold()).strip("_")}
    if reviewed_game and game == "mixed":
        associations |= set(review.associations)
    base = dict(game=game, game_evidence=_short(game_items), role_evidence=role_evidence, songs=songs,
                song_pages=song_pages, mapping=mapping, mapping_evidence=mapping_evidence, strength=top,
                associations=sorted(associations))
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
        return Decision(None, **base, disposition="pending", reason=reason)
    if mime == "image/gif" and (frames is None or frames > 1) and not include_animated:
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


def _mapping(items: list[Evidence], role: str | None) -> tuple[str, list[Any]]:
    mapped = [item for item in items if item.song and item.role == role]
    if not mapped:
        return "unmapped", []
    confirmed = [item for item in mapped if item.strength >= 2]
    chosen = confirmed or mapped
    evidence = []
    for item in chosen[:MAX_EVIDENCE]:
        evidence.append({"url": item.url, "field": item.context, "quote": item.detail[:300] or None,
                         "source_id": item.source_id})
    return ("confirmed_wiki" if confirmed else "inferred"), evidence
