"""Official YouTube channel: video thumbnails and channel art, one explicit video ID (or art item) at a time.

``www.youtube.com/oembed?url=<watch URL>&format=json`` (public, no key) names the video's title and channel
(``author_url``); a video whose channel is not the seed's ``params.channel_url`` is held as ``pending``. The file is the
largest thumbnail rendition YouTube generates for the video: ``i.ytimg.com/vi/<id>/maxresdefault.jpg``, falling back to
``sddefault.jpg`` and ``hqdefault.jpg`` when the larger one answers 404 (older uploads have no maxres rendition). These
are YouTube's own JPEG renditions of the video's thumbnail (``transformed``; never an uploaded original); what oEmbed
advertises (the ``hqdefault`` URL) stays ``source_reported.url`` with its size in the notes, because another rendition
is not comparable byte for byte; oEmbed advertises no MIME type, so ``source_reported.mime`` is null and
``format_match`` is not computed against a rendition the archive did not store. No video, audio, Data API or login
is used, and the channel's video list is not enumerated here (the seed names the videos; the list stays open with its reason). ``--resume`` reuses an archived
thumbnail of the same video whichever rendition it is (no maxres/sd requests for a video whose archive fell back).

oEmbed answers 401/403 for a single private or non-embeddable video (YouTube's documented behaviour): that is the
video's own ``failed`` result (``http_4xx``, not retried) and does not block ``www.youtube.com`` for the other videos;
only repeated refusals in a row or a bot challenge do (see ``GuardedClient``).

``{"channel_art": "banner" | "avatar", "url": "https://yt3.googleusercontent.com/<id>"}`` items read the channel page
once: the image ID must occur in it (identity), and the ``=s0`` (full-size) rendition is downloaded.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from lib import net

from .. import model
from . import base

HOSTS = ["www.youtube.com", "i.ytimg.com", "yt3.googleusercontent.com"]
OEMBED = "https://www.youtube.com/oembed"
RENDITIONS = ("maxresdefault", "sddefault", "hqdefault")
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_ART = re.compile(r"^https://yt3\.googleusercontent\.com/([A-Za-z0-9_-]{20,})$")
THUMB_QUALITY = ("YouTube-generated JPEG thumbnail rendition of the video (largest available of maxresdefault "
                 "1280x720, sddefault 640x480, hqdefault 480x360); a platform transform, not an uploaded original.")


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    channel = (seed.get("params") or {}).get("channel_url")
    if not isinstance(channel, str) or not re.fullmatch(r"https://www\.youtube\.com/@[A-Za-z0-9._-]+", channel):
        raise ValueError(f"{where}: params.channel_url (https://www.youtube.com/@<handle>) is required")
    items = seed.get("items")
    if not isinstance(items, list):
        raise ValueError(f"{where}: items must be a list")
    keys = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{where}: items[{index}] must be an object")
        if "channel_art" in item:
            if item["channel_art"] not in ("banner", "avatar") or not _ART.match(str(item.get("url", ""))):
                raise ValueError(f"{where}: items[{index}] channel_art must be banner/avatar with a "
                                 "yt3.googleusercontent.com image URL without size suffix")
            keys.append(f"channel:{item['channel_art']}")
        elif not _VIDEO_ID.fullmatch(str(item.get("video_id", ""))):
            raise ValueError(f"{where}: items[{index}].video_id must be an 11-character YouTube video ID")
        else:
            keys.append(item["video_id"])
    if len(set(keys)) != len(keys):
        raise ValueError(f"{where}: duplicate items")


def thumbnail(video_id: str, rendition: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/{rendition}.jpg"


def same_channel(author_url: Any, channel_url: str) -> bool:
    return isinstance(author_url, str) and author_url.rstrip("/").casefold() == channel_url.rstrip("/").casefold()


def _video(ctx: Any, seed: Mapping[str, Any], discovery: model.Discovery, item: Mapping[str, Any]) -> None:
    video_id, channel = item["video_id"], seed["params"]["channel_url"]
    watch = f"https://www.youtube.com/watch?v={video_id}"
    try:
        try:
            data, _ = ctx.client.get_json(OEMBED, {"url": watch, "format": "json"}, per_item_refusal=True)
        except net.BlockedError as refusal:
            if getattr(refusal, "skipped", False) or isinstance(refusal, net.ChallengeError) or \
                    refusal.http_status not in (401, 403):
                raise
            raise net.HttpError(f"oEmbed refused this video (HTTP {refusal.http_status}): private or not embeddable",
                                url=watch, http_status=refusal.http_status, retriable=False) from refusal
        if not isinstance(data, dict) or not isinstance(data.get("title"), str):
            raise net.FetchError("oEmbed response without a title", url=watch)
    except net.FetchError as error:
        draft = model.error_draft(video_id, watch, item.get("title") or f"YouTube video {video_id}", error,
                                  phase="discover")
        if item.get("notes"):
            draft.candidate_extra = {"notes": item["notes"]}
        discovery.candidates.append(draft)
        base.mark_unread(discovery, "seed-videos", error)
        return
    texts = {"title": data["title"], "author": f"{data.get('author_name') or ''} {data.get('author_url') or ''}".strip()}
    rule = model.find_rule(base.rules(seed), video_id)
    if not same_channel(data.get("author_url"), channel):
        rule = model.hold(rule, f"unresolved_game: the video's channel {data.get('author_url')!r} is not {channel}")
    advertised = data.get("thumbnail_url") if isinstance(data.get("thumbnail_url"), str) else None
    draft = model.apply_rule(
        rule, key=video_id, page_url=watch, texts=texts, file_url=thumbnail(video_id, RENDITIONS[0]),
        default_title=data["title"], default_quality=ctx.quality, default_rights=ctx.rights,
        original_filename=f"{video_id}.jpg", base_evidence=[watch, f"oEmbed author: {texts['author']}"],
        source_reported=base.reported(url=advertised))  # oEmbed names a URL, no MIME type: the stored rendition differs
    draft.fallback_urls = [thumbnail(video_id, rendition) for rendition in RENDITIONS[1:]]
    renditions = {thumbnail(video_id, rendition) for rendition in RENDITIONS}
    draft.same_file = renditions.__contains__  # an archived fallback rendition of this video is this file
    note = (f"oEmbed names the video “{model.clip(data['title'], 120)}” on channel {data.get('author_name')} "
            f"({data.get('author_url')}) and advertises {advertised} ({data.get('thumbnail_width')}x"
            f"{data.get('thumbnail_height')}); the archive requests the largest generated rendition instead.")
    if draft.action == "download":
        draft.meta["transformed"] = True
        draft.meta["quality"]["notes"] = " ".join(filter(None, [draft.meta["quality"].get("notes"), THUMB_QUALITY]))
        draft.meta["notes"] = " ".join(filter(None, [draft.meta.get("notes"), note]))
    notes = " ".join(filter(None, [item.get("notes"), draft.candidate_extra.get("notes")]))
    draft.candidate_extra["notes"] = notes or note
    discovery.candidates.append(draft)


def _channel_art(ctx: Any, seed: Mapping[str, Any], discovery: model.Discovery, item: Mapping[str, Any],
                 page: dict) -> None:
    channel = seed["params"]["channel_url"]
    key = f"channel:{item['channel_art']}"
    if "html" not in page:
        try:
            page["html"], _, _ = ctx.client.get_text(channel)
        except net.FetchError as error:
            page["html"], page["error"] = None, error
            base.mark_unread(discovery, "channel-art", error)
    if page["html"] is None:
        discovery.candidates.append(model.error_draft(key, channel, f"YouTube channel {item['channel_art']}",
                                                      page["error"], phase="discover"))
        return
    image_id = _ART.match(item["url"]).group(1)
    document = base.soup(page["html"])
    texts = {"title": document.title.get_text(" ", strip=True) if document.title else ""}
    rule = model.find_rule(base.rules(seed), key)
    if image_id not in page["html"]:
        rule = model.hold(rule, f"unresolved_game: the channel page no longer shows this {item['channel_art']} image")
    discovery.candidates.append(model.apply_rule(
        rule, key=key, page_url=channel, texts=texts, file_url=f"{item['url']}=s0",
        default_title=f"{texts['title'] or 'YouTube channel'} {item['channel_art']}", default_quality=ctx.quality,
        default_rights=ctx.rights, original_filename=f"{item['channel_art']}-{image_id[:16]}.jpg",
        base_evidence=[channel, f"channel page lists image {image_id[:24]}…"],
        source_reported=base.reported(url=f"{item['url']}=s0")))


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    art = [item for item in seed["items"] if "channel_art" in item]
    discovery = base.new_discovery(seed, lists=["seed-videos", *(["channel-art"] if art else [])], api=OEMBED,
                                   evidence_level="live_api",
                                   scope_notes="Explicit video IDs via YouTube's public oEmbed endpoint and the "
                                               "thumbnail renditions YouTube generates; no video, audio or Data API.")
    page: dict = {}
    for item in seed["items"]:
        if "channel_art" in item:
            _channel_art(ctx, seed, discovery, item, page)
        else:
            _video(ctx, seed, discovery, item)
    return discovery
