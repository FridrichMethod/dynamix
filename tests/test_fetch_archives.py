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
from primary.adapters import wayback  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
