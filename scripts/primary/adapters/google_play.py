"""Google Play listing screenshots (``play.google.com/store/apps/details?id=...``).

The listing's ``<img data-screenshot-index=...>`` elements (alt text is localized) carry ``play-lh.googleusercontent.com``
URLs with a size suffix (``=w526-h296``, srcset ``=w1052-h592``). The suffix is a resize request, not the image's
size, and may even disagree with the decoded result (e.g. 1052x591 for ``-h592``); the base URL with ``=s0`` asks for the
stored image at its native size. Width and height always come from decoding the saved bytes; the advertised srcset
sizes are kept in the candidate notes only. The app icon is recorded as a ``game_ui`` exclusion. Several items (one
listing per locale) may list the same image; it stays one candidate whose notes name every listing.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from lib import net

from .. import model
from . import base

HOSTS = ["play.google.com", "play-lh.googleusercontent.com"]
_BASE = re.compile(r"^(https://play-lh\.googleusercontent\.com/[A-Za-z0-9_-]+)(?:=([A-Za-z0-9_-]+))?$")


def check_seed(seed: Mapping[str, Any], where: str) -> None:
    base.require_items(seed, where, ["url"])


def split_size(url: str) -> tuple[str, str | None] | None:
    """(base URL, size suffix) of a play-lh URL, None for anything else."""
    match = _BASE.match(url.strip())
    return (match.group(1), match.group(2)) if match else None


def advertised_size(suffix: str | None) -> tuple[int | None, int | None]:
    """(w, h) a ``w1052-h592`` style suffix requests (a resize request, not the true size)."""
    width = re.search(r"(?:^|-)w(\d+)", suffix or "")
    height = re.search(r"(?:^|-)h(\d+)", suffix or "")
    return (int(width.group(1)) if width else None, int(height.group(1)) if height else None)


def screenshots(html: str, page_url: str) -> list[dict]:
    """Unique screenshots in page order: {token, base, srcs[]} (first index wins)."""
    document = base.soup(html)
    found: dict[str, dict] = {}
    # ``data-screenshot-index`` marks the gallery in every locale (the alt text is localized).
    images = document.find_all("img", attrs={"data-screenshot-index": True}) or document.find_all(
        "img", attrs={"alt": "Screenshot image"})
    for image in images:
        urls = [image.get("src") or ""]
        urls += [part.strip().split(" ")[0] for part in (image.get("srcset") or "").split(",") if part.strip()]
        for url in urls:
            parsed = split_size(base.absolute(page_url, url)) if url else None
            if parsed is None:
                continue
            token = parsed[0].rsplit("/", 1)[-1]
            entry = found.setdefault(token, {"token": token, "base": parsed[0], "srcs": []})
            if url not in entry["srcs"]:
                entry["srcs"].append(url)
    return list(found.values())


def discover(ctx: Any, seed: Mapping[str, Any]) -> model.Discovery:
    discovery = base.new_discovery(seed, lists=["listing-screenshots"],
                                   scope_notes="Listing pages of the seed locales; unique screenshot images at native "
                                               "size (=s0). Reviews, avatars and similar-app images are ignored.")
    by_key: dict[str, model.Draft] = {}
    for item in seed["items"]:
        page_url = item["url"]
        try:
            html, _, _ = ctx.client.get_text(page_url)
        except net.FetchError as error:
            discovery.candidates.append(model.error_draft(base.page_key(page_url), page_url, "Google Play listing",
                                                          error, phase="discover"))
            base.mark_unread(discovery, "listing-screenshots", error)
            continue
        document = base.soup(html)
        texts = {f"ld.{name}": str(block.get(name) or "") for block in base.json_ld(document)
                 for name in ("name", "url") if block.get(name)}
        for block in base.json_ld(document):
            author = block.get("author")
            if isinstance(author, dict) and author.get("name"):
                texts["ld.author"] = author["name"]
        texts["page.title"] = document.title.get_text() if document.title else ""
        evidence, missing = base.identity(seed, texts, page_url)
        shots = screenshots(html, page_url)
        if not shots:
            discovery.errors.append(model.discovery_error("discover", page_url, "no screenshot images found"))
            discovery.continuation["listing-screenshots"] = "structure-not-recognized"
        for index, shot in enumerate(shots, start=1):
            key = f"screenshot:{shot['token']}"
            if key in by_key:
                previous = by_key[key]
                previous.candidate_extra["notes"] = (previous.candidate_extra.get("notes", "")
                                                     + f"; also listed on {page_url}").lstrip("; ")
                continue
            file_url = f"{shot['base']}=s0"
            sizes = sorted({f"{w}x{h}" for w, h in (advertised_size(split_size(base.absolute(page_url, s))[1])
                                                     for s in shot["srcs"]) if w and h})
            rule = base.identity_rule(model.find_rule(base.rules(seed), key), missing)
            draft = model.apply_rule(
                rule, key=key, page_url=page_url, texts=texts, file_url=file_url,
                default_title=f"Google Play screenshot {index}", default_quality=ctx.quality,
                default_rights=ctx.rights, original_filename=base.last_segment(file_url), base_evidence=evidence,
                source_reported=base.reported(url=file_url))
            draft.candidate_extra["notes"] = (f"listed on {page_url}" + ("; page advertised resize requests "
                                              + ", ".join(sizes) + "; stored size is decoded from the =s0 bytes"
                                              if sizes else ""))
            by_key[key] = draft
            discovery.candidates.append(draft)
        icon = base.meta_content(document, "og:image")
        parsed = split_size(icon) if icon else None
        if parsed and f"icon:{parsed[0].rsplit('/', 1)[-1]}" not in by_key:
            by_key[f"icon:{parsed[0].rsplit('/', 1)[-1]}"] = None
            discovery.candidates.append(model.Draft(
                upstream_key=f"icon:{parsed[0].rsplit('/', 1)[-1]}", page_url=page_url, title="Google Play app icon",
                action="excluded", file_url=f"{parsed[0]}=s0", game="dynamix", game_evidence=evidence,
                reason="game_ui: store app icon (branding, not artwork)"))
    return discovery
