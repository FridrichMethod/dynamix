"""TapTap app page screenshots (``www.taptap.cn/app/<id>``), parsed from the page's Nuxt payload.

The server-rendered ``<script id="__NUXT_DATA__">`` is a ``devalue``-encoded array: objects and arrays hold indices into
it. The app object (the one with ``screenshots`` and ``identifier``) lists image objects with ``original_url``,
``original_format``, ``width``, ``height`` and ``original_size``; the transform suffixes (``/_tap_screenshot.jpg``) are
previews. ``original_url`` is downloaded, the advertised size/bytes are kept as ``source_reported``, and the 32-hex
name token is compared with the file's MD5 as extra evidence of an untouched upload. The banner, when it is the trailer
thumbnail, is a ``video_placeholder`` and the icon a ``game_ui`` exclusion; every other trailer poster frame of
``app_videos`` (``thumbnail``, and ``raw_cover``: the video's first frame, ``video-picture/<id>/first.jpg``) is a
``video_placeholder`` exclusion too, so each image of the payload is accounted for.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from lib import net

from .. import model
from . import base

HOSTS = ["www.taptap.cn", ".tapimg.com"]
SPECIAL = {-1: None, -2: None, -3: None, -4: None, -5: None, -6: 0}
WRAPPERS = ("Reactive", "ShallowReactive", "Ref", "ShallowRef")
_TOKEN = re.compile(r"/([0-9a-f]{32})\.(png|jpe?g|webp|gif)$")
_RENDITION = re.compile(r"^(https://[^/]+/.+/[0-9a-f]{32})_crop_[0-9a-z]+_thumbnail_\d+x\d+\.(png|jpe?g|webp)$")
_RENDITION_SIZES = re.compile(r"_crop_(\d+)x(\d+)[0-9a-z]*_thumbnail_(\d+)x(\d+)\.")


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    base.require_items(seed, where, ["url"])


def nuxt_payload(html: str) -> list:
    script = base.soup(html).find("script", attrs={"id": "__NUXT_DATA__"})
    if script is None:
        raise ValueError("no __NUXT_DATA__ payload on the page")
    payload = json.loads(script.string or script.get_text())
    if not isinstance(payload, list):
        raise ValueError("__NUXT_DATA__ is not a devalue array")
    return payload


def resolve(payload: list, index: int, *, depth: int = 0) -> Any:
    """Resolve a devalue index (objects/arrays hold indices; wrapper tuples such as ``["Reactive", i]``)."""
    if depth > 64:
        raise ValueError("devalue payload nested too deeply")
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError(f"devalue reference is not an index: {index!r}")
    if index < 0:
        return SPECIAL.get(index)
    value = payload[index]
    if isinstance(value, dict):
        return {key: resolve(payload, item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        if value and isinstance(value[0], str):
            tag = value[0]
            if tag in WRAPPERS:
                return resolve(payload, value[1], depth=depth + 1) if len(value) > 1 else None
            if tag in ("EmptyRef", "EmptyShallowRef", "null"):
                return None
            if tag == "Date":
                return value[1] if len(value) > 1 else None
            if tag == "Set":
                return [resolve(payload, item, depth=depth + 1) for item in value[1:]]
            if tag == "Map":
                return {
                    str(resolve(payload, value[i], depth=depth + 1)): resolve(payload, value[i + 1], depth=depth + 1)
                    for i in range(1, len(value) - 1, 2)
                }
        return [resolve(payload, item, depth=depth + 1) for item in value]
    return value


def find_app(payload: list, identifier: str | None) -> dict | None:
    """The app object: a payload dict with ``screenshots`` and ``identifier`` (matching ``identifier`` if given)."""
    for value in payload:
        if isinstance(value, dict) and "screenshots" in value and "identifier" in value:
            app = {
                key: (
                    resolve(payload, value[key])
                    if key in ("screenshots", "banner", "icon", "identifier", "title", "developers", "app_videos")
                    else None
                )
                for key in value
            }
            if identifier is None or app.get("identifier") == identifier:
                return app
    return None


def _image(entry: Any) -> dict | None:
    return entry if isinstance(entry, dict) and isinstance(entry.get("original_url"), str) else None


def rendition_reason(url: str, width: Any, height: Any, poster: bool) -> str:
    """Exclusion reason of a ``<md5>_crop_<cw>x<ch>…_thumbnail_<tw>x<th>`` banner rendition: ``upscaled`` only when the
    thumbnail is larger than the region it was cropped from (the URL states both sizes); a same-size or smaller
    rendition is ``out_of_scope_other`` (a platform crop/resize of an upload that is archived separately)."""
    match = _RENDITION_SIZES.search(url)
    crop = (int(match.group(1)), int(match.group(2))) if match else None
    thumb = (int(match.group(3)), int(match.group(4))) if match else None
    role = " (also the trailer's poster frame)" if poster else ""
    advertised = f"{width}x{height}" if width and height else "size not advertised"
    if crop and thumb and (thumb[0] > crop[0] or thumb[1] > crop[1]):
        return (
            f"upscaled: TapTap crop/resize rendition ({advertised}) of the uploaded banner, enlarged from a "
            f"{crop[0]}x{crop[1]} crop to {thumb[0]}x{thumb[1]}{role}; the upload itself is the separate banner "
            "candidate"
        )
    detail = f" ({crop[0]}x{crop[1]} crop shown at {thumb[0]}x{thumb[1]})" if crop and thumb else ""
    return (
        f"out_of_scope_other: TapTap crop/resize rendition ({advertised}) of the uploaded banner{detail}{role}; "
        "the upload itself is the separate banner candidate"
    )


def banner_drafts(
    ctx: Any,
    seed: Mapping[str, Any],
    banner: Mapping[str, Any],
    thumbnails: set,
    page_url: str,
    texts: Mapping[str, str],
    evidence: list[str],
    missing: list[str],
) -> list[model.Draft]:
    """The banner: a trailer thumbnail is a ``video_placeholder``; a ``<md5>_crop_..._thumbnail_WxH`` URL is TapTap's
    cropped and resized rendition (see :func:`rendition_reason`), so the base ``<md5>.<ext>`` upload becomes the
    candidate that seed rules classify. The page advertises only the rendition: its URL stays the upload candidate's
    ``source_reported.url`` (its size goes to the candidate notes, not ``source_reported``: the upload is another
    rendition by construction, so the advertised size and format are not comparable with it); the derived upload URL
    is only the download URL."""
    url = banner["original_url"]
    key = f"banner:{base.last_segment(url)}"
    match = _RENDITION.match(url)
    advertised = base.reported(
        url=url, mime=base.mime_for(url.rsplit(".", 1)[-1]), width=banner.get("width"), height=banner.get("height")
    )
    if url in thumbnails and not match:
        return [
            model.Draft(
                upstream_key=key,
                page_url=page_url,
                title="TapTap banner",
                action="excluded",
                file_url=base.stored(url),
                game="dynamix",
                game_evidence=evidence,
                source_reported=advertised,
                reason="video_placeholder: banner is the trailer video thumbnail",
            )
        ]
    drafts = []
    upload_reported = advertised
    if match:
        upload_reported = base.reported(url=url)
        drafts.append(
            model.Draft(
                upstream_key=key,
                page_url=page_url,
                title="TapTap banner (platform rendition)",
                action="excluded",
                file_url=base.stored(url),
                game="dynamix",
                game_evidence=evidence,
                source_reported=advertised,
                reason=rendition_reason(url, banner.get("width"), banner.get("height"), url in thumbnails),
            )
        )
        url = f"{match.group(1)}.{match.group(2)}"
        key = f"banner:{base.last_segment(url)}"
    token = _TOKEN.search(url)
    draft = model.apply_rule(
        base.identity_rule(model.find_rule(base.rules(seed), key), missing),
        key=key,
        page_url=page_url,
        texts=texts,
        file_url=base.stored(url),
        default_title="TapTap banner",
        default_quality=ctx.quality,
        default_rights=ctx.rights,
        original_filename=base.last_segment(url),
        base_evidence=evidence,
        source_reported=upload_reported,
    )
    draft.md5_token = token.group(1) if token else None
    if match:
        size = (
            f" ({banner['width']}x{banner['height']} advertised)"
            if banner.get("width") and banner.get("height")
            else ""
        )
        note = (
            f"The page lists only the rendition {base.last_segment(banner['original_url'])}{size}; "
            f"{base.last_segment(url)} is the upload its name derives from."
        )
        draft.candidate_extra["notes"] = " ".join(filter(None, [draft.candidate_extra.get("notes"), note]))
    drafts.append(draft)
    return drafts


def poster_drafts(urls: set, page_url: str, evidence: list[str]) -> list[model.Draft]:
    """``video_placeholder`` exclusions for trailer poster frames (``.../video-picture/<id>/first.jpg``), keyed by the
    URL path below the host so that two videos' ``first.jpg`` frames stay distinct."""
    drafts = []
    for url in sorted(u for u in urls if isinstance(u, str) and u):
        path = url.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
        drafts.append(
            model.Draft(
                upstream_key=f"video-poster:{path}",
                page_url=page_url,
                title="TapTap trailer poster",
                action="excluded",
                file_url=base.stored(url),
                game="dynamix",
                game_evidence=evidence,
                reason="video_placeholder: trailer poster frame listed with the app's videos",
            )
        )
    return drafts


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    discovery = base.new_discovery(
        seed,
        lists=["app-screenshots"],
        scope_notes="One app page; screenshots (original_url), banner and icon of the "
        "app object only. Reviews/forum images are not collected.",
    )
    identifier = (seed.get("params") or {}).get("identifier")
    pages_read: set[str] = set()
    discovery.coverage["app-screenshots"] = base.listed_on(pages_read)
    for item in seed["items"]:
        page_url = item["url"]
        try:
            html, _, _ = ctx.client.get_text(page_url)
            app = find_app(nuxt_payload(html), identifier)
        except net.FetchError as error:
            discovery.candidates.append(
                model.error_draft(base.page_key(page_url), page_url, "TapTap app page", error, phase="discover")
            )
            base.mark_unread(discovery, "app-screenshots", error)
            continue
        except (ValueError, IndexError) as error:
            discovery.errors.append(model.discovery_error("discover", page_url, f"page structure changed: {error}"))
            discovery.continuation["app-screenshots"] = "structure-not-recognized"
            continue
        if app is None:
            discovery.errors.append(
                model.discovery_error("discover", page_url, f"no app object with identifier {identifier}")
            )
            discovery.continuation["app-screenshots"] = "app-not-found"
            continue
        pages_read.add(page_url)
        developers = " ".join(d.get("name", "") for d in app.get("developers") or [] if isinstance(d, dict))
        texts = {
            "identifier": str(app.get("identifier") or ""),
            "title": str(app.get("title") or ""),
            "developers": developers,
        }
        evidence, missing = base.identity(seed, texts, page_url)
        seen: dict[str, model.Draft] = {}
        for index, shot in enumerate(app.get("screenshots") or [], start=1):
            image = _image(shot)
            if image is None:
                continue
            url = image["original_url"]
            token = _TOKEN.search(url)
            key = f"screenshot:{token.group(1) if token else index}"
            if key in seen:  # the app object may list the same upload twice; one candidate per upload
                previous = seen[key]
                previous.candidate_extra["notes"] = " ".join(
                    filter(
                        None, [previous.candidate_extra.get("notes"), f"Listed again at screenshot position {index}."]
                    )
                )
                continue
            rule = base.identity_rule(model.find_rule(base.rules(seed), key), missing)
            draft = model.apply_rule(
                rule,
                key=key,
                page_url=page_url,
                texts=texts,
                file_url=url,
                default_title=f"TapTap screenshot {index}",
                default_quality=ctx.quality,
                default_rights=ctx.rights,
                original_filename=base.last_segment(url),
                base_evidence=evidence,
                source_reported=base.reported(
                    url=url,
                    mime=base.mime_for(image.get("original_format")),
                    bytes=image.get("original_size"),
                    width=image.get("width"),
                    height=image.get("height"),
                ),
            )
            draft.md5_token = token.group(1) if token else None
            seen[key] = draft
            discovery.candidates.append(draft)
        videos = [video.get("video") or {} for video in app.get("app_videos") or [] if isinstance(video, dict)]
        thumbnails = {(video.get("thumbnail") or {}).get("original_url") for video in videos}
        posters = thumbnails | {(video.get("raw_cover") or {}).get("original_url") for video in videos}
        banner, icon = _image(app.get("banner")), _image(app.get("icon"))
        if banner:
            discovery.candidates.extend(
                banner_drafts(ctx, seed, banner, thumbnails, page_url, texts, evidence, missing)
            )
        discovery.candidates.extend(
            poster_drafts(posters - {banner["original_url"] if banner else None}, page_url, evidence)
        )
        if icon:
            discovery.candidates.append(
                model.Draft(
                    upstream_key=f"icon:{base.last_segment(icon['original_url'])}",
                    page_url=page_url,
                    title="TapTap app icon",
                    action="excluded",
                    file_url=base.stored(icon["original_url"]),
                    game="dynamix",
                    game_evidence=evidence,
                    reason="game_ui: store app icon (branding, not artwork)",
                )
            )
    return discovery
