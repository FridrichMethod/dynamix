"""End-to-end offline tests of ``scripts/fetch_wikis.py`` against fake wikis: ``python -I tests/test_fetch_wikis.py``.

The real CLI (``wikis.cli.main``) runs with ``--root`` in a temporary directory and a fake client (see
``tests/fixtures/wikis``); no request leaves the process.
"""

from __future__ import annotations

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
from wikis import acquire, cli, config  # noqa: E402

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

    def tearDown(self) -> None:
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
        check("miraheze", "File:Cosmic Pulse Chart List.jpg", "pending", "promotional", acquire.AWAITING)
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
        squared = self.asset("miraheze", "File:Cosmic Pulse Squared.png")
        self.assertEqual(squared["game_associations"], ["dynamix", "ravon"])
        self.assertEqual(squared["illustrators"], [])  # not the template's cover: no illustrator inferred
        # Excluded and pending candidates are never downloaded.
        downloaded = " ".join(self.client.downloads)
        for name in ("Emi-Animated.gif", "Universe_Song_cover.png", "Composer_Photo.jpg", "Lonely_BG-HQ.png"):
            self.assertNotIn(name, downloaded)
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))
        index = manifest.load_json(self.root / "data/discovery/song-index.wikis.json")
        cosmic = next(song for song in index["songs"] if song["title"] == "Cosmic Pulse")
        self.assertIn(mira["id"], cosmic["artwork_asset_ids"])

    def test_include_animated(self) -> None:
        self.assertEqual(self.run_cli("--include-animated", "--only", "File:Emi-Animated.gif"), 0)
        gif = self.asset("miraheze", "File:Emi-Animated.gif")
        self.assertEqual((gif["format"], gif["kind"]), ("GIF", "character"))

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
