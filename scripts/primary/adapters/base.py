"""Helpers shared by the per-platform adapters."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

from bs4 import BeautifulSoup

from lib import schema

from .. import model


def require_items(seed: Mapping[str, Any], where: str, fields: Iterable[str]) -> list[dict]:
    items = seed.get("items")
    if not isinstance(items, list):
        raise ValueError(f"{where}: items must be a list")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{where}: items[{index}] must be an object")
        for name in fields:
            if not isinstance(item.get(name), str) or not item[name].strip():
                raise ValueError(f"{where}: items[{index}].{name} is required")
    return items


def new_discovery(
    seed: Mapping[str, Any],
    *,
    lists: list[str],
    api: str | None = None,
    evidence_level: str = "page_read",
    scope_notes: str = "",
) -> model.Discovery:
    """A discovery whose ``lists`` are complete once processed; the seed's ``unenumerated_lists`` stay open."""
    continuation = {name: None for name in lists}
    unenumerated = seed.get("unenumerated_lists", {})
    continuation.update({name: str(state) for name, state in unenumerated.items()})
    crawl = seed.get("crawl_scope")
    notes = scope_notes or (crawl.get("notes", "") if isinstance(crawl, dict) else "")
    return model.Discovery(
        lists=[*lists, *unenumerated],
        continuation=continuation,
        api=api,
        evidence_level=evidence_level,
        scope_notes=notes,
    )


def mark_unread(discovery: model.Discovery, list_name: str, error: Any) -> None:
    """Keep ``list_name`` open after a failed read (07: never infer completeness from an error or empty response).

    The token names the failure class; an earlier open token of the list (a resume key, a sample size) is kept."""
    if discovery.continuation.get(list_name) is None:
        discovery.continuation[list_name] = f"not-read:{getattr(error, 'error_class', None) or type(error).__name__}"


PAGE_KEY_PREFIX = "page:"  # key of a seed page whose read failed (:func:`page_key`)


def listed_on(pages: set[str]) -> Any:
    """Coverage predicate (``Discovery.coverage``): a previous candidate is covered by a list when its page is one of
    the ``pages`` this run read completely (a page-read failure candidate ``page:<url>`` never is)."""

    def covers(candidate: Mapping[str, Any]) -> bool:
        return candidate.get("page_url") in pages and not str(candidate.get("upstream_key", "")).startswith(
            PAGE_KEY_PREFIX
        )

    return covers


def held_on(pages: Iterable[str], complete: set[str]) -> Any:
    """Hold predicate (``Discovery.holds``): a previous candidate is held by a list while its page is one of the list's
    seeded ``pages`` that this run did not read completely (``complete``, filled during discovery: the pages whose
    :func:`listed_on` coverage applies). A candidate of a page the seeds no longer name is not held."""
    seeded = set(pages)

    def holds(candidate: Mapping[str, Any]) -> bool:
        page = candidate.get("page_url")
        return page in seeded and page not in complete

    return holds


def page_key(url: str) -> str:
    """Candidate key of a seed page whose read failed (one per page, so several failed pages never collide)."""
    return f"{PAGE_KEY_PREFIX}{url}"


def rules(seed: Mapping[str, Any]) -> list[dict]:
    return list(seed.get("rules", []))


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def page_text(document: BeautifulSoup) -> str:
    for element in document(["script", "style", "noscript"]):
        element.extract()
    return " ".join(document.get_text(" ").split())


def headline(document: BeautifulSoup) -> str:
    """A page's headline for record titles: its ``h1`` when that reads as a headline (at least 16 characters), else
    ``og:title``, else the document title, else the short ``h1`` (a gallery page's ``h1`` is often just the game name)."""
    h1 = document.find("h1")
    text = " ".join(h1.get_text(" ").split()) if h1 else ""
    if len(text) >= 16:
        return text
    return (
        meta_content(document, "og:title")
        or (" ".join(document.title.get_text(" ").split()) if document.title else "")
        or text
    )


def json_ld(document: BeautifulSoup) -> list[dict]:
    """Every JSON-LD object on the page (invalid blocks are skipped)."""
    found = []
    for script in document.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict):
                found.append(item)
    return found


def meta_content(document: BeautifulSoup, name: str) -> str | None:
    tag = document.find("meta", attrs={"property": name}) or document.find("meta", attrs={"name": name})
    content = tag.get("content") if tag else None
    return content.strip() if isinstance(content, str) and content.strip() else None


def absolute(base: str, link: str) -> str:
    return urljoin(base, link.strip())


def document_base(document: BeautifulSoup, url: str) -> str:
    """The URL relative links of ``document`` resolve against: its first ``<base href>`` (resolved against ``url``)
    when that is an http(s) URL, else ``url``, the URL the page was served from (after redirects; HTML base URL)."""
    tag = document.find("base", href=True)
    href = str(tag.get("href") or "").strip() if tag is not None else ""
    if href:
        try:
            resolved = urljoin(url, href)
            if urlsplit(resolved).scheme in ("http", "https"):
                return resolved
        except ValueError:
            pass
    return url


