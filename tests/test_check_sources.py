"""Offline tests for scripts/check_sources.py with recorded MediaWiki allimages responses (no network access).

The saved snapshots are the synthetic contract fixture (tests/fixtures/contract/data/discovery), read in place or from
a temporary copy; the live side is a fake client replaying recorded ``list=allimages`` pages.
"""

import copy
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_sources  # noqa: E402
from lib import net  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/contract"
MIRAHEZE = check_sources.MEDIAWIKI_SOURCES["wikis:miraheze"]
FANDOM = check_sources.MEDIAWIKI_SOURCES["wikis:fandom"]
HOSTILE_TITLE = "File:Evil | `tick` <script>alert(1)</script> @here [x](https://evil.example)\n\u202eesrever.png"


def image(title, sha1=None, size=None, width=None, height=None, mime="image/png", timestamp="2025-01-14T00:00:00Z",
          host="dynamix.miraheze.org"):
    page = title.replace(" ", "_")
    return {"name": page[len("File:"):], "title": title, "timestamp": timestamp, "url": f"https://static.example/{page}",
            "descriptionurl": f"https://{host}/wiki/{page}", "size": size, "width": width, "height": height,
            "sha1": sha1, "mime": mime, "ns": 6}


# Recorded replies for the Miraheze fixture snapshot, keyed by the aicontinue token sent (None = first request).
# Versus data/discovery/miraheze.json: Cosmic Pulse (crop) was re-uploaded (SHA-1/size/timestamp differ), "New cover"
# is new, Cosmic_Pulse_UV_cover.png and Song_select_screen.png are gone; the rest match after normalization
# (timestamp offset form, image/jpg alias, upper-case SHA-1) or have no saved metadata to compare.
PAGE_1 = {"batchcomplete": True, "continue": {"aicontinue": "Cosmic_Pulse_copy.png", "continue": "-||"},
          "query": {"allimages": [
              image("File:Character idle.gif", "1" * 40, 9000, 250, 250, "image/gif"),
              image("File:Cosmic Pulse.png", "155747e7f866176e7b567eaf88e3c9983fe18f9a", 943, 600, 600),
              image("File:Cosmic Pulse (crop).png", "a" * 40, 999, 300, 300, timestamp="2026-09-01T10:00:00Z"),
          ]}}
PAGE_2 = {"batchcomplete": True, "query": {"allimages": [
    image("File:Cosmic Pulse copy.png", "2" * 40, 943, 600, 600),
    image("File:Dynamix background 03.png", "264AE36942E7D1553660F525F2F9A7978E8856E9", 525, 640, 360,
          timestamp="2025-01-14T00:00:00+00:00"),
    {**image(HOSTILE_TITLE, "3" * 40, 77, 10, 10), "descriptionurl": "https://evil.example/wiki/x"},
    image("File:New cover.png", "4" * 40, 4096, 1920, 1080, timestamp="2026-10-01T00:00:00Z"),
    image("File:交差して歪む過去と未来.jpg", "e02570b86336577ba18e514063a95f40e86a2135", 3395, 320, 180, "image/jpg"),
]}}
RECORDED = {None: PAGE_1, "Cosmic_Pulse_copy.png": PAGE_2}


