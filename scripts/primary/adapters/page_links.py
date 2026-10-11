"""Files linked from one explicit web page (press releases, press screenshot galleries).

Each seed item names a page and ``link_rules``: which attribute to read (``href``/``src``), a regex the absolute URL's
path must match, and whether to drop the query (CMS resize parameters such as ``?mw=1980``). Every matching link is a
candidate keyed by its path (``<host>/<path>`` under a rule with ``same_host: false``, whose links may sit on several
hosts); a file that several seed pages link is one candidate, the first page's, whose notes name the other pages. Its
classification comes from the source's seed rules, unclassified links stay ``pending``.
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
            for name in ("same_host", "strip_query"):
                if name in rule and not isinstance(rule[name], bool):
                    raise ValueError(f"{label}.{name} must be true or false")
    if not seed.get("hosts"):
        raise ValueError(f"{where}: hosts are required")


def links(
    html: str, page_url: str, link_rules: list[Mapping[str, Any]], *, final_url: str | None = None
) -> tuple[list[tuple[str, str]], list[str]]:
    """([(key, absolute URL)] for every link matching a rule, first occurrence wins, in page order; [matching links
    that are no valid http(s) URL even after browser-style encoding (:func:`base.link_url`)], which the caller
    reports instead of keying them).

    Links resolve against the document base: ``<base href>``, else ``final_url`` (the URL the page was served from
    after redirects), else ``page_url``. ``same_host`` accepts the requested and the final host."""
    document = base.soup(html)
    found: dict[str, str] = {}
    skipped: list[str] = []
    served = final_url or page_url
    resolve = base.document_base(document, served)
    page_hosts = {urlsplit(page_url).hostname, urlsplit(served).hostname}
    for rule in link_rules:
        pattern = re.compile(rule["pattern"])
        for element in document.find_all(attrs={rule["attr"]: True}):
            raw = element.get(rule["attr"])
            try:
                url = base.link_url(resolve, raw)
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
            if rule.get("same_host", True) and parts.hostname not in page_hosts:
                continue
            if rule.get("strip_query"):
                url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            key = parts.path.lstrip("/") + ("" if rule.get("strip_query") or not parts.query else f"?{parts.query}")
            if not rule.get("same_host", True):  # links on several hosts: the same path on two hosts is two files
                key = f"{parts.netloc.lower()}/{key}"
            found.setdefault(key, url)
    return list(found.items()), skipped


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    discovery = base.new_discovery(
        seed, lists=["seed-pages"], scope_notes="Links matching explicit patterns on explicit pages only."
    )
    pages_read: set[str] = set()
    discovery.coverage["seed-pages"] = base.listed_on(pages_read)
    skipped_links = 0
    emitted: dict[str, model.Draft] = {}  # key -> the draft of the first page that links it
    for item in seed["items"]:
        page_url = item["url"]
        try:
            html, final_url, _ = ctx.client.get_text(page_url)
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
        found, skipped = links(html, page_url, item["link_rules"], final_url=final_url if final_url else None)
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
            first = emitted.get(key)
            if first is not None and first.file_url == base.stored(url):
                # The same file linked from another seed page: one candidate (the first page's) that names this page.
                note = f"Also linked from {page_url}."
                notes = first.candidate_extra.get("notes") or ""
                if note not in notes:
                    first.candidate_extra["notes"] = f"{notes} {note}".strip()
                continue
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
            draft = model.apply_rule(
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
            emitted.setdefault(key, draft)
            discovery.candidates.append(draft)
    if skipped_links and discovery.continuation.get("seed-pages") is None:
        # A matching link without a candidate: the pages' links are not fully accounted for.
        discovery.continuation["seed-pages"] = f"links-skipped-{skipped_links}"
    return discovery
