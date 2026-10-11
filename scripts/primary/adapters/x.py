"""X (Twitter) posts through the public embed/syndication endpoint, one explicit post ID at a time.

``cdn.syndication.twimg.com/tweet-result?id=<id>&lang=en&token=<t>`` is what X's own embed widget calls; the token is a
public function of the post ID (``((id / 1e15) * pi).toString(36)`` without zeros and dots), not a credential, but it is
left out of every stored URL anyway. Photos are fetched from ``pbs.twimg.com/media/<key>?format=<ext>&name=orig``, the
largest rendition X serves. X may re-encode uploads, so ``name=orig`` is recorded as the site's original, never as the
artist's master file. ``source_reported`` keeps what the endpoint advertises (``media_url_https`` and
``original_info`` size); the derived ``name=orig`` URL is only the download URL. No search or login-only endpoint is
used.

Each photo of a multi-image post is its own candidate ``<post id>:<page index>`` classified by its own seed rule, so a
post title never maps every page to the same song.

A ``{"timeline": "<screen name>"}`` item reads the account's public embed timeline
(``syndication.twitter.com/srv/timeline-profile/screen-name/<name>``, the data behind X's own profile widget): one
request, a bounded recent sample of the account's posts (never complete history; the list stays open with its reason),
whose image posts become candidates like explicit ones. Retweets and other authors are held as ``pending``, and so is
every timeline post that no per-post rule (one whose ``match`` names the post ID, or that needs ``evidence_quotes``)
classifies: a catch-all rule never admits a timeline post.
"""

from __future__ import annotations

import html
import json
import math
import re
from typing import Any, Mapping

from lib import net

from .. import model
from . import base

HOSTS = ["cdn.syndication.twimg.com", "pbs.twimg.com", "syndication.twitter.com"]
API = "https://cdn.syndication.twimg.com/tweet-result"
TIMELINE = "https://syndication.twitter.com/srv/timeline-profile/screen-name/"
_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_MEDIA = re.compile(r"^https://pbs\.twimg\.com/media/([A-Za-z0-9_-]+)\.([a-z]+)$")


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    items = seed.get("items")
    if not isinstance(items, list):
        raise ValueError(f"{where}: items must be a list")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{where}: items[{index}] must be an object")
        if "timeline" in item:
            if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", str(item["timeline"])):
                raise ValueError(f"{where}: items[{index}].timeline must be a screen name")
        elif not str(item.get("post_id", "")).isdigit():
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
    while integer_value / radix >= 2.0**53:
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
    """Post texts as plain text: X returns ``text`` HTML-escaped (``&amp;``, ``&lt;``), which is decoded here."""
    texts = {"text": html.unescape(str(data.get("text") or ""))}
    quoted = data.get("quoted_tweet") or {}
    if isinstance(quoted, dict):
        texts["quoted_tweet.text"] = html.unescape(str(quoted.get("text") or ""))
        user = quoted.get("user") or {}
        texts["quoted_tweet.user"] = f"{user.get('name', '')} @{user.get('screen_name', '')}".strip()
    user = data.get("user") or {}
    texts["user"] = f"{user.get('name', '')} @{user.get('screen_name', '')}".strip()
    return texts


def _media(data: Mapping[str, Any]) -> list[dict]:
    media = data.get("mediaDetails")
    if isinstance(media, list) and media:
        return [item for item in media if isinstance(item, dict)]
    extended = (data.get("extended_entities") or {}).get("media") or (data.get("entities") or {}).get("media")
    if isinstance(extended, list) and extended:
        return [item for item in extended if isinstance(item, dict)]
    photos = data.get("photos") or []
    return [
        {
            "type": "photo",
            "media_url_https": photo.get("url"),
            "original_info": {"width": photo.get("width"), "height": photo.get("height")},
        }
        for photo in photos
        if isinstance(photo, dict)
    ]


