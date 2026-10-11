"""App Store product-page screenshots (``apps.apple.com``), parsed from the page's embedded server data.

Observed structure (03 §4, not a published API): ``<script id="serialized-server-data">`` holds
``data[0].data.shelfMapping`` whose ``product_media_<device>_`` shelves list ``ProductMediaItem`` objects. A screenshot
is an ``Artwork`` with a URL ``template`` (``.../pr_source.png/{w}x{h}{c}.{f}``), its native ``width``/``height``, a
``crop`` code and ``variants`` (the formats the page itself loads, e.g. JPEG quality 70). The adapter requests the
native size with the advertised crop in the *uploaded source's* format when that is PNG (``pr_source.png`` ->
``.png``): Apple renders a lossless PNG of the PNG upload at the same size, whereas the advertised JPEG variant is a
lossy re-encode. ``source_reported`` keeps what the page advertises for the image (its own rendition URL and the native
size, which the requested rendition shares); the advertised variant's MIME type is kept only when the stored rendition
has that format, since a different rendition by construction is not comparable (``format_match`` null). The bytes are
still Apple's rendering of the upload (``transformed: true``), never an untouched master. Trailer items are recorded as
``video_placeholder`` exclusions.

A ``{"lookup_id": "<app id>", "countries": [...]}`` item reads the public iTunes Search ``lookup`` API per storefront,
which still lists older, storefront-specific screenshot sets (``screenshotUrls``/``ipadScreenshotUrls``) that the web
page no longer shows. Those URLs (``.../mzl.xxxx.png/406x228bb.png``) are small renditions; the adapter requests the
``4000x4000bb`` bounding box in the upload's format (PNG for a ``.png`` upload), which Apple serves at the image's
native size (it does not enlarge); the stored size is decoded from the bytes and ``source_reported`` keeps the
advertised URL (and its format only when the requested rendition has the same one). A screenshot listed by several
storefronts is one candidate whose notes name every storefront.
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
LOSSLESS_SOURCE = {"png": "png"}  # upload format -> rendition requested instead of the advertised lossy variant
# Observed 2026-10-10 (scripts/primary/README.md, "App Store renditions"): every render of a rendition URL embeds a new
# 26-character token; CDN caches serve one render's bytes repeatedly, while a cache miss serves a fresh render.
IDENTIFIER_NOTE = ("Apple writes an identifier into each render of a rendition (a 26-character token in EXIF "
                   "UserComment, and in XMP dc:creator for PNG). Requests served from one CDN cache entry return the "
                   "same bytes, but a fresh render of the same URL (a cache miss) carries a new token: different bytes "
                   "and SHA-256 with identical pixels. SHA-256 verifies the archived file; compare a re-fetch with it "
                   "by decoded pixels.")
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


def source_format(url: str) -> str | None:
    """Extension of the uploaded source an mzstatic URL or template names (``.../pr_source.png/<size>.<fmt>`` or
    ``.../mzl.abcd.png/<size>.<fmt>``): the path segment before the size segment."""
    parts = (url or "").split("?", 1)[0].rstrip("/").split("/")
    if len(parts) < 2 or "." not in parts[-2]:
        return None
    ext = parts[-2].rsplit(".", 1)[-1].lower()
    return FORMAT_EXT.get(ext)


def rendition_note(requested: str, upload: str | None) -> str:
    """Quality note for the rendition actually requested (``requested``: png/jpg/webp) of an upload of format
    ``upload`` (from the URL; None when not stated)."""
    names = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}
    fmt = names.get(requested, requested.upper())
    if upload is None:
        return f"Apple's {fmt} rendition at native size (the upload's own format is not stated)."
    if requested == "png" and upload == "png":
        return (f"Apple's PNG rendition of the PNG upload at native size (lossless; requested instead of the lossy "
                "variant the page or API advertises).")
    lossy = " (lossy, as the upload itself is a JPEG; a PNG re-encoding would add no information)" if upload == "jpg" \
        else ""
    return f"Apple's {fmt} rendition of the {names.get(upload, upload.upper())} upload at native size{lossy}."


def apple_quality(draft: model.Draft, requested: str, upload: str | None) -> None:
    """Per-record quality note: the rendition actually stored, then the source's note and the identifier note."""
    if draft.action != "download":
        return
    quality = draft.meta["quality"]
    quality["notes"] = " ".join(filter(None, [rendition_note(requested, upload), quality.get("notes"),
                                              IDENTIFIER_NOTE]))


def same_format_mime(advertised: str, requested: str) -> str | None:
    """The advertised rendition's MIME type when the requested rendition has the same format, else None: the stored
    file is then another rendition by construction, so the advertised format is not comparable with it."""
    def ext(url: str) -> str:
        return FORMAT_EXT.get(url.split("?", 1)[0].rsplit(".", 1)[-1].lower(), "")
    return base.mime_for(ext(advertised)) if ext(advertised) and ext(advertised) == ext(requested) else None


