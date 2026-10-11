#!/usr/bin/env python3
"""Generate the synthetic schema-v1 contract fixture (repository-shaped; usable as ``--root`` by builders).

    python -I tests/fixtures/contract/make_fixture.py [--output DIR]

Writes ``data/`` and ``assets/`` under DIR (default: this directory). Every byte is deterministic for the pinned
Pillow (requirements.txt): a PNG keeps the committed bytes of the identical image (tests/fixtures/pinned_png.py), so the
zlib build cannot change a committed picture. tests/test_fixtures_contract.py regenerates into a temporary directory
and compares.
All records are SYNTHETIC test data: images are generated patterns, post IDs/URLs are placeholders and nothing here
is evidence about real Dynamix artwork. Scenario letters (a)-(o) are listed in README.md.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
sys.path.insert(0, str(HERE.parent))

from lib import manifest, paths, schema  # noqa: E402
from pinned_png import PinnedPNG  # noqa: E402

PINNED = PinnedPNG(HERE / "assets")

FETCHED = "2026-10-10T12:00:00Z"
STARTED, FINISHED = "2026-10-10T11:00:00Z", "2026-10-10T11:30:00Z"
SYNTHETIC = "Synthetic contract fixture for offline tests; not a real acquisition or real artwork."
MIRAHEZE, FANDOM = "https://dynamix.miraheze.org", "https://dynamix.fandom.com"
MIRAHEZE_CDN, FANDOM_CDN = "https://static.miraheze.org/dynamixwiki", "https://static.wikia.nocookie.net/dynamix/images"
X_POST = "https://x.com/ariiol_/status/1000000000000000001"
X_GIF_POST = "https://x.com/ariiol_/status/1000000000000000003"
HKSTP = "https://www.hkstp.org/fixture"
FACEBOOK = "https://www.facebook.com/dynamix.fixture"
FACEBOOK_PHOTO = "https://www.facebook.com/photo/?fbid=1000000000000001"
COVER_GROUP = "song:cosmic-pulse/cover"
SCRIPT_TITLE = "Cosmic Pulse full illustration </script><script>alert(\"x\")</script> & 'quotes'"
PALETTE = [16, 24, 48, 40, 90, 200, 230, 80, 160, 250, 220, 90, 90, 220, 200, 245, 245, 250]


# --- Deterministic media ----------------------------------------------------------------------------------------------

def _pattern(size: int, cells: int = 12) -> Image.Image:
    grid = Image.new("P", (cells, cells))
    grid.putpalette(PALETTE)
    grid.putdata([((x * 3 + y * 5) // 2) % 6 for y in range(cells) for x in range(cells)])
    return grid.resize((size, size), Image.Resampling.NEAREST)


def _encode(image: Image.Image, fmt: str, **options: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    return buffer.getvalue()


def _png(image: Image.Image) -> bytes:
    return PINNED.encode(image, compress_level=9)


def _bands(width: int, height: int, offset: int = 0) -> Image.Image:
    image = Image.new("P", (width, height))
    image.putpalette(PALETTE)
    image.putdata([((y * 6) // height + offset) % 6 for y in range(height) for _x in range(width)])
    return image


def _lanes(width: int, height: int) -> Image.Image:
    """A gameplay-screenshot stand-in: side lanes and a hit line (synthetic)."""
    image = Image.new("P", (width, height))
    image.putpalette(PALETTE)
    image.putdata([5 if abs(y - height * 3 // 4) < 2 else 1 if x < width // 5 or x >= width * 4 // 5 else 0
                   for y in range(height) for x in range(width)])
    return image


def _animated_gif() -> bytes:
    frames = []
    for index in range(3):
        frame = Image.new("P", (64, 64))
        frame.putpalette(PALETTE)
        frame.putdata([(index + (x // 16 + y // 16)) % 6 for y in range(64) for x in range(64)])
        frames.append(frame)
    return _encode(frames[0], "GIF", save_all=True, append_images=frames[1:], duration=120, loop=0)


def _gradient_jpeg() -> bytes:
    width, height = 320, 180
    image = Image.new("RGB", (width, height))
    image.putdata([(x * 255 // width, y * 255 // height, 128) for y in range(height) for x in range(width)])
    return _encode(image, "JPEG", quality=70, optimize=False, progressive=False, subsampling=2)


def _quadrant_webp() -> bytes:
    size = 256
    image = Image.new("RGB", (size, size))
    colors = [(40, 90, 200), (230, 80, 160), (250, 220, 90), (90, 220, 200)]
    image.putdata([colors[(y >= size // 2) * 2 + (x >= size // 2)] for y in range(size) for x in range(size)])
    return _encode(image, "WEBP", lossless=True, quality=100, method=4, exact=True)


def _pdf() -> bytes:
    """A minimal valid one-page PDF with a correct cross-reference table."""
    content = b"BT /F1 12 Tf 10 45 Td (Synthetic reference fixture) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 240 100] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def media_files() -> dict[str, bytes]:
    wiki_cover = _pattern(600)
    return {
        "cosmic": _png(wiki_cover),  # (a) same bytes on Miraheze and Fandom
        "cosmic_crop": _png(wiki_cover.crop((100, 100, 400, 400))),  # (b) distinct crop, same variant group
        "cosmic_artist": _png(_pattern(1200)),  # (c) larger artist original
        "kousa": _gradient_jpeg(),  # (i) non-ASCII title, JPEG
        "background": _png(_bands(640, 360)),  # (e) unknown illustrator, unmapped
        "background_old": _png(_bands(480, 270, offset=3)),  # (k) earlier upload under the same File: title
        "moonlight": _quadrant_webp(),  # (h) WEBP served behind a .png URL
        "press_kit": _pdf(),  # (g) reference PDF, archive tier
        "screenshot": _png(_lanes(320, 180)),  # (l) raster gameplay screenshot, archive tier
        "character_gif": _animated_gif(),  # (n) animated GIF
    }


# --- Records ----------------------------------------------------------------------------------------------------------

def _measure(data: bytes) -> dict:
    fmt = {b"\x89P": "PNG", b"\xff\xd8": "JPEG", b"RI": "WEBP", b"%P": "PDF", b"GI": "GIF"}[data[:2]]
    width = height = None
    if fmt != "PDF":
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    return {"format": fmt, "width": width, "height": height, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "sha1": hashlib.sha1(data).hexdigest()}


def _fake_sha1(label: str) -> str:
    return hashlib.sha1(f"fixture: {label}".encode()).hexdigest()


def _asset(spec: dict, data: bytes, files: dict[str, bytes]) -> dict:
    """An Asset record from a scenario spec; measured fields come from the bytes, comparisons are computed."""
    info = _measure(data)
    source_id = spec["source_id"]
    family = schema.source_family(source_id)
    path = paths.asset_relpath(family, paths.source_slug(source_id), spec["original_filename"], info["sha256"],
                               schema.FORMAT_EXT[info["format"]])
    files[path] = data
    reported = schema.make_source_reported(**spec.get("reported", {}))
    observed = schema.make_download_observed(info, spec["content_type"])
    record = {
        "id": schema.asset_id(source_id, info["sha256"]), "source_id": source_id,
        "candidate_id": schema.candidate_id(source_id, spec["key"]), "title": spec["title"], "kind": spec["kind"],
        "game": schema.GAME, "page_url": spec["page_url"], "download_url": spec["download_url"],
        "resolved_url": spec.get("resolved_url", spec["download_url"]), "path": path,
        "original_filename": spec["original_filename"], "bytes": info["bytes"], "sha256": info["sha256"],
        "sha1": info["sha1"], "format": info["format"], "mime": schema.FORMAT_MIME[info["format"]],
        "width": info["width"], "height": info["height"], "fetched_at": FETCHED,
        "illustrators": spec.get("illustrators", []), "composers": spec.get("composers", []),
        "song_titles": spec.get("song_titles", []), "song_ids": spec.get("song_ids", []),
        "collections": spec.get("collections", []), "mapping_status": spec["mapping_status"],
        "mapping_evidence": spec.get("mapping_evidence", []), "quality": spec["quality"],
        "rights": schema.default_rights(creator=spec.get("creator"), url=spec["page_url"]),
        "source_reported": reported, "download_observed": observed,
        "comparisons": schema.compute_comparisons(reported, observed),
    }
    record.update(spec.get("extra", {}))
    return record


def _reported(info: dict, url: str, mime: str, **overrides: object) -> dict:
    values = {"url": url, "mime": mime, "bytes": info["bytes"], "width": info["width"], "height": info["height"],
              "sha1": info["sha1"], "timestamp": "2025-01-14T00:00:00Z", "revision": 1001}
    values.update(overrides)
    return values


def _wiki_quality(notes: str = "") -> dict:
    return {"source_variant": "site_original", "origin_status": "wiki_copy", "master_status": "unverified",
            "notes": notes}


def _wiki_spec(source_id: str, title: str, filename: str, kind: str, **fields: object) -> dict:
    base, cdn = (MIRAHEZE, MIRAHEZE_CDN) if source_id == "wikis:miraheze" else (FANDOM, FANDOM_CDN)
    key = "File:" + filename.replace(" ", "_")
    spec = {"source_id": source_id, "key": key, "title": title, "kind": kind, "page_url": f"{base}/wiki/{key}",
            "download_url": f"{cdn}/a/a1/{filename.replace(' ', '_')}", "original_filename": filename,
            "quality": _wiki_quality(), "content_type": "image/png"}
    spec.update(fields)
    return spec


def song_ids() -> dict[str, str]:
    titles = ["Cosmic Pulse", "交差して歪む過去と未来", "Moonlight of Sand Castle", "Fixture Song Without Artwork",
              "Fixture Universe-only Song"]
    return dict(zip(titles, paths.assign_song_ids(titles), strict=True))


def build_assets(media: dict[str, bytes], files: dict[str, bytes]) -> dict[str, dict]:
    ids = song_ids()
    cosmic = {"song_titles": ["Cosmic Pulse"], "song_ids": [ids["Cosmic Pulse"]], "illustrators": ["ariiol"],
              "composers": ["Yunosuke"], "collections": ["Event"], "creator": "ariiol"}
    wiki_evidence = [{"url": f"{MIRAHEZE}/wiki/Cosmic_Pulse", "field": "Template:Song|image",
                      "quote": "image = Cosmic_Pulse.png"}]
    info = {name: _measure(data) for name, data in media.items()}
    fandom_cosmic_url = f"{FANDOM_CDN}/a/a1/Cosmic_Pulse.png/revision/latest?cb=20250114000000"
    moonlight_url = f"{FANDOM_CDN}/a/a1/Moonlight_of_Sand_Castle.png/revision/latest?cb=20250114000000"
    old_background_url = f"{MIRAHEZE_CDN}/archive/a/a1/20240601000000!Dynamix_background_03.png"
    specs = {
        "W1": _wiki_spec("wikis:miraheze", "Cosmic Pulse", "Cosmic Pulse.png", "song_art", **cosmic,
                         mapping_status="confirmed_wiki", mapping_evidence=wiki_evidence,
                         reported=_reported(info["cosmic"], f"{MIRAHEZE_CDN}/a/a1/Cosmic_Pulse.png", "image/png"),
                         extra={"variant_group": COVER_GROUP, "variant_role": "wiki-copy"}),
        "W2": _wiki_spec("wikis:miraheze", "Cosmic Pulse (crop)", "Cosmic Pulse (crop).png", "song_art", **cosmic,
                         mapping_status="inferred",
                         mapping_evidence=[{"url": f"{MIRAHEZE}/wiki/File:Cosmic_Pulse_(crop).png",
                                            "quote": "file name and crop of File:Cosmic_Pulse.png; not used by "
                                                     "Template:Song (synthetic)"}],
                         reported=_reported(info["cosmic_crop"], f"{MIRAHEZE_CDN}/a/a1/Cosmic_Pulse_(crop).png",
                                            "image/png"),
                         extra={"variant_group": COVER_GROUP, "variant_role": "crop"}),
        "W3": _wiki_spec("wikis:miraheze", "交差して歪む過去と未来", "交差して歪む過去と未来.jpg", "song_art",
                         song_titles=["交差して歪む過去と未来"], song_ids=[ids["交差して歪む過去と未来"]],
                         mapping_status="confirmed_wiki", content_type="image/jpeg",
                         mapping_evidence=[{"url": f"{MIRAHEZE}/wiki/交差して歪む過去と未来",
                                            "field": "Template:Song|image",
                                            "quote": "image = 交差して歪む過去と未来.jpg"}],
                         reported=_reported(info["kousa"], f"{MIRAHEZE_CDN}/a/a1/交差して歪む過去と未来.jpg",
                                            "image/jpeg")),
        "W4": _wiki_spec("wikis:miraheze", "Dynamix background 03", "Dynamix background 03.png", "background",
                         mapping_status="unmapped",
                         reported=_reported(info["background"], f"{MIRAHEZE_CDN}/a/a1/Dynamix_background_03.png",
                                            "image/png"),
                         extra={"upstream_status": "current", "upstream_revision": 1001}),
        "W5": _wiki_spec("wikis:miraheze", "Dynamix background 03", "Dynamix background 03.png", "background",
                         mapping_status="unmapped", download_url=old_background_url,
                         reported=_reported(info["background_old"], old_background_url, "image/png",
                                            timestamp="2024-06-01T00:00:00Z", revision=1000),
                         extra={"upstream_status": "superseded",
                                "superseded_by": schema.asset_id("wikis:miraheze", info["background"]["sha256"]),
                                "upstream_revision": 1000,
                                "notes": "Earlier upload of File:Dynamix_background_03.png, replaced upstream by a "
                                         "re-upload under the same title; archived bytes kept."}),
        "F1": _wiki_spec("wikis:fandom", "Cosmic Pulse", "Cosmic Pulse.png", "song_art", **cosmic,
                         mapping_status="confirmed_wiki", mapping_evidence=wiki_evidence,
                         download_url=fandom_cosmic_url + "&format=original",
                         reported=_reported(info["cosmic"], fandom_cosmic_url, "image/png",
                                            bytes=info["cosmic"]["bytes"] + 137, sha1=_fake_sha1("Fandom API sha1")),
                         extra={"variant_group": COVER_GROUP, "variant_role": "wiki-copy",
                                "notes": "Fandom API size/SHA-1 differ from the format=original bytes, which equal "
                                         "the Miraheze file; kept as source_reported evidence, not a second version."}),
        "F2": _wiki_spec("wikis:fandom", "Moonlight of Sand Castle", "Moonlight of Sand Castle.png", "song_art",
                         song_titles=["Moonlight of Sand Castle"], song_ids=[ids["Moonlight of Sand Castle"]],
                         mapping_status="confirmed_wiki", download_url=moonlight_url, content_type="image/webp",
                         mapping_evidence=[f"{FANDOM}/wiki/Moonlight_of_Sand_Castle"],
                         quality={**_wiki_quality("CDN-transformed WEBP delivered for a .png URL; the stored "
                                                  "original was not obtained."), "source_variant": "unknown"},
                         reported=_reported(info["moonlight"], moonlight_url, "image/png",
                                            bytes=info["moonlight"]["bytes"] + 2048, sha1=_fake_sha1("Fandom PNG")),
                         extra={"transformed": True}),
        "A1": {"source_id": "artists:x:ariiol_", "key": "1000000000000000001:1", "title": SCRIPT_TITLE,
               "kind": "song_art", "page_url": X_POST, "original_filename": "FIXTUREcosmic.png",
               "download_url": "https://pbs.twimg.com/media/FIXTUREcosmic?format=png&name=orig",
               "content_type": "image/png", **cosmic, "composers": [], "mapping_status": "confirmed_primary",
               "mapping_evidence": [{"url": X_POST, "quote": "Dynamix - Cosmic Pulse (synthetic)"}],
               "quality": {"source_variant": "site_original", "origin_status": "artist_post",
                           "master_status": "unverified", "notes": "Largest rendition the post offers; not "
                                                                   "confirmed as the artist master."},
               "reported": {"url": "https://pbs.twimg.com/media/FIXTUREcosmic?format=png&name=orig"},
               "extra": {"variant_group": COVER_GROUP, "variant_role": "artist-original",
                         "credit_evidence": [{"role": "illustrator", "name": "ariiol", "url": X_POST,
                                              "field": "post author", "quote": None}]}},
        "A2": {"source_id": "artists:x:ariiol_", "key": "1000000000000000003:1",
               "title": "Fixture character idle animation", "kind": "character", "page_url": X_GIF_POST,
               "original_filename": "FIXTUREidle.gif", "download_url": "https://pbs.twimg.com/media/FIXTUREidle.gif",
               "content_type": "image/gif", "illustrators": ["ariiol"], "creator": "ariiol",
               "mapping_status": "unmapped",
               "quality": {"source_variant": "site_original", "origin_status": "artist_post",
                           "master_status": "unverified", "notes": "Animated GIF as posted; first frame is the "
                                                                   "preview."},
               "reported": {"url": "https://pbs.twimg.com/media/FIXTUREidle.gif", "mime": "image/gif"},
               "extra": {"credit_evidence": [{"role": "illustrator", "name": "ariiol", "url": X_GIF_POST,
                                              "field": "post author", "quote": None}]}},
        "R2": {"source_id": "archives:hkstp", "key": "press-kit-2014-screenshot.png",
               "title": "Synthetic gameplay screenshot (press kit)", "kind": "gameplay_screenshot",
               "page_url": f"{HKSTP}/press-kit", "download_url": f"{HKSTP}/press-kit-2014-screenshot.png",
               "content_type": "image/png", "original_filename": "press-kit-2014-screenshot.png",
               "mapping_status": "unmapped",
               "quality": {"source_variant": "site_original", "origin_status": "unknown",
                           "master_status": "unverified", "notes": ""},
               "reported": {"mime": "image/png", "bytes": info["screenshot"]["bytes"]}},
        "R1": {"source_id": "archives:hkstp", "key": "press-kit-2014.pdf", "title": "Synthetic press kit (reference)",
               "kind": "reference", "page_url": f"{HKSTP}/press-kit",
               "download_url": f"{HKSTP}/press-kit-2014.pdf", "content_type": "application/pdf",
               "original_filename": "press-kit-2014.pdf", "mapping_status": "unmapped",
               "quality": {"source_variant": "site_original", "origin_status": "unknown",
                           "master_status": "unverified", "notes": ""},
               "reported": {"mime": "application/pdf", "bytes": info["press_kit"]["bytes"]}},
    }
    media_for = {"W1": "cosmic", "W2": "cosmic_crop", "W3": "kousa", "W4": "background", "W5": "background_old",
                 "F1": "cosmic", "F2": "moonlight", "A1": "cosmic_artist", "A2": "character_gif", "R1": "press_kit",
                 "R2": "screenshot"}
    return {name: _asset(spec, media[media_for[name]], files) for name, spec in specs.items()}


def _candidate(asset: dict | None = None, **fields: object) -> dict:
    if asset is not None:
        source_id = asset["source_id"]
        key = asset["candidate_id"][len(source_id) + 1:]
        base = {"page_url": asset["page_url"], "file_url": asset["download_url"], "title": asset["title"],
                "role": asset["kind"], "game": "dynamix", "game_evidence": [asset["page_url"]],
                "source_reported": asset["source_reported"], "disposition": "downloaded", "asset_id": asset["id"]}
        if asset["song_titles"]:
            base["song_titles"] = asset["song_titles"]
    else:
        source_id, key, base = str(fields.pop("source_id")), str(fields.pop("key")), {}
    record = {"id": schema.candidate_id(source_id, key), "source_id": source_id, "upstream_key": key,
              "file_url": None, "role": None, "source_reported": schema.make_source_reported(), **base, **fields}
    return schema.canonical(record, "candidate")


def build_discoveries(assets: dict[str, dict]) -> dict[str, dict]:
    def wiki_extra(source_id: str, name: str, **fields: object) -> dict:
        base = MIRAHEZE if source_id == "wikis:miraheze" else FANDOM
        key = "File:" + name
        return _candidate(source_id=source_id, key=key, page_url=f"{base}/wiki/{key}", title=name.rsplit(".", 1)[0],
                          **fields)

    miraheze = [
        _candidate(assets["W1"]), _candidate(assets["W2"]), _candidate(assets["W3"]), _candidate(assets["W4"]),
        wiki_extra("wikis:miraheze", "Cosmic_Pulse_copy.png", role="song_art", game="dynamix",
                   game_evidence=[f"{MIRAHEZE}/wiki/Cosmic_Pulse"], disposition="duplicate_bytes",
                   reason="identical SHA-256 to File:Cosmic_Pulse.png", asset_id=assets["W1"]["id"]),
        wiki_extra("wikis:miraheze", "Cosmic_Pulse_UV_cover.png", role="song_art", game="dynamix_universe",
                   game_evidence=["Category:Dynamix Universe covers (synthetic)"], disposition="excluded",
                   reason="universe_only: square Dynamix Universe cover"),
        wiki_extra("wikis:miraheze", "Character_idle.gif", role="character", game="dynamix",
                   game_evidence=["Category:Characters (synthetic)"], disposition="excluded",
                   reason="animated_capture: 250 px animated GIF capture"),
        wiki_extra("wikis:miraheze", "Song_select_screen.png", game="unknown", game_evidence=[],
                   disposition="pending", reason="unresolved_role: classification pending manual review"),
    ]
    fandom = [
        _candidate(assets["F1"]), _candidate(assets["F2"]),
        wiki_extra("wikis:fandom", "Broken_Upload.png", role="song_art", game="dynamix",
                   game_evidence=[f"{FANDOM}/wiki/Broken_Upload"], disposition="failed",
                   reason="download failed: HTTP 503 after 3 attempts",
                   file_url=f"{FANDOM_CDN}/0/00/Broken_Upload.png/revision/latest?format=original"),
    ]
    artist = [
        _candidate(assets["A1"]), _candidate(assets["A2"]),
        _candidate(source_id="artists:x:ariiol_", key="1000000000000000002:1",
                   page_url="https://x.com/ariiol_/status/1000000000000000002", title="Unrelated portfolio piece",
                   game="not_dynamix", game_evidence=["post text names another project (synthetic)"],
                   disposition="excluded", reason="not_dynamix: unrelated portfolio illustration"),
    ]
    archive = [_candidate(assets["R1"]), _candidate(assets["R2"])]
    facebook = [_candidate(source_id="official:facebook", key="1000000000000001:1",
                           page_url=f"{FACEBOOK}/posts/1000000000000001", title="Synthetic official announcement image",
                           role="promotional", game="unknown", game_evidence=[], disposition="blocked",
                           reason="challenge: HTTP 403 bot/login interstitial; manual access required",
                           file_url=FACEBOOK_PHOTO)]
    scope = {"api": f"{MIRAHEZE}/w/api.php", "lists": ["allimages", "Category:Songs"], "notes": SYNTHETIC}
    snapshots = {
        "wikis:miraheze": (miraheze, scope, {"allimages": None}, True),
        "wikis:fandom": (fandom, {**scope, "api": f"{FANDOM}/api.php"}, {"allimages": "Zeta_file.png"}, False),
        "artists:x:ariiol_": (artist, {"lists": ["seed-posts"], "notes": SYNTHETIC}, {"seed-posts": None}, True),
        "archives:hkstp": (archive, {"lists": ["seed-urls"], "notes": SYNTHETIC}, {"seed-urls": None}, True),
        "official:facebook": (facebook, {"lists": ["seed-posts", "page-feed"], "notes": SYNTHETIC},
                              {"page-feed": None, "seed-posts": None}, False),
    }
    errors = {"wikis:fandom": {"phase": "discover", "url": f"{FANDOM}/api.php",
                               "message": "run stopped at --limit; continuation kept"},
              "official:facebook": {"phase": "discover", "url": FACEBOOK,
                                    "message": "page feed not enumerated: HTTP 403 challenge (login/bot interstitial)"}}
    return {source_id: manifest.sort_discovery({
        "schema_version": schema.SCHEMA_VERSION, "source_id": source_id, "started_at": STARTED,
        "finished_at": FINISHED, "scope": scope_value, "continuation": continuation, "discovery_complete": complete,
        "candidates": candidates, "errors": [errors[source_id]] if source_id in errors else [],
    }) for source_id, (candidates, scope_value, continuation, complete) in snapshots.items()}


def _source(source_id: str, name: str, url: str, platform: str, status: str, **optional: object) -> dict:
    return {"id": source_id, "name": name, "family": schema.source_family(source_id), "url": url,
            "platform": platform, "status": status, "checked_at": FETCHED, "notes": SYNTHETIC, **optional}


def build_manifests(assets: dict[str, dict], discoveries: dict[str, dict]) -> dict[str, dict]:
    failure = schema.failure_record(
        source_id="wikis:fandom", candidate_id="wikis:fandom:File:Broken_Upload.png", phase="download",
        url=f"{FANDOM_CDN}/0/00/Broken_Upload.png/revision/latest?format=original", attempted_at=FETCHED, attempts=3,
        error_class="http_5xx", http_status=503, retriable=True, manual=False, message="HTTP 503")
    blocked = schema.failure_record(
        source_id="official:facebook", candidate_id="official:facebook:1000000000000001:1", phase="download",
        url=FACEBOOK_PHOTO, attempted_at=FETCHED, attempts=1, error_class="challenge", http_status=403,
        retriable=False, manual=True, message="challenge page (HTTP 403)")
    failures = {"wikis": [failure], "artists": [], "official": [blocked], "archives": []}
    sources = {
        "wikis": [_source("wikis:miraheze", "Dynamix Wiki (Miraheze)", f"{MIRAHEZE}/wiki/Main_Page", "mediawiki",
                          "available", api_url=f"{MIRAHEZE}/w/api.php", evidence_level="live_api"),
                  _source("wikis:fandom", "Dynamix Wiki (Fandom)", f"{FANDOM}/wiki/Dynamix_Wiki", "mediawiki",
                          "partially_available", api_url=f"{FANDOM}/api.php", evidence_level="live_api")],
        "artists": [_source("artists:x:ariiol_", "ariiol (X)", "https://x.com/ariiol_", "x", "available",
                            evidence_level="page_read")],
        "official": [_source("official:apple-store", "App Store listing", "https://apps.apple.com/app/id000000000",
                             "app-store", "metadata_only", evidence_level="page_read"),
                     _source("official:facebook", "Official Facebook page", FACEBOOK, "web", "manual_access",
                             evidence_level="blocked")],
        "archives": [_source("archives:hkstp", "HKSTP press archive", HKSTP, "web", "available",
                             evidence_level="page_read")],
    }
    result = {}
    for family in schema.FAMILIES:
        own_assets = [asset for asset in assets.values() if schema.source_family(asset["source_id"]) == family]
        family_sources = []
        for source in sources[family]:
            snapshot = discoveries.get(source["id"])
            if snapshot is not None:
                source = {**source, "discovery_complete": snapshot["discovery_complete"],
                          "candidate_count": len(snapshot["candidates"]),
                          "asset_count": sum(asset["source_id"] == source["id"] for asset in own_assets),
                          "failure_count": sum(item["source_id"] == source["id"] for item in failures[family])}
            family_sources.append(source)
        result[family] = manifest.sort_manifest({
            "schema_version": schema.SCHEMA_VERSION, "family": family, "fetched_at": FETCHED if own_assets else None,
            "sources": family_sources, "assets": own_assets, "failures": failures[family]})
    return result


def build_song_index(assets: dict[str, dict]) -> dict:
    ids = song_ids()
    artwork = {song_id: sorted(asset["id"] for asset in assets.values() if song_id in asset["song_ids"])
               for song_id in ids.values()}

    def song(title: str, *, in_game: bool = True, universe: bool = False, **fields: object) -> dict:
        page = f"{MIRAHEZE}/wiki/{title.replace(' ', '_')}"
        return {"id": ids[title], "title": title, "aliases": [], "composers": [], "illustrators": [],
                "collections": [], "in_original_game": in_game,
                "inclusion_evidence": [f"{page} (Template:Song; synthetic)"] if in_game else [],
                "universe_also": universe, "removed": False,
                "evidence": [{"source_id": "wikis:miraheze", "url": page, "revision": 1001}],
                "artwork_asset_ids": artwork[ids[title]], **fields}

    songs = [song("Cosmic Pulse", universe=True, composers=["Yunosuke"], illustrators=["ariiol"],
                  collections=["Event"]),
             song("交差して歪む過去と未来"), song("Moonlight of Sand Castle"), song("Fixture Song Without Artwork"),
             song("Fixture Universe-only Song", in_game=False, universe=True)]
    return manifest.sort_song_index({
        "schema_version": schema.SCHEMA_VERSION,
        "generated_from": ["data/discovery/fandom.json", "data/discovery/miraheze.json"], "as_of": "2026-10-10",
        "songs": songs,
        "disagreements": [{"song_id": ids["Moonlight of Sand Castle"], "field": "collections",
                           "values": {"wikis:miraheze": ["Fixture Pack"], "wikis:fandom": []}}]})


def build(output: Path) -> list[str]:
    """Write the fixture under ``output``; returns the written relative paths (sorted)."""
    files: dict[str, bytes] = {}
    assets = build_assets(media_files(), files)
    discoveries = build_discoveries(assets)
    for family, value in build_manifests(assets, discoveries).items():
        files[paths.manifest_relpath(family)] = manifest.dumps(value).encode("utf-8")
    for source_id, snapshot in discoveries.items():
        files[paths.discovery_relpath(source_id)] = manifest.dumps(snapshot).encode("utf-8")
    files["data/sources/song-index.json"] = manifest.dumps(build_song_index(assets)).encode("utf-8")
    for rel, data in sorted(files.items()):
        target = paths.safe_path(output, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return sorted(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, default=HERE, help="fixture root to write (default: %(default)s)")
    args = parser.parse_args(argv)
    for rel in build(args.output):
        print(rel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
