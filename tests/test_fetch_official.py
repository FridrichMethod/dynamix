"""Offline tests for scripts/fetch_official.py: App Store shelves and iTunes lookup sets, Google Play native sizes,
TapTap uploads, the X embed timeline sample, availability probes, offline registry sources, the CLI lock and
``--verify``. Run: ``python -I tests/test_fetch_official.py``.
"""

import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import manifest, net, paths  # noqa: E402
from primary import cli, model, runner  # noqa: E402
from primary.adapters import apple, facebook, probe, registry as registry_adapter, x as x_adapter, youtube  # noqa: E402,E501
from primary.testing import FIXED_NOW, FakeNetwork, full_url, image_bytes  # noqa: E402

FIXTURES = ROOT / "tests/fixtures/official"
QUALITY = {"source_variant": "site_original", "origin_status": "official", "master_status": "unverified",
           "notes": "test"}


def text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class OfficialTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_family(self, seeds, network, **options):
        acquisition = runner.Runner(self.root, "official", sorted(seeds, key=lambda s: s["id"]),
                                    client_factory=network.factory, now=lambda: FIXED_NOW, sleep=lambda s: None,
                                    **options)
        return acquisition.run()

    def candidates(self, source_id):
        return {c["upstream_key"]: c for c in manifest.load_discovery(self.root, source_id)["candidates"]}

    def manifest(self):
        return manifest.load_manifest(self.root, "official")

    def verify(self):
        errors, warnings, counts = runner.verify_family(self.root, "official")
        self.assertEqual(errors, [], errors)
        return counts


class AppleTests(OfficialTestCase):
    PAGE = "https://apps.apple.com/us/app/dynamix/id945955095"

    def seed(self, items):
        return {"id": "official:apple-store", "name": "Apple", "url": self.PAGE, "platform": "app-store",
                "adapter": "apple_store", "identity_quotes": ["C4Cat"], "quality": QUALITY, "items": items,
                "rules": [{"match": ".*", "role": "store_screenshot", "game": "dynamix"}]}

    def test_product_media_shelves_at_native_size(self):
        routes = {self.PAGE: {"text": text("apple_page.html")}}
        expected = {
            "phone:f09cad41-0b41-d96d-5c48-be8c76b110f4": "https://is1-ssl.mzstatic.com/image/thumb/Purple123/v4/f0/9c/"
            "ad/f09cad41-0b41-d96d-5c48-be8c76b110f4/pr_source.png/2688x1242bb.png",
            "pad:a89c0c45-e49f-72ab-0a80-754c422d057f": "https://is1-ssl.mzstatic.com/image/thumb/Purple123/v4/f0/9c/"
            "ad/a89c0c45-e49f-72ab-0a80-754c422d057f/pr_source.png/2732x2048bb.png",
        }
        for index, url in enumerate(expected.values()):
            routes[url] = {"bytes": image_bytes("PNG", (80, 37 + index)), "content_type": "image/png"}
        network = FakeNetwork(routes)
        self.run_family([self.seed([{"url": self.PAGE}])], network)
        found = self.candidates("official:apple-store")
        for key, url in expected.items():
            # The PNG upload is requested in Apple's lossless PNG rendition, not the page's JPEG (quality 70) one.
            self.assertEqual(found[key]["file_url"], url)
            self.assertEqual(found[key]["disposition"], "downloaded")
            self.assertEqual(found[key]["source_reported"]["url"], url[: -len("png")] + "jpeg")
            # The stored PNG is another rendition by construction: the advertised JPEG format is not comparable.
            self.assertIsNone(found[key]["source_reported"]["mime"])
            self.assertIn("lossless rendition", found[key]["notes"])
        self.assertEqual(found["phone:video-2"]["disposition"], "excluded")  # trailer item
        self.assertTrue(found["phone:video-2"]["reason"].startswith("video_placeholder"))
        self.assertEqual(found["phone:1a2b3c4d-0000-1111-2222-333344445555"]["disposition"], "failed")  # no route
        assets = self.manifest()["assets"]
        self.assertTrue(all(a["transformed"] and a["kind"] == "store_screenshot" for a in assets))
        phone = next(a for a in assets if a["candidate_id"].endswith("f09cad41-0b41-d96d-5c48-be8c76b110f4"))
        self.assertEqual((phone["source_reported"]["width"], phone["width"]), (2688, 80))
        self.assertIs(phone["comparisons"]["dimensions_match"], False)
        self.assertIsNone(phone["comparisons"]["format_match"])  # advertised JPEG variant, requested PNG
        self.assertEqual(phone["format"], "PNG")
        self.assertIn("lossless rendition", phone["notes"])  # candidate notes reach the asset record
        self.assertTrue(phone["quality"]["notes"].startswith("Apple's PNG rendition of the PNG upload at native size"))
        self.assertIn(apple.IDENTIFIER_NOTE, phone["quality"]["notes"])
        self.assertEqual(self.manifest()["sources"][0]["status"], "partially_available")
        self.verify()

    def test_an_unusable_artwork_template_is_held_as_unresolved_role(self):
        # 09: a template that renders no URL leaves no file to fetch: unresolved_role, never awaiting_download.
        page = text("apple_page.html")
        good = "a89c0c45-e49f-72ab-0a80-754c422d057f/pr_source.png/{w}x{h}{c}.{f}"
        self.assertEqual(page.count(good), 1)
        routes = {self.PAGE: {"text": page.replace(good, good[: -len("{f}")] + "{q}")}}
        self.run_family([self.seed([{"url": self.PAGE}])], FakeNetwork(routes))
        held = self.candidates("official:apple-store")["pad:a89c0c45-e49f-72ab-0a80-754c422d057f"]
        self.assertEqual(held["disposition"], "pending")
        self.assertEqual(held["reason"], "unresolved_role: unusable artwork template")
        self.assertFalse(held.get("file_url"))
        self.verify()

    def test_shelf_parser_reads_the_observed_structure(self):
        mapping = apple.shelves(apple.server_data(text("apple_page.html")))
        self.assertEqual([(d, i) for d, i, _ in apple.media_items(mapping)], [("pad", 1), ("phone", 1), ("phone", 2),
                                                                              ("phone", 3)])

    def test_itunes_lookup_sets_requested_at_native_size_and_merged_across_storefronts(self):
        routes = {}
        for country in ("us", "jp"):
            url = full_url(apple.LOOKUP, {"id": "945955095", "country": country, "entity": "software"})
            routes[url] = {"json": json.loads(text(f"itunes_lookup_{country}.json"))}
        data = [json.loads(text(f"itunes_lookup_{c}.json"))["results"][0] for c in ("us", "jp")]
        urls = sorted({u for app in data for u in app["screenshotUrls"] + app["ipadScreenshotUrls"]})
        for index, url in enumerate(urls):
            routes[apple.native_lookup_url(url)] = {"bytes": image_bytes("PNG", (60 + index, 34)),
                                                    "content_type": "image/png"}
        network = FakeNetwork(routes)
        self.run_family([self.seed([{"lookup_id": "945955095", "countries": ["us", "jp"]}])], network)
        found = self.candidates("official:apple-store")
        self.assertEqual(len(found), 3)  # the US/JP shared screenshot is one candidate
        shared = found["lookup:4b59219a-06f6-3e05-317d-595eca968375"]
        self.assertTrue(shared["file_url"].endswith("/pr_source.png/4000x4000bb.png"))  # the upload's format
        self.assertTrue(shared["source_reported"]["url"].endswith("/pr_source.png/392x696bb.jpg"))  # as advertised
        self.assertIsNone(shared["source_reported"]["mime"])  # a JPEG rendition was advertised, a PNG one stored
        self.assertEqual(apple.same_format_mime("https://e.test/a.png/1x1bb.jpg", "https://e.test/a.png/9x9bb.jpg"),
                         "image/jpeg")
        self.assertIn("us:phone1", shared["notes"])
        self.assertIn("jp:phone2", shared["notes"])
        self.assertIn("lookup:92092603-00f8-d690-9f01-b4437bda3e80", found)
        self.assertEqual({c["disposition"] for c in found.values()}, {"downloaded"})
        self.verify()

    def test_quality_note_describes_the_rendition_actually_stored(self):
        sized = "https://is1-ssl.mzstatic.com/image/thumb/Purple/v4/aa/bb/cc/"
        lookup = {"resultCount": 1, "results": [{"trackId": 945955095, "trackName": "Dynamix", "sellerName": "C4Cat",
                                                 "bundleId": "com.c4cat.dynamix", "ipadScreenshotUrls": [],
                                                 "screenshotUrls": [sized + "mzl.sklhwvmk.jpg/406x228bb.jpg",
                                                                    sized + "mzl.eumuhmxw.png/406x228bb.png"]}]}
        routes = {full_url(apple.LOOKUP, {"id": "945955095", "country": "hk", "entity": "software"}): {"json": lookup},
                  sized + "mzl.sklhwvmk.jpg/4000x4000bb.jpg": {"bytes": image_bytes("JPEG", (40, 30)),
                                                               "content_type": "image/jpeg"},
                  sized + "mzl.eumuhmxw.png/4000x4000bb.png": {"bytes": image_bytes("PNG", (40, 31)),
                                                               "content_type": "image/png"}}
        seed = self.seed([{"lookup_id": "945955095", "countries": ["hk"]}])
        seed["quality"] = {**QUALITY, "notes": "A store re-render of the upload."}
        self.run_family([seed], FakeNetwork(routes))
        notes = {a["format"]: a["quality"]["notes"] for a in self.manifest()["assets"]}
        self.assertTrue(notes["JPEG"].startswith("Apple's JPEG rendition of the JPEG upload at native size (lossy"))
        self.assertNotIn("(lossless", notes["JPEG"])
        self.assertTrue(notes["PNG"].startswith("Apple's PNG rendition of the PNG upload at native size (lossless"))
        self.assertTrue(all("A store re-render of the upload." in n and apple.IDENTIFIER_NOTE in n
                            for n in notes.values()))
        self.verify()

    def test_an_empty_lookup_answer_keeps_the_list_open(self):
        url = full_url(apple.LOOKUP, {"id": "945955095", "country": "hk", "entity": "software"})
        self.run_family([self.seed([{"lookup_id": "945955095", "countries": ["hk"]}])],
                        FakeNetwork({url: {"json": {"resultCount": 0, "results": []}}}))
        snapshot = manifest.load_discovery(self.root, "official:apple-store")
        self.assertEqual(snapshot["continuation"]["itunes-lookup"], "no-result-hk")
        self.assertFalse(snapshot["discovery_complete"])

    def test_lossless_rendition_follows_the_upload_format(self):
        sized = "https://is1-ssl.mzstatic.com/image/thumb/Purple/v4/aa/bb/cc/"
        self.assertEqual(apple.native_lookup_url(sized + "mzl.eumuhmxw.png/406x228bb.png"),
                         sized + "mzl.eumuhmxw.png/4000x4000bb.png")
        self.assertEqual(apple.native_lookup_url(sized + "mzl.abc.jpg/406x228bb.jpg"),
                         sized + "mzl.abc.jpg/4000x4000bb.jpg")  # a JPEG upload gains nothing from PNG
        rendition = apple.native_rendition({"template": sized + "x/pr_source.jpg/{w}x{h}{c}.{f}", "width": 10,
                                            "height": 5, "crop": "bb", "variants": [{"format": "jpeg"}]})
        self.assertEqual((rendition["format"], rendition["advertised_format"]), ("jpeg", "jpeg"))