def native_lookup_url(url: str) -> str | None:
    """A lookup screenshot URL re-sized to the ``4000x4000bb`` bounding box (Apple caps it at the native size), in the
    upload's own format when it is lossless (PNG); otherwise in the advertised extension."""
    match = _SIZED.match(url or "")
    if not match:
        return None
    fmt = LOSSLESS_SOURCE.get(source_format(url) or "", FORMAT_EXT.get(match.group(5), match.group(5)))
    return f"{match.group(1)}{LOOKUP_BOX}x{LOOKUP_BOX}bb.{fmt}"


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
        if app is None:  # an empty answer is no evidence that the storefront's set is gone (07 §Integrity)
            discovery.errors.append(model.discovery_error("discover", LOOKUP, f"lookup {country}: no result for "
                                                                              f"{item['lookup_id']}"))
            discovery.continuation["itunes-lookup"] = f"no-result-{country}"
            continue
        for device, field in (("phone", "screenshotUrls"), ("pad", "ipadScreenshotUrls")):
            for index, url in enumerate(app.get(field) or [], start=1):
                native = native_lookup_url(url) if isinstance(url, str) else None
                if native is None:
                    continue
                key = lookup_key(url)
                entry = found.setdefault(key, {"url": native, "advertised": url, "device": device, "countries": [],
                                               "index": index, "app": app})
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
            source_reported=base.reported(url=entry["advertised"], mime=same_format_mime(entry["advertised"],
                                                                         entry["url"])))
        upload = entry["advertised"].split("/")[-2]
        draft.candidate_extra["notes"] = ("iTunes lookup API screenshot listed by storefront(s) "
                                          + ", ".join(entry["countries"]) + f"; the API advertises a "
                                          f"{entry['advertised'].rsplit('/', 1)[-1]} rendition of the upload {upload}; "
                                          f"requested {entry['url'].rsplit('/', 1)[-1]} (Apple's rendition in the "
                                          "upload's format, bounded by 4000x4000, served at native size); stored "
                                          "size decoded from the bytes")
        if draft.action == "download":
            draft.meta["transformed"] = True
        apple_quality(draft, entry["url"].rsplit(".", 1)[-1], source_format(entry["advertised"]))
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


def native_rendition(artwork: Mapping[str, Any]) -> dict | None:
    """Native-size renditions of an Artwork: ``url``/``format`` requested (the upload's lossless format when it is PNG,
    else the advertised variant), ``advertised``/``advertised_format`` (what the page itself loads), ``width``,
    ``height``; None when the template is unusable."""
    template, width, height = artwork.get("template"), artwork.get("width"), artwork.get("height")
    if not isinstance(template, str) or not all(isinstance(v, int) and v > 0 for v in (width, height)):
        return None
    variants = [v.get("format") for v in artwork.get("variants") or [] if isinstance(v, dict)]
    advertised_format = next((f for f in variants if f in FORMAT_EXT), "jpeg")
    fmt = LOSSLESS_SOURCE.get(source_format(template) or "", advertised_format)

    def render(value: str) -> str:
        return (template.replace("{w}", str(width)).replace("{h}", str(height))
                .replace("{c}", artwork.get("crop") or "bb").replace("{f}", value))

    url, advertised = render(fmt), render(advertised_format)
    if "{" in url or "{" in advertised:
        return None
    return {"url": url, "format": fmt, "advertised": advertised, "advertised_format": advertised_format,
            "width": width, "height": height}


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
    pages_read: set[str] = set()
    lookup_pages = {f"https://apps.apple.com/app/id{item['lookup_id']}" for item in lookups}
    shelves_cover = base.listed_on(pages_read)
    discovery.coverage["product-media-shelves"] = lambda c: (not str(c.get("upstream_key", "")).startswith(
        "lookup:") and shelves_cover(c))
    if lookups:
        discovery.coverage["itunes-lookup"] = lambda c: (str(c.get("upstream_key", "")).startswith("lookup:")
                                                         and c.get("page_url") in lookup_pages)
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
            discovery.candidates.append(model.error_draft(base.page_key(page_url), page_url, "App Store page", error,
                                                          phase="discover"))
            base.mark_unread(discovery, "product-media-shelves", error)
            continue
        except ValueError as error:
            discovery.errors.append(model.discovery_error("discover", page_url, f"page structure changed: {error}"))
            discovery.continuation["product-media-shelves"] = "structure-not-recognized"
            continue
        texts = {"page": base.page_text(base.soup(html))}
        evidence, missing = base.identity(seed, texts, page_url)
        pages_read.add(page_url)
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
                    action="pending", reason="awaiting_download: unusable artwork template"))
                continue
            rule = base.identity_rule(model.find_rule(base.rules(seed), key), missing)
            url = rendition["url"]
            draft = model.apply_rule(
                rule, key=key, page_url=page_url, texts=texts, file_url=url,
                default_title=f"App Store {device} screenshot {index}", default_quality=ctx.quality,
                default_rights=ctx.rights, original_filename=base.last_segment(url), base_evidence=evidence,
                source_reported=base.reported(url=rendition["advertised"], mime=same_format_mime(
                    rendition["advertised"], url), width=rendition["width"], height=rendition["height"]))
            if rendition["format"] != rendition["advertised_format"]:
                upload = url.split("/")[-2]
                note = (f"The page loads a {rendition['advertised_format'].upper()} rendition "
                        f"({rendition['advertised'].rsplit('/', 1)[-1]}); requested {url.rsplit('/', 1)[-1]}, "
                        f"Apple's lossless rendition of the {upload} upload at the same native size.")
                draft.candidate_extra["notes"] = " ".join(filter(None, [draft.candidate_extra.get("notes"), note]))
            if draft.action == "download":
                draft.meta["transformed"] = True
            apple_quality(draft, FORMAT_EXT[rendition["format"]], source_format(artwork.get("template") or ""))
            discovery.candidates.append(draft)
    return discovery
