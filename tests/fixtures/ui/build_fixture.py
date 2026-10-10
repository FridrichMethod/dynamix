"""Regenerate the synthetic UI fixture: a complete mini site data set in the frozen catalog shape (schema version 1).

    python -I tests/fixtures/ui/build_fixture.py          # rewrite the fixture
    python -I tests/fixtures/ui/build_fixture.py --check  # exit 1 if the committed fixture differs

Everything is synthetic: geometric images drawn here, fictional titles, people and example.org/example.com URLs. It
exercises the archive and slideshow (duplicate provenance with source mismatches, a variant pair, a non-ASCII title,
HTML/script injection strings, unknown credits, a character cut-out, archive-only files including a PDF, a small
low-resolution file, failures and an empty catalog). It is not evidence about any real artwork or person.

The outputs follow docs/implementation/09-frozen-contracts.md: catalog.json (UTF-8, indent 2, trailing newline),
catalog.js (the frozen reference encoding: `window.DYNAMIX_CATALOG = ` + compact JSON, `separators=(",", ":")`,
`ensure_ascii=False`, with every `<` written as \\u003c and U+2028/U+2029 escaped + `;\\n`), originals under
assets/public/<family>/<source-slug>/<slug>--<sha256[:12]>.<ext> and WebP previews (long edge 480, never enlarged,
quality 75, method 6) under assets/thumbs/<sha256[:16]>.webp. Slugs, asset paths, song IDs (with the title-hash suffix
for non-ASCII titles) and comparisons come from scripts/lib, the executable contract, which also validates the result
(tests/test_layout_fixture.py).
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))

from lib import paths, schema  # noqa: E402 - the contract library lives in scripts/lib
OWNER, GAME, REPOSITORY = "FridrichMethod", "Dynamix", "https://github.com/FridrichMethod/dynamix"
KIND_ORDER = ["song_art", "pack_cover", "character", "background", "part", "promotional", "store_screenshot",
              "gameplay_screenshot", "process", "reference"]
SITE_KINDS = {"song_art", "pack_cover", "character", "background", "part", "promotional", "process"}
SLIDESHOW_KINDS = {"song_art", "pack_cover", "promotional", "character"}
FAMILY_ORDER = ["artists", "official", "wikis", "archives"]
MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif", "PDF": "application/pdf"}
EXTENSION = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif", "PDF": "pdf"}
HOLDER = "C4Cat Entertainment Limited and/or the credited artist"
INJECTION = "<img src=x onerror=alert(1)>"
FETCHED = "2026-10-10T12:00:00Z"

SOURCES = [
    {"id": "artists:x:fixture-artist", "name": "Fixture Artist A (X)", "family": "artists", "url": "https://example.org/artist-a",
     "platform": "x", "status": "available", "checked_at": FETCHED, "notes": "Synthetic UI fixture source (artist posts).",
     "discovery_complete": True, "evidence_level": "page_read"},
    {"id": "official:apple-store", "name": "App Store listing (fixture)", "family": "official", "url": "https://example.com/app-store",
     "platform": "app-store", "status": "available", "checked_at": FETCHED, "notes": "Synthetic UI fixture source (store screenshots)."},
    {"id": "official:website", "name": "Official website (fixture)", "family": "official", "url": "https://example.com/official",
     "platform": "web", "status": "temporarily_unavailable", "checked_at": FETCHED,
     "notes": "Synthetic: the site timed out on every attempt; nothing is inferred from the failure.", "discovery_complete": False},
    {"id": "wikis:miraheze", "name": "Fixture Wiki (MediaWiki A)", "family": "wikis", "url": "https://example.org/wiki-a",
     "platform": "mediawiki", "status": "available", "checked_at": FETCHED, "notes": "Synthetic UI fixture wiki; allimages enumerated to the end.",
     "api_url": "https://example.org/wiki-a/api.php", "discovery_complete": True, "evidence_level": "live_api"},
    {"id": "wikis:fandom", "name": "Fixture Wiki (MediaWiki B)", "family": "wikis", "url": "https://example.org/wiki-b",
     "platform": "mediawiki", "status": "partially_available", "checked_at": FETCHED,
     "notes": "Synthetic: the CDN serves transformed WEBP for some PNG names; enumeration stopped at a continuation token.",
     "discovery_complete": False, "evidence_level": "live_api"},
    {"id": "archives:wayback", "name": "Web archive snapshots (fixture)", "family": "archives", "url": "https://example.org/archive",
     "platform": "web", "status": "available", "checked_at": FETCHED, "notes": "Synthetic archived press pages and captures."},
]
SOURCE_SLUGS = {"artists:x:fixture-artist": "x-fixture-artist", "official:apple-store": "apple-store", "wikis:miraheze": "miraheze",
                "wikis:fandom": "fandom", "archives:wayback": "wayback"}


def draw(size, background, shapes, mode="RGB"):
    """A flat-colour picture: shapes are ("rect"|"ellipse"|"polygon", coordinates as fractions, colour)."""
    width, height = size
    image = Image.new(mode, size, background)
    canvas = ImageDraw.Draw(image)
    for shape, points, colour in shapes:
        scaled = [(x * width, y * height) for x, y in points]
        if shape == "rect":
            canvas.rectangle([scaled[0], scaled[1]], fill=colour)
        elif shape == "ellipse":
            canvas.ellipse([scaled[0], scaled[1]], fill=colour)
        else:
            canvas.polygon(scaled, fill=colour)
    return image


def encode(image, fmt, **options):
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    return buffer.getvalue()


def tiny_pdf(text):
    """A one-page PDF with valid cross-reference offsets (plain ASCII, no fonts embedded beyond Helvetica)."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def cutout():
    """A character cut-out: opaque art left of centre on a transparent 1000 x 1200 canvas, cut at the bottom edge."""
    image = Image.new("RGBA", (1000, 1200), (0, 0, 0, 0))
    canvas = ImageDraw.Draw(image)
    canvas.ellipse([260, 140, 560, 440], fill=(95, 212, 255, 255))
    canvas.polygon([(220, 1200), (600, 1200), (520, 470), (300, 470)], fill=(32, 36, 44, 255))
    canvas.rectangle([360, 520, 460, 700], fill=(255, 255, 255, 255))
    return image


