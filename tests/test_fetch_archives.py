"""Offline tests for scripts/fetch_archives.py: Wayback CDX paging and capture provenance, interrupted listings,
misleading extensions, press-release PDFs (archive tier), live 404s, primary-post evidence cross-checks and
``--verify`` tamper detection. Run: ``python -I tests/test_fetch_archives.py``.
"""

import base64
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import manifest, net, paths, schema  # noqa: E402
from primary import model, runner  # noqa: E402
from primary.adapters import direct as direct_adapter, wayback  # noqa: E402
from primary.testing import FIXED_NOW, MINIMAL_PDF, FakeNetwork, full_url, image_bytes  # noqa: E402

FIXTURES = ROOT / "tests/fixtures/archives"
HEADER = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]
QUALITY = {"source_variant": "site_original", "origin_status": "official", "master_status": "unverified",
           "notes": "test"}


def cdx_digest(data):
    return base64.b32encode(hashlib.sha1(data).digest()).decode("ascii")


def cdx_url(item, resume=None):
    params = {"url": item["url"], "matchType": item.get("match_type", "exact"), "output": "json",
              "fl": wayback.FIELDS, "showResumeKey": "true", "limit": str(item.get("page_size", 500))}
    if item.get("filters"):
        params["filter"] = list(item["filters"])
    if resume:
        params["resumeKey"] = resume
    return full_url(wayback.CDX, params)


class ArchivesTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_family(self, seeds, network, **options):
        acquisition = runner.Runner(self.root, "archives", sorted(seeds, key=lambda s: s["id"]),
                                    client_factory=network.factory, now=lambda: FIXED_NOW, sleep=lambda s: None,
                                    **options)
        return acquisition.run()

    def candidates(self, source_id):
        return {c["upstream_key"]: c for c in manifest.load_discovery(self.root, source_id)["candidates"]}

    def manifest(self):
        return manifest.load_manifest(self.root, "archives")

    def verify(self, family="archives"):
        errors, warnings, _ = runner.verify_family(self.root, family)
        self.assertEqual(errors, [], errors)
        return warnings


