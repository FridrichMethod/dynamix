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
    key="miraheze", source_id="wikis:miraheze", name="Dynamix Wiki (Miraheze)",
    url="https://dynamix.miraheze.org/wiki/Main_Page", platform="mediawiki",
    hosts=("dynamix.miraheze.org", "static.wikitide.net", "static.miraheze.org"),
    api_url="https://dynamix.miraheze.org/w/api.php", article_base="https://dynamix.miraheze.org/wiki/",
    notes="Current English community wiki (moved from Fandom in 2026; most files were imported by a maintenance "
          "script on 2026-08-09, so their upload timestamps are import times). Files are served unmodified from "
          "static.wikitide.net; the API SHA-1 equals the served bytes for sampled files.",
)
FANDOM = WikiSource(
    key="fandom", source_id="wikis:fandom", name="Dynamix Wiki (Fandom, legacy)",
    url="https://dynamixc4cat.fandom.com/wiki/Main_Page", platform="mediawiki",
    hosts=("dynamixc4cat.fandom.com", "static.wikia.nocookie.net"),
    api_url="https://dynamixc4cat.fandom.com/api.php", article_base="https://dynamixc4cat.fandom.com/wiki/",
    download_query="format=original",
    notes="Legacy English wiki (dynamixc4cat.fandom.com; dynamix.fandom.com is an unrelated game). Its CDN serves a "
          "transformed WEBP for /revision/latest unless format=original is requested; files are downloaded with "
          "format=original. The API size/SHA-1 of many files differ from the bytes format=original returns; that is "
          "recorded as source_reported versus download_observed, not as a second version. YouTube entries "
          "(video/youtube) are placeholders, not files.",
)
ATWIKI = WikiSource(
    key="atwiki-ja", source_id="wikis:atwiki-ja", name="Dynamix wiki (atwiki, Japanese)",
    url="https://w.atwiki.jp/dynamix2ch/", platform="atwiki", hosts=("w.atwiki.jp",),
    notes="Japanese wiki of the original game (\"無印Dynamix\"); read for the original-game song list only. "
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
    key="rhythmgame-ja", source_id="wikis:rhythmgame-ja", name="Rhythm game wiki (wikiwiki.jp): Dynamix",
    url="https://wikiwiki.jp/rhythmgame/Dynamix", platform="wikiwiki", hosts=("wikiwiki.jp",),
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
    "Characters", "Parts", "Event Bundles", "Festival Events", "Event Setting", "Code", "Modes", "Mods", "Combo",
    "Special & 2016 Event", "Wave Test Setting", "Charters",
} | {f"{year} Event" for year in range(2016, 2026)}
ORIGINAL_GAME_PREFIXES = ("Charters/",)
# Pages that discuss both games or none (their usage alone gives no game): artist/charter lists, wiki navigation.
# "Store" and "Grading" are tab indexes transcluding both games' subpages.
NEUTRAL_PAGES = {"Artists", "Main Page", "Dynamix Wiki", "Songs", "Update History", "How to Play", "Main Menu",
                 "Start", "Dynamix", "Store", "Grading"}

# Media roles of reviewed list pages (role, strength 2). Section-specific roles are in SECTION_ROLES.
PAGE_ROLES = {
    "Characters": "character",
    "Artists": "person_photo", "Artists (Legacy)": "person_photo", "Charters": "person_photo",
    "Google Play Achievements": "game_ui", "Grading/Dynamix": "game_ui", "Grading/Dynamix Universe": "game_ui",
}
PAGE_ROLE_PREFIXES = (("Charters/", "person_photo"),)
# Game-mechanics/UI pages: small images are interface elements (game_ui), large ones screenshots of the game.
UI_PAGES = {"Wave Test Setting", "Main Menu/Dynamix", "Main Menu/Dynamix Universe", "How to Play/Dynamix",
            "How to Play/Dynamix Universe", "Start/Dynamix", "Start/Dynamix Universe", "Code", "Event Setting",
            "Modes", "Mods", "Combo", "Wave Test"}
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
)
# Wiki chrome: templates and pages that decorate the wiki itself.
SITE_UI_PAGES = {"Main Page", "Dynamix Wiki"}
SITE_UI_TEMPLATE_RE = re.compile(r"^Template:(?:Main ?Page|MainPage|Navbox|Dynamix|Tnavbar|Headercell|"
                                 r"UnderConstruction|Moving|Color|Clr)", re.I)

