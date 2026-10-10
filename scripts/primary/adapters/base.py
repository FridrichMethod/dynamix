"""Helpers shared by the per-platform adapters."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlsplit

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


def new_discovery(seed: Mapping[str, Any], *, lists: list[str], api: str | None = None,
                  evidence_level: str = "page_read", scope_notes: str = "") -> model.Discovery:
    """A discovery whose ``lists`` are complete once processed; the seed's ``unenumerated_lists`` stay open."""
    continuation = {name: None for name in lists}
    unenumerated = seed.get("unenumerated_lists", {})
    continuation.update({name: str(state) for name, state in unenumerated.items()})
    crawl = seed.get("crawl_scope")
    notes = scope_notes or (crawl.get("notes", "") if isinstance(crawl, dict) else "")
    return model.Discovery(lists=[*lists, *unenumerated], continuation=continuation, api=api,
                           evidence_level=evidence_level, scope_notes=notes)


def mark_unread(discovery: model.Discovery, list_name: str, error: Any) -> None:
    """Keep ``list_name`` open after a failed read (07: never infer completeness from an error or empty response).

    The token names the failure class; an earlier open token of the list (a resume key, a sample size) is kept."""
    if discovery.continuation.get(list_name) is None:
        discovery.continuation[list_name] = f"not-read:{getattr(error, 'error_class', None) or type(error).__name__}"


def page_key(url: str) -> str:
    """Candidate key of a seed page whose read failed (one per page, so several failed pages never collide)."""
    return f"page:{url}"


def rules(seed: Mapping[str, Any]) -> list[dict]:
    return list(seed.get("rules", []))


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def page_text(document: BeautifulSoup) -> str:
    for element in document(["script", "style", "noscript"]):
        element.extract()
    return " ".join(document.get_text(" ").split())


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


def last_segment(url: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] or "file"


def stored(url: str | None) -> str | None:
    from ..runner import stored_url  # local import: runner imports the adapter registry

    return stored_url(url)


def reported(*, url: str | None = None, mime: str | None = None, bytes: int | None = None,
             width: int | None = None, height: int | None = None, sha1: str | None = None,
             timestamp: str | None = None, revision: Any = None) -> dict:
    """``source_reported`` with only valid values (bad advertised numbers become null, never an exception)."""
    def number(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    sha = sha1.lower() if isinstance(sha1, str) and re.fullmatch(r"[0-9a-fA-F]{40}", sha1) else None
    return schema.make_source_reported(url=stored(url), mime=mime or None, bytes=number(bytes),
                                       width=number(width), height=number(height), sha1=sha, timestamp=timestamp,
                                       revision=revision)


MIME_BY_EXT = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
               "webp": "image/webp", "pdf": "application/pdf"}


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
    return model.hold(rule, "pending: page identity not established (missing "
                      + "; ".join(model.clip(quote, 60) for quote in missing) + ")")
