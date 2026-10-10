"""App Store product-page screenshots (``apps.apple.com``), parsed from the page's embedded server data.

Observed structure (03 §4, not a published API): ``<script id="serialized-server-data">`` holds
``data[0].data.shelfMapping`` whose ``product_media_<device>_`` shelves list ``ProductMediaItem`` objects. A screenshot
is an ``Artwork`` with a URL ``template`` (``.../pr_source.png/{w}x{h}{c}.{f}``), its native ``width``/``height``, a
``crop`` code and ``variants`` (formats). The native rendition is requested with the native size, the advertised crop
and the first advertised variant format. Apple renders that file from the uploaded ``pr_source.png``, so the stored
bytes are a known platform transform (``transformed: true``), never an untouched master. Trailer items are recorded as
``video_placeholder`` exclusions.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from lib import net

from .. import model
from . import base

HOSTS = ["apps.apple.com", ".mzstatic.com"]
_UUID = re.compile(r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/")
FORMAT_EXT = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp"}


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    base.require_items(seed, where, ["url"])


def server_data(html: str) -> Any:
    document = base.soup(html)
    script = document.find("script", attrs={"id": "serialized-server-data"})
    if script is None:
        raise ValueError("no serialized-server-data script on the page")
    return json.loads(script.string or script.get_text())


def shelves(data: Any) -> dict[str, Any]:
    """``shelfMapping`` of the first page intent (``data[0].data.shelfMapping``)."""
    entries = data.get("data") if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        raise ValueError("serialized-server-data has no data list")
    mapping = ((entries[0] or {}).get("data") or {}).get("shelfMapping")
    if not isinstance(mapping, dict):
        raise ValueError("no data[0].data.shelfMapping")
    return mapping


def native_rendition(artwork: Mapping[str, Any]) -> tuple[str, int, int, str] | None:
    """(URL, width, height, format) of an Artwork's native-size rendition; None when the template is unusable."""
    template, width, height = artwork.get("template"), artwork.get("width"), artwork.get("height")
    if not isinstance(template, str) or not all(isinstance(v, int) and v > 0 for v in (width, height)):
        return None
    variants = [v.get("format") for v in artwork.get("variants") or [] if isinstance(v, dict)]
    fmt = next((f for f in variants if f in FORMAT_EXT), "jpeg")
    url = (template.replace("{w}", str(width)).replace("{h}", str(height))
           .replace("{c}", artwork.get("crop") or "bb").replace("{f}", fmt))
    return (url, width, height, fmt) if "{" not in url else None


def media_items(mapping: Mapping[str, Any]) -> list[tuple[str, int, dict]]:
    """(device, 1-based index, item) for every product-media shelf, in shelf-name order."""
    found = []
    for name in sorted(mapping):
        match = re.fullmatch(r"product_media_([a-z]+)_?", name)
        shelf = mapping[name]
        if not match or not isinstance(shelf, dict):
            continue
        for index, item in enumerate(shelf.get("items") or [], start=1):
            if isinstance(item, dict):
                found.append((match.group(1), index, item))
    return found


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    discovery = base.new_discovery(seed, lists=["product-media-shelves"],
                                   scope_notes="One storefront product page; screenshots from its product_media_* "
                                               "shelves at native size. App icon and other storefronts not "
                                               "collected.")
    for item in seed["items"]:
        page_url = item["url"]
        try:
            html, _, _ = ctx.client.get_text(page_url)
            mapping = shelves(server_data(html))
        except net.FetchError as error:
            discovery.candidates.append(model.error_draft("page", page_url, "App Store page", error,
                                                          phase="discover"))
            continue
        except ValueError as error:
            discovery.errors.append(model.discovery_error("discover", page_url, f"page structure changed: {error}"))
            discovery.continuation["product-media-shelves"] = "structure-not-recognized"
            continue
        texts = {"page": base.page_text(base.soup(html))}
        evidence, missing = base.identity(seed, texts, page_url)
        for device, index, media in media_items(mapping):
            artwork = media.get("screenshot")
            if not isinstance(artwork, dict):
                key = f"{device}:video-{index}"
                discovery.candidates.append(model.Draft(
                    upstream_key=key, page_url=page_url, title=f"App Store {device} media {index}",
                    action="excluded", game="dynamix", game_evidence=evidence,
                    reason="video_placeholder: trailer/preview item, not a screenshot"))
                continue
            rendition = native_rendition(artwork)
            uuid = _UUID.search(artwork.get("template") or "")
            key = f"{device}:{uuid.group(1) if uuid else index}"
            if rendition is None:
                discovery.candidates.append(model.Draft(
                    upstream_key=key, page_url=page_url, title=f"App Store {device} screenshot {index}",
                    action="pending", reason="pending: unusable artwork template"))
                continue
            url, width, height, fmt = rendition
            rule = base.identity_rule(model.find_rule(base.rules(seed), key), missing)
            draft = model.apply_rule(
                rule, key=key, page_url=page_url, texts=texts, file_url=url,
                default_title=f"App Store {device} screenshot {index}", default_quality=ctx.quality,
                default_rights=ctx.rights, original_filename=base.last_segment(url), base_evidence=evidence,
                source_reported=base.reported(url=url, mime=base.mime_for(FORMAT_EXT[fmt]), width=width,
                                              height=height))
            if draft.action == "download":
                draft.meta["transformed"] = True
            discovery.candidates.append(draft)
    return discovery
