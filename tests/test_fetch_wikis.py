"""End-to-end offline tests of ``scripts/fetch_wikis.py`` against fake wikis: ``python -I tests/test_fetch_wikis.py``.

The real CLI (``wikis.cli.main``) runs with ``--root`` in a temporary directory and a fake client (see
``tests/fixtures/wikis``); no request leaves the process.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "wikis"))

from lib import manifest, net, schema  # noqa: E402
from wikis import acquire, cli, config, reviews  # noqa: E402

from fakewiki import image_bytes  # noqa: E402
from scenario import Scenario  # noqa: E402

NOW = "2026-10-10T12:00:00Z"


class WikiRun(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fetch-wikis-test-"))
        self.root = self.tmp / "repo"
        self.root.mkdir()
        self.work = self.tmp / "work"
        self.scenario = Scenario()
        self.logs: list[str] = []
        # Hermetic archivist reviews: the scenario's own table instead of scripts/wikis/reviews.json.
        self.reviews_path = self.tmp / "reviews.json"
        self.reviews_path.write_text(json.dumps(self.scenario.reviews_json()), encoding="utf-8")
        self._saved_reviews_path = reviews.REVIEWS_PATH
        reviews.REVIEWS_PATH = self.reviews_path

    def tearDown(self) -> None:
        reviews.REVIEWS_PATH = self._saved_reviews_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args: str, client=None) -> int:
        client = client or self.scenario.client()
        self.client = client
        return cli.main(["--root", str(self.root), "--work-dir", str(self.work), *args],
                        client_factory=lambda: client, now=lambda: NOW, log=self.logs.append)

    def verify(self) -> int:
        return cli.main(["--root", str(self.root), "--verify"], log=self.logs.append)

    def snapshot(self, key: str) -> dict:
        return manifest.load_discovery(self.root, config.SOURCES[key].source_id)

    def candidate(self, key: str, title: str) -> dict:
        cid = schema.candidate_id(config.SOURCES[key].source_id, title.replace(" ", "_"))
        return next(c for c in self.snapshot(key)["candidates"] if c["id"] == cid)

    def manifest(self) -> dict:
        return manifest.load_manifest(self.root, "wikis", missing_ok=False)

    def asset(self, key: str, title: str) -> dict:
        candidate = self.candidate(key, title)
        return next(a for a in self.manifest()["assets"] if a["id"] == candidate["asset_id"])


class MetadataOnlyTests(WikiRun):
    def test_complete_discovery_and_classification(self) -> None:
        self.assertEqual(self.run_cli("--metadata-only"), 0, "\n".join(self.logs))
        self.assertEqual(self.client.downloads, [])
        mira = self.snapshot("miraheze")
        self.assertTrue(mira["discovery_complete"])
        self.assertTrue(all(value is None for value in mira["continuation"].values()))
        api = self.client.apis[config.MIRAHEZE.api_url]
        self.assertGreater(api.calls["allimages"], 3, "page size 2 forces several continuation round trips")
        self.assertEqual(len(mira["candidates"]), len(self.scenario.miraheze_names))

        def check(key, title, disposition, role, reason_prefix=None, game=None):
            candidate = self.candidate(key, title)
            self.assertEqual((candidate["disposition"], candidate["role"]), (disposition, role), candidate)
            if reason_prefix is not None:
                self.assertEqual(candidate["reason"].split(":")[0], reason_prefix, candidate)
            if game is not None:
                self.assertEqual(candidate["game"], game)
            return candidate

        cosmic = check("miraheze", "File:CosmicPulseDynamix.png", "pending", "song_art", acquire.AWAITING, "dynamix")
        self.assertEqual((cosmic["illustrators"], cosmic["composers"]), (["ariiol"], ["Yunosuke"]))
        # Coordinator decision 2026-10-10 as amended: a chart announcement is a reference (archive tier) whether it
        # is about one song or two, and maps the songs its caption names.
        single = check("miraheze", "File:Cosmic Pulse Chart List.jpg", "pending", "reference", acquire.AWAITING)
        self.assertEqual(single["song_titles"], ["Cosmic Pulse"])
        self.assertNotIn("single-song", single["notes"])
        pair = check("miraheze", "File:Pair Comeback Chart List.jpg", "pending", "reference", acquire.AWAITING)
        self.assertEqual(pair["song_titles"], ["Cosmic Pulse", "(XD)"])
        self.assertEqual(schema.tier_for("reference", "JPEG"), "archive")
        # File description pages: a redirect to an identical file is an alias; a file that is only cited by a
        # [[:File:]] link has no role; the import boilerplate is not quoted, a human description is.
        alias = check("miraheze", "File:Alias Chart List.jpg", "excluded", None, "redirect_or_alias")
        self.assertIn("File:Cosmic Pulse Chart List.jpg", alias["reason"])
        cited = check("miraheze", "File:Cited Screenshot.png", "pending", None, "unresolved_role")
        self.assertIn("linked from Cosmic Pulse, Talk:Cosmic Pulse", cited["reason"])
        self.assertEqual(cited["game"], "unknown")  # a link is not a use: no game from the linking page
        self.assertNotIn("Importing images", self.candidate("miraheze", "File:Cosmic Pulse Chart List.jpg")["notes"])
        self.assertIn("file page text: 'The image is taken from the link below. https://x.com/ariiol_/status/1'",
                      cosmic["notes"])
        # Archivist reviews: a current review supplies role, game and an inferred song; a review of other bytes
        # no longer applies; an exclusion review uses the frozen vocabulary and quotes its evidence.
        event = check("miraheze", "File:Unused Event.png", "pending", "promotional", acquire.AWAITING, "dynamix")
        self.assertIn("archivist review (File:Unused Event.png): visual_inspection: event key art",
                      event["game_evidence"][0] + event["notes"])
        self.assertEqual(event["song_titles"], ["Cosmic Pulse"])
        stale = check("miraheze", "File:Stale Review.png", "pending", None, "unresolved_role")
        self.assertIn("review of an earlier revision no longer applies", stale["reason"])
        button = check("miraheze", "File:Wiki Button.png", "excluded", None, "site_ui")
        self.assertIn("visual_inspection: wiki navigation button", button["reason"])
        # List-graphic rule: a reviewed event lineup filed in Category:Event images is a reference; the candidate
        # notes keep the overridden category evidence and name the rule.
        for key in ("miraheze", "fandom"):
            lineup = check(key, "File:Events Lineup.png", "pending", "reference", acquire.AWAITING, "dynamix")
            self.assertIn("reference <- list and chart-announcement rule (coordinator decision 2026-10-10, amended)",
                          lineup["notes"])
            self.assertIn("(file category; Category:Event images)", lineup["notes"])
        # (XD)_D.jpg: a large JPEG in the Trivia gallery is gameplay, not the song's artwork.
        xd_d = check("miraheze", "File:(XD) D.jpg", "pending", "gameplay_screenshot", acquire.AWAITING, "dynamix")
        self.assertEqual(xd_d["song_titles"], ["(XD)"])
        self.assertNotIn("illustrators", xd_d)
        # "-HQ" names: a used background stays a background; an unused one is not accepted from its name.
        check("miraheze", "File:(XD)BG-HQ.png", "pending", "background", acquire.AWAITING)
        check("miraheze", "File:Lonely BG-HQ.png", "pending", None, "unresolved_game")
        check("miraheze", "File:Emi-Raw.png", "pending", "character", acquire.AWAITING)
        check("miraheze", "File:Emi-Animated.gif", "excluded", "character", "animated_capture")
        check("miraheze", "File:Universe Song cover.png", "excluded", "song_art", "universe_only", "dynamix_universe")
        check("miraheze", "File:Composer Photo.jpg", "excluded", None, "person_photo")
        check("miraheze", "File:Package Vocal.jpg", "pending", "pack_cover", acquire.AWAITING)
        check("fandom", "File:Some video", "excluded", None, "video_placeholder")
        # Shared artwork for another game named by its caption; an unreleased song's cover stays pending.
        check("miraheze", "File:Cosmic Pulse Squared.png", "pending", "song_art", acquire.AWAITING, "mixed")
        lost = check("miraheze", "File:LostDynamix.png", "pending", "song_art", "unresolved_game", "unknown")
        self.assertIn("Template:SongOther", lost["reason"])
        check("fandom", "File:WHIRLWINDBG.png", "pending", "background", acquire.AWAITING)

        index = manifest.load_json(self.root / "data/discovery/song-index.wikis.json")
        schema.validate_song_index(index)
        songs = {song["title"]: song for song in index["songs"]}
        self.assertEqual((songs["Cosmic Pulse"]["composers"], songs["Cosmic Pulse"]["illustrators"]),
                         (["Yunosuke"], ["ariiol"]))
        self.assertTrue(songs["Cosmic Pulse"]["in_original_game"])
        self.assertFalse(songs["Universe Song"]["in_original_game"])  # SongUV only ...
        self.assertTrue(songs["Universe Song"]["universe_also"])
        self.assertEqual(songs["交差して歪む過去と未来"]["id"], "song:file-568a196c")
        self.assertEqual(songs["交差して歪む過去と未来"]["composers"], [])  # "N/A" is unknown, not a name
        self.assertTrue(songs["Japanese Only Song"]["in_original_game"])
        self.assertTrue(songs["Removed Song"]["removed"])
        self.assertFalse(songs["Lost Song"]["in_original_game"])  # Template:SongOther + Category:Unreleased
        fields = {(d["song_id"], d["field"]) for d in index["disagreements"]}
        self.assertIn((songs["Japanese Only Song"]["id"], "presence"), fields)
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))
        sources = {s["id"]: s for s in self.manifest()["sources"]}
        self.assertEqual(sources["wikis:atwiki-ja"]["status"], "metadata_only")
        self.assertEqual(sources["wikis:miraheze"]["candidate_count"], len(mira["candidates"]))

    def test_incomplete_enumeration_is_recorded(self) -> None:
        wikis = self.scenario.wikis()
        wikis[config.MIRAHEZE.api_url].fail[("allimages", 2)] = net.HttpError(
            "HTTP 503", url=config.MIRAHEZE.api_url, http_status=503)
        client = self.scenario.client(wikis)
        self.assertEqual(self.run_cli("--metadata-only", client=client), 2)
        mira = self.snapshot("miraheze")
        self.assertFalse(mira["discovery_complete"])
        self.assertIn("aicontinue", mira["continuation"]["allimages"])
        self.assertTrue(mira["errors"])
        self.assertEqual(len(mira["candidates"]), 2)  # only the first page, nothing invented
        source = next(s for s in self.manifest()["sources"] if s["id"] == "wikis:miraheze")
        self.assertEqual((source["status"], source["discovery_complete"]), ("partially_available", False))
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_blocked_japanese_page_is_reported(self) -> None:
        client = self.scenario.client()
        client.errors["https://wikiwiki.jp/rhythmgame/Dynamix"] = net.ChallengeError(
            "challenge page served with HTTP 403", url="https://wikiwiki.jp/rhythmgame/Dynamix", http_status=403)
        self.assertEqual(self.run_cli("--metadata-only", "--source", "rhythmgame-ja", client=client), 2)
        snapshot = self.snapshot("rhythmgame-ja")
        self.assertIn("challenge", snapshot["errors"][0]["message"])
        source = next(s for s in self.manifest()["sources"] if s["id"] == "wikis:rhythmgame-ja")
        self.assertEqual((source["status"], source["evidence_level"]), ("manual_access", "blocked"))

    def test_pack_cover_is_titled_with_its_caption(self) -> None:
        """A package page's cover takes the wiki's caption as its title (Titles rule), not the file name."""
        vocal = dict(self.scenario.miraheze_pages["Vocal"])
        vocal["wikitext"] = vocal["wikitext"].replace("File:Package Vocal.jpg|Package Vocal",
                                                      "File:Package Vocal.jpg|Package Vocal #1")
        for pages in (self.scenario.miraheze_pages, self.scenario.fandom_pages):
            pages["Vocal"] = vocal
        self.assertEqual(self.run_cli("--metadata-only", "--source", "miraheze"), 0, "\n".join(self.logs))
        candidate = self.candidate("miraheze", "File:Package Vocal.jpg")
        self.assertEqual((candidate["role"], candidate["title"]), ("pack_cover", "Package Vocal #1"))

    def test_lock_prevents_second_writer(self) -> None:
        with acquire.exclusive_lock(self.work / "lock"):
            self.assertEqual(self.run_cli("--metadata-only"), 3)


class AcquisitionTests(WikiRun):
    def test_download_records_and_byte_rules(self) -> None:
        self.assertEqual(self.run_cli(), 0, "\n".join(self.logs))
        self.assertTrue(all("format=original" in url for url in self.client.downloads
                            if "wikia.nocookie.net" in url))
        mira = self.asset("miraheze", "File:CosmicPulseDynamix.png")
        self.assertEqual((mira["illustrators"], mira["composers"]), (["ariiol"], ["Yunosuke"]))
        roles = {(item["role"], item["name"], item["field"]) for item in mira["credit_evidence"]}
        self.assertEqual(roles, {("illustrator", "ariiol", "Template:Song|Cover Artist"),
                                 ("composer", "Yunosuke", "Template:Song|Compound Artist Link")})
        self.assertEqual(mira["mapping_status"], "confirmed_wiki")
        self.assertEqual(mira["song_ids"], ["song:cosmic-pulse"])
        self.assertEqual(mira["comparisons"], {"sha1_match": True, "bytes_match": True, "dimensions_match": True,
                                               "format_match": True})
        # Duplicate cross-wiki bytes: Fandom keeps its own record and path; its API size/SHA-1 disagree.
        fandom = self.asset("fandom", "File:CosmicPulseDynamix.png")
        self.assertEqual(fandom["sha256"], mira["sha256"])
        self.assertNotEqual(fandom["path"], mira["path"])
        self.assertTrue(fandom["path"].startswith("assets/public/wikis/fandom/"))
        self.assertEqual((fandom["comparisons"]["sha1_match"], fandom["comparisons"]["bytes_match"]),
                         (False, False))
        self.assertIn("not as a second version", fandom["notes"])
        self.assertIn("(the API reports 18 more bytes than were served; cause not established)", fandom["notes"])
        self.assertEqual(fandom["quality"]["source_variant"], "unknown")  # not the upload byte for byte
        self.assertEqual(mira["quality"]["source_variant"], "site_original")
        self.assertIn("Fandom's API records this title's upload with another SHA-1", mira["notes"])
        self.assertNotIn("transformed", fandom)
        # Same-wiki duplicate: a second File: title with identical bytes points at the first record.
        copy = self.candidate("miraheze", "File:Cosmic Pulse copy.png")
        self.assertEqual((copy["disposition"], copy["asset_id"]), ("duplicate_bytes", mira["id"]))
        # CDN WEBP behind a .png URL: stored as WEBP with a .webp path and flagged as a transform.
        whirl = self.asset("fandom", "File:WHIRLWINDBG.png")
        self.assertEqual((whirl["format"], whirl["path"][-5:], whirl.get("transformed")), ("WEBP", ".webp", True))
        self.assertFalse(whirl["comparisons"]["format_match"])
        self.assertEqual(whirl["original_filename"], "WHIRLWINDBG.png")
        # Unicode title and case-colliding titles get distinct, collision-free paths.
        uni = self.asset("miraheze", "File:交差して歪む過去と未来.jpg")
        self.assertRegex(uni["path"], r"^assets/public/wikis/miraheze/file--[0-9a-f]{12}\.jpg$")
        lower, upper = self.asset("miraheze", "File:Abc.png"), self.asset("miraheze", "File:ABC.png")
        self.assertNotEqual(lower["path"].casefold(), upper["path"].casefold())
        # Same size, different bytes: two records, never a duplicate.
        a, b = self.asset("miraheze", "File:SameSizeA.png"), self.asset("miraheze", "File:SameSizeB.png")
        self.assertEqual(a["bytes"], b["bytes"])
        self.assertNotEqual(a["sha256"], b["sha256"])
        # Kinds by tier: the gameplay screenshot is archive tier, the background site tier.
        self.assertEqual(self.asset("miraheze", "File:(XD) D.jpg")["kind"], "gameplay_screenshot")
        self.assertEqual(self.asset("miraheze", "File:(XD)BG-HQ.png")["kind"], "background")
        self.assertEqual(self.asset("miraheze", "File:(XD)BG-HQ.png")["illustrators"], [])
        # A reviewed file's song mapping is at most inferred and quotes the review.
        event = self.asset("miraheze", "File:Unused Event.png")
        self.assertEqual((event["kind"], event["mapping_status"], event["song_ids"]),
                         ("promotional", "inferred", ["song:cosmic-pulse"]))
        self.assertEqual(event["mapping_evidence"][0]["field"], "archivist review")
        self.assertTrue(event["mapping_evidence"][0]["quote"].startswith("visual_inspection:"))
        chart_list = self.asset("miraheze", "File:Cosmic Pulse Chart List.jpg")
        self.assertEqual((chart_list["kind"], chart_list["mapping_status"], chart_list["song_ids"]),
                         ("reference", "confirmed_wiki", ["song:cosmic-pulse"]))
        self.assertNotIn("single-song", chart_list.get("notes", ""))
        pair = self.asset("miraheze", "File:Pair Comeback Chart List.jpg")
        self.assertEqual((pair["kind"], pair["mapping_status"], pair["song_ids"]),
                         ("reference", "confirmed_wiki", ["song:cosmic-pulse", "song:xd"]))
        self.assertIn("caption 'Cosmic Pulse X (XD) Comeback Chart List' names the song '(XD)'",
                      pair["mapping_evidence"][-1]["quote"])
        # Titles (catalog presentation rules): confirmed single-song art takes the song's title; other files the
        # wiki's caption, else the file name without its extension.
        self.assertEqual(self.asset("miraheze", "File:CosmicPulseDynamix.png")["title"], "Cosmic Pulse")
        self.assertEqual(chart_list["title"], "Cosmic Pulse Chart List")
        self.assertEqual(self.asset("miraheze", "File:(XD)BG-HQ.png")["title"], 'Used Background "XD"')
        self.assertEqual(self.asset("miraheze", "File:Unused Event.png")["title"], "Unused Event")
        # List-graphic rule: the asset notes name the rule, the list evidence and the overridden page evidence.
        lineup = self.asset("fandom", "File:Events Lineup.png")
        self.assertEqual((lineup["kind"], lineup["mapping_status"]), ("reference", "unmapped"))
        self.assertIn("Kind reference by the list and chart-announcement rule (coordinator decision 2026-10-10, "
                      "amended)", lineup["notes"])
        self.assertIn("List evidence: archivist review (File:Events Lineup.png): visual_inspection: monthly event "
                      "lineup", lineup["notes"])
        self.assertIn("Promotional page evidence it overrides: https://dynamix.miraheze.org/wiki/"
                      "File:Events_Lineup.png (file category; Category:Event images)", lineup["notes"])
        self.assertIn("File description page: 'The image is taken from the link below.",
                      self.asset("miraheze", "File:CosmicPulseDynamix.png")["notes"])
        squared = self.asset("miraheze", "File:Cosmic Pulse Squared.png")
        self.assertEqual(squared["game_associations"], ["dynamix", "ravon"])
        self.assertEqual(squared["illustrators"], [])  # not the template's cover: no illustrator inferred
        # Excluded and pending candidates are never downloaded.
        downloaded = " ".join(self.client.downloads)
        for name in ("Emi-Animated.gif", "Universe_Song_cover.png", "Composer_Photo.jpg", "Lonely_BG-HQ.png",
                     "Alias_Chart_List.jpg", "Cited_Screenshot.png", "Stale_Review.png", "Wiki_Button.png"):
            self.assertNotIn(name, downloaded)
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))
        index = manifest.load_json(self.root / "data/discovery/song-index.wikis.json")
        cosmic = next(song for song in index["songs"] if song["title"] == "Cosmic Pulse")
        self.assertIn(mira["id"], cosmic["artwork_asset_ids"])

    def test_fandom_bytes_that_miraheze_shows_are_not_the_upload(self) -> None:
        """Both APIs record the upload with one SHA-1, Miraheze serves those bytes, Fandom's format=original response
        differs: the Fandom bytes are known to be a platform transform of the stored upload (Dangerous Insanity)."""
        client = self.scenario.client()
        url = next(url for url in client.blobs if "Pair_Comeback" in url and "nocookie" in url)
        client.blobs[url] = image_bytes("JPEG", (64, 36), 126)  # other bytes, same format and dimensions
        self.assertNotEqual(client.blobs[url], self.scenario.data["Pair Comeback Chart List.jpg"])
        self.assertEqual(self.run_cli(client=client), 0, "\n".join(self.logs))
        fandom = self.asset("fandom", "File:Pair Comeback Chart List.jpg")
        mira = self.asset("miraheze", "File:Pair Comeback Chart List.jpg")
        self.assertNotEqual(fandom["sha256"], mira["sha256"])
        self.assertEqual(mira["sha1"], fandom["source_reported"]["sha1"])
        self.assertIs(fandom.get("transformed"), True)
        self.assertEqual(fandom["quality"]["source_variant"], "unknown")
        self.assertIn("Miraheze's API reports the same SHA-1", fandom["quality"]["notes"])
        self.assertNotIn("cause not established", fandom.get("notes", ""))
        self.assertNotIn("transformed", mira)
        self.assertNotIn("transformed", self.asset("fandom", "File:CosmicPulseDynamix.png"))  # APIs disagree there
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_include_animated(self) -> None:
        self.assertEqual(self.run_cli("--include-animated", "--source", "miraheze", "--only",
                                      "File:Emi-Animated.gif"), 0)
        gif = self.asset("miraheze", "File:Emi-Animated.gif")
        self.assertEqual((gif["format"], gif["kind"]), ("GIF", "character"))
        # The option is reversible: later default runs keep the archived GIF instead of stopping for review.
        self.assertEqual(self.run_cli("--metadata-only"), 0, "\n".join(self.logs))
        self.assertEqual(self.run_cli(), 0, "\n".join(self.logs))
        candidate = self.candidate("miraheze", "File:Emi-Animated.gif")
        self.assertEqual((candidate["disposition"], candidate["asset_id"]), ("downloaded", gif["id"]))
        self.assertIn("--include-animated", candidate["notes"])
        self.assertEqual(self.candidate("fandom", "File:Emi-Animated.gif")["reason"].split(":")[0],
                         "animated_capture")  # never archived on Fandom: still excluded by default
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_metadata_refresh_without_download(self) -> None:
        """Better evidence on a later run updates an archived record's descriptive fields, never its bytes."""
        self.assertEqual(self.run_cli("--source", "miraheze", "--only", "File:CosmicPulseDynamix.png"), 0)
        before = self.asset("miraheze", "File:CosmicPulseDynamix.png")
        self.scenario.miraheze_pages["Cosmic Pulse"] = dict(self.scenario.miraheze_pages["Cosmic Pulse"],
                                                            categories=["Category:Songs", "Category:IAPs"])
        client = self.scenario.client()
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only", client=client), 0)
        after = self.asset("miraheze", "File:CosmicPulseDynamix.png")
        self.assertEqual(client.downloads, [])
        self.assertEqual((before["collections"], after["collections"]), (["Event", "Vocal"], ["Song packs (IAP)", "Vocal"]))
        self.assertEqual({k: v for k, v in after.items() if k != "collections"},
                         {k: v for k, v in before.items() if k != "collections"})
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_reclassified_archived_file_stops_for_review(self) -> None:
        self.assertEqual(self.run_cli("--source", "miraheze", "--only", "File:Emi-Raw.png"), 0)
        before = (self.root / "data/sources/wikis.json").read_bytes()
        # A page edit: the only page using Emi-Raw.png is now a Dynamix Universe song page.
        xd = dict(self.scenario.miraheze_pages["(XD)"])
        xd["wikitext"] = xd["wikitext"].replace("{{Song", "{{SongUV")
        self.scenario.miraheze_pages["(XD)"] = xd
        self.scenario.fandom_pages["(XD)"] = xd
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 4)
        self.assertEqual((self.root / "data/sources/wikis.json").read_bytes(), before)
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_reupload_and_removal_keep_archived_bytes(self) -> None:
        self.assertEqual(self.run_cli("--source", "miraheze"), 0, "\n".join(self.logs))
        old_bg = self.asset("miraheze", "File:(XD)BG-HQ.png")
        old_emi = self.asset("miraheze", "File:Emi-Raw.png")
        from fakewiki import image_bytes
        self.scenario.data["(XD)BG-HQ.png"] = image_bytes("PNG", (49, 42), 99)
        self.scenario.overrides[("miraheze", "(XD)BG-HQ.png")] = {"timestamp": "2026-09-01T00:00:00Z"}
        self.scenario.miraheze_names.remove("Emi-Raw.png")
        self.assertEqual(self.run_cli("--source", "miraheze"), 0, "\n".join(self.logs))
        assets = {a["id"]: a for a in self.manifest()["assets"]}
        new_bg = self.asset("miraheze", "File:(XD)BG-HQ.png")
        self.assertNotEqual(new_bg["id"], old_bg["id"])
        self.assertEqual((assets[old_bg["id"]]["upstream_status"], assets[old_bg["id"]]["superseded_by"]),
                         ("superseded", new_bg["id"]))
        self.assertEqual(assets[old_emi["id"]]["upstream_status"], "removed")
        self.assertTrue((self.root / old_bg["path"]).is_file())
        self.assertTrue((self.root / old_emi["path"]).is_file())
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_failures_and_blocks_are_recorded(self) -> None:
        client = self.scenario.client()
        bad = next(url for url in client.blobs if "Package_Vocal" in url and "wikitide" in url)
        blocked = next(url for url in client.blobs if "TwinDynamix" in url and "wikitide" in url)
        client.errors[bad] = net.HttpError("HTTP 503", url=bad, http_status=503, retriable=True)
        client.errors[blocked] = net.BlockedError("HTTP 403", url=blocked, http_status=403)
        self.assertEqual(self.run_cli("--source", "miraheze", client=client), 1)
        self.assertEqual(self.candidate("miraheze", "File:Package Vocal.jpg")["disposition"], "failed")
        self.assertEqual(self.candidate("miraheze", "File:TwinDynamix.png")["disposition"], "blocked")
        failures = {f["candidate_id"]: f for f in self.manifest()["failures"]}
        self.assertTrue(failures["wikis:miraheze:File:TwinDynamix.png"]["manual"])
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))
        # A later run retries the retriable failure (not the block) and clears its Failure record.
        client = self.scenario.client()
        self.assertEqual(self.run_cli("--source", "miraheze", client=client), 1)  # the block remains
        self.assertEqual(self.candidate("miraheze", "File:Package Vocal.jpg")["disposition"], "downloaded")
        self.assertNotIn("TwinDynamix", " ".join(client.downloads))
        self.assertEqual(self.candidate("miraheze", "File:TwinDynamix.png")["disposition"], "blocked")


    def test_failed_files_are_retried_after_new_files(self) -> None:
        """A --limit batch spends its attempts on new files first; an earlier failure is retried once, last."""
        client = self.scenario.client()
        bad = next(url for url in client.blobs if "Package_Vocal" in url and "wikitide" in url)
        client.errors[bad] = net.HttpError("HTTP 503", url=bad, http_status=503, retriable=True)
        self.assertEqual(self.run_cli("--source", "miraheze", "--limit", "3", client=client), 1)
        self.assertIn(bad, client.downloads)  # strength-3 files come first; Package Vocal is among them
        client = self.scenario.client()
        self.assertEqual(self.run_cli("--source", "miraheze", client=client), 0, "\n".join(self.logs))
        self.assertGreater(len(client.downloads), 3)
        self.assertEqual(client.downloads[-1], bad)
        self.assertEqual(self.candidate("miraheze", "File:Package Vocal.jpg")["disposition"], "downloaded")
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))