class WaybackTests(ArchivesTestCase):
    ITEM = {"name": "official-img", "url": "dynamix.c4-cat.com/img/", "match_type": "prefix",
            "filters": ["statuscode:200", "mimetype:image/.*"], "page_size": 2}

    def seed(self, rules, items=None):
        return {"id": "archives:wayback", "name": "Wayback", "url": "https://web.archive.org/", "platform": "wayback",
                "adapter": "wayback_cdx", "quality": QUALITY, "items": items or [self.ITEM], "rules": rules}

    def routes(self, *, fail_second_page=False):
        cap = image_bytes("PNG", (64, 36))
        icon = image_bytes("PNG", (15, 15), (0, 0, 0))
        disguised = image_bytes("JPEG", (20, 10))  # a JPEG behind a .png URL
        page1 = [HEADER,
                 ["com,c4-cat,dynamix)/img/cap1.png", "20150630034232", "http://dynamix.c4-cat.com/img/cap1.png",
                  "image/png", "200", cdx_digest(cap), "1160031"],
                 ["com,c4-cat,dynamix)/img/cap1.png", "20150412124448", "http://dynamix.c4-cat.com/img/cap1.png",
                  "image/png", "200", cdx_digest(cap), "1160031"],
                 [], ["RESUME-KEY-1"]]
        page2 = [HEADER,
                 ["com,c4-cat,dynamix)/img/icon_v2.png", "20150412124811",
                  "http://dynamix.c4-cat.com/img/icon_v2.png", "image/png", "200", cdx_digest(icon), "17845"],
                 ["com,c4-cat,dynamix)/img/shot.png", "20160101000000", "http://dynamix.c4-cat.com/img/shot.png",
                  "image/png", "200", cdx_digest(disguised), "999"]]
        second = {"error": net.HttpError("HTTP 503", url=wayback.CDX, http_status=503)} if fail_second_page else {
            "json": page2}
        return {cdx_url(self.ITEM): {"json": page1}, cdx_url(self.ITEM, "RESUME-KEY-1"): second,
                "https://web.archive.org/web/20150412124448id_/http://dynamix.c4-cat.com/img/cap1.png": {
                    "bytes": cap, "content_type": "image/png"},
                "https://web.archive.org/web/20160101000000id_/http://dynamix.c4-cat.com/img/shot.png": {
                    "bytes": disguised, "content_type": "image/png"}}

    RULES = [{"match": "/img/cap[1-6]\\.png@", "role": "gameplay_screenshot"},
             {"match": "/img/icon_v2\\.png@", "action": "exclude", "reason": "game_ui: app icon"},
             {"match": "/img/shot\\.png@", "role": "gameplay_screenshot"}]

    def test_pages_are_followed_and_each_payload_is_one_capture(self):
        network = FakeNetwork(self.routes())
        self.run_family([self.seed(self.RULES)], network)
        snapshot = manifest.load_discovery(self.root, "archives:wayback")
        self.assertTrue(snapshot["discovery_complete"])
        self.assertEqual(snapshot["continuation"], {"official-img": None})
        found = {c["upstream_key"]: c for c in snapshot["candidates"]}
        # Two captures with the same digest -> the earliest one only.
        self.assertEqual(sorted(found), ["dynamix.c4-cat.com/img/cap1.png@20150412124448",
                                         "dynamix.c4-cat.com/img/icon_v2.png@20150412124811",
                                         "dynamix.c4-cat.com/img/shot.png@20160101000000"])
        self.assertTrue(found["dynamix.c4-cat.com/img/icon_v2.png@20150412124811"]["reason"].startswith("game_ui"))
        assets = {a["candidate_id"].split(":", 2)[2]: a for a in self.manifest()["assets"]}
        cap = assets["dynamix.c4-cat.com/img/cap1.png@20150412124448"]
        self.assertEqual(cap["page_url"], "https://web.archive.org/web/20150412124448/http://dynamix.c4-cat.com/img/"
                                          "cap1.png")
        self.assertEqual(cap["source_reported"]["url"], "http://dynamix.c4-cat.com/img/cap1.png")
        self.assertEqual(cap["source_reported"]["timestamp"], "2015-04-12T12:44:48Z")
        self.assertEqual(cap["upstream_revision"], "20150412124448")
        self.assertIs(cap["comparisons"]["sha1_match"], True)  # bytes are the archived payload
        self.assertIsNone(cap["comparisons"]["bytes_match"])  # CDX length is the WARC record, not compared
        self.assertIn("WARC record length 1160031", cap["notes"])
        self.verify()

    def test_a_capture_served_from_another_timestamp_or_with_other_bytes_is_held(self):
        routes = self.routes()
        routes["https://web.archive.org/web/20150412124448id_/http://dynamix.c4-cat.com/img/cap1.png"]["final_url"] = (
            "https://web.archive.org/web/20150630034232id_/http://dynamix.c4-cat.com/img/cap1.png")
        routes["https://web.archive.org/web/20160101000000id_/http://dynamix.c4-cat.com/img/shot.png"]["bytes"] = (
            image_bytes("JPEG", (21, 10)))  # not the payload whose digest the CDX row lists
        self.run_family([self.seed(self.RULES)], FakeNetwork(routes))
        found = self.candidates("archives:wayback")
        cap = found["dynamix.c4-cat.com/img/cap1.png@20150412124448"]
        self.assertEqual(cap["disposition"], "pending")
        self.assertIn("with capture 20150630034232", cap["reason"])
        shot = found["dynamix.c4-cat.com/img/shot.png@20160101000000"]
        self.assertEqual(shot["disposition"], "pending")
        self.assertIn("not the payload the CDX API lists", shot["reason"])
        self.assertEqual(self.manifest()["assets"], [])
        self.verify()

    def test_suffix_does_not_decide_the_format(self):
        self.run_family([self.seed(self.RULES)], FakeNetwork(self.routes()))
        asset = next(a for a in self.manifest()["assets"] if "shot.png" in a["candidate_id"])
        self.assertEqual((asset["format"], asset["mime"]), ("JPEG", "image/jpeg"))
        self.assertTrue(asset["path"].endswith(".jpg"))
        self.assertEqual(asset["original_filename"], "shot.png")
        self.assertIs(asset["comparisons"]["format_match"], False)
        self.verify()

    def test_interrupted_listing_keeps_its_resume_key(self):
        network = FakeNetwork(self.routes(fail_second_page=True))
        self.run_family([self.seed(self.RULES)], network)
        snapshot = manifest.load_discovery(self.root, "archives:wayback")
        self.assertFalse(snapshot["discovery_complete"])
        self.assertEqual(snapshot["continuation"]["official-img"], "RESUME-KEY-1")
        self.assertIn("CDX listing interrupted", snapshot["errors"][0]["message"])
        self.assertEqual(len(snapshot["candidates"]), 1)  # the first page is still processed
        self.assertFalse(self.manifest()["sources"][0]["discovery_complete"])
        self.assertEqual(self.manifest()["sources"][0]["status"], "partially_available")
        self.verify()

    def test_tampered_file_fails_verify(self):
        self.run_family([self.seed(self.RULES)], FakeNetwork(self.routes()))
        asset = self.manifest()["assets"][0]
        target = paths.safe_path(self.root, asset["path"])
        target.write_bytes(target.read_bytes() + b"\0")
        errors, _, _ = runner.verify_family(self.root, "archives")
        self.assertTrue(any(asset["path"] in error or "asset file" in error for error in errors), errors)

    def test_a_repeated_resume_key_stops_paging(self):
        item = {**self.ITEM, "max_pages": 50}
        loop = [HEADER, ["com,c4-cat,dynamix)/img/cap1.png", "20150412124448", "http://dynamix.c4-cat.com/img/cap1.png",
                         "image/png", "200", "X" * 32, "1"], [], ["SAME-KEY"]]
        network = FakeNetwork({cdx_url(item): {"json": loop}, cdx_url(item, "SAME-KEY"): {"json": loop}})
        self.run_family([self.seed(self.RULES, [item])], network)
        self.assertEqual(network.count("web.archive.org"), 2 + 1)  # two CDX pages, then the one capture download
        snapshot = manifest.load_discovery(self.root, "archives:wayback")
        self.assertFalse(snapshot["discovery_complete"])
        self.assertEqual(snapshot["continuation"]["official-img"], "SAME-KEY")
        self.assertIn("again", snapshot["errors"][0]["message"])

    def test_paging_is_bounded_by_max_pages(self):
        item = {**self.ITEM, "max_pages": 1}
        network = FakeNetwork({cdx_url(item): self.routes()[cdx_url(self.ITEM)]})
        self.run_family([self.seed(self.RULES, [item])], network)
        snapshot = manifest.load_discovery(self.root, "archives:wayback")
        self.assertEqual(snapshot["continuation"]["official-img"], "RESUME-KEY-1")  # the unfetched page stays open
        self.assertIn("max_pages", snapshot["errors"][0]["message"])
        self.assertEqual(sum(1 for method, _ in network.calls if method == "get_json"), 1)

    def test_an_unusable_row_keeps_the_listing_open(self):
        routes = self.routes()
        page1 = list(routes[cdx_url(self.ITEM)]["json"])
        page1.insert(1, ["com,c4-cat,dynamix)/img/bad", "20150101000000", "http://dynamix.c4-cat.com/img/a b\x01.png",
                         "image/png", "200", "X" * 32, "1"])
        page1.insert(2, ["com,c4-cat,dynamix)/img/late", "2015-bad", "http://dynamix.c4-cat.com/img/late.png",
                         "image/png", "200", "Y" * 32, "1"])
        routes[cdx_url(self.ITEM)] = {"json": page1}
        self.run_family([self.seed(self.RULES)], FakeNetwork(routes))
        snapshot = manifest.load_discovery(self.root, "archives:wayback")
        self.assertEqual(snapshot["continuation"], {"official-img": "rows-skipped-2"})
        self.assertFalse(snapshot["discovery_complete"])
        self.assertEqual(sum("unusable CDX row skipped" in e["message"] for e in snapshot["errors"]), 2)
        self.verify()

    def test_digest_conversion(self):
        data = b"payload"
        self.assertEqual(wayback.digest_hex(cdx_digest(data)), hashlib.sha1(data).hexdigest())
        self.assertIsNone(wayback.digest_hex("not-a-digest"))
        self.assertEqual(wayback.capture_key("http://Example.org:80/a b.png", "20150101000000"),
                         "example.org/a%20b.png@20150101000000")


