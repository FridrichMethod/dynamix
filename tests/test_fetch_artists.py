"""Offline tests for scripts/fetch_artists.py and the artist adapters (pixiv, X, SoundCloud/Bandcamp, ArtStation).

Covers multi-page/multi-image posts mapped per image, blocked access without a retry storm, rejected submissions,
unrelated portfolio entries, uploader identity checks, misleading advertised dimensions, interrupted runs and
idempotent ``--resume``. Run: ``python -I tests/test_fetch_artists.py``.
"""

import base64
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import manifest, net, paths, schema  # noqa: E402
from primary import model, runner  # noqa: E402
from primary.adapters import pixiv as pixiv_adapter, x as x_adapter  # noqa: E402
from primary.testing import FIXED_NOW, FakeNetwork, full_url, image_bytes  # noqa: E402

FIXTURES = ROOT / "tests/fixtures/artists"
QUALITY = {"source_variant": "site_original", "origin_status": "artist_post", "master_status": "unverified",
           "notes": "test"}


def fixture_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name):
    return json.loads(fixture_text(name))


class ArtistsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_family(self, seeds, network, **options):
        acquisition = runner.Runner(self.root, "artists", sorted(seeds, key=lambda s: s["id"]),
                                    client_factory=network.factory, now=lambda: FIXED_NOW, sleep=lambda s: None,
                                    **options)
        counts = acquisition.run()
        return acquisition, counts

    def manifest(self):
        return manifest.load_manifest(self.root, "artists")

    def snapshot(self, source_id):
        return manifest.load_discovery(self.root, source_id)

    def candidates(self, source_id):
        return {c["upstream_key"]: c for c in self.snapshot(source_id)["candidates"]}

    def verify(self):
        errors, warnings, counts = runner.verify_family(self.root, "artists")
        self.assertEqual(errors, [], errors)
        return warnings, counts


# --- pixiv: multi-page artwork, per-page rules, hotlink-protected originals -------------------------------------------

def pixiv_seed():
    return {"id": "artists:pixiv:5401047", "name": "pixiv Sand Suna", "url": "https://www.pixiv.net/users/5401047",
            "platform": "pixiv", "adapter": "pixiv_artwork", "params": {"user_id": "5401047"}, "quality": QUALITY,
            "items": [{"illust_id": "92594600"}],
            "rules": [{"match": "^92594600:p0$", "role": "song_art", "song_titles": ["MassacrE || 3rc4224M"],
                       "mapping_status": "confirmed_primary", "evidence_quotes": ["For Dynamix x RAVON"],
                       "variant_group": "massacre", "variant_role": "square"},
                      {"match": "^92594600:p1$", "role": "song_art", "song_titles": ["MassacrE || 3rc4224M"],
                       "mapping_status": "confirmed_primary", "evidence_quotes": ["For Dynamix x RAVON"],
                       "variant_group": "massacre", "variant_role": "16:9"}]}


def pixiv_routes(original_error=None):
    detail = fixture_json("pixiv_illust_92594600.json")
    pages = fixture_json("pixiv_pages_92594600.json")
    routes = {"https://www.pixiv.net/ajax/illust/92594600": {"json": detail},
              "https://www.pixiv.net/ajax/illust/92594600/pages": {"json": pages}}
    for index, page in enumerate(pages["body"]):
        url = page["urls"]["original"]
        routes[url] = {"error": original_error(url)} if original_error else {
            "bytes": image_bytes("JPEG", (48, 27), (40 * index, 90, 160)), "content_type": "image/jpeg"}
    return routes


class PixivTests(ArtistsTestCase):
    def test_multipage_pages_are_separate_candidates_with_their_own_rules(self):
        network = FakeNetwork(pixiv_routes())
        self.run_family([pixiv_seed()], network)
        found = self.candidates("artists:pixiv:5401047")
        self.assertEqual(sorted(found), ["92594600:p0", "92594600:p1", "92594600:p2"])
        self.assertEqual(found["92594600:p0"]["disposition"], "downloaded")
        self.assertEqual(found["92594600:p1"]["disposition"], "downloaded")
        # p2 has no rule: never admitted from the post title alone.
        self.assertEqual(found["92594600:p2"]["disposition"], "pending")
        self.assertIn("no seed rule", found["92594600:p2"]["reason"])
        assets = {a["candidate_id"]: a for a in self.manifest()["assets"]}
        p0 = assets["artists:pixiv:5401047:92594600:p0"]
        self.assertEqual(p0["variant_role"], "square")
        self.assertEqual(p0["mapping_status"], "confirmed_primary")
        self.assertEqual(p0["mapping_evidence"][0]["field"], "caption")
        # Advertised page size (2560x2560) is evidence only; the decoded size is stored.
        self.assertEqual((p0["source_reported"]["width"], p0["width"]), (2560, 48))
        self.assertIs(p0["comparisons"]["dimensions_match"], False)
        self.verify()

    def test_withheld_file_urls_are_blocked_without_a_file_request(self):
        routes = pixiv_routes()
        detail = fixture_json("pixiv_illust_92594600.json")
        detail["body"]["pageCount"] = 1
        detail["body"]["urls"] = {"original": None, "regular": None}
        routes["https://www.pixiv.net/ajax/illust/92594600"] = {"json": detail}
        network = FakeNetwork(routes)
        self.run_family([pixiv_seed()], network)
        candidate = self.candidates("artists:pixiv:5401047")["92594600:p0"]
        self.assertEqual(candidate["disposition"], "blocked")
        self.assertIn("logged-out readers", candidate["reason"])
        self.assertEqual(network.count("i.pximg.net"), 0)
        self.assertEqual(self.manifest()["failures"], [])
        self.verify()

    def test_hotlink_protected_originals_are_blocked_once_without_retry_storm(self):
        network = FakeNetwork(pixiv_routes(lambda url: net.BlockedError("HTTP 403", url=url, http_status=403)))
        acquisition, counts = self.run_family([pixiv_seed()], network)
        self.assertEqual(network.count("i.pximg.net"), 1)  # the second page is not requested again
        found = self.candidates("artists:pixiv:5401047")
        self.assertEqual(found["92594600:p0"]["disposition"], "blocked")
        self.assertEqual(found["92594600:p1"]["disposition"], "blocked")
        self.assertIn("not contacted again", found["92594600:p1"]["reason"])
        current = self.manifest()
        self.assertEqual(len(current["failures"]), 1)
        failure = current["failures"][0]
        self.assertEqual((failure["error_class"], failure["retriable"], failure["manual"]), ("blocked", False, True))
        self.assertEqual(current["sources"][0]["status"], "manual_access")
        self.assertEqual(current["assets"], [])
        self.verify()


# --- pixiv originals from Internet Archive raw captures ---------------------------------------------------------------

P1_ORIGINAL = "https://i.pximg.net/img-original/img/2021/09/08/13/09/32/92594600_p1.jpg"
P1_STAMP = "20221216231405"


