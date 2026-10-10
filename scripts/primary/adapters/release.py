"""Single music-release pages (Bandcamp track/album, SoundCloud track) named explicitly by a seed.

The page's JSON-LD (``name``, ``description``, ``creditText``) and Open Graph tags are the evidence; the cover image is
the Open Graph image upgraded to the platform's original rendition (Bandcamp ``_0``, SoundCloud ``-original``). A
release cover is **external release art** until a seed rule ties it to the in-game jacket with evidence, and a page that
announces a *rejected submission* is excluded by the model guard regardless of any Dynamix keyword on it.
"""

from __future__ import annotations

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
        cover = original_cover(og_image or image)
        rule = model.find_rule(base.rules(seed), key)
        discovery.candidates.append(model.apply_rule(
            rule, key=key, page_url=page_url, texts=texts, file_url=base.stored(cover),
            default_title=texts.get("og:title") or title, default_quality=ctx.quality, default_rights=ctx.rights,
            original_filename=base.last_segment(cover) if cover else None, base_evidence=[page_url],
            source_reported=base.reported(url=cover)))
    return discovery