class PressReleaseTests(ArchivesTestCase):
    PAGE = "https://www.hkstp.org/zh-cn/park-life/news-and-events/news/press-release-20160809"

    def seed(self):
        return {"id": "archives:hkstp", "name": "HKSTP", "url": self.PAGE, "platform": "web", "adapter": "page_links",
                "hosts": ["www.hkstp.org"], "identity_quotes": ["Dynamix", "C4Cat"], "quality": QUALITY,
                "items": [{"url": self.PAGE, "link_rules": [
                    {"attr": "href", "pattern": "^/media/[a-z0-9]+/appendix[0-9]+\\.pdf$"},
                    {"attr": "src", "pattern": "^/-/media/corpsite/assets/news/img/20160809_[0-9]+\\.jpg$",
                     "strip_query": True}]}],
                "rules": [{"match": "appendix3\\.pdf$", "role": "reference"},
                          {"match": "appendix[12]\\.pdf$", "action": "exclude", "game": "not_dynamix",
                           "reason": "not_dynamix: another incubatee"},
                          {"match": "20160809_[0-9]+\\.jpg$", "action": "exclude", "reason": "person_photo: launch"}]}

    def test_pdf_reference_is_stored_byte_for_byte_in_the_archive_tier(self):
        network = FakeNetwork({self.PAGE: {"text": (FIXTURES / "press_release.html").read_text(encoding="utf-8")},
                               "https://www.hkstp.org/media/blqg5pwq/appendix3.pdf": {
                                   "bytes": MINIMAL_PDF, "content_type": "application/pdf"}})
        self.run_family([self.seed()], network)
        found = self.candidates("archives:hkstp")
        self.assertEqual(sorted(found), ["-/media/corpsite/assets/news/img/20160809_1.jpg",
                                         "media/blqg5pwq/appendix3.pdf", "media/hmzhrcry/appendix2.pdf",
                                         "media/rgscafs0/appendix1.pdf"])
        self.assertEqual(found["media/rgscafs0/appendix1.pdf"]["game"], "not_dynamix")
        self.assertTrue(found["-/media/corpsite/assets/news/img/20160809_1.jpg"]["reason"].startswith("person_photo"))
        self.assertEqual(found["-/media/corpsite/assets/news/img/20160809_1.jpg"]["file_url"],
                         "https://www.hkstp.org/-/media/corpsite/assets/news/img/20160809_1.jpg")
        asset = self.manifest()["assets"][0]
        self.assertEqual((asset["format"], asset["mime"], asset["width"], asset["height"]),
                         ("PDF", "application/pdf", None, None))
        self.assertEqual(schema.tier_for(asset["kind"], asset["format"]), "archive")
        self.assertEqual(paths.safe_path(self.root, asset["path"]).read_bytes(), MINIMAL_PDF)
        self.assertEqual(asset["sha256"], hashlib.sha256(MINIMAL_PDF).hexdigest())
        # Titled from the page it cites (its headline and the link's position), not from the file name.
        self.assertTrue(asset["title"].startswith("科技园培育公司多元发展"), asset["title"])
        self.assertRegex(asset["title"], r" \(\d/4\)$")
        self.assertEqual(asset["original_filename"], "appendix3.pdf")
        self.assertEqual(network.count("other.example.org"), 0)
        self.verify()

    def test_live_404_is_a_failure_not_a_success(self):
        network = FakeNetwork({self.PAGE: {"text": (FIXTURES / "press_release.html").read_text(encoding="utf-8")}})
        self.run_family([self.seed()], network)
        candidate = self.candidates("archives:hkstp")["media/blqg5pwq/appendix3.pdf"]
        self.assertEqual(candidate["disposition"], "failed")
        current = self.manifest()
        self.assertEqual(current["assets"], [])
        failure = current["failures"][0]
        self.assertEqual((failure["phase"], failure["error_class"], failure["http_status"]),
                         ("download", "http_4xx", 404))
        self.assertEqual(current["sources"][0]["status"], "metadata_only")
        self.verify()

    def test_unreadable_page_is_not_a_complete_enumeration(self):
        network = FakeNetwork({self.PAGE: {"error": net.HttpError("HTTP 503", url=self.PAGE, http_status=503)}})
        self.run_family([self.seed()], network)
        snapshot = manifest.load_discovery(self.root, "archives:hkstp")
        self.assertFalse(snapshot["discovery_complete"])
        self.assertEqual(snapshot["continuation"], {"seed-pages": "not-read:http_5xx"})
        self.assertEqual([c["upstream_key"] for c in snapshot["candidates"]], [f"page:{self.PAGE}"])
        self.verify()