class GooglePlayTests(OfficialTestCase):
    EN = "https://play.google.com/store/apps/details?id=com.c4cat.dynamix&hl=en_US&gl=US"
    JA = "https://play.google.com/store/apps/details?id=com.c4cat.dynamix&hl=ja&gl=JP"
    P = "https://play-lh.googleusercontent.com/P3sU0wnQ9kUeB-hjBw50MTW0Kil_mGlLjdlFWKISt5Y0O-GYB46LNk1UQdqw90ub57SkQa98W5haM7R-5lWzkP4"
    Q = "https://play-lh.googleusercontent.com/QC8ectWHiiahajhJcvSgsmQH4T5_3dckTukIMeh02gD1oRTQ60M0y8TpJjdpbCTrsFZTTN7W8hdkJh5yqAkM"

    def test_native_size_is_decoded_not_taken_from_the_url(self):
        network = FakeNetwork({
            self.EN: {"text": text("google_play_en.html")}, self.JA: {"text": text("google_play_ja.html")},
            f"{self.P}=s0": {"bytes": image_bytes("PNG", (1052, 591)), "content_type": "image/png"},
            f"{self.Q}=s0": {"bytes": image_bytes("PNG", (128, 72), (1, 2, 3)), "content_type": "image/png"}})
        seed = {"id": "official:google-play", "name": "Google Play", "platform": "google-play",
                "url": "https://play.google.com/store/apps/details?id=com.c4cat.dynamix", "adapter": "google_play",
                "identity_quotes": ["C4Cat"], "quality": QUALITY, "items": [{"url": self.EN}, {"url": self.JA}],
                "rules": [{"match": "^screenshot:", "role": "store_screenshot"}]}
        self.run_family([seed], network)
        found = self.candidates("official:google-play")
        self.assertEqual(sorted(found), ["icon:IconTokenAbcdef0123456789", f"screenshot:{self.P.rsplit('/', 1)[1]}",
                                         f"screenshot:{self.Q.rsplit('/', 1)[1]}"])
        first = found[f"screenshot:{self.P.rsplit('/', 1)[1]}"]
        self.assertIn("1052x592", first["notes"])  # the advertised resize request is kept as a note only
        second = found[f"screenshot:{self.Q.rsplit('/', 1)[1]}"]
        self.assertIn(self.JA, second["notes"])  # listed by both locales, recorded once
        self.assertTrue(found["icon:IconTokenAbcdef0123456789"]["reason"].startswith("game_ui"))
        asset = next(a for a in self.manifest()["assets"] if a["download_url"] == f"{self.P}=s0")
        self.assertEqual((asset["width"], asset["height"]), (1052, 591))
        self.assertIsNone(asset["source_reported"]["height"])
        # The page advertises a resize rendition; the derived =s0 URL is only the download URL.
        self.assertTrue(asset["source_reported"]["url"].startswith(f"{self.P}=w"), asset["source_reported"]["url"])
        self.assertEqual(network.count("play-lh.googleusercontent.com"), 2)
        self.verify()

    def test_refused_listing_pages_are_one_candidate_each_and_later_sources_still_run(self):
        # A 403 on the first locale makes the host skipped for the others: both refusals must be distinct candidates
        # (they once collided as "page" and aborted the family run before official:website).
        network = FakeNetwork({self.EN: {"error": net.BlockedError("HTTP 403", url=self.EN, http_status=403)},
                               "https://dynamix.c4-cat.com/": {"text": "<title>Dynamix</title>"}})
        seed = {"id": "official:google-play", "name": "Google Play", "platform": "google-play", "url": self.EN,
                "adapter": "google_play", "quality": QUALITY, "items": [{"url": self.EN}, {"url": self.JA}],
                "rules": [{"match": "^screenshot:", "role": "store_screenshot"}]}
        website = {"id": "official:website", "name": "Website", "url": "https://dynamix.c4-cat.com/",
                   "platform": "web", "adapter": "page_probe", "hosts": ["dynamix.c4-cat.com"],
                   "items": [{"url": "https://dynamix.c4-cat.com/"}]}
        self.run_family([seed, website], network)
        found = self.candidates("official:google-play")
        self.assertEqual(sorted(found), [f"page:{self.EN}", f"page:{self.JA}"])
        self.assertEqual({c["disposition"] for c in found.values()}, {"blocked"})
        self.assertEqual(network.count("play.google.com"), 1)  # the second listing is skipped, not requested
        snapshot = manifest.load_discovery(self.root, "official:google-play")
        self.assertFalse(snapshot["discovery_complete"])  # the screenshots were never enumerated
        self.assertTrue(snapshot["continuation"]["listing-screenshots"].startswith("not-read:"))
        self.assertIsNotNone(manifest.load_discovery(self.root, "official:website"))
        self.verify()


