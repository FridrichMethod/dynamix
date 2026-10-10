"""scripts/build_catalog.py: SHA-256 dedup across sources and families, retained variants and revisions, safe paths and
URLs, offline verification, stale-output detection, deterministic rebuilds, safe catalog.js and the index hook.

Adapted from FridrichMethod/deemo tests/test_catalog.py (Apache-2.0, commit b14bf0d5f5a05e5382c319f3fd5019a4936820b8)
for the Dynamix schema-v1 contract; every case runs on a temporary copy of tests/fixtures/contract.
"""

import contextlib
import copy
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures/catalog"))

import catalog_fixture as fx  # noqa: E402
import build_catalog as build  # noqa: E402
import build_thumbnails as thumbs  # noqa: E402
from lib import manifest, schema  # noqa: E402

OUTPUTS = (build.CATALOG_JSON, build.CATALOG_JS, build.INDEX_HTML)


class Slides(HTMLParser):
    """Each slide <img>'s attributes (names and character references resolved), in order."""

    def __init__(self, markup):
        super().__init__()
        self.images, self.in_slide = [], False
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "div" and "mySlides" in (values.get("class") or ""):
            self.in_slide = True
        elif tag == "img" and self.in_slide:
            self.images.append(values)
            self.in_slide = False


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = fx.copy_contract(self.base / "repo")

    def entries(self, catalog, **match):
        return [asset for asset in catalog["assets"] if all(asset.get(k) == v for k, v in match.items())]

    def run_main(self, *args, root=None):
        """The command line in-process against a temporary root; returns (exit code, stderr)."""
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = build.main([*args, "--root", str(root or self.root)])
        return code, stderr.getvalue()

    def outputs(self, root=None):
        root = root or self.root
        return {rel: (root / rel).read_bytes() for rel in OUTPUTS if (root / rel).exists()}


class ContractFixtureTests(Base):
    def test_contract_fixture_builds_a_valid_deduplicated_catalog(self):
        catalog = build.combine(self.root, verify=True)
        build.validate_catalog(catalog)
        summary = catalog["summary"]
        self.assertEqual((summary["source_asset_records"], summary["unique_files"], summary["exact_duplicate_records"]),
                         (11, 10, 1))
        self.assertEqual((summary["gallery_images"], summary["slideshow_images"], summary["failure_count"]), (9, 1, 2))
        self.assertEqual(summary["by_family"], {"wikis": 7, "artists": 2, "official": 0, "archives": 2})
        self.assertEqual(catalog["snapshots"]["official"], None)
        self.assertEqual([source["id"] for source in catalog["sources"]], sorted(s["id"] for s in catalog["sources"]))
        counts = {source["id"]: source["asset_count"] for source in catalog["sources"]}
        self.assertEqual((counts["wikis:miraheze"], counts["official:apple-store"]), (5, 0))
        # Miraheze and Fandom hold identical Cosmic Pulse bytes: one entry, Miraheze (the current wiki) primary.
        cosmic = self.entries(catalog, title="Cosmic Pulse")
        self.assertEqual(len(cosmic), 1)
        self.assertEqual([p["source_id"] for p in cosmic[0]["provenance"]], ["wikis:miraheze", "wikis:fandom"])
        self.assertEqual(cosmic[0]["source_id"], "wikis:miraheze")
        self.assertTrue(cosmic[0]["url"].startswith("assets/public/wikis/miraheze/"))
        self.assertEqual(len(cosmic[0]["variants"]), 2)
        self.assertEqual([failure["id"] for failure in catalog["failures"]],
                         sorted(failure["id"] for failure in catalog["failures"]))

    def test_tiers_gallery_and_slideshow_follow_the_contract(self):
        catalog = build.combine(self.root)
        pdf = self.entries(catalog, format="PDF")[0]
        self.assertEqual((pdf["gallery"], pdf["slideshow"], pdf["site_original"]), (False, False, False))
        shot = self.entries(catalog, kind="gameplay_screenshot")[0]
        self.assertEqual((shot["gallery"], shot["site_original"]), (True, False))
        self.assertEqual(shot["archive_url"], schema.ARCHIVE_URL_PREFIX + shot["url"])
        self.assertTrue(build.needs_preview(shot), "an archive-tier raster needs a preview even when small")
        slides = [asset for asset in catalog["assets"] if asset["slideshow"]]
        self.assertEqual([(a["source_id"], a["width"]) for a in slides], [("artists:x:ariiol_", 1200)])
        kousa = self.entries(catalog, title="交差して歪む過去と未来")[0]
        self.assertTrue(kousa["url"].startswith("assets/public/wikis/miraheze/file--"))
        self.assertFalse(build.needs_preview(kousa), "a small site-tier image is shown from its original")


