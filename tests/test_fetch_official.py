"""Offline tests for scripts/fetch_official.py: App Store shelves and iTunes lookup sets, Google Play native sizes,
TapTap uploads, the X embed timeline sample, availability probes, offline registry sources, the CLI lock and
``--verify``. Run: ``python -I tests/test_fetch_official.py``.
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import manifest, net  # noqa: E402
from primary import cli, runner  # noqa: E402
from primary.adapters import apple, x as x_adapter  # noqa: E402
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
            "ad/f09cad41-0b41-d96d-5c48-be8c76b110f4/pr_source.png/2688x1242bb.jpeg",
            "pad:a89c0c45-e49f-72ab-0a80-754c422d057f": "https://is1-ssl.mzstatic.com/image/thumb/Purple123/v4/f0/9c/"
            "ad/a89c0c45-e49f-72ab-0a80-754c422d057f/pr_source.png/2732x2048bb.jpeg",
        }
        for index, url in enumerate(expected.values()):
            routes[url] = {"bytes": image_bytes("JPEG", (80, 37 + index)), "content_type": "image/jpeg"}
        network = FakeNetwork(routes)
        self.run_family([self.seed([{"url": self.PAGE}])], network)
        found = self.candidates("official:apple-store")
        for key, url in expected.items():
            self.assertEqual(found[key]["file_url"], url)
            self.assertEqual(found[key]["disposition"], "downloaded")
        self.assertEqual(found["phone:video-2"]["disposition"], "excluded")  # trailer item
        self.assertTrue(found["phone:video-2"]["reason"].startswith("video_placeholder"))
        self.assertEqual(found["phone:1a2b3c4d-0000-1111-2222-333344445555"]["disposition"], "failed")  # no route
        assets = self.manifest()["assets"]
        self.assertTrue(all(a["transformed"] and a["kind"] == "store_screenshot" for a in assets))
        phone = next(a for a in assets if a["candidate_id"].endswith("f09cad41-0b41-d96d-5c48-be8c76b110f4"))
        self.assertEqual((phone["source_reported"]["width"], phone["width"]), (2688, 80))
        self.assertIs(phone["comparisons"]["dimensions_match"], False)
        self.assertEqual(self.manifest()["sources"][0]["status"], "partially_available")
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
            routes[apple.native_lookup_url(url)] = {"bytes": image_bytes("JPEG", (60 + index, 34)),
                                                    "content_type": "image/jpeg"}
        network = FakeNetwork(routes)
        self.run_family([self.seed([{"lookup_id": "945955095", "countries": ["us", "jp"]}])], network)
        found = self.candidates("official:apple-store")
        self.assertEqual(len(found), 3)  # the US/JP shared screenshot is one candidate
        shared = found["lookup:4b59219a-06f6-3e05-317d-595eca968375"]
        self.assertTrue(shared["file_url"].endswith("/pr_source.png/4000x4000bb.jpg"))
        self.assertIn("us:phone1", shared["notes"])
        self.assertIn("jp:phone2", shared["notes"])
        self.assertIn("lookup:92092603-00f8-d690-9f01-b4437bda3e80", found)
        self.assertEqual({c["disposition"] for c in found.values()}, {"downloaded"})
        self.verify()


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
        self.assertEqual(network.count("play-lh.googleusercontent.com"), 2)
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
        self.assertTrue(rendition["reason"].startswith("upscaled"))
        self.assertEqual(found["banner:4395ff13f01cca52f28efb2a1694bce9.jpg"]["disposition"], "downloaded")
        self.assertTrue(found["icon:23c51666ae309b07d5f4fe5d888157e5.png"]["reason"].startswith("game_ui"))
        assets = {a["candidate_id"].split(":", 2)[2]: a for a in self.manifest()["assets"]}
        self.assertIn("equals the upstream name token", assets[f"screenshot:{token}"]["quality"]["notes"])
        self.assertIn("differs from the upstream name token",
                      assets["screenshot:1111c7d8f23cfefc08f151e5de5f0222"]["quality"]["notes"])
        self.assertEqual(assets[f"screenshot:{token}"]["source_reported"]["bytes"], 589803)
        self.assertIn("Listed again at screenshot position 3", found[f"screenshot:{token}"]["notes"])
        self.assertIs(assets[f"screenshot:{token}"]["comparisons"]["bytes_match"], False)
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


class XTimelineTests(OfficialTestCase):
    def test_embed_timeline_is_a_sample_and_guards_apply(self):
        post_url = full_url(x_adapter.API, {"id": "981147690795900928", "lang": "en",
                                            "token": x_adapter.syndication_token("981147690795900928")})
        network = FakeNetwork({
            x_adapter.TIMELINE + "c4c4cat": {"text": text("x_timeline.html")},
            post_url: {"json": {"__typename": "TweetTombstone"}},
            "https://pbs.twimg.com/media/GoqoEkJW4AA5saO?format=jpg&name=orig": {
                "bytes": image_bytes("JPEG", (120, 63)), "content_type": "image/jpeg"}})
        seed = {"id": "official:x", "name": "X", "url": "https://x.com/c4c4cat", "platform": "x", "adapter": "x_post",
                "params": {"handle": "c4c4cat"}, "quality": QUALITY,
                "items": [{"timeline": "c4c4cat"}, {"post_id": "981147690795900928"}],
                "rules": [{"match": ".*", "role": "promotional", "game": "dynamix"}]}
        self.run_family([seed], network)
        snapshot = manifest.load_discovery(self.root, "official:x")
        self.assertFalse(snapshot["discovery_complete"])
        self.assertEqual(snapshot["continuation"]["timeline:c4c4cat"], "sample-of-3-recent-posts")
        found = {c["upstream_key"]: c for c in snapshot["candidates"]}
        self.assertEqual(found["1913000000000000001:1"]["disposition"], "downloaded")
        self.assertIn("not this source's account", found["1913000000000000002:1"]["reason"])
        self.assertIn("Dynamix Universe", found["1913000000000000003:1"]["reason"])
        self.assertEqual(found["981147690795900928"]["disposition"], "failed")
        self.assertEqual(network.count("pbs.twimg.com"), 1)
        self.verify()


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
        self.assertIn("cannot resolve", manifest.load_discovery(self.root, "official:website")["errors"][0]["message"])
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


class CliTests(OfficialTestCase):
    def test_verify_on_the_contract_fixture(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main("official", ["--verify", "--root", str(ROOT / "tests/fixtures/contract")])
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
        self.assertIsNotNone(cli.acquire_lock(lock_path))

    def test_verify_refuses_acquisition_options(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main("official", ["--verify", "--resume", "--root", str(self.root)]), 2)


if __name__ == "__main__":
    unittest.main()
