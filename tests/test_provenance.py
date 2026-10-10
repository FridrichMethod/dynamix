"""Catalog provenance: every source record survives deduplication with its own evidence, credits stay role-separated
and merge as ordered unions, the primary record follows the contract's priority, and source-reported mismatches,
revisions and rights stay attached to the record that carried them."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures/catalog"))

import catalog_fixture as fx  # noqa: E402
import build_catalog as build  # noqa: E402
from lib import schema  # noqa: E402

PROVENANCE_KEYS = ["asset_id", *schema.PROVENANCE_FIELDS]


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = fx.copy_contract(Path(self.temp.name) / "repo")

    def records(self):
        return [record for family in schema.FAMILIES for record in fx.load_manifest(self.root, family)["assets"]]

    def entry(self, catalog, sha256):
        return next(asset for asset in catalog["assets"] if asset["sha256"] == sha256)

    def test_every_source_record_appears_once_with_its_own_fields(self):
        catalog = build.combine(self.root)
        records = {record["id"]: record for record in self.records()}
        seen = [p["asset_id"] for asset in catalog["assets"] for p in asset["provenance"]]
        self.assertEqual(sorted(seen), sorted(records))
        names = {source["id"]: source["name"] for source in catalog["sources"]}
        for asset in catalog["assets"]:
            for entry in asset["provenance"]:
                record = records[entry["asset_id"]]
                self.assertEqual(list(entry), PROVENANCE_KEYS)
                self.assertEqual(record["sha256"], asset["sha256"])
                self.assertEqual((entry["source_name"], entry["family"]),
                                 (names[record["source_id"]], schema.source_family(record["source_id"])))
                for field in schema.PROVENANCE_FIELDS[3:]:
                    self.assertEqual(entry[field], record.get(field), field)

    def test_primary_record_follows_family_then_source_then_asset_id(self):
        cosmic = fx.find_record(self.root, source_id="wikis:miraheze", title="Cosmic Pulse")
        archive = fx.add_record(self.root, fx.copy_record(self.root, cosmic, "archives:fixture-scan"))
        official = fx.add_record(self.root, fx.copy_record(self.root, cosmic, "official:website"))
        catalog = build.combine(self.root, verify=True)
        entry = self.entry(catalog, cosmic["sha256"])
        self.assertEqual([p["source_id"] for p in entry["provenance"]],
                         ["official:website", "wikis:fandom", "wikis:miraheze", "archives:fixture-scan"])
        self.assertEqual((entry["id"], entry["source_id"], entry["family"]),
                         (official["id"], "official:website", "official"))
        self.assertEqual(entry["provenance"][-1]["asset_id"], archive["id"])
        self.assertEqual(entry["url"], schema.catalog_url(official["path"]))
        keys = [schema.provenance_sort_key(p) for p in entry["provenance"]]
        self.assertEqual(keys, sorted(keys))

    def test_illustrators_and_composers_stay_separate_and_merge_in_provenance_order(self):
        cosmic = fx.find_record(self.root, source_id="wikis:miraheze", title="Cosmic Pulse")
        artist = fx.add_record(self.root, fx.copy_record(
            self.root, cosmic, "artists:x:fixture_painter", illustrators=["ariiol", "Second Painter"],
            song_titles=["Cosmic Pulse"], song_ids=cosmic["song_ids"], mapping_status="confirmed_primary",
            mapping_evidence=["https://example.org/x-fixture_painter/post"],
            credit_evidence=[{"role": "illustrator", "name": "Second Painter", "url": None, "field": "caption",
                              "quote": "art: Second Painter"}]))
        catalog = build.combine(self.root)
        entry = self.entry(catalog, cosmic["sha256"])
        self.assertEqual(entry["provenance"][0]["asset_id"], artist["id"])
        self.assertEqual(entry["illustrators"], ["ariiol", "Second Painter"])
        self.assertEqual(entry["composers"], ["Yunosuke"])  # only the wikis credit a composer
        self.assertEqual(entry["provenance"][0]["composers"], [])
        self.assertEqual(entry["provenance"][0]["credit_evidence"], artist["credit_evidence"])
        self.assertIsNone(entry["provenance"][1]["credit_evidence"])
        self.assertEqual(entry["collections"], ["Event"])
        self.assertEqual(entry["song_ids"], cosmic["song_ids"])
        for field in build.MERGED_FIELDS:
            self.assertEqual(entry[field], schema.ordered_union(p[field] for p in entry["provenance"]), field)

    def test_source_reported_mismatches_stay_with_their_record(self):
        catalog = build.combine(self.root)
        cosmic = fx.find_record(self.root, source_id="wikis:miraheze", title="Cosmic Pulse")
        entry = self.entry(catalog, cosmic["sha256"])
        by_source = {p["source_id"]: p for p in entry["provenance"]}
        self.assertEqual((by_source["wikis:fandom"]["comparisons"]["sha1_match"],
                          by_source["wikis:miraheze"]["comparisons"]["sha1_match"]), (False, True))
        self.assertNotEqual(by_source["wikis:fandom"]["source_reported"]["sha1"], entry["sha1"])
        self.assertIn("Fandom API", by_source["wikis:fandom"]["notes"])
        self.assertIsNone(by_source["wikis:miraheze"]["notes"])
        webp = next(asset for asset in catalog["assets"] if asset["format"] == "WEBP")
        self.assertIs(webp["transformed"], True)
        self.assertEqual(webp["quality"]["source_variant"], "unknown")

    def test_revisions_rights_and_mapping_evidence_are_kept(self):
        catalog = build.combine(self.root)
        old = next(asset for asset in catalog["assets"] if asset.get("upstream_status") == "superseded")
        self.assertEqual((old["provenance"][0]["upstream_status"], old["provenance"][0]["superseded_by"]),
                         ("superseded", old["superseded_by"]))
        self.assertEqual(old["upstream_revision"], 1000)
        crop = next(asset for asset in catalog["assets"] if asset["title"] == "Cosmic Pulse (crop)")
        self.assertEqual(crop["mapping_status"], "inferred")
        self.assertEqual(crop["provenance"][0]["mapping_evidence"], crop["mapping_evidence"])
        for asset in catalog["assets"]:
            for entry in asset["provenance"]:
                self.assertIsNone(entry["rights"]["license"], "a license is never invented")
                self.assertEqual(set(entry["rights"]), set(schema.RIGHTS_FIELDS))


if __name__ == "__main__":
    unittest.main()