# File description page categories (strength 2).
FILE_CATEGORY_ROLES = {
    "Category:Character images": "character", "Category:Background parts images": "background",
    "Category:Parts images": "part", "Category:Chart list images": "promotional",
    "Category:Event images": "promotional", "Category:Artist profile images": "person_photo",
    "Category:Cover Art": "song_art", "Category:Videos": "video_placeholder",
}

# Gallery/link captions (strength 2), checked in order; the first match wins for one caption.
CAPTION_ROLES = (
    (re.compile(r"chart\s*list|new\s+charts?\b|comeback", re.I), "promotional"),
    (re.compile(r"background\s+parts?\b", re.I), None),  # "Background Parts": part or background, undecidable
    (re.compile(r"\bgameplay\b|screen\s*shot|\bin-?game\b|result screen", re.I), "gameplay_screenshot"),
    # Background-animation (BGA) scenes, logos and assets: in-game material, kept as references (archive tier).
    (re.compile(r"\bBGA\b|\bscene\b", re.I), "reference"),
    # Covers of separate music releases ("Cover Art from Circle 2: Light Evolved Diodes", "Single" jacket).
    (re.compile(r"\balbum\b|cover\s*art\s+from\b|\b(?:single|EP)\b[^|]*\bjacket\b", re.I), "external_release_art"),
    (re.compile(r"\bartwork\b|cover\s*art|\bjacket\b|\billustration\b", re.I), "song_art"),
    (re.compile(r"\bcharacter\b", re.I), "character"),
    (re.compile(r"\bbackground\b|\bBG\b", re.I), "background"),
    (re.compile(r"\bparts?\b", re.I), "part"),
    (re.compile(r"\bbanner\b|\bteaser\b|\bannouncement\b|\bposter\b|key\s*visual|\bevent\b|version\s+update|"
                r"update\s+log|\bcollaboration\b|\bdecision\b|\bpack(?:age)?s?\b|new\s+(?:exclusive\s+)?song|"
                r"now\s+available|\breveal\b", re.I), "promotional"),
    (re.compile(r"\balbum\b", re.I), "external_release_art"),
)
# A caption naming artwork for another game or event ("Artwork in RAVON", "Artwork in PABAT! 2017"): the file is a
# shared/variant artwork of the song, so its game association is "mixed".
OTHER_GAME_ARTWORK = re.compile(r"\b(?:artwork|cover|jacket|illustration)\s+(?:in|for|from)\s+(?!dynamix\b)"
                                r"(?P<other>[^\s,;()][^,;()]*)", re.I)
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
    (re.compile(r"^(?:Site-(?:background|logo|favicon)|Wiki-(?:background|wordmark)|Community-header-background|"
                r"Favicon|Wiki\.png$)", re.I), "site_ui"),
    (re.compile(r"chart[ _]*list", re.I), "promotional"),
    (re.compile(r"Dynamix\.(?:png|jpe?g)$"), "song_art"),
    (re.compile(r"[ _-]?BG(?:[ _-]?HQ)?\.(?:png|jpe?g)$", re.I), "background"),
    (re.compile(r"Parts?(?:[ _-]?HQ)?\.(?:png|jpe?g)$", re.I), "part"),
    (re.compile(r"-(?:Raw|Still|Animated|Effect)\.(?:png|gif|jpe?g)$", re.I), "character"),
    (re.compile(r"(?:^|[ _])cover\.(?:png|jpe?g)$", re.I), "song_art"),
)

# Song categories that name collections (pack/event membership) on the English wikis.
COLLECTION_CATEGORIES = {
    "Category:Event songs": "Event", "Category:IAPs": "Song packs (IAP)", "Category:Free songs": "Free",
    "Category:Premium Songs": "Premium", "Category:Collaboration Songs": "Collaboration",
    "Category:Wave Test Songs": "Wave Test", "Category:Special songs": "Special",
}
REMOVED_CATEGORIES = re.compile(r"^Category:(?:Removed|Deleted|Delisted)\b", re.I)
