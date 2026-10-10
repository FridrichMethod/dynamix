"""scripts/build_coverage.py: reconciled per-source accounting, song coverage by song IDs (never titles), separate
size/originality buckets, explicit gap lists, byte totals, reconciliation failures and stale-output detection."""

import contextlib
import io
import json
import os
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
import prepare_pages as pages  # noqa: E402  (CI-owned; imported read-only to check the staged-site estimate)
from lib import manifest, paths, schema  # noqa: E402

MISSING = "song:fixture-song-without-artwork"
MISSING_TITLE = "Fixture Song Without Artwork"
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
        self.assertEqual((songs["with_any_artwork"], songs["inferred_only"], songs["conflict_only"],
                          songs["missing_art"]), (3, 0, 0, 1))
        self.assertEqual((songs["with_confirmed_original"], songs["with_long_edge_ge_1920"],
                          songs["low_resolution_only"]), (1, 0, 3))
        self.assertEqual(songs["best_long_edge"], {"lt_1000": 2, "lt_1920": 1, "ge_1920": 0})
        # Moonlight's only record is a known platform transform: a preview whatever its source_variant says.
        self.assertEqual((songs["with_site_original"], songs["variant_unknown"], songs["preview_only"]), (2, 0, 1))
        gaps = report["gaps"]
        self.assertEqual(gaps["missing_art_songs"], [MISSING])
        self.assertEqual((gaps["inferred_only_songs"], gaps["conflict_only_songs"]), ([], []))
        self.assertEqual(gaps["low_resolution_only_songs"],
                         ["song:cosmic-pulse", "song:file-568a196c", "song:moonlight-of-sand-castle"])
        self.assertEqual(gaps["preview_only_songs"], ["song:moonlight-of-sand-castle"])
        self.assertEqual(gaps["variant_unknown_songs"], [])
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
        self.assertIsNone(data["index_html"], "no template under a fixture root: index.html is not rendered")
        self.assertEqual(data["catalog_json"], len(manifest.dumps(catalog).encode("utf-8")))
        self.assertEqual(data["catalog_script"], len(build_catalog.catalog_script(catalog).encode("utf-8")))
        self.assertEqual(data["coverage_json"], len(manifest.dumps(report).encode("utf-8")))
        self.assertEqual(data["staged_site_estimate"], data["by_tier"]["site"] + data["catalog_json"]
                         + data["catalog_script"] + data["coverage_json"])
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

    def test_the_staged_estimate_counts_every_generated_site_file(self):
        """Site originals, previews and the generated files prepare_pages stages, at their written sizes."""
        fx.write_template(self.root)
        thumbs.build(self.root)
        build_catalog.build(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(coverage.main(["--root", str(self.root)]), 0)
        report = json.loads((self.root / coverage.COVERAGE_JSON).read_text(encoding="utf-8"))
        listing = thumbs.read_manifest(self.root)["thumbnails"]
        self.assertEqual(report["assets"]["entries_with_preview"], len(listing))
        data = report["bytes"]
        self.assertEqual(data["previews"], sum(entry["bytes"] for entry in listing.values()))
        written = {key: (self.root / rel).stat().st_size for key, rel in (
            ("catalog_json", build_catalog.CATALOG_JSON), ("catalog_script", build_catalog.CATALOG_JS),
            ("index_html", build_catalog.INDEX_HTML), ("coverage_json", coverage.COVERAGE_JSON))}
        self.assertEqual({key: data[key] for key in written}, written)
        self.assertEqual(data["staged_site_estimate"],
                         data["by_tier"]["site"] + data["previews"] + sum(written.values()))
        self.assertEqual(coverage.build(self.root), report)

    def test_the_staged_estimate_decides_the_budget_flag(self):
        """The estimate includes data/coverage.json itself, so the flag and the size must agree at the boundary."""
        def staged(budget):
            with mock.patch.object(schema, "PAGES_BUDGET_BYTES", budget):
                report = coverage.build(self.root)
            self.assertEqual(report["bytes"]["coverage_json"], len(manifest.dumps(report).encode("utf-8")))
            return report["bytes"]["staged_site_estimate"], report["bytes"]["within_pages_budget"]

        over, flag = staged(100_000)  # budgets with as many digits as the fixture's estimate (about 130 kB)
        self.assertFalse(flag)
        self.assertTrue(100_000 < over - 2 and over < 1_000_000, over)
        self.assertEqual(staged(over), (over - 1, True))  # "true" is one byte shorter than "false"
        self.assertEqual(staged(over - 2), (over, False))


class PagesConsistencyTests(Base):
    """The staged-site estimate counts what scripts/prepare_pages.py (CI-owned) really stages: one original per
    SHA-256 at the primary record's url, each preview once and the generated page files. Only the static page files
    (not part of the estimate by definition) separate the two totals."""

    STATIC = {".nojekyll": b"", "LICENSE": b"Fixture license text\n", "NOTICE": b"Fixture notice\n",
              "README.md": b"# Fixture\n", "README.zh-CN.md": "# 样例\n".encode("utf-8"),
              "archive.html": b"<!doctype html>\n<title>Archive</title>\n", "src/app.js": b"\"use strict\";\n",
              "assets/site/site.css": b"body { margin: 0; }\n"}

    def stage_site(self):
        fx.write_template(self.root)
        thumbs.build(self.root)
        build_catalog.build(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(coverage.main(["--root", str(self.root)]), 0)
        for rel, data in self.STATIC.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_bytes(data)
        if not (fx.git(self.root, "init", "-q") and fx.git(self.root, "add", "-A")
                and fx.git(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.org", "commit",
                           "-q", "--no-gpg-sign", "-m", "fixture")):
            self.skipTest("git is not available")
        output = Path(self.temp.name) / "site"
        with mock.patch.dict(os.environ, fx.clean_environment(), clear=True):
            summary, _ = pages.prepare(self.root, output, pages.tracked_entries(self.root),
                                       commit=pages.head_commit(self.root))
        report = json.loads((self.root / coverage.COVERAGE_JSON).read_text(encoding="utf-8"))
        return summary, report, output

    def test_the_estimate_matches_what_prepare_pages_stages(self):
        summary, report, output = self.stage_site()
        catalog = manifest.load_json(self.root / build_catalog.CATALOG_JSON)
        data = report["bytes"]
        self.assertTrue(summary["matches_commit"])
        originals = coverage.staged_originals(catalog)
        self.assertEqual(summary["tiers"]["site_original"], {"files": len(originals), "bytes": data["by_tier"]["site"]})
        previews = coverage.staged_previews(catalog, thumbs.read_manifest(self.root))
        self.assertEqual(summary["tiers"]["preview"], {"files": len(previews), "bytes": data["previews"]})
        generated = sum(data[key] for key in ("catalog_json", "catalog_script", "index_html", "coverage_json"))
        static = sum(map(len, self.STATIC.values()))
        self.assertEqual(summary["tiers"]["page"], {"files": len(self.STATIC) + 4, "bytes": generated + static})
        self.assertEqual(summary["bytes"], data["staged_site_estimate"] + static)
        staged = [path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()]
        self.assertEqual(sorted(rel for rel in staged if rel.startswith("assets/public/")), sorted(originals))

    def test_identical_bytes_from_both_wikis_are_staged_and_counted_once(self):
        summary, report, output = self.stage_site()
        miraheze = fx.find_record(self.root, source_id="wikis:miraheze", title="Cosmic Pulse")
        fandom = fx.find_record(self.root, source_id="wikis:fandom", title="Cosmic Pulse")
        self.assertEqual(miraheze["sha256"], fandom["sha256"])
        self.assertTrue((output / miraheze["path"]).is_file(), "the primary (Miraheze) copy is the one published")
        self.assertFalse((output / fandom["path"]).exists())
        archive_only = summary["excluded"]["archive_only_originals"]
        self.assertEqual(summary["excluded"]["unstaged_public_files"],
                         {"files": archive_only["files"] + 1, "bytes": archive_only["bytes"] + fandom["bytes"]})
        records = [r for family in schema.FAMILIES for r in fx.load_manifest(self.root, family)["assets"]]
        site_records = sum(r["bytes"] for r in records if schema.tier_for(r["kind"], r["format"]) == "site")
        self.assertEqual(report["bytes"]["by_tier"]["site"], site_records - fandom["bytes"])


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
        self.assertEqual(report["songs"]["best_long_edge"], {"lt_1000": 2, "lt_1920": 1, "ge_1920": 1})
        self.assertEqual(report["songs"]["low_resolution_only"], 3)
        self.assertNotIn(MISSING, report["gaps"]["low_resolution_only_songs"])
        self.assertIn("song:cosmic-pulse", report["gaps"]["low_resolution_only_songs"])
        self.add_song_art("artists:x:fixture_painter", (1200, 600), "Fixture Song Without Artwork", MISSING,
                          mapping_status="confirmed_primary",
                          quality={"source_variant": "site_original", "origin_status": "artist_post",
                                   "master_status": "unverified", "notes": ""})
        report = coverage.build(self.root)
        self.assertEqual((report["songs"]["with_long_edge_ge_1920"], report["songs"]["with_confirmed_original"]),
                         (1, 2))

    def assert_song_buckets(self, report, expected):
        """(with_any_artwork, inferred_only, conflict_only, missing_art), which always sum to the denominator."""
        songs = report["songs"]
        found = tuple(songs[key] for key in ("with_any_artwork", "inferred_only", "conflict_only", "missing_art"))
        self.assertEqual(found, expected)
        self.assertEqual(sum(found), songs["denominator"])
        self.assertEqual(sum(songs["best_long_edge"].values()), songs["with_any_artwork"])
        self.assertEqual(songs["with_site_original"] + songs["variant_unknown"] + songs["preview_only"],
                         songs["with_any_artwork"])

    def test_inferred_and_conflict_links_are_reported_beside_coverage(self):
        record = self.add_song_art("wikis:fixture-mirror", (64, 64), MISSING_TITLE, MISSING, mapping_status="inferred")
        report = coverage.build(self.root)
        self.assert_song_buckets(report, (3, 1, 0, 0))
        self.assertEqual((report["gaps"]["inferred_only_songs"], report["gaps"]["missing_art_songs"]), ([MISSING], []))
        self.assertIn(record["id"], report["gaps"]["inferred_only_assets"])
        self.assertNotIn(MISSING, report["gaps"]["low_resolution_only_songs"], "only confirmed art is measured")
        record = self.add_song_art("wikis:fixture-mirror", (66, 66), MISSING_TITLE, MISSING, mapping_status="conflict")
        report = coverage.build(self.root)
        self.assert_song_buckets(report, (3, 0, 1, 0))
        gaps = report["gaps"]
        self.assertEqual((gaps["conflict_only_songs"], gaps["inferred_only_songs"]), ([MISSING], []))
        self.assertIn(record["id"], report["gaps"]["conflict_assets"])
        self.add_song_art("wikis:fixture-mirror", (68, 68), MISSING_TITLE, MISSING)  # confirmed_wiki
        report = coverage.build(self.root)
        self.assert_song_buckets(report, (4, 0, 0, 0))
        self.assertEqual((report["gaps"]["conflict_only_songs"], report["gaps"]["inferred_only_songs"]), ([], []))

    def test_a_conflicting_secondary_record_does_not_cover_its_song(self):
        cosmic = fx.find_record(self.root, source_id="artists:x:ariiol_", kind="song_art")
        fx.add_record(self.root, fx.copy_record(self.root, cosmic, "wikis:fixture-mirror", song_titles=[MISSING_TITLE],
                                                song_ids=[MISSING], mapping_status="conflict",
                                                mapping_evidence=["https://example.org/disputed"]))
        entry = next(asset for asset in build_catalog.combine(self.root)["assets"] if asset["id"] == cosmic["id"])
        self.assertIn(MISSING, entry["song_ids"], "the merged song_ids are a union across provenance")
        report = coverage.build(self.root)
        self.assert_song_buckets(report, (3, 0, 1, 0))
        self.assertEqual(report["gaps"]["conflict_only_songs"], [MISSING])
        self.assertIn(cosmic["id"], report["gaps"]["conflict_assets"])

    def test_renditions_and_confirmed_originals_follow_each_record(self):
        def artist_post(size, variant, **extra):
            quality = {"source_variant": variant, "origin_status": "artist_post", "master_status": "unverified",
                       "notes": ""}
            self.add_song_art("artists:x:fixture_painter", size, MISSING_TITLE, MISSING,
                              mapping_status="confirmed_primary", quality=quality, **extra)
            report = coverage.build(self.root)
            self.assert_song_buckets(report, (4, 0, 0, 0))
            gaps = report["gaps"]
            bucket = [name for name in ("preview_only_songs", "variant_unknown_songs") if MISSING in gaps[name]]
            return report["songs"]["with_confirmed_original"], bucket

        self.assertEqual(artist_post((640, 360), "preview"), (1, ["preview_only_songs"]),
                         "a platform-downscaled post is neither an original nor a site original")
        self.assertEqual(artist_post((650, 360), "site_original", transformed=True), (1, ["preview_only_songs"]),
                         "a known platform transform is a preview")
        self.assertEqual(artist_post((660, 360), "unknown"), (1, ["variant_unknown_songs"]),
                         "an unknown variant is reported as unknown, not as a preview")
        self.assertEqual(artist_post((670, 360), "site_original"), (2, []))

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

    def test_the_repository_build_requires_previews_and_the_index(self):
        with mock.patch.object(build_catalog, "REPO_ROOT", self.root):
            self.assert_fails("Missing required input: data/thumbs.json")
            thumbs.build(self.root)
            self.assert_fails("Missing required input: templates/slideshow.html")
            fx.write_template(self.root)
            data = coverage.build(self.root)["bytes"]
            self.assertIsNotNone(data["previews"])
            self.assertIsNotNone(data["index_html"])

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
