"""Offline tests for scripts/fetch_archives.py: Wayback CDX paging and capture provenance, interrupted listings,
misleading extensions, press-release PDFs (archive tier), live 404s, primary-post evidence cross-checks and
``--verify`` tamper detection. Run: ``python -I tests/test_fetch_archives.py``.
"""

import base64
import io
import json
import hashlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import manifest, net, paths, schema  # noqa: E402
from primary import cli, model, runner  # noqa: E402
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

    def test_an_empty_answer_after_listed_captures_keeps_the_list_open(self):
        """A CDX query that listed captures before and now answers [] is an incomplete listing (07 §Integrity): the
        archived captures keep their status and the snapshot is not complete. A query that never listed a capture
        may stay empty and complete."""
        self.run_family([self.seed(self.RULES)], FakeNetwork(self.routes()))
        before = {a["id"]: a["upstream_status"] for a in self.manifest()["assets"]}
        self.assertEqual(set(before.values()), {"current"})
        empty_item = {"name": "never-archived", "url": "dynamix.c4-cat.com/none/", "match_type": "prefix"}
        routes = {cdx_url(self.ITEM): {"json": []}, cdx_url(empty_item): {"json": []}}
        self.run_family([self.seed(self.RULES, items=[self.ITEM, empty_item])], FakeNetwork(routes))
        snapshot = manifest.load_discovery(self.root, "archives:wayback")
        self.assertFalse(snapshot["discovery_complete"])
        self.assertEqual(snapshot["continuation"], {"official-img": "empty-answer", "never-archived": None})
        self.assertTrue(any("listed no capture, although the previous snapshot holds 3 capture(s)" in e["message"]
                            for e in snapshot["errors"]), snapshot["errors"])
        self.assertEqual({a["id"]: a["upstream_status"] for a in self.manifest()["assets"]}, before)
        self.assertFalse(any(runner.UNKNOWN_NOTE in (a.get("notes") or "") for a in self.manifest()["assets"]))
        self.assertNotEqual(self.manifest()["sources"][0]["status"], "available")
        self.verify()

    def test_cdx_item_matching_follows_the_query(self):
        prefix = {"url": "dynamix.c4-cat.com/img/", "match_type": "prefix"}
        self.assertTrue(wayback.matches_item(prefix, "http://www.dynamix.c4-cat.com:80/IMG/cap1.png"))
        self.assertFalse(wayback.matches_item(prefix, "http://dynamix.c4-cat.com/image/cap1.png"))
        exact = {"url": "www.hkstp.org/media/a.pdf"}
        self.assertTrue(wayback.matches_item(exact, "https://www.hkstp.org/media/a.pdf"))
        self.assertFalse(wayback.matches_item(exact, "https://www.hkstp.org/media/a.pdf2"))
        self.assertTrue(wayback.matches_item({"url": "c4-cat.com", "match_type": "domain"},
                                             "http://dl.dnx.c4-cat.com/x.png"))
        self.assertFalse(wayback.matches_item({"url": "c4-cat.com", "match_type": "host"},
                                              "http://dl.dnx.c4-cat.com/x.png"))

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
    maxDiff = None
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
        # og:image, the body images (srcset/data-src, an M/ rendition rewritten to B/) and the selection's other image
        # (a lazy-load placeholder), never the sidebar or logo outside the selection, a data: URI or a page link.
        self.assertEqual(sorted(found), ["gnn129234:0001345595", "gnn129234:0001345596", "gnn129234:0001345608",
                                         "gnn129234:0001345632", "gnn129234:0001345692",
                                         "gnn129234:other:img.example.org/lazy.gif"])
        self.assertEqual(found["gnn129234:0001345595"]["disposition"], "pending")  # no rule: held, not dropped
        other = found["gnn129234:other:img.example.org/lazy.gif"]
        self.assertEqual((other["disposition"], other["file_url"]), ("pending", "https://img.example.org/lazy.gif"))
        self.assertEqual(other["title"], "音樂遊戲《Dynamix》近期更新內容預告 (other image)")
        # Advertised sizes: og:image:width/height of an og:image listed as the upload itself, data-orig-size.
        reported = {key: (c["source_reported"]["width"], c["source_reported"]["height"]) for key, c in found.items()}
        self.assertEqual(reported["gnn129234:0001345632"], (640, 360))
        self.assertEqual(reported["gnn129234:0001345608"], (40, 22))
        self.assertEqual(reported["gnn129234:0001345596"], (None, None))
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
        asset = next(a for a in self.manifest()["assets"] if a["candidate_id"].endswith("0001345608"))
        self.assertTrue(asset["comparisons"]["dimensions_match"])
        self.verify()

    def test_a_missing_article_selection_keeps_the_list_open(self):
        self.run_family([self.seed(self.RULES, select="div.no-such-body")], FakeNetwork(self.routes()))
        snapshot = manifest.load_discovery(self.root, "archives:press")
        self.assertFalse(snapshot["discovery_complete"])
        self.assertEqual(snapshot["continuation"]["article-images"], "selection-not-found")
        # The images that were found (the og:image) are still accounted for; the list just is not complete.
        self.assertEqual(sorted(c["upstream_key"] for c in snapshot["candidates"]),
                         ["gnn129234:0001345596", "gnn129234:0001345632"])
        self.verify()

    def test_an_empty_selection_is_not_a_complete_enumeration(self):
        # The selection exists but holds no image of the pattern (a client-rendered body): the list stays open, and a
        # previously archived article image is not marked removed by this empty read.
        network = FakeNetwork(self.routes())
        self.run_family([self.seed(self.RULES)], network)
        empty = self.routes()
        empty[self.PAGE] = {"text": "<html><head><title>音樂遊戲《Dynamix》近期更新內容預告</title>"
                                    "<meta property='og:image' content='https://img.example.org/B/2KU/32/0001345632.JPG'>"
                                    "</head><body><div class='article-body'><p>Dynamix</p></div></body></html>"}
        self.run_family([self.seed(self.RULES)], FakeNetwork(empty), resume=True)
        snapshot = manifest.load_discovery(self.root, "archives:press")
        self.assertFalse(snapshot["discovery_complete"])
        self.assertEqual(snapshot["continuation"]["article-images"], "selection-empty")
        self.assertTrue(any("holds no image" in error["message"] for error in snapshot["errors"]))
        statuses = {a["candidate_id"].split(":", 2)[2]: a["upstream_status"] for a in self.manifest()["assets"]}
        self.assertEqual(statuses["gnn129234:0001345608"], "current")  # carried over, not removed
        self.verify()

    def test_coverage_is_limited_to_the_keys_the_enumeration_produces(self):
        # A named file outside the key prefix that the seeds stop naming is "unknown", never "removed" by the article
        # enumeration; an enumerated image that the complete page no longer shows is "removed".
        items = [{"key": "gnn129234:0001345596", "page_url": self.PAGE, "read_page": True,
                  "file_url": self.IMG.format("96/0001345596", "JPG")},
                 {"key": "named:extra", "page_url": self.PAGE, "read_page": True,
                  "file_url": "https://img.example.org/B/2KU/50/0001345650.JPG"}]
        rules = [*self.RULES, {"match": "^named:extra$", "role": "promotional", "evidence_quotes": ["Dynamix"]}]
        routes = self.routes()
        routes["https://img.example.org/B/2KU/50/0001345650.JPG"] = {"bytes": image_bytes("JPEG", (30, 20)),
                                                                     "content_type": "image/jpeg"}
        self.run_family([self.seed(rules, items=items)], FakeNetwork(routes))
        later = dict(routes)
        later[self.PAGE] = {"text": routes[self.PAGE]["text"].replace("0001345608", "0001345609")}
        later[self.IMG.format("09/0001345609", "JPG")] = {"bytes": image_bytes("JPEG", (41, 22)),
                                                          "content_type": "image/jpeg"}
        self.run_family([self.seed(rules, items=items[:1])], FakeNetwork(later), resume=True)
        statuses = {a["candidate_id"].split(":", 2)[2]: a["upstream_status"] for a in self.manifest()["assets"]}
        self.assertEqual(statuses["gnn129234:0001345608"], "removed")
        self.assertEqual(statuses["named:extra"], "unknown")
        self.verify()

    NEXT_PAGE = "https://www.example.hk/article/175169"
    CDN = "https://cdn.example.hk/di/media/images/{}/org/{}.{}/{}"

    def next_seed(self, rules, *, count=3):
        return {"id": "archives:press", "name": "Press", "url": "https://www.example.hk/", "platform": "web",
                "adapter": "direct_files", "hosts": ["www.example.hk", "cdn.example.hk"], "quality": QUALITY,
                "params": {"article_images": [{
                    "page_url": self.NEXT_PAGE, "key_prefix": "hk01-175169:", "select": "article", "og_image": True,
                    "embedded_json": {"script_id": "__NEXT_DATA__", "blocks": "props.pageProps.article.blocks",
                                      "type_field": "blockType", "images": {"image": "image", "gallery": "images"},
                                      "url_field": "cdnUrl", "caption_field": "caption",
                                      "width_field": "originalWidth", "height_field": "originalHeight",
                                      "extra_images": ["props.pageProps.article.mainImage"],
                                      "count": "props.pageProps.article.imageCount"},
                    "pattern": "https://cdn\\.example\\.hk/di/media/images/(?P<key>\\d+)/org/(?P<name>[0-9a-f]+"
                               "\\.(?:jpe?g|png|PNG))/[A-Za-z0-9_-]+"}]},
                "items": [{"key": "hk01:175169", "page_url": self.NEXT_PAGE, "read_page": True,
                           "file_url": self.CDN.format(1, "aa", "jpg", "tok1")}],
                "rules": rules}

    def next_page(self, count):
        def image(media, caption):
            return {"mediaId": media, "caption": caption, "cdnUrl": self.CDN.format(media, "bb", "PNG", f"t{media}"),
                    "originalWidth": 30 + media, "originalHeight": 20}
        main = {"mediaId": 1, "caption": "", "cdnUrl": self.CDN.format(1, "aa", "jpg", "tok1"), "originalWidth": 31,
                "originalHeight": 20}
        data = {"props": {"pageProps": {"article": {"imageCount": count, "mainImage": main, "blocks": [
            {"blockType": "text", "htmlTokens": []},
            {"blockType": "image", "image": image(2, "簡潔又帶科幻感的界面")},
            {"blockType": "gallery", "images": [image(3, "藍色是輕觸屏幕"), image(4, "")]},
            {"blockType": "related", "articles": [{"data": {"mainImage": image(99, "other article")}}]}]}}}}
        og = self.CDN.format(1, "aa", "jpg", "tok1") + "?v=w1280r16_9"
        return {"text": f"<html><head><title>本地獨立手機遊戲系列：《Dynamix》</title>"
                        f"<meta property='og:image' content='{og}'></head><body><h1>本地獨立手機遊戲系列：《Dynamix》</h1>"
                        f"<article><p>Dynamix</p></article><script id='__NEXT_DATA__' type='application/json'>"
                        f"{json.dumps(data, ensure_ascii=False)}</script></body></html>"}

    NEXT_RULES = [{"match": "^hk01:175169$", "role": "promotional", "evidence_quotes": ["Dynamix"]},
                  {"match": "^hk01-175169:", "role": "gameplay_screenshot", "evidence_quotes": ["Dynamix"]}]

    def test_body_images_of_an_embedded_article_payload_are_enumerated(self):
        routes = {self.NEXT_PAGE: self.next_page(3)}
        for media in (1, 2, 3, 4):
            url = self.CDN.format(media, "aa" if media == 1 else "bb", "jpg" if media == 1 else "PNG",
                                  "tok1" if media == 1 else f"t{media}")
            routes[url] = {"bytes": image_bytes("JPEG", (30 + media, 20)), "content_type": "image/jpeg"}
        self.run_family([self.next_seed(self.NEXT_RULES)], FakeNetwork(routes))
        snapshot = manifest.load_discovery(self.root, "archives:press")
        self.assertTrue(snapshot["discovery_complete"], snapshot["continuation"])
        found = {c["upstream_key"]: c for c in snapshot["candidates"]}
        # The named og:image (signed path kept, query dropped) and the 3 body images; never the related article's.
        self.assertEqual(sorted(found), ["hk01-175169:2", "hk01-175169:3", "hk01-175169:4", "hk01:175169"])
        self.assertEqual({c["disposition"] for c in found.values()}, {"downloaded"})
        titles = {a["candidate_id"].split(":", 2)[2]: a["title"] for a in self.manifest()["assets"]}
        self.assertEqual(titles["hk01-175169:2"], "本地獨立手機遊戲系列：《Dynamix》 — 簡潔又帶科幻感的界面 (2/4)")
        self.assertEqual(titles["hk01:175169"], "本地獨立手機遊戲系列：《Dynamix》 (1/4)")
        names = {a["candidate_id"].split(":", 2)[2]: a["original_filename"] for a in self.manifest()["assets"]}
        self.assertEqual(names["hk01-175169:2"], "bb.PNG")  # the upload's name, not the signature segment
        self.assertEqual(names["hk01:175169"], "aa.jpg")
        # The payload's originalWidth/originalHeight (blocks, and the main image outside them) are the advertised size.
        for asset in self.manifest()["assets"]:
            self.assertEqual((asset["source_reported"]["width"], asset["source_reported"]["height"]),
                             (asset["width"], asset["height"]))
            self.assertTrue(asset["comparisons"]["dimensions_match"], asset["id"])
        self.verify()

    def test_advertised_sizes_describe_only_the_upload_url(self):
        # og:image:width/height belong to the og:image they follow, and only to one listed as the upload URL itself
        # (a query may select a resized rendition); data-orig-size is the WordPress attachment's original upload.
        page = ("<html><head><meta property='og:image' content='https://img.example.org/B/2KU/01/0000000001.JPG?w=300'>"
                "<meta property='og:image:width' content='300'><meta property='og:image:height' content='200'>"
                "<meta property='og:image' content='https://img.example.org/B/2KU/02/0000000002.JPG'>"
                "<meta property='og:image:width' content='1200'><meta property='og:image:height' content='800'>"
                "</head><body><div class='article-body'><img src='https://img.example.org/S/2KU/03/0000000003.JPG' "
                "data-orig-size='1920,1080'><img src='https://img.example.org/B/2KU/04/0000000004.JPG' "
                "data-orig-size='x,1'></div></body></html>")
        config = self.seed(self.RULES)["params"]["article_images"][0]
        images, others, state = direct_adapter.article_images(page, self.PAGE, config)
        self.assertEqual({image["key"]: image["size"] for image in images},
                         {"gnn129234:0000000001": None, "gnn129234:0000000002": (1200, 800),
                          "gnn129234:0000000003": (1920, 1080), "gnn129234:0000000004": None})
        self.assertEqual((others, state), ([], None))

    def test_an_image_count_that_disagrees_keeps_the_list_open(self):
        routes = {self.NEXT_PAGE: self.next_page(10)}
        self.run_family([self.next_seed(self.NEXT_RULES)], FakeNetwork(routes), metadata_only=True)
        snapshot = manifest.load_discovery(self.root, "archives:press")
        self.assertEqual(snapshot["continuation"]["article-images"], "image-count-mismatch:3/10")
        routes = {self.NEXT_PAGE: {"text": self.next_page(3)["text"].replace("__NEXT_DATA__", "other")}}
        self.run_family([self.next_seed(self.NEXT_RULES)], FakeNetwork(routes), metadata_only=True)
        snapshot = manifest.load_discovery(self.root, "archives:press")
        self.assertEqual(snapshot["continuation"]["article-images"], "embedded-json-not-found")

    def test_a_404_of_our_normalized_url_falls_back_to_the_url_the_page_lists(self):
        # The page lists only an M/ rendition; our rewrite to B/ answers 404 upstream: the listed URL is archived and
        # the record describes it (advertised URL, quality not called the upload); no failure is recorded against
        # upstream, and --resume reuses the archived rendition without requesting either URL again.
        rules = [*self.RULES[:1], {"match": "^gnn129234:0001345692$", "role": "promotional",
                                   "evidence_quotes": ["Dynamix"]}]
        routes = self.routes()
        listed = "https://img.example.org/M/2KU/92/0001345692.PNG?_=1"
        routes[listed] = {"bytes": image_bytes("PNG", (21, 21)), "content_type": "image/png"}
        self.run_family([self.seed(rules)], FakeNetwork(routes))

        def record():
            return [a for a in self.manifest()["assets"] if a["candidate_id"].endswith("0001345692")][0]
        asset = record()
        self.assertEqual(asset["download_url"], listed)
        self.assertEqual(asset["source_reported"]["url"], listed)  # what the page lists, not the URL that 404'd
        self.assertEqual(asset["original_filename"], "0001345692.PNG")
        self.assertEqual(asset["quality"]["source_variant"], "unknown")
        self.assertIn("Not established as the upload", asset["quality"]["notes"])
        self.assertIn("rendition or proxy copy", asset["quality"]["notes"])
        self.assertIn("answered HTTP 404", asset["notes"])
        self.assertEqual([f for f in self.manifest()["failures"] if f["candidate_id"].endswith("0001345692")], [])
        candidate = self.candidates("archives:press")["gnn129234:0001345692"]
        self.assertEqual((candidate["file_url"], candidate["source_reported"]["url"]), (listed, listed))
        self.verify()
        network = FakeNetwork(routes)
        self.run_family([self.seed(rules)], network, resume=True)
        self.assertEqual([url for _, url in network.calls if "0001345692" in url], [])
        self.assertEqual(record(), asset)  # a refresh describes the same fetch the same way
        self.verify()

    def test_a_fallback_rendition_keeps_its_own_file_name(self):
        page = ("<html><head><title>Dynamix review</title></head><body><h1>A long Dynamix review headline</h1>"
                "<div class='article-body'><img src='https://img.example.org/up/shot-1024x576.jpg'></div></body></html>")
        seed = self.seed([{"match": "^wp:", "role": "gameplay_screenshot", "evidence_quotes": ["Dynamix"]}], items=[])
        seed["params"]["article_images"][0].update({
            "key_prefix": "wp:", "og_image": False, "rewrite": [["-\\d+x\\d+(\\.\\w+)$", "\\1"]],
            "pattern": "https://img\\.example\\.org/up/[^/]+\\.jpg"})
        listed = "https://img.example.org/up/shot-1024x576.jpg"
        network = FakeNetwork({self.PAGE: {"text": page},
                               listed: {"bytes": image_bytes("JPEG", (32, 18)), "content_type": "image/jpeg"}})
        self.run_family([seed], network)
        self.assertEqual([url for _, url in network.calls if "/up/" in url],
                         ["https://img.example.org/up/shot.jpg", listed])
        [asset] = self.manifest()["assets"]
        self.assertEqual((asset["original_filename"], asset["download_url"]), ("shot-1024x576.jpg", listed))
        self.assertIn("/shot-1024x576--", asset["path"])
        self.assertEqual(asset["quality"]["source_variant"], "unknown")
        self.verify()

    def test_a_fallback_on_a_host_the_seed_does_not_allow_is_not_tried(self):
        # A proxy URL the page lists (i0.wp.com) is outside the seed's hosts: the recorded failure is the 404 of the
        # normalized upload URL, not a host_not_allowed error of the proxy.
        seed = self.seed([*self.RULES[:1], {"match": "^gnn129234:0001345692$", "role": "promotional",
                                            "evidence_quotes": ["Dynamix"]}])
        config = seed["params"]["article_images"][0]
        config["rewrite"] = [["^https://i0\\.wp\\.com/img\\.example\\.org/[MS]/", "https://img.example.org/B/"],
                             *config["rewrite"]]
        routes = self.routes()
        routes[self.PAGE] = {"text": routes[self.PAGE]["text"].replace(
            "https://img.example.org/M/2KU/92/0001345692.PNG", "https://i0.wp.com/img.example.org/M/2KU/92/0001345692.PNG")}
        self.run_family([seed], FakeNetwork(routes))
        [failure] = [f for f in self.manifest()["failures"] if f["candidate_id"].endswith("0001345692")]
        self.assertEqual((failure["error_class"], failure["http_status"], failure["url"]),
                         ("http_4xx", 404, self.IMG.format("92/0001345692", "PNG")))
        candidate = self.candidates("archives:press")["gnn129234:0001345692"]
        self.assertEqual(candidate["disposition"], "failed")
        self.assertNotIn("host_not_allowed", candidate["reason"])
        self.verify()

    DEFAULT_NOTE = "Press copy as served by the publication; the page does not state who supplied the image."
    SUPPLIED_NOTE = "Press copy as served by the publication, inside the part the page labels as supplied material."

    def test_only_images_after_the_pages_supplied_marker_are_called_developer_supplied(self):
        rules = [{"match": "^gnn129234:0001345632$", "role": "gameplay_screenshot", "evidence_quotes": ["Dynamix"]},
                 {"match": "^gnn129234:0001345595$", "role": "gameplay_screenshot", "evidence_quotes": ["Dynamix"],
                  "quality": {"notes": "Curated note of this rule."}},
                 {"match": "^gnn129234:\\d+$", "role": "gameplay_screenshot", "evidence_quotes": ["Dynamix"]}]
        seed = self.seed(rules)
        seed["quality"] = {**QUALITY, "notes": self.DEFAULT_NOTE}
        seed["params"]["article_images"][0].update({"supplied_marker": "【以下內容為廠商提供資料原文】",
                                                    "supplied_note": self.SUPPLIED_NOTE})
        routes = self.routes()
        routes[self.PAGE] = {"text": routes[self.PAGE]["text"].replace(
            '<ul class="thumbs"><li><div class="thumb">\n<img alt="image" data-orig-size',
            '<p>【以下內容為廠商提供資料原文】</p><ul class="thumbs"><li><div class="thumb">\n<img alt="image" data-orig-size')}
        for index, name in enumerate(("95/0001345595", "92/0001345692", "32/0001345632")):
            routes[self.IMG.format(name, "PNG" if name.startswith("92") else "JPG")] = {
                "bytes": image_bytes("JPEG", (30, 31 + index)), "content_type": "image/jpeg"}
        self.run_family([seed], FakeNetwork(routes))
        notes = {a["candidate_id"].split(":", 2)[2]: a["quality"]["notes"] for a in self.manifest()["assets"]}
        label = "The page places this image after its label '【以下內容為廠商提供資料原文】'."
        self.assertEqual(notes["gnn129234:0001345608"], f"{self.SUPPLIED_NOTE} {label}")  # replaces the default note
        self.assertEqual(notes["gnn129234:0001345692"], f"{self.SUPPLIED_NOTE} {label}")
        self.assertEqual(notes["gnn129234:0001345595"], f"Curated note of this rule. {label}")  # a rule's note stays
        for key in ("gnn129234:0001345596", "gnn129234:0001345632"):  # before the marker; og:image only
            self.assertEqual(notes[key], self.DEFAULT_NOTE)
        for key, text in notes.items():  # never both "not stated" and "supplied" on one record
            self.assertFalse("does not state who supplied" in text and "label" in text, (key, text))
        self.verify()
        del seed["params"]["article_images"][0]["supplied_note"]
        with self.assertRaises(ValueError):  # the marker needs the note that replaces the default
            direct_adapter.check_seed(seed, "test")

    def test_urls_in_named_attributes_are_enumerated(self):
        seed = self.seed([{"match": "^gnn129234:", "role": "gameplay_screenshot", "evidence_quotes": ["Dynamix"]}],
                         items=[])
        config = seed["params"]["article_images"][0]
        config.update({"og_image": False, "attr_urls": ["onclick"],
                       "pattern": "https://news\\.example\\.org/SS/(?P<key>\\d+)\\.jpg"})
        page = ("<html><head><title>Dynamix column</title></head><body><h1>Dynamix column #862 (long headline)</h1>"
                "<div class='article-body'>" + "".join(
                    f"<a href='./gallery?num={n}' onclick=\"return OPEN(event,'/SS/{n}.jpg','1136','640');\">"
                    f"<img src='/TN/{n}.jpg' alt='thumb'></a>" for n in ("003", "012", "011")) + "</div></body></html>")
        network = FakeNetwork({self.PAGE: {"text": page}})
        self.run_family([seed], network, metadata_only=True)
        snapshot = manifest.load_discovery(self.root, "archives:press")
        titles = {c["upstream_key"]: c["title"] for c in snapshot["candidates"]}
        thumbs = {f"gnn129234:other:news.example.org/TN/{n}.jpg":
                  f"Dynamix column #862 (long headline) — thumb (other image {i}/3)"
                  for i, n in enumerate(("003", "012", "011"), start=1)}
        self.assertEqual(titles, {"gnn129234:003": "Dynamix column #862 (long headline) (1/3)",
                                  "gnn129234:012": "Dynamix column #862 (long headline) (2/3)",
                                  "gnn129234:011": "Dynamix column #862 (long headline) (3/3)", **thumbs})
        self.assertTrue(snapshot["discovery_complete"])

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

    def test_headline_prefers_a_real_heading_over_a_bare_game_name(self):
        from primary.adapters import base as adapter_base
        page = ("<html><head><title>画像ギャラリー | iPhone/iPad版 Dynamix</title>"
                "<meta property='og:title' content='画像ギャラリー | iPhone/iPad版 Dynamix'></head>"
                "<body><h1>Dynamix</h1></body></html>")
        self.assertEqual(adapter_base.headline(adapter_base.soup(page)), "画像ギャラリー | iPhone/iPad版 Dynamix")
        article = "<html><head><title>X - Site</title></head><body><h1>音樂遊戲《Dynamix》近期更新內容預告</h1></body></html>"
        self.assertEqual(adapter_base.headline(adapter_base.soup(article)), "音樂遊戲《Dynamix》近期更新內容預告")

    def test_rule_checks_for_titles_and_archive_under(self):
        with self.assertRaises(ValueError):  # a file name is not a title
            model.check_rule({"match": "x", "role": "promotional", "title": "Screenshot ({filename})"}, "test")
        with self.assertRaises(ValueError):  # archive_under only on a download rule
            model.check_rule({"match": "x", "action": "exclude", "reason": "site_ui: x", "archive_under": "y"}, "t")
        model.check_rule({"match": "x", "role": "reference", "archive_under": "y:1"}, "test")

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

    def test_archive_under_records_the_file_under_the_named_candidate(self):
        # Two listings carry identical bytes; a curated rule says the file belongs under img/b.png (whose text names
        # what it shows). An existing record of img/a.png is taken over (same asset ID, b's own fetch and path) and
        # img/a.png becomes duplicate_bytes; a fresh acquisition gives the same result directly.
        routes = {self.PAGE: self.page("a.png", "b.png"), "https://press.example.org/img/a.png": self.file((5, 6, 7)),
                  "https://press.example.org/img/b.png": self.file((5, 6, 7))}
        self.run_family([self.seed()], FakeNetwork(routes))
        first = self.candidates("archives:press")
        self.assertEqual((first["img/a.png"]["disposition"], first["img/b.png"]["disposition"]),
                         ("downloaded", "duplicate_bytes"))
        old_path = self.manifest()["assets"][0]["path"]
        rules = [{"match": "^img/a\\.png$", "role": "promotional", "game": "dynamix", "archive_under": "img/b.png"},
                 {"match": "^img/", "role": "promotional", "game": "dynamix"}]
        for _ in range(2):  # the second run changes nothing
            self.run_family([self.seed(rules=rules)], FakeNetwork(routes), resume=True)
            found = self.candidates("archives:press")
            self.assertEqual((found["img/a.png"]["disposition"], found["img/b.png"]["disposition"]),
                             ("duplicate_bytes", "downloaded"))
            [asset] = self.manifest()["assets"]
            self.assertEqual(asset["id"], first["img/a.png"]["asset_id"])
            self.assertTrue(asset["candidate_id"].endswith("img/b.png"))
            self.assertEqual(asset["download_url"], "https://press.example.org/img/b.png")
            self.assertIn("archive_under", asset["notes"])
            self.assertFalse(paths.safe_path(self.root, old_path).exists())
            self.verify()
        fresh = tempfile.TemporaryDirectory()
        self.addCleanup(fresh.cleanup)
        runner.Runner(fresh.name, "archives", [self.seed(rules=rules)], client_factory=FakeNetwork(routes).factory,
                      now=lambda: FIXED_NOW, sleep=lambda s: None).run()
        [asset] = manifest.load_manifest(Path(fresh.name), "archives")["assets"]
        self.assertTrue(asset["candidate_id"].endswith("img/b.png"))
        self.assertNotIn("archive_under", asset.get("notes") or "")  # nothing was taken over

    TAKE_OVER_RULES = [{"match": "^img/a\\.png$", "role": "promotional", "game": "dynamix", "archive_under": "img/b.png"},
                       {"match": "^img/", "role": "promotional", "game": "dynamix"}]

    def test_archive_under_supersedes_the_target_candidates_own_earlier_file(self):
        # img/b.png already holds its own earlier bytes, then serves img/a.png's bytes: b takes the record over, its own
        # earlier record is superseded (bytes kept), a's old file leaves only after the new manifest is written.
        a_url, b_url = "https://press.example.org/img/a.png", "https://press.example.org/img/b.png"
        routes = {self.PAGE: self.page("a.png", "b.png"), a_url: self.file((5, 6, 7)), b_url: self.file((1, 1, 1))}
        self.run_family([self.seed()], FakeNetwork(routes))
        before = self.assets()
        old_a, old_b = before["img/a.png"], before["img/b.png"]
        routes[b_url] = self.file((5, 6, 7))
        acquisition = runner.Runner(self.root, "archives", [self.seed(rules=self.TAKE_OVER_RULES)],
                                    client_factory=FakeNetwork(routes).factory, now=lambda: FIXED_NOW,
                                    sleep=lambda s: None)
        acquisition.run()
        self.assertEqual(acquisition.source_errors, {})
        assets = {asset["id"]: asset for asset in self.manifest()["assets"]}
        self.assertEqual((assets[old_b["id"]]["upstream_status"], assets[old_b["id"]]["superseded_by"]),
                         ("superseded", old_a["id"]))
        self.assertTrue(paths.safe_path(self.root, old_b["path"]).is_file())  # earlier bytes kept
        taken = assets[old_a["id"]]
        self.assertTrue(taken["candidate_id"].endswith("img/b.png"))
        self.assertFalse(paths.safe_path(self.root, old_a["path"]).exists())
        found = self.candidates("archives:press")
        self.assertEqual((found["img/a.png"]["disposition"], found["img/b.png"]["disposition"]),
                         ("duplicate_bytes", "downloaded"))
        self.verify()

    def test_archive_under_clears_the_target_candidates_earlier_failure(self):
        a_url, b_url = "https://press.example.org/img/a.png", "https://press.example.org/img/b.png"
        routes = {self.PAGE: self.page("a.png", "b.png"), a_url: self.file((5, 6, 7)),
                  b_url: {"error": net.HttpError("HTTP 503", url=b_url, http_status=503)}}
        self.run_family([self.seed()], FakeNetwork(routes))
        self.assertEqual([f["candidate_id"] for f in self.manifest()["failures"]], ["archives:press:img/b.png"])
        routes[b_url] = self.file((5, 6, 7))
        self.run_family([self.seed(rules=self.TAKE_OVER_RULES)], FakeNetwork(routes), resume=True)
        self.assertEqual(self.manifest()["failures"], [])
        self.assertEqual(self.candidates("archives:press")["img/b.png"]["disposition"], "downloaded")
        self.verify()

    def test_a_failed_write_after_a_take_over_keeps_the_previous_file(self):
        routes = {self.PAGE: self.page("a.png", "b.png"), "https://press.example.org/img/a.png": self.file((5, 6, 7)),
                  "https://press.example.org/img/b.png": self.file((5, 6, 7))}
        self.run_family([self.seed()], FakeNetwork(routes))
        [old] = self.manifest()["assets"]
        original = runner.manifest.write_json_atomic

        def refuse(path, data):
            if Path(path).name == "archives.json":
                raise OSError("disk full (test)")
            return original(path, data)
        runner.manifest.write_json_atomic = refuse
        try:
            acquisition = runner.Runner(self.root, "archives", [self.seed(rules=self.TAKE_OVER_RULES)],
                                        client_factory=FakeNetwork(routes).factory, now=lambda: FIXED_NOW,
                                        sleep=lambda s: None)
            acquisition.run()
        finally:
            runner.manifest.write_json_atomic = original
        self.assertIn("archives:press", acquisition.source_errors)
        self.assertEqual(self.manifest()["assets"], [old])  # the written manifest still names the old file ...
        self.assertTrue(paths.safe_path(self.root, old["path"]).is_file())  # ... which is still there

    def test_an_archive_under_target_that_is_not_listed_is_reported(self):
        routes = {self.PAGE: self.page("a.png"), "https://press.example.org/img/a.png": self.file((5, 6, 7))}
        rules = [{"match": "^img/a\\.png$", "role": "promotional", "game": "dynamix", "archive_under": "img/typo.png"},
                 {"match": "^img/", "role": "promotional", "game": "dynamix"}]
        self.run_family([self.seed(rules=rules)], FakeNetwork(routes))
        self.assertEqual(self.candidates("archives:press")["img/a.png"]["disposition"], "downloaded")
        errors = manifest.load_discovery(self.root, "archives:press")["errors"]
        self.assertTrue(any(runner.UNMATCHED_ARCHIVE_UNDER in error["message"] and "'img/typo.png'" in error["message"]
                            for error in errors), errors)
        warnings = self.verify()
        self.assertTrue(any(runner.UNMATCHED_ARCHIVE_UNDER in warning for warning in warnings), warnings)

    def test_one_file_has_one_kind_within_a_source(self):
        # Two listings with identical bytes, classified differently: --verify reports the duplicate whose role
        # disagrees with the kind of the record of its bytes.
        routes = {self.PAGE: self.page("a.png", "b.png"), "https://press.example.org/img/a.png": self.file((5, 6, 7)),
                  "https://press.example.org/img/b.png": self.file((5, 6, 7))}
        rules = [{"match": "^img/b\\.png$", "role": "reference", "game": "dynamix"},
                 {"match": "^img/", "role": "promotional", "game": "dynamix"}]
        self.run_family([self.seed(rules=rules)], FakeNetwork(routes))
        errors, _, _ = runner.verify_family(self.root, "archives")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("archives:press:img/b.png: role 'reference' differs from the kind 'promotional'", errors[0])

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