def wayback_seed(captures, rules=None):
    seed = pixiv_seed()
    seed["items"] = [{"illust_id": "92594600", "wayback": captures}]
    if rules is not None:
        seed["rules"] = rules
    return seed


def cdx_url(original, stamp):
    return full_url(pixiv_adapter.CDX, {"url": original, "matchType": "exact", "output": "json",
                                        "fl": pixiv_adapter.CDX_FIELDS, "from": stamp, "to": stamp})


def wayback_routes(payload, *, cdx_rows=None, stamp=P1_STAMP, original=P1_ORIGINAL):
    routes = pixiv_routes(lambda url: net.BlockedError("HTTP 403", url=url, http_status=403))
    digest = base64.b32encode(hashlib.sha1(payload).digest()).decode()
    rows = cdx_rows if cdx_rows is not None else [
        ["timestamp", "original", "mimetype", "statuscode", "digest", "length"],
        [stamp, original, "image/jpeg", "200", digest, "2215933"]]
    routes[cdx_url(original, stamp)] = {"json": rows}
    routes[f"https://web.archive.org/web/{stamp}id_/{original}"] = {"bytes": payload, "content_type": "image/jpeg"}
    return routes


class PixivWaybackTests(ArtistsTestCase):
    CAPTURE = {"page": 1, "timestamp": P1_STAMP, "original": P1_ORIGINAL}

    def test_archived_original_is_downloaded_from_the_capture_with_the_post_as_page(self):
        payload = image_bytes("JPEG", (48, 27), (1, 2, 3))
        network = FakeNetwork(wayback_routes(payload))
        self.run_family([wayback_seed([self.CAPTURE])], network)
        found = self.candidates("artists:pixiv:5401047")
        p1 = found["92594600:p1"]
        self.assertEqual(p1["disposition"], "downloaded")
        self.assertEqual(p1["page_url"], "https://www.pixiv.net/artworks/92594600")
        self.assertEqual(p1["file_url"], f"https://web.archive.org/web/{P1_STAMP}id_/{P1_ORIGINAL}")
        # Pages without a capture still go to i.pximg.net once (blocked); the archived page never does.
        self.assertEqual(found["92594600:p0"]["disposition"], "blocked")
        self.assertNotIn(("download", P1_ORIGINAL), network.calls)
        asset = next(a for a in self.manifest()["assets"] if a["candidate_id"].endswith(":p1"))
        self.assertEqual(asset["download_url"], f"https://web.archive.org/web/{P1_STAMP}id_/{P1_ORIGINAL}")
        self.assertEqual(asset["source_reported"]["url"], P1_ORIGINAL)
        self.assertEqual(asset["source_reported"]["sha1"], hashlib.sha1(payload).hexdigest())
        self.assertIs(asset["comparisons"]["sha1_match"], True)  # the received bytes are the archived payload
        self.assertEqual(asset["upstream_revision"], P1_STAMP)
        self.assertEqual(asset["quality"]["origin_status"], "artist_post")
        self.assertEqual(asset["quality"]["notes"], pixiv_adapter.WAYBACK_QUALITY)
        self.assertIn("2022-12-16T23:14:05Z", asset["notes"])
        self.assertEqual(self.snapshot("artists:pixiv:5401047")["continuation"]["wayback-captures"], None)
        self.verify()

    def test_capture_missing_from_cdx_is_held_without_a_download(self):
        payload = image_bytes("JPEG", (48, 27))
        routes = wayback_routes(payload, cdx_rows=[["timestamp", "original", "mimetype", "statuscode", "digest",
                                                    "length"], [P1_STAMP, P1_ORIGINAL, "text/html", "302", "X", "1"]])
        network = FakeNetwork(routes)
        self.run_family([wayback_seed([self.CAPTURE])], network)
        p1 = self.candidates("artists:pixiv:5401047")["92594600:p1"]
        self.assertEqual(p1["disposition"], "pending")
        self.assertIn("lists no HTTP 200 capture", p1["reason"])
        self.assertEqual(p1["game"], "unknown")
        self.assertEqual(network.count("web.archive.org"), 1)  # the CDX check only
        self.verify()

    def test_capture_of_a_url_pixiv_no_longer_lists_is_held(self):
        other = P1_ORIGINAL.replace("2021/09/08/13/09/32", "2020/01/01/00/00/00")
        network = FakeNetwork(wayback_routes(image_bytes("JPEG", (48, 27)), original=other))
        self.run_family([wayback_seed([{"page": 1, "timestamp": P1_STAMP, "original": other}])], network)
        p1 = self.candidates("artists:pixiv:5401047")["92594600:p1"]
        self.assertEqual(p1["disposition"], "pending")
        self.assertIn("re-upload", p1["reason"])
        self.assertEqual(network.count("web.archive.org"), 0)
        self.verify()

    def test_withheld_work_is_downloaded_from_its_capture(self):
        payload = image_bytes("JPEG", (48, 27), (9, 9, 9))
        routes = wayback_routes(payload)
        detail = fixture_json("pixiv_illust_92594600.json")
        detail["body"]["pageCount"] = 1
        detail["body"]["urls"] = {"original": None, "regular": None}
        routes["https://www.pixiv.net/ajax/illust/92594600"] = {"json": detail}
        network = FakeNetwork(routes)
        self.run_family([wayback_seed([self.CAPTURE])], network)
        found = self.candidates("artists:pixiv:5401047")
        self.assertEqual(found["92594600:p0"]["disposition"], "blocked")  # withheld, no capture: no request
        self.assertEqual(found["92594600:p1"]["disposition"], "downloaded")
        self.assertEqual(network.count("i.pximg.net"), 0)
        self.verify()

    def test_cdx_failure_keeps_the_list_open_but_fetches_the_capture(self):
        payload = image_bytes("JPEG", (48, 27), (5, 5, 5))
        routes = wayback_routes(payload)
        routes[cdx_url(P1_ORIGINAL, P1_STAMP)] = {"error": net.HttpError("HTTP 503", url=pixiv_adapter.CDX,
                                                                         http_status=503)}
        network = FakeNetwork(routes)
        self.run_family([wayback_seed([self.CAPTURE])], network)
        snapshot = self.snapshot("artists:pixiv:5401047")
        self.assertEqual(snapshot["continuation"]["wayback-captures"], "not-read:http_5xx")
        self.assertFalse(snapshot["discovery_complete"])
        asset = next(a for a in self.manifest()["assets"] if a["candidate_id"].endswith(":p1"))
        self.assertIsNone(asset["comparisons"]["sha1_match"])
        self.verify()

    def test_seed_capture_must_be_the_original_of_its_own_page(self):
        for capture in ({"page": 0, "timestamp": P1_STAMP, "original": P1_ORIGINAL},
                        {"page": 1, "timestamp": "2022", "original": P1_ORIGINAL},
                        {"page": 1, "timestamp": P1_STAMP, "original": P1_ORIGINAL.replace("92594600", "1")}):
            with self.assertRaises(ValueError):
                pixiv_adapter.check_seed(wayback_seed([capture]), "test")

    def test_a_capture_url_stands_for_its_original_in_primary_post_checks(self):
        capture = f"https://web.archive.org/web/{P1_STAMP}id_/{P1_ORIGINAL}"
        self.assertTrue(runner._same_file({"file_url": capture}, P1_ORIGINAL))
        self.assertFalse(runner._same_file({"file_url": capture}, P1_ORIGINAL.replace("_p1", "_p2")))


