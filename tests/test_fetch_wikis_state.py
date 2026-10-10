"""Offline tests of ``fetch_wikis.py`` acquisition state across runs (review round 2): Failure records that follow
upstream changes and outages, order-independent supersession, deferred withdrawals, the private work directory and
journal checks, and the internal-error exit code. ``python -I tests/test_fetch_wikis_state.py``.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_fetch_wikis import NOW, WikiRun  # noqa: E402  (also puts scripts/ and the fixtures on sys.path)

from fakewiki import image_bytes  # noqa: E402
from lib import net  # noqa: E402
from wikis import acquire, cli, config  # noqa: E402


class FailureLifecycleTests(WikiRun):
    """A Failure record never makes later runs crash: it follows the candidate's current revision."""

    def fail_package_vocal(self) -> None:
        client = self.scenario.client()
        del client.blobs[next(url for url in client.blobs if "Package_Vocal" in url and "wikitide" in url)]  # 404
        self.assertEqual(self.run_cli("--source", "miraheze", client=client), 1, "\n".join(self.logs))
        self.assertEqual(self.candidate("miraheze", "File:Package Vocal.jpg")["disposition"], "failed")
        self.assertEqual(len(self.manifest()["failures"]), 1)

    def assert_no_failures(self) -> None:
        self.assertEqual(self.manifest()["failures"], [])
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_failure_then_removal(self) -> None:
        self.fail_package_vocal()
        self.scenario.miraheze_names.remove("Package Vocal.jpg")
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 0, "\n".join(self.logs))
        self.assert_no_failures()
        self.assertEqual(self.run_cli("--source", "miraheze"), 0, "\n".join(self.logs))
        self.assert_no_failures()

    def test_failure_then_reupload(self) -> None:
        self.fail_package_vocal()
        self.scenario.data["Package Vocal.jpg"] = image_bytes("JPEG", (64, 36), 55)
        self.scenario.overrides[("miraheze", "Package Vocal.jpg")] = {"timestamp": "2026-09-01T00:00:00Z"}
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 0, "\n".join(self.logs))
        candidate = self.candidate("miraheze", "File:Package Vocal.jpg")
        self.assertEqual((candidate["disposition"], candidate["reason"].split(":")[0]), ("pending", acquire.AWAITING))
        self.assert_no_failures()
        client = self.scenario.client()
        self.assertEqual(self.run_cli("--source", "miraheze", client=client), 0, "\n".join(self.logs))
        self.assertEqual(self.candidate("miraheze", "File:Package Vocal.jpg")["disposition"], "downloaded")
        self.assert_no_failures()

    def test_failure_then_reclassification(self) -> None:
        self.fail_package_vocal()
        vocal = dict(self.scenario.miraheze_pages["Vocal"])
        vocal["wikitext"] = vocal["wikitext"].replace("File:Package Vocal.jpg|Package Vocal", "File:Package Vocal.jpg|")
        for pages in (self.scenario.miraheze_pages, self.scenario.fandom_pages):
            pages["Vocal"] = vocal
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 0, "\n".join(self.logs))
        self.assertEqual(self.candidate("miraheze", "File:Package Vocal.jpg")["reason"].split(":")[0],
                         "unresolved_role")
        self.assert_no_failures()

    def crash_after_failed_download(self) -> None:
        """An acquiring run journals a failed download of Package Vocal and dies before its final write (disk full,
        OOM, kill -9): the journal keeps [clear_failures, failure, candidate failed]."""
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 0, "\n".join(self.logs))
        client = self.scenario.client()
        del client.blobs[next(url for url in client.blobs if "Package_Vocal" in url and "wikitide" in url)]  # 404
        original, calls = cli.Runner.write, {"count": 0}

        def write(runner: cli.Runner) -> None:
            calls["count"] += 1
            if calls["count"] >= 2:  # the final write after the downloads
                raise OSError("simulated crash before the final write")
            original(runner)
        cli.Runner.write = write
        try:
            code = self.run_cli("--source", "miraheze", "--only", "File:Package Vocal.jpg", client=client)
        finally:
            cli.Runner.write = original
        self.assertEqual(code, 5)
        events = acquire.Journal(self.work / "journal.jsonl", self.root).read()
        self.assertEqual([event["event"] for event in events], ["clear_failures", "failure", "candidate"])
        self.assertEqual(self.manifest()["failures"], [])  # nothing reached the manifest

    def test_crash_then_reclassification(self) -> None:
        """The journaled outcome of a crashed run never overrides what the current complete evidence decides: the
        reclassified candidate keeps no Failure record and the journal is cleared (no run is stuck on reconcile)."""
        self.crash_after_failed_download()
        vocal = dict(self.scenario.miraheze_pages["Vocal"])
        vocal["wikitext"] = vocal["wikitext"].replace("File:Package Vocal.jpg|Package Vocal", "File:Package Vocal.jpg|")
        for pages in (self.scenario.miraheze_pages, self.scenario.fandom_pages):
            pages["Vocal"] = vocal
        for _ in range(2):
            self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 0, "\n".join(self.logs))
            candidate = self.candidate("miraheze", "File:Package Vocal.jpg")
            self.assertEqual(candidate["reason"].split(":")[0], "unresolved_role")
            self.assertFalse((self.work / "journal.jsonl").exists())
            self.assert_no_failures()

    def test_crash_then_same_classification(self) -> None:
        """Unchanged evidence: the replayed failure is kept (Failure record and failed candidate), exit 1."""
        self.crash_after_failed_download()
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 1, "\n".join(self.logs))
        candidate = self.candidate("miraheze", "File:Package Vocal.jpg")
        self.assertEqual((candidate["disposition"], candidate["reason"]), ("failed", "download http_4xx: HTTP 404"))
        self.assertEqual(len(self.manifest()["failures"]), 1)
        self.assertFalse((self.work / "journal.jsonl").exists())
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_japanese_list_run_keeps_an_interrupted_journal(self) -> None:
        """A run of a Japanese list alone neither replays nor clears an English-wiki journal (it cannot weigh it
        against the evidence and would leave the manifest out of step with the wiki snapshots); the next English-wiki
        run replays it."""
        self.crash_after_failed_download()
        journal = (self.work / "journal.jsonl").read_bytes()
        self.assertEqual(self.run_cli("--source", "atwiki-ja"), 0, "\n".join(self.logs))
        self.assertEqual((self.work / "journal.jsonl").read_bytes(), journal)
        self.assertEqual(self.manifest()["failures"], [])
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 1, "\n".join(self.logs))
        self.assertEqual(self.candidate("miraheze", "File:Package Vocal.jpg")["disposition"], "failed")
        self.assertFalse((self.work / "journal.jsonl").exists())
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_failure_survives_a_partial_allimages_run(self) -> None:
        """An incomplete snapshot leaves the failed candidate out; the next complete run rebuilds the failed
        disposition from the Failure record itself (the upload predates the attempt) instead of crashing."""
        self.fail_package_vocal()
        wikis = self.scenario.wikis()
        wikis[config.MIRAHEZE.api_url].fail[("allimages", 2)] = net.HttpError("HTTP 503", url="x", http_status=503)
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only", client=self.scenario.client(wikis)),
                         2, "\n".join(self.logs))
        self.assertNotIn("wikis:miraheze:File:Package_Vocal.jpg",
                         {c["id"] for c in self.snapshot("miraheze")["candidates"]})
        self.assertEqual(len(self.manifest()["failures"]), 1)  # kept: the incomplete discovery proves nothing
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))
        for _ in range(2):
            client = self.scenario.client()
            del client.blobs[next(url for url in client.blobs if "Package_Vocal" in url and "wikitide" in url)]
            self.assertEqual(self.run_cli("--source", "miraheze", client=client), 1, "\n".join(self.logs))
            candidate = self.candidate("miraheze", "File:Package Vocal.jpg")
            self.assertEqual((candidate["disposition"], candidate["reason"]), ("failed", "download http_4xx: HTTP 404"))
            self.assertFalse(any("Package_Vocal" in url for url in client.downloads))  # a 404 is not retried
            self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_missing_url_is_retried_once_reported(self) -> None:
        wikis = self.scenario.wikis()
        next(row for row in wikis[config.MIRAHEZE.api_url].files if row["name"] == "TwinDynamix.png")["url"] = None
        self.assertEqual(self.run_cli("--source", "miraheze", client=self.scenario.client(wikis)), 1)
        [failure] = self.manifest()["failures"]
        self.assertTrue(failure["retriable"])
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 0, "\n".join(self.logs))
        self.assertEqual(self.candidate("miraheze", "File:TwinDynamix.png")["reason"].split(":")[0], acquire.AWAITING)
        client = self.scenario.client()
        self.assertEqual(self.run_cli("--source", "miraheze", client=client), 0, "\n".join(self.logs))
        self.assertTrue(any("TwinDynamix" in url for url in client.downloads))
        self.assertEqual(self.candidate("miraheze", "File:TwinDynamix.png")["disposition"], "downloaded")
        self.assert_no_failures()

    def test_unexpected_error_has_its_own_exit_code(self) -> None:
        class Broken:
            def __enter__(self):
                raise RuntimeError("boom")

            def __exit__(self, *args):
                return None
        code = cli.main(["--root", str(self.root), "--work-dir", str(self.work), "--metadata-only"],
                        client_factory=Broken, now=lambda: NOW, log=self.logs.append)
        self.assertEqual(code, 5)