class FamilyConsistencyTests(ArchivesTestCase):
    """--verify within a primary family: one file, one kind, and song IDs that the reconciled song index gives."""

    @staticmethod
    def source(source_id, host, rule):
        return {"id": source_id, "name": source_id, "url": f"https://{host}/", "platform": "web",
                "adapter": "direct_files", "hosts": [host], "quality": QUALITY,
                "items": [{"key": "k1", "page_url": f"https://{host}/a", "file_url": f"https://{host}/1.png"}],
                "rules": [{"match": "^k1$", **rule}]}

    def network(self):
        png = image_bytes("PNG")
        return FakeNetwork({f"https://{host}/1.png": {"bytes": png, "content_type": "image/png"}
                            for host in ("a.example.org", "b.example.org")})

    def test_one_sha256_with_two_kinds_fails_verify(self):
        seeds = [self.source("archives:press", "a.example.org", {"role": "promotional"}),
                 self.source("archives:mag", "b.example.org", {"role": "reference"})]
        self.run_family(seeds, self.network())
        errors, _warnings, _ = runner.verify_family(self.root, "archives")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("records of the same bytes have different kinds (promotional: archives:press:", errors[0])
        self.assertIn("reference: archives:mag:", errors[0])

    def test_song_ids_come_from_the_song_index(self):
        index = {"schema_version": 1, "generated_from": [], "as_of": "2026-10-10", "disagreements": [], "songs": [
            {"id": song_id, "title": title, "aliases": [], "composers": [], "illustrators": [], "collections": [],
             "in_original_game": True, "inclusion_evidence": ["test"], "universe_also": False, "removed": False,
             "evidence": [], "artwork_asset_ids": []}
            for song_id, title in (("song:hope", "HOPE"), ("song:hope-2", "Hope!!"))]}
        manifest.write_json_atomic(self.root / "data/sources/song-index.json", index)
        rule = {"role": "song_art", "song_titles": ["Hope!!"], "mapping_status": "inferred"}
        self.run_family([self.source("archives:press", "a.example.org", rule)], self.network())
        asset = self.manifest()["assets"][0]
        self.assertEqual((asset["song_titles"], asset["song_ids"]), (["Hope!!"], ["song:hope-2"]))  # not song:hope
        self.verify()
        data = self.manifest()
        data["assets"][0]["song_ids"] = ["song:hope"]  # the title-derived base ID names the other song
        manifest.write_json_atomic(self.root / "data/sources/archives.json", data)
        errors, _warnings, _ = runner.verify_family(self.root, "archives")
        self.assertTrue(any("'song:hope' is the song 'HOPE'" in error for error in errors), errors)