# Each source file: what it is, how it is drawn/encoded, and the records (source_id plus record-specific fields) that
# hold exactly these bytes. The first record is the primary one (highest family priority).
def files():
    lattice = draw((1600, 1600), (24, 30, 46), [("ellipse", [(.18, .18), (.82, .82)], (95, 212, 255)),
                                                 ("polygon", [(.5, .1), (.9, .5), (.5, .9), (.1, .5)], (240, 244, 250)),
                                                 ("rect", [(.42, .42), (.58, .58)], (24, 30, 46))])
    bloom = draw((1200, 1200), (250, 236, 240), [("ellipse", [(.2, .2), (.8, .8)], (226, 92, 128)), ("ellipse", [(.38, .38), (.62, .62)], (255, 214, 102))])
    bloom_alt = draw((1000, 1400), (40, 44, 60), [("ellipse", [(.15, .2), (.85, .7)], (226, 92, 128)), ("rect", [(.1, .8), (.9, .86)], (255, 214, 102))])
    crossing = draw((1000, 1000), (238, 242, 247), [("polygon", [(0, 0), (1, 1), (1, .8), (.2, 0)], (88, 74, 160)), ("polygon", [(1, 0), (0, 1), (0, .8), (.8, 0)], (60, 170, 150))])
    night = draw((1000, 1000), (12, 14, 22), [("rect", [(.1, .6), (.9, .64)], (95, 212, 255)), ("ellipse", [(.6, .15), (.8, .35)], (250, 250, 210))])
    pack = draw((1000, 1000), (32, 36, 44), [("rect", [(.08, .08), (.92, .92)], (95, 212, 255)), ("rect", [(.16, .16), (.84, .84)], (32, 36, 44)), ("polygon", [(.5, .25), (.75, .5), (.5, .75), (.25, .5)], (255, 255, 255))])
    store = draw((600, 1067), (246, 248, 251), [("rect", [(0, 0), (1, .12)], (32, 36, 44)), ("rect", [(.08, .2), (.92, .55)], (95, 212, 255)), ("rect", [(.08, .62), (.92, .66)], (180, 186, 196))])
    gameplay = draw((960, 540), (10, 12, 20), [("rect", [(.05, 0), (.08, 1)], (95, 212, 255)), ("rect", [(.92, 0), (.95, 1)], (95, 212, 255)), ("rect", [(.3, .9), (.7, .93)], (95, 212, 255))])
    scan = draw((800, 1100), (236, 230, 214), [("rect", [(.1, .08), (.9, .12)], (60, 60, 60)), ("rect", [(.1, .18), (.8, .2)], (120, 120, 120)), ("rect", [(.1, .24), (.85, .26)], (120, 120, 120))])
    tiny = draw((200, 200), (255, 214, 102), [("ellipse", [(.2, .2), (.8, .8)], (32, 36, 44))])
    mh, fd = "https://example.org/wiki-a", "https://example.org/wiki-b"
    return [
        {"key": "lattice", "title": "Aurora Lattice", "kind": "song_art", "name": "Aurora_Lattice.png", "bytes": encode(lattice, "PNG", optimize=True),
         "illustrators": ["Fixture Artist A"], "composers": ["Fixture Composer"], "songs": ["Aurora Lattice"], "collections": ["Fixture Pack Vol. 1"],
         "records": [
             {"source_id": "artists:x:fixture-artist", "page_url": "https://example.org/artist-a/status/1001",
              "download_url": "https://example.org/media/aurora-lattice.png?name=orig", "quality": ["site_original", "artist_post", "confirmed", "The artist's post links this file as the full-size jacket."],
              "mapping_status": "confirmed_primary", "mapping_evidence": [{"url": "https://example.org/artist-a/status/1001", "quote": "Jacket illustration for Aurora Lattice"}],
              "credit_evidence": [{"role": "illustrator", "name": "Fixture Artist A", "url": "https://example.org/artist-a/status/1001", "field": "post text", "quote": "I drew the jacket for Aurora Lattice"},
                                  {"role": "other", "name": "Fixture Designer", "url": None, "field": "post text", "quote": "logo by Fixture Designer"}],
              "reported": {}, "creator": "Fixture Artist A", "collections": []},
             {"source_id": "wikis:miraheze", "page_url": f"{mh}/File:Aurora_Lattice.png", "download_url": f"{mh}/images/a/a1/Aurora_Lattice.png",
              "quality": ["site_original", "wiki_copy", "unverified", ""], "mapping_status": "confirmed_wiki",
              "mapping_evidence": [f"{mh}/Aurora_Lattice", "Template:Song image field names this file"], "reported": {"exact": True},
              "upstream_revision": "4021"},
             {"source_id": "wikis:fandom", "page_url": f"{fd}/File:Aurora_Lattice.png", "download_url": f"{fd}/images/Aurora_Lattice.png/revision/latest?format=original",
              "quality": ["site_original", "wiki_copy", "unverified", "The API size and SHA-1 differ from the bytes the CDN serves with format=original."],
              "mapping_status": "confirmed_wiki", "mapping_evidence": [{"url": f"{fd}/Aurora_Lattice", "field": "Template:Song|image"}],
              "reported": {"sha1": "0f" * 20, "bytes_delta": 18}},
         ]},
        {"key": "bloom", "title": "Signal Bloom", "kind": "song_art", "name": "Signal_Bloom.png", "bytes": encode(bloom, "PNG", optimize=True),
         "illustrators": ["Fixture Artist B"], "composers": ["Fixture Composer"], "songs": ["Signal Bloom"], "collections": [],
         "variant_group": "song:signal-bloom", "variant_role": "original jacket", "upstream_status": "superseded", "superseded_by": "bloom_alt",
         "records": [{"source_id": "wikis:miraheze", "page_url": f"{mh}/File:Signal_Bloom.png", "download_url": f"{mh}/images/b/b2/Signal_Bloom.png",
                      "quality": ["site_original", "wiki_copy", "unverified", ""], "mapping_status": "confirmed_wiki",
                      "mapping_evidence": [f"{mh}/Signal_Bloom"], "reported": {"exact": True}}]},
        {"key": "bloom_alt", "title": "Signal Bloom (Alternate jacket)", "kind": "song_art", "name": "Signal_Bloom_alt.jpg",
         "bytes": encode(bloom_alt, "JPEG", quality=60, optimize=True),
         "illustrators": ["Fixture Artist B"], "composers": ["Fixture Composer"], "songs": ["Signal Bloom"], "collections": [],
         "variant_group": "song:signal-bloom", "variant_role": "alternate jacket", "upstream_status": "current",
         "records": [{"source_id": "wikis:miraheze", "page_url": f"{mh}/File:Signal_Bloom_alt.jpg", "download_url": f"{mh}/images/c/c3/Signal_Bloom_alt.jpg",
                      "quality": ["site_original", "wiki_copy", "unverified", ""], "mapping_status": "confirmed_wiki",
                      "mapping_evidence": [f"{mh}/Signal_Bloom"], "reported": {"exact": True}}]},
        {"key": "crossing", "title": "交差して歪む過去と未来", "kind": "song_art", "name": "交差して歪む過去と未来.png",
         "bytes": encode(crossing, "WEBP", quality=60, method=6), "transformed": True,
         "illustrators": [], "composers": [], "songs": ["交差して歪む過去と未来"], "collections": [],
         "records": [{"source_id": "wikis:fandom", "page_url": f"{fd}/File:" + quote("交差して歪む過去と未来.png"),
                      "download_url": f"{fd}/images/" + quote("交差して歪む過去と未来.png") + "/revision/latest",
                      "quality": ["unknown", "wiki_copy", "unverified", "The CDN returned WEBP bytes for a .png name; no original was obtainable."],
                      "mapping_status": "inferred", "mapping_evidence": ["The file name repeats the song page title; no page links the file."],
                      "reported": {"mime": "image/png", "sha1": None}}]},
        {"key": "night", "title": f"Night {INJECTION} Drive </script>", "kind": "song_art", "name": "Night_Drive.png", "bytes": encode(night, "PNG", optimize=True),
         "illustrators": [INJECTION], "composers": ["\"Quote\" & 'Apostrophe' </script><script>alert(2)</script>"],
         "songs": [f"Night {INJECTION} Drive </script>"], "collections": ["</script><b>Pack</b>"], "notes": "<b>bold?</b> </script><script>alert(5)</script>",
         "records": [{"source_id": "wikis:miraheze", "page_url": f"{mh}/File:Night_Drive.png", "download_url": f"{mh}/images/d/d4/Night_Drive.png",
                      "quality": ["site_original", "wiki_copy", "unverified", "<i>not italic</i>"], "mapping_status": "inferred",
                      "mapping_evidence": [{"field": "Template:Song|artist", "quote": "<script>alert(4)</script>"}, "javascript:alert(6)"],
                      "reported": {"exact": True}, "restrictions": "<u>No reuse</u> </script>"}]},
        {"key": "cutout", "title": "Lane Keeper", "kind": "character", "name": "Lane_Keeper.png", "bytes": encode(cutout(), "PNG", optimize=True),
         "illustrators": ["Fixture Artist A"], "composers": [], "songs": [], "collections": [],
         "records": [{"source_id": "wikis:miraheze", "page_url": f"{mh}/File:Lane_Keeper.png", "download_url": f"{mh}/images/e/e5/Lane_Keeper.png",
                      "quality": ["site_original", "wiki_copy", "unverified", ""], "mapping_status": "confirmed_wiki",
                      "mapping_evidence": [f"{mh}/Characters"], "reported": {"exact": True}}]},
        {"key": "pack", "title": "Fixture Pack Vol. 1", "kind": "pack_cover", "name": "pack-vol-1.jpg", "bytes": encode(pack, "JPEG", quality=60, optimize=True),
         "illustrators": [], "composers": [], "songs": [], "collections": ["Fixture Pack Vol. 1"],
         "records": [{"source_id": "archives:wayback", "page_url": "https://example.org/archive/20150101/official/packs",
                      "download_url": "https://example.org/archive/20150101im_/official/img/pack-vol-1.jpg",
                      "quality": ["site_original", "official", "unverified", "Archived copy of the official site's pack page."],
                      "mapping_status": "confirmed_primary", "mapping_evidence": ["https://example.org/archive/20150101/official/packs"], "reported": {}}]},
        {"key": "store", "title": "App Store screenshot 1", "kind": "store_screenshot", "name": "screenshot-1.png", "bytes": encode(store, "PNG", optimize=True),
         "illustrators": [], "composers": [], "songs": [], "collections": [],
         "records": [{"source_id": "official:apple-store", "page_url": "https://example.com/app-store/app/fixture",
                      "download_url": "https://example.com/app-store/image/screenshot-1.png", "quality": ["preview", "official", "unverified", "The store serves resized screenshots."],
                      "mapping_status": "unmapped", "mapping_evidence": [], "reported": {"dims_only": True}}]},
        {"key": "gameplay", "title": "Gameplay capture (fixture)", "kind": "gameplay_screenshot", "name": "gameplay-capture.png", "bytes": encode(gameplay, "PNG", optimize=True),
         "illustrators": [], "composers": [], "songs": [], "collections": [],
         "records": [{"source_id": "archives:wayback", "page_url": "https://example.org/archive/20160101/press/review",
                      "download_url": "https://example.org/archive/20160101im_/press/capture.png", "quality": ["site_original", "repost", "unverified", ""],
                      "mapping_status": "unmapped", "mapping_evidence": [], "reported": {}}]},
        {"key": "scan", "title": "Interview scan (fixture)", "kind": "reference", "name": "interview-scan.jpg", "bytes": encode(scan, "JPEG", quality=55, optimize=True),
         "illustrators": [], "composers": [], "songs": [], "collections": [],
         "records": [{"source_id": "archives:wayback", "page_url": "https://example.org/archive/20160101/press/interview",
                      "download_url": "https://example.org/archive/20160101im_/press/interview-scan.jpg", "quality": ["site_original", "scan", "unverified", ""],
                      "mapping_status": "unmapped", "mapping_evidence": [], "reported": {}}]},
        {"key": "pdf", "title": "Press kit (fixture PDF)", "kind": "reference", "name": "press-kit.pdf", "bytes": tiny_pdf("Fixture press kit"),
         "illustrators": [], "composers": [], "songs": [], "collections": [],
         "records": [{"source_id": "archives:wayback", "page_url": "https://example.org/archive/20160101/press/kit",
                      "download_url": "https://example.org/archive/20160101id_/press/press-kit.pdf", "quality": ["site_original", "official", "unverified", ""],
                      "mapping_status": "unmapped", "mapping_evidence": [], "reported": {"mime": "application/pdf"}}]},
        {"key": "tiny", "title": "Tiny Jacket", "kind": "song_art", "name": "Tiny_Jacket.png", "bytes": encode(tiny, "PNG", optimize=True),
         "illustrators": ["Fixture Artist B"], "composers": ["Fixture Composer"], "songs": ["Tiny Jacket"], "collections": [],
         "records": [{"source_id": "wikis:fandom", "page_url": f"{fd}/File:Tiny_Jacket.png", "download_url": f"{fd}/images/Tiny_Jacket.png/revision/latest?format=original",
                      "quality": ["site_original", "wiki_copy", "unverified", "Only this 200 px copy is known."], "mapping_status": "confirmed_wiki",
                      "mapping_evidence": [f"{fd}/Tiny_Jacket"], "reported": {"exact": True}}]},
    ]


