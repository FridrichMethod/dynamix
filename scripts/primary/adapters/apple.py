"""App Store product-page screenshots (``apps.apple.com``), parsed from the page's embedded server data.

Observed structure (03 §4, not a published API): ``<script id="serialized-server-data">`` holds
``data[0].data.shelfMapping`` whose ``product_media_<device>_`` shelves list ``ProductMediaItem`` objects. A screenshot
is an ``Artwork`` with a URL ``template`` (``.../pr_source.png/{w}x{h}{c}.{f}``), its native ``width``/``height``, a
``crop`` code and ``variants`` (formats). The native rendition is requested with the native size, the advertised crop
and the first advertised variant format. Apple renders that file from the uploaded ``pr_source.png``, so the stored
bytes are a known platform transform (``transformed: true``), never an untouched master. Trailer items are recorded as
``video_placeholder`` exclusions.

A ``{"lookup_id": "<app id>", "countries": [...]}`` item reads the public iTunes Search ``lookup`` API per storefront,
which still lists older, storefront-specific screenshot sets (``screenshotUrls``/``ipadScreenshotUrls``) that the web
page no longer shows. Those URLs carry a small ``<w>x<h>bb`` size; the adapter requests the ``4000x4000bb`` bounding
box, which Apple serves at the image's native size (it does not enlarge), and the stored size is decoded from the
bytes. A screenshot listed by several storefronts is one candidate whose notes name every storefront.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from lib import net

from .. import model
from . import base

HOSTS = ["apps.apple.com", ".mzstatic.com", "itunes.apple.com"]
LOOKUP = "https://itunes.apple.com/lookup"
_SIZED = re.compile(r"^(https://[a-z0-9-]+\.mzstatic\.com/image/thumb/.+/)(\d+)x(\d+)(bb|sr|[a-z]{2})\.(jpg|jpeg|png|webp)$")
LOOKUP_BOX = 4000
_UUID = re.compile(r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/")
FORMAT_EXT = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp"}


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    items = seed.get("items")
    if not isinstance(items, list):
        raise ValueError(f"{where}: items must be a list")
    for index, item in enumerate(items):
        if "lookup_id" in item:
            if not str(item["lookup_id"]).isdigit() or not item.get("countries") or not all(
                    re.fullmatch(r"[a-z]{2}", c) for c in item["countries"]):
                raise ValueError(f"{where}: items[{index}] lookup needs a numeric lookup_id and 2-letter countries")
        elif not isinstance(item.get("url"), str) or not item["url"].strip():
            raise ValueError(f"{where}: items[{index}].url is required")


def native_lookup_url(url: str) -> str | None:
    """A lookup screenshot URL re-sized to the ``4000x4000bb`` bounding box (Apple caps it at the native size)."""
    match = _SIZED.match(url or "")
    if not match:
        return None
    return f"{match.group(1)}{LOOKUP_BOX}x{LOOKUP_BOX}bb.{match.group(5)}"


def lookup_key(url: str) -> str:
    """``lookup:<asset folder or file stem>`` -- the UUID folder of ``.../<uuid>/pr_source.png/..`` or the
    ``mzl.xxxx.png`` name, which identify the uploaded image independent of the requested size."""
    uuid = _UUID.search(url)
    if uuid:
        return f"lookup:{uuid.group(1)}"
    parts = url.split("/")
    return f"lookup:{parts[-2] if len(parts) > 2 else parts[-1]}"


def discover_lookup(ctx: Any, seed: Mapping[str, Any], item: Mapping[str, Any], discovery: model.Discovery) -> None:
    found: dict[str, dict] = {}
    for country in item["countries"]:
        try:
            data, _ = ctx.client.get_json(LOOKUP, {"id": item["lookup_id"], "country": country, "entity": "software"})
        except net.FetchError as error:
            discovery.errors.append(model.discovery_error("discover", LOOKUP, f"lookup {country}: {error}"))
            discovery.continuation["itunes-lookup"] = f"incomplete-{country}"
            continue
        results = data.get("results") if isinstance(data, dict) else None
        app = next((r for r in results or [] if isinstance(r, dict) and str(r.get("trackId")) == str(
            item["lookup_id"])), None)
        if app is None:
            discovery.errors.append(model.discovery_error("discover", LOOKUP, f"lookup {country}: no result for "
                                                                              f"{item['lookup_id']}"))
            continue
        for device, field in (("phone", "screenshotUrls"), ("pad", "ipadScreenshotUrls")):
            for index, url in enumerate(app.get(field) or [], start=1):
                native = native_lookup_url(url) if isinstance(url, str) else None
                if native is None:
                    continue
                key = lookup_key(url)
                entry = found.setdefault(key, {"url": native, "device": device, "countries": [], "index": index,
                                               "app": app})
                entry["countries"].append(f"{country}:{device}{index}")
    for key, entry in sorted(found.items()):
        texts = {"trackName": str(entry["app"].get("trackName") or ""),
                 "sellerName": str(entry["app"].get("sellerName") or ""),
                 "bundleId": str(entry["app"].get("bundleId") or "")}
        evidence, missing = base.identity(seed, texts, LOOKUP)
        rule = base.identity_rule(model.find_rule(base.rules(seed), key), missing)
        page_url = f"https://apps.apple.com/app/id{item['lookup_id']}"
        draft = model.apply_rule(
            rule, key=key, page_url=page_url, texts=texts, file_url=entry["url"],
            default_title=f"App Store {entry['device']} screenshot (storefront set {entry['countries'][0]})",
            default_quality=ctx.quality, default_rights=ctx.rights, original_filename=base.last_segment(entry["url"]),
            base_evidence=evidence,
            source_reported=base.reported(url=entry["url"], mime=base.mime_for(entry["url"].rsplit(".", 1)[-1])))
        draft.candidate_extra["notes"] = ("iTunes lookup API screenshot listed by storefront(s) "
                                          + ", ".join(entry["countries"]) + "; requested at the 4000x4000bb bounding "
                                          "box, stored size decoded from the bytes")
        if draft.action == "download":
            draft.meta["transformed"] = True
        discovery.candidates.append(draft)


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
    lookups = [item for item in seed["items"] if "lookup_id" in item]
    discovery = base.new_discovery(seed, lists=["product-media-shelves", *(["itunes-lookup"] if lookups else [])],
                                   scope_notes="One storefront product page; screenshots from its product_media_* "
                                               "shelves at native size"
                                               + ("; plus the iTunes lookup screenshot sets of the listed "
                                                  "storefronts" if lookups else "") + ". App icon not collected.")
    for item in lookups:
        discover_lookup(ctx, seed, item, discovery)
    for item in seed["items"]:
        if "lookup_id" in item:
            continue
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