class GuardedSpacingTests(unittest.TestCase):
    """GuardedClient spaces requests per host across the per-source clients of a run (frozen >= 1.0 s default),
    including a host that answered only after a redirect."""

    def test_waits_are_kept_across_sources_and_redirect_hosts(self):
        clock, sleeps = [100.0], []

        def sleep(seconds):
            sleeps.append(round(seconds, 3))
            clock[0] += seconds

        class Inner:
            def get_text(self, url, **_):
                clock[0] += 0.25
                return "", url, {}

            def download(self, url, dest, stem, **_):
                clock[0] += 0.25
                return net.DownloadResult(Path("unused.part"), "http://b.example/target", 200, "image/png", 1, "0" * 64,
                                          "0" * 40, {})

        shared = {"blocked_hosts": {}, "last_used": {}, "min_interval": 1.0, "sleep": sleep, "clock": lambda: clock[0]}
        source_a = runner.GuardedClient(Inner(), **shared)
        source_b = runner.GuardedClient(Inner(), **shared)
        source_a.get_text("http://a.example/1")
        source_a.get_text("http://a.example/2")  # same host: waits
        self.assertEqual(sleeps, [1.0])
        source_a.download("http://a.example/redirect", None, "x")  # a.example again, answered by b.example
        self.assertEqual(sleeps, [1.0, 1.0])
        source_b.get_text("http://b.example/other")  # another source, the redirect target: waits too
        self.assertEqual(sleeps, [1.0, 1.0, 1.0])
        source_b.get_text("http://c.example/fresh")  # an untouched host: no wait
        self.assertEqual(sleeps, [1.0, 1.0, 1.0])

    def test_default_clients_of_a_run_share_one_schedule(self):
        with tempfile.TemporaryDirectory() as temp:
            acquisition = runner.Runner(temp, "archives", [])
        first = acquisition.client_factory(["a.example"], 1.0)
        second = acquisition.client_factory(["a.example"], 1.0)
        self.assertIs(first._schedule, acquisition.host_schedule)
        self.assertIs(second._schedule, acquisition.host_schedule)
        first.close()
        second.close()