def timeline_tweets(html: str) -> list[dict]:
    """Tweet objects of a ``timeline-profile`` page (``__NEXT_DATA__`` ``props.pageProps.timeline.entries``)."""
    document = base.soup(html)
    script = document.find("script", attrs={"id": "__NEXT_DATA__"})
    if script is None:
        raise ValueError("no __NEXT_DATA__ on the timeline page")
    data = json.loads(script.string or script.get_text())
    entries = (((data.get("props") or {}).get("pageProps") or {}).get("timeline") or {}).get("entries")
    if not isinstance(entries, list):
        raise ValueError("timeline has no entries list")
    tweets = []
    for entry in entries:
        tweet = ((entry or {}).get("content") or {}).get("tweet") if isinstance(entry, dict) else None
        if isinstance(tweet, dict) and str(tweet.get("id_str") or "").isdigit():
            tweets.append(tweet)
    return tweets


_TCO = re.compile(r"https?://t\.co/[A-Za-z0-9]+")


def link_texts(data: Mapping[str, Any]) -> dict[str, str]:
    """t.co link -> the text X displays for it (``entities.urls[].display_url``): links the post text shows as words
    (X auto-links names such as ``Marion.net``). Media links (``pic.x.com/...``) are not included and are dropped."""
    found: dict[str, str] = {}
    for entry in (data.get("entities") or {}).get("urls") or []:
        if isinstance(entry, dict) and isinstance(entry.get("url"), str) and isinstance(entry.get("display_url"), str):
            found[entry["url"]] = entry["display_url"]
    return found


def post_title(text: str, fallback: str, index: int, count: int, links: Mapping[str, str] | None = None) -> str:
    """A readable record title from the post text (whitespace collapsed, at most 100 characters); ``fallback`` when the
    post has no text. A t.co link is replaced by the text X displays for it (``links``) and dropped otherwise (media
    links); only the link token itself is replaced, so text written right after it (no space in CJK) is kept. Several
    images of one post are numbered ``(n/N)``."""
    links = links or {}
    words = " ".join(_TCO.sub(lambda match: links.get(match.group(0)) or " ", text or "").split())
    title = model.clip(words, 100) if words else fallback
    return f"{title} ({index}/{count})" if count > 1 and words else title


def per_post_rule(rule: Mapping[str, Any] | None, post_id: str) -> bool:
    """True when ``rule`` was written for this post: its ``match`` names the post ID or it needs evidence quotes."""
    return rule is not None and (post_id in str(rule.get("match", "")) or bool(rule.get("evidence_quotes")))