class TapTapTests(OfficialTestCase):
    def test_original_uploads_and_banner_rendition(self):
        base = "https://img-tc.tapimg.com/market/images/"
        shot_png = image_bytes("PNG", (22, 12))
        import hashlib
        token = hashlib.md5(shot_png).hexdigest()
        page = text("taptap_app.html").replace("0f46c7d8f23cfefc08f151e5de5f02ee", token)
        payload = json.loads(page.split('data-ssr="true">', 1)[1].split("</script>", 1)[0])
        app = next(v for v in payload if isinstance(v, dict) and "screenshots" in v)
        payload[app["screenshots"]].append(payload[app["screenshots"]][0])  # the same upload listed twice
        page = page.split('data-ssr="true">', 1)[0] + 'data-ssr="true">' + json.dumps(payload) + "</script>" + \
            page.split('data-ssr="true">', 1)[1].split("</script>", 1)[1]
        network = FakeNetwork({
            "https://www.taptap.cn/app/7082": {"text": page},
            f"{base}{token}.png": {"bytes": shot_png, "content_type": "image/png"},
            f"{base}1111c7d8f23cfefc08f151e5de5f0222.jpg": {"bytes": image_bytes("JPEG", (13, 7)),
                                                            "content_type": "image/jpeg"},
            f"{base}4395ff13f01cca52f28efb2a1694bce9.jpg": {"bytes": image_bytes("JPEG", (102, 50)),
                                                            "content_type": "image/jpeg"}})
        seed = {"id": "official:taptap", "name": "TapTap", "url": "https://www.taptap.cn/app/7082",
                "platform": "taptap", "adapter": "taptap_app", "params": {"identifier": "com.c4cat.dynamix"},
                "identity_quotes": ["C4Cat"], "quality": QUALITY, "items": [{"url": "https://www.taptap.cn/app/7082"}],
                "rules": [{"match": "^screenshot:", "role": "store_screenshot"},
                          {"match": "^banner:[0-9a-f]{32}\\.", "role": "promotional"}]}
        self.run_family([seed], network)
        found = self.candidates("official:taptap")
        rendition = found["banner:4395ff13f01cca52f28efb2a1694bce9_crop_888x500a76a0_thumbnail_1920x1080.jpg"]
        self.assertTrue(rendition["reason"].startswith("upscaled"))  # an 888x500 crop shown at 1920x1080
        self.assertIn("enlarged from a 888x500 crop to 1920x1080", rendition["reason"])
        self.assertEqual(found["banner:4395ff13f01cca52f28efb2a1694bce9.jpg"]["disposition"], "downloaded")
        banner = next(a for a in self.manifest()["assets"] if "banner" in a["candidate_id"])
        # The page lists only the rendition: its URL stays source_reported.url, the derived upload URL is only
        # downloaded, and the rendition's size and format (another rendition by construction) are not compared.
        self.assertEqual(banner["source_reported"]["url"], rendition["file_url"])
        self.assertEqual([banner["source_reported"][k] for k in ("width", "height", "mime", "bytes")], [None] * 4)
        self.assertEqual(set(banner["comparisons"].values()), {None})
        self.assertIn("thumbnail_1920x1080.jpg (1920x1080 advertised)", banner["notes"])
        self.assertEqual(banner["download_url"], f"{base}4395ff13f01cca52f28efb2a1694bce9.jpg")
        self.assertTrue(found["icon:23c51666ae309b07d5f4fe5d888157e5.png"]["reason"].startswith("game_ui"))
        assets = {a["candidate_id"].split(":", 2)[2]: a for a in self.manifest()["assets"]}
        self.assertIn("equals the upstream name token", assets[f"screenshot:{token}"]["quality"]["notes"])
        self.assertNotIn("transformed", assets[f"screenshot:{token}"])
        reencoded = assets["screenshot:1111c7d8f23cfefc08f151e5de5f0222"]
        self.assertIn("differs from the upstream name token", reencoded["quality"]["notes"])
        self.assertIs(reencoded["transformed"], True)  # a known platform transform, not the site's original
        self.assertEqual(reencoded["quality"]["source_variant"], "unknown")
        self.assertEqual(assets[f"screenshot:{token}"]["source_reported"]["bytes"], 589803)
        self.assertIn("Listed again at screenshot position 3", found[f"screenshot:{token}"]["notes"])
        self.assertIs(assets[f"screenshot:{token}"]["comparisons"]["bytes_match"], False)
        self.verify()


    def test_every_trailer_poster_frame_is_accounted_for(self):
        page = text("taptap_app.html")
        head, rest = page.split('data-ssr="true">', 1)
        body, tail = rest.split("</script>", 1)
        payload = json.loads(body)
        app = next(v for v in payload if isinstance(v, dict) and "screenshots" in v)
        posters = ["https://img-tc.tapimg.com/video-picture/1001/first.jpg",
                   "https://img-tc.tapimg.com/video-picture/1002/first.jpg"]
        for url, field in zip(posters, ("thumbnail", "raw_cover")):  # raw_cover: the video's first frame
            base_index = len(payload)
            payload += [{"video": base_index + 1}, {field: base_index + 2}, {"original_url": base_index + 3}, url]
            payload[app["app_videos"]].append(base_index)
        page = head + 'data-ssr="true">' + json.dumps(payload) + "</script>" + tail
        network = FakeNetwork({"https://www.taptap.cn/app/7082": {"text": page}})
        seed = {"id": "official:taptap", "name": "TapTap", "url": "https://www.taptap.cn/app/7082",
                "platform": "taptap", "adapter": "taptap_app", "params": {"identifier": "com.c4cat.dynamix"},
                "quality": QUALITY, "items": [{"url": "https://www.taptap.cn/app/7082"}], "rules": []}
        self.run_family([seed], network)
        found = self.candidates("official:taptap")
        for url in posters:
            key = "video-poster:" + url.split("tapimg.com/", 1)[1]
            self.assertEqual(found[key]["disposition"], "excluded")
            self.assertTrue(found[key]["reason"].startswith("video_placeholder"))
            self.assertEqual(found[key]["file_url"], url)
        self.assertFalse(any(m == "download" and "video-picture" in u for m, u in network.calls))  # never fetched
        self.verify()

    def test_reencoded_copy_names_the_byte_exact_upload_archived_elsewhere(self):
        upload = image_bytes("PNG", (128, 72), (1, 2, 3))  # what Google Play =s0 serves
        token = hashlib.md5(upload).hexdigest()
        base = "https://img-tc.tapimg.com/market/images/"
        page = text("taptap_app.html").replace("1111c7d8f23cfefc08f151e5de5f0222", token)
        play = "https://play.google.com/store/apps/details?id=com.c4cat.dynamix&hl=en_US&gl=US"
        shot = ("https://play-lh.googleusercontent.com/QC8ectWHiiahajhJcvSgsmQH4T5_3dckTukIMeh02gD1oRTQ60M0y8TpJjdpbCT"
                "rsFZTTN7W8hdkJh5yqAkM")
        network = FakeNetwork({
            play: {"text": text("google_play_en.html")},
            f"{GooglePlayTests.P}=s0": {"bytes": image_bytes("PNG", (20, 10)), "content_type": "image/png"},
            f"{shot}=s0": {"bytes": upload, "content_type": "image/png"},
            "https://www.taptap.cn/app/7082": {"text": page},
            f"{base}{token}.jpg": {"bytes": image_bytes("JPEG", (128, 72), (1, 2, 3)), "content_type": "image/jpeg"}})
        google = {"id": "official:google-play", "name": "Google Play", "platform": "google-play", "url": play,
                  "adapter": "google_play", "quality": QUALITY, "items": [{"url": play}],
                  "rules": [{"match": "^screenshot:", "role": "store_screenshot"}]}
        taptap = {"id": "official:taptap", "name": "TapTap", "url": "https://www.taptap.cn/app/7082",
                  "platform": "taptap", "adapter": "taptap_app", "params": {"identifier": "com.c4cat.dynamix"},
                  "quality": QUALITY, "items": [{"url": "https://www.taptap.cn/app/7082"}],
                  "rules": [{"match": f"^screenshot:{token}$", "role": "store_screenshot"}]}
        self.run_family([google, taptap], network)
        assets = self.manifest()["assets"]
        exact = next(a for a in assets if a["download_url"] == f"{shot}=s0")
        copy = next(a for a in assets if a["candidate_id"] == f"official:taptap:screenshot:{token}")
        self.assertIs(copy["transformed"], True)
        self.assertIn(f"archived as {exact['id']}", copy["quality"]["notes"])
        self.verify()

    def test_banner_rendition_used_as_trailer_poster_still_yields_the_upload(self):
        from primary import runner as runner_module
        from primary.adapters import taptap

        url = ("https://img-tc.tapimg.com/market/images/4395ff13f01cca52f28efb2a1694bce9_crop_888x500a76a0_"
               "thumbnail_1920x1080.jpg")
        context = runner_module.AdapterContext(client=None, seed={}, source_id="official:taptap")
        drafts = taptap.banner_drafts(context, {"rules": [{"match": "^banner:", "role": "promotional"}]},
                                      {"original_url": url, "width": 1920, "height": 1080}, {url},
                                      "https://www.taptap.cn/app/7082", {}, [], [])
        self.assertEqual([d.action for d in drafts], ["excluded", "download"])
        self.assertIn("trailer's poster frame", drafts[0].reason)
        self.assertEqual(drafts[1].file_url,
                         "https://img-tc.tapimg.com/market/images/4395ff13f01cca52f28efb2a1694bce9.jpg")

    def test_a_downscaled_rendition_is_not_called_upscaled(self):
        from primary import runner as runner_module
        from primary.adapters import taptap

        url = ("https://img-tc.tapimg.com/market/images/4395ff13f01cca52f28efb2a1694bce9_crop_888x500a76a0_"
               "thumbnail_480x270.jpg")
        context = runner_module.AdapterContext(client=None, seed={}, source_id="official:taptap")
        drafts = taptap.banner_drafts(context, {"rules": [{"match": "^banner:", "role": "promotional"}]},
                                      {"original_url": url, "width": 480, "height": 270}, set(),
                                      "https://www.taptap.cn/app/7082", {}, [], [])
        self.assertTrue(drafts[0].reason.startswith("out_of_scope_other: TapTap crop/resize rendition"))
        self.assertIn("888x500 crop shown at 480x270", drafts[0].reason)


