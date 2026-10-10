"""The UI fixture (tests/fixtures/ui/) is a complete mini site data set in exactly the frozen catalog shape: the browser
smoke and the slideshow renderer test against it, so a drift from docs/implementation/09-frozen-contracts.md here
would let the interface pass against data the catalog builder never writes."""

import hashlib
import io
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import quote

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from lib import paths, schema  # noqa: E402 - scripts/lib is the executable form of the frozen contract
FIXTURE = ROOT / "tests/fixtures/ui"
REPOSITORY = "https://github.com/FridrichMethod/dynamix"
TOP_LEVEL = {"schema_version", "owner", "game", "repository", "snapshots", "sources", "assets", "failures", "summary"}
SUMMARY = {"source_count", "source_asset_records", "unique_files", "gallery_images", "slideshow_images", "exact_duplicate_records",
           "by_family", "by_kind", "failure_count", "downloaded_bytes", "site_original_bytes", "archive_only_bytes"}
SOURCE_REQUIRED = {"id", "name", "family", "url", "platform", "status", "checked_at", "notes"}
SOURCE_OPTIONAL = {"api_url", "owner_evidence", "crawl_scope", "rate_policy", "discovery_complete", "candidate_count", "asset_count",
                   "failure_count", "evidence_level"}
ASSET_REQUIRED = {"id", "source_id", "candidate_id", "title", "kind", "game", "page_url", "download_url", "resolved_url", "path",
                  "original_filename", "bytes", "sha256", "sha1", "format", "mime", "width", "height", "fetched_at", "illustrators",
                  "composers", "song_titles", "song_ids", "collections", "mapping_status", "mapping_evidence", "quality", "rights",
                  "source_reported", "download_observed", "comparisons"}
ASSET_OPTIONAL = {"credit_evidence", "variant_group", "variant_role", "upstream_status", "superseded_by", "game_associations", "notes",
                  "upstream_revision", "transformed"}
CATALOG_FIELDS = {"family", "source_name", "url", "thumb", "gallery", "slideshow", "site_original", "archive_url", "provenance", "variants"}
PROVENANCE = {"source_id", "source_name", "family", "page_url", "download_url", "resolved_url", "quality", "rights", "mapping_status",
              "mapping_evidence", "credit_evidence", "illustrators", "composers", "song_titles", "song_ids", "collections",
              "source_reported", "comparisons", "upstream_status", "superseded_by", "notes"}
PROVENANCE_OPTIONAL = {"asset_id"}  # scripts/lib/schema.py PROVENANCE_SPEC: the source record's own asset ID
FAILURE = {"id", "source_id", "candidate_id", "phase", "url", "attempted_at", "attempts", "error_class", "http_status", "retriable", "manual", "message"}
QUALITY = {"source_variant", "origin_status", "master_status", "notes"}
RIGHTS = {"holder", "creator", "url", "license", "restrictions"}
REPORTED = {"url", "mime", "bytes", "width", "height", "sha1", "timestamp", "revision"}
OBSERVED = {"content_type", "sniffed_format", "bytes", "width", "height", "sha1", "sha256"}
COMPARISONS = {"sha1_match", "bytes_match", "dimensions_match", "format_match"}
KINDS = ["song_art", "pack_cover", "character", "background", "part", "promotional", "store_screenshot", "gameplay_screenshot", "process", "reference"]
SITE_KINDS = {"song_art", "pack_cover", "character", "background", "part", "promotional", "process"}
FAMILY_PRIORITY = ["artists", "official", "wikis", "archives"]
PATH = re.compile(r"assets/public/(artists|official|wikis|archives)/[a-z0-9-]+/[A-Za-z0-9._-]+--[0-9a-f]{12}\.(png|jpg|webp|gif|pdf)")


def load(name):
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


def contract_js(catalog):
    """The frozen catalog.js encoding (09-frozen-contracts.md, "Catalog"; the DEEMO reference form): `window.DYNAMIX_CATALOG = `
    + compact JSON (separators ",", ":"; ensure_ascii=False) with every "<" written as \\u003c and U+2028/U+2029 escaped
    + ";\\n"."""
    compact = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    return "window.DYNAMIX_CATALOG = " + compact.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029") + ";\n"


class FixtureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load("catalog.json")
        cls.empty = load("catalog-empty.json")

    def test_generator_reproduces_the_committed_fixture(self):
        result = subprocess.run([sys.executable, "-I", str(FIXTURE / "build_fixture.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fixture_stays_small(self):
        total = sum(path.stat().st_size for path in FIXTURE.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
        self.assertLess(total, 300_000)

    def test_catalog_js_is_the_frozen_reference_encoding(self):
        for name in ("catalog", "catalog-empty"):
            with self.subTest(fixture=name):
                data = load(f"{name}.json")
                script = (FIXTURE / f"{name}.js").read_text(encoding="utf-8")
                self.assertEqual(script, contract_js(data))
                self.assertNotIn("<", script.removeprefix("window.DYNAMIX_CATALOG = "))
                self.assertNotIn("\u2028", script)
                self.assertNotIn("\u2029", script)
                self.assertEqual(json.loads(script.removeprefix("window.DYNAMIX_CATALOG = ").removesuffix(";\n")), data)
                self.assertEqual((FIXTURE / f"{name}.json").read_text(encoding="utf-8"), json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def test_top_level_and_summary_shape(self):
        for catalog in (self.catalog, self.empty):
            self.assertEqual(set(catalog), TOP_LEVEL)
            self.assertEqual((catalog["schema_version"], catalog["owner"], catalog["game"], catalog["repository"]), (1, "FridrichMethod", "Dynamix", REPOSITORY))
            self.assertEqual(set(catalog["summary"]), SUMMARY)
        self.assertEqual((self.empty["assets"], self.empty["sources"], self.empty["failures"]), ([], [], []))

    def test_sources_and_failures_shape(self):
        for source in self.catalog["sources"]:
            with self.subTest(source=source["id"]):
                self.assertTrue(SOURCE_REQUIRED <= set(source) <= SOURCE_REQUIRED | SOURCE_OPTIONAL)
                self.assertRegex(source["id"], r"^(artists|official|wikis|archives):[a-z0-9-]+(:[a-z0-9_-]+)?$")
                self.assertEqual(source["id"].split(":")[0], source["family"])
        for failure in self.catalog["failures"]:
            self.assertEqual(set(failure), FAILURE)
            self.assertEqual(failure["id"], f"{failure['candidate_id']}:{failure['phase']}")

    def test_assets_follow_the_contract(self):
        sources = {source["id"]: source for source in self.catalog["sources"]}
        for asset in self.catalog["assets"]:
            with self.subTest(asset=asset["title"]):
                keys = set(asset)
                self.assertTrue(ASSET_REQUIRED | CATALOG_FIELDS <= keys, sorted(ASSET_REQUIRED | CATALOG_FIELDS - keys))
                self.assertTrue(keys <= ASSET_REQUIRED | ASSET_OPTIONAL | CATALOG_FIELDS, sorted(keys - ASSET_REQUIRED - ASSET_OPTIONAL - CATALOG_FIELDS))
                self.assertEqual(asset["id"], f"{asset['source_id']}:{asset['sha256'][:16]}")
                self.assertEqual(asset["game"], "dynamix")
                self.assertIn(asset["kind"], KINDS)
                self.assertRegex(asset["path"], PATH)
                self.assertTrue(asset["path"].endswith(asset["sha256"][:12] + "." + asset["path"].rsplit(".", 1)[1]))
                self.assertEqual(asset["url"], quote(asset["path"], safe="/"))
                self.assertEqual(asset["archive_url"], f"{REPOSITORY}/raw/main/{asset['url']}")
                self.assertEqual(asset["family"], sources[asset["source_id"]]["family"])
                self.assertEqual(asset["source_name"], sources[asset["source_id"]]["name"])
                self.assertEqual(set(asset["quality"]), QUALITY)
                self.assertEqual(set(asset["rights"]), RIGHTS)
                self.assertEqual(set(asset["source_reported"]), REPORTED)
                self.assertEqual(set(asset["download_observed"]), OBSERVED)
                self.assertEqual(set(asset["comparisons"]), COMPARISONS)
                self.assertTrue(all(value in (True, False, None) for value in asset["comparisons"].values()))
                raster = asset["format"] != "PDF"
                self.assertEqual(asset["gallery"], raster and asset["kind"] != "reference")
                self.assertEqual(asset["site_original"], asset["kind"] in SITE_KINDS and raster)
                self.assertEqual(asset["slideshow"], asset["gallery"] and asset["kind"] in {"song_art", "pack_cover", "promotional", "character"}
                                 and max(asset["width"], asset["height"]) >= 1000)
                for record in asset["provenance"]:
                    self.assertTrue(PROVENANCE <= set(record) <= PROVENANCE | PROVENANCE_OPTIONAL, "provenance records carry the contract's fields")
                    self.assertEqual(record["asset_id"], f"{record['source_id']}:{asset['sha256'][:16]}")
                self.assertEqual(asset["provenance"][0]["source_id"], asset["source_id"], "the primary record comes first")
                keys = [(FAMILY_PRIORITY.index(record["family"]), record["source_id"], record["asset_id"]) for record in asset["provenance"]]
                self.assertEqual(keys, sorted(keys), "provenance is ordered by family priority, source ID, asset ID")

    def test_files_match_their_records(self):
        for asset in self.catalog["assets"]:
            with self.subTest(asset=asset["title"]):
                raw = (FIXTURE / asset["path"]).read_bytes()
                self.assertEqual(len(raw), asset["bytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), asset["sha256"])
                self.assertEqual(hashlib.sha1(raw).hexdigest(), asset["sha1"])
                if asset["format"] == "PDF":
                    self.assertTrue(raw.startswith(b"%PDF-"))
                    self.assertIsNone(asset["width"])
                    self.assertIsNone(asset["thumb"])
                else:
                    with Image.open(io.BytesIO(raw)) as image:
                        self.assertEqual((image.format, image.width, image.height), (asset["format"], asset["width"], asset["height"]))
                if asset["thumb"]:
                    self.assertEqual(asset["thumb"], f"assets/thumbs/{asset['sha256'][:16]}.webp")
                    with Image.open(FIXTURE / asset["thumb"]) as preview:
                        self.assertEqual(preview.format, "WEBP")
                        self.assertEqual(max(preview.size), min(480, max(asset["width"], asset["height"])))
                else:
                    self.assertTrue(asset["format"] == "PDF" or max(asset["width"], asset["height"]) <= 480, "previews are never enlarged")

    def test_order_variants_and_summary_reconcile(self):
        assets = self.catalog["assets"]
        self.assertEqual([KINDS.index(asset["kind"]) for asset in assets], sorted(KINDS.index(asset["kind"]) for asset in assets))
        ids = {asset["id"] for asset in assets}
        for asset in assets:
            self.assertEqual(asset["variants"], sorted(asset["variants"]))
            self.assertTrue(set(asset["variants"]) <= ids - {asset["id"]})
            for record in asset["provenance"]:
                if record["superseded_by"]:
                    self.assertEqual(record["upstream_status"], "superseded")
                    self.assertIn(record["superseded_by"], ids)
        summary = self.catalog["summary"]
        records = [record for asset in assets for record in asset["provenance"]]
        self.assertEqual(summary["unique_files"], len(assets))
        self.assertEqual(summary["source_asset_records"], len(records))
        self.assertEqual(summary["exact_duplicate_records"], len(records) - len(assets))
        self.assertEqual(summary["gallery_images"], sum(asset["gallery"] for asset in assets))
        self.assertEqual(summary["slideshow_images"], sum(asset["slideshow"] for asset in assets))
        self.assertEqual(summary["failure_count"], len(self.catalog["failures"]))
        self.assertEqual(summary["downloaded_bytes"], summary["site_original_bytes"] + summary["archive_only_bytes"])
        self.assertEqual(sum(summary["by_family"].values()), len(records))
        self.assertEqual(sum(summary["by_kind"].values()), len(assets))
        self.assertEqual(sum(source["asset_count"] for source in self.catalog["sources"]), len(records))

    def test_fixture_covers_the_cases_the_interface_must_handle(self):
        assets = self.catalog["assets"]
        titles = {asset["title"] for asset in assets}
        self.assertIn("交差して歪む過去と未来", titles)
        self.assertTrue(any("<img src=x onerror=alert(1)>" in asset["title"] and "</script>" in asset["title"] for asset in assets))
        self.assertTrue(any("<img src=x onerror=alert(1)>" in name for asset in assets for name in asset["illustrators"]))
        self.assertTrue(any(asset["kind"] == "song_art" and not asset["illustrators"] and not asset["composers"] for asset in assets), "unknown credits")
        self.assertTrue(any(len(asset["provenance"]) > 1 and asset["provenance"][0]["quality"]["origin_status"] == "artist_post"
                            and any(record["family"] == "wikis" for record in asset["provenance"]) for asset in assets), "artist original with wiki duplicates")
        self.assertTrue(any(asset["variants"] for asset in assets), "a variant pair")
        self.assertTrue(any(asset["kind"] == "character" for asset in assets))
        self.assertTrue(any(not asset["site_original"] and asset["kind"] == "reference" and asset["format"] != "PDF" for asset in assets))
        self.assertTrue(any(not asset["site_original"] and asset["format"] == "PDF" for asset in assets))
        self.assertTrue(any(not asset["site_original"] and asset["gallery"] for asset in assets), "an archive-only gallery image")
        self.assertTrue(any(asset["format"] != "PDF" and max(asset["width"], asset["height"]) <= 200 for asset in assets), "a 200 px file")
        self.assertTrue(any(value is False for asset in assets for record in asset["provenance"] for value in record["comparisons"].values()))
        self.assertTrue(any(entry == "javascript:alert(6)" for asset in assets for record in asset["provenance"] for entry in record["mapping_evidence"]),
                        "a script-looking evidence string (valid text, never a link)")
        self.assertTrue(any(entry["role"] == "other" for asset in assets for record in asset["provenance"] for entry in record["credit_evidence"] or []),
                        "credit evidence with the role 'other'")
        self.assertTrue(any(len({tuple(record["collections"]) for record in asset["provenance"]}) > 1 for asset in assets),
                        "records of one file that name different packs")

    def test_contract_library_accepts_the_fixture(self):
        for name in ("catalog.json", "catalog-empty.json"):
            with self.subTest(fixture=name):
                schema.validate_catalog(load(name))

    def test_song_ids_follow_the_contract_rules(self):
        """Song IDs are assigned over the fixture's whole title set (as the reconciled song index does); a title whose slug
        loses characters carries the 8-hex title hash; an unmapped record links no song; the catalog lists the
        ordered union of its records' song IDs and collections."""
        titles = sorted({title for asset in self.catalog["assets"] for title in asset["song_titles"]})
        expected = dict(zip(titles, paths.assign_song_ids(titles)))
        for asset in self.catalog["assets"]:
            for record in asset["provenance"]:
                with self.subTest(asset=asset["title"], source=record["source_id"]):
                    if record["mapping_status"] == "unmapped":
                        self.assertEqual(record["song_ids"], [])
                    else:
                        self.assertEqual(record["song_ids"], [expected[title] for title in record["song_titles"]])
            for field in ("song_ids", "collections"):
                self.assertEqual(asset[field], schema.ordered_union(record[field] for record in asset["provenance"]))
        crossing = next(asset for asset in self.catalog["assets"] if asset["title"] == "交差して歪む過去と未来")
        self.assertRegex(crossing["song_ids"][0], r"^song:file-[0-9a-f]{8}$")
        self.assertEqual(crossing["song_ids"], [paths.song_id_base(crossing["title"])])


if __name__ == "__main__":
    unittest.main()