# --- X: multi-image posts mapped per image ----------------------------------------------------------------------------

def x_seed(rules):
    return {"id": "artists:x:ariiol_", "name": "X ariiol", "url": "https://x.com/ariiol_", "platform": "x",
            "adapter": "x_post", "params": {"handle": "ariiol_"}, "quality": QUALITY,
            "items": [{"post_id": "1879155241788293570"}], "rules": rules}


def x_routes():
    post = fixture_json("x_tweet_result.json")
    url = full_url(x_adapter.API, {"id": "1879155241788293570", "lang": "en",
                                   "token": x_adapter.syndication_token("1879155241788293570")})
    return {url: {"json": post},
            "https://pbs.twimg.com/media/GhQZt1eboAAs87K?format=jpg&name=orig": {
                "bytes": image_bytes("JPEG", (64, 36), (10, 20, 30)), "content_type": "image/jpeg"},
            "https://pbs.twimg.com/media/GhQZt2xyzAAs87K?format=png&name=orig": {
                "bytes": image_bytes("PNG", (32, 32), (90, 90, 90)), "content_type": "image/png"}}


class XTests(ArtistsTestCase):
    def test_each_image_of_a_post_is_classified_on_its_own(self):
        rules = [{"match": "^1879155241788293570:1$", "role": "song_art", "song_titles": ["Cosmic Pulse"],
                  "mapping_status": "confirmed_primary", "evidence_quotes": ["Made the cover", "Cosmic Pulse"]},
                 {"match": "^1879155241788293570:2$", "role": "process"}]
        network = FakeNetwork(x_routes())
        self.run_family([x_seed(rules)], network)
        found = self.candidates("artists:x:ariiol_")
        self.assertEqual(found["1879155241788293570:3"]["disposition"], "excluded")
        self.assertTrue(found["1879155241788293570:3"]["reason"].startswith("video_placeholder"))
        assets = {a["candidate_id"].rsplit(":", 1)[-1]: a for a in self.manifest()["assets"]}
        self.assertEqual(assets["1"]["song_titles"], ["Cosmic Pulse"])
        self.assertEqual(assets["1"]["song_ids"], ["song:cosmic-pulse"])
        self.assertEqual(assets["1"]["kind"], "song_art")
        self.assertEqual(assets["2"]["song_titles"], [])  # the post title does not map the second image
        self.assertEqual(assets["2"]["mapping_status"], "unmapped")
        # The quoted Dynamix announcement is part of the evidence the post itself carries.
        quotes = [entry["quote"] for entry in assets["1"]["mapping_evidence"]]
        self.assertIn("Cosmic Pulse", quotes)
        self.assertEqual(assets["1"]["download_url"],
                         "https://pbs.twimg.com/media/GhQZt1eboAAs87K?format=jpg&name=orig")
        # source_reported is what the endpoint advertised (media_url_https), not the derived name=orig URL.
        self.assertEqual(assets["1"]["source_reported"]["url"], "https://pbs.twimg.com/media/GhQZt1eboAAs87K.jpg")
        self.verify()

    def test_missing_evidence_quote_holds_the_candidate(self):
        rules = [{"match": "^1879155241788293570:1$", "role": "song_art", "song_titles": ["Other Song"],
                  "mapping_status": "confirmed_primary", "evidence_quotes": ["Other Song"]}]
        network = FakeNetwork(x_routes())
        self.run_family([x_seed(rules)], network)
        found = self.candidates("artists:x:ariiol_")
        self.assertEqual(found["1879155241788293570:1"]["disposition"], "pending")
        self.assertIn("seed evidence not found", found["1879155241788293570:1"]["reason"])
        self.assertEqual(found["1879155241788293570:1"]["game"], "unknown")  # the rule's claim is not recorded
        self.assertEqual(network.count("pbs.twimg.com"), 0)

    def test_post_text_is_html_unescaped_before_quotes_are_matched(self):
        post = fixture_json("x_tweet_result.json")
        post["text"] = "次のイベント曲はkRM &amp; 残響Pさんが作った「オレンジ」 &lt;3"
        texts = x_adapter._texts(post)
        self.assertIn("kRM & 残響P", texts["text"])
        self.assertIsNotNone(model.locate_term("kRM & 残響P", texts))

    def test_syndication_token_matches_the_embed_script(self):
        # Value computed by X's embed script ((id / 1e15) * pi).toString(36) without zeros and dots.
        self.assertEqual(x_adapter.syndication_token("1879155241788293570"), "4jzjg8cthuv")


# --- Release pages: SoundCloud, Bandcamp rejected submission ----------------------------------------------------------

def release_seed(source_id, url_base, items, rules, hosts, params=None):
    seed = {"id": source_id, "name": source_id, "url": url_base, "platform": "soundcloud", "adapter": "release_page",
            "hosts": hosts, "quality": QUALITY, "items": items, "rules": rules}
    if params:
        seed["params"] = params
    return seed


