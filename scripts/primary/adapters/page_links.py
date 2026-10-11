"""Files linked from one explicit web page (press releases, press screenshot galleries).

Each seed item names a page and ``link_rules``: which attribute to read (``href``/``src``), a regex the absolute URL's
path must match, and whether to drop the query (CMS resize parameters such as ``?mw=1980``). Every matching link is a
candidate keyed by its path; its classification comes from the source's seed rules, unclassified links stay ``pending``.
Used for the HKSTP press release (appendix PDFs, press photo) and the 4Gamer screenshot gallery.
"""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from lib import net

from .. import model
from . import base

HOSTS: list[str] = []  # per seed


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    for index, item in enumerate(base.require_items(seed, where, ["url"])):
        link_rules = item.get("link_rules")
        if not isinstance(link_rules, list) or not link_rules:
            raise ValueError(f"{where}: items[{index}].link_rules must be a non-empty list")
        for rule in link_rules:
            if rule.get("attr") not in ("href", "src") or not isinstance(rule.get("pattern"), str):
                raise ValueError(f"{where}: items[{index}].link_rules need attr href/src and a pattern")
            re.compile(rule["pattern"])
    if not seed.get("hosts"):
        raise ValueError(f"{where}: hosts are required")


def links(html: str, page_url: str, link_rules: list[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """(key, absolute URL) for every link matching a rule, first occurrence wins, in page order."""
    document = base.soup(html)
    found: dict[str, str] = {}
    page_host = urlsplit(page_url).hostname
    for rule in link_rules:
        pattern = re.compile(rule["pattern"])
        for element in document.find_all(attrs={rule["attr"]: True}):
            url = base.absolute(page_url, element.get(rule["attr"]))
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not pattern.search(parts.path):
                continue
            if rule.get("same_host", True) and parts.hostname != page_host:
                continue
            if rule.get("strip_query"):
                url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            key = parts.path.lstrip("/") + ("" if rule.get("strip_query") or not parts.query else f"?{parts.query}")
            found.setdefault(key, url)
    return list(found.items())


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    discovery = base.new_discovery(
        seed, lists=["seed-pages"], scope_notes="Links matching explicit patterns on explicit pages only."
    )
    pages_read: set[str] = set()
    discovery.coverage["seed-pages"] = base.listed_on(pages_read)
    for item in seed["items"]:
        page_url = item["url"]
        try:
            html, _, _ = ctx.client.get_text(page_url)
        except net.FetchError as error:
            discovery.candidates.append(
                model.error_draft(
                    item.get("key") or base.page_key(page_url),
                    page_url,
                    item.get("title", "seed page"),
                    error,
                    phase="discover",
                )
            )
            base.mark_unread(discovery, "seed-pages", error)
            continue
        document = base.soup(html)
        texts = {
            "title": document.title.get_text(" ", strip=True) if document.title else "",
            "body": base.page_text(base.soup(html)),
        }
        evidence, missing = base.identity(seed, texts, page_url)
        found = links(html, page_url, item["link_rules"])
        if not found:
            discovery.errors.append(model.discovery_error("discover", page_url, "no links matched the seed rules"))
            discovery.continuation["seed-pages"] = "no-matching-links"
        else:  # every link of the page was read: a link it no longer has is gone from the page
            pages_read.add(page_url)
        title = base.headline(document)
        for position, (key, url) in enumerate(found, start=1):
            rule = base.identity_rule(model.find_rule(base.rules(seed), key), missing)
            name = base.last_segment(url)
            ext = name.rsplit(".", 1)[-1] if "." in name else None
            # Titled from the page it cites: its headline and the file's position among the page's links.
            default = (
                f"{item['title_prefix']}{name}"
                if item.get("title_prefix")
                else f"{model.clip(title, 120)} ({position}/{len(found)})"
                if title and len(found) > 1
                else title or name
            )
            discovery.candidates.append(
                model.apply_rule(
                    rule,
                    key=key,
                    page_url=page_url,
                    texts=texts,
                    file_url=base.stored(url),
                    default_title=default,
                    default_quality=ctx.quality,
                    default_rights=ctx.rights,
                    original_filename=name,
                    base_evidence=evidence,
                    source_reported=base.reported(url=url, mime=base.mime_for(ext)),
                )
            )
    return discovery
