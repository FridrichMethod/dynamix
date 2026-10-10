"""scripts/build_coverage.py: reconciled per-source accounting, song coverage by song IDs (never titles), separate
size/originality buckets, explicit gap lists, byte totals, reconciliation failures and stale-output detection."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures/catalog"))

import catalog_fixture as fx  # noqa: E402
import build_catalog  # noqa: E402
import build_coverage as coverage  # noqa: E402
import build_thumbnails as thumbs  # noqa: E402
from lib import manifest, paths, schema  # noqa: E402

MISSING = "song:fixture-song-without-artwork"
UNIVERSE = "song:fixture-universe-only-song"


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = fx.copy_contract(Path(self.temp.name) / "repo")

    def snapshot(self, source_id):
        return manifest.load_json(self.root / paths.discovery_relpath(source_id))

    def save_snapshot(self, value):
        target = self.root / paths.discovery_relpath(value["source_id"])
        target.write_text(manifest.dumps(manifest.sort_discovery(value)), encoding="utf-8")

    def song_index(self):
        return manifest.load_json(self.root / coverage.SONG_INDEX)

    def add_song_art(self, source_id, size, song_title, song_id, **fields):
        """A song_art record linked to a song of the index (by ID and exact title), with mapping evidence."""
        record = fx.make_record(self.root, source_id, fx.solid_png(size, (size[0] % 251, 7, 9)),
                                f"{song_title} art {size[0]}.png", title=song_title, song_titles=[song_title],
                                song_ids=[song_id], mapping_status=fields.pop("mapping_status", "confirmed_wiki"),
                                mapping_evidence=["https://example.org/evidence"], **fields)
        return fx.add_record(self.root, record)


class FixtureCoverageTests(Base):
    def test_contract_fixture_report(self):
        report = coverage.build(self.root)
        self.assertEqual(report["as_of"], "2026-10-10")
        songs = report["songs"]
        self.assertEqual((songs["index_songs"], songs["denominator"], songs["outside_denominator"]), (5, 4, 1))
        self.assertEqual((songs["with_any_artwork"], songs["with_confirmed_original"], songs["with_long_edge_ge_1920"],
                          songs["missing_art"]), (3, 1, 0, 1))
        gaps = report["gaps"]
        self.assertEqual(gaps["missing_art_songs"], [MISSING])
        self.assertEqual(gaps["preview_only_songs"], ["song:moonlight-of-sand-castle"])
        self.assertEqual(gaps["pending_candidates"], ["wikis:miraheze:File:Song_select_screen.png"])
        self.assertEqual(gaps["blocked_candidates"], ["official:facebook:1000000000000001:1"])
        self.assertEqual(gaps["failed_candidates"], ["wikis:fandom:File:Broken_Upload.png"])
        self.assertEqual(gaps["inferred_only_assets"], [fx.find_record(self.root, title="Cosmic Pulse (crop)")["id"]])
        background = fx.find_record(self.root, title="Dynamix background 03", upstream_status="current")
        self.assertIn(background["id"], gaps["unknown_illustrator_assets"])
        self.assertIn(background["id"], gaps["unmapped_assets"])
        reference = fx.find_record(self.root, kind="reference")
        self.assertNotIn(reference["id"], gaps["unknown_illustrator_assets"], "references credit no illustrator")
        self.assertNotIn(UNIVERSE, gaps["missing_art_songs"], "Universe-only songs are outside the denominator")

    def test_sources_and_candidates_reconcile(self):
        report = coverage.build(self.root)
        sources = report["sources"]
        self.assertEqual(sources["enumeration"], {"complete": ["archives:hkstp", "artists:x:ariiol_", "wikis:miraheze"],
                                                  "incomplete": ["official:facebook", "wikis:fandom"],
                                                  "not_enumerated": ["official:apple-store"]})
        self.assertIn("wikis:atwiki-ja", sources["registered_without_record"])
        rows = {row["id"]: row for row in sources["records"]}
        for row in rows.values():
            if row["candidates"] is not None:
                self.assertEqual(sum(row["by_disposition"].values()), row["candidates"], row["id"])
        self.assertEqual(rows["wikis:fandom"]["open_continuations"], ["allimages"])
        self.assertEqual(rows["wikis:miraheze"]["exclusion_reasons"], {"animated_capture": 1, "universe_only": 1})
        self.assertEqual(rows["official:facebook"]["failures_by_class"], {"challenge": 1})
        candidates = report["candidates"]
        self.assertEqual(candidates["total"], 17)
        self.assertEqual(sum(candidates["by_disposition"].values()), candidates["total"])
        self.assertEqual(candidates["by_disposition"], {"downloaded": 10, "duplicate_bytes": 1, "excluded": 3,
                                                        "failed": 1, "blocked": 1, "pending": 1})
        self.assertEqual(sum(candidates["by_exclusion_reason"].values()), candidates["by_disposition"]["excluded"])

    def test_assets_bytes_and_mismatches_are_separate_counts(self):
        report = coverage.build(self.root)
        catalog = build_catalog.combine(self.root)
        assets, summary = report["assets"], catalog["summary"]
        self.assertEqual((assets["source_asset_records"], assets["unique_files"], assets["exact_duplicate_records"]),
                         (11, 10, 1))
        self.assertEqual((assets["variant_groups"], assets["entries_in_variant_groups"]), (1, 3))
        self.assertEqual(assets["records_by_upstream_status"]["superseded"], 1)
        self.assertEqual(sum(assets["gallery_long_edge"].values()), assets["gallery_images"])
        self.assertIsNone(assets["entries_with_preview"])
        data = report["bytes"]
        self.assertEqual(data["unique_total"], summary["downloaded_bytes"])
        self.assertEqual(data["by_tier"], {"site": summary["site_original_bytes"],
                                           "archive": summary["archive_only_bytes"]})
        self.assertEqual(sum(data["unique_by_primary_family"].values()), data["unique_total"])
        records = [r for family in schema.FAMILIES for r in fx.load_manifest(self.root, family)["assets"]]
        self.assertEqual(sum(data["records_by_family"].values()), sum(r["bytes"] for r in records))
        self.assertIsNone(data["previews"])
        self.assertEqual(data["staged_site_estimate"], data["by_tier"]["site"] + data["catalog_script"])
        self.assertTrue(data["within_pages_budget"])
        fandom = {item["asset_id"]: item["fields"] for item in report["mismatches"]["source_reported"]}
        cosmic = fx.find_record(self.root, source_id="wikis:fandom", title="Cosmic Pulse")
        self.assertEqual(fandom[cosmic["id"]], ["sha1_match", "bytes_match"])
        self.assertEqual(len(report["mismatches"]["transformed_records"]), 1)
        self.assertEqual(report["mismatches"]["song_index_disagreements"],
                         [{"song_id": "song:moonlight-of-sand-castle", "field": "collections"}])
        self.assertEqual(report["mismatches"]["song_index_artwork"], [])

    def test_a_successor_merged_under_another_primary_is_reported(self):
        self.assertEqual(coverage.build(self.root)["mismatches"]["successor_not_primary"], [])
        current = fx.find_record(self.root, title="Dynamix background 03", upstream_status="current")
        artist = fx.add_record(self.root, fx.copy_record(self.root, current, "artists:x:fixture_painter"))
        old = fx.find_record(self.root, upstream_status="superseded")
        report = coverage.build(self.root)
        self.assertEqual(report["mismatches"]["successor_not_primary"],
                         [{"asset_id": old["id"], "superseded_by": current["id"], "catalog_entry": artist["id"]}])

    def test_previews_count_toward_the_staged_estimate(self):
        thumbs.build(self.root)
        report = coverage.build(self.root)
        listing = thumbs.read_manifest(self.root)["thumbnails"]
        self.assertEqual(report["assets"]["entries_with_preview"], len(listing))
        self.assertEqual(report["bytes"]["previews"], sum(entry["bytes"] for entry in listing.values()))
        self.assertEqual(report["bytes"]["staged_site_estimate"], report["bytes"]["by_tier"]["site"]
                         + report["bytes"]["previews"] + report["bytes"]["catalog_script"])


class SongCoverageTests(Base):
    def test_art_counts_follow_song_ids_not_titles(self):
        fx.add_record(self.root, fx.make_record(self.root, "wikis:fixture-mirror", fx.solid_png((50, 50), (1, 1, 1)),
                                                "Fixture Song Without Artwork.png",
                                                title="Fixture Song Without Artwork",
                                                song_titles=["Fixture Song Without Artwork"]))
        report = coverage.build(self.root)
        self.assertEqual(report["gaps"]["missing_art_songs"], [MISSING], "a title alone maps nothing")
        self.add_song_art("wikis:fixture-mirror", (60, 40), "Fixture Song Without Artwork", MISSING)
        report = coverage.build(self.root)
        self.assertEqual((report["songs"]["with_any_artwork"], report["songs"]["missing_art"]), (4, 0))
        self.assertEqual(report["gaps"]["missing_art_songs"], [])
        # The index does not list the new record yet: a reported mismatch, not a silent change.
        self.assertEqual([item["song_id"] for item in report["mismatches"]["song_index_artwork"]], [MISSING])

    def test_size_and_originality_buckets_are_reported_separately(self):
        self.add_song_art("wikis:fixture-mirror", (2000, 1000), "Fixture Song Without Artwork", MISSING)
        report = coverage.build(self.root)
        self.assertEqual((report["songs"]["with_long_edge_ge_1920"], report["songs"]["with_confirmed_original"]),
                         (1, 1))
        self.assertEqual(report["assets"]["gallery_long_edge"]["ge_1920"], 1)
        self.add_song_art("artists:x:fixture_painter", (1200, 600), "Fixture Song Without Artwork", MISSING,
                          mapping_status="confirmed_primary",
                          quality={"source_variant": "site_original", "origin_status": "artist_post",
                                   "master_status": "unverified", "notes": ""})
        report = coverage.build(self.root)
        self.assertEqual((report["songs"]["with_long_edge_ge_1920"], report["songs"]["with_confirmed_original"]),
                         (1, 2))

    def test_screenshots_do_not_count_as_song_art(self):
        self.add_song_art("archives:fixture-press", (320, 180), "Fixture Song Without Artwork", MISSING,
                          kind="gameplay_screenshot")
        report = coverage.build(self.root)
        self.assertEqual(report["gaps"]["missing_art_songs"], [MISSING])
        self.assertEqual(report["songs"]["with_any_linked_asset"], 4)

    def test_art_of_songs_outside_the_denominator_is_reported_not_counted(self):
        self.add_song_art("wikis:fixture-mirror", (64, 64), "Fixture Universe-only Song", UNIVERSE)
        report = coverage.build(self.root)
        self.assertEqual(report["songs"]["with_any_artwork"], 3)
        self.assertEqual(report["gaps"]["linked_outside_denominator"], [UNIVERSE])


class ReconciliationFailureTests(Base):
    def assert_fails(self, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            coverage.build(self.root)
        self.assertFalse((self.root / coverage.COVERAGE_JSON).exists())

    def test_a_downloaded_asset_whose_candidate_is_not_downloaded_fails(self):
        value = self.snapshot("wikis:miraheze")
        crop = fx.find_record(self.root, title="Cosmic Pulse (crop)")
        for candidate in value["candidates"]:
            if candidate["id"] == crop["candidate_id"]:
                candidate.update(disposition="pending", reason="re-review", asset_id=None)
        self.save_snapshot(value)
        self.assert_fails("does not reconcile")

    def test_a_wrong_candidate_count_fails(self):
        value = fx.load_manifest(self.root, "wikis")
        value["sources"][0]["candidate_count"] += 1
        fx.save_manifest(self.root, "wikis", value)
        self.assert_fails("candidate_count")

    def test_a_failed_candidate_without_a_failure_record_fails(self):
        value = fx.load_manifest(self.root, "wikis")
        value["failures"] = []
        for source in value["sources"]:
            source.pop("failure_count", None)
        fx.save_manifest(self.root, "wikis", value)
        self.assert_fails("needs a Failure record")

    def test_a_snapshot_of_an_undeclared_source_fails(self):
        value = self.snapshot("archives:hkstp")
        value["source_id"] = "archives:wayback"
        for candidate in value["candidates"]:
            candidate.update(source_id="archives:wayback", id="archives:wayback:" + candidate["upstream_key"])
            candidate.pop("asset_id", None)
            candidate.update(disposition="pending", reason="not fetched")
        self.save_snapshot(value)
        self.assert_fails("not the discovery snapshot of a source declared")
        (self.root / "data/discovery/wayback.json").unlink()
        (self.root / "data/discovery/song-index.wikis.json").write_text("{}", encoding="utf-8")
        coverage.build(self.root)  # a proposed song index may sit next to the snapshots

    def test_counts_claimed_without_a_snapshot_fail(self):
        value = fx.load_manifest(self.root, "official")
        apple = next(source for source in value["sources"] if source["id"] == "official:apple-store")
        apple["candidate_count"] = 3
        fx.save_manifest(self.root, "official", value)
        self.assert_fails("without a discovery snapshot")
        del apple["candidate_count"]
        apple["failure_count"] = 2
        fx.save_manifest(self.root, "official", value)
        self.assert_fails("failure_count 2")

    def test_the_repository_build_requires_previews(self):
        with mock.patch.object(build_catalog, "REPO_ROOT", self.root):
            self.assert_fails("Missing required input: data/thumbs.json")
            thumbs.build(self.root)
            self.assertIsNotNone(coverage.build(self.root)["bytes"]["previews"])

    def test_song_index_problems_fail(self):
        target = self.root / coverage.SONG_INDEX
        original = target.read_text(encoding="utf-8")
        index = self.song_index()
        index["songs"] = [song for song in index["songs"] if song["id"] != "song:moonlight-of-sand-castle"]
        index["disagreements"] = []
        target.write_text(manifest.dumps(index), encoding="utf-8")
        self.assert_fails("song:moonlight-of-sand-castle")
        target.write_text(original, encoding="utf-8")
        target.unlink()
        self.assert_fails("Missing required input: data/sources/song-index.json")


class CommandLineTests(Base):
    def run_main(self, *args):
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = coverage.main([*args, "--root", str(self.root)])
        return code, stderr.getvalue()

    def test_check_detects_stale_output_without_writing_and_rebuilds_identically(self):
        self.assertEqual(self.run_main()[0], 0)
        path = self.root / coverage.COVERAGE_JSON
        built = path.read_bytes()
        self.assertEqual(self.run_main("--check")[0], 0)
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(path.read_bytes(), built)
        path.write_bytes(built.replace(b"\n", b"\r\n"))
        self.assertEqual(self.run_main("--check")[0], 0)
        value = json.loads(built)
        value["songs"]["missing_art"] = 0
        path.write_text(manifest.dumps(value), encoding="utf-8")
        edited = path.read_bytes()
        self.assertEqual(self.run_main("--check"), (1, f"Out-of-date generated file: {coverage.COVERAGE_JSON}\n"))
        self.assertEqual(path.read_bytes(), edited)
        path.unlink()
        self.assertEqual(self.run_main("--check")[0], 1)
        self.assertFalse(path.exists())

    def test_a_reconciliation_failure_exits_nonzero_without_writing(self):
        value = fx.load_manifest(self.root, "archives")
        value["sources"][0]["candidate_count"] = 99
        fx.save_manifest(self.root, "archives", value)
        code, stderr = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("candidate_count", stderr)
        self.assertFalse((self.root / coverage.COVERAGE_JSON).exists())

    def test_documented_command_line_runs_isolated(self):
        result = fx.run_script("build_coverage", "--root", str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["songs"]["denominator"], 4)
        result = fx.run_script("build_coverage", "--root", str(self.root), "--check")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
