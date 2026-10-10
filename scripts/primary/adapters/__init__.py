"""Adapter registry. Each adapter module exposes ``HOSTS`` (fixed host allowlist entries; seeds may add their own
via ``hosts``), ``check_seed(seed, where)`` and ``discover(ctx, seed) -> primary.model.Discovery``."""

from . import apple, artstation, bilibili, google_play, page_links, probe, release, taptap, wayback, x

ADAPTERS = {
    "x_post": x,
    "bilibili_opus": bilibili,
    "artstation_project": artstation,
    "release_page": release,
    "apple_store": apple,
    "google_play": google_play,
    "taptap_app": taptap,
    "page_links": page_links,
    "wayback_cdx": wayback,
    "page_probe": probe,
}
