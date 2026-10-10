"""Bilibili opus/dynamic posts through the public web API, one explicit opus ID at a time.

``api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail?id=<id>`` returns the post's modules (author, text, pictures).
Bilibili answers anonymous clients with risk-control code ``-352``/``-412`` or a captcha page (``验证码``,
``risk-captcha``); both are recorded as a ``challenge`` (blocked, manual access) and never worked around: no cookie
farming, request signing or captcha solving. Picture URLs on ``*.hdslb.com`` are stored without their ``@...`` resize
suffix, which is the original upload as served.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from lib import net

from .. import model
from . import base

HOSTS = ["api.bilibili.com", ".hdslb.com"]
API = "https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail"
RISK_CODES = {-352: "risk control", -412: "request intercepted", -401: "unauthorized", -403: "access denied"}
CAPTCHA_MARKERS = ("risk-captcha", "验证码_哔哩哔哩", "_riskdata_")


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    for index, item in enumerate(base.require_items(seed, where, ["opus_id"])):
        if not item["opus_id"].isdigit():
            raise ValueError(f"{where}: items[{index}].opus_id must be digits")


def is_captcha_page(text: str) -> bool:
    return any(marker in text for marker in CAPTCHA_MARKERS)


def _walk(value: Any) -> Iterable[Any]:
    stack = [value]
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, dict):
            stack.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            stack.extend(reversed(current))


def original_picture_url(url: str) -> str | None:
    """``https://i0.hdslb.com/bfs/...png@1192w.webp`` -> ``https://i0.hdslb.com/bfs/...png`` (hdslb.com only)."""
    if not isinstance(url, str) or not url:
        return None
    url = ("https:" + url) if url.startswith("//") else url.replace("http://", "https://", 1)
    url = url.split("@", 1)[0]
    host = (urlsplit(url).hostname or "").lower()
    return url if host == "hdslb.com" or host.endswith(".hdslb.com") else None


def extract(data: Mapping[str, Any]) -> tuple[dict, list[dict], dict[str, str]]:
    """(author {mid, name}, pictures [{url, width, height, size}] in document order, texts) from an opus detail."""
    author: dict = {}
    pictures: list[dict] = []
    seen: set[str] = set()
    words: list[str] = []
    title = ""
    for node in _walk(data):
        if not isinstance(node, dict):
            continue
        if not author and "mid" in node and isinstance(node.get("name"), str):
            author = {"mid": str(node["mid"]), "name": node["name"]}
        if isinstance(node.get("words"), str):
            words.append(node["words"])
        if isinstance(node.get("title"), str) and not title:
            title = node["title"]
        url = original_picture_url(node.get("url") or node.get("src") or "")
        if url and ("width" in node or "height" in node) and url not in seen:
            seen.add(url)
            pictures.append({"url": url, "width": node.get("width"), "height": node.get("height"),
                             "size": node.get("size")})
    texts = {"title": title, "text": " ".join(words), "author": author.get("name", "")}
    return author, pictures, texts


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    discovery = base.new_discovery(seed, lists=["seed-posts"], api=API, evidence_level="live_api",
                                   scope_notes="Explicit seed opus IDs via Bilibili's public web API; no space "
                                               "listing or search is crawled.")
    expected_mid = str((seed.get("params") or {}).get("mid") or "")
    for item in seed["items"]:
        opus_id = item["opus_id"]
        page_url = f"https://www.bilibili.com/opus/{opus_id}"
        title = f"Bilibili opus {opus_id}"
        try:
            data, _ = ctx.client.get_json(API, {"id": opus_id})
            code = data.get("code") if isinstance(data, dict) else None
            if code in RISK_CODES:
                ctx.client.mark_blocked(API, f"code {code}")
                raise net.ChallengeError(f"Bilibili {RISK_CODES[code]} (code {code}) for an anonymous client",
                                         url=page_url, http_status=200)
            if code != 0:
                raise net.FetchError(f"Bilibili API code {code}: {model.clip(str(data.get('message')), 80)}",
                                     url=page_url)
        except net.FetchError as error:
            discovery.candidates.append(model.error_draft(opus_id, page_url, title, error, phase="discover"))
            continue
        author, pictures, texts = extract(data.get("data") or {})
        if expected_mid and author.get("mid") != expected_mid:
            override = {"action": "pending", "reason": f"pending: post author mid {author.get('mid')} is not "
                                                       f"this source's account {expected_mid}"}
        else:
            override = None
        if not pictures:
            discovery.candidates.append(model.Draft(upstream_key=opus_id, page_url=page_url, title=title,
                                                    action="excluded", reason="out_of_scope_other: no pictures"))
            continue
        for index, picture in enumerate(pictures, start=1):
            key = f"{opus_id}:{index}"
            rule = model.find_rule(base.rules(seed), key)
            if override:
                rule = {**(rule or {}), **override}
            name = base.last_segment(picture["url"])
            discovery.candidates.append(model.apply_rule(
                rule, key=key, page_url=page_url, texts=texts, file_url=picture["url"],
                default_title=f"{title} image {index}", default_quality=ctx.quality, default_rights=ctx.rights,
                original_filename=name, base_evidence=[page_url],
                source_reported=base.reported(url=picture["url"], mime=base.mime_for(name.rsplit(".", 1)[-1]),
                                              width=picture["width"], height=picture["height"])))
    return discovery
