"""Adapter registry. Each adapter module exposes ``HOSTS`` (fixed host allowlist entries; seeds may add their own
via ``hosts``), ``check_seed(seed, where)`` and ``discover(ctx, seed) -> primary.model.Discovery``. An adapter with
``OFFLINE = True`` makes no request at all and gets a client that refuses every call."""

from . import (
    apple,
    artstation,
    bilibili,
    direct,
    facebook,
    google_play,
    page_links,
    pixiv,
    probe,
    registry,
    release,
    taptap,
    wayback,
    x,
    youtube,
)

ADAPTERS = {
    "x_post": x,
    "bilibili_opus": bilibili,
    "artstation_project": artstation,
    "pixiv_artwork": pixiv,
    "release_page": release,
    "apple_store": apple,
    "google_play": google_play,
    "taptap_app": taptap,
    "page_links": page_links,
    "direct_files": direct,
    "wayback_cdx": wayback,
    "page_probe": probe,
    "lead_registry": registry,
    "youtube_video": youtube,
    "facebook_post": facebook,
}