class XTimelineTests(OfficialTestCase):
    MEDIA = "https://pbs.twimg.com/media/GoqoEkJW4AA5saO?format=jpg&name=orig"

    def network(self, timeline=None):
        post_url = full_url(x_adapter.API, {"id": "981147690795900928", "lang": "en",
                                            "token": x_adapter.syndication_token("981147690795900928")})
        return FakeNetwork({
            x_adapter.TIMELINE + "c4c4cat": {"text": timeline or text("x_timeline.html")},
            post_url: {"json": {"__typename": "TweetTombstone"}},
            self.MEDIA: {"bytes": image_bytes("JPEG", (120, 63)), "content_type": "image/jpeg"}})

    def seed(self, rules, items=None):
        return {"id": "official:x", "name": "X", "url": "https://x.com/c4c4cat", "platform": "x", "adapter": "x_post",
                "params": {"handle": "c4c4cat"}, "quality": QUALITY,
                "items": items or [{"timeline": "c4c4cat"}, {"post_id": "981147690795900928"}], "rules": rules}

    def test_embed_timeline_is_a_sample_and_a_catch_all_rule_admits_nothing_from_it(self):
        network = self.network()
        self.run_family([self.seed([{"match": ".*", "role": "promotional", "game": "dynamix"}])], network)
        snapshot = manifest.load_discovery(self.root, "official:x")
        self.assertFalse(snapshot["discovery_complete"])
        self.assertEqual(snapshot["continuation"]["timeline:c4c4cat"], "sample-of-3-recent-posts")
        self.assertTrue(snapshot["continuation"]["seed-posts"].startswith("not-read:"))  # the tombstoned seed post
        found = {c["upstream_key"]: c for c in snapshot["candidates"]}
        # Recent posts are sequel news, other titles and greetings as often as Dynamix: never admitted by a catch-all.
        self.assertEqual(found["1913000000000000001:1"]["disposition"], "pending")
        self.assertIn("per-post seed rule", found["1913000000000000001:1"]["reason"])
        self.assertEqual(found["1913000000000000001:1"]["game"], "unknown")
        self.assertIn("not this source's account", found["1913000000000000002:1"]["reason"])
        self.assertEqual(found["1913000000000000002:1"]["game"], "unknown")
        self.assertEqual(found["981147690795900928"]["disposition"], "failed")
        self.assertEqual(network.count("pbs.twimg.com"), 0)
        self.assertEqual(self.manifest()["assets"], [])
        self.verify()

    def test_timeline_post_is_admitted_only_by_its_own_rule_and_guards_still_apply(self):
        rules = [{"match": "^(1913000000000000001|1913000000000000003):", "role": "promotional"}]
        network = self.network()
        self.run_family([self.seed(rules)], network)
        found = self.candidates("official:x")
        self.assertEqual(found["1913000000000000001:1"]["disposition"], "downloaded")
        held = found["1913000000000000003:1"]
        self.assertIn("Dynamix Universe", held["reason"])  # the guard holds even a per-post rule
        self.assertEqual(held["game"], "dynamix_universe")  # not recorded as an original-game candidate
        self.assertEqual(network.count("pbs.twimg.com"), 1)
        self.verify()

    def test_committed_official_x_rules_hold_a_sequel_post_from_the_timeline(self):
        page = text("x_timeline.html")
        match = re.search(r'(<script id="__NEXT_DATA__"[^>]*>)(.*?)(</script>)', page, re.S)
        data = json.loads(match.group(2))
        tweet = data["props"]["pageProps"]["timeline"]["entries"][0]["content"]["tweet"]
        for key in ("full_text", "text"):
            if key in tweet:
                tweet[key] = "DNXU 2.0 update out now on iOS/Android! New songs for the sequel."
        page = page[:match.start(2)] + json.dumps(data) + page[match.end(2):]
        seeds = runner.load_seeds(runner.SEEDS_DIR / "official.json", "official")
        seed = {**next(s for s in seeds if s["id"] == "official:x"), "items": [{"timeline": "c4c4cat"}]}
        network = self.network(page)
        self.run_family([seed], network)
        found = self.candidates("official:x")
        self.assertEqual(found["1913000000000000001:1"]["disposition"], "pending")
        self.assertEqual(network.count("pbs.twimg.com"), 0)
        self.assertEqual(self.manifest()["assets"], [])


class ClassificationSeedTests(unittest.TestCase):
    """The committed seeds cite the coordinator's classification decisions (09-frozen-contracts.md) only where they
    apply: D1 covers graphics that display chart levels, D2 graphics whose primary content is a list of songs or
    charts (lineup cards printing several titled jackets)."""

    CITATION = re.compile(r"kind reference \(coordinator decisions? (D\d)(?: and (D\d))?")

    def rules(self):
        for family in ("artists", "official", "archives"):
            for seed in runner.load_seeds(runner.SEEDS_DIR / f"{family}.json", family):
                for rule in seed.get("rules", []):
                    yield seed["id"], rule

    def test_a_cited_decision_matches_the_rule(self):
        cited = 0
        for source_id, rule in self.rules():
            notes = " ".join(filter(None, [rule.get("candidate_notes"), rule.get("notes")]))
            for match in self.CITATION.finditer(notes):
                cited += 1
                where = f"{source_id} {rule.get('match')}"
                self.assertEqual(rule.get("role"), "reference", where)
                if "D2" in match.groups():  # a list graphic: several titled songs, all mapped
                    self.assertGreaterEqual(len(rule.get("song_titles", [])), 2, where)
        self.assertGreater(cited, 10)

    def test_the_package_lovely_lineup_card_is_a_mapped_reference(self):
        rule = next(rule for source_id, rule in self.rules()
                    if source_id == "official:youtube" and rule.get("match") == "^Q4A5Wg5CViM$")
        self.assertEqual((rule["role"], rule["mapping_status"]), ("reference", "inferred"))
        self.assertEqual(rule["song_titles"], ["Crystal Ribbon", "Heart Eyes", "Super Macaron",
                                               "White Chocolate (dynamix edit)"])
        self.assertTrue(all(entry["note"].startswith("visual_inspection") for entry in rule["mapping_evidence"]))