class ArchiveStateTests(WikiRun):
    def test_duplicate_sorting_first_takes_over_in_one_run(self) -> None:
        """'Cosmic Pulse copy.png' sorts before CosmicPulseDynamix.png, whose record it points at; the owner is
        re-uploaded with other bytes: the copy title's current bytes keep a current record after one run."""
        self.assertEqual(self.run_cli("--source", "miraheze"), 0, "\n".join(self.logs))
        owner = self.asset("miraheze", "File:CosmicPulseDynamix.png")
        copy = self.candidate("miraheze", "File:Cosmic Pulse copy.png")
        self.assertEqual((copy["disposition"], copy["asset_id"]), ("duplicate_bytes", owner["id"]))
        self.scenario.data["CosmicPulseDynamix.png"] = image_bytes("PNG", (64, 36), 77)
        self.scenario.overrides[("miraheze", "CosmicPulseDynamix.png")] = {"timestamp": "2026-09-01T00:00:00Z"}
        self.assertEqual(self.run_cli("--source", "miraheze"), 0, "\n".join(self.logs))
        copy = self.candidate("miraheze", "File:Cosmic Pulse copy.png")
        record = {a["id"]: a for a in self.manifest()["assets"]}[owner["id"]]
        self.assertEqual((copy["disposition"], copy["asset_id"]), ("downloaded", owner["id"]))
        self.assertEqual((record["upstream_status"], record["candidate_id"], record.get("superseded_by")),
                         ("current", copy["id"], None))
        self.assertNotEqual(self.asset("miraheze", "File:CosmicPulseDynamix.png")["id"], owner["id"])
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_metadata_only_defers_a_reviewed_withdrawal(self) -> None:
        self.assertEqual(self.run_cli("--source", "miraheze"), 0)
        event = self.asset("miraheze", "File:Unused Event.png")
        table = self.scenario.reviews_json()
        entry = next(item for item in table["reviews"] if item["title"] == "File:Unused Event.png")
        entry.update(role="fanart", evidence="visual_inspection: fan-made event poster")
        for key in ("game", "game_evidence", "songs"):
            entry.pop(key)
        table["withdrawals"] = [{"title": "File:Unused Event.png", "sha256": [event["sha256"]], "reason": "fanart",
                                 "evidence": "visual_inspection: fan-made event poster"}]
        self.reviews_path.write_text(json.dumps(table), encoding="utf-8")
        record_before = json.dumps(event, sort_keys=True)
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 0, "\n".join(self.logs))
        self.assertTrue((self.root / event["path"]).is_file())
        kept = {a["id"]: a for a in self.manifest()["assets"]}[event["id"]]
        self.assertEqual(json.dumps(kept, sort_keys=True), record_before)  # the displayed corpus is unchanged
        candidate = self.candidate("miraheze", "File:Unused Event.png")
        self.assertEqual((candidate["disposition"], candidate["asset_id"], candidate["role"]),
                         ("downloaded", event["id"], "promotional"))
        self.assertIn(acquire.WITHDRAWAL_PENDING_NOTE, candidate["notes"])
        self.assertTrue(any(line.startswith("WITHDRAWAL PENDING") for line in self.logs))
        self.assertFalse(any(line.startswith("WITHDRAWN") for line in self.logs))
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))
        self.assertEqual(self.run_cli("--source", "miraheze"), 0, "\n".join(self.logs))  # an acquiring run does it
        self.assertFalse((self.root / event["path"]).exists())
        self.assertNotIn(event["id"], {a["id"] for a in self.manifest()["assets"]})
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))


