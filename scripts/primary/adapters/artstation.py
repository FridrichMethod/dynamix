"""ArtStation projects through the public project JSON, one explicit project hash at a time.

``www.artstation.com/projects/<hash>.json`` lists a project's assets (title, image URL, size). ArtStation sits behind a
bot check that often answers automated clients with HTTP 403 and a challenge page; that is recorded as ``blocked``
(manual access) and never bypassed. When the JSON is available, each image asset is its own candidate
``<hash>:<asset id>``; a project may mix the Dynamix composition with other commissions (e.g. the same artwork made for
another game), so every asset needs its own seed rule and unclassified assets stay ``pending``. A project is the
artist's own only when its owner equals the seed's ``params.username``; without that account every asset is held as
``pending`` (``unresolved_game``).

Image URLs (``cdn*.artstation.com/p/assets/images/.../large/<name>``) are requested in the ``4k`` rendition first and
fall back to the advertised ``large`` one when ``4k`` does not exist (HTTP 404, or a plain 401/403 for the 4k URL, which
is our derivation: object stores answer 403 for a missing key; it does not block the CDN host, a bot challenge does);
the stored ``download_url`` is the one that delivered the bytes. An archived fallback is not called the upload (09,
archived fallback renditions): ``quality.source_variant`` becomes ``unknown`` and the notes name the 4k URL and the
status it answered.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from lib import net

from .. import model
from . import base

HOSTS = ["www.artstation.com", ".artstation.com"]
_SIZE_SEGMENT = re.compile(r"/(large|medium|small|smaller_square|micro_square|4k|original)/")
FALLBACK_NOTE = (
    "Archived from {url}, the rendition the project JSON lists: the 4k rendition {file_url} answered HTTP {status}."
)
FALLBACK_QUALITY_NOTE = (
    "Not established as the upload: ArtStation's listed rendition {url}, archived because the 4k rendition "
    "{file_url} answered HTTP {status}. The advertised width and height describe the upload, not this rendition."
)


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    for index, item in enumerate(base.require_items(seed, where, ["project"])):
        if not re.fullmatch(r"[A-Za-z0-9]+", item["project"]):
            raise ValueError(f"{where}: items[{index}].project must be an ArtStation hash ID")


def rendition_urls(image_url: str) -> list[str]:
    """Download URLs to try, largest first: the ``4k`` rendition (when the URL has a size segment), then as given."""
    if _SIZE_SEGMENT.search(image_url) and "/4k/" not in image_url:
        return [_SIZE_SEGMENT.sub("/4k/", image_url, count=1), image_url]
    return [image_url]


def fallback_adjust(draft: model.Draft, url: str, status: int) -> model.Draft:
    """``Draft.fallback_adjust``: archived from the listed rendition ``url`` (``source_reported.url`` already names it)
    after the 4k rendition answered HTTP ``status``, the record is not called the upload (``source_variant: unknown``,
    with a note)."""
    used = copy.copy(draft)
    if draft.action == "download":
        note = FALLBACK_QUALITY_NOTE.format(url=base.stored(url), file_url=base.stored(draft.file_url), status=status)
        quality = {**draft.meta["quality"], "source_variant": "unknown"}
        quality["notes"] = " ".join(filter(None, [quality.get("notes"), note]))
        used.meta = {**draft.meta, "quality": quality}
    return used


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    discovery = base.new_discovery(
        seed,
        lists=["seed-projects"],
        api="https://www.artstation.com/projects/",
        evidence_level="live_api",
        scope_notes="Explicit seed project hashes via ArtStation project JSON; the portfolio listing is not crawled.",
    )
    username = ((seed.get("params") or {}).get("username") or "").lower()
    for item in seed["items"]:
        project = item["project"]
        json_url = f"https://www.artstation.com/projects/{project}.json"
        page_url = f"https://www.artstation.com/artwork/{project}"
        title = item.get("title") or f"ArtStation project {project}"
        try:
            data, _ = ctx.client.get_json(json_url)
            if not isinstance(data, dict):
                raise net.FetchError(f"project JSON is a {type(data).__name__}, not an object", url=json_url)
        except net.FetchError as error:
            draft = model.error_draft(project, page_url, title, error, phase="discover")
            if item.get("notes"):
                draft.candidate_extra = {"notes": item["notes"]}
            discovery.candidates.append(draft)
            discovery.evidence_level = "blocked" if draft.action == "blocked" else discovery.evidence_level
            base.mark_unread(discovery, "seed-projects", error)
            continue
        user = data.get("user") if isinstance(data.get("user"), dict) else {}
        owner = str(user.get("username") or "").lower()
        texts = {
            "title": str(data.get("title") or ""),
            "description": str(data.get("description") or ""),
            "user": owner,
        }
        listed = data.get("assets") if isinstance(data.get("assets"), list) else []
        assets = [asset for asset in listed if isinstance(asset, dict)]
        images = [asset for asset in assets if asset.get("has_image") and isinstance(asset.get("image_url"), str)]
        if not images:
            discovery.candidates.append(
                model.Draft(
                    upstream_key=project,
                    page_url=page_url,
                    title=title,
                    action="excluded",
                    reason="out_of_scope_other: no image assets",
                )
            )
            continue
        for position, asset in enumerate(images, start=1):
            key = f"{project}:{asset.get('id', position)}"
            rule = model.find_rule(base.rules(seed), key)
            if not username:
                # Without the seed's account the project cannot be attributed to the artist: never admitted as theirs.
                rule = model.hold(
                    rule,
                    "unresolved_game: project owner not verified (params.username not established); "
                    f"owner {owner or '?'}",
                )
            elif owner != username:
                rule = model.hold(
                    rule, f"unresolved_game: project owner {owner or '?'} is not this source's account {username}"
                )
            asset_texts = {**texts, "asset.title": asset.get("title") or ""}
            urls = rendition_urls(asset["image_url"])
            draft = model.apply_rule(
                rule,
                key=key,
                page_url=page_url,
                texts=asset_texts,
                file_url=urls[0],
                default_title=f"{data.get('title') or title} ({position}/{len(images)})",
                default_quality=ctx.quality,
                default_rights=ctx.rights,
                original_filename=base.last_segment(asset["image_url"]),
                base_evidence=[page_url],
                source_reported=base.reported(
                    url=asset["image_url"], width=asset.get("width"), height=asset.get("height")
                ),
            )
            draft.fallback_urls = urls[1:]
            draft.derived_file_url = len(urls) > 1  # the 4k URL is ours; the JSON lists the rendition after it
            draft.fallback_note = FALLBACK_NOTE
            draft.fallback_adjust = fallback_adjust
            discovery.candidates.append(draft)
    return discovery