class YouTubeTests(OfficialTestCase):
    CHANNEL = "https://www.youtube.com/@C4CatEntertainment"
    VIDEO = "FInFw2lVz_I"

    def seed(self, items, rules=None):
        return {"id": "official:youtube", "name": "YouTube", "url": self.CHANNEL, "platform": "youtube",
                "adapter": "youtube_video", "params": {"channel_url": self.CHANNEL},
                "quality": {**QUALITY, "source_variant": "preview"}, "items": items,
                "rules": rules if rules is not None else [
                    {"match": "^FInFw2lVz_I$", "role": "promotional", "evidence_quotes": ["Package Vocal"],
                     "collections": ["Package Vocal"]},
                    {"match": "^channel:banner$", "role": "promotional"},
                    {"match": "^channel:avatar$", "action": "exclude", "reason": "site_ui: company logo"}]}

    def oembed_url(self, video):
        return full_url(youtube.OEMBED, {"url": f"https://www.youtube.com/watch?v={video}", "format": "json"})

    def test_largest_generated_thumbnail_with_fallback(self):
        routes = {self.oembed_url(self.VIDEO): {"json": json.loads(text("youtube_oembed.json"))},
                  youtube.thumbnail(self.VIDEO, "maxresdefault"): {
                      "error": net.HttpError("HTTP 404", url="https://i.ytimg.com/", http_status=404)},
                  youtube.thumbnail(self.VIDEO, "sddefault"): {"bytes": image_bytes("JPEG", (64, 48)),
                                                               "content_type": "image/jpeg"}}
        network = FakeNetwork(routes)
        self.run_family([self.seed([{"video_id": self.VIDEO}])], network)
        candidate = self.candidates("official:youtube")[self.VIDEO]
        self.assertEqual(candidate["disposition"], "downloaded")
        asset = self.manifest()["assets"][0]
        self.assertEqual(asset["download_url"], youtube.thumbnail(self.VIDEO, "sddefault"))
        self.assertEqual(asset["page_url"], f"https://www.youtube.com/watch?v={self.VIDEO}")
        self.assertEqual(asset["title"], "Dynamix《Package Vocal》")
        self.assertTrue(asset["transformed"])
        self.assertEqual(asset["quality"]["source_variant"], "preview")
        # oEmbed advertises another rendition: its URL is kept, sizes are not compared.
        self.assertEqual(asset["source_reported"]["url"], "https://i.ytimg.com/vi/FInFw2lVz_I/hqdefault.jpg")
        self.assertIsNone(asset["comparisons"]["dimensions_match"])
        # oEmbed names no MIME type and the stored rendition is another one: no format comparison either.
        self.assertIsNone(asset["source_reported"]["mime"])
        self.assertIsNone(asset["comparisons"]["format_match"])
        self.assertIn("advertises", asset["notes"])
        self.verify()

    def test_video_of_another_channel_is_held(self):
        data = json.loads(text("youtube_oembed.json"))
        data["author_url"] = "https://www.youtube.com/@someone-else"
        network = FakeNetwork({self.oembed_url(self.VIDEO): {"json": data}})
        self.run_family([self.seed([{"video_id": self.VIDEO}])], network)
        candidate = self.candidates("official:youtube")[self.VIDEO]
        self.assertEqual(candidate["disposition"], "pending")
        self.assertIn("is not https://www.youtube.com/@C4CatEntertainment", candidate["reason"])
        self.assertEqual(network.count("i.ytimg.com"), 0)
        self.verify()

    def test_unavailable_video_is_a_failure_and_keeps_the_list_open(self):
        network = FakeNetwork({})  # oEmbed 404: removed video
        self.run_family([self.seed([{"video_id": self.VIDEO}])], network)
        snapshot = manifest.load_discovery(self.root, "official:youtube")
        self.assertEqual(snapshot["candidates"][0]["disposition"], "failed")
        self.assertEqual(snapshot["continuation"]["seed-videos"], "not-read:http_4xx")
        self.verify()

    def test_a_private_video_refused_by_oembed_does_not_block_the_other_videos(self):
        private = "AAAAAAAAAAA"
        rules = [{"match": "^(FInFw2lVz_I|AAAAAAAAAAA)$", "role": "promotional"}]
        network = FakeNetwork({
            self.oembed_url(private): {"error": net.BlockedError("access refused (HTTP 401)", url=youtube.OEMBED,
                                                                 http_status=401)},
            self.oembed_url(self.VIDEO): {"json": json.loads(text("youtube_oembed.json"))},
            youtube.thumbnail(self.VIDEO, "maxresdefault"): {"bytes": image_bytes("JPEG", (64, 36)),
                                                             "content_type": "image/jpeg"}})
        self.run_family([self.seed([{"video_id": private}, {"video_id": self.VIDEO}], rules)], network)
        found = self.candidates("official:youtube")
        self.assertEqual(found[private]["disposition"], "failed")
        self.assertIn("private or not embeddable", found[private]["reason"])
        self.assertEqual(found[self.VIDEO]["disposition"], "downloaded")
        failure = self.manifest()["failures"][0]
        self.assertEqual((failure["error_class"], failure["http_status"], failure["manual"]), ("http_4xx", 401, False))
        self.assertNotEqual(self.manifest()["sources"][0]["status"], "manual_access")
        self.verify()

    def test_repeated_oembed_refusals_block_the_host(self):
        videos = ["AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC", "DDDDDDDDDDD", self.VIDEO]
        routes = {self.oembed_url(video): {"error": net.BlockedError("HTTP 403", url=youtube.OEMBED, http_status=403)}
                  for video in videos}
        network = FakeNetwork(routes)
        rules = [{"match": ".*", "role": "promotional"}]
        self.run_family([self.seed([{"video_id": video} for video in videos], rules)], network)
        found = self.candidates("official:youtube")
        self.assertEqual(network.count("www.youtube.com"), runner.GuardedClient.ITEM_REFUSAL_LIMIT)
        self.assertEqual(found["AAAAAAAAAAA"]["disposition"], "failed")
        self.assertEqual(found[self.VIDEO]["disposition"], "blocked")  # host-wide refusal: not contacted again
        self.verify()

    def test_resume_reuses_an_archived_fallback_rendition_without_requests(self):
        def routes():
            return {self.oembed_url(self.VIDEO): {"json": json.loads(text("youtube_oembed.json"))},
                    youtube.thumbnail(self.VIDEO, "maxresdefault"): {
                        "error": net.HttpError("HTTP 404", url="https://i.ytimg.com/", http_status=404)},
                    youtube.thumbnail(self.VIDEO, "sddefault"): {"bytes": image_bytes("JPEG", (64, 48)),
                                                                 "content_type": "image/jpeg"}}
        seed = self.seed([{"video_id": self.VIDEO}])
        self.run_family([seed], FakeNetwork(routes()))
        before = self.manifest()["assets"][0]
        network = FakeNetwork(routes())
        self.run_family([seed], network, resume=True)
        self.assertEqual(network.count("i.ytimg.com"), 0)
        after = self.manifest()["assets"][0]
        self.assertEqual(after["download_url"], youtube.thumbnail(self.VIDEO, "sddefault"))
        self.assertEqual((after["resolved_url"], after["fetched_at"]), (before["resolved_url"], before["fetched_at"]))
        self.assertEqual(self.candidates("official:youtube")[self.VIDEO]["file_url"], after["download_url"])
        self.verify()

    def test_channel_art_needs_the_image_on_the_channel_page(self):
        banner = "https://yt3.googleusercontent.com/" + "B" * 40
        avatar = "https://yt3.googleusercontent.com/" + "A" * 40
        network = FakeNetwork({self.CHANNEL: {"text": f"<title>C4 Cat - YouTube</title> {'B' * 40}=w1060"},
                               f"{banner}=s0": {"bytes": image_bytes("JPEG", (80, 45)), "content_type": "image/jpeg"}})
        self.run_family([self.seed([{"channel_art": "banner", "url": banner},
                                    {"channel_art": "avatar", "url": avatar}])], network)
        found = self.candidates("official:youtube")
        self.assertEqual(found["channel:banner"]["disposition"], "downloaded")
        self.assertEqual(found["channel:avatar"]["disposition"], "pending")  # not on the page: never admitted
        self.assertIn("no longer shows", found["channel:avatar"]["reason"])
        self.assertTrue(found["channel:avatar"]["reason"].startswith("unresolved_game: "))  # the channel's claim
        self.assertEqual(network.count("www.youtube.com"), 1)  # the channel page is read once
        self.verify()

    def test_seed_validation(self):
        with self.assertRaises(ValueError):
            youtube.check_seed(self.seed([{"video_id": "short"}]), "test")
        with self.assertRaises(ValueError):
            youtube.check_seed({**self.seed([]), "params": {"channel_url": "https://example.com/c"}}, "test")