class PressArticleTests(ArchivesTestCase):
    PAGE = "https://news.example.org/detail.php?sn=129234"
    IMG = "https://img.example.org/B/2KU/{}.{}"

    def seed(self, rules, *, select="div.article-body", items=None):
        return {"id": "archives:press", "name": "Press", "url": "https://news.example.org/", "platform": "web",
                "adapter": "direct_files", "hosts": ["news.example.org", "img.example.org"], "quality": QUALITY,
                "params": {"article_images": [{
                    "page_url": self.PAGE, "key_prefix": "gnn129234:", "select": select, "og_image": True,
                    "rewrite": [["^https://img\\.example\\.org/[MS]/2KU/", "https://img.example.org/B/2KU/"]],
                    "pattern": "https://img\\.example\\.org/B/2KU/\\d+/(?P<key>\\d+)\\.(?:JPG|PNG)"}]},
                "items": items if items is not None else [
                    {"key": "gnn129234:0001345596", "page_url": self.PAGE, "read_page": True,
                     "file_url": self.IMG.format("96/0001345596", "JPG")}],
                "rules": rules}

    RULES = [{"match": "^gnn129234:0001345596$", "role": "reference", "evidence_quotes": ["Dynamix"]},
             {"match": "^gnn129234:0001345608$", "role": "gameplay_screenshot", "evidence_quotes": ["Dynamix"]},
             {"match": "^gnn129234:0001345632$", "action": "exclude", "reason": "out_of_scope_other: header copy"},
             {"match": "^gnn129234:0001345692$", "action": "exclude", "reason": "site_ui: QR code"}]

    def routes(self):
        routes = {self.PAGE: {"text": (FIXTURES / "press_article.html").read_text(encoding="utf-8")}}
        for name, size in (("96/0001345596", (40, 23)), ("08/0001345608", (40, 22))):
            routes[self.IMG.format(name, "JPG")] = {"bytes": image_bytes("JPEG", size), "content_type": "image/jpeg"}
        return routes

    def test_every_article_image_gets_a_disposition(self):
        network = FakeNetwork(self.routes())
        self.run_family([self.seed(self.RULES)], network)
        snapshot = manifest.load_discovery(self.root, "archives:press")
        found = {c["upstream_key"]: c for c in snapshot["candidates"]}
        # og:image, the body images (srcset/data-src, an M/ rendition rewritten to B/), never the sidebar or logo.
        self.assertEqual(sorted(found), ["gnn129234:0001345595", "gnn129234:0001345596", "gnn129234:0001345608",
                                         "gnn129234:0001345632", "gnn129234:0001345692"])
        self.assertEqual(found["gnn129234:0001345595"]["disposition"], "pending")  # no rule: held, not dropped
        self.assertEqual(found["gnn129234:0001345595"]["file_url"], self.IMG.format("95/0001345595", "JPG"))
        self.assertEqual(found["gnn129234:0001345692"]["file_url"], self.IMG.format("92/0001345692", "PNG"))
        self.assertEqual(found["gnn129234:0001345608"]["disposition"], "downloaded")
        self.assertTrue(snapshot["discovery_complete"])
        self.assertEqual(snapshot["continuation"], {"article-images": None, "seed-files": None})
        self.assertEqual(network.count("news.example.org"), 1)  # the page is read once
        titles = {a["candidate_id"].split(":", 2)[2]: a["title"] for a in self.manifest()["assets"]}
        headline = "音樂遊戲《Dynamix》近期更新內容預告"
        self.assertEqual(titles["gnn129234:0001345596"], f"{headline} (2/5)")  # the named item takes its position
        self.assertEqual(titles["gnn129234:0001345608"], f"{headline} — GameParty2016 活動的來場特典曲，Play A Day (3/5)")
        self.assertNotIn("4", found["gnn129234:0001345595"]["title"].replace("(4/5)", ""))  # alt repeating the headline
        self.verify()

    def test_a_missing_article_selection_keeps_the_list_open(self):
        self.run_family([self.seed(self.RULES, select="div.no-such-body")], FakeNetwork(self.routes()))
        snapshot = manifest.load_discovery(self.root, "archives:press")
        self.assertFalse(snapshot["discovery_complete"])
        self.assertEqual(snapshot["continuation"]["article-images"], "selection-not-found")
        self.assertEqual(sorted(c["upstream_key"] for c in snapshot["candidates"]), ["gnn129234:0001345596"])
        self.verify()

    def test_an_unreadable_article_page_keeps_both_lists_open(self):
        network = FakeNetwork({self.PAGE: {"error": net.HttpError("HTTP 503", url=self.PAGE, http_status=503)}})
        self.run_family([self.seed(self.RULES)], network)
        snapshot = manifest.load_discovery(self.root, "archives:press")
        self.assertEqual(snapshot["continuation"], {"article-images": "not-read:http_5xx",
                                                    "seed-files": "not-read:http_5xx"})
        self.verify()

    def test_named_item_matched_by_url_is_not_enumerated_twice(self):
        items = [{"key": "gnn129234:og", "page_url": self.PAGE, "read_page": True,
                  "file_url": self.IMG.format("32/0001345632", "JPG")}]
        self.run_family([self.seed(self.RULES, items=items)], FakeNetwork(self.routes()))
        keys = sorted(c["upstream_key"] for c in manifest.load_discovery(self.root, "archives:press")["candidates"])
        self.assertIn("gnn129234:og", keys)
        self.assertNotIn("gnn129234:0001345632", keys)

    def test_seed_validation(self):
        seed = self.seed(self.RULES)
        seed["params"]["article_images"][0]["pattern"] = "("
        with self.assertRaises(Exception):
            direct_adapter.check_seed(seed, "test")
        seed = self.seed(self.RULES)
        del seed["params"]["article_images"][0]["select"]
        with self.assertRaises(ValueError):
            direct_adapter.check_seed(seed, "test")


