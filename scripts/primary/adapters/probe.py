"""Registry-only sources: one bounded read of a page (or none) to record availability, never a crawl.

Used for sources that cannot be enumerated or hold no artwork: the dead official site (DNS failure is recorded as
``temporarily_unavailable``), the C4Cat placeholder, social accounts behind login walls (``manual_access``, the feed is
an open ``unenumerated_lists`` entry) and credit pages. Expected quotes found on the page are kept as notes; a missing
quote is reported as a discovery error. Items with ``"request": false`` make no request at all.
"""

from __future__ import annotations

from typing import Any, Mapping

from lib import net

from .. import model
from . import base

HOSTS: list[str] = []  # per seed
FAILURE_STATUS = {"blocked": "manual_access", "challenge": "manual_access", "host_not_allowed": "manual_access"}


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    for index, item in enumerate(base.require_items(seed, where, ["url"])):
        if item.get("request", True) and not seed.get("hosts"):
            raise ValueError(f"{where}: hosts are required when items[{index}] makes a request")
    if seed.get("status_on_success") and seed["status_on_success"] not in (
            "available", "partially_available", "metadata_only", "manual_access", "excluded"):
        raise ValueError(f"{where}: invalid status_on_success")


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    discovery = base.new_discovery(seed, lists=list(seed.get("lists", ["probe"])),
                                   scope_notes="Availability probe only; no candidates are enumerated.")
    status = seed.get("status_on_success", "metadata_only")
    for item in seed["items"]:
        url = item["url"]
        if not item.get("request", True):
            discovery.notes.append(item.get("note", ""))
            discovery.evidence_level = item.get("evidence_level", "not_established")
            continue
        try:
            html, final_url, _ = ctx.client.get_text(url)
        except net.FetchError as error:
            discovery.errors.append(model.discovery_error("discover", url, str(error)))
            discovery.evidence_level = "blocked" if error.error_class in FAILURE_STATUS else "not_established"
            status = FAILURE_STATUS.get(error.error_class, "temporarily_unavailable")
            discovery.notes.append(f"Probe of {url} on {ctx.now()[:10]} failed: {error.error_class}"
                                   f"{'' if error.http_status is None else f' (HTTP {error.http_status})'}: "
                                   f"{model.clip(error.message, 120)}.")
            continue
        document = base.soup(html)
        texts = {"title": document.title.get_text(" ", strip=True) if document.title else "",
                 "body": base.page_text(base.soup(html))}
        found, missing = model.find_quotes(item.get("expect_quotes", []), texts, url)
        discovery.notes.extend(f"Page read {ctx.now()[:10]} ({url}): {entry['field']} contains "
                               f"“{entry['quote']}”." for entry in found)
        for quote in missing:
            discovery.errors.append(model.discovery_error("discover", url, f"expected text not found: "
                                                                           f"{model.clip(quote, 80)}"))
        if item.get("note"):
            discovery.notes.append(item["note"])
    discovery.status = status
    discovery.notes = [note for note in discovery.notes if note]
    return discovery