def measure(raw):
    if raw.startswith(b"%PDF-"):
        return "PDF", None, None
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        return image.format, image.width, image.height


def preview(raw, fmt):
    """The DEEMO-style preview: WebP, long edge 480, never enlarged, quality 75, method 6; first frame; None for PDFs."""
    if fmt == "PDF":
        return None
    with Image.open(io.BytesIO(raw)) as image:
        image.seek(0)
        frame = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    if max(frame.size) <= 480:
        return None
    frame.thumbnail((480, 480), Image.Resampling.LANCZOS)
    return encode(frame, "WEBP", quality=75, method=6)


def build():
    sources = {source["id"]: source for source in SOURCES}
    entries, blobs, keyed = [], {}, {}
    specs = files()
    # Song IDs as the reconciled song index assigns them: over the whole title set, in sorted exact-title order.
    titles = sorted({title for spec in specs for title in spec["songs"]})
    song_ids = dict(zip(titles, paths.assign_song_ids(titles)))
    for spec in specs:
        raw = spec["bytes"]
        fmt, width, height = measure(raw)
        sha256, sha1 = hashlib.sha256(raw).hexdigest(), hashlib.sha1(raw).hexdigest()
        primary = spec["records"][0]
        family = sources[primary["source_id"]]["family"]
        path = paths.asset_relpath(family, SOURCE_SLUGS[primary["source_id"]], spec["name"], sha256, EXTENSION[fmt])
        blobs[path] = raw
        thumb_bytes = preview(raw, fmt)
        thumb = f"assets/thumbs/{sha256[:16]}.webp" if thumb_bytes else None
        if thumb:
            blobs[thumb] = thumb_bytes
        keyed[spec["key"]] = f"{primary['source_id']}:{sha256[:16]}"
        entries.append((spec, raw, fmt, width, height, sha256, sha1, path, thumb))
    assets = []
    for spec, raw, fmt, width, height, sha256, sha1, path, thumb in entries:
        provenance, extra = [], []
        observed = {"content_type": "image/png" if spec.get("transformed") else MIME[fmt], "sniffed_format": fmt, "bytes": len(raw),
                    "width": width, "height": height, "sha1": sha1, "sha256": sha256}
        for record in spec["records"]:
            source = sources[record["source_id"]]
            reported = record["reported"]
            if reported.get("exact"):
                source_reported = {"url": record["download_url"], "mime": MIME[fmt], "bytes": len(raw), "width": width, "height": height,
                                   "sha1": sha1, "timestamp": "2024-05-01T00:00:00Z", "revision": None}
            elif "bytes_delta" in reported:
                source_reported = {"url": record["download_url"], "mime": MIME[fmt], "bytes": len(raw) + reported["bytes_delta"], "width": width,
                                   "height": height, "sha1": reported["sha1"], "timestamp": "2024-05-02T00:00:00Z", "revision": None}
            elif reported.get("dims_only"):
                source_reported = {"url": record["download_url"], "mime": None, "bytes": None, "width": 1242, "height": 2208, "sha1": None,
                                   "timestamp": None, "revision": None}
            elif "mime" in reported:
                source_reported = {"url": record["download_url"], "mime": reported["mime"], "bytes": None, "width": None, "height": None,
                                   "sha1": None, "timestamp": None, "revision": None}
            else:
                source_reported = {key: None for key in ("url", "mime", "bytes", "width", "height", "sha1", "timestamp", "revision")}
            comparisons = schema.compute_comparisons(source_reported, observed)
            variant, origin, master, quality_notes = record["quality"]
            superseded = keyed[spec["superseded_by"]] if spec.get("superseded_by") else None
            # Exactly the provenance fields of the frozen contract; record-only details stay in `extra`.
            extra.append({"id": f"{record['source_id']}:{sha256[:16]}", "candidate_id": f"{record['source_id']}:{spec['name'].replace(' ', '_')}",
                          "upstream_revision": record.get("upstream_revision")})
            provenance.append({
                "asset_id": f"{record['source_id']}:{sha256[:16]}",
                "source_id": record["source_id"], "source_name": source["name"], "family": source["family"],
                "page_url": record["page_url"], "download_url": record["download_url"], "resolved_url": record["download_url"],
                "quality": {"source_variant": variant, "origin_status": origin, "master_status": master, "notes": quality_notes},
                "rights": {"holder": HOLDER, "creator": record.get("creator"), "url": None, "license": None, "restrictions": record.get("restrictions")},
                "mapping_status": record["mapping_status"], "mapping_evidence": record["mapping_evidence"],
                "credit_evidence": record.get("credit_evidence", []),
                "illustrators": spec["illustrators"], "composers": spec["composers"], "song_titles": spec["songs"],
                # An unmapped record links no song (the manifest rule); the catalog merges every record's lists.
                "song_ids": [] if record["mapping_status"] == "unmapped" else [song_ids[title] for title in spec["songs"]],
                "collections": record.get("collections", spec["collections"]),
                "source_reported": source_reported, "comparisons": comparisons,
                "upstream_status": spec.get("upstream_status", "current"), "superseded_by": superseded, "notes": spec.get("notes"),
            })
        # Primary first, then by family priority, source ID and asset ID, as the catalog builder orders provenance.
        order = sorted(range(len(provenance)), key=lambda i: schema.provenance_sort_key(provenance[i]))
        provenance, extra = [provenance[i] for i in order], [extra[i] for i in order]
        primary = {**provenance[0], **extra[0]}
        site = spec["kind"] in SITE_KINDS and fmt != "PDF"
        gallery = fmt != "PDF" and spec["kind"] != "reference"
        url = schema.catalog_url(path)
        asset = {
            "id": primary["id"], "source_id": primary["source_id"], "candidate_id": primary["candidate_id"], "title": spec["title"],
            "kind": spec["kind"], "game": "dynamix", "page_url": primary["page_url"], "download_url": primary["download_url"],
            "resolved_url": primary["resolved_url"], "path": path, "original_filename": spec["name"], "bytes": len(raw), "sha256": sha256,
            "sha1": sha1, "format": fmt, "mime": MIME[fmt], "width": width, "height": height, "fetched_at": FETCHED,
            **{field: schema.ordered_union(record[field] for record in provenance)
               for field in ("illustrators", "composers", "song_titles", "song_ids", "collections")},
            "mapping_status": primary["mapping_status"], "mapping_evidence": primary["mapping_evidence"], "quality": primary["quality"],
            "rights": primary["rights"], "source_reported": primary["source_reported"],
            "download_observed": observed,
            "comparisons": primary["comparisons"], "credit_evidence": primary["credit_evidence"],
            "upstream_status": primary["upstream_status"],
        }
        for key in ("variant_group", "variant_role", "notes", "transformed"):
            if key in spec:
                asset[key] = spec[key]
        if primary["superseded_by"]:
            asset["superseded_by"] = primary["superseded_by"]
        if primary["upstream_revision"]:
            asset["upstream_revision"] = primary["upstream_revision"]
        asset.update({
            "family": primary["family"], "source_name": primary["source_name"], "url": url, "thumb": thumb, "gallery": gallery,
            "slideshow": gallery and spec["kind"] in SLIDESHOW_KINDS and max(width, height) >= 1000, "site_original": site,
            "archive_url": f"{REPOSITORY}/raw/main/{url}", "provenance": provenance, "variants": [],
        })
        assets.append(asset)
    groups = {}
    for asset in assets:
        if asset.get("variant_group"):
            groups.setdefault(asset["variant_group"], []).append(asset["id"])
    for asset in assets:
        asset["variants"] = sorted(other for other in groups.get(asset.get("variant_group"), []) if other != asset["id"])
    assets.sort(key=schema.catalog_sort_key)
    failures = [
        {"id": "wikis:fandom:File:Missing_Jacket.png:download", "source_id": "wikis:fandom", "candidate_id": "wikis:fandom:File:Missing_Jacket.png",
         "phase": "download", "url": "https://example.org/wiki-b/images/Missing_Jacket.png", "attempted_at": FETCHED, "attempts": 1,
         "error_class": "http_4xx", "http_status": 404, "retriable": False, "manual": False, "message": "HTTP 404 <not found> </script>"},
        {"id": "artists:x:fixture-artist:2002:p1:download", "source_id": "artists:x:fixture-artist", "candidate_id": "artists:x:fixture-artist:2002:p1",
         "phase": "download", "url": "https://example.org/media/locked.png", "attempted_at": FETCHED, "attempts": 1,
         "error_class": "challenge", "http_status": 403, "retriable": False, "manual": True, "message": "Login wall; recorded as blocked."},
    ]
    records = [record for asset in assets for record in asset["provenance"]]
    counts = {}
    for record in records:
        counts[record["source_id"]] = counts.get(record["source_id"], 0) + 1
    catalog_sources = [{**source, "asset_count": counts.get(source["id"], 0)} for source in SOURCES]
    by_family = {family: sum(1 for record in records if record["family"] == family) for family in FAMILY_ORDER}
    by_kind = {kind: sum(1 for asset in assets if asset["kind"] == kind) for kind in KIND_ORDER if any(asset["kind"] == kind for asset in assets)}
    catalog = {
        "schema_version": 1, "owner": OWNER, "game": GAME, "repository": REPOSITORY,
        "snapshots": {family: FETCHED for family in FAMILY_ORDER},
        "sources": catalog_sources, "assets": assets, "failures": failures,
        "summary": {
            "source_count": len(SOURCES), "source_asset_records": len(records), "unique_files": len(assets),
            "gallery_images": sum(asset["gallery"] for asset in assets), "slideshow_images": sum(asset["slideshow"] for asset in assets),
            "exact_duplicate_records": len(records) - len(assets), "by_family": by_family, "by_kind": by_kind, "failure_count": len(failures),
            "downloaded_bytes": sum(asset["bytes"] for asset in assets),
            "site_original_bytes": sum(asset["bytes"] for asset in assets if asset["site_original"]),
            "archive_only_bytes": sum(asset["bytes"] for asset in assets if not asset["site_original"]),
        },
    }
    empty = {
        "schema_version": 1, "owner": OWNER, "game": GAME, "repository": REPOSITORY, "snapshots": {}, "sources": [], "assets": [], "failures": [],
        "summary": {"source_count": 0, "source_asset_records": 0, "unique_files": 0, "gallery_images": 0, "slideshow_images": 0,
                    "exact_duplicate_records": 0, "by_family": {family: 0 for family in FAMILY_ORDER}, "by_kind": {}, "failure_count": 0,
                    "downloaded_bytes": 0, "site_original_bytes": 0, "archive_only_bytes": 0},
    }
    outputs = dict(blobs)
    for name, data in (("catalog", catalog), ("catalog-empty", empty)):
        outputs[f"{name}.json"] = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        outputs[f"{name}.js"] = catalog_js(data).encode("utf-8")
    return outputs


def catalog_js(catalog):
    """The contract's catalog.js: a script that cannot be closed early by `</script>` or broken by U+2028/U+2029."""
    compact = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    return "window.DYNAMIX_CATALOG = " + compact.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029") + ";\n"


def main(argv):
    outputs = build()
    generated = {Path(name) for name in outputs}
    existing = {path.relative_to(HERE) for path in HERE.rglob("*") if path.is_file() and path.suffix != ".py" and path.name != "README.md"}
    if "--check" in argv:
        stale = sorted(str(name) for name, data in outputs.items() if not (HERE / name).is_file() or (HERE / name).read_bytes() != data)
        extra = sorted(str(path) for path in existing - generated)
        if stale or extra:
            print(f"UI fixture is stale: changed {stale}, unexpected {extra}", file=sys.stderr)
            return 1
        print(f"UI fixture up to date ({len(outputs)} files, {sum(map(len, outputs.values()))} bytes)")
        return 0
    for directory in ("assets",):
        if (HERE / directory).exists():
            shutil.rmtree(HERE / directory)
    for name, data in outputs.items():
        target = HERE / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    print(f"Wrote {len(outputs)} files, {sum(map(len, outputs.values()))} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