def _post_candidates(
    ctx: Any,
    seed: Mapping[str, Any],
    discovery: model.Discovery,
    handle: str,
    post_id: str,
    data: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    from_timeline: bool = False,
) -> None:
    status_url = f"https://x.com/{handle}/status/{post_id}"
    author = ((data.get("user") or {}).get("screen_name") or "").lower()
    texts = _texts(data)
    if not texts["text"] and data.get("full_text"):
        texts["text"] = html.unescape(str(data["full_text"]))
    media = _media(data)
    base_evidence = [status_url]
    notes = item.get("notes")
    if not media:
        draft = model.Draft(
            upstream_key=post_id,
            page_url=status_url,
            title=f"@{handle} post {post_id}",
            action="excluded",
            game="unknown",
            reason="out_of_scope_other: the post has no image attachment",
        )
        if notes:
            draft.candidate_extra = {"notes": notes}
        discovery.candidates.append(draft)
        return
    for index, entry in enumerate(media, start=1):
        key = f"{post_id}:{index}"
        rule = model.find_rule(base.rules(seed), key)
        if (
            from_timeline
            and rule is not None
            and rule.get("action", "download") == "download"
            and not per_post_rule(rule, post_id)
        ):
            # A timeline sample holds whatever the account posted lately (sequel news, other titles, greetings): a
            # catch-all rule never admits it; only a rule written for the post or one with quotes found in its text.
            rule = model.hold(
                rule,
                "unresolved_role: embed-timeline post without a per-post seed rule or evidence quote; needs review",
            )
        if author != handle.lower():
            rule = model.hold(
                rule, f"unresolved_game: post author @{author or '?'} is not this source's account @{handle}"
            )
        title = post_title(
            texts["text"], f"@{handle} post {post_id} image {index}", index, len(media), link_texts(data)
        )
        if entry.get("type") != "photo":
            discovery.candidates.append(
                model.Draft(
                    upstream_key=key,
                    page_url=status_url,
                    title=title,
                    action="excluded",
                    game="unknown",
                    reason=f"video_placeholder: X {entry.get('type')} attachment; only a poster frame is an image",
                )
            )
            continue
        match = _MEDIA.match(entry.get("media_url_https") or "")
        if not match:
            discovery.candidates.append(
                model.Draft(
                    upstream_key=key,
                    page_url=status_url,
                    title=title,
                    action="pending",
                    reason="unresolved_role: unrecognized X media URL",
                )
            )
            continue
        media_key, ext = match.groups()
        file_url = f"https://pbs.twimg.com/media/{media_key}?format={ext}&name=orig"
        info = entry.get("original_info") or {}
        draft = model.apply_rule(
            rule,
            key=key,
            page_url=status_url,
            texts=texts,
            file_url=file_url,
            default_title=title,
            default_quality=ctx.quality,
            default_rights=ctx.rights,
            original_filename=f"{media_key}.{ext}",
            source_reported=base.reported(
                url=entry.get("media_url_https"),
                mime=base.mime_for(ext),
                width=info.get("width"),
                height=info.get("height"),
                timestamp=data.get("created_at"),
            ),
            base_evidence=base_evidence,
        )
        if notes and "notes" not in draft.candidate_extra:
            draft.candidate_extra["notes"] = notes
        discovery.candidates.append(draft)


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    handle = seed["params"]["handle"]
    timelines = [item for item in seed["items"] if "timeline" in item]
    lists = ["seed-posts", *(f"timeline:{item['timeline']}" for item in timelines)]
    discovery = base.new_discovery(
        seed,
        lists=lists,
        api=API,
        evidence_level="live_api",
        scope_notes="Explicit seed post IDs via X's public embed (syndication) endpoint"
        + (
            "; plus the bounded public embed timeline sample (not the full history)"
            if timelines
            else "; the account timeline is not enumerated."
        ),
    )
    seen: set[str] = set()
    for item in timelines:
        name = f"timeline:{item['timeline']}"
        url = TIMELINE + item["timeline"]
        try:
            text, _, _ = ctx.client.get_text(url)
            tweets = timeline_tweets(text)
        except (net.FetchError, ValueError) as error:
            discovery.errors.append(model.discovery_error("discover", url, f"{name}: {error}"))
            discovery.continuation[name] = "not-read"
            continue
        # The embed timeline is a recent sample: the list stays open because older posts are not reachable here.
        discovery.continuation[name] = f"sample-of-{len(tweets)}-recent-posts"
        for tweet in tweets:
            post_id = str(tweet["id_str"])
            if post_id in seen:
                continue
            seen.add(post_id)
            _post_candidates(ctx, seed, discovery, handle, post_id, tweet, item, from_timeline=True)
    for item in seed["items"]:
        if "timeline" in item:
            continue
        post_id = str(item["post_id"])
        if post_id in seen:
            continue
        seen.add(post_id)
        status_url = f"https://x.com/{handle}/status/{post_id}"
        try:
            data, _ = ctx.client.get_json(API, {"id": post_id, "lang": "en", "token": syndication_token(post_id)})
        except net.FetchError as error:
            discovery.candidates.append(
                model.error_draft(post_id, status_url, f"@{handle} post {post_id}", error, phase="discover")
            )
            base.mark_unread(discovery, "seed-posts", error)
            continue
        if not isinstance(data, dict) or data.get("__typename") != "Tweet":
            kind = data.get("__typename") if isinstance(data, dict) else type(data).__name__
            error = net.FetchError(
                f"post not available from the embed endpoint ({kind or 'empty response'})", url=status_url
            )
            discovery.candidates.append(
                model.error_draft(post_id, status_url, f"@{handle} post {post_id}", error, phase="discover")
            )
            base.mark_unread(discovery, "seed-posts", error)
            continue
        _post_candidates(ctx, seed, discovery, handle, post_id, data, item)
    return discovery
