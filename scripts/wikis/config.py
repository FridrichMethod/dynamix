"""Registered wiki sources (09-frozen-contracts.md, Identifiers) and the reviewed page evidence tables.

The page tables are data, not code paths: each entry names a wiki page whose game or media role was established by
reading it (2026-10-10). They are used only together with per-file usage evidence and are listed in classification
evidence strings, so a reviewer can see why a file got its role.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WikiSource:
    key: str
    source_id: str
    name: str
    url: str
    platform: str
    hosts: tuple[str, ...]
    api_url: str | None = None
    article_base: str | None = None  # page URL prefix; the title is appended with spaces as underscores
    download_query: str | None = None  # extra query parameter appended to every file URL (Fandom: format=original)
    notes: str = ""
    list_pages: tuple[tuple[str, str], ...] = field(default=())  # (label, URL) for HTML list sources


MIRAHEZE = WikiSource(
    key="miraheze",
    source_id="wikis:miraheze",
    name="Dynamix Wiki (Miraheze)",
    url="https://dynamix.miraheze.org/wiki/Main_Page",
    platform="mediawiki",
    hosts=("dynamix.miraheze.org", "static.wikitide.net", "static.miraheze.org"),
    api_url="https://dynamix.miraheze.org/w/api.php",
    article_base="https://dynamix.miraheze.org/wiki/",
    notes="Current English community wiki (moved from Fandom in 2026; most files were imported by a maintenance "
    "script on 2026-08-09, so their upload timestamps are import times). Files are served unmodified from "
    "static.wikitide.net; the API SHA-1 equals the served bytes for sampled files.",
)
FANDOM = WikiSource(
    key="fandom",
    source_id="wikis:fandom",
    name="Dynamix Wiki (Fandom, legacy)",
    url="https://dynamixc4cat.fandom.com/wiki/Main_Page",
    platform="mediawiki",
    hosts=("dynamixc4cat.fandom.com", "static.wikia.nocookie.net"),
    api_url="https://dynamixc4cat.fandom.com/api.php",
    article_base="https://dynamixc4cat.fandom.com/wiki/",
    download_query="format=original",
    notes="Legacy English wiki (dynamixc4cat.fandom.com; dynamix.fandom.com is an unrelated game). Its CDN serves a "
    "transformed WEBP for /revision/latest unless format=original is requested; files are downloaded with "
    "format=original. The API size/SHA-1 of many files differ from the bytes format=original returns; that is "
    "recorded as source_reported versus download_observed, not as a second version. YouTube entries "
    "(video/youtube) are placeholders, not files.",
)
ATWIKI = WikiSource(
    key="atwiki-ja",
    source_id="wikis:atwiki-ja",
    name="Dynamix wiki (atwiki, Japanese)",
    url="https://w.atwiki.jp/dynamix2ch/",
    platform="atwiki",
    hosts=("w.atwiki.jp",),
    notes='Japanese wiki of the original game ("無印Dynamix"); read for the original-game song list only. '
    "Attachments were not enumerated.",
    list_pages=(
        ("Rank・Share解禁楽曲一覧", "https://w.atwiki.jp/dynamix2ch/pages/25.html"),
        ("楽曲パック一覧", "https://w.atwiki.jp/dynamix2ch/pages/36.html"),
        ("イベント楽曲・frag解禁曲一覧", "https://w.atwiki.jp/dynamix2ch/pages/37.html"),
        ("コラボ楽曲一覧", "https://w.atwiki.jp/dynamix2ch/pages/499.html"),
        ("削除曲", "https://w.atwiki.jp/dynamix2ch/pages/529.html"),
    ),
)
RHYTHMGAME = WikiSource(
    key="rhythmgame-ja",
    source_id="wikis:rhythmgame-ja",
    name="Rhythm game wiki (wikiwiki.jp): Dynamix",
    url="https://wikiwiki.jp/rhythmgame/Dynamix",
    platform="wikiwiki",
    hosts=("wikiwiki.jp",),
    notes="Japanese multi-game rhythm-game wiki; its Dynamix page has one original-game song table (title, credited "
    "name, levels). Read for metadata only; no files.",
    list_pages=(("楽曲一覧", "https://wikiwiki.jp/rhythmgame/Dynamix"),),
)
SOURCES = {source.key: source for source in (MIRAHEZE, FANDOM, ATWIKI, RHYTHMGAME)}
MEDIAWIKI_KEYS = ("miraheze", "fandom")
HTML_KEYS = ("atwiki-ja", "rhythmgame-ja")
ALL_HOSTS = tuple(sorted({host for source in SOURCES.values() for host in source.hosts}))


def page_url(source: WikiSource, title: str) -> str:
    from urllib.parse import quote

    return source.article_base + quote(title.replace(" ", "_"), safe="/:()!,'*;@$&+=~-._")


# --- Page evidence ----------------------------------------------------------------------------------------------------

# Pages of the original game that carry no "Dynamix" category or "/Dynamix" suffix. Each was read on 2026-10-10:
# Characters/Parts document the Dynamix 3.x character and parts systems ("introduced in version 3.1"/"3.4.3"), the
# event pages list Dynamix 3.x event songs, Event Bundles is "the Event Bundles system in Dynamix, introduced in
# version 3.18", Festival Events lists festival events "that happened in Dynamix".
ORIGINAL_GAME_PAGES = {
    "Characters",
    "Parts",
    "Event Bundles",
    "Festival Events",
    "Event Setting",
    "Code",
    "Modes",
    "Mods",
    "Combo",
    "Special & 2016 Event",
    "Wave Test Setting",
    "Charters",
} | {f"{year} Event" for year in range(2016, 2026)}
ORIGINAL_GAME_PREFIXES = ("Charters/",)
# Pages that discuss both games or none (their usage alone gives no game): artist/charter lists, wiki navigation.
# "Store" and "Grading" are tab indexes transcluding both games' subpages.
NEUTRAL_PAGES = {
    "Artists",
    "Main Page",
    "Dynamix Wiki",
    "Songs",
    "Update History",
    "How to Play",
    "Main Menu",
    "Start",
    "Dynamix",
    "Store",
    "Grading",
}

# Media roles of reviewed list pages (role, strength 2). Section-specific roles are in SECTION_ROLES.
PAGE_ROLES = {
    "Characters": "character",
    "Artists": "person_photo",
    "Artists (Legacy)": "person_photo",
    "Charters": "person_photo",
    "Google Play Achievements": "game_ui",
    "Grading/Dynamix": "game_ui",
    "Grading/Dynamix Universe": "game_ui",
}
PAGE_ROLE_PREFIXES = (("Charters/", "person_photo"),)
# Game-mechanics/UI pages: small images are interface elements (game_ui), large ones screenshots of the game.
UI_PAGES = {
    "Wave Test Setting",
    "Main Menu/Dynamix",
    "Main Menu/Dynamix Universe",
    "How to Play/Dynamix",
    "How to Play/Dynamix Universe",
    "Start/Dynamix",
    "Start/Dynamix Universe",
    "Code",
    "Event Setting",
    "Modes",
    "Mods",
    "Combo",
    "Wave Test",
}
UI_SCREENSHOT_MIN_EDGE = 640  # long edge in px separating screenshots from icons on UI pages (documented heuristic)
# Store-like pages: small images without any other role are item/bundle icons (e.g. "EventBundle1.png", 200 px).
STORE_PAGES = {"Store/Dynamix", "Event Bundles"}
STORE_ICON_MAX_EDGE = 300
# (page, section regex, role): section headings are compared case-insensitively after markup removal. The innermost
# heading is tried first; outer headings only for artwork roles (Store's "Items" wraps lists of event collectibles).
SECTION_ROLES = (
    ("Parts", re.compile(r"title", re.I), "part"),
    ("Parts", re.compile(r"background", re.I), "background"),
    ("Parts", re.compile(r"\bparts?\b", re.I), "part"),
    ("Store/Dynamix", re.compile(r"^(?:bits|in store)$", re.I), "game_ui"),
    ("Store/Dynamix", re.compile(r"character", re.I), "character"),
    ("Store/Dynamix", re.compile(r"background", re.I), "background"),
    ("Store/Dynamix", re.compile(r"\bparts?\b", re.I), "part"),
    # Wave Test Setting > Wave Test Instructions: "The pictures are from C4Cat's Wave Test Cantonese introduction video
    # on YouTube" (the page, 2026-10-10). The 26 "Wave Test Step" images are frames of that presenter video (three
    # people at a table, an inset tablet, bilingual subtitles; inspected 2026-10-10), explanatory stills rather than
    # screenshots of the game: reference (archive tier), which also keeps the presenters out of the gallery grid.
    ("Wave Test Setting", re.compile(r"^wave test instructions$", re.I), "reference"),
)
SECTION_NOTES = {
    ("Wave Test Setting", "reference"): (
        "the page says \"The pictures are from C4Cat's Wave Test Cantonese "
        'introduction video on YouTube": stills of an official explanation video'
    ),
}
# Wiki chrome: templates and pages that decorate the wiki itself.
SITE_UI_PAGES = {"Main Page", "Dynamix Wiki"}
SITE_UI_TEMPLATE_RE = re.compile(
    r"^Template:(?:Main ?Page|MainPage|Navbox|Dynamix|Tnavbar|Headercell|"
    r"UnderConstruction|Moving|Color|Clr)",
    re.I,
)

# File description page categories (strength 2). Tier revision 1.1 (09-frozen-contracts.md): official chart lists are
# informational graphics, kind ``reference`` (archive tier), not promotional key art.
FILE_CATEGORY_ROLES = {
    "Category:Character images": "character",
    "Category:Background parts images": "background",
    "Category:Parts images": "part",
    "Category:Chart list images": "reference",
    "Category:Event images": "promotional",
    "Category:Artist profile images": "person_photo",
    "Category:Cover Art": "song_art",
    "Category:Videos": "video_placeholder",
}

# List and chart-announcement rule (coordinator decision 2026-10-10 as amended after wave 3, 09-frozen-contracts.md
# "Coordinator classification decisions"; it supersedes the earlier single-song exception): kind ``reference`` covers
# (D1) any announcement graphic that displays chart difficulty levels, for one song or several -- the single-song
# "<song> Chart List" / "COMING SOON" / "i'm back!" teasers with level marks included -- and (D2) any graphic whose
# primary content is a list of songs or charts (event and comeback lineups, "Upcoming Events", "Comeback Tracks",
# pack/collaboration lineup cards that print several titled jackets), regardless of the wiki category or caption that
# files it as promotional. ``promotional`` keeps graphics without chart-level information about one song, pack or
# event: key art, event/anniversary posters, single collaboration key visuals, banners, logos/wordmarks. Evidence for
# the rule is a chart-list/comeback/new-chart/lineup caption (LIST_CAPTION: the wiki's names of this design), a file
# category of chart lists, or an archivist review that saw such a graphic in the bytes (``list_graphic``); a filename
# alone never is. The rule replaces only a ``promotional`` role (classify._list_rule) and is recorded in the role
# evidence and the asset notes; a review that saw neither a list nor level marks (role promotional) keeps the graphic
# promotional.
LIST_GRAPHIC_RULE = (
    "list and chart-announcement rule (coordinator decision 2026-10-10, amended): an announcement "
    "graphic that displays chart difficulty levels, for one song or several, or whose primary content "
    "is a list of songs or charts (lineups; pack/collaboration cards printing several titled jackets) "
    "is kind reference regardless of the wiki category or caption that files it as promotional"
)
# Only plural/lineup forms are list captions: "Upcoming Event: Halloween 2023" announces one event (promotional).
LIST_CAPTION = re.compile(
    r"chart\s*list|difficulty\s+list|new\s+charts?\b|comeback|(?:up)?coming\s+events\b|event\s+line-?up", re.I
)
LIST_CATEGORIES = {"Category:Chart list images"}

# The songs such a graphic names (its subject) are all mapped: the text of the list caption (or, without one, of the
# file title) before the list words (SUBJECT_END), read against the original-game song titles: the whole text as one
# title, else split at SUBJECT_SEPARATOR into the fewest known titles (classify.list_subject). Song pages that show the
# graphic add their song only to a subject that a caption or the file title gives (a lineup caption without one, such
# as "Upcoming Events", names no song, which is recorded); a subject the titles do not resolve maps nothing.
SUBJECT_END = re.compile(r"\b(?:release\s+)?(?:chart\s*list|difficulty\s+list|new\s+charts?|comeback)\b", re.I)
SUBJECT_SEPARATOR = re.compile(r"\s+[xX\u00d7]\s+|\s*[&\uff06]\s*|\s+and\s+|\s*[,\uff0c\u3001]\s*|\s+/\s+")

# Informational announcement graphics (tier revision 1.1, kind ``reference``): the list and chart-announcement
# graphics above plus version-update/update-log notes and settings/difficulty/results announcements. Key art, banners,
# event/anniversary/collaboration visuals and teasers without level marks stay ``promotional``.
INFORMATIONAL_CAPTION = re.compile(
    LIST_CAPTION.pattern + r"|version\s+update|update\s+(?:log|notes?)\b|patch\s+notes?|"
    r"(?:settings?|difficulty|results?)\s+announcement",
    re.I,
)

# Gallery/link captions (strength 2), checked in order; the first match wins for one caption.
CAPTION_ROLES = (
    (INFORMATIONAL_CAPTION, "reference"),
    (re.compile(r"background\s+parts?\b", re.I), None),  # "Background Parts": part or background, undecidable
    (re.compile(r"\bgameplay\b|screen\s*shot|\bin-?game\b|result screen", re.I), "gameplay_screenshot"),
    # Numbered scenes of a song's background animation (BGA) are captures of the game during play ("Scene 1"); the
    # BGA's own art keeps its role from the rest of the caption ('Character "PUPA" from the BGA' is a character,
    # 'Background from the BGA' a background), and a collaboration/unit logo shown in it is promotional.
    (re.compile(r"\bscene\s*\d+\b", re.I), "gameplay_screenshot"),
    (re.compile(r"\blogo\b.*\bBGA\b|\bBGA\b.*\blogo\b", re.I), "promotional"),
    # Covers of separate music releases ("Cover Art from Circle 2: Light Evolved Diodes", "Single" jacket).
    (re.compile(r"\balbum\b|cover\s*art\s+from\b|\b(?:single|EP)\b[^|]*\bjacket\b", re.I), "external_release_art"),
    (re.compile(r"\bartwork\b|cover\s*art|\bjacket\b|\billustration\b", re.I), "song_art"),
    (re.compile(r"\bcharacter\b", re.I), "character"),
    (re.compile(r"\bbackground\b|\bBG\b", re.I), "background"),
    (re.compile(r"\bparts?\b", re.I), "part"),
    (
        re.compile(
            r"\bbanner\b|\bteaser\b|\bannouncement\b|\bposter\b|key\s*visual|\bevent\b|\bcollaboration\b|"
            r"\bdecision\b|\bpack(?:age)?s?\b|new\s+(?:exclusive\s+)?song|now\s+available|\breveal\b",
            re.I,
        ),
        "promotional",
    ),
    (re.compile(r"\balbum\b", re.I), "external_release_art"),
)
# A caption naming artwork for another game ("Artwork in RAVON"): the file is a shared/variant artwork of the song, so
# that game becomes a game association; the page or tab still decides the original game (classify.decide makes a file
# with an original-game context "mixed"). Release events of the BMS (and similar doujin) scene are the exception:
# "Artwork in PABAT! 2017" is the artwork of the song's own release in that event (an eyecatch with the event title
# and credits), a separate external release (external_release_art), not Dynamix artwork.
EXTERNAL_RELEASE_EVENT = re.compile(r"\b(?:PABAT|BOF|BMS)\b", re.I)
# The name after "Artwork/Cover/Jacket/Illustration in/for/from" (group "other", up to a comma, semicolon or
# parenthesis) goes to the reviewed list (OTHER_GAMES, classify.listed_games), also when it starts with "Dynamix": "for
# Dynamix x RAVON Collaboration" names RAVON, while an unlisted partner and Dynamix's own occasions ("for Dynamix's 5th
# Anniversary") name no listed game and get the curator note like every other unlisted name. Only the original game's
# own name alone, with or without a possessive and trailing sentence punctuation ("Artwork in Dynamix", "Jacket from
# Dynamix (2016)", "Artwork in Dynamix!"), is not matched: it names neither another game nor an occasion, so there is
# nothing to record or review. With a dash-separated qualifier after it, it gets no curator note either
# (DYNAMIX_QUALIFIED).
OTHER_GAME_ARTWORK = re.compile(
    r"\b(?:artwork|cover|jacket|illustration)\s+(?:in|for|from)\s+"
    r"(?!dynamix(?:['’]s?)?\s*[.!?…]*\s*(?:[,;()]|$))"
    r"(?P<other>[^\s,;()][^,;()]*)",
    re.I,
)
# The original game's own name and a qualifier after a dash with a space on each side, or a dash ending the caption
# ("Artwork in Dynamix - original", "Dynamix – 2016 version", "Dynamix — HQ"): no curator note, as for the name alone,
# while a listed game in the qualifier is still recorded ("for Dynamix - Lanota Collaboration": Lanota). A hyphenated
# or dash-joined name ("Dynamix-exclusive Event", "Dynamix–Lanota Event") is a name like any other
# (classify.page_evidence).
DYNAMIX_QUALIFIED = re.compile(r"dynamix(?:['’]s?)?\s*[.!?…]*\s+[-‐‑‒–—―]+(?:\s|$)", re.I)
# A name (group "other") that starts with "Dynamix" is never a release event of EXTERNAL_RELEASE_EVENT, as before such
# names went to the reviewed list: "for Dynamix PABAT 2017" stays the song's artwork (with the curator note), while
# "for the Dynamix BMS" and "in PABAT! 2017" are external releases (classify.page_evidence).
DYNAMIX_NAME = re.compile(r"dynamix\b", re.I)


@dataclass(frozen=True)
class OtherGame:
    """A reviewed entry of OTHER_GAMES: a game that an "Artwork in/for/from <name>" caption can name."""

    name: str  # canonical name, used in evidence text
    slug: str  # its game_associations key
    spellings: tuple[str, ...]  # accepted spellings, the canonical name included (see OTHER_GAME_NAMES)


# Other games named in captions (owner decision 2026-10-11, 09-frozen-contracts.md "Other games named in captions"):
# the name after "Artwork/Cover/Jacket/Illustration in/for/from" records another game only when it names a game of this
# reviewed list, and then that game's association (an event or anniversary of the game included: "the RAVON
# Collaboration Event", "Arcaea's 3rd Anniversary"). Any other name -- an unlisted event, holiday or game, Dynamix's own
# occasions among them ("the Halloween Event") -- records no association, so the page game and the default rights
# holder stay, and the item's evidence names it with OTHER_GAME_REVIEW for a curator. A new collaboration needs a
# reviewed entry here. The owner approved all seven entries on 2026-10-11 (RAVON, Arcaea, Lanota, Cytus, Cytus II,
# Phigros and Rotaeno); each entry says so and names the committed evidence for it, if there is any: RAVON is the only
# game that committed wiki captions name, and committed primary-source records associate Arcaea, Lanota and Rotaeno
# with Dynamix artwork. The list replaces the occasion heuristic (word lists DYNAMIX_OCCASION/OCCASION_WORDS), which
# read every name it did not know as a game.
OTHER_GAMES = (
    # Owner-approved reviewed entry (2026-10-11), also evidenced by committed captions: the galleries of six
    # original-game song pages on both wikis caption their square art "<song> Artwork in RAVON" (Black：Magnam, Jack Out
    # KIllER, MassacrE, Rave! and Get on it!, ReLyna, Totally bounded; data/discovery/miraheze.json and fandom.json),
    # Dynamix's RAVON collaboration.
    OtherGame("RAVON", "ravon", ("RAVON",)),
    # Owner-approved reviewed entry (2026-10-11). No committed caption names it; the primary seeds associate it with
    # Dynamix artwork: Party Vinyl's square art, in Dynamix's Arcaea collaboration pack
    # (scripts/primary/seeds/archives.json, game_associations dynamix and arcaea).
    OtherGame("Arcaea", "arcaea", ("Arcaea",)),
    # Owner-approved reviewed entry (2026-10-11). No committed caption or game association names it (committed records
    # name Cytus only in excluded fan-video notes and a press-roundup capture excluded as not Dynamix).
    OtherGame("Cytus", "cytus", ("Cytus",)),
    # Owner-approved reviewed entry (2026-10-11). No committed record names it. Its spelling starts with Cytus's, so
    # the longer one is read first (see OTHER_GAME_NAMES).
    OtherGame("Cytus II", "cytus_ii", ("Cytus II",)),
    # Owner-approved reviewed entry (2026-10-11). No committed caption names it; the primary seeds associate it with
    # Dynamix artwork: GIFT's SoundCloud art (scripts/primary/seeds/artists.json, game_associations dynamix and lanota).
    OtherGame("Lanota", "lanota", ("Lanota",)),
    # Owner-approved reviewed entry (2026-10-11). No committed caption or game association names it (committed records
    # name Phigros only in the titles and notes of excluded fan videos on Fandom).
    OtherGame("Phigros", "phigros", ("Phigros",)),
    # Owner-approved reviewed entry (2026-10-11). No committed caption names it; the primary seeds associate it with
    # Dynamix artwork: C4Cat's "Dynamix X Rotaeno Collaboration Announcement" key visual
    # (scripts/primary/seeds/archives.json, game_associations dynamix and rotaeno).
    OtherGame("Rotaeno", "rotaeno", ("Rotaeno",)),
)
# A spelling matches as whole words in any letter case, its words separated by any whitespace, the longest spelling
# first ("Cytus II" before "Cytus"); a possessive after it ("Arcaea's", "Arcaea’s", "Phigros'") is not part of the
# match, so it names the game, while a longer word ("Arcaeana", "Cytus2") does not. Each spelling is a named group
# (OTHER_GAME_GROUPS gives its game).
_OTHER_GAME_ALTERNATIVES = sorted(
    ((spelling, game) for game in OTHER_GAMES for spelling in game.spellings),
    key=lambda pair: (-len(pair[0]), pair[0]),
)
OTHER_GAME_NAMES = re.compile(
    r"(?<!\w)(?:"
    + "|".join(
        f"(?P<g{index}>" + r"\s+".join(re.escape(word) for word in spelling.split()) + ")"
        for index, (spelling, _game) in enumerate(_OTHER_GAME_ALTERNATIVES)
    )
    + r")(?!\w)",
    re.I,
)
OTHER_GAME_GROUPS = {f"g{index}": game for index, (_spelling, game) in enumerate(_OTHER_GAME_ALTERNATIVES)}
OTHER_GAME_REVIEW = (
    "which is not on the reviewed game list (scripts/wikis/config.py OTHER_GAMES), so no game association is "
    "recorded (curator review)"
)
# Observed layout of all song pages with both Template:Song and Template:SongUV (2026-10-10, 20 pages on Miraheze):
# each game's template and notes sit in its own tab, and the page-level gallery after the tabs lists the original
# game's event items (characters, parts, backgrounds, chart lists, comeback announcements). A page-level gallery
# role on such a page is therefore original-game evidence unless its caption names Universe.
MIXED_PAGE_GALLERY_ROLES = {"character", "part", "background", "promotional", "gameplay_screenshot", "reference"}
# Song-page sections whose images are gameplay captures or trivia rather than artwork, unless a caption says
# otherwise: e.g. (XD) shows "(XD) D.jpg" in Trivia after "slide notes forming an X and D".
TRIVIA_SECTIONS = re.compile(r"^(?:trivia|notes?|difficulty changelog|charts?)$", re.I)

# "Package <name>" captions on Category:Song Package pages name the package's own cover image (strength 3).
PACKAGE_CAPTION = re.compile(r"^\s*package\b", re.I)

# Filename conventions (strength 0): never used against usage/template evidence, only to fill a missing role and
# always named in the evidence. "-HQ"/"-Raw" say nothing about being a full illustration.
FILENAME_ROLES = (
    # Wiki theme files of the Fandom/Miraheze skins (no extension on Fandom), logos and favicons.
    (
        re.compile(
            r"^(?:Site-(?:background|logo|favicon)|Wiki-(?:background|wordmark)|Community-header-background|"
            r"Favicon|Wiki\.png$)",
            re.I,
        ),
        "site_ui",
    ),
    (
        re.compile(
            r"chart[ _]*list|(?:^|[ _])(?:comeback|new[ _]charts?)(?:[ _.(]|$)|version[ _]update|"
            r"update[ _]log",
            re.I,
        ),
        "reference",
    ),
    (re.compile(r"Dynamix\.(?:png|jpe?g)$"), "song_art"),
    (re.compile(r"[ _-]?BG(?:[ _-]?HQ)?\.(?:png|jpe?g)$", re.I), "background"),
    (re.compile(r"Parts?(?:[ _-]?HQ)?\.(?:png|jpe?g)$", re.I), "part"),
    (re.compile(r"-(?:Raw|Still|Animated|Effect)\.(?:png|gif|jpe?g)$", re.I), "character"),
    (re.compile(r"(?:^|[ _])cover\.(?:png|jpe?g)$", re.I), "song_art"),
)

# Song categories that name collections (pack/event membership) on the English wikis.
COLLECTION_CATEGORIES = {
    "Category:Event songs": "Event",
    "Category:IAPs": "Song packs (IAP)",
    "Category:Free songs": "Free",
    "Category:Premium Songs": "Premium",
    "Category:Collaboration Songs": "Collaboration",
    "Category:Wave Test Songs": "Wave Test",
    "Category:Special songs": "Special",
}
REMOVED_CATEGORIES = re.compile(r"^Category:(?:Removed|Deleted|Delisted)\b", re.I)