def clock():
    """Increasing UTC timestamps (one second per call)."""
    count = iter(range(10 ** 6))

    def now() -> str:
        n = next(count)
        return f"2026-10-10T{12 + n // 3600:02d}:{n // 60 % 60:02d}:{n % 60:02d}Z"
    return now


class ArchiveIntegrityTests(WikiRun):
    """Archived records follow their bytes: damage is repaired, moves and reverts keep a current record, an outage
    never re-classifies archived files, and only a reviewed withdrawal removes one."""

    def damage(self, title: str, how: str) -> dict:
        asset = self.asset("miraheze", title)
        target = self.root / asset["path"]
        data = bytearray(target.read_bytes())
        if how == "truncate":
            del data[-10:]
        else:
            data[-20] ^= 0xFF  # same size, other bytes
        target.write_bytes(bytes(data))
        return asset

    def test_resume_repairs_damaged_files(self) -> None:
        self.assertEqual(self.run_cli("--source", "miraheze"), 0)
        truncated = self.damage("File:(XD)Dynamix.png", "truncate")
        flipped = self.damage("File:Emi-Raw.png", "flip")
        self.assertEqual(self.verify(), 1)
        client = self.scenario.client()
        self.assertEqual(self.run_cli("--source", "miraheze", "--resume", client=client), 0, "\n".join(self.logs))
        self.assertEqual(len(client.downloads), 2)
        self.assertFalse((self.work / "journal.jsonl").exists())
        self.assertEqual(self.manifest()["failures"], [])
        for asset in (truncated, flipped):
            self.assertEqual(self.asset("miraheze", "File:" + asset["original_filename"].replace("_", " "))["id"],
                             asset["id"])
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 0)  # nothing left to replay

    def test_failed_repair_keeps_the_record(self) -> None:
        self.assertEqual(self.run_cli("--source", "miraheze"), 0)
        asset = self.damage("File:(XD)Dynamix.png", "truncate")
        client = self.scenario.client()
        url = next(url for url in client.blobs if "(XD)Dynamix" in url.replace("%28", "(").replace("%29", ")")
                   and "wikitide" in url)
        client.errors[url] = net.HttpError("HTTP 503", url=url, http_status=503, retriable=True)
        self.assertEqual(self.run_cli("--source", "miraheze", "--resume", client=client), 1)
        self.assertTrue(any(line.startswith("REPAIR FAILED") for line in self.logs))
        candidate = self.candidate("miraheze", "File:(XD)Dynamix.png")
        self.assertEqual((candidate["disposition"], candidate["asset_id"]), ("downloaded", asset["id"]))
        self.assertEqual(self.manifest()["failures"], [])
        self.assertFalse((self.work / "journal.jsonl").exists())
        self.assertEqual(self.verify(), 1)  # the damage is still visible
        self.assertEqual(self.run_cli("--source", "miraheze", "--resume"), 0, "\n".join(self.logs))
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_file_move_keeps_a_current_record(self) -> None:
        self.assertEqual(self.run_cli(), 0)
        old = self.asset("miraheze", "File:(XD)Dynamix.png")
        sc = self.scenario
        new = "XD cover (moved).png"
        sc.data[new] = sc.data["(XD)Dynamix.png"]
        for names in (sc.miraheze_names, sc.fandom_names):
            names.remove("(XD)Dynamix.png")
            names.append(new)
        for pages in (sc.miraheze_pages, sc.fandom_pages):
            pages["(XD)"] = dict(pages["(XD)"], wikitext=pages["(XD)"]["wikitext"].replace("(XD)Dynamix.png", new))
        self.assertEqual(self.run_cli(), 0, "\n".join(self.logs))
        moved = self.asset("miraheze", "File:" + new)
        self.assertEqual((moved["id"], moved["path"], moved["upstream_status"]), (old["id"], old["path"], "current"))
        self.assertEqual((moved["candidate_id"], moved["original_filename"]),
                         ("wikis:miraheze:File:XD_cover_(moved).png", "(XD)Dynamix.png"))
        self.assertIn("first archived as wikis:miraheze:File:(XD)Dynamix.png", moved["notes"])
        index = manifest.load_json(self.root / "data/discovery/song-index.wikis.json")
        xd = next(song for song in index["songs"] if song["title"] == "(XD)")
        self.assertIn(old["id"], xd["artwork_asset_ids"])
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_duplicate_title_inherits_the_record_of_a_removed_title(self) -> None:
        self.assertEqual(self.run_cli("--source", "miraheze"), 0)
        record = self.asset("miraheze", "File:CosmicPulseDynamix.png")
        self.assertEqual(self.candidate("miraheze", "File:Cosmic Pulse copy.png")["disposition"], "duplicate_bytes")
        self.scenario.miraheze_names.remove("CosmicPulseDynamix.png")
        client = self.scenario.client()
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only", client=client), 0,
                         "\n".join(self.logs))
        copy = self.candidate("miraheze", "File:Cosmic Pulse copy.png")
        self.assertEqual((copy["disposition"], copy["asset_id"]), ("downloaded", record["id"]))
        assets = {a["id"]: a for a in self.manifest()["assets"]}
        self.assertEqual(assets[record["id"]]["upstream_status"], "current")
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_same_bytes_under_a_new_revision_stay_current(self) -> None:
        self.assertEqual(self.run_cli("--source", "miraheze"), 0)
        before = self.asset("miraheze", "File:(XD)BG-HQ.png")
        self.scenario.overrides[("miraheze", "(XD)BG-HQ.png")] = {"timestamp": "2026-09-01T00:00:00Z"}
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 0)
        after = self.asset("miraheze", "File:(XD)BG-HQ.png")
        self.assertEqual((after["id"], after["upstream_status"], after["source_reported"]["timestamp"]),
                         (before["id"], "current", "2026-09-01T00:00:00Z"))
        self.assertIn("Upstream history: same bytes as the earlier upload 2021-02-11T14:41:30Z", after["notes"])
        client = self.scenario.client()
        self.assertEqual(self.run_cli("--source", "miraheze", client=client), 0)
        self.assertEqual(client.downloads, [])  # nothing to re-download, no flip-flop
        self.assertEqual(self.asset("miraheze", "File:(XD)BG-HQ.png"), after)
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_revert_restores_the_earlier_record(self) -> None:
        from fakewiki import image_bytes
        self.assertEqual(self.run_cli("--source", "miraheze"), 0)
        first = self.asset("miraheze", "File:(XD)BG-HQ.png")
        original = self.scenario.data["(XD)BG-HQ.png"]
        self.scenario.data["(XD)BG-HQ.png"] = image_bytes("PNG", (49, 42), 99)
        self.scenario.overrides[("miraheze", "(XD)BG-HQ.png")] = {"timestamp": "2026-09-01T00:00:00Z"}
        self.assertEqual(self.run_cli("--source", "miraheze"), 0)
        second = self.asset("miraheze", "File:(XD)BG-HQ.png")
        self.scenario.data["(XD)BG-HQ.png"] = original  # revert: the first bytes as a new revision
        self.scenario.overrides[("miraheze", "(XD)BG-HQ.png")] = {"timestamp": "2026-09-02T00:00:00Z"}
        self.assertEqual(self.run_cli("--source", "miraheze"), 0, "\n".join(self.logs))
        assets = {a["id"]: a for a in self.manifest()["assets"]}
        self.assertEqual(self.candidate("miraheze", "File:(XD)BG-HQ.png")["asset_id"], first["id"])
        self.assertEqual(assets[first["id"]]["upstream_status"], "current")
        self.assertNotIn("superseded_by", assets[first["id"]])
        self.assertEqual((assets[second["id"]]["upstream_status"], assets[second["id"]]["superseded_by"]),
                         ("superseded", first["id"]))
        self.assertEqual(assets[first["id"]]["source_reported"]["timestamp"], "2026-09-02T00:00:00Z")
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_outage_after_acquisition_keeps_archived_classification(self) -> None:
        self.assertEqual(self.run_cli(), 0)
        before = {key: self.snapshot(key) for key in ("miraheze", "fandom")}
        index = (self.root / "data/discovery/song-index.wikis.json").read_bytes()
        wikis = self.scenario.wikis()
        wikis[config.FANDOM.api_url].fail[("pages", 1)] = net.HttpError(
            "HTTP 503", url=config.FANDOM.api_url, http_status=503)  # an outage on Fandom only
        client = self.scenario.client(wikis)
        self.assertEqual(self.run_cli(client=client), 2, "\n".join(self.logs))
        self.assertEqual(client.downloads, [])
        mira, fandom = self.snapshot("miraheze"), self.snapshot("fandom")
        self.assertTrue(mira["discovery_complete"])  # Miraheze itself was enumerated completely ...
        self.assertFalse(fandom["discovery_complete"])
        self.assertTrue(fandom["errors"])
        for key, snapshot in (("miraheze", mira), ("fandom", fandom)):  # ... but the pooled evidence is partial
            old = {c["id"]: c for c in before[key]["candidates"]}
            for candidate in snapshot["candidates"]:
                if old[candidate["id"]]["disposition"] == "downloaded":
                    self.assertEqual((candidate["disposition"], candidate["role"], candidate["asset_id"]),
                                     (old[candidate["id"]]["disposition"], old[candidate["id"]]["role"],
                                      old[candidate["id"]]["asset_id"]))
                    self.assertIn("this discovery is incomplete", candidate["notes"])
        self.assertEqual((self.root / "data/discovery/song-index.wikis.json").read_bytes(), index)
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))
        self.assertEqual(self.run_cli(), 0, "\n".join(self.logs))  # the next complete discovery re-classifies
        self.assertNotIn("incomplete", self.candidate("miraheze", "File:Emi-Raw.png")["notes"])

    def test_withdrawal_is_the_only_way_out(self) -> None:
        self.assertEqual(self.run_cli("--source", "miraheze"), 0)
        event = self.asset("miraheze", "File:Unused Event.png")
        table = self.scenario.reviews_json()
        entry = next(item for item in table["reviews"] if item["title"] == "File:Unused Event.png")
        entry.update(role="fanart", evidence="visual_inspection: fan-made event poster")
        for key in ("game", "game_evidence", "songs"):
            entry.pop(key)
        self.reviews_path.write_text(json.dumps(table), encoding="utf-8")
        before = (self.root / "data/sources/wikis.json").read_bytes()
        self.assertEqual(self.run_cli("--source", "miraheze"), 4)  # an unreviewed exclusion stops the run
        self.assertEqual((self.root / "data/sources/wikis.json").read_bytes(), before)
        table["withdrawals"] = [{"title": "File:Unused Event.png", "sha256": [event["sha256"]], "reason": "fanart",
                                 "evidence": "visual_inspection: fan-made event poster"}]
        self.reviews_path.write_text(json.dumps(table), encoding="utf-8")
        self.assertEqual(self.run_cli("--source", "miraheze"), 0, "\n".join(self.logs))
        self.assertNotIn(event["id"], {a["id"] for a in self.manifest()["assets"]})
        self.assertFalse((self.root / event["path"]).exists())
        self.assertEqual(self.candidate("miraheze", "File:Unused Event.png")["reason"].split(":")[0], "fanart")
        self.assertTrue(any(line.startswith("WITHDRAWN") for line in self.logs))
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_download_probe_failure_is_recorded(self) -> None:
        table = self.scenario.reviews_json()
        table["reviews"].append({"title": "File:Cited Screenshot.png", "sha1": [hashlib.sha1(
            self.scenario.data["Cited Screenshot.png"]).hexdigest()], "role": None,
            "evidence": "download_probe: the file returns HTTP 404 from the CDN",
            "pending": "the content could not be inspected"})
        self.reviews_path.write_text(json.dumps(table), encoding="utf-8")
        client = self.scenario.client()
        url = next(url for url in client.blobs if "Cited_Screenshot" in url and "wikitide" in url)
        del client.blobs[url]  # the CDN answers 404
        self.assertEqual(self.run_cli("--source", "miraheze", client=client), 1)
        candidate = self.candidate("miraheze", "File:Cited Screenshot.png")
        self.assertEqual((candidate["disposition"], candidate["role"]), ("failed", None))
        self.assertIn("inspection download for the archivist review (unresolved_role: archivist review",
                      candidate["notes"])
        [failure] = [f for f in self.manifest()["failures"] if f["candidate_id"] == candidate["id"]]
        self.assertEqual((failure["phase"], failure["error_class"], failure["http_status"]), ("download", "http_4xx",
                                                                                              404))
        client = self.scenario.client()
        del client.blobs[url]
        self.assertEqual(self.run_cli("--source", "miraheze", client=client), 1)
        self.assertNotIn(url, client.downloads)  # a 404 is not retried automatically
        self.assertEqual(self.candidate("miraheze", "File:Cited Screenshot.png")["disposition"], "failed")
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_missing_file_url_is_a_failure(self) -> None:
        wikis = self.scenario.wikis()
        row = next(row for row in wikis[config.MIRAHEZE.api_url].files if row["name"] == "TwinDynamix.png")
        row["url"] = None
        client = self.scenario.client(wikis)
        self.assertEqual(self.run_cli(client=client), 1, "\n".join(self.logs))
        candidate = self.candidate("miraheze", "File:TwinDynamix.png")
        self.assertEqual(candidate["disposition"], "failed")
        [failure] = [f for f in self.manifest()["failures"] if f["candidate_id"] == candidate["id"]]
        self.assertEqual((failure["error_class"], failure["url"]), ("other", candidate["page_url"]))
        self.assertEqual(self.candidate("fandom", "File:TwinDynamix.png")["disposition"], "downloaded")
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_checked_at_is_the_last_access(self) -> None:
        client = self.scenario.client()
        self.assertEqual(cli.main(["--root", str(self.root), "--work-dir", str(self.work), "--source", "miraheze"],
                                  client_factory=lambda: client, now=clock(), log=self.logs.append), 0)
        data = self.manifest()
        source = next(s for s in data["sources"] if s["id"] == "wikis:miraheze")
        last = max(a["fetched_at"] for a in data["assets"] if a["source_id"] == "wikis:miraheze")
        self.assertGreater(last, self.snapshot("miraheze")["finished_at"])
        self.assertGreaterEqual(source["checked_at"], last)

    def test_root_lock_and_foreign_journal(self) -> None:
        with acquire.exclusive_lock(acquire.root_lock_path(self.root)):
            self.assertEqual(self.run_cli("--metadata-only"), 3)  # another --work-dir does not help
        self.assertEqual(self.run_cli("--metadata-only"), 0)
        acquire.Journal(self.work / "journal.jsonl", self.root).append(
            {"event": "clear_failures", "id": "wikis:miraheze:File:Nothing.png"})
        other = self.tmp / "other-work"
        code = cli.main(["--root", str(self.root), "--work-dir", str(other), "--metadata-only"],
                        client_factory=self.scenario.client, now=lambda: NOW, log=self.logs.append)
        self.assertEqual(code, 3)
        self.assertEqual(self.run_cli("--metadata-only"), 0)  # resuming with the journal's own work dir works