class PrimaryEvidenceTests(ArchivesTestCase):
    ORIGINAL = "https://i.pximg.net/img-original/img/2017/05/15/17/01/36/62913946_p0.png"
    QUOTE = "My work for the Dynamix song Bluebell"

    def run_capture(self, quote):
        png = image_bytes("PNG", (19, 11))
        item = {"name": "pixiv-1", "url": "i.pximg.net/img-original/img/2017/05/15/17/01/36/62913946_p",
                "match_type": "prefix", "filters": ["statuscode:200"], "page_size": 50}
        rows = [HEADER, ["net,pximg,i)/img-original/img/2017/05/15/17/01/36/62913946_p0.png", "20221216101704",
                         self.ORIGINAL, "image/png", "200", cdx_digest(png), "2380000"]]
        network = FakeNetwork({cdx_url(item): {"json": rows},
                               f"https://web.archive.org/web/20221216101704id_/{self.ORIGINAL}": {
                                   "bytes": png, "content_type": "image/png"}})
        seed = {"id": "archives:wayback", "name": "Wayback", "url": "https://web.archive.org/", "platform": "wayback",
                "adapter": "wayback_cdx", "quality": QUALITY, "items": [item],
                "rules": [{"match": "62913946_p0\\.png@", "role": "song_art", "song_titles": ["Bluebell"],
                           "mapping_status": "confirmed_primary",
                           "primary_evidence": [{"url": "https://www.pixiv.net/artworks/62913946", "quote": quote}]}]}
        self.run_family([seed], network)
        return self.manifest()["assets"][0]

    def artist_snapshot(self, key, file_url):
        snapshot = {"schema_version": 1, "source_id": "artists:pixiv:760598", "started_at": FIXED_NOW,
                    "finished_at": FIXED_NOW, "scope": {"lists": ["seed-artworks"]}, "continuation": {},
                    "discovery_complete": True, "errors": [], "candidates": [{
                        "id": f"artists:pixiv:760598:{key}", "source_id": "artists:pixiv:760598",
                        "upstream_key": key, "page_url": "https://www.pixiv.net/artworks/62913946",
                        "file_url": file_url, "title": "Bluebell", "role": "song_art", "game": "dynamix",
                        "game_evidence": [f"caption: {self.QUOTE}"],
                        "source_reported": schema.make_source_reported(), "disposition": "blocked",
                        "reason": "blocked: HTTP 403"}]}
        manifest.write_json_atomic(self.root / paths.discovery_relpath("artists:pixiv:760598"), snapshot)

    def problems(self):
        errors, _, _ = runner.verify_family(self.root, "archives")
        return [error for error in errors if "primary-post evidence" in error]

    def test_copy_of_a_primary_post_is_cross_checked_against_the_artist_snapshot(self):
        asset = self.run_capture(self.QUOTE)
        self.assertEqual(asset["mapping_status"], "confirmed_primary")
        self.assertEqual(asset["mapping_evidence"][0]["field"], "primary_post")
        self.assertTrue(any("not corroborated" in error for error in self.problems()))  # an error, not a warning
        self.artist_snapshot("62913946:p0", self.ORIGINAL)
        self.assertEqual(self.problems(), [])
        self.verify()

    def test_withheld_file_url_is_matched_by_work_and_page(self):
        self.run_capture(self.QUOTE)
        self.artist_snapshot("62913946:p0", None)  # pixiv withheld the URL: <work>:p<n> names 62913946_p0
        self.assertEqual(self.problems(), [])
        self.artist_snapshot("62913946:p1", None)  # the quote belongs to another page of the work
        self.assertTrue(any("other files" in error for error in self.problems()))

    def test_evidence_of_another_file_does_not_corroborate(self):
        self.run_capture(self.QUOTE)
        self.artist_snapshot("62913946:p0", self.ORIGINAL.replace("_p0", "_p1"))
        self.assertTrue(any("other files" in error for error in self.problems()))

    def test_a_declared_alias_names_the_song(self):
        model.check_rule({"match": "x", "role": "song_art", "song_titles": ["Bluebell"],
                          "title_aliases": {"Bluebell": ["Blue bell"]}}, "rule")
        with self.assertRaises(ValueError):
            model.check_rule({"match": "x", "role": "song_art", "song_titles": ["Bluebell"],
                              "title_aliases": {"Other": ["x"]}}, "rule")
        status, evidence, notes = model.mapping_fields(
            {"song_titles": ["MassacrE || 3rc4224M"], "mapping_status": "confirmed_primary",
             "title_aliases": {"MassacrE || 3rc4224M": ["MassacrE"]},
             "primary_evidence": [{"url": "https://www.pixiv.net/artworks/92594600", "quote": "MassacrE"}]},
            [], "https://web.archive.org/x", {"original": "https://i.pximg.net/92594600_p0.jpg"})
        self.assertEqual((status, notes), ("confirmed_primary", []))

    def test_a_quote_that_does_not_name_the_song_is_not_confirmed_primary(self):
        asset = self.run_capture("My work for a Dynamix song")
        self.assertEqual(asset["mapping_status"], "inferred")
        self.assertIn("does not name “Bluebell”", asset["notes"])

    def test_an_excluded_or_held_candidate_corroborates_nothing(self):
        self.run_capture(self.QUOTE)
        self.artist_snapshot("62913946:p0", self.ORIGINAL)
        path = self.root / paths.discovery_relpath("artists:pixiv:760598")
        snapshot = manifest.load_json(path)
        for change in ({"disposition": "excluded", "game": "not_dynamix",
                        "reason": "rejected_submission: unused image for Dynamix Submission 2019"},
                       {"disposition": "pending", "game": "unknown", "reason": "pending: artwork owner 1 is not ..."}):
            snapshot["candidates"][0].update(change)
            manifest.write_json_atomic(path, snapshot)
            problems = self.problems()
            self.assertEqual(len(problems), 1, problems)
            self.assertIn("does not admit for Dynamix", problems[0])
            self.assertIn(change["disposition"], problems[0])


