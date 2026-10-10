"""Explicit file URLs on an explicit page (portfolio galleries, album sites, circle pages).

For pages whose gallery is rendered by JavaScript (Wix pro-gallery, Tumblr themes) the seed names each file directly:
``{"key", "page_url", "file_url"}``. With ``"read_page": true`` the page is fetched once and its title/text become the
evidence that seed rules quote; otherwise rules cannot claim ``confirmed_primary`` and carry their own notes. Nothing
beyond the named files is followed.
"""

from __future__ import annotations

from typing import Any, Mapping

from lib import checks, net

from .. import model
from . import base

HOSTS: list[str] = []  # per seed


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    for index, item in enumerate(base.require_items(seed, where, ["key", "page_url", "file_url"])):
        for name in ("page_url", "file_url"):
            if checks.http_url(item[name]):
                raise ValueError(f"{where}: items[{index}].{name} {checks.http_url(item[name])}")
    if not seed.get("hosts"):
        raise ValueError(f"{where}: hosts are required")


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    discovery = base.new_discovery(seed, lists=["seed-files"],
                                   scope_notes="Explicitly named files only; the page's gallery is not crawled.")
    page_texts: dict[str, dict[str, str]] = {}
    for item in seed["items"]:
        page_url, file_url, key = item["page_url"], item["file_url"], item["key"]
        texts = {}
        if item.get("read_page"):
            if page_url not in page_texts:
                try:
                    html, _, _ = ctx.client.get_text(page_url)
                    document = base.soup(html)
                    page_texts[page_url] = {"title": document.title.get_text(" ", strip=True) if document.title
                                            else "", "body": base.page_text(base.soup(html))}
                except net.FetchError as error:
                    discovery.errors.append(model.discovery_error("discover", page_url, str(error)))
                    base.mark_unread(discovery, "seed-files", error)
                    page_texts[page_url] = {}
            texts = page_texts[page_url]
        name = base.last_segment(file_url)
        ext = name.rsplit(".", 1)[-1] if "." in name else None
        draft = model.apply_rule(
            model.find_rule(base.rules(seed), key), key=key, page_url=page_url, texts=texts,
            file_url=base.stored(file_url), default_title=item.get("title") or name, default_quality=ctx.quality,
            default_rights=ctx.rights, original_filename=item.get("original_filename") or name,
            base_evidence=[page_url], source_reported=base.reported(url=file_url, mime=base.mime_for(ext)))
        if item.get("notes") and "notes" not in draft.candidate_extra:
            draft.candidate_extra["notes"] = item["notes"]
        discovery.candidates.append(draft)
    return discovery