class FacebookTests(OfficialTestCase):
    POST = "https://www.facebook.com/c4cat.dynamix/posts/1848526438667526/"
    IMAGE = ("https://scontent-sjc6-1.xx.fbcdn.net/v/t39.30808-6/474475887_2672744962912332_8069806985176556266_n.jpg"
             "?stp=dst-jpg_tt6&cstp=mx1920x1080&ctp=p600x600&_nc_cat=104&ccb=1-7&_nc_sid=cae128&oh=00_AfTest"
             "&oe=68000000")

    def seed(self, items, rules=None):
        return {"id": "official:facebook", "name": "Facebook", "url": "https://www.facebook.com/c4cat.dynamix/",
                "platform": "facebook", "adapter": "facebook_post", "params": {"page": "c4cat.dynamix"},
                "quality": {**QUALITY, "source_variant": "preview"}, "rights": {"holder": "C4Cat Entertainment Limited"},
                "items": items, "rules": rules if rules is not None else [
                    {"match": "^1848526438667526$", "role": "reference", "evidence_quotes": ["Own The Night"],
                     "song_titles": ["Own The Night", "Arty Party"], "mapping_status": "confirmed_primary"}]}

    def test_open_graph_image_of_a_permalink(self):
        network = FakeNetwork({self.POST: {"text": text("facebook_post.html")},
                               self.IMAGE: {"bytes": image_bytes("JPEG", (60, 34)), "content_type": "image/jpeg"}})
        self.run_family([self.seed([{"key": "1848526438667526", "url": self.POST}])], network)
        candidate = self.candidates("official:facebook")["1848526438667526"]
        self.assertEqual(candidate["disposition"], "downloaded")
        asset = self.manifest()["assets"][0]
        self.assertEqual(asset["download_url"], self.IMAGE)  # entities decoded, the fetched URL kept
        self.assertEqual(asset["original_filename"], "474475887_2672744962912332_8069806985176556266_n.jpg")
        self.assertEqual(asset["mapping_status"], "confirmed_primary")
        self.assertEqual(asset["mapping_evidence"][0]["field"], "og:description")
        self.assertTrue(asset["transformed"])
        self.assertIn("display size p600x600", asset["notes"])
        # The note carries no run date: a later refresh that reuses the archive must not restate when it was served.
        self.assertIn("as served to a logged-out reader", asset["notes"])
        self.assertNotRegex(asset["notes"], r"\d{4}-\d{2}-\d{2}")
        self.verify()

    def test_the_open_graph_note_appears_once_with_item_and_rule_notes(self):
        rules = [{"match": "^1848526438667526$", "role": "reference", "evidence_quotes": ["Own The Night"],
                  "candidate_notes": "Comeback card."}]
        network = FakeNetwork({self.POST: {"text": text("facebook_post.html")},
                               self.IMAGE: {"bytes": image_bytes("JPEG", (60, 34)), "content_type": "image/jpeg"}})
        self.run_family([self.seed([{"key": "1848526438667526", "url": self.POST, "notes": "Item note."}], rules)],
                        network)
        notes = self.manifest()["assets"][0]["notes"]
        self.assertEqual(notes.count("Open Graph image of"), 1, notes)
        self.assertEqual(notes.count("Comeback card."), 1, notes)
        self.assertIn("Item note.", notes)
        self.verify()

    def resign(self, page, oh, oe):
        return (page.replace("oh=00_AfTest&amp;oe=68000000", f"oh={oh}&amp;oe={oe}"),
                self.IMAGE.replace("oh=00_AfTest&oe=68000000", f"oh={oh}&oe={oe}"))

    def test_resume_reuses_the_archive_when_only_the_signature_changed(self):
        img = image_bytes("JPEG", (60, 34))
        seed = self.seed([{"key": "1848526438667526", "url": self.POST}])
        self.run_family([seed], FakeNetwork({self.POST: {"text": text("facebook_post.html")},
                                             self.IMAGE: {"bytes": img, "content_type": "image/jpeg"}}))
        before = self.manifest()["assets"][0]
        page, signed = self.resign(text("facebook_post.html"), "00_AfOther", "69000000")
        network = FakeNetwork({self.POST: {"text": page}, signed: {"bytes": img, "content_type": "image/jpeg"}})
        self.run_family([seed], network, resume=True)
        self.assertEqual(network.count("scontent-sjc6-1.xx.fbcdn.net"), 0)
        after = self.manifest()["assets"][0]
        for name in ("download_url", "resolved_url", "fetched_at"):
            self.assertEqual(after[name], before[name], name)
        self.assertEqual(after["source_reported"]["url"], before["download_url"])
        self.assertEqual(self.candidates("official:facebook")["1848526438667526"]["file_url"], before["download_url"])
        # A different rendition (other stp/ctp) of the same upload is another file: downloaded again.
        page, signed = self.resign(text("facebook_post.html"), "00_AfOther", "69000000")
        page, signed = page.replace("ctp=p600x600", "ctp=p720x720"), signed.replace("ctp=p600x600", "ctp=p720x720")
        network = FakeNetwork({self.POST: {"text": page}, signed: {"bytes": img, "content_type": "image/jpeg"}})
        self.run_family([seed], network, resume=True)
        self.assertEqual(network.count("scontent-sjc6-1.xx.fbcdn.net"), 1)
        self.verify()

    def test_refetched_identical_bytes_keep_one_fetch_s_url_target_and_time(self):
        img = image_bytes("JPEG", (60, 34))
        seed = self.seed([{"key": "1848526438667526", "url": self.POST}])
        self.run_family([seed], FakeNetwork({self.POST: {"text": text("facebook_post.html")},
                                             self.IMAGE: {"bytes": img, "content_type": "image/jpeg"}}))
        before = self.manifest()["assets"][0]
        page, signed = self.resign(text("facebook_post.html"), "00_AfOther", "69000000")
        network = FakeNetwork({self.POST: {"text": page}, signed: {"bytes": img, "content_type": "image/jpeg"}})
        acquisition = runner.Runner(self.root, "official", [seed], client_factory=network.factory,
                                    now=lambda: "2026-10-11T08:00:00Z", sleep=lambda s: None)
        acquisition.run()  # a refresh without --resume downloads again
        self.assertEqual(network.count("scontent-sjc6-1.xx.fbcdn.net"), 1)
        after = self.manifest()["assets"][0]
        self.assertEqual(after["download_url"], after["resolved_url"])
        for name in ("download_url", "resolved_url", "fetched_at"):
            self.assertEqual(after[name], before[name], name)
        self.assertIn("Re-fetched 2026-10-11 (same file, re-signed URL): the archived bytes again", after["notes"])
        self.verify()

    def test_mime_follows_the_requested_rendition_not_the_upload_name(self):
        png_named = self.IMAGE.replace("_n.jpg?", "_n.png?")
        page = text("facebook_post.html").replace("_n.jpg?", "_n.png?")
        network = FakeNetwork({self.POST: {"text": page},
                               png_named: {"bytes": image_bytes("JPEG", (60, 34)), "content_type": "image/jpeg"}})
        self.run_family([self.seed([{"key": "1848526438667526", "url": self.POST}])], network)
        asset = self.manifest()["assets"][0]
        self.assertEqual(asset["source_reported"]["mime"], "image/jpeg")  # stp=dst-jpg_tt6
        self.assertIs(asset["comparisons"]["format_match"], True)
        self.assertIn("ends in .png", asset["notes"])
        self.assertEqual(facebook.rendition_mime("https://x.fbcdn.net/v/a_n.png?stp=c0.5.1_dst-png_s206x206"),
                         "image/png")
        self.assertIsNone(facebook.rendition_mime("https://x.fbcdn.net/v/a_n.jpg?_nc_cat=1"))
        self.verify()

    def test_permalink_without_an_image_is_blocked_without_a_failure(self):
        network = FakeNetwork({self.POST: {"text": text("facebook_shell.html")}})
        self.run_family([self.seed([{"key": "1848526438667526", "url": self.POST}])], network)
        candidate = self.candidates("official:facebook")["1848526438667526"]
        self.assertEqual(candidate["disposition"], "blocked")
        self.assertIn("logged-out reader", candidate["reason"])
        self.assertEqual(candidate["song_titles"], ["Own The Night", "Arty Party"])  # the lead stays traceable
        self.assertEqual(self.manifest()["failures"], [])
        self.assertEqual(network.count("scontent-sjc6-1.xx.fbcdn.net"), 0)
        self.verify()

    def test_curated_exclusion_applies_to_a_post_without_an_image(self):
        rules = [{"match": "^1848526438667526$", "action": "exclude",
                  "reason": "out_of_scope_other: text-only post (submission rules); no image"}]
        network = FakeNetwork({self.POST: {"text": text("facebook_shell.html")}})
        self.run_family([self.seed([{"key": "1848526438667526", "url": self.POST}], rules)], network)
        candidate = self.candidates("official:facebook")["1848526438667526"]
        self.assertEqual(candidate["disposition"], "excluded")
        self.assertTrue(candidate["reason"].startswith("out_of_scope_other: text-only post"))
        self.verify()

    def test_login_redirect_is_blocked_and_reel_redirect_is_the_same_video(self):
        login = "https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fc4cat.dynamix%2Fphotos%2F1%2F"
        photo = "https://www.facebook.com/c4cat.dynamix/photos/a.1/1/"
        video = "https://www.facebook.com/c4cat.dynamix/videos/258082247123314"
        reel = text("facebook_post.html").replace(
            "https://www.facebook.com/c4cat.dynamix/posts/comeback-track-event/1848526438667526/",
            "https://www.facebook.com/reel/258082247123314/").replace(
            "<title>Dynamix - [Comeback", "<title>47 reactions | [Comeback")
        network = FakeNetwork({photo: {"text": text("facebook_shell.html"), "final_url": login},
                               video: {"text": reel, "final_url": "https://www.facebook.com/reel/258082247123314/"},
                               self.IMAGE: {"bytes": image_bytes("JPEG", (60, 34)), "content_type": "image/jpeg"}})
        rules = [{"match": "^(1|258082247123314)$", "role": "gameplay_screenshot", "evidence_quotes": ["Own The Night"]}]
        self.run_family([self.seed([{"key": "1", "url": photo}, {"key": "258082247123314", "url": video}], rules)],
                        network)
        found = self.candidates("official:facebook")
        self.assertEqual(found["1"]["disposition"], "blocked")
        self.assertIn("login page", found["1"]["reason"])
        self.assertEqual(found["258082247123314"]["disposition"], "downloaded")
        asset = self.manifest()["assets"][0]
        self.assertTrue(asset["title"].startswith("[Comeback Track Event"))  # reaction counter dropped
        self.assertEqual(self.manifest()["failures"], [])
        self.verify()

    def test_permalink_leaving_the_page_is_held(self):
        page = text("facebook_post.html").replace("https://www.facebook.com/c4cat.dynamix/posts/",
                                                  "https://www.facebook.com/someone.else/posts/")
        network = FakeNetwork({self.POST: {"text": page}})
        self.run_family([self.seed([{"key": "1848526438667526", "url": self.POST}])], network)
        candidate = self.candidates("official:facebook")["1848526438667526"]
        self.assertEqual(candidate["disposition"], "pending")
        self.assertIn("not the c4cat.dynamix page", candidate["reason"])
        self.verify()

    def test_login_refusal_is_blocked(self):
        network = FakeNetwork({self.POST: {"error": net.BlockedError("HTTP 403", url=self.POST, http_status=403)}})
        self.run_family([self.seed([{"key": "1848526438667526", "url": self.POST}])], network)
        self.assertEqual(self.candidates("official:facebook")["1848526438667526"]["disposition"], "blocked")
        self.assertEqual(self.manifest()["sources"][0]["status"], "manual_access")
        self.verify()

    def test_seed_urls_must_be_permalinks_of_the_page(self):
        with self.assertRaises(ValueError):
            facebook.check_seed(self.seed([{"key": "x", "url": "https://www.facebook.com/other/posts/1/"}]), "test")