class ResumeTests(WikiRun):
    def test_interrupt_writes_outputs_and_resume_continues(self) -> None:
        client = self.scenario.client()
        client.interrupt_after = 3
        self.assertEqual(self.run_cli("--source", "miraheze", client=client), 130)
        done = {a["candidate_id"] for a in self.manifest()["assets"]}
        self.assertEqual(len(done), 3)
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))
        self.assertFalse((self.work / "journal.jsonl").exists())
        client = self.scenario.client()
        self.assertEqual(self.run_cli("--source", "miraheze", "--resume", client=client), 0, "\n".join(self.logs))
        fetched_again = [url for url in client.downloads if any(
            cid.split(":File:", 1)[1].replace(" ", "_") in url.replace("%28", "(").replace("%29", ")")
            for cid in done)]
        self.assertEqual(fetched_again, [])
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_concurrent_workers_share_the_limit_and_stop_together(self) -> None:
        """Both wikis download concurrently (one worker each); --limit bounds them together, and an interrupt in one
        worker stops the other after its current file with consistent outputs."""
        client = self.scenario.client()
        self.assertEqual(self.run_cli("--limit", "5", client=client), 0, "\n".join(self.logs))
        self.assertEqual(len(client.downloads), 5)
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))
        client = self.scenario.client()
        client.interrupt_after = 6
        self.assertEqual(self.run_cli(client=client), 130)
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))
        client = self.scenario.client()
        self.assertEqual(self.run_cli("--resume", client=client), 0, "\n".join(self.logs))
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))
        sources = {a["source_id"] for a in self.manifest()["assets"]}
        self.assertEqual(sources, {"wikis:miraheze", "wikis:fandom"})

    def test_journal_replay_after_hard_kill(self) -> None:
        """A process killed between downloads and the next manifest write loses nothing: the journal is replayed."""
        self.assertEqual(self.run_cli("--metadata-only", "--source", "miraheze"), 0)
        args = cli.parse_args(["--root", str(self.root), "--source", "miraheze"])
        client = self.scenario.client()
        runner = cli.Runner(self.root, args, self.work, client_factory=lambda: client, now=lambda: NOW,
                            log=self.logs.append)
        runner.state = acquire.State.load(self.root, runner.journal)
        runner.discover(client)
        acquirer = acquire.Acquirer(self.root, runner.state, runner.journal, client, now=lambda: NOW,
                                    log=self.logs.append)
        plans = [p for p in runner.plans["miraheze"] if p.accepted][:2]
        for plan in plans:
            acquirer.download(plan)
        # "kill": no write(); the manifest on disk still has no assets, the journal has the events.
        self.assertEqual(self.manifest()["assets"], [])
        self.assertTrue((self.work / "journal.jsonl").is_file())
        client = self.scenario.client()
        self.assertEqual(self.run_cli("--source", "miraheze", "--resume", client=client), 0, "\n".join(self.logs))
        ids = {a["candidate_id"] for a in self.manifest()["assets"]}
        for plan in plans:
            self.assertIn(plan.candidate_id, ids)
            self.assertNotIn(plan.info["url"], client.downloads)
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_resume_rehashes_and_verify_catches_damage(self) -> None:
        self.assertEqual(self.run_cli("--source", "miraheze"), 0)
        asset = self.asset("miraheze", "File:(XD)Dynamix.png")
        (self.root / asset["path"]).unlink()
        client = self.scenario.client()
        self.assertEqual(self.run_cli("--source", "miraheze", "--resume", client=client), 0, "\n".join(self.logs))
        self.assertEqual(len(client.downloads), 1)
        self.assertTrue((self.root / asset["path"]).is_file())
        self.assertEqual(self.verify(), 0)
        target = self.root / asset["path"]
        target.write_bytes(target.read_bytes()[:-10])
        self.assertEqual(self.verify(), 1)

    def test_verify_reports_orphans(self) -> None:
        self.assertEqual(self.run_cli("--source", "miraheze", "--only", "File:(XD)Dynamix.png"), 0)
        stray = self.root / "assets/public/wikis/miraheze/stray--000000000000.png"
        stray.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.assertEqual(self.verify(), 1)
        self.assertTrue(any("orphan" in line for line in self.logs))

    def test_script_entry_point(self) -> None:
        """``python -I scripts/fetch_wikis.py --verify --root`` works as a subprocess (offline)."""
        result = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "fetch_wikis.py"), "--verify",
                                 "--root", str(self.root)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("verify: OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
