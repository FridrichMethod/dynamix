"""Single music-release pages (Bandcamp track/album, SoundCloud track) named explicitly by a seed.

Evidence is the page's own metadata: SoundCloud's server-rendered ``window.__sc_hydration`` sound object (title,
description, uploader, ``artwork_url``), otherwise JSON-LD (``name``, ``description``, ``creditText``) and Open Graph
tags. The cover is upgraded to the platform's largest stored rendition (Bandcamp ``_0``, SoundCloud ``-original``);
those are platform-served files, so a cover is never called the illustrator's master. A release cover is **external
release art** until a seed rule ties it to the in-game jacket, and a page that announces a *rejected submission* is
excluded by the model guard regardless of any Dynamix keyword on it. With ``params.handle`` the uploader's SoundCloud
permalink must match, otherwise the candidate is held as ``pending``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from lib import net

from .. import model
from . import base

HOSTS: list[str] = []  # per seed: the release host and its image CDN


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    base.require_items(seed, where, ["url", "key"])
    if not seed.get("hosts"):
        raise ValueError(f"{where}: hosts (release host and image CDN) are required")


def original_cover(url: str | None) -> str | None:
    if not url:
        return None
    bandcamp = re.sub(r"(/img/a?\d+)_\d+\.(jpg|png)$", r"\1_0.\2", url)
    if bandcamp != url:
        return bandcamp
    return re.sub(r"-(t\d+x\d+|large|crop|t500x500)\.(jpg|png)$", r"-original.\2", url)


_HYDRATION = re.compile(r"window\.__sc_hydration\s*=\s*(\[.*?\]);\s*</script>", re.S)


def soundcloud_sound(html: str) -> dict | None:
    """The ``sound`` object of a SoundCloud track page's hydration data (None when absent or unparsable)."""
    match = _HYDRATION.search(html)
    if not match:
        return None
    try:
        entries = json.loads(match.group(1))
    except ValueError:
        return None
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("hydratable") == "sound" and isinstance(entry.get("data"), dict):
            return entry["data"]
    return None


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    discovery = base.new_discovery(seed, lists=["seed-pages"],
                                   scope_notes="Explicit release page URLs only; the artist's catalogue is not "
                                               "enumerated.")
    for item in seed["items"]:
        page_url, key = item["url"], item["key"]
        title = item.get("title") or key
        try:
            html, final_url, _ = ctx.client.get_text(page_url)
        except net.FetchError as error:
            discovery.candidates.append(model.error_draft(key, page_url, title, error, phase="discover"))
            continue
        document = base.soup(html)
        texts: dict[str, str] = {}
        image = None
        reported_time = None
        sound = soundcloud_sound(html)
        if sound is not None:
            user = sound.get("user") if isinstance(sound.get("user"), dict) else {}
            texts.update({"sc.title": str(sound.get("title") or ""), "sc.description": str(
                sound.get("description") or ""), "sc.user": f"{user.get('username') or ''} "
                                                            f"({user.get('permalink') or ''})"})
            reported_time = sound.get("created_at") if isinstance(sound.get("created_at"), str) else None
            handle = (seed.get("params") or {}).get("handle")
            if handle and str(user.get("permalink") or "").lower() != handle.lower():
                texts["sc.uploader_mismatch"] = str(user.get("permalink") or "?")
            image = sound.get("artwork_url") if isinstance(sound.get("artwork_url"), str) else None
            if not image:
                discovery.candidates.append(model.Draft(
                    upstream_key=key, page_url=page_url, title=texts["sc.title"] or title, action="excluded",
                    reason="out_of_scope_other: the track has no artwork of its own (SoundCloud shows the "
                           "uploader avatar)"))
                continue
        for index, block in enumerate(base.json_ld(document)):
            for name in ("name", "description", "creditText", "headline"):
                if isinstance(block.get(name), str):
                    texts[f"ld[{index}].{name}"] = block[name]
            if image is None:
                image = block.get("image") if isinstance(block.get("image"), str) else None
        for name in ("og:title", "og:description"):
            if base.meta_content(document, name):
                texts[name] = base.meta_content(document, name)
        og_image = base.meta_content(document, "og:image")
        cover = original_cover(image if sound is not None else (og_image or image))
        rule = model.find_rule(base.rules(seed), key)
        if "sc.uploader_mismatch" in texts:
            rule = {**(rule or {}), "action": "pending", "reason": f"pending: uploader "
                    f"{texts['sc.uploader_mismatch']} is not this source's account {seed['params']['handle']}"}
        draft = model.apply_rule(
            rule, key=key, page_url=page_url, texts=texts, file_url=base.stored(cover),
            default_title=texts.get("sc.title") or texts.get("og:title") or title, default_quality=ctx.quality,
            default_rights=ctx.rights, original_filename=base.last_segment(cover) if cover else None,
            base_evidence=[page_url], source_reported=base.reported(url=cover, timestamp=reported_time))
        if item.get("notes") and "notes" not in draft.candidate_extra:
            draft.candidate_extra["notes"] = item["notes"]
        discovery.candidates.append(draft)
    return discovery