class ProbeAndRegistryTests(OfficialTestCase):
    def test_dns_failure_is_temporarily_unavailable(self):
        network = FakeNetwork({"https://dynamix.c4-cat.com/": {"error": net.ConnectionFetchError(
            "cannot resolve dynamix.c4-cat.com", url="https://dynamix.c4-cat.com/")}})
        seed = {"id": "official:website", "name": "Website", "url": "https://dynamix.c4-cat.com/", "platform": "web",
                "adapter": "page_probe", "hosts": ["dynamix.c4-cat.com"],
                "items": [{"url": "https://dynamix.c4-cat.com/"}]}
        self.run_family([seed], network)
        source = self.manifest()["sources"][0]
        self.assertEqual(source["status"], "temporarily_unavailable")
        snapshot = manifest.load_discovery(self.root, "official:website")
        self.assertIn("cannot resolve", snapshot["errors"][0]["message"])
        # Nothing was read, so nothing is known to be complete.
        self.assertEqual(snapshot["continuation"], {"probe": "not-read:other"})
        self.assertEqual((snapshot["discovery_complete"], source["discovery_complete"]), (False, False))
        self.verify()

    def test_login_wall_probe_keeps_the_feed_open(self):
        network = FakeNetwork({"https://www.facebook.com/c4cat.dynamix/": {"error": net.BlockedError(
            "HTTP 403", url="https://www.facebook.com/c4cat.dynamix/", http_status=403)}})
        seed = {"id": "official:facebook", "name": "Facebook", "url": "https://www.facebook.com/c4cat.dynamix/",
                "platform": "facebook", "adapter": "page_probe", "hosts": ["www.facebook.com"],
                "unenumerated_lists": {"page-timeline": "login wall"},
                "items": [{"url": "https://www.facebook.com/c4cat.dynamix/"}]}
        self.run_family([seed], network)
        source = self.manifest()["sources"][0]
        self.assertEqual((source["status"], source["evidence_level"], source["discovery_complete"]),
                         ("manual_access", "blocked", False))
        self.verify()

    def test_registry_sources_make_no_request(self):
        network = FakeNetwork({})
        seed = {"id": "official:official-leads", "name": "Leads", "url": "https://dynamix.c4-cat.com/",
                "platform": "registry", "adapter": "lead_registry",
                "items": [{"key": "qooapp:237", "page_url": "https://apps.qoo-app.com/en/app/237", "title": "QooApp",
                           "disposition": "excluded", "reason": "out_of_scope_other: mirror"},
                          {"key": "fb:login", "page_url": "https://www.facebook.com/c4cat.dynamix/", "title": "FB",
                           "disposition": "blocked", "reason": "blocked: login wall"}]}
        self.run_family([seed], network)
        self.assertEqual(network.calls, [])
        self.assertEqual(network.factories, [])  # no network client was even created
        found = self.candidates("official:official-leads")
        self.assertEqual(found["fb:login"]["disposition"], "blocked")
        self.assertEqual(self.manifest()["sources"][0]["status"], "metadata_only")
        self.assertEqual(self.manifest()["failures"], [])
        self.verify()

    def test_awaiting_download_needs_a_file_url(self):
        # 09: awaiting_download is an admitted candidate with a known file URL; a lead with nothing to fetch is
        # unresolved_role. The registry rejects such a seed item, and --verify rejects such a candidate.
        lead = {"key": "x:gone", "page_url": "https://x.com/gone", "title": "Deleted account",
                "disposition": "pending", "reason": "unresolved_role: every archived status is a TweetTombstone"}
        upload = {"key": "file:known", "page_url": "https://example.org/page", "title": "Known file",
                  "file_url": "https://example.org/a.png", "disposition": "pending",
                  "reason": "awaiting_download: not fetched yet"}
        seed = {"id": "official:official-leads", "name": "Leads", "url": "https://dynamix.c4-cat.com/",
                "platform": "registry", "adapter": "lead_registry", "items": [lead, upload]}
        with self.assertRaisesRegex(ValueError, "needs its file_url"):
            registry_adapter.check_seed({**seed, "items": [{**lead, "reason": "awaiting_download: nothing"}]}, "s")
        self.run_family([seed], FakeNetwork({}))
        self.verify()  # a known file URL may await its download
        path = self.root / paths.discovery_relpath("official:official-leads")
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        for candidate in snapshot["candidates"]:
            if candidate["upstream_key"] == "x:gone":
                candidate["reason"] = "awaiting_download: every archived status is a TweetTombstone"
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        errors, _warnings, _counts = runner.verify_family(self.root, "official")
        self.assertEqual(errors, ["official:official-leads:x:gone: pending 'awaiting_download' without a file URL "
                                  "(awaiting_download is an admitted candidate with a known file URL; a lead with no "
                                  "file to fetch is unresolved_role)"])
        # An admitted rule whose item has no file URL is held as unresolved_role, never awaiting_download.
        draft = model.apply_rule({"match": ".*", "role": "promotional"}, key="k", page_url="https://example.org/",
                                 texts={}, file_url=None, default_title="t", default_quality=QUALITY,
                                 default_rights={})
        self.assertEqual((draft.action, draft.reason), ("pending", model.NO_FILE_REASON))
        self.assertTrue(draft.reason.startswith("unresolved_role: "))


class SoundCloudListTests(OfficialTestCase):
    def sound_page(self, title, artwork):
        sound = {"title": title, "description": "", "artwork_url": artwork, "created_at": "2017-07-04T16:15:35Z",
                 "user": {"username": "C4Cat", "permalink": "c4cat"}}
        return {"text": "<html><head><title>x</title></head><body><script>window.__sc_hydration = "
                        + json.dumps([{"hydratable": "sound", "data": sound}]) + ";</script></body></html>"}

    def test_every_listed_track_is_accounted_for_and_a_partial_list_stays_open(self):
        art = "https://i1.sndcdn.com/artworks-000000000001-aaaaaa-large.jpg"
        network = FakeNetwork({
            "https://soundcloud.com/c4cat/tracks": {"text": text("soundcloud_tracks.html")},
            "https://soundcloud.com/c4cat/le-ciel-heureux-p": self.sound_page("P4koo - Le ciel heureux (Preview)", art),
            "https://soundcloud.com/c4cat/packer-woo-meeting-in-sky": self.sound_page("Packer Woo - Meeting", art),
            "https://soundcloud.com/c4cat/package-zhd": self.sound_page("Package ZHD", None),
            art.replace("-large", "-original"): {"bytes": image_bytes("JPEG", (30, 30)), "content_type": "image/jpeg"}})
        seed = {"id": "official:soundcloud-c4cat", "name": "C4Cat SoundCloud", "url": "https://soundcloud.com/c4cat",
                "platform": "soundcloud", "adapter": "release_page", "hosts": ["soundcloud.com", ".sndcdn.com"],
                "params": {"handle": "c4cat"}, "quality": QUALITY,
                "items": [{"url": "https://soundcloud.com/c4cat/le-ciel-heureux-p", "key": "le-ciel-heureux-p"},
                          {"tracks_of": "c4cat"}],
                "rules": [{"match": "^le-ciel-heureux-p$", "role": "song_art", "song_titles": ["Le ciel heureux"],
                           "mapping_status": "confirmed_primary", "evidence_quotes": ["Le ciel heureux"]}]}
        self.run_family([seed], network)
        snapshot = manifest.load_discovery(self.root, "official:soundcloud-c4cat")
        found = {c["upstream_key"]: c for c in snapshot["candidates"]}
        self.assertEqual(sorted(found), ["le-ciel-heureux-p", "package-zhd", "packer-woo-meeting-in-sky"])
        self.assertEqual(found["le-ciel-heureux-p"]["disposition"], "downloaded")
        self.assertEqual(found["package-zhd"]["disposition"], "excluded")  # no artwork of its own
        self.assertEqual(found["packer-woo-meeting-in-sky"]["disposition"], "pending")  # no rule: never admitted
        self.assertEqual(snapshot["continuation"]["tracks:c4cat"], "listed-3-of-5")
        self.assertFalse(snapshot["discovery_complete"])
        self.assertEqual(network.count("soundcloud.com"), 4)  # the explicit item is not requested twice
        self.verify()