class DedupTests(Base):
    def test_duplicates_across_families_take_the_artist_record_as_primary(self):
        background = fx.find_record(self.root, title="Dynamix background 03", upstream_status="current")
        artist = fx.add_record(self.root, fx.copy_record(self.root, background, "artists:x:fixture_painter",
                                                         illustrators=["Fixture Painter"], title="Background 03 post"))
        official = fx.add_record(self.root, fx.copy_record(self.root, background, "official:website",
                                                           composers=["Not A Painter"]))
        catalog = build.combine(self.root, verify=True)
        build.validate_catalog(catalog)
        entry = self.entries(catalog, sha256=background["sha256"])
        self.assertEqual(len(entry), 1)
        entry = entry[0]
        self.assertEqual(entry["id"], artist["id"])
        self.assertEqual((entry["family"], entry["title"]), ("artists", "Background 03 post"))
        self.assertEqual([p["asset_id"] for p in entry["provenance"]], [artist["id"], official["id"],
                                                                         background["id"]])
        self.assertEqual((entry["illustrators"], entry["composers"]), (["Fixture Painter"], ["Not A Painter"]))
        self.assertEqual(catalog["summary"]["exact_duplicate_records"], 3)
        self.assertEqual(catalog["summary"]["unique_files"], 10)

    def test_distinct_bytes_stay_distinct_entries(self):
        cosmic = fx.find_record(self.root, source_id="wikis:miraheze", title="Cosmic Pulse")
        same_title = fx.add_record(self.root, fx.make_record(
            self.root, "archives:fixture-scan", fx.solid_png((60, 60), (1, 2, 3)), "Cosmic Pulse.png",
            title="Cosmic Pulse", variant_group=cosmic["variant_group"], variant_role="scan"))
        catalog = build.combine(self.root, verify=True)
        titles = [asset["id"] for asset in self.entries(catalog, title="Cosmic Pulse")]
        self.assertEqual(len(titles), 2)
        self.assertIn(same_title["id"], titles)
        crop = self.entries(catalog, title="Cosmic Pulse (crop)")[0]
        self.assertIn(same_title["id"], crop["variants"])
        self.assertEqual(catalog["summary"]["unique_files"], 11)
        members = [asset for asset in catalog["assets"] if asset.get("variant_group") == cosmic["variant_group"]]
        group = sorted(asset["id"] for asset in members)
        for asset in members:
            self.assertEqual(asset["variants"], [i for i in group if i != asset["id"]])

    def test_superseded_and_removed_records_are_retained(self):
        removed = fx.add_record(self.root, fx.make_record(
            self.root, "wikis:fixture-mirror", fx.solid_png((40, 30), (9, 9, 9)), "Gone.png", kind="background",
            upstream_status="removed", notes="Deleted upstream; archived bytes kept."))
        catalog = build.combine(self.root, verify=True)
        old = self.entries(catalog, upstream_status="superseded")
        self.assertEqual(len(old), 1)
        current = self.entries(catalog, id=old[0]["superseded_by"])[0]
        self.assertEqual((current["upstream_status"], current["title"]), ("current", old[0]["title"]))
        self.assertEqual(old[0]["provenance"][0]["superseded_by"], current["id"])
        self.assertEqual(old[0]["provenance"][0]["upstream_status"], "superseded")
        gone = self.entries(catalog, id=removed["id"])[0]
        self.assertEqual((gone["upstream_status"], gone["provenance"][0]["upstream_status"]), ("removed", "removed"))

    def test_records_sharing_a_hash_must_agree_on_measured_fields(self):
        record = {"id": "wikis:a:0000000000000000", "sha256": "0" * 64, "bytes": 1, "sha1": "a" * 40,
                  "format": "PNG", "width": 1, "height": 1}
        with self.assertRaisesRegex(build.CatalogError, "disagree on width"):
            build._group_by_sha([record, {**record, "id": "wikis:b:0000000000000000", "width": 2}])

    def test_asset_count_must_match_the_records(self):
        value = fx.load_manifest(self.root, "archives")
        value["sources"][0]["asset_count"] = 7
        fx.save_manifest(self.root, "archives", value)
        with self.assertRaisesRegex(build.CatalogError, "asset_count 7"):
            build.combine(self.root)


