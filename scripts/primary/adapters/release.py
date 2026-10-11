"""Single music-release pages (Bandcamp track/album, SoundCloud track) named explicitly by a seed.

Evidence is the page's own metadata: SoundCloud's server-rendered ``window.__sc_hydration`` sound object (title,
description, uploader, ``artwork_url``), otherwise JSON-LD (``name``, ``description``, ``creditText``) and Open Graph
tags. The cover is upgraded to the platform's largest stored rendition (Bandcamp ``_0``, SoundCloud ``-original``);
those are platform-served files, so a cover is never called the illustrator's master. ``source_reported.url`` keeps the
rendition the page advertises (``artwork_url``, ``og:image``); the upgraded URL is only the download URL. A release cover is **external
release art** until a seed rule ties it to the in-game jacket, and a page that announces a *rejected submission* is
excluded by the model guard regardless of any Dynamix keyword on it. With ``params.handle`` the uploader's SoundCloud
permalink must match, otherwise the candidate is held as ``pending``.

A ``{"tracks_of": "<handle>"}`` item reads the account's ``soundcloud.com/<handle>/tracks`` page once and adds every
server-rendered track (``<article itemprop="track">`` links) as an item keyed by its permalink, so an official account's
uploads are all accounted for; tracks without a seed rule stay ``pending``. SoundCloud renders only part of a longer
list: when fewer tracks are listed than the hydrated user's ``track_count``, the ``tracks:<handle>`` list stays open.
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
    items = seed.get("items")
    if not isinstance(items, list):
        raise ValueError(f"{where}: items must be a list")
    for index, item in enumerate(items):
        if isinstance(item, dict) and "tracks_of" in item:
            if not re.fullmatch(r"[a-z0-9_-]+", str(item["tracks_of"])) or "soundcloud.com" not in seed.get(
                "hosts", []
            ):
                raise ValueError(
                    f"{where}: items[{index}].tracks_of must be a SoundCloud permalink (with host soundcloud.com)"
                )
        elif not isinstance(item, dict) or not all(
            isinstance(item.get(k), str) and item[k].strip() for k in ("url", "key")
        ):
            raise ValueError(f"{where}: items[{index}] needs url and key (or tracks_of)")
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


def soundcloud_tracks(html: str, handle: str) -> tuple[list[str], int | None]:
    """(track permalinks in page order, the account's ``track_count`` or None) of a ``/<handle>/tracks`` page."""
    document = base.soup(html)
    found: list[str] = []
    for article in document.find_all("article", attrs={"itemprop": "track"}):
        link = article.find("a", attrs={"itemprop": "url"})
        match = re.fullmatch(rf"/{re.escape(handle)}/([a-z0-9_-]+)", (link.get("href") or "") if link else "")
        if match and match.group(1) not in found:
            found.append(match.group(1))
    count = None
    match = _HYDRATION.search(html)
    try:
        entries = json.loads(match.group(1)) if match else []
    except ValueError:
        entries = []
    for entry in entries if isinstance(entries, list) else []:
        data = entry.get("data") if isinstance(entry, dict) and entry.get("hydratable") == "user" else None
        if isinstance(data, dict) and str(data.get("permalink", "")).lower() == handle.lower():
            value = data.get("track_count")
            count = value if isinstance(value, int) and not isinstance(value, bool) else None
    return found, count


def account_tracks(handle: str) -> Any:
    """Coverage predicate of a ``tracks:<handle>`` list: a previous candidate on one of the account's track pages."""
    prefix = f"https://soundcloud.com/{handle}/"

    def covers(candidate: Mapping[str, Any]) -> bool:
        return str(candidate.get("page_url") or "").startswith(prefix)

    return covers


def listed_items(ctx: Any, seed: Mapping[str, Any], discovery: model.Discovery) -> list[dict]:
    """The seed's explicit items plus every track its ``tracks_of`` lists name (explicit items win)."""
    items = [item for item in seed["items"] if "tracks_of" not in item]
    known = {item["url"] for item in items}
    for item in seed["items"]:
        if "tracks_of" not in item:
            continue
        handle, name = item["tracks_of"], f"tracks:{item['tracks_of']}"
        url = f"https://soundcloud.com/{handle}/tracks"
        discovery.coverage[name] = account_tracks(handle)
        try:
            html, _, _ = ctx.client.get_text(url)
        except net.FetchError as error:
            discovery.errors.append(model.discovery_error("discover", url, f"{name}: {error}"))
            base.mark_unread(discovery, name, error)
            continue
        tracks, total = soundcloud_tracks(html, handle)
        if not tracks:
            discovery.errors.append(model.discovery_error("discover", url, f"{name}: no track links found"))
        if total is None or len(tracks) < total:
            discovery.continuation[name] = f"listed-{len(tracks)}-of-{total if total is not None else 'unknown'}"
            discovery.notes.append(
                f"{url} rendered {len(tracks)} of {total if total is not None else '?'} tracks on "
                f"{ctx.now()[:10]}; the others are not enumerated."
            )
        for slug in tracks:
            track_url = f"https://soundcloud.com/{handle}/{slug}"
            if track_url not in known:
                known.add(track_url)
                items.append({"url": track_url, "key": slug})
    return items


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    lists = [f"tracks:{item['tracks_of']}" for item in seed["items"] if "tracks_of" in item]
    discovery = base.new_discovery(
        seed,
        lists=["seed-pages", *lists],
        scope_notes="Explicit release page URLs"
        + (
            " plus the tracks the account's /tracks page renders"
            if lists
            else " only; the artist's catalogue is not enumerated"
        )
        + ".",
    )
    for item in listed_items(ctx, seed, discovery):
        page_url, key = item["url"], item["key"]
        title = item.get("title") or key
        try:
            html, final_url, _ = ctx.client.get_text(page_url)
        except net.FetchError as error:
            discovery.candidates.append(model.error_draft(key, page_url, title, error, phase="discover"))
            base.mark_unread(discovery, "seed-pages", error)
            continue
        document = base.soup(html)
        texts: dict[str, str] = {}
        image = None
        reported_time = None
        sound = soundcloud_sound(html)
        if sound is not None:
            user = sound.get("user") if isinstance(sound.get("user"), dict) else {}
            texts.update(
                {
                    "sc.title": str(sound.get("title") or ""),
                    "sc.description": str(sound.get("description") or ""),
                    "sc.user": f"{user.get('username') or ''} ({user.get('permalink') or ''})",
                }
            )
            reported_time = sound.get("created_at") if isinstance(sound.get("created_at"), str) else None
            handle = (seed.get("params") or {}).get("handle")
            if handle and str(user.get("permalink") or "").lower() != handle.lower():
                texts["sc.uploader_mismatch"] = str(user.get("permalink") or "?")
            image = sound.get("artwork_url") if isinstance(sound.get("artwork_url"), str) else None
            if not image:
                discovery.candidates.append(
                    model.Draft(
                        upstream_key=key,
                        page_url=page_url,
                        title=texts["sc.title"] or title,
                        action="excluded",
                        reason="out_of_scope_other: the track has no artwork of its own (SoundCloud shows the "
                        "uploader avatar)",
                    )
                )
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
        advertised = image if sound is not None else (og_image or image)  # what the page itself serves
        cover = original_cover(advertised)
        rule = model.find_rule(base.rules(seed), key)
        if "sc.uploader_mismatch" in texts:
            rule = model.hold(
                rule,
                f"unresolved_game: uploader {texts['sc.uploader_mismatch']} is not this source's "
                f"account {seed['params']['handle']}",
            )
        draft = model.apply_rule(
            rule,
            key=key,
            page_url=page_url,
            texts=texts,
            file_url=base.stored(cover),
            default_title=texts.get("sc.title") or texts.get("og:title") or title,
            default_quality=ctx.quality,
            default_rights=ctx.rights,
            original_filename=base.last_segment(cover) if cover else None,
            base_evidence=[page_url],
            source_reported=base.reported(url=advertised, timestamp=reported_time),
        )
        if item.get("notes") and "notes" not in draft.candidate_extra:
            draft.candidate_extra["notes"] = item["notes"]
        discovery.candidates.append(draft)
    return discovery
