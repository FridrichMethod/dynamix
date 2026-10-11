"""Small, dependency-free wikitext readers for the Dynamix wikis (MediaWiki markup is untrusted data).

Only what classification needs is parsed: comments (masked, so offsets stay valid), templates and their parameters,
game tabs (``<div class="tabberex-tab" data-tab-header=...>`` and ``<tabber>``), section headings, and file references
in galleries, ``[[File:...]]`` links and template parameters. Nothing here renders or follows links.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote

IMAGE_EXTENSIONS = ("png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "tif", "tiff")
_FILE_VALUE = re.compile(r"^[^\n|{}\[\]<>]+\.(?:" + "|".join(IMAGE_EXTENSIONS) + r")$", re.IGNORECASE)
_FILE_PREFIX = re.compile(r"^\s*(?:file|image|media)\s*:\s*", re.IGNORECASE)
_PERCENT = re.compile(r"%[0-9A-Fa-f]{2}")
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
_HEADING = re.compile(r"^(={1,6})(.+?)\1[ \t]*$", re.MULTILINE)
_GALLERY = re.compile(r"<gallery\b([^>]*)>(.*?)</gallery\s*>", re.IGNORECASE | re.DOTALL)
_LINK_START = re.compile(r"\[\[\s*(?:file|image|media)\s*:", re.IGNORECASE)
_TABBEREX_OPEN = re.compile(r"<div\b[^>]*class\s*=\s*[\"'][^\"']*\btabberex-tab\b[^\"']*[\"'][^>]*>", re.IGNORECASE)
_TAB_HEADER = re.compile(r"data-tab-header\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)
_DIV_TOKEN = re.compile(r"<div\b[^>]*>|</div\s*>", re.IGNORECASE)
_TABBER = re.compile(r"<tabber\b[^>]*>(.*?)</tabber\s*>", re.IGNORECASE | re.DOTALL)
_REF = re.compile(r"<ref\b[^>/]*/>|<ref\b[^>]*>.*?</ref\s*>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^<>]+>")
_NOWIKI_OPEN = re.compile(r"<nowiki\s*>", re.IGNORECASE)
_NOWIKI_CLOSE = re.compile(r"</nowiki\s*>", re.IGNORECASE)
_NOWIKI = re.compile(r"<nowiki\s*>(.*?)(?:</nowiki\s*>|\Z)|<nowiki\s*/>", re.IGNORECASE | re.DOTALL)
_LITERAL = re.compile("\ue000(\\d+)\ue001")
# Whitespace runs that collapse to one space inside a line: ASCII only. Other spaces are part of the text (the
# ideographic space U+3000 in the exact title "MEGA\u3000POP").
_ASCII_SPACES = re.compile(r"[ \t\f\v\r]+")
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
# As in MediaWiki's parser, a link label ends at the first "]]" and may hold a lone "[" or "]" (never "[["): the
# label of [[Nyarlathotep|^/7(L⎥?[_(L+#<>+&⎥^(o)]] is shown verbatim, and [[Artists#.5Bsound_shinobi.5D|[sound
# shinobi]]] shows "[sound shinobi]" (MediaWiki moves the third "]" into a label holding "["; as plain text the result
# is the same).
_WIKILINK = re.compile(r"\[\[([^\[\]|]*)(?:\|((?:(?!\[\[).)*?))?\]\]", re.DOTALL)
_EXTLINK = re.compile(r"\[(?:https?:)?//[^\s\]]+(?:\s+([^\]]*))?\]")
_QUOTES = re.compile(r"'{2,}")
_LINK_OPTION = re.compile(
    r"^(?:\d*x?\d+\s*px|thumb|thumbnail|frame|framed|frameless|border|left|right|center|centre|none|baseline|sub|"
    r"super|top|text-top|middle|bottom|text-bottom|upright(?:\s*=.*)?|(?:link|alt|page|class|lang|linktext|"
    r"thumbtime|start|end)\s*=.*)$", re.IGNORECASE | re.DOTALL)
UNKNOWN_CREDITS = frozenset({"", "-", "–", "—", "?", "??", "???", "n/a", "na", "none", "unknown", "tba", "tbd",
                             "not credited", "uncredited"})


@dataclass(frozen=True)
class Template:
    name: str  # canonical: underscores as spaces, first letter upper-case
    params: dict[str, str]  # named and positional ("1", "2", ...) parameters, raw values stripped
    start: int
    end: int


@dataclass(frozen=True)
class Region:
    label: str
    start: int
    end: int


@dataclass(frozen=True)
class FileRef:
    name: str  # canonical file title without the namespace, e.g. "Cosmic Pulse Chart List.jpg"
    raw: str  # as written
    context: str  # "gallery", "link" or "template"
    offset: int
    caption: str = ""  # cleaned caption text ("" when none)
    link: str | None = None  # link= target of a link/gallery entry
    template: str | None = None
    param: str | None = None
    gallery_options: str = field(default="", compare=False)


def mask_comments(text: str) -> str:
    """``text`` with every HTML comment replaced by spaces (newlines kept), so offsets stay comparable."""
    return _COMMENT.sub(lambda match: re.sub(r"[^\n]", " ", match.group(0)), text or "")


def canonical_file_name(raw: str | None) -> str | None:
    """MediaWiki canonical title text of a file reference (no namespace): entities and percent-escapes decoded,
    underscores as spaces, whitespace collapsed, first letter upper-case. None unless it names an image file."""
    if not raw:
        return None
    value = html.unescape(raw).strip()
    value = _FILE_PREFIX.sub("", value)
    if _PERCENT.search(value):
        value = unquote(value)
    value = " ".join(value.replace("_", " ").split())
    if not value or not _FILE_VALUE.match(value):
        return None
    return value[0].upper() + value[1:]


def canonical_page_title(raw: str) -> str:
    """Canonical page title: entities decoded, underscores as spaces, collapsed whitespace, first letter upper,
    fragment removed."""
    value = html.unescape(raw).split("#", 1)[0]
    value = " ".join(value.replace("_", " ").split())
    return value[:1].upper() + value[1:]


def _matching(text: str, start: int, opener: str, closer: str) -> int:
    """Index just past the closer matching the opener at ``start`` (``len(text)`` when unbalanced)."""
    depth, index = 0, start
    while index < len(text):
        if text.startswith(opener, index):
            depth += 1
            index += len(opener)
        elif text.startswith(closer, index):
            depth -= 1
            index += len(closer)
            if depth == 0:
                return index
        else:
            index += 1
    return len(text)


def split_top_level(text: str, separator: str = "|") -> list[str]:
    """Split on ``separator`` outside ``{{...}}``, ``[[...]]`` and ``<nowiki>...</nowiki>`` (literal text: a caption
    ``"MassacrE <nowiki>||</nowiki> 3rc4224M"`` is one part)."""
    parts, depth_t, depth_l, current, index = [], 0, 0, [], 0
    while index < len(text):
        literal = _NOWIKI_OPEN.match(text, index)
        if literal:
            closing = _NOWIKI_CLOSE.search(text, literal.end())
            end = closing.end() if closing else len(text)
            current.append(text[index:end])
            index = end
            continue
        two = text[index:index + 2]
        if two == "{{":
            depth_t += 1
            current.append(two)
            index += 2
            continue
        if two == "}}" and depth_t:
            depth_t -= 1
            current.append(two)
            index += 2
            continue
        if two == "[[":
            depth_l += 1
            current.append(two)
            index += 2
            continue
        if two == "]]" and depth_l:
            depth_l -= 1
            current.append(two)
            index += 2
            continue
        char = text[index]
        if char == separator and not depth_t and not depth_l:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current))
    return parts


def _template_name(raw: str) -> str:
    name = " ".join(raw.replace("_", " ").split())
    name = re.sub(r"^template\s*:\s*", "", name, flags=re.IGNORECASE)
    return name[:1].upper() + name[1:]


def find_templates(text: str, names: set[str] | None = None) -> list[Template]:
    """Every ``{{Name|...}}`` (nested ones included) in comment-masked ``text``; ``names`` filters canonical names.
    Parser functions (``{{#if:``) and magic words (``{{PAGENAME}}``-like, containing ``:``) are skipped."""
    masked = mask_comments(text)
    result = []
    for match in re.finditer(r"\{\{(?!\{)", masked):
        start = match.start()
        end = _matching(masked, start, "{{", "}}")
        inner = masked[start + 2:max(start + 2, end - 2)]
        pieces = split_top_level(inner)
        name = _template_name(pieces[0])
        if not name or name.startswith("#") or ":" in name:
            continue
        if names is not None and name not in names:
            continue
        params: dict[str, str] = {}
        position = 0
        for piece in pieces[1:]:
            key, equals, value = piece.partition("=")
            if equals and "{{" not in key and "[[" not in key and key.strip():
                params[" ".join(key.split())] = value.strip()
            else:
                position += 1
                params[str(position)] = piece.strip()
        result.append(Template(name, params, start, end))
    return result


def regions(text: str) -> list[Region]:
    """Game tabs: ``<div class="tabberex-tab" data-tab-header="...">`` blocks and ``<tabber>`` tabs."""
    masked = mask_comments(text)
    found = []
    openings = list(_TABBEREX_OPEN.finditer(masked))
    for number, match in enumerate(openings):
        header = _TAB_HEADER.search(match.group(0))
        depth, end = 0, len(masked)
        for token in _DIV_TOKEN.finditer(masked, match.start()):
            depth += -1 if token.group(0).startswith("</") else 1
            if depth == 0:
                end = token.end()
                break
        # Tabs are siblings: an unclosed <div> inside a tab (seen in template parameters) must not make the next
        # tab look nested in this one, so a tab ends at the next tab opening at the latest.
        if number + 1 < len(openings):
            end = min(end, openings[number + 1].start())
        found.append(Region(html.unescape(header.group(1)).strip() if header else "", match.start(), end))
    for match in _TABBER.finditer(masked):
        body_start = match.start(1)
        offset = body_start
        for piece in re.split(r"(\|-\|)", match.group(1)):
            if piece == "|-|":
                offset += len(piece)
                continue
            label, equals, _ = piece.partition("=")
            if equals and "\n" not in label.strip():
                found.append(Region(label.strip(), offset, offset + len(piece)))
            offset += len(piece)
    return sorted(found, key=lambda region: (region.start, region.end))


def region_game(label: str) -> str | None:
    """Game named by a tab label: ``dynamix_universe`` for Universe/UV tabs, ``dynamix`` for a Dynamix tab."""
    value = label.casefold()
    if "universe" in value or re.search(r"\buv\b", value):
        return "dynamix_universe"
    if "dynamix" in value:
        return "dynamix"
    return None


def regions_at(found: list[Region], offset: int) -> list[Region]:
    return [region for region in found if region.start <= offset < region.end]


def headings(text: str) -> list[tuple[int, int, str]]:
    """``(offset, level, clean title)`` of every section heading in comment-masked text."""
    masked = mask_comments(text)
    return [(match.start(), len(match.group(1)), clean_markup(match.group(2)).strip())
            for match in _HEADING.finditer(masked)]


def section_path(found: list[tuple[int, int, str]], offset: int, *, since: int = 0) -> list[str]:
    """Heading titles enclosing ``offset`` from outermost to innermost; headings before ``since`` (e.g. the start of
    the game tab holding ``offset``) are ignored."""
    stack: list[tuple[int, str]] = []
    for start, level, title in found:
        if start > offset:
            break
        if start < since:
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
    return [title for _, title in stack]


def clean_markup(value: str | None) -> str:
    """Plain text of a short wikitext value (captions, credits): references, comments, tags and templates removed,
    links reduced to their label, ``<br>`` as newlines, entities decoded. ``<nowiki>`` content is kept literally, and
    only ASCII whitespace runs collapse (exact titles keep characters such as the ideographic space)."""
    if not value:
        return ""
    text = _COMMENT.sub("", value)
    literals: list[str] = []

    def hold(match: re.Match) -> str:
        literals.append(match.group(1) or "")
        return f"\ue000{len(literals) - 1}\ue001"
    text = _NOWIKI.sub(hold, text)
    text = _REF.sub("", text)
    text = _BR.sub("\n", text)
    for _ in range(5):  # innermost templates first; {{color|#hex|Text}} keeps its text
        replaced = re.sub(r"\{\{\s*(?:color|colour|clr)\s*\|[^{}|]*\|([^{}]*)\}\}", r"\1", text, flags=re.IGNORECASE)
        replaced = re.sub(r"\{\{[^{}]*\}\}", "", replaced)
        if replaced == text:
            break
        text = replaced
    text = _WIKILINK.sub(lambda match: match.group(2) if match.group(2) is not None else match.group(1), text)
    text = _EXTLINK.sub(lambda match: match.group(1) or "", text)
    text = _TAG.sub("", text)
    text = _QUOTES.sub("", text)
    text = _LITERAL.sub(lambda match: literals[int(match.group(1))], text)
    text = html.unescape(text)
    lines = [_ASCII_SPACES.sub(" ", line).strip(" \t\f\v\r") for line in re.split(r"\r?\n", text)]
    return "\n".join(line for line in lines if line)


# An illustrator note inside a composer credit: "Linne X SCTL(Illust.Keiyuu&RED-FOX)", "Street (jacket : Sin:cK)".
ILLUSTRATOR_NOTE = re.compile(r"\s*[(（]\s*(?:illust(?:ration|rated)?\.?(?:\s*by)?|jacket|cover(?:\s*art)?)\s*[.:：]?\s*"
                              r"[^()（）]*[)）]", re.IGNORECASE)


def credit_names(value: str | None, *, strip_illustrator_notes: bool = False) -> list[str]:
    """Credited names of a template field, verbatim after markup removal: one entry per ``<br>``/line, unknown
    placeholders (``N/A``, ``-``, ``?``...) dropped. Names are never split on ``&``/``,``/``x`` (that would guess).
    ``strip_illustrator_notes`` (composer fields) removes an explicitly labeled illustrator note such as
    ``(Illust.Keiyuu&RED-FOX)``: that is the illustrator's credit, which the template states separately."""
    names = []
    for line in clean_markup(value).splitlines():
        if strip_illustrator_notes:
            line = ILLUSTRATOR_NOTE.sub("", line)
        name = line.strip().strip(",;").strip()
        if name.casefold() in UNKNOWN_CREDITS:
            continue
        if name not in names:
            names.append(name)
    return names


def _caption_and_link(parts: list[str]) -> tuple[str, str | None]:
    caption, link = "", None
    for part in parts:
        stripped = part.strip()
        key, equals, value = stripped.partition("=")
        if equals and key.strip().casefold() == "link":
            link = value.strip() or None
            continue
        if _LINK_OPTION.match(stripped):
            continue
        caption = stripped  # MediaWiki uses the last unrecognized part as the caption
    return clean_markup(caption), link


def file_refs(text: str) -> list[FileRef]:
    """File references in galleries, ``[[File:]]``/``[[Image:]]``/``[[Media:]]`` links and template parameters whose
    value is a bare file name. Offsets refer to the original text."""
    masked = mask_comments(text)
    refs: list[FileRef] = []
    gallery_spans = []
    for match in _GALLERY.finditer(masked):
        gallery_spans.append((match.start(), match.end()))
        body_offset = match.start(2)
        for line_match in re.finditer(r"[^\n]+", match.group(2)):
            line = line_match.group(0).strip()
            if not line:
                continue
            parts = split_top_level(line)
            name = canonical_file_name(parts[0])
            if name is None:
                continue
            caption, link = _caption_and_link(parts[1:])
            refs.append(FileRef(name, parts[0].strip(), "gallery", body_offset + line_match.start(), caption, link,
                                gallery_options=match.group(1).strip()))
    for match in _LINK_START.finditer(masked):
        if any(start <= match.start() < end for start, end in gallery_spans):
            continue
        end = _matching(masked, match.start(), "[[", "]]")
        parts = split_top_level(masked[match.start() + 2:max(match.start() + 2, end - 2)])
        name = canonical_file_name(parts[0])
        if name is None:
            continue
        caption, link = _caption_and_link(parts[1:])
        refs.append(FileRef(name, parts[0].strip(), "link", match.start(), caption, link))
    for template in find_templates(text):
        for key, value in template.params.items():
            candidate = value.strip()
            if "\n" in candidate or not _FILE_VALUE.match(html.unescape(candidate) if candidate else ""):
                continue
            name = canonical_file_name(candidate)
            if name is not None:
                refs.append(FileRef(name, candidate, "template", template.start, "", None, template.name, key))
    return sorted(refs, key=lambda ref: (ref.offset, ref.context, ref.name))


def normalize_key(value: str) -> str:
    """Loose comparison key (NFKC, casefold, alphanumerics only) used to match titles across sources; never stored
    as a display value. Spacing modifier letters (U+02B0-U+02FF, e.g. ``ˍ`` standing in for ``_``) count as
    punctuation, not letters."""
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold()
                   if char.isalnum() and not 0x02B0 <= ord(char) <= 0x02FF)


# Upside-down ("turned") spellings: each turned letter and the letter it reads as when the text is rotated 180°.
_TURNED = dict(zip("ɐqɔpǝɟƃɥᴉıɾʞןɯuodbɹsʇnʌʍxʎz∀ꓭƆᗡƎℲ⅁ſꓘ˥WԀᴚ⊥∩ΛMX⅄",
                   "abcdefghiijklmnopqrstuvwxyzABCDEFGJKLMPRTUVWXY"))
_TURNED_ONLY = set("ɐɔǝɟƃɥᴉɾʞɯɹʇʌʍʎ∀ꓭƆᗡƎℲ⅁ꓘ˥ԀᴚΛ⅄")  # characters that occur only in turned text


def unturned(value: str) -> str | None:
    """The plain reading of an upside-down title (``ǝɹǝɥdsᴉɯǝH`` -> ``Hemisphere``): turned letters mapped back and
    the order reversed; None unless at least two characters occur only in turned text."""
    if sum(1 for char in value if char in _TURNED_ONLY) < 2:
        return None
    return "".join(_TURNED.get(char, char) for char in reversed(value))