class SourceNoteTests(WikiRun):
    def test_pending_note_describes_the_pending_set(self) -> None:
        def note() -> str:
            return next(s for s in self.manifest()["sources"] if s["id"] == "wikis:miraheze")["notes"]
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 0, "\n".join(self.logs))
        self.assertRegex(note(), r"Pending: \d+ accepted files await download \(metadata-only run\); \d+ unresolved")
        self.assertEqual(self.run_cli("--source", "miraheze"), 0, "\n".join(self.logs))
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 0, "\n".join(self.logs))
        self.assertNotIn("await download", note())  # everything accepted is archived
        self.assertRegex(note(), r"Pending: \d+ unresolved \(role or game not established\)\.$")


class WorkDirTests(WikiRun):
    def default_run(self, *args: str, client=None) -> int:
        client = client or self.scenario.client()
        return cli.main(["--root", str(self.root), *args], client_factory=lambda: client, now=lambda: NOW,
                        log=self.logs.append)

    def test_default_work_dir_is_private_and_inside_the_root(self) -> None:
        work = acquire.default_work_dir(self.root)
        self.assertEqual(work, self.root.resolve() / ".cache/fetch_wikis/work")
        self.assertEqual(self.default_run("--metadata-only", "--source", "miraheze"), 0, "\n".join(self.logs))
        self.assertEqual(stat.S_IMODE(os.lstat(work).st_mode), 0o700)

    def test_lock_symlink_is_refused(self) -> None:
        work = acquire.default_work_dir(self.root)
        work.mkdir(parents=True, mode=0o700)
        decoy = self.tmp / "decoy.txt"
        decoy.write_text("precious\n" * 50, encoding="utf-8")
        (work / "lock").symlink_to(decoy)
        self.assertEqual(self.default_run("--metadata-only", "--source", "miraheze"), 3)
        self.assertEqual(decoy.read_text(encoding="utf-8"), "precious\n" * 50)
        self.assertFalse((self.root / "data/sources/wikis.json").exists())

    def test_symlinked_or_world_writable_work_dir_is_refused(self) -> None:
        target = self.tmp / "elsewhere"
        target.mkdir()
        link = self.tmp / "link-work"
        link.symlink_to(target, target_is_directory=True)
        code = cli.main(["--root", str(self.root), "--work-dir", str(link), "--metadata-only"],
                        client_factory=self.scenario.client, now=lambda: NOW, log=self.logs.append)
        self.assertEqual(code, 3)
        open_dir = self.tmp / "open-work"
        open_dir.mkdir()
        open_dir.chmod(0o777)
        code = cli.main(["--root", str(self.root), "--work-dir", str(open_dir), "--metadata-only"],
                        client_factory=self.scenario.client, now=lambda: NOW, log=self.logs.append)
        self.assertEqual(code, 3)

    def test_journal_of_another_root_is_refused_and_kept(self) -> None:
        other_root = self.tmp / "other-repo"
        other_root.mkdir()
        journal = acquire.Journal(self.work / "journal.jsonl", other_root)
        journal.append({"event": "clear_failures", "id": "wikis:miraheze:File:Nothing.png"})
        before = journal.path.read_bytes()
        self.assertEqual(self.run_cli("--metadata-only"), 3)
        self.assertEqual(journal.path.read_bytes(), before)
        self.assertFalse((self.root / "data/sources/wikis.json").exists())
        self.assertFalse(acquire.Journal(journal.path, self.root).belongs_to_root())

    def test_planted_withdrawal_is_refused(self) -> None:
        self.assertEqual(self.run_cli("--source", "miraheze"), 0, "\n".join(self.logs))
        asset = self.asset("miraheze", "File:(XD)Dynamix.png")
        other = self.asset("miraheze", "File:Emi-Raw.png")
        journal = acquire.Journal(self.work / "journal.jsonl", self.root)
        journal.append({"event": "withdraw", "id": asset["id"], "path": other["path"], "reason": "fanart"})
        self.assertEqual(self.run_cli("--source", "miraheze"), 3)
        self.assertTrue((self.root / other["path"]).is_file())
        self.assertTrue((self.root / asset["path"]).is_file())

    def withdraw_unused_event(self) -> dict:
        """An acquiring run that carries out a reviewed withdrawal of File:Unused Event.png; its asset record."""
        self.assertEqual(self.run_cli("--source", "miraheze"), 0, "\n".join(self.logs))
        event = self.asset("miraheze", "File:Unused Event.png")
        table = self.scenario.reviews_json()
        entry = next(item for item in table["reviews"] if item["title"] == "File:Unused Event.png")
        entry.update(role="fanart", evidence="visual_inspection: fan-made event poster")
        for key in ("game", "game_evidence", "songs"):
            entry.pop(key)
        table["withdrawals"] = [{"title": "File:Unused Event.png", "sha256": [event["sha256"]], "reason": "fanart",
                                 "evidence": "visual_inspection: fan-made event poster"}]
        self.reviews_path.write_text(json.dumps(table), encoding="utf-8")
        self.assertEqual(self.run_cli("--source", "miraheze"), 0, "\n".join(self.logs))
        self.assertNotIn(event["id"], {a["id"] for a in self.manifest()["assets"]})
        return event

    def test_withdrawal_written_before_a_crash_is_not_replayed(self) -> None:
        # The run died after writing its outputs and before clearing the journal: the withdrawal is already done.
        event = self.withdraw_unused_event()
        journal = acquire.Journal(self.work / "journal.jsonl", self.root)
        journal.append({"event": "withdraw", "id": event["id"], "path": event["path"], "reason": "fanart"})
        before = (self.root / "data/sources/wikis.json").read_bytes()
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 0, "\n".join(self.logs))
        self.assertEqual((self.root / "data/sources/wikis.json").read_bytes(), before)
        self.assertFalse(journal.path.exists())
        self.assertEqual(self.verify(), 0, "\n".join(self.logs))

    def test_withdrawal_of_a_missing_record_whose_file_exists_is_refused(self) -> None:
        event = self.withdraw_unused_event()
        (self.root / event["path"]).write_bytes(b"restored by hand")  # the journal does not describe this root
        journal = acquire.Journal(self.work / "journal.jsonl", self.root)
        journal.append({"event": "withdraw", "id": event["id"], "path": event["path"], "reason": "fanart"})
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 3)
        self.assertTrue((self.root / event["path"]).is_file())  # nothing is deleted on a refused replay
        self.assertTrue(journal.path.exists())

    def test_corrupt_journal_lines(self) -> None:
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 0, "\n".join(self.logs))
        journal = acquire.Journal(self.work / "journal.jsonl", self.root)
        event = {"event": "clear_failures", "id": "wikis:miraheze:File:Nothing.png"}
        journal.append(event)
        with open(journal.path, "a", encoding="utf-8") as stream:
            stream.write('{"event": "clear_fail')  # a torn last line: ignored, and cut before the next append
        self.assertEqual(journal.read(), [event])
        journal.append(event)
        self.assertEqual(journal.read(), [event, event])
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 0, "\n".join(self.logs))
        journal.append(event)
        with open(journal.path, "a", encoding="utf-8") as stream:
            stream.write("not json\n" + json.dumps(event) + "\n")  # a corrupt line before the last
        self.assertEqual(self.run_cli("--source", "miraheze", "--metadata-only"), 3)
        with self.assertRaises(acquire.UnsafeWorkDir):
            journal.read()


if __name__ == "__main__":
    unittest.main()