class RunnerStateTests(ArchivesTestCase):
    """Refresh bookkeeping of the shared runner: upstream removals, reverted and corrupted files, failed refreshes."""

    PAGE = "https://press.example.org/gallery"

    def seed(self, role="promotional", rules=None):
        return {"id": "archives:press", "name": "Press", "url": self.PAGE, "platform": "web", "adapter": "page_links",
                "hosts": ["press.example.org"], "quality": QUALITY,
                "items": [{"url": self.PAGE, "link_rules": [{"attr": "src", "pattern": "^/img/"}]}],
                "rules": rules or [{"match": "^img/", "role": role, "game": "dynamix"}]}

    @classmethod
    def page(cls, *names):
        return {"text": "<html><title>Dynamix gallery</title><body>" + "".join(
            f'<img src="https://press.example.org/img/{name}">' for name in names) + "</body></html>"}

    @staticmethod
    def file(color):
        return {"bytes": image_bytes("PNG", (20, 20), color), "content_type": "image/png"}

    def assets(self):
        return {asset["candidate_id"].rsplit(":", 1)[-1]: asset for asset in self.manifest()["assets"]}

    def test_a_file_gone_from_a_complete_listing_is_marked_removed(self):
        routes = {self.PAGE: self.page("a.png", "b.png"), "https://press.example.org/img/a.png": self.file((1, 2, 3)),
                  "https://press.example.org/img/b.png": self.file((9, 9, 9))}
        self.run_family([self.seed()], FakeNetwork(routes))
        routes[self.PAGE] = self.page("a.png")
        self.run_family([self.seed()], FakeNetwork(routes))
        snapshot = manifest.load_discovery(self.root, "archives:press")
        self.assertTrue(snapshot["discovery_complete"])
        self.assertEqual([c["upstream_key"] for c in snapshot["candidates"]], ["img/a.png"])
        removed = [a for a in self.manifest()["assets"] if a["candidate_id"].endswith("img/b.png")][0]
        self.assertEqual(removed["upstream_status"], "removed")
        self.assertIn("Not listed by the complete seed-pages enumeration", removed["notes"])
        self.assertTrue(paths.safe_path(self.root, removed["path"]).is_file())  # bytes kept
        self.verify()
        # An unreadable listing proves nothing: nothing more is marked.
        routes[self.PAGE] = {"error": net.HttpError("HTTP 503", url=self.PAGE, http_status=503)}
        self.run_family([self.seed()], FakeNetwork(routes))
        self.assertEqual({a["upstream_status"] for a in self.manifest()["assets"]}, {"current", "removed"})
        self.verify()

    def test_a_candidate_the_seeds_no_longer_reach_becomes_unknown_then_current_again(self):
        other = "https://press.example.org/other"
        seed = self.seed()
        seed["items"].append({"url": other, "link_rules": [{"attr": "src", "pattern": "^/img/"}]})
        routes = {self.PAGE: self.page("a.png"), other: self.page("c.png"),
                  "https://press.example.org/img/a.png": self.file((1, 2, 3)),
                  "https://press.example.org/img/c.png": self.file((4, 5, 6))}
        self.run_family([seed], FakeNetwork(routes))
        self.run_family([self.seed()], FakeNetwork(routes))  # the second page is no longer a seed item
        asset = self.assets()["img/c.png"]
        self.assertEqual(asset["upstream_status"], "unknown")
        self.assertIn(runner.UNKNOWN_NOTE, asset["notes"])
        self.assertEqual(self.candidates("archives:press")["img/c.png"]["disposition"], "downloaded")
        self.verify()
        self.run_family([seed], FakeNetwork(routes), resume=True)
        asset = self.assets()["img/c.png"]
        self.assertEqual(asset["upstream_status"], "current")
        self.assertNotIn(runner.UNKNOWN_NOTE, asset.get("notes") or "")
        self.verify()

    def test_bytes_that_revert_supersede_the_newer_record(self):
        url = "https://press.example.org/img/a.png"
        routes = {self.PAGE: self.page("a.png"), url: self.file((1, 2, 3))}
        self.run_family([self.seed()], FakeNetwork(routes))
        first = self.manifest()["assets"][0]["id"]
        routes[url] = self.file((9, 9, 9))
        self.run_family([self.seed()], FakeNetwork(routes))
        second = next(a["id"] for a in self.manifest()["assets"] if a["id"] != first)
        routes[url] = self.file((1, 2, 3))
        acquisition = runner.Runner(self.root, "archives", [self.seed()], client_factory=FakeNetwork(routes).factory,
                                    now=lambda: FIXED_NOW, sleep=lambda s: None)
        acquisition.run()
        self.assertEqual(acquisition.source_errors, {})
        assets = {a["id"]: a for a in self.manifest()["assets"]}
        self.assertEqual((assets[first]["upstream_status"], assets[first].get("superseded_by")), ("current", None))
        self.assertEqual((assets[second]["upstream_status"], assets[second]["superseded_by"]), ("superseded", first))
        self.assertEqual(self.candidates("archives:press")["img/a.png"]["asset_id"], first)
        self.verify()

    def test_a_failed_refresh_keeps_the_previous_candidate_record(self):
        url = "https://press.example.org/img/a.png"
        routes = {self.PAGE: self.page("a.png", "c.png"), url: self.file((1, 2, 3)),
                  "https://press.example.org/img/c.png": self.file((5, 5, 5))}
        self.run_family([self.seed("promotional")], FakeNetwork(routes))
        before = self.candidates("archives:press")["img/a.png"]
        routes[url] = {"error": net.HttpError("HTTP 503", url=url, http_status=503)}
        acquisition = runner.Runner(self.root, "archives", [self.seed("reference")],
                                    client_factory=FakeNetwork(routes).factory, now=lambda: FIXED_NOW,
                                    sleep=lambda s: None)
        acquisition.run()
        self.assertEqual(acquisition.source_errors, {})
        found = self.candidates("archives:press")
        self.assertEqual(found["img/a.png"], before)  # role change applies with the next successful refresh
        self.assertEqual(found["img/c.png"]["role"], "reference")  # later candidates were still refreshed
        self.assertEqual(self.assets()["img/a.png"]["kind"], "promotional")
        messages = [e["message"] for e in manifest.load_discovery(self.root, "archives:press")["errors"]]
        self.assertTrue(any("refresh of archives:press:img/a.png failed" in m for m in messages), messages)
        self.verify()

    def test_a_corrupted_file_is_repaired_when_the_bytes_match_the_record(self):
        url = "https://press.example.org/img/a.png"
        routes = {self.PAGE: self.page("a.png"), url: self.file((1, 2, 3))}
        self.run_family([self.seed()], FakeNetwork(routes))
        asset = self.manifest()["assets"][0]
        target = paths.safe_path(self.root, asset["path"])
        good = target.read_bytes()
        for resume in (True, False):
            target.write_bytes(b"corrupt")  # damaged in place, not deleted
            network = FakeNetwork(routes)
            self.run_family([self.seed()], network, resume=resume)
            self.assertEqual(sum(1 for _, called in network.calls if called == url), 1)
            self.assertEqual(target.read_bytes(), good)
            self.assertEqual(self.manifest()["assets"], [asset])
            messages = [e["message"] for e in manifest.load_discovery(self.root, "archives:press")["errors"]]
            self.assertTrue(any("replaced by a fresh download with the recorded SHA-256" in m for m in messages))
            self.assertEqual(self.manifest()["failures"], [])
            self.verify()
            self.assertEqual(list(target.parent.glob(".*")), [])

    def test_a_corrupted_file_that_cannot_be_repaired_is_reported(self):
        url = "https://press.example.org/img/a.png"
        routes = {self.PAGE: self.page("a.png"), url: self.file((1, 2, 3))}
        self.run_family([self.seed()], FakeNetwork(routes))
        target = paths.safe_path(self.root, self.manifest()["assets"][0]["path"])
        target.write_bytes(b"corrupt")
        routes[url] = {"error": net.HttpError("HTTP 503", url=url, http_status=503)}
        self.run_family([self.seed()], FakeNetwork(routes), resume=True)
        errors = manifest.load_discovery(self.root, "archives:press")["errors"]
        self.assertEqual([e["phase"] for e in errors], ["verify"])
        self.assertIn("does not verify", errors[0]["message"])
        self.assertIn("could not be repaired", errors[0]["message"])
        verify_errors, _, _ = runner.verify_family(self.root, "archives")
        self.assertTrue(any("asset file" in error for error in verify_errors))  # the --verify gate fails
        # New upstream bytes: the damaged record is superseded and its damage named.
        routes[url] = self.file((7, 7, 7))
        self.run_family([self.seed()], FakeNetwork(routes))
        messages = [e["message"] for e in manifest.load_discovery(self.root, "archives:press")["errors"]]
        self.assertTrue(any("cannot be repaired from upstream" in m for m in messages), messages)

    def test_pixel_identical_files_are_linked(self):
        from PIL import Image
        import io

        def png(text):
            image = Image.new("RGB", (8, 8), (10, 20, 30))
            info = __import__("PIL.PngImagePlugin", fromlist=["PngInfo"]).PngInfo()
            info.add_text("Comment", text)
            buffer = io.BytesIO()
            image.save(buffer, "PNG", pnginfo=info)
            return {"bytes": buffer.getvalue(), "content_type": "image/png"}

        routes = {self.PAGE: self.page("a.png", "b.png", "c.png"), "https://press.example.org/img/a.png": png("one"),
                  "https://press.example.org/img/b.png": png("two"),
                  "https://press.example.org/img/c.png": self.file((1, 1, 1))}
        self.run_family([self.seed()], FakeNetwork(routes))
        assets = self.assets()
        a, b = assets["img/a.png"], assets["img/b.png"]
        self.assertNotEqual(a["sha256"], b["sha256"])
        self.assertEqual(a["variant_group"], b["variant_group"])
        self.assertTrue(a["variant_group"].startswith("pixels:"))
        self.assertIn(f"pixels identical to {b['id']}", a["notes"])
        self.assertNotIn("variant_group", assets["img/c.png"])
        first = self.manifest()
        self.run_family([self.seed()], FakeNetwork(routes), resume=True)
        self.assertEqual(self.manifest(), first)  # idempotent
        self.verify()

    @staticmethod
    def tagged_png(token):
        """The same pixels with a different embedded token (an App Store re-render)."""
        from PIL import Image, PngImagePlugin
        import io

        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", token)
        buffer = io.BytesIO()
        Image.new("RGB", (16, 9), (9, 8, 7)).save(buffer, "PNG", pnginfo=info)
        return {"bytes": buffer.getvalue(), "content_type": "image/png"}

    def test_a_plain_rerun_with_the_same_pixels_keeps_the_archived_record(self):
        url = "https://press.example.org/img/a.png"
        routes = {self.PAGE: self.page("a.png"), url: self.tagged_png("TOKENAAAA")}
        self.run_family([self.seed()], FakeNetwork(routes))
        first = self.manifest()["assets"][0]
        for token in ("TOKENBBBB", "TOKENCCCC"):  # plain runs (no --resume) re-download every candidate
            routes[url] = self.tagged_png(token)
            self.run_family([self.seed()], FakeNetwork(routes))
        assets = self.manifest()["assets"]
        self.assertEqual([(a["id"], a["upstream_status"]) for a in assets], [(first["id"], "current")])
        files = sorted(p.name for p in (self.root / "assets/public/archives/press").iterdir())
        self.assertEqual(files, [Path(first["path"]).name])  # no second copy, no leftover .part file
        latest = hashlib.sha256(self.tagged_png("TOKENCCCC")["bytes"]).hexdigest()
        self.assertEqual(len(runner.REFETCH_NOTE_RE.findall(assets[0]["notes"])), 1)  # only the latest re-fetch
        self.assertIn(f"sha256 {latest}", assets[0]["notes"])
        self.assertEqual((assets[0]["sha256"], assets[0]["fetched_at"]), (first["sha256"], first["fetched_at"]))
        self.run_family([self.seed()], FakeNetwork(routes), resume=True)  # a refresh keeps the provenance note
        self.assertIn(f"sha256 {latest}", self.manifest()["assets"][0]["notes"])
        self.verify()
        routes[url] = self.file((1, 2, 3))  # other pixels are a new upstream revision
        self.run_family([self.seed()], FakeNetwork(routes))
        statuses = {a["id"]: a["upstream_status"] for a in self.manifest()["assets"]}
        self.assertEqual(statuses[first["id"]], "superseded")
        self.verify()

    def test_a_file_moved_to_a_new_key_is_adopted_not_left_removed(self):
        data = self.file((1, 2, 3))
        routes = {self.PAGE: self.page("a.png"), "https://press.example.org/img/a.png": data}
        self.run_family([self.seed()], FakeNetwork(routes))
        record = self.manifest()["assets"][0]
        routes = {self.PAGE: self.page("b.png"), "https://press.example.org/img/b.png": data}
        self.run_family([self.seed()], FakeNetwork(routes))
        found = self.candidates("archives:press")
        self.assertEqual(list(found), ["img/b.png"])
        self.assertEqual((found["img/b.png"]["disposition"], found["img/b.png"]["asset_id"]), ("downloaded",
                                                                                               record["id"]))
        [asset] = self.manifest()["assets"]
        self.assertEqual((asset["id"], asset["path"], asset["upstream_status"]), (record["id"], record["path"],
                                                                                  "current"))
        self.assertEqual(asset["candidate_id"], "archives:press:img/b.png")
        self.assertIn("First archived for candidate archives:press:img/a.png (upstream_status then removed)",
                      asset["notes"])
        self.assertNotIn("removed upstream", asset["notes"])
        self.verify()
        self.run_family([self.seed()], FakeNetwork(routes), resume=True)  # stable, history kept
        self.assertIn("First archived for candidate archives:press:img/a.png", self.manifest()["assets"][0]["notes"])
        self.verify()

    def test_bytes_of_another_candidates_earlier_revision_are_adopted(self):
        a, b = "https://press.example.org/img/a.png", "https://press.example.org/img/b.png"
        routes = {self.PAGE: self.page("a.png"), a: self.file((1, 2, 3))}
        self.run_family([self.seed()], FakeNetwork(routes))
        old = self.manifest()["assets"][0]["id"]
        routes = {self.PAGE: self.page("a.png", "b.png"), a: self.file((7, 7, 7)), b: self.file((1, 2, 3))}
        self.run_family([self.seed()], FakeNetwork(routes))
        found = self.candidates("archives:press")
        assets = {asset["id"]: asset for asset in self.manifest()["assets"]}
        self.assertEqual(found["img/b.png"]["disposition"], "downloaded")
        self.assertEqual(found["img/b.png"]["asset_id"], old)
        self.assertEqual((assets[old]["upstream_status"], assets[old].get("superseded_by")), ("current", None))
        self.assertIn("upstream_status then superseded", assets[old]["notes"])
        newer = found["img/a.png"]["asset_id"]
        self.assertEqual((assets[newer]["candidate_id"], assets[newer]["upstream_status"]),
                         ("archives:press:img/a.png", "current"))
        self.verify()

    def test_an_unknown_record_listed_under_another_key_is_current_again(self):
        other = "https://press.example.org/other"
        seed = self.seed()
        seed["items"].append({"url": other, "link_rules": [{"attr": "src", "pattern": "^/img/"}]})
        data = self.file((4, 5, 6))
        self.run_family([seed], FakeNetwork({self.PAGE: self.page(), other: self.page("c.png"),
                                             "https://press.example.org/img/c.png": data}))
        record = self.manifest()["assets"][0]
        # The second page is no longer a seed item (c.png becomes unknown); the first page now lists the same bytes.
        self.run_family([self.seed()], FakeNetwork({self.PAGE: self.page("b.png"),
                                                    "https://press.example.org/img/b.png": data}))
        found = self.candidates("archives:press")
        self.assertEqual((found["img/b.png"]["disposition"], found["img/b.png"]["asset_id"]),
                         ("duplicate_bytes", record["id"]))
        [asset] = self.manifest()["assets"]
        self.assertEqual((asset["candidate_id"], asset["upstream_status"]), (record["candidate_id"], "current"))
        self.assertIn("also listed upstream as archives:press:img/b.png", asset["notes"])
        self.assertNotIn(runner.UNKNOWN_NOTE, asset["notes"])
        errors, warnings, _ = runner.verify_family(self.root, "archives")
        self.assertEqual((errors, warnings), ([], []))


