"""X (Twitter) posts through the public embed/syndication endpoint, one explicit post ID at a time.

``cdn.syndication.twimg.com/tweet-result?id=<id>&lang=en&token=<t>`` is what X's own embed widget calls; the token is a
public function of the post ID (``((id / 1e15) * pi).toString(36)`` without zeros and dots), not a credential, but it is
left out of every stored URL anyway. Photos are fetched from ``pbs.twimg.com/media/<key>?format=<ext>&name=orig``, the
largest rendition X serves. X may re-encode uploads, so ``name=orig`` is recorded as the site's original, never as the
artist's master file. No timeline, search or login-only endpoint is used.

Each photo of a multi-image post is its own candidate ``<post id>:<page index>`` classified by its own seed rule, so a
post title never maps every page to the same song.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from lib import net

from .. import model
from . import base

HOSTS = ["cdn.syndication.twimg.com", "pbs.twimg.com"]
API = "https://cdn.syndication.twimg.com/tweet-result"
_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_MEDIA = re.compile(r"^https://pbs\.twimg\.com/media/([A-Za-z0-9_-]+)\.([a-z]+)$")


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    for index, item in enumerate(base.require_items(seed, where, ["post_id"])):
        if not item["post_id"].isdigit():
            raise ValueError(f"{where}: items[{index}].post_id must be digits")
    if not (seed.get("params") or {}).get("handle"):
        raise ValueError(f"{where}: params.handle (the account's screen name) is required")


def js_radix_string(value: float, radix: int = 36) -> str:
    """``Number.prototype.toString(radix)`` for a finite non-negative double (port of V8's DoubleToRadixCString)."""
    if value < 0 or not math.isfinite(value):
        raise ValueError("only finite non-negative values are supported")
    integer = math.floor(value)
    fraction = value - integer
    delta = max(0.5 * (math.nextafter(value, math.inf) - value), math.nextafter(0.0, 1.0))
    digits: list[str] = []
    if fraction >= delta:
        while True:
            fraction *= radix
            delta *= radix
            digit = int(fraction)
            digits.append(_DIGITS[digit])
            fraction -= digit
            if fraction > 0.5 or (fraction == 0.5 and digit & 1):
                if fraction + delta > 1:
                    while True:  # round up, carrying into earlier digits or the integer part
                        if not digits:
                            integer += 1
                            break
                        last = _DIGITS.index(digits.pop())
                        if last + 1 < radix:
                            digits.append(_DIGITS[last + 1])
                            break
                    break
            if fraction < delta:
                break
    integer_digits = []
    integer_value = float(integer)
    while integer_value / radix >= 2.0 ** 53:
        integer_value /= radix
        integer_digits.append("0")
    while True:
        remainder = math.fmod(integer_value, radix)
        integer_digits.append(_DIGITS[int(remainder)])
        integer_value = (integer_value - remainder) / radix
        if integer_value <= 0:
            break
    text = "".join(reversed(integer_digits))
    return f"{text}.{''.join(digits)}" if digits else text


def syndication_token(post_id: str) -> str:
    """The public token X's embed script sends with ``tweet-result`` for ``post_id``."""
    return re.sub(r"(0+|\.)", "", js_radix_string((float(int(post_id)) / 1e15) * math.pi))


def _texts(data: Mapping[str, Any]) -> dict[str, str]:
    texts = {"text": data.get("text") or ""}
    quoted = data.get("quoted_tweet") or {}
    if isinstance(quoted, dict):
        texts["quoted_tweet.text"] = quoted.get("text") or ""
        user = quoted.get("user") or {}
        texts["quoted_tweet.user"] = f"{user.get('name', '')} @{user.get('screen_name', '')}".strip()
    user = data.get("user") or {}
    texts["user"] = f"{user.get('name', '')} @{user.get('screen_name', '')}".strip()
    return texts


def _media(data: Mapping[str, Any]) -> list[dict]:
    media = data.get("mediaDetails")
    if isinstance(media, list) and media:
        return [item for item in media if isinstance(item, dict)]
    photos = data.get("photos") or []
    return [{"type": "photo", "media_url_https": photo.get("url"),
             "original_info": {"width": photo.get("width"), "height": photo.get("height")}}
            for photo in photos if isinstance(photo, dict)]


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    handle = seed["params"]["handle"]
    discovery = base.new_discovery(seed, lists=["seed-posts"], api=API, evidence_level="live_api",
                                   scope_notes="Explicit seed post IDs via X's public embed (syndication) endpoint; "
                                               "the account timeline is not enumerated.")
    for item in seed["items"]:
        post_id = item["post_id"]
        status_url = f"https://x.com/{handle}/status/{post_id}"
        try:
            data, _ = ctx.client.get_json(API, {"id": post_id, "lang": "en", "token": syndication_token(post_id)})
        except net.FetchError as error:
            discovery.candidates.append(model.error_draft(post_id, status_url, f"@{handle} post {post_id}", error,
                                                          phase="discover"))
            continue
        if not isinstance(data, dict) or data.get("__typename") != "Tweet":
            kind = data.get("__typename") if isinstance(data, dict) else type(data).__name__
            error = net.FetchError(f"post not available from the embed endpoint ({kind or 'empty response'})",
                                   url=status_url)
            discovery.candidates.append(model.error_draft(post_id, status_url, f"@{handle} post {post_id}", error,
                                                          phase="discover"))
            continue
        author = ((data.get("user") or {}).get("screen_name") or "").lower()
        texts = _texts(data)
        media = _media(data)
        base_evidence = [status_url]
        if not media:
            discovery.candidates.append(model.Draft(
                upstream_key=post_id, page_url=status_url, title=f"@{handle} post {post_id}", action="excluded",
                game="unknown", reason="out_of_scope_other: the post has no image attachment"))
            continue
        for index, entry in enumerate(media, start=1):
            key = f"{post_id}:{index}"
            rule = model.find_rule(base.rules(seed), key)
            if author != handle.lower():
                rule = {**(rule or {}), "action": "pending",
                        "reason": f"pending: post author @{author or '?'} is not this source's account @{handle}"}
            title = f"@{handle} post {post_id} image {index}"
            if entry.get("type") != "photo":
                discovery.candidates.append(model.Draft(
                    upstream_key=key, page_url=status_url, title=title, action="excluded", game="unknown",
                    reason=f"video_placeholder: X {entry.get('type')} attachment; only a poster frame is an image"))
                continue
            match = _MEDIA.match(entry.get("media_url_https") or "")
            if not match:
                discovery.candidates.append(model.Draft(
                    upstream_key=key, page_url=status_url, title=title, action="pending",
                    reason="pending: unrecognized X media URL"))
                continue
            media_key, ext = match.groups()
            file_url = f"https://pbs.twimg.com/media/{media_key}?format={ext}&name=orig"
            info = entry.get("original_info") or {}
            draft = model.apply_rule(
                rule, key=key, page_url=status_url, texts=texts, file_url=file_url, default_title=title,
                default_quality=ctx.quality, default_rights=ctx.rights, original_filename=f"{media_key}.{ext}",
                source_reported=base.reported(url=file_url, mime=base.mime_for(ext), width=info.get("width"),
                                              height=info.get("height"), timestamp=data.get("created_at")),
                base_evidence=base_evidence)
            discovery.candidates.append(draft)
    return discovery
