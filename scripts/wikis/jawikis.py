"""Japanese wiki song lists (metadata only): atwiki ``dynamix2ch`` list pages and wikiwiki ``rhythmgame/Dynamix``.

Pages are fetched politely (one request per page, the shared client's spacing) and parsed as untrusted HTML with
BeautifulSoup. Only innermost tables are read; a header row naming a title column (``Title``/``曲名``/``楽曲``) and a
credit column (``Artist``/``Composer``/``作曲者``/``名義``) locates the fields, with ``colspan`` expanded. Rows are kept
verbatim (whitespace collapsed). Blocked or failed pages are recorded as errors, never as an empty song list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup

from lib import net
from lib.checks import redact_text

from . import config

TITLE_HEADERS = re.compile(r"^(?:title|曲名|楽曲名?|タイトル)$", re.IGNORECASE)
CREDIT_HEADERS = re.compile(r"^(?:artist|composer|作曲者?|名義|アーティスト)$", re.IGNORECASE)
SKIP_TITLES = re.compile(r"^(?:title|曲名|楽曲名?|casual|level|\+|-|)$", re.IGNORECASE)
# Footnote markers appended to titles on atwiki lists ("Xetta ( *4 )", "春菊（*1）"); notes, not part of the title.
FOOTNOTE = re.compile(r"\s*[(（]\s*[*※]\s*\d+\s*[)）]\s*$")


@dataclass
class SongRow:
    title: str
    credit: str
    list_label: str
    url: str


@dataclass
class ListPage:
    label: str
    url: str
    rows: list[SongRow] = field(default_factory=list)
    error: dict | None = None
    final_url: str | None = None
    last_modified: str | None = None


def _text(cell: Any) -> str:
    return " ".join(cell.get_text(" ", strip=True).split())


def _expanded(cells: list[Any]) -> list[str]:
    values = []
    for cell in cells:
        try:
            span = max(1, min(20, int(cell.get("colspan", 1))))
        except TypeError, ValueError:
            span = 1
        values.extend([_text(cell)] * span)
    return values


def parse_tables(
    html: str, label: str, url: str, *, container_ids: tuple[str, ...] = ("wikibody", "body")
) -> list[SongRow]:
    """Song rows from every innermost table with a recognizable title header."""
    soup = BeautifulSoup(html, "html.parser")
    root = None
    for container in container_ids:
        root = soup.find(id=container)
        if root is not None:
            break
    root = root or soup
    rows: list[SongRow] = []
    seen: set[tuple[str, str]] = set()
    for table in root.find_all("table"):
        if table.find("table") is not None:
            continue  # an outer layout table; its inner tables are read on their own
        title_index = credit_index = None
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            expanded = _expanded(cells)
            header_titles = [i for i, value in enumerate(expanded) if TITLE_HEADERS.match(value)]
            if header_titles:
                title_index = header_titles[0]
                credits = [i for i, value in enumerate(expanded) if CREDIT_HEADERS.match(value)]
                credit_index = credits[0] if credits else None
                continue
            if title_index is None:
                continue
            values = [_text(cell) for cell in cells]  # data rows: one value per cell
            if title_index >= len(values):
                continue
            title = FOOTNOTE.sub("", values[title_index]).strip()
            if SKIP_TITLES.match(title):
                continue
            credit = values[credit_index] if credit_index is not None and credit_index < len(values) else ""
            if (title, credit) in seen:
                continue
            seen.add((title, credit))
            rows.append(SongRow(title, credit, label, url))
    return rows


def _last_modified(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find(id="lastmodified")
    if node is not None:
        return " ".join(node.get_text(" ", strip=True).split())[:80]
    match = re.search(r"最終更新[：:]\s*([0-9]{4}年[0-9]{2}月[0-9]{2}日\s*[0-9:]*)", soup.get_text(" ", strip=True))
    return match.group(1) if match else None


def fetch_lists(client: Any, source: config.WikiSource) -> list[ListPage]:
    """Fetch and parse every registered list page of an HTML wiki source."""
    pages = []
    for label, url in source.list_pages:
        page = ListPage(label, url)
        try:
            text, final_url, _headers = client.get_text(url)
        except net.FetchError as error:
            page.error = {
                "phase": "discover",
                "url": url,
                "message": redact_text(f"{label}: {error.error_class}: {error}")[:500],
            }
            pages.append(page)
            continue
        page.final_url = final_url
        page.last_modified = _last_modified(text)
        page.rows = parse_tables(text, label, url)
        if not page.rows:
            page.error = {"phase": "discover", "url": url, "message": f"{label}: no song rows recognized"}
        pages.append(page)
    return pages