class SafetyTests(Base):
    def test_unsafe_paths_and_urls_are_rejected(self):
        record = fx.find_record(self.root, title="Dynamix background 03", upstream_status="current")
        cases = {
            "path": {"path": "assets/public/wikis/miraheze/../../../../secret.png"},
            "page_url": {"page_url": "javascript:alert(1)"},
            "download_url": {"download_url": "https://user:secret@static.miraheze.org/x.png"},
            "resolved_url": {"resolved_url": "file:///etc/passwd"},
        }
        for field, change in cases.items():
            with self.subTest(field=field):
                fx.update_record(self.root, record["id"], **change)
                with self.assertRaisesRegex(build.CatalogError, field):
                    build.combine(self.root)
                fx.update_record(self.root, record["id"], **{field: record[field]})
        build.combine(self.root)

    def test_symlinks_cannot_lead_an_asset_path_elsewhere(self):
        record = fx.find_record(self.root, title="Dynamix background 03", upstream_status="current")
        target = self.root / record["path"]
        outside = self.base / "outside.png"
        shutil.move(target, outside)
        try:
            target.symlink_to(outside)
        except OSError as error:  # platforms without symlink support
            self.skipTest(f"cannot create symlinks: {error}")
        for verify in (False, True):
            with self.subTest(verify=verify), self.assertRaisesRegex(build.CatalogError, "symlink"):
                build.combine(self.root, verify=verify)
        target.unlink()
        shutil.move(outside, target)
        directory = self.root / "assets/public/archives"
        shutil.move(directory, self.base / "archives")
        directory.symlink_to(self.base / "archives", target_is_directory=True)
        with self.assertRaisesRegex(build.CatalogError, "symlink"):
            build.combine(self.root)

    def test_verify_rehashes_and_decodes_every_file(self):
        record = fx.find_record(self.root, title="Dynamix background 03", upstream_status="current")
        file = self.root / record["path"]
        data = bytearray(file.read_bytes())
        data[len(data) // 2] ^= 0xFF  # same size: only hashing/decoding notices
        file.write_bytes(bytes(data))
        build.combine(self.root)
        with self.assertRaisesRegex(build.CatalogError, record["id"]):
            build.combine(self.root, verify=True)
        file.write_bytes(b"")
        with self.assertRaisesRegex(build.CatalogError, "bytes"):
            build.combine(self.root)
        file.unlink()
        with self.assertRaisesRegex(build.CatalogError, "missing file"):
            build.combine(self.root)

    def test_verify_rejects_orphans_under_assets_public(self):
        orphan = self.root / "assets/public/wikis/miraheze/Stray--000000000000.png"
        orphan.write_bytes(fx.solid_png((4, 4), (0, 0, 0)))
        (self.root / "assets/public/wikis/miraheze/.Interrupted.x.part").write_bytes(b"partial download")
        build.combine(self.root)  # only --verify looks for orphans
        stray = "assets/public/wikis/miraheze/Stray--000000000000.png"
        with self.assertRaisesRegex(build.CatalogError, stray) as raised:
            build.combine(self.root, verify=True)
        self.assertNotIn(".part", str(raised.exception))
        orphan.unlink()
        build.combine(self.root, verify=True)

    def test_missing_manifest_is_allowed_but_an_invalid_one_fails(self):
        (self.root / "data/sources/official.json").unlink()
        catalog = build.combine(self.root, verify=True)
        self.assertNotIn("official:facebook", [source["id"] for source in catalog["sources"]])
        self.assertIsNone(catalog["snapshots"]["official"])
        wikis = self.root / "data/sources/wikis.json"
        original = wikis.read_text(encoding="utf-8")
        for broken in (original.replace('"illustrators"', '"illustrator"', 1), original[:-20],
                       original.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1)):
            with self.subTest(broken=broken[-40:]):
                wikis.write_text(broken, encoding="utf-8")
                with self.assertRaisesRegex(build.CatalogError, "data/sources/wikis.json"):
                    build.combine(self.root)
        wikis.write_text(original, encoding="utf-8")

    def test_a_tracked_manifest_missing_from_disk_fails(self):
        if not (fx.git(self.root, "init", "-q") and fx.git(self.root, "add", "data")):
            self.skipTest("git is unavailable")
        self.assertIn("data/sources/archives.json", build.tracked_files(self.root, ["data/sources"]))
        (self.root / "data/sources/archives.json").unlink()
        with self.assertRaisesRegex(build.CatalogError, "tracked by Git but missing"):
            build.combine(self.root)
        nested = self.root / "data"  # a directory inside a checkout is not the top of a checkout
        self.assertIsNone(build.tracked_files(nested, ["sources"]))

    def test_the_repository_root_fails_closed_when_git_cannot_tell(self):
        (self.root / "data/sources/archives.json").unlink()
        with mock.patch.object(build, "tracked_files", return_value=None):
            catalog = build.combine(self.root)  # a fixture root: the family is empty
            self.assertNotIn("archives:hkstp", [source["id"] for source in catalog["sources"]])
            with mock.patch.object(build, "REPO_ROOT", self.root), \
                    self.assertRaisesRegex(build.CatalogError, "archives.json is missing and Git cannot be queried"):
                build.combine(self.root)
            with mock.patch.object(build, "REPO_ROOT", self.root), \
                    self.assertRaisesRegex(build.CatalogError, "Git cannot be queried"):
                thumbs.build(self.root, prune=True)  # --prune must not retire the family's previews
            self.assertFalse((self.root / thumbs.MANIFEST).exists())

    def test_command_line_without_git_refuses_a_missing_manifest(self):
        """The documented command at a checkout's root: Git on PATH names the tracked file; without Git the build
        still refuses instead of writing a catalog without that family."""
        checkout = self.base / "checkout"
        shutil.copytree(self.root, checkout)
        shutil.copytree(fx.SCRIPTS, checkout / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        if not (fx.git(checkout, "init", "-q") and fx.git(checkout, "add", "data")):
            self.skipTest("git is unavailable")
        (checkout / "data/sources/archives.json").unlink()
        script = [sys.executable, "-I", "-B", str(checkout / "scripts/build_catalog.py")]
        for environment, message in ((fx.clean_environment(), "tracked by Git but missing"),
                                     ({"PATH": str(self.base / "no-git"), "HOME": str(self.base)},
                                      "Git cannot be queried")):
            with self.subTest(message=message):
                result = subprocess.run(script, capture_output=True, text=True, timeout=300, env=environment)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(message, result.stderr)
                self.assertFalse((checkout / build.CATALOG_JSON).exists())


class OutputTests(Base):
    def setUp(self):
        super().setUp()
        fx.write_template(self.root)
        thumbs.build(self.root)

    def test_build_writes_valid_outputs_and_check_passes(self):
        self.assertEqual(self.run_main("--verify")[0], 0)
        self.assertEqual(sorted(self.outputs()), sorted(OUTPUTS))
        catalog = manifest.load_json(self.root / build.CATALOG_JSON)
        build.validate_catalog(catalog)
        self.assertEqual([a["id"] for a in catalog["assets"] if a["thumb"]],
                         [a["id"] for a in catalog["assets"] if build.needs_preview(a)])
        self.assertEqual(self.run_main("--verify", "--check")[0], 0)

    def test_check_reports_each_stale_or_missing_output_without_writing(self):
        self.assertEqual(self.run_main()[0], 0)
        for rel in OUTPUTS:
            with self.subTest(rel):
                path = self.root / rel
                built = path.read_bytes()
                path.write_bytes(built + b"<!-- edited -->")
                code, stderr = self.run_main("--check")
                self.assertEqual((code, stderr.strip()), (1, f"Out-of-date generated file: {rel}"))
                self.assertEqual(path.read_bytes(), built + b"<!-- edited -->")
                path.unlink()
                self.assertEqual(self.run_main("--check")[0], 1)
                self.assertFalse(path.exists())
                path.write_bytes(built)
        self.assertEqual(self.run_main("--check")[0], 0)
        record = fx.find_record(self.root, title="Dynamix background 03", upstream_status="current")
        fx.update_record(self.root, record["id"], title="Renamed upstream")
        code, stderr = self.run_main("--check")
        self.assertEqual(code, 1)
        self.assertIn(f"Out-of-date generated file: {build.CATALOG_JSON}", stderr)
        self.assertIn(f"Out-of-date generated file: {build.CATALOG_JS}", stderr)

    def test_check_accepts_a_crlf_checkout_but_not_a_lone_cr(self):
        self.assertEqual(self.run_main()[0], 0)
        index = self.root / build.INDEX_HTML
        built = index.read_bytes()
        self.assertNotIn(b"\r", built)
        index.write_bytes(built.replace(b"\n", b"\r\n"))
        self.assertEqual(self.run_main("--check")[0], 0)
        index.write_bytes(built.replace(b"\n", b"\r", 1))
        self.assertEqual(self.run_main("--check")[0], 1)

    def test_rebuilds_are_byte_identical(self):
        other = fx.copy_contract(self.base / "other")
        fx.write_template(other)
        thumbs.build(other)
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.run_main(root=other)[0], 0)
        first = self.outputs()
        self.assertEqual(first, self.outputs(other))
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.outputs(), first)

    def test_catalog_script_escapes_markup_and_line_separators(self):
        record = fx.find_record(self.root, title="Dynamix background 03", upstream_status="current")
        hostile = "Back</script><script>alert(1)</script><!--  line para & 'q' \"d\""
        fx.update_record(self.root, record["id"], title=hostile, notes=hostile)
        self.assertEqual(self.run_main()[0], 0)
        script = (self.root / build.CATALOG_JS).read_text(encoding="utf-8")
        prefix = build.JS_GLOBAL + " = "
        self.assertTrue(script.startswith(prefix) and script.endswith(";\n"))
        for forbidden in ("<", " ", " ", "\r"):
            self.assertNotIn(forbidden, script)
        self.assertEqual(script.count("\n"), 1)
        payload = json.loads(script[len(prefix):-2])
        self.assertEqual(payload, manifest.load_json(self.root / build.CATALOG_JSON))
        self.assertIn(hostile, [asset["title"] for asset in payload["assets"]])

    def test_index_lists_only_slideshow_assets_with_escaped_attributes(self):
        artist = fx.find_record(self.root, source_id="artists:x:ariiol_", kind="song_art")
        self.assertIn("</script>", artist["title"])
        self.assertEqual(self.run_main()[0], 0)
        markup = (self.root / build.INDEX_HTML).read_text(encoding="utf-8")
        self.assertTrue(markup.startswith("<!doctype html>\n<main>\n"))
        self.assertNotIn("@python-work-area", markup)
        self.assertNotIn("<script", markup)
        catalog = manifest.load_json(self.root / build.CATALOG_JSON)
        slides = Slides(markup).images
        self.assertEqual([img.get("data-id") for img in slides],
                         [asset["id"] for asset in catalog["assets"] if asset["slideshow"]])
        self.assertIn(artist["title"], slides[0].values())

    def test_repository_builds_need_previews_and_the_template(self):
        catalog = build.combine(self.root)
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaisesRegex(build.CatalogError, "Missing required input: data/thumbs.json"):
                build.attach_thumbnails(Path(empty), catalog, required=True)
            self.assertEqual(build.attach_thumbnails(Path(empty), catalog, required=False), catalog)
            with self.assertRaisesRegex(build.CatalogError, "Missing required input: templates/slideshow.html"):
                build.render_index(Path(empty), catalog, required=True)
            self.assertIsNone(build.render_index(Path(empty), catalog, required=False))

    def test_the_repository_root_requires_previews_template_and_renderer(self):
        with mock.patch.object(build, "REPO_ROOT", self.root):
            self.assertEqual(self.run_main()[0], 0)
            self.assertEqual(sorted(self.outputs()), sorted(OUTPUTS))
            for rel in (build.THUMBS_MANIFEST, fx.TEMPLATE_PATH):
                with self.subTest(missing=rel):
                    saved = (self.root / rel).read_bytes()
                    (self.root / rel).unlink()
                    code, stderr = self.run_main("--check")
                    self.assertEqual(code, 1)
                    self.assertIn(f"Missing required input: {rel}", stderr)
                    (self.root / rel).write_bytes(saved)
            with mock.patch.dict(sys.modules, {"render_slideshow": None}):
                code, stderr = self.run_main("--check")
            self.assertEqual(code, 1)
            self.assertIn("render_slideshow", stderr)

    def test_fixture_roots_build_without_previews_or_template(self):
        bare = fx.copy_contract(self.base / "bare")
        self.assertEqual(self.run_main("--verify", root=bare)[0], 0)
        self.assertEqual(sorted(self.outputs(bare)), [build.CATALOG_JS, build.CATALOG_JSON])
        catalog = manifest.load_json(bare / build.CATALOG_JSON)
        self.assertTrue(all(asset["thumb"] is None for asset in catalog["assets"]))
        self.assertEqual(self.run_main("--check", root=bare)[0], 0)

    def test_previews_are_checked_when_attached(self):
        catalog = build.combine(self.root)
        listing = thumbs.read_manifest(self.root)["thumbnails"]
        entry = listing[fx.find_record(self.root, kind="gameplay_screenshot")["sha256"]]
        file = self.root / entry["path"]
        data = bytearray(file.read_bytes())
        data[-1] ^= 1
        file.write_bytes(bytes(data))
        build.attach_thumbnails(self.root, catalog)  # same size: only --verify hashes previews
        with self.assertRaisesRegex(build.CatalogError, "Thumbnail SHA-256 mismatch"):
            build.attach_thumbnails(self.root, catalog, verify=True)
        file.unlink()
        with self.assertRaisesRegex(build.CatalogError, "build_thumbnails"):
            build.attach_thumbnails(self.root, catalog)
        value = manifest.load_json(self.root / build.THUMBS_MANIFEST)
        sha256 = next(iter(value["thumbnails"]))
        for change in ({"path": "assets/public/wikis/miraheze/x.webp"}, {"bytes": 0}, {"extra": 1}):
            with self.subTest(change=change):
                broken = copy.deepcopy(value)
                broken["thumbnails"][sha256].update(change)
                (self.root / build.THUMBS_MANIFEST).write_text(json.dumps(broken), encoding="utf-8")
                with self.assertRaisesRegex(build.CatalogError, "data/thumbs.json"):
                    build.attach_thumbnails(self.root, catalog)
        del value["thumbnails"][sha256]
        (self.root / build.THUMBS_MANIFEST).write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(build.CatalogError, "has no preview for"):
            build.attach_thumbnails(self.root, catalog)


class CommandLineTests(Base):
    def test_documented_command_line_runs_isolated_and_fails_on_stale_output(self):
        result = fx.run_script("build_catalog", "--root", str(self.root), "--verify")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["unique_files"], 10)
        result = fx.run_script("build_catalog", "--root", str(self.root), "--verify", "--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.root / build.CATALOG_JS).write_text("window.DYNAMIX_CATALOG = {};\n", encoding="utf-8")
        result = fx.run_script("build_catalog", "--root", str(self.root), "--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Out-of-date generated file: data/catalog.js", result.stderr)
        fx.update_record(self.root, fx.find_record(self.root, kind="reference")["id"], page_url="javascript:x")
        result = fx.run_script("build_catalog", "--root", str(self.root))
        self.assertEqual(result.returncode, 1)
        self.assertIn("page_url", result.stderr)


if __name__ == "__main__":
    unittest.main()