class ReleaseTests(ArtistsTestCase):
    def test_soundcloud_original_artwork_and_uploader_check(self):
        page = fixture_text("soundcloud_track.html")
        other = page.replace('"permalink":"kuske-kuonrecords"', '"permalink":"someone-else"')
        network = FakeNetwork({
            "https://soundcloud.com/kuske-kuonrecords/dynamix-kplecraft-16383": {"text": page},
            "https://soundcloud.com/kuske-kuonrecords/reupload": {"text": other},
            "https://i1.sndcdn.com/artworks-000264879671-n9mjel-original.jpg": {
                "bytes": image_bytes("JPEG", (40, 40)), "content_type": "image/jpeg"}})
        rules = [{"match": ".*", "role": "song_art", "song_titles": ["16383"], "mapping_status": "confirmed_primary",
                  "evidence_quotes": ["Rhythm Game \"Dynamix\"", "16383"], "illustrators": ["Kplecraft"],
                  "composers": ["Kplecraft"],
                  "credit_source": {"url": "https://dynamix.miraheze.org/wiki/16383", "note": "the wiki song page"}}]
        seed = release_seed("artists:soundcloud:kuske-kuonrecords", "https://soundcloud.com/kuske-kuonrecords",
                            [{"url": "https://soundcloud.com/kuske-kuonrecords/dynamix-kplecraft-16383",
                              "key": "dynamix-kplecraft-16383"},
                             {"url": "https://soundcloud.com/kuske-kuonrecords/reupload", "key": "reupload"}],
                            rules, ["soundcloud.com", ".sndcdn.com"], {"handle": "kuske-kuonrecords"})
        self.run_family([seed], network)
        found = self.candidates("artists:soundcloud:kuske-kuonrecords")
        self.assertEqual(found["dynamix-kplecraft-16383"]["disposition"], "downloaded")
        self.assertEqual(found["dynamix-kplecraft-16383"]["file_url"],
                         "https://i1.sndcdn.com/artworks-000264879671-n9mjel-original.jpg")
        self.assertEqual(found["reupload"]["disposition"], "pending")
        self.assertIn("uploader someone-else", found["reupload"]["reason"])
        self.assertEqual(found["reupload"]["game"], "unknown")
        asset = self.manifest()["assets"][0]
        self.assertEqual(asset["illustrators"], ["Kplecraft"])
        self.assertEqual(asset["source_reported"]["timestamp"], "2017-12-04T13:40:06Z")
        # source_reported keeps the rendition the page advertises; the derived -original URL is only downloaded.
        self.assertEqual(asset["source_reported"]["url"],
                         "https://i1.sndcdn.com/artworks-000264879671-n9mjel-large.jpg")
        self.assertEqual(asset["download_url"], "https://i1.sndcdn.com/artworks-000264879671-n9mjel-original.jpg")
        # The title "[Dynamix] Kplecraft - 16383" bills the artist but states no role: neither credit is quoted
        # from it; both cite the rule's credit source instead.
        self.assertEqual({(c["role"], c["field"], c["quote"], c["url"]) for c in asset["credit_evidence"]},
                         {("illustrator", None, None, "https://dynamix.miraheze.org/wiki/16383"),
                          ("composer", None, None, "https://dynamix.miraheze.org/wiki/16383")})
        self.assertIn("Kplecraft (illustrator), Kplecraft (composer)", asset["notes"])
        self.assertEqual(asset["mapping_status"], "confirmed_primary")
        self.assertIn("16383", " ".join(e["quote"] for e in asset["mapping_evidence"]))
        self.verify()

    def test_unstated_credit_and_unnamed_title_are_not_claimed_from_the_page(self):
        page = fixture_text("soundcloud_track.html")
        network = FakeNetwork({
            "https://soundcloud.com/kuske-kuonrecords/dynamix-kplecraft-16383": {"text": page},
            "https://i1.sndcdn.com/artworks-000264879671-n9mjel-original.jpg": {
                "bytes": image_bytes("JPEG", (40, 40)), "content_type": "image/jpeg"}})
        rules = [{"match": ".*", "role": "song_art", "song_titles": ["Nanohemu"], "mapping_status": "confirmed_primary",
                  "evidence_quotes": ["Dynamix"], "illustrators": ["Zero"], "composers": ["Kplecraft"],
                  "credit_source": {"url": "https://dynamix.miraheze.org/wiki/16383", "note": "the wiki song page"}}]
        seed = release_seed("artists:soundcloud:kuske-kuonrecords", "https://soundcloud.com/kuske-kuonrecords",
                            [{"url": "https://soundcloud.com/kuske-kuonrecords/dynamix-kplecraft-16383",
                              "key": "dynamix-kplecraft-16383"}],
                            rules, ["soundcloud.com", ".sndcdn.com"], {"handle": "kuske-kuonrecords"})
        self.run_family([seed], network)
        asset = self.manifest()["assets"][0]
        # The page never names "Nanohemu": the mapping cannot be confirmed by this primary post.
        self.assertEqual(asset["mapping_status"], "inferred")
        self.assertIn("does not name “Nanohemu”", asset["notes"])
        credits = {c["name"]: c for c in asset["credit_evidence"]}
        self.assertEqual((credits["Zero"]["field"], credits["Zero"]["quote"]), (None, None))
        self.assertEqual(credits["Zero"]["url"], "https://dynamix.miraheze.org/wiki/16383")
        self.assertIn("Zero (illustrator)", asset["notes"])
        self.assertEqual((credits["Kplecraft"]["field"], credits["Kplecraft"]["quote"]), (None, None))
        self.verify()

    def test_credit_without_any_source_fails_verify(self):
        page = fixture_text("soundcloud_track.html")
        network = FakeNetwork({
            "https://soundcloud.com/kuske-kuonrecords/dynamix-kplecraft-16383": {"text": page},
            "https://i1.sndcdn.com/artworks-000264879671-n9mjel-original.jpg": {
                "bytes": image_bytes("JPEG", (40, 40)), "content_type": "image/jpeg"}})
        rules = [{"match": ".*", "role": "song_art", "evidence_quotes": ["Dynamix"], "illustrators": ["Kplecraft"]}]
        with self.assertRaises(ValueError):  # load_seeds refuses it; a hand-built seed still fails --verify
            model.check_rule(rules[0], "rule")
        seed = release_seed("artists:soundcloud:kuske-kuonrecords", "https://soundcloud.com/kuske-kuonrecords",
                            [{"url": "https://soundcloud.com/kuske-kuonrecords/dynamix-kplecraft-16383",
                              "key": "dynamix-kplecraft-16383"}],
                            rules, ["soundcloud.com", ".sndcdn.com"], {"handle": "kuske-kuonrecords"})
        self.run_family([seed], network)
        errors, _, _ = runner.verify_family(self.root, "artists")
        self.assertEqual([e for e in errors if "cites no source" in e],
                         ["artists:soundcloud:kuske-kuonrecords:" + self.manifest()["assets"][0]["sha256"][:16]
                          + ": credit 'Kplecraft' (illustrator) cites no source URL"])

    def test_rejected_submission_is_excluded_even_with_a_download_rule(self):
        network = FakeNetwork({"https://veirtunes.bandcamp.com/track/beyond-ephemera": {
            "text": fixture_text("bandcamp_rejected.html")}})
        seed = release_seed("artists:bandcamp:veirtunes", "https://veirtunes.bandcamp.com/",
                            [{"url": "https://veirtunes.bandcamp.com/track/beyond-ephemera",
                              "key": "track/beyond-ephemera"}],
                            [{"match": ".*", "role": "song_art"}], ["veirtunes.bandcamp.com", ".bcbits.com"])
        self.run_family([seed], network)
        candidate = self.candidates("artists:bandcamp:veirtunes")["track/beyond-ephemera"]
        self.assertEqual(candidate["disposition"], "excluded")
        self.assertTrue(candidate["reason"].startswith("rejected_submission"))
        self.assertEqual(candidate["game"], "not_dynamix")
        self.assertEqual(candidate["file_url"], "https://f4.bcbits.com/img/a2011763979_0.jpg")
        self.assertEqual(network.count("f4.bcbits.com"), 0)
        self.assertEqual(self.manifest()["sources"][0]["status"], "excluded")
        self.verify()


# --- ArtStation: mixed portfolio entries, 403 ------------------------------------------------------------------------