class FakeClient:
    """Replays recorded pages by ``aicontinue``; a recorded exception is raised instead of returned."""

    def __init__(self, pages):
        self.pages, self.calls, self.hosts, self.closed = pages, [], None, False

    def __call__(self, hosts):
        self.hosts = list(hosts)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.closed = True
        return False

    def get_json(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        reply = self.pages[(params or {}).get("aicontinue")]
        if isinstance(reply, BaseException):
            raise reply
        return copy.deepcopy(reply), url


def tree_digest(root: Path) -> dict:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def inside_code_span(line: str, start: int, length: int) -> bool:
    """Whether ``line[start:start + length]`` lies inside one CommonMark code span (fence = equal backtick runs)."""
    runs = [(match.start(), match.end()) for match in re.finditer("`+", line)]
    index = 0
    while index < len(runs):
        open_start, open_end = runs[index]
        width = open_end - open_start
        closing = next((j for j in range(index + 1, len(runs)) if runs[j][1] - runs[j][0] == width), None)
        if closing is None:
            index += 1
            continue
        if open_end <= start and start + length <= runs[closing][0]:
            return True
        index = closing + 1
    return False


def entry_for(report, source_id):
    return next(entry for entry in report["sources"] if entry["source_id"] == source_id)


def keys(rows):
    return [row["key"] for row in rows]


ALIAS_KEY = "File:Cosmic_Pulse_alias.png"


def fixture_with_alias(base: Path) -> Path:
    """A copy of the fixture whose Miraheze snapshot also holds a file redirect excluded as redirect_or_alias."""
    root = base / "checkout"
    shutil.copytree(FIXTURE, root)
    path = root / "data/discovery/miraheze.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    template = next(c for c in snapshot["candidates"] if c["disposition"] == "excluded")
    alias = {**template, "id": "wikis:miraheze:" + ALIAS_KEY, "upstream_key": ALIAS_KEY,
             "page_url": "https://dynamix.miraheze.org/wiki/" + ALIAS_KEY, "title": "Cosmic_Pulse_alias",
             "role": None, "game_evidence": [], "reason": "redirect_or_alias: redirect to File:Cosmic_Pulse.png"}
    snapshot["candidates"] = sorted(snapshot["candidates"] + [alias], key=lambda c: c["id"])
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return root


class CheckTestCase(unittest.TestCase):
    def check(self, pages, sources=("wikis:miraheze",), root=FIXTURE, **kwargs):
        client = FakeClient(pages)
        report = check_sources.run(root, sources=sources, client_factory=client, now="2026-10-10T00:00:00Z", **kwargs)
        return report, client


class CompleteListingTests(CheckTestCase):
    def test_complete_listing_reports_added_changed_and_removed(self):
        report, client = self.check(RECORDED)
        entry = entry_for(report, "wikis:miraheze")
        self.assertEqual(entry["status"], "compared")
        self.assertTrue(entry["live"]["complete"])
        self.assertIsNone(entry["live"]["continuation"])
        self.assertEqual(entry["counts"], {
            "saved_candidates": 8, "saved_files": 8, "not_comparable": 0, "live_files": 8, "added": 2, "changed": 1,
            "removed": 2, "not_seen": 0, "aliases_not_listed": 0, "unchanged": 5, "saved_without_metadata": 2})
        self.assertEqual([row["key"] for row in entry["removed"]],
                         ["File:Cosmic_Pulse_UV_cover.png", "File:Song_select_screen.png"])
        self.assertEqual({row["key"] for row in entry["added"]},
                         {"File:New_cover.png", HOSTILE_TITLE.replace(" ", "_")})
        changed = entry["changed"][0]
        self.assertEqual(changed["key"], "File:Cosmic_Pulse_(crop).png")
        self.assertEqual(set(changed["changes"]), {"sha1", "bytes", "timestamp"})
        self.assertEqual(changed["changes"]["bytes"], [353, 999])
        self.assertEqual(changed["disposition"], "downloaded")
        self.assertTrue(report["complete"])
        self.assertTrue(report["differences"])
        self.assertEqual(report["not_seen"], 0)

    def test_redirects_and_aliases_are_never_removals(self):
        """allimages lists only real files: a saved redirect missing from it is expected, not removed."""
        with tempfile.TemporaryDirectory() as temporary:
            root = fixture_with_alias(Path(temporary))
            report, _ = self.check(RECORDED, root=root)
            entry = entry_for(report, "wikis:miraheze")
            self.assertEqual(entry["status"], "compared")
            self.assertEqual(keys(entry["removed"]), ["File:Cosmic_Pulse_UV_cover.png", "File:Song_select_screen.png"])
            self.assertEqual(keys(entry["aliases_not_listed"]), [ALIAS_KEY])
            self.assertEqual(entry["counts"]["aliases_not_listed"], 1)
            self.assertEqual(report["totals"]["aliases_not_listed"], 1)
            self.assertIn("#### Redirects and aliases not listed (expected; not a removal) (1)",
                          check_sources.render(report))
            pages = {**RECORDED, "Cosmic_Pulse_copy.png": net.TimeoutFetchError("timed out", url=MIRAHEZE.api)}
            entry = entry_for(self.check(pages, root=root)[0], "wikis:miraheze")
            self.assertEqual(keys(entry["aliases_not_listed"]), [ALIAS_KEY])
            self.assertNotIn(ALIAS_KEY, keys(entry["not_seen"]))
        self.assertTrue(check_sources.listed_by_allimages({"disposition": "excluded", "reason": "universe_only: x"}))
        self.assertTrue(check_sources.listed_by_allimages({"disposition": "pending", "reason": "redirect_or_alias"}))
        self.assertFalse(check_sources.listed_by_allimages({"disposition": "excluded", "reason": "redirect_or_alias"}))

    def test_requests_are_metadata_only_and_follow_continuation(self):
        _, client = self.check(RECORDED)
        self.assertEqual(client.hosts, ["dynamix.miraheze.org"])
        self.assertTrue(client.closed)
        self.assertEqual(len(client.calls), 2)
        for url, params in client.calls:
            self.assertEqual(url, MIRAHEZE.api)
            self.assertEqual(params["list"], "allimages")
            self.assertNotIn("prop", params)
        self.assertNotIn("aicontinue", client.calls[0][1])
        self.assertEqual(client.calls[1][1]["aicontinue"], "Cosmic_Pulse_copy.png")
        self.assertEqual(client.calls[1][1]["continue"], "-||")

    def test_equivalent_values_are_not_changes(self):
        self.assertEqual(check_sources.differences(
            {"sha1": "ABC", "mime": "image/jpg", "timestamp": "2025-01-14T00:00:00Z", "bytes": 5},
            {"sha1": "abc", "mime": "image/jpeg", "timestamp": "2025-01-14T00:00:00+00:00", "bytes": 5}), {})

    def test_values_the_snapshot_did_not_record_are_not_changes(self):
        self.assertEqual(check_sources.differences({"sha1": None, "bytes": None}, {"sha1": "f" * 40, "bytes": 1}), {})
        report, _ = self.check(RECORDED)
        changed = {row["key"] for row in entry_for(report, "wikis:miraheze")["changed"]}
        self.assertNotIn("File:Character_idle.gif", changed)
        self.assertNotIn("File:Cosmic_Pulse_copy.png", changed)

    def test_same_bytes_different_size_metadata_is_a_change(self):
        pages = copy.deepcopy(RECORDED)
        pages[None]["query"]["allimages"][1]["size"] = 944
        report, _ = self.check(pages)
        changed = {row["key"]: row["changes"] for row in entry_for(report, "wikis:miraheze")["changed"]}
        self.assertEqual(changed["File:Cosmic_Pulse.png"], {"bytes": [943, 944]})


class IncompleteListingTests(CheckTestCase):
    def assertNoRemovalConcluded(self, entry, not_seen):
        self.assertEqual(entry["status"], "incomplete")
        self.assertFalse(entry["live"]["complete"])
        self.assertEqual(entry["removed"], [])
        self.assertEqual(entry["counts"]["removed"], 0)
        self.assertEqual(entry["counts"]["not_seen"], not_seen)
        self.assertEqual(len(entry["not_seen"]), not_seen)

    def test_transient_error_on_a_later_page_is_not_a_removal(self):
        pages = {**RECORDED, "Cosmic_Pulse_copy.png": net.TimeoutFetchError("read timed out", url=MIRAHEZE.api)}
        report, _ = self.check(pages)
        entry = entry_for(report, "wikis:miraheze")
        self.assertNoRemovalConcluded(entry, 5)
        self.assertEqual(entry["live"]["continuation"], {"aicontinue": "Cosmic_Pulse_copy.png", "continue": "-||"})
        self.assertEqual(entry["live"]["errors"][0]["error_class"], "timeout")
        self.assertFalse(report["complete"])

    def test_blocked_first_page_reports_nothing_as_removed(self):
        report, _ = self.check({None: net.ChallengeError("challenge page", url=MIRAHEZE.api, http_status=403)})
        entry = entry_for(report, "wikis:miraheze")
        self.assertNoRemovalConcluded(entry, 8)
        self.assertEqual(entry["live"]["pages"], 0)
        self.assertEqual(entry["live"]["errors"][0]["error_class"], "challenge")
        self.assertFalse(report["complete"])
        self.assertFalse(report["differences"], "files that were not seen are not upstream differences")
        self.assertEqual(report["not_seen"], 8)

    def test_unreadable_entry_makes_the_listing_incomplete(self):
        """An entry without a usable title hides one file: no saved candidate may then be reported removed."""
        for label, bad in (("no title or name", lambda e: {k: v for k, v in e.items() if k not in ("title", "name")}),
                           ("not an object", lambda e: "garbage"),
                           ("title outside the File namespace", lambda e: {**e, "title": "Dynamix", "name": ""})):
            with self.subTest(entry=label):
                pages = copy.deepcopy(RECORDED)
                images = pages["Cosmic_Pulse_copy.png"]["query"]["allimages"]
                index = next(i for i, e in enumerate(images) if e["title"] == "File:Dynamix background 03.png")
                images[index] = bad(images[index])
                report, client = self.check(pages)
                entry = entry_for(report, "wikis:miraheze")
                self.assertNoRemovalConcluded(entry, 3)
                self.assertEqual(keys(entry["not_seen"]), ["File:Cosmic_Pulse_UV_cover.png",
                                                           "File:Dynamix_background_03.png",
                                                           "File:Song_select_screen.png"])
                self.assertEqual(len(client.calls), 2, "the rest of the listing is still read")
                self.assertIsNone(entry["live"]["continuation"])
                self.assertIn("page 2: 1 allimages entry has no usable title or name",
                              entry["live"]["errors"][0]["message"])
                self.assertEqual(entry["counts"]["added"], 2)  # what was read is still compared
                self.assertEqual(entry["counts"]["changed"], 1)
                self.assertFalse(report["complete"])
                self.assertTrue(report["differences"])

    def test_server_error_and_api_error_and_malformed_replies(self):
        for reply in (net.HttpError("HTTP 503", url=MIRAHEZE.api, http_status=503),
                      net.FetchError("response is not valid JSON", url=MIRAHEZE.api, http_status=200),
                      ValueError("URL contains a forbidden character"),
                      {"error": {"code": "ratelimited", "info": "slow down <b>now</b>"}},
                      {"query": {"pages": []}}, ["not", "an", "object"],
                      {**PAGE_2, "continue": "not-an-object"}):
            with self.subTest(reply=str(reply)[:40]):
                report, _ = self.check({**RECORDED, "Cosmic_Pulse_copy.png": reply})
                entry = entry_for(report, "wikis:miraheze")
                self.assertEqual(entry["status"], "incomplete")
                self.assertEqual(entry["removed"], [])
                self.assertTrue(entry["live"]["errors"])

    def test_page_limit_keeps_the_continuation(self):
        report, client = self.check(RECORDED, max_pages=1)
        entry = entry_for(report, "wikis:miraheze")
        self.assertNoRemovalConcluded(entry, 5)
        self.assertEqual(len(client.calls), 1)
        self.assertIn("--max-pages 1", entry["live"]["errors"][0]["message"])
        self.assertEqual(entry["live"]["continuation"]["aicontinue"], "Cosmic_Pulse_copy.png")

    def test_continuation_that_does_not_advance_stops(self):
        looping = {**PAGE_2, "continue": {"aicontinue": "Cosmic_Pulse_copy.png", "continue": "-||"}}
        report, client = self.check({**RECORDED, "Cosmic_Pulse_copy.png": looping})
        entry = entry_for(report, "wikis:miraheze")
        self.assertEqual(entry["status"], "incomplete")
        self.assertIn("did not advance", entry["live"]["errors"][0]["message"])
        self.assertEqual(len(client.calls), 2)


class BaselineTests(CheckTestCase):
    def test_incomplete_saved_snapshot_and_endpoint_mismatch_are_flagged(self):
        pages = {None: {"query": {"allimages": [
            image("File:Cosmic Pulse.png", "286d2f023dc45e14248aaf16401cf216f6514949", 1080, 600, 600,
                  host="dynamixc4cat.fandom.com"),
            image("File:Zeta file.png", "5" * 40, 10, 1, 1, host="dynamixc4cat.fandom.com")]}}}
        report, client = self.check(pages, sources=("wikis:fandom",))
        entry = entry_for(report, "wikis:fandom")
        self.assertEqual(client.hosts, ["dynamixc4cat.fandom.com"])
        self.assertFalse(entry["baseline"]["discovery_complete"])
        self.assertTrue(any("saved snapshot is incomplete" in note for note in entry["notes"]))
        self.assertTrue(any("differs from the checked endpoint" in note for note in entry["notes"]))
        self.assertEqual(entry["counts"]["added"], 1)
        self.assertEqual(entry["counts"]["removed"], 2)  # the live listing itself was complete

    def test_other_snapshots_are_listed_but_not_checked_live(self):
        report, _ = self.check(RECORDED)
        statuses = {entry["source_id"]: entry["status"] for entry in report["sources"]}
        self.assertEqual(statuses, {"wikis:miraheze": "compared", "wikis:fandom": "not_checked",
                                    "official:facebook": "not_checked", "archives:hkstp": "not_checked",
                                    "artists:x:ariiol_": "not_checked"})
        facebook = entry_for(report, "official:facebook")
        self.assertIn("no read-only live adapter", facebook["reason"])
        self.assertEqual(facebook["baseline"]["dispositions"]["blocked"], 1)
        self.assertIn("not selected", entry_for(report, "wikis:fandom")["reason"])

    def test_missing_or_invalid_snapshot_is_reported_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            shutil.copytree(FIXTURE, root)
            (root / "data/discovery/miraheze.json").unlink()
            (root / "data/discovery/song-index.wikis.json").write_text('{"schema_version": 1, "songs": []}\n',
                                                                         encoding="utf-8")
            report, client = self.check(RECORDED, root=root)
            entry = entry_for(report, "wikis:miraheze")
            self.assertEqual(entry["status"], "baseline_error")
            self.assertIn("no saved snapshot", entry["reason"])
            self.assertEqual(client.calls, [])
            self.assertFalse(report["complete"])
            self.assertNotIn(None, [e["source_id"] for e in report["sources"]])  # song index skipped quietly
            broken = json.loads((FIXTURE / "data/discovery/fandom.json").read_text(encoding="utf-8"))
            broken["candidates"][0]["disposition"] = "vanished"
            (root / "data/discovery/fandom.json").write_text(json.dumps(broken), encoding="utf-8")
            report, client = self.check(RECORDED, sources=("wikis:fandom",), root=root)
            self.assertEqual(entry_for(report, "wikis:fandom")["status"], "baseline_error")
            self.assertEqual(client.calls, [])


class ReadOnlyTests(unittest.TestCase):
    def run_main(self, *argv, client=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = check_sources.main(list(argv), client_factory=client or FakeClient(RECORDED))
        return status, out.getvalue(), err.getvalue()

    def test_checkout_is_never_modified_and_reports_go_elsewhere(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "checkout"
            shutil.copytree(FIXTURE, root)
            before = tree_digest(root)
            status, out, _ = self.run_main("--root", str(root), "--report", str(base / "out/report.md"),
                                           "--summary", str(base / "out/summary.json"), "--source", "wikis:miraheze")
            self.assertEqual(status, 0)
            self.assertEqual(tree_digest(root), before)
            summary = json.loads((base / "out/summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["read_only"])
            self.assertEqual(summary["totals"]["removed"], 2)
            self.assertEqual(json.loads(out)["totals"], summary["totals"])
            self.assertIn("## Source metadata check", (base / "out/report.md").read_text(encoding="utf-8"))
            for inside in (root / "report.md", root / "data/discovery/report.md"):
                status, _, err = self.run_main("--root", str(root), "--report", str(inside))
                self.assertEqual(status, 1)
                self.assertIn("inside the checkout", err)
                self.assertFalse(inside.exists())
            self.assertEqual(tree_digest(root), before)

    def test_incomplete_check_writes_the_report_and_exits_3(self):
        blocked = FakeClient({None: net.BlockedError("HTTP 403", url=MIRAHEZE.api, http_status=403)})
        with tempfile.TemporaryDirectory() as temporary:
            report, summary = Path(temporary) / "r.md", Path(temporary) / "s.json"
            status, out, err = self.run_main("--root", str(FIXTURE), "--report", str(report), "--summary",
                                             str(summary), client=blocked)
            self.assertEqual(status, check_sources.INCOMPLETE_STATUS)
            self.assertEqual(status, 3)
            self.assertIn("a live check was incomplete", err)
            self.assertEqual(json.loads(out), {"complete": False, "differences": False, "not_seen": 11,
                                               "totals": json.loads(summary.read_text(encoding="utf-8"))["totals"]})
            text = report.read_text(encoding="utf-8")
            self.assertIn("Live checks complete: `False` (**incomplete**", text)
            self.assertIn("Upstream differences (added, changed or removed): `False`", text)
        with tempfile.TemporaryDirectory() as temporary:
            status, out, err = self.run_main("--root", str(FIXTURE), "--report", str(Path(temporary) / "r.md"))
            self.assertEqual((status, err), (0, ""))
            self.assertTrue(json.loads(out)["complete"])

    def test_script_runs_isolated_as_ci_invokes_it(self):
        result = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/check_sources.py"), "--help"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--report", result.stdout)

    def test_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = str(Path(temporary) / "r.md")
            self.assertEqual(self.run_main("--root", str(FIXTURE), "--report", report, "--max-pages", "0")[0], 1)
            self.assertEqual(self.run_main("--root", str(FIXTURE), "--report", report, "--summary", report)[0], 1)
            with self.assertRaises(SystemExit):
                self.run_main("--root", str(FIXTURE), "--report", report, "--source", "official:facebook")


class RenderingTests(CheckTestCase):
    def test_untrusted_strings_cannot_escape_their_cells(self):
        report, _ = self.check(RECORDED)
        markdown = check_sources.render(report)
        rows = [line for line in markdown.splitlines() if line.startswith("| ") and "evil" in line.lower()]
        self.assertEqual(len(rows), 1, "the hostile title must stay on one table row")
        row = rows[0]
        self.assertEqual(row.replace("\\|", "").count("|"), 6, "5 columns: a pipe in a title must be escaped")
        self.assertNotIn("](<https://evil.example", markdown)
        self.assertNotIn("\u202e", markdown)
        for fragment in ("<script>", "@here", "[x](https://evil.example)"):
            for line in markdown.splitlines():
                for start in (i for i in range(len(line)) if line.startswith(fragment, i)):
                    self.assertTrue(inside_code_span(line, start, len(fragment)),
                                    f"{fragment!r} must be inside a code span: {line}")
        self.assertIn("](<https://dynamix.miraheze.org/wiki/File:New_cover.png>)", markdown)

    def test_code_span_fences(self):
        self.assertEqual(check_sources.code("plain"), "`plain`")
        self.assertEqual(check_sources.code("a`b"), "``a`b``")
        self.assertEqual(check_sources.code("`x"), "`` `x ``")
        self.assertEqual(check_sources.code("a|b"), "`a\\|b`")
        self.assertEqual(check_sources.code(" \n "), "?")

    def test_links_only_to_https_pages_on_the_source_host(self):
        host = "dynamix.miraheze.org"
        self.assertEqual(check_sources.safe_link("https://dynamix.miraheze.org/wiki/File:A B.png", host),
                         "https://dynamix.miraheze.org/wiki/File:A%20B.png")
        for url in ("http://dynamix.miraheze.org/wiki/x", "https://evil.example/wiki/x",
                    "https://user@dynamix.miraheze.org/x", "https://dynamix.miraheze.org:8443/x",
                    "javascript:alert(1)", "https://dynamix.miraheze.org.evil.example/x", None, 5):
            with self.subTest(url=url):
                self.assertIsNone(check_sources.safe_link(url, host))

    def test_incomplete_report_says_not_seen_instead_of_removed(self):
        pages = {**RECORDED, "Cosmic_Pulse_copy.png": net.TimeoutFetchError("read timed out", url=MIRAHEZE.api)}
        report, _ = self.check(pages)
        markdown = check_sources.render(report)
        self.assertIn("#### Not seen (live listing incomplete; not a removal) (5)", markdown)
        self.assertNotIn("#### Removed", markdown)
        self.assertIn("complete `False`", markdown)


if __name__ == "__main__":
    unittest.main()
