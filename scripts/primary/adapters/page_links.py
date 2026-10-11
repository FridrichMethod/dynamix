"""Files linked from one explicit web page (press releases, press screenshot galleries).

Each seed item names a page and ``link_rules``: which attribute to read (``href``/``src``), a regex the absolute URL's
path must match, and whether to drop the query (CMS resize parameters such as ``?mw=1980``). Every matching link is a
candidate keyed by its path; its classification comes from the source's seed rules, unclassified links stay ``pending``.
Links are resolved as a browser requests them (a literal space is percent-encoded, :func:`base.link_url`); a matching
link that is still no valid http(s) URL is reported as a discovery error and keeps the list open (``links-skipped-N``).
Used for the HKSTP press release (appendix PDFs, press photo) and the 4Gamer screenshot gallery.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from lib import checks, net

from .. import model
from . import base

HOSTS: list[str] = []  # per seed


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    for index, item in enumerate(base.require_items(seed, where, ["url"])):
        link_rules = item.get("link_rules")
        if not isinstance(link_rules, list) or not link_rules:
            raise ValueError(f"{where}: items[{index}].link_rules must be a non-empty list")
        for rule_index, rule in enumerate(link_rules):
            label = f"{where}: items[{index}].link_rules[{rule_index}]"
            if not isinstance(rule, dict) or rule.get("attr") not in ("href", "src"):
                raise ValueError(f"{label} needs attr href/src and a pattern")
            model.check_regex(rule.get("pattern"), f"{label}.pattern")
    if not seed.get("hosts"):
        raise ValueError(f"{where}: hosts are required")


def links(html: str, page_url: str, link_rules: list[Mapping[str, Any]]) -> tuple[list[tuple[str, str]], list[str]]:
    """([(key, absolute URL)] for every link matching a rule, first occurrence wins, in page order; [matching links
    that are no valid http(s) URL even after browser-style encoding (:func:`base.link_url`)], which the caller
    reports instead of keying them)."""
    document = base.soup(html)
    found: dict[str, str] = {}
    skipped: list[str] = []
    page_host = urlsplit(page_url).hostname
    for rule in link_rules:
        pattern = re.compile(rule["pattern"])
        for element in document.find_all(attrs={rule["attr"]: True}):
            raw = element.get(rule["attr"])
            try:
                url = base.link_url(page_url, raw)
                parts = urlsplit(url)
            except ValueError:  # unparseable, e.g. an unbalanced IPv6 bracket: reported, never keyed
                skipped.append(raw)
                continue
            if parts.scheme not in ("http", "https") or not pattern.search(parts.path):
                continue
            if checks.url_problem(url):
                if url not in skipped:
                    skipped.append(url)
                continue
            if rule.get("same_host", True) and parts.hostname != page_host:
                continue
            if rule.get("strip_query"):
                url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            key = parts.path.lstrip("/") + ("" if rule.get("strip_query") or not parts.query else f"?{parts.query}")
            found.setdefault(key, url)
    return list(found.items()), skipped


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    discovery = base.new_discovery(
        seed, lists=["seed-pages"], scope_notes="Links matching explicit patterns on explicit pages only."
    )
    pages_read: set[str] = set()
    discovery.coverage["seed-pages"] = base.listed_on(pages_read)
    skipped_links = 0
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
        found, skipped = links(html, page_url, item["link_rules"])
        for url in skipped:
            discovery.errors.append(
                model.discovery_error(
                    "discover",
                    page_url,
                    f"link skipped, not a valid http(s) URL ({checks.url_problem(url) or 'unparseable'}): "
                    f"{model.clip(repr(url), 160)}",
                )
            )
        skipped_links += len(skipped)
        if not found:
            discovery.errors.append(model.discovery_error("discover", page_url, "no links matched the seed rules"))
            discovery.continuation["seed-pages"] = "no-matching-links"
        elif not skipped:  # every link of the page was read: a link it no longer has is gone from the page
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
    if skipped_links and discovery.continuation.get("seed-pages") is None:
        # A matching link without a candidate: the pages' links are not fully accounted for.
        discovery.continuation["seed-pages"] = f"links-skipped-{skipped_links}"
    return discovery