class CliExitTests(ArchivesTestCase):
    """The shared primary CLI (fetch_artists/official/archives.py): exit status and the family lock."""

    def write_seeds(self):
        path = self.root / "seeds.json"
        path.write_text(json.dumps({"family": "archives", "sources": [{
            "id": "archives:press", "name": "Press", "url": "https://news.example.org/", "platform": "web",
            "adapter": "direct_files", "hosts": ["news.example.org", "img.example.org"], "quality": QUALITY,
            "items": [{"key": "k1", "page_url": "https://news.example.org/a", "file_url": "https://img.example.org/1.png"},
                      {"key": "k2", "page_url": "https://news.example.org/a",
                       "file_url": "https://img.example.org/2.png"}],
            "rules": [{"match": "^k[12]$", "role": "promotional"}]}]}), encoding="utf-8")
        return path

    def main(self, routes, *extra):
        network = FakeNetwork(routes)
        err = io.StringIO()
        with mock.patch.object(net, "PoliteClient", lambda hosts, **kw: network.factory(hosts, kw.get("min_interval",
                                                                                                         1.0))), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = cli.main("archives", ["--root", str(self.root), "--seeds", str(self.write_seeds()), *extra])
        return code, err.getvalue()

    def routes(self, error=None):
        ok = {"bytes": image_bytes("PNG"), "content_type": "image/png"}
        return {"https://img.example.org/1.png": {"error": error} if error else ok,
                "https://img.example.org/2.png": {"bytes": image_bytes("PNG", (9, 9)), "content_type": "image/png"}}

    def test_failed_or_blocked_candidates_exit_one(self):
        url = "https://img.example.org/1.png"
        for error, disposition in ((net.HttpError("HTTP 500", url=url, http_status=500, retriable=True), "failed"),
                                   (net.TimeoutFetchError("timed out", url=url), "failed"),
                                   (net.BlockedError("access refused (HTTP 403)", url=url, http_status=403), "blocked")):
            with self.subTest(error=type(error).__name__):
                code, err = self.main(self.routes(error))
                self.assertEqual(code, 1, err)
                self.assertEqual(self.candidates("archives:press")["k1"]["disposition"], disposition)
                self.assertIn(f"archives:press left 1 {disposition}", err)
        code, err = self.main(self.routes())
        self.assertEqual(code, 0, err)
        self.assertEqual({c["disposition"] for c in self.candidates("archives:press").values()}, {"downloaded"})

    def test_explicit_lock_does_not_replace_the_family_lock(self):
        held = cli.acquire_lock(cli.default_lock_path(self.root, "archives"))
        try:
            code, err = self.main(self.routes(), "--lock", str(self.root / "other.lock"))
            self.assertEqual(code, 3)
            self.assertIn("another acquisition holds the lock", err)
            self.assertFalse((self.root / "data/sources/archives.json").exists())
        finally:
            held.close()
        other = cli.acquire_lock(self.root / "other.lock")  # the additional lock was released (or never taken)
        self.assertIsNotNone(other)
        try:
            code, err = self.main(self.routes(), "--lock", str(self.root / "other.lock"))
            self.assertEqual(code, 3)  # the additional lock is honoured too
        finally:
            other.close()
        code, err = self.main(self.routes(), "--lock", str(self.root / "other.lock"))
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