class VerifySnapshotTests(ArchivesTestCase):
    PAGE = RunnerStateTests.PAGE

    def setUp(self):
        super().setUp()
        routes = {self.PAGE: RunnerStateTests.page("a.png"),
                  "https://press.example.org/img/a.png": RunnerStateTests.file((1, 2, 3))}
        self.run_family([RunnerStateTests.seed(self)], FakeNetwork(routes))
        self.path = self.root / paths.discovery_relpath("archives:press")

    def errors(self):
        return runner.verify_family(self.root, "archives")

    def test_a_source_without_its_snapshot_fails(self):
        self.path.unlink()
        errors, _, _ = self.errors()
        self.assertIn("archives:press: no valid discovery snapshot at data/discovery/press.json", errors)

    def test_a_snapshot_without_a_valid_source_id_fails(self):
        manifest.write_json_atomic(self.path, {"schema_version": 1, "source_id": "archives/press"})
        errors, _, _ = self.errors()
        self.assertTrue(any(e.startswith("data/discovery/press.json: not a discovery snapshot of a valid source ID")
                            for e in errors), errors)
        self.assertIn("archives:press: no valid discovery snapshot at data/discovery/press.json", errors)
        self.path.write_text("{not json", encoding="utf-8")
        errors, _, _ = self.errors()
        self.assertTrue(any("press.json: not a discovery snapshot" in e and "unreadable" in e for e in errors))

    def test_a_registered_name_of_this_family_is_checked_and_unknown_names_warn(self):
        manifest.write_json_atomic(self.root / "data/discovery/wayback.json", {"source_id": None})
        manifest.write_json_atomic(self.root / "data/discovery/stray.json", {"source_id": "nope"})
        manifest.write_json_atomic(self.root / "data/discovery/song-index.wikis.json", {"songs": []})
        errors, warnings, _ = self.errors()
        self.assertTrue(any(e.startswith("data/discovery/wayback.json: not a discovery snapshot") for e in errors))
        self.assertTrue(any(w.startswith("data/discovery/stray.json: not a discovery snapshot") for w in warnings))
        self.assertFalse(any("song-index" in line for line in errors + warnings))


if __name__ == "__main__":
    unittest.main()