class MatchingTests(unittest.TestCase):
    def test_terms_match_whole_words_only(self):
        texts = {"description": "Japan tour drawing by ケシksくん; Artwork : 原木シィタケ"}
        self.assertIsNone(model.locate_term("pan", texts))
        self.assertIsNone(model.locate_term("WING", texts))
        self.assertEqual(model.locate_term("ケシｋｓくん", texts)[0], "description")  # NFKC: full-width letters
        self.assertEqual(model.locate_term("原木シィタケ", texts)[0], "description")
        self.assertIsNotNone(model.locate_term("japan TOUR", texts))

    def test_credit_is_quoted_from_its_credit_line(self):
        texts = {"sc.title": "TangJuan vs Black Box - Xetta", "sc.description": "Illustration by TangJuan"}
        self.assertEqual(model.locate_credit("TangJuan", texts, "illustrator"),
                         ("sc.description", "Illustration by TangJuan"))
        # A title naming the artist states no role: no composer evidence from it.
        self.assertIsNone(model.locate_credit("TangJuan", texts, "composer"))

    def test_credit_needs_a_label_of_its_own_role(self):
        locate = model.locate_credit
        self.assertIsNone(locate("Black Box", {"d": "Music: Black Box"}, "illustrator"))
        self.assertEqual(locate("Black Box", {"d": "Music: Black Box"}, "composer"), ("d", "Music: Black Box"))
        text = "Composed by TangJuan and Black Box Illustration by TangJuan Black Box: https://e.test"
        self.assertIn("Composed by TangJuan and Black Box", locate("Black Box", {"d": text}, "composer")[1])
        self.assertIsNone(locate("Black Box", {"d": text}, "illustrator"))
        self.assertIn("Illustration by TangJuan", locate("TangJuan", {"d": text}, "illustrator")[1])
        # A movie/BGA credit is not an illustrator credit; a title is no credit at all.
        self.assertIsNone(locate("Rumskii", {"d": "Music: Nordius Movie: lemonade; & Rumskii"}, "illustrator"))
        self.assertIsNone(locate("inukoro", {"d": "BGA by inukoro"}, "illustrator"))
        self.assertIsNone(locate("Kplecraft", {"t": "[Dynamix] Kplecraft - Nanohemu"}, "illustrator"))
        self.assertIsNone(locate("Kplecraft", {"t": "[Dynamix] Kplecraft - Nanohemu"}, "composer"))
        # Labels of both roles joined into one ("Music&Artwork") state both; a label on the previous line counts.
        joint = {"d": "配信されます。\n◆Music&Artwork Se-U-Ra(せゆーら)"}
        self.assertIsNotNone(locate("Se-U-Ra", joint, "composer"))
        self.assertIsNotNone(locate("Se-U-Ra", joint, "illustrator"))
        self.assertIsNotNone(locate("Keiyuu", {"d": "Illustration:\nKeiyuu"}, "illustrator"))
        # A list bullet or new line starts a new credit: the previous label does not carry over.
        self.assertIsNone(locate("Reku", {"d": "Artwork: A\n◆ Reku Mochizuki □ https://e.test"}, "illustrator"))
        # Japanese labels.
        self.assertIsNotNone(locate("よしもん", {"d": "イラストはよしもんさんに描いて頂きました。"}, "illustrator"))
        self.assertIsNotNone(locate("kRM & 残響P", {"d": "[Event] 次のイベント曲はkRM & 残響Pさんが作った「オレンジ」"},
                                    "composer"))
        self.assertIsNone(locate("Yunosuke", {"d": "Made the cover for Yunosuke!"}, "composer"))
        # 曲絵 / 楽曲絵 / 楽曲イラスト ("the song's artwork") label an illustrator only, never a composer; a bare 曲 label
        # (曲：X, 曲・絵：X) still credits the composer.
        for text in ("曲絵：さとう", "楽曲絵: さとう", "楽曲イラスト：さとう", "曲ジャケ：さとう"):
            self.assertIsNone(locate("さとう", {"d": text}, "composer"), text)
            self.assertIsNotNone(locate("さとう", {"d": text}, "illustrator"), text)
        self.assertIsNotNone(locate("さとう", {"d": "曲：さとう"}, "composer"))
        self.assertIsNotNone(locate("さとう", {"d": "曲・絵：さとう"}, "composer"))
        self.assertIsNotNone(locate("さとう", {"d": "曲・絵：さとう"}, "illustrator"))
        # "Cover by X" is a vocal/remix cover; only a cover-art label credits the illustrator.
        self.assertIsNone(locate("Alice", {"d": "Cover by Alice (vocal cover of the original)"}, "illustrator"))
        for text in ("Cover: Alice", "Cover art by Alice", "Cover illustration: Alice", "Cover image by Alice"):
            self.assertIsNotNone(locate("Alice", {"d": text}, "illustrator"), text)
        # The label must lead to the name (directly, or through a list of names), not merely precede it.
        self.assertIsNone(locate("Kplecraft", {"d": 'Included in the music package "Kplecraft Collection" of'},
                                 "composer"))
        self.assertIsNone(locate("Foo", {"d": "Artwork: Max Foo"}, "illustrator"))  # part of another name
        for name, text, role in (("Jehezukiel", "Composition/Vocal: Jehezukiel Visualized by X", "composer"),
                                 ("長井りく", "🎨Artwork 長井りく:https://twitter.com/sojyiqui", "illustrator"),
                                 ("Violpa", "流星をイメージしました。ジャケットはViolpaさんです。", "illustrator"),
                                 ("SCTL", "Composed by Linne X SCTL @sctl_shin Visualized by", "composer"),
                                 ("deronoitz", "Mixing and Mastering done by myself illust by @deronoitz", "illustrator"),
                                 ("MimiChair", "Visualized: DCWH & MimiChair Pattern Design: T", "illustrator")):
            self.assertIsNotNone(locate(name, {"d": text}, role), (name, text))

    def test_credit_fields_never_borrow_the_other_roles_evidence(self):
        entries, notes = model.credit_fields(
            {"illustrators": ["Black Box"], "credit_source": {"url": "https://e.test/wiki", "note": "the wiki"}},
            "https://e.test/p", {"sc.description": "Music: Black Box"})
        self.assertEqual(entries, [{"role": "illustrator", "name": "Black Box", "url": "https://e.test/wiki",
                                    "field": None, "quote": None}])
        self.assertIn("the wiki https://e.test/wiki: Black Box (illustrator)", notes[0])

    def test_rule_game_is_validated(self):
        for game in ("dynamix_universe", "unknown", "dynamics"):
            with self.assertRaises(ValueError):
                model.check_rule({"match": "^a$", "role": "song_art", "game": game}, "rule")
        model.check_rule({"match": "^a$", "role": "song_art", "game": "mixed"}, "rule")
        model.check_rule({"match": "^a$", "action": "exclude", "reason": "universe_only: sequel",
                          "game": "dynamix_universe"}, "rule")
        with self.assertRaises(ValueError):
            model.check_rule({"match": "^a$", "action": "exclude", "reason": "fanart: x", "game": "dynamics"}, "rule")
        with self.assertRaises(ValueError):
            model.check_rule({"match": "^a$", "role": "song_art", "withdraw_archived": True}, "rule")

    def test_list_fields_must_be_lists_of_non_empty_strings(self):
        rule = {"match": "^1:1$", "role": "song_art", "song_titles": ["Xetta"], "mapping_status": "inferred",
                "evidence_quotes": ["Dynamix"], "illustrators": ["ab"],
                "credit_source": {"url": "https://e.test/wiki"}}
        model.check_rule(rule, "rule")
        for name in model.LIST_FIELDS:
            for bad in ("Dynamix", ["ok", ""], ["ok", "  "], [3], {"a": 1}):
                with self.assertRaisesRegex(ValueError, f"{name} must be a list of non-empty strings"):
                    model.check_rule({**rule, name: bad}, "rule")
        # A string would otherwise be read per character: one-letter "quotes" and credits.
        with self.assertRaises(ValueError):
            model.check_rule({**rule, "evidence_quotes": "Dynamix", "illustrators": "ab"}, "rule")

    def test_credit_sources_must_cite_a_url(self):
        rule = {"match": "^a$", "role": "song_art", "illustrators": ["A"], "composers": ["B"]}
        with self.assertRaisesRegex(ValueError, "needs a credit_source"):
            model.check_rule(rule, "rule")
        with self.assertRaisesRegex(ValueError, "https url"):
            model.check_rule({**rule, "credit_source": {"note": "the wiki"}}, "rule")
        model.check_rule({**rule, "credit_source": {"url": "https://e.test/wiki", "note": "the wiki"}}, "rule")
        curated = [{"role": "illustrator", "name": "A", "url": "https://e.test/a", "quote": "Art: A"},
                   {"role": "composer", "name": "B", "url": "https://e.test/b"}]
        model.check_rule({**rule, "credit_evidence": curated}, "rule")  # a curated, sourced entry per name
        with self.assertRaisesRegex(ValueError, "credit_evidence\\[1\\]"):
            model.check_rule({**rule, "credit_evidence": [curated[0], {"role": "composer", "name": "B"}]}, "rule")
        model.check_rule({"match": "^a$", "action": "pending", "reason": "pending: review", "illustrators": ["A"]},
                         "rule")  # only an admitted file's credits are recorded on an asset

    def test_min_interval_cannot_undercut_the_network_policy(self):
        for bad in (0, 0.1, -1, "2", True, float("nan"), float("inf")):
            seed = {**direct_seed([{"match": "^a$", "role": "song_art"}]), "min_interval": bad}
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "artists.json"
                path.write_text(json.dumps({"family": "artists", "sources": [seed]}), encoding="utf-8")
                # NaN/Infinity are already refused by the strict JSON reader; the rest by the seed check.
                with self.assertRaisesRegex(ValueError, "min_interval|non-finite", msg=repr(bad)):
                    runner.load_seeds(path, "artists")
        seed = {**direct_seed([{"match": "^a$", "role": "song_art"}]), "min_interval": float("nan")}
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(runner.manifest, "load_json",
                                                                      return_value={"family": "artists",
                                                                                    "sources": [seed]}):
            with self.assertRaisesRegex(ValueError, "min_interval"):
                runner.load_seeds(Path(temp) / "artists.json", "artists")
        seed = {**direct_seed([{"match": "^a$", "role": "song_art"}]), "min_interval": 2.5}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "artists.json"
            path.write_text(json.dumps({"family": "artists", "sources": [seed]}), encoding="utf-8")
            self.assertEqual(runner.load_seeds(path, "artists")[0]["min_interval"], 2.5)

    def test_multi_game_art_gets_no_default_holder(self):
        default = {"holder": "C4Cat Entertainment Limited and/or the credited artist", "creator": None, "url": None,
                   "license": None, "restrictions": None}
        quality = dict(QUALITY)
        meta = model.asset_meta({"role": "song_art", "game_associations": ["dynamix", "ravon"]}, [], "https://e.test/p",
                                quality, default)
        self.assertIsNone(meta["rights"]["holder"])
        self.assertIn("also associated with ravon", meta["notes"])
        named = model.asset_meta({"role": "song_art", "game_associations": ["dynamix", "arcaea"],
                                  "rights": {"holder": "lowiro and/or the credited artist"}}, [], "https://e.test/p",
                                 quality, default)
        self.assertEqual(named["rights"]["holder"], "lowiro and/or the credited artist")
        self.assertNotIn("notes", named)
        plain = model.asset_meta({"role": "song_art"}, [], "https://e.test/p", quality, default)
        self.assertEqual(plain["rights"]["holder"], default["holder"])

    def test_a_collaboration_collection_needs_partner_associations(self):
        rule = {"match": "^a$", "role": "promotional", "collections": ["Berry Melody Collaboration"]}
        with self.assertRaisesRegex(ValueError, "collaboration collection needs game_associations"):
            model.check_rule(rule, "rule")
        model.check_rule({**rule, "game_associations": ["dynamix", "berry_melody"]}, "rule")
        model.check_rule({**rule, "action": "pending", "reason": "pending: review"}, "rule")  # nothing admitted
        model.check_rule({"match": "^a$", "role": "promotional", "collections": ["Event"]}, "rule")

    def test_committed_partner_art_names_its_partners(self):
        seeds = {seed["id"]: seed for family in ("artists", "official")
                 for seed in runner.load_seeds(runner.SEEDS_DIR / f"{family}.json", family)}

        def rule(source_id, match):
            return next(r for r in seeds[source_id]["rules"] if r.get("match") == match)

        berry = rule("official:x", "^1889645633545613724:1$")
        self.assertEqual(berry["game_associations"], ["dynamix", "berry_melody"])
        # The image itself prints both notices; the record names both holders, never C4Cat alone.
        self.assertEqual(berry["rights"]["holder"], "Sky Way Studio and C4Cat Entertainment Limited")
        for source_id, match, event in (("artists:soundcloud:linne_ennil", "^whirlwind$", "PABAT! 2017"),
                                        ("artists:soundcloud:sctl_shin", "^venus\\-invader$", "PABAT! 2018")):
            entry = rule(source_id, match)
            self.assertEqual(entry["game_associations"], ["dynamix", "bms"], source_id)
            self.assertIn(event, entry["evidence_quotes"])
            self.assertNotIn("holder", entry.get("rights", {}))  # so the partner rule leaves the holder null

    def test_committed_song_art_credits_follow_one_rule(self):
        # Mapped song art either credits its illustrator (with a cited source) or says why not; a credit never lives
        # only in free-text variant_role.
        for family in ("artists", "official", "archives"):
            for seed in runner.load_seeds(runner.SEEDS_DIR / f"{family}.json", family):
                for rule in seed.get("rules", []):
                    if rule.get("action", "download") != "download" or rule.get("role") != "song_art":
                        continue
                    where = f"{seed['id']} {rule.get('match')}"
                    self.assertNotIn("wiki:", rule.get("variant_role", ""), where)
                    if rule.get("mapping_status", "unmapped") != "unmapped" and not rule.get("illustrators"):
                        self.assertIn("Illustrator not credited", rule.get("notes", ""), where)

    def test_load_seeds_rejects_a_wrong_game_on_a_download_rule(self):
        seed = dict(direct_seed([{"match": "^a$", "role": "song_art", "game": "dynamix_universe"}]))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "artists.json"
            path.write_text(json.dumps({"family": "artists", "sources": [seed]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "original-game files only"):
                runner.load_seeds(path, "artists")


DIRECT_PAGE = "https://example.wixsite.com/portfolio"
DIRECT_FILE = "https://static.wixstatic.com/media/a.png"


def direct_seed(rules, name=None, notes=None):
    item = {"key": "a", "page_url": DIRECT_PAGE, "file_url": DIRECT_FILE}
    if name:
        item["original_filename"] = name
    if notes:
        item["notes"] = notes
    return {"id": "artists:wix:testcase", "name": "Wix test", "url": DIRECT_PAGE, "platform": "web",
            "adapter": "direct_files", "hosts": ["static.wixstatic.com"], "quality": QUALITY, "items": [item],
            "rules": rules}


def direct_network():
    return FakeNetwork({DIRECT_FILE: {"bytes": image_bytes("PNG", (10, 10)), "content_type": "image/png"}})


class ReclassificationTests(ArtistsTestCase):
    def test_same_bytes_under_a_new_upstream_name_keep_the_archived_path(self):
        self.run_family([direct_seed([{"match": "^a$", "role": "character"}], name="a.png")], direct_network())
        before = self.manifest()["assets"][0]
        self.run_family([direct_seed([{"match": "^a$", "role": "character"}], name="renamed.png")], direct_network())
        after = self.manifest()["assets"]
        self.assertEqual(len(after), 1)
        self.assertEqual((after[0]["path"], after[0]["original_filename"]), (before["path"], "a.png"))
        self.assertIn("now renamed.png", after[0]["quality"]["notes"])
        files = sorted(p.name for p in (self.root / "assets/public/artists").rglob("*") if p.is_file())
        self.assertEqual(files, [Path(before["path"]).name])  # no second copy, no unreferenced file
        self.verify()

    def test_an_archived_candidate_now_excluded_fails_verify_until_withdrawn(self):
        self.run_family([direct_seed([{"match": "^a$", "role": "song_art"}])], direct_network())
        archived = self.manifest()["assets"][0]
        exclude = {"match": "^a$", "action": "exclude", "reason": "universe_only: sequel jacket"}
        self.run_family([direct_seed([exclude])], direct_network())
        self.assertEqual(self.candidates("artists:wix:testcase")["a"]["disposition"], "downloaded")  # bytes kept
        errors, _, _ = runner.verify_family(self.root, "artists")
        self.assertTrue(any("is now excluded" in error and "kept for review" in error for error in errors), errors)
        self.run_family([direct_seed([{**exclude, "withdraw_archived": True}])], direct_network())
        candidate = self.candidates("artists:wix:testcase")["a"]
        self.assertEqual((candidate["disposition"], candidate["game"]), ("excluded", "dynamix_universe"))
        self.assertIn(archived["sha256"], candidate["notes"])
        self.assertEqual(self.manifest()["assets"], [])
        self.assertFalse(paths.safe_path(self.root, archived["path"]).exists())
        self.verify()
        self.run_family([direct_seed([{**exclude, "withdraw_archived": True}])], direct_network())
        self.assertIn(archived["sha256"], self.candidates("artists:wix:testcase")["a"]["notes"])  # stays recorded
        self.verify()

    def test_a_held_archived_candidate_is_a_warning_and_metadata_only_never_withdraws(self):
        self.run_family([direct_seed([{"match": "^a$", "role": "song_art"}])], direct_network())
        hold = {"match": "^a$", "action": "pending", "reason": "pending: scope decision"}
        self.run_family([direct_seed([{**hold, "withdraw_archived": True}])], direct_network(), metadata_only=True)
        self.assertEqual(len(self.manifest()["assets"]), 1)  # --metadata-only never changes archived records
        self.run_family([direct_seed([hold])], direct_network())
        self.assertEqual(len(self.manifest()["assets"]), 1)  # without withdraw_archived the bytes stay archived
        warnings, _ = self.verify()
        self.assertTrue(any("is now pending" in warning for warning in warnings), warnings)

    def test_candidate_notes_reach_the_asset_record(self):
        self.run_family([direct_seed([{"match": "^a$", "role": "character", "notes": "Rule note."}],
                                     notes="Character: Nami (jacket of Cosmic Pulse).")], direct_network())
        asset = self.manifest()["assets"][0]
        self.assertEqual(asset["notes"], "Rule note. Character: Nami (jacket of Cosmic Pulse).")
        self.verify()


class ArtStationTests(ArtistsTestCase):
    def seed(self):
        return {"id": "artists:artstation:sandgodoven", "name": "ArtStation", "platform": "artstation",
                "url": "https://sandgodoven.artstation.com/projects/6baqbW", "adapter": "artstation_project",
                "params": {"username": "sandgodoven"}, "quality": QUALITY, "items": [{"project": "6baqbW"}],
                "rules": [{"match": "^6baqbW:101$", "role": "song_art", "song_titles": ["MassacrE || 3rc4224M"],
                           "mapping_status": "confirmed_primary", "evidence_quotes": ["Dynamix"]},
                          {"match": "^6baqbW:102$", "action": "exclude", "reason": "not_dynamix: RAVON-only "
                                                                                   "composition", "game": "not_dynamix"}]}

    def test_unrelated_portfolio_entries_are_not_admitted(self):
        data = fixture_json("artstation_project.json")
        large = data["assets"][0]["image_url"]
        network = FakeNetwork({
            "https://www.artstation.com/projects/6baqbW.json": {"json": data},
            large.replace("/large/", "/4k/"): {"error": net.HttpError("HTTP 404", url=large, http_status=404)},
            large: {"bytes": image_bytes("JPEG", (30, 30)), "content_type": "image/jpeg"}})
        self.run_family([self.seed()], network)
        found = self.candidates("artists:artstation:sandgodoven")
        self.assertEqual(found["6baqbW:101"]["disposition"], "downloaded")
        self.assertEqual(found["6baqbW:102"]["disposition"], "excluded")
        self.assertEqual(found["6baqbW:103"]["disposition"], "pending")
        asset = self.manifest()["assets"][0]
        self.assertEqual(asset["download_url"], large)  # 4k missing -> advertised rendition
        self.verify()

    def test_unexpected_json_shape_is_a_failure_not_a_crash(self):
        network = FakeNetwork({"https://www.artstation.com/projects/6baqbW.json": {"json": ["not", "an", "object"]}})
        self.run_family([self.seed()], network)
        candidate = self.candidates("artists:artstation:sandgodoven")["6baqbW"]
        self.assertEqual(candidate["disposition"], "failed")
        self.assertIn("not an object", candidate["reason"])
        self.assertFalse(self.snapshot("artists:artstation:sandgodoven")["discovery_complete"])
        self.verify()

    def test_bot_check_is_blocked_and_recorded(self):
        network = FakeNetwork({"https://www.artstation.com/projects/6baqbW.json": {
            "error": net.ChallengeError("bot check", url="https://www.artstation.com/projects/6baqbW.json",
                                        http_status=403)}})
        self.run_family([self.seed()], network)
        candidate = self.candidates("artists:artstation:sandgodoven")["6baqbW"]
        self.assertEqual(candidate["disposition"], "blocked")
        self.assertIn("manual access required", candidate["reason"])
        self.assertEqual(len(network.calls), 1)
        failure = self.manifest()["failures"][0]
        self.assertEqual((failure["phase"], failure["error_class"]), ("discover", "challenge"))
        self.verify()


# --- Interrupted runs and idempotent resume ---------------------------------------------------------------------------

class ResumeTests(ArtistsTestCase):
    def rules(self):
        return [{"match": "^1879155241788293570:1$", "role": "song_art"},
                {"match": "^1879155241788293570:2$", "role": "process"}]

    def test_interrupted_run_keeps_completed_candidates_and_resume_finishes(self):
        network = FakeNetwork(x_routes())
        with self.assertRaises(KeyboardInterrupt):
            self.run_family([x_seed(self.rules())], network, interrupt_after=1)
        snapshot = self.snapshot("artists:x:ariiol_")
        self.assertFalse(snapshot["discovery_complete"])
        self.assertEqual(snapshot["continuation"]["downloads"], "in-progress")
        dispositions = sorted(c["disposition"] for c in snapshot["candidates"])
        self.assertEqual(dispositions, ["downloaded", "excluded", "pending"])
        self.verify()  # a partial run is still a valid, reconciled state
        network2 = FakeNetwork(x_routes())
        self.run_family([x_seed(self.rules())], network2, resume=True)
        self.assertEqual(network2.count("pbs.twimg.com"), 1)  # only the missing image is downloaded
        self.assertTrue(self.snapshot("artists:x:ariiol_")["discovery_complete"])
        self.assertEqual(len(self.manifest()["assets"]), 2)
        self.verify()

    def test_resume_is_idempotent(self):
        self.run_family([x_seed(self.rules())], FakeNetwork(x_routes()))
        first = {path: path.read_bytes() for path in sorted((self.root / "data").rglob("*.json"))}
        network = FakeNetwork(x_routes())
        self.run_family([x_seed(self.rules())], network, resume=True)
        second = {path: path.read_bytes() for path in sorted((self.root / "data").rglob("*.json"))}
        self.assertEqual(first, second)
        self.assertEqual(network.count("pbs.twimg.com"), 0)  # files re-hashed instead of downloaded

    def test_metadata_only_never_downloads_and_keeps_archived_records(self):
        network = FakeNetwork(x_routes())
        self.run_family([x_seed(self.rules())], network, metadata_only=True)
        self.assertEqual(network.count("pbs.twimg.com"), 0)
        found = self.candidates("artists:x:ariiol_")
        self.assertIn("--metadata-only", found["1879155241788293570:1"]["reason"])
        self.assertEqual(self.manifest()["assets"], [])
        self.run_family([x_seed(self.rules())], FakeNetwork(x_routes()))
        archived = self.manifest()["assets"]
        network = FakeNetwork(x_routes())
        self.run_family([x_seed(self.rules())], network, metadata_only=True)
        self.assertEqual(network.count("pbs.twimg.com"), 0)
        self.assertEqual(self.manifest()["assets"], archived)
        self.assertEqual(self.candidates("artists:x:ariiol_")["1879155241788293570:1"]["disposition"], "downloaded")
        self.verify()

    def test_resume_redownloads_a_corrupted_file(self):
        self.run_family([x_seed(self.rules())], FakeNetwork(x_routes()))
        asset = self.manifest()["assets"][0]
        target = paths.safe_path(self.root, asset["path"])
        good = target.read_bytes()
        target.write_bytes(b"corrupt")  # corrupted in place, not deleted
        errors, _, _ = runner.verify_family(self.root, "artists")
        self.assertTrue(any("asset file" in error for error in errors))
        network = FakeNetwork(x_routes())
        self.run_family([x_seed(self.rules())], network, resume=True)
        self.assertEqual(network.count("pbs.twimg.com"), 1)
        self.assertEqual(target.read_bytes(), good)  # repaired: the new bytes hash to the record's SHA-256
        self.assertEqual(self.manifest()["assets"][0], asset)
        self.verify()
        target.unlink()  # a deleted file is downloaded again as well
        network = FakeNetwork(x_routes())
        self.run_family([x_seed(self.rules())], network, resume=True)
        self.assertEqual(network.count("pbs.twimg.com"), 1)
        self.assertEqual(target.read_bytes(), good)
        self.verify()


# --- Committed seeds --------------------------------------------------------------------------------------------------

class SeedTests(unittest.TestCase):
    def test_committed_seed_files_are_valid(self):
        ids = set()
        for family in ("artists", "official", "archives"):
            seeds = runner.load_seeds(runner.SEEDS_DIR / f"{family}.json", family)
            self.assertTrue(seeds)
            ids.update(seed["id"] for seed in seeds)
        schema.check_source_slugs([*schema.REGISTERED_SOURCE_IDS, *ids])

    def test_every_research_lead_has_a_recorded_disposition(self):
        crosswalk = manifest.load_json(runner.SEEDS_DIR / "leads-crosswalk.json")
        seeds = {}
        for family in ("artists", "official", "archives"):
            seeds.update({seed["id"]: seed for seed in runner.load_seeds(runner.SEEDS_DIR / f"{family}.json",
                                                                          family)})
        self.assertTrue(crosswalk["leads"])
        # The crosswalk still lists every yes/unclear lead of the research file (a dropped lead changes the hash).
        pairs = [[lead["lead"], lead["url"], lead["dynamix_original_game"]] for lead in crosswalk["leads"]]
        self.assertEqual([pair[0] for pair in pairs], list(range(crosswalk["lead_list"]["count"])))
        canonical = json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), crosswalk["lead_list"]["pairs_sha256"])
        for lead in crosswalk["leads"]:
            self.assertTrue(lead["dispositions"], f"lead {lead['lead']} has no disposition")
            for target in lead["dispositions"]:
                self.assertIn(target["source_id"], seeds)
                seed, prefix = seeds[target["source_id"]], target["key_prefix"]
                if seed["adapter"] == "lead_registry":
                    self.assertIn(prefix, {item["key"] for item in seed["items"]})
                    continue
                # Other sources: the committed snapshot holds a candidate under the prefix (a seed item alone is not
                # enough: the lead must have been acquired); an empty prefix stands for the source record itself.
                snapshot = manifest.load_discovery(ROOT, target["source_id"])
                self.assertIsNotNone(snapshot, f"lead {lead['lead']}: {target['source_id']} has no snapshot")
                keys = [c["upstream_key"] for c in snapshot["candidates"]]
                self.assertTrue(not prefix or any(key.startswith(prefix) for key in keys),
                                f"lead {lead['lead']}: {target['source_id']} has no candidate {prefix!r}")


if __name__ == "__main__":
    unittest.main()