def resolution_note(document: BeautifulSoup, page_url: str, final_url: str | None) -> str | None:
    """Provenance note for the candidates whose links a read page resolves against another URL than the seed
    ``page_url`` (which stays their ``page_url``): the URL it was served from after redirects (``final_url``) and/or its
    ``<base href>`` (:func:`document_base`). None when links resolve against ``page_url`` itself."""
    served = final_url or page_url
    resolved = document_base(document, served)
    shown_served, shown_resolved = stored(served), stored(resolved)
    if served != page_url and shown_served:
        against = f"its <base href> {shown_resolved}" if resolved != served and shown_resolved else "that URL"
        return (
            f"The page was served from {shown_served} after redirects from the seed URL; its links resolve against "
            f"{against}."
        )
    if resolved != page_url and shown_resolved:
        return f"The page's <base href> {shown_resolved} is the URL its links resolve against."
    return None


_LINK_DROPPED = str.maketrans("", "", "\t\n\r")  # removed from a link before parsing (WHATWG URL)
_LINK_ESCAPED = frozenset(' "<>^`{|}')  # ASCII a URL cannot carry literally (lib.checks URL_FORBIDDEN_ASCII, not \)


def link_url(base: str, link: str) -> str:
    """The absolute URL a browser requests for the page link ``link`` (``href``/``src``) against the document base
    ``base``: ASCII tab and newline removed, surrounding whitespace stripped, resolved, then every whitespace or control
    character and ``"<>^`{|}`` percent-encoded as UTF-8 (``img/New Shot.png`` -> ``img/New%20Shot.png``), as browsers and
    ``requests`` send it. Every other character stays as written, so a link that is already a valid URL resolves exactly
    as with :func:`absolute`; one that still fails :func:`lib.checks.url_problem` (a backslash, credentials, a bad host)
    is for the caller to skip and report."""
    joined = urljoin(base, link.translate(_LINK_DROPPED).strip())
    return "".join(
        quote(char, safe="") if char.isspace() or unicodedata.category(char) == "Cc" or char in _LINK_ESCAPED else char
        for char in joined
    )


_SRCSET_SPACE = "\t\n\f\r "  # ASCII whitespace (HTML)


def srcset_urls(value: str) -> list[str]:
    """The image URLs of a ``srcset`` attribute, parsed as HTML does: a candidate URL runs to whitespace (so it may
    contain commas, e.g. Jetpack's ``?resize=300,169``) without its trailing commas, and its descriptors run to the next
    comma outside parentheses. Shared by the adapters that read ``srcset`` (direct article images, Google Play)."""
    urls: list[str] = []
    position, end = 0, len(value)
    while True:
        while position < end and (value[position] in _SRCSET_SPACE or value[position] == ","):
            position += 1
        if position >= end:
            return urls
        start = position
        while position < end and value[position] not in _SRCSET_SPACE:
            position += 1
        url = value[start:position]
        if url.endswith(","):  # no descriptors: the comma ends the candidate
            url = url.rstrip(",")
        else:
            in_parens = False
            while position < end and (value[position] != "," or in_parens):
                if value[position] == "(":
                    in_parens = True
                elif value[position] == ")":
                    in_parens = False
                position += 1
        if url:
            urls.append(url)


def last_segment(url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] or "file"


def stored(url: str | None) -> str | None:
    from ..runner import stored_url  # local import: runner imports the adapter registry

    return stored_url(url)


def reported(
    *,
    url: str | None = None,
    mime: str | None = None,
    bytes: int | None = None,
    width: int | None = None,
    height: int | None = None,
    sha1: str | None = None,
    timestamp: str | None = None,
    revision: Any = None,
) -> dict:
    """``source_reported`` with only valid values (bad advertised numbers become null, never an exception)."""

    def number(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    sha = sha1.lower() if isinstance(sha1, str) and re.fullmatch(r"[0-9a-fA-F]{40}", sha1) else None
    return schema.make_source_reported(
        url=stored(url),
        mime=mime or None,
        bytes=number(bytes),
        width=number(width),
        height=number(height),
        sha1=sha,
        timestamp=timestamp,
        revision=revision,
    )


MIME_BY_EXT = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
}


def mime_for(ext: str | None) -> str | None:
    return MIME_BY_EXT.get((ext or "").lower().lstrip("."))


def identity(seed: Mapping[str, Any], texts: Mapping[str, str], url: str) -> tuple[list[str], list[str]]:
    """(evidence strings for the seed's ``identity_quotes`` found in ``texts``, missing quotes)."""
    found, missing = model.find_quotes(seed.get("identity_quotes", []), texts, url)
    return [f"{item['field']}: {item['quote']}" for item in found], missing


def identity_rule(rule: Mapping[str, Any] | None, missing: list[str]) -> Mapping[str, Any] | None:
    """Downgrade a download rule to ``pending`` when the page's identity quotes were not found."""
    if rule is None or not missing or rule.get("action", "download") != "download":
        return rule
    return model.hold(
        rule,
        "unresolved_game: page identity not established (missing "
        + "; ".join(model.clip(quote, 60) for quote in missing)
        + ")",
    )