class IsolationTests(OfficialTestCase):
    REGISTRY = {"id": "official:official-leads", "name": "Leads", "url": "https://dynamix.c4-cat.com/",
                "platform": "registry", "adapter": "lead_registry",
                "items": [{"key": "qooapp:237", "page_url": "https://apps.qoo-app.com/en/app/237", "title": "QooApp",
                           "disposition": "excluded", "reason": "out_of_scope_other: mirror"}]}
    WEBSITE = {"id": "official:website", "name": "Website", "url": "https://dynamix.c4-cat.com/", "platform": "web",
               "adapter": "page_probe", "hosts": ["dynamix.c4-cat.com"],
               "items": [{"url": "https://dynamix.c4-cat.com/"}]}

    def test_an_adapter_error_is_recorded_and_the_next_source_still_runs(self):
        network = FakeNetwork({"https://dynamix.c4-cat.com/": {"text": "<title>Dynamix</title>"}})
        self.run_family([self.REGISTRY, self.WEBSITE], network)
        with mock.patch.object(registry_adapter, "discover", side_effect=RuntimeError("unexpected payload")), \
                mock.patch.object(probe, "discover", wraps=probe.discover) as later:
            acquisition = runner.Runner(self.root, "official", [self.REGISTRY, self.WEBSITE],
                                        client_factory=network.factory, now=lambda: FIXED_NOW, sleep=lambda s: None)
            acquisition.run()
        self.assertEqual(later.call_count, 1)  # the source after the failing one was still processed
        self.assertIn("RuntimeError", acquisition.source_errors["official:official-leads"])
        snapshot = manifest.load_discovery(self.root, "official:official-leads")
        self.assertFalse(snapshot["discovery_complete"])
        self.assertEqual(snapshot["continuation"]["adapter"], "error:RuntimeError")
        self.assertIn("unexpected payload", snapshot["errors"][0]["message"])
        kept = {c["upstream_key"]: c for c in snapshot["candidates"]}
        self.assertEqual(kept["qooapp:237"]["disposition"], "excluded")  # the previous snapshot is carried over
        self.assertIn("discovery failed", kept["qooapp:237"]["notes"])
        source = next(s for s in self.manifest()["sources"] if s["id"] == "official:official-leads")
        self.assertEqual((source["status"], source["discovery_complete"]), ("temporarily_unavailable", False))
        self.verify()

    def test_an_unexpected_download_error_fails_only_that_candidate(self):
        page = "https://dynamix.c4-cat.com/press"
        seed = {"id": "official:website", "name": "Website", "url": page, "platform": "web",
                "adapter": "direct_files", "hosts": ["dynamix.c4-cat.com"], "quality": QUALITY,
                "items": [{"key": "a", "page_url": page, "file_url": "https://dynamix.c4-cat.com/a.png"},
                          {"key": "b", "page_url": page, "file_url": "https://dynamix.c4-cat.com/b.png"}],
                "rules": [{"match": "^[ab]$", "role": "promotional"}]}
        network = FakeNetwork({
            "https://dynamix.c4-cat.com/a.png": {"bytes": image_bytes(), "content_type": "image/png"},
            "https://dynamix.c4-cat.com/b.png": {"bytes": image_bytes("PNG", (9, 9)), "content_type": "image/png"}})
        original = runner.Runner._resolve_download

        def flaky(self, state, client, draft):
            if draft.upstream_key == "a":
                raise KeyError("broken record")
            return original(self, state, client, draft)

        with mock.patch.object(runner.Runner, "_resolve_download", flaky):
            self.run_family([seed], network)
        found = self.candidates("official:website")
        self.assertEqual((found["a"]["disposition"], found["b"]["disposition"]), ("failed", "downloaded"))
        self.assertIn("KeyError", self.manifest()["failures"][0]["message"])
        self.verify()


class CliTests(OfficialTestCase):
    def test_verify_on_the_contract_fixture(self):
        # Every manifest source needs its discovery snapshot. Until the contracts owner adds the shared fixture's
        # official:apple-store snapshot (requested), its absence must be the only error; once added, the fixture as
        # committed must verify. A copy with a snapshot added verifies either way.
        fixture = ROOT / "tests/fixtures/contract"
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main("official", ["--verify", "--root", str(fixture)])
        if (fixture / "data/discovery/apple-store.json").exists():
            self.assertEqual(code, 0, out.getvalue())
        else:
            self.assertEqual(code, 1, out.getvalue())
            self.assertEqual([line.strip() for line in out.getvalue().splitlines() if "ERROR" in line],
                             ["ERROR: official:apple-store: no valid discovery snapshot at "
                              "data/discovery/apple-store.json"])
        shutil.copytree(fixture, self.root / "contract")
        manifest.write_json_atomic(self.root / "contract/data/discovery/apple-store.json", {
            "schema_version": 1, "source_id": "official:apple-store", "started_at": FIXED_NOW, "finished_at": FIXED_NOW,
            "scope": {"lists": ["product-media-shelves"], "api": None, "notes": "test"},
            "continuation": {"product-media-shelves": "not-read:test"}, "discovery_complete": False,
            "candidates": [], "errors": []})
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main("official", ["--verify", "--root", str(self.root / "contract")])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("OK", out.getvalue())

    def test_verify_reports_unreferenced_files(self):
        stray = self.root / "assets/public/official/x/stray.png"
        stray.parent.mkdir(parents=True)
        stray.write_bytes(image_bytes())
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main("official", ["--verify", "--root", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("not referenced", out.getvalue())

    def test_second_writer_is_refused_by_the_lock(self):
        lock_path = self.root / "official.lock"
        held = cli.acquire_lock(lock_path)
        try:
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = cli.main("official", ["--root", str(self.root), "--lock", str(lock_path)])
            self.assertEqual(code, 3)
            self.assertIn("another acquisition holds", err.getvalue())
            self.assertFalse((self.root / "data").exists())
        finally:
            held.close()
        again = cli.acquire_lock(lock_path)
        self.assertIsNotNone(again)
        again.close()

    def test_default_lock_is_anchored_at_the_root_not_the_temp_directory(self):
        paths_seen = []
        for temp in ("tmpA", "tmpB"):
            with mock.patch.dict(os.environ, {"TMPDIR": str(self.root / temp)}):
                paths_seen.append(cli.default_lock_path(self.root, "official"))
        self.assertEqual(paths_seen[0], paths_seen[1])
        self.assertEqual(paths_seen[0], self.root / "assets/public/official")
        held = cli.acquire_lock(paths_seen[0])
        try:
            self.assertIsNotNone(held)
            self.assertIsNone(cli.acquire_lock(paths_seen[1]))  # a second writer of the same root is refused
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                code = cli.main("official", ["--root", str(self.root)])
            self.assertEqual(code, 3)
        finally:
            held.close()
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["assets"])  # no lock file left behind

    def test_verify_reports_seed_sources_without_a_record(self):
        seeds_path = self.root / "official-seeds.json"
        seeds_path.write_text(json.dumps({"family": "official", "sources": [
            {"id": "official:website", "name": "Website", "url": "https://dynamix.c4-cat.com/", "platform": "web",
             "adapter": "page_probe", "hosts": ["dynamix.c4-cat.com"],
             "items": [{"url": "https://dynamix.c4-cat.com/"}]},
            {"id": "official:c4cat", "name": "C4Cat", "url": "https://c4-cat.com/", "platform": "web",
             "adapter": "page_probe", "hosts": ["c4-cat.com"], "items": [{"url": "https://c4-cat.com/"}]}]}),
            encoding="utf-8")
        network = FakeNetwork({"https://dynamix.c4-cat.com/": {"text": "<title>Dynamix</title>"}})
        self.run_family([s for s in runner.load_seeds(seeds_path, "official") if s["id"] == "official:website"],
                        network)
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main("official", ["--verify", "--root", str(self.root), "--seeds", str(seeds_path)])
        self.assertEqual(code, 1)
        self.assertIn("official:c4cat: seed source has no source record", out.getvalue())

    def test_verify_refuses_acquisition_options(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main("official", ["--verify", "--resume", "--root", str(self.root)]), 2)


if __name__ == "__main__":
    unittest.main()
