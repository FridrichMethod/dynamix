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
                self.assertEqual(
                    (entry["source_name"], entry["family"]),
                    (names[record["source_id"]], schema.source_family(record["source_id"])),
                )
                for field in schema.PROVENANCE_FIELDS[3:]:
                    self.assertEqual(entry[field], record.get(field), field)

    def test_primary_record_follows_family_then_preferred_source_then_variant_then_ids(self):
        cosmic = fx.find_record(self.root, source_id="wikis:miraheze", title="Cosmic Pulse")
        archive = fx.add_record(self.root, fx.copy_record(self.root, cosmic, "archives:fixture-scan"))
        official = fx.add_record(self.root, fx.copy_record(self.root, cosmic, "official:website"))
        catalog = build.combine(self.root, verify=True)
        entry = self.entry(catalog, cosmic["sha256"])
        self.assertEqual(
            [p["source_id"] for p in entry["provenance"]],
            ["official:website", "wikis:miraheze", "wikis:fandom", "archives:fixture-scan"],
        )
        self.assertEqual(
            (entry["id"], entry["source_id"], entry["family"]), (official["id"], "official:website", "official")
        )
        self.assertEqual(entry["provenance"][-1]["asset_id"], archive["id"])
        self.assertEqual(entry["url"], schema.catalog_url(official["path"]))
        keys = [schema.provenance_sort_key(p) for p in entry["provenance"]]
        self.assertEqual(keys, sorted(keys))
        schema.validate_catalog(catalog)

    def test_miraheze_is_primary_when_both_english_wikis_hold_the_bytes(self):
        """The frozen rule ranks Miraheze (current wiki) before Fandom (legacy) although 'wikis:fandom' sorts first by
        ID, and before an unlisted wiki whatever its ID or rendition; the catalog shows the Miraheze title and path."""
        cosmic = fx.find_record(self.root, source_id="wikis:miraheze", title="Cosmic Pulse")
        fx.update_record(
            self.root,
            fx.find_record(self.root, source_id="wikis:fandom", title="Cosmic Pulse")["id"],
            title="Cosmic Pulse (Fandom caption)",
            mapping_status="inferred",
        )
        atwiki = fx.add_record(
            self.root, fx.copy_record(self.root, cosmic, "wikis:atwiki-ja", title="コズミックパルス")
        )
        catalog = build.combine(self.root, verify=True)
        schema.validate_catalog(catalog)
        entry = self.entry(catalog, cosmic["sha256"])
        self.assertEqual(
            [p["source_id"] for p in entry["provenance"]], ["wikis:miraheze", "wikis:fandom", "wikis:atwiki-ja"]
        )
        self.assertEqual(
            (entry["id"], entry["title"], entry["path"], entry["url"]),
            (cosmic["id"], "Cosmic Pulse", cosmic["path"], schema.catalog_url(cosmic["path"])),
        )
        self.assertEqual(entry["provenance"][-1]["asset_id"], atwiki["id"])

    def test_an_artist_record_is_primary_over_every_wiki_copy(self):
        cosmic = fx.find_record(self.root, source_id="wikis:miraheze", title="Cosmic Pulse")
        artist = fx.add_record(
            self.root,
            fx.copy_record(
                self.root,
                cosmic,
                "artists:x:fixture_painter",
                title="Cosmic Pulse key visual (artist post)",
                quality={
                    "source_variant": "preview",
                    "origin_status": "artist_post",
                    "master_status": "unverified",
                    "notes": "platform rendition",
                },
            ),
        )
        catalog = build.combine(self.root, verify=True)
        schema.validate_catalog(catalog)
        entry = self.entry(catalog, cosmic["sha256"])
        self.assertEqual(
            [p["source_id"] for p in entry["provenance"]],
            ["artists:x:fixture_painter", "wikis:miraheze", "wikis:fandom"],
        )
        self.assertEqual(
            (entry["id"], entry["family"], entry["title"], entry["url"]),
            (artist["id"], "artists", "Cosmic Pulse key visual (artist post)", schema.catalog_url(artist["path"])),
        )
        self.assertEqual(entry["quality"]["source_variant"], "preview", "family priority comes before the rendition")

    def test_within_a_family_the_rendition_comes_before_the_source_id(self):
        """09 Primary record: Miraheze, then Fandom, then every other source, tied; then source_variant; then source
        ID; then asset ID. So official:x's original is primary over official:facebook's preview."""
        cosmic = fx.find_record(self.root, source_id="wikis:miraheze", title="Cosmic Pulse")
        quality = {"origin_status": "official", "master_status": "unverified", "notes": ""}
        preview = fx.add_record(
            self.root,
            fx.copy_record(self.root, cosmic, "official:facebook", quality={"source_variant": "preview", **quality}),
        )
        unknown = fx.add_record(
            self.root,
            fx.copy_record(self.root, cosmic, "official:c4cat", quality={"source_variant": "unknown", **quality}),
        )
        original = fx.add_record(
            self.root,
            fx.copy_record(self.root, cosmic, "official:x", quality={"source_variant": "site_original", **quality}),
        )
        entry = self.entry(build.combine(self.root), cosmic["sha256"])
        self.assertEqual(
            [p["asset_id"] for p in entry["provenance"][:3]], [original["id"], preview["id"], unknown["id"]]
        )
        self.assertEqual(entry["id"], original["id"])

    def test_the_primary_choice_does_not_depend_on_manifest_order(self):
        cosmic = fx.find_record(self.root, source_id="wikis:miraheze", title="Cosmic Pulse")
        for source_id in ("official:x", "official:facebook", "archives:fixture-scan", "wikis:atwiki-ja"):
            fx.add_record(self.root, fx.copy_record(self.root, cosmic, source_id))
        manifests = build.load_manifests(self.root)
        expected = build.combine(self.root, manifests=manifests)
        reversed_manifests = {
            family: {**value, "sources": value["sources"][::-1], "assets": value["assets"][::-1]}
            for family, value in manifests.items()
        }
        self.assertEqual(build.combine(self.root, manifests=reversed_manifests), expected)
        entry = self.entry(expected, cosmic["sha256"])
        self.assertEqual(
            [p["source_id"] for p in entry["provenance"]],
            [
                "official:facebook",
                "official:x",
                "wikis:miraheze",
                "wikis:fandom",
                "wikis:atwiki-ja",
                "archives:fixture-scan",
            ],
        )
        group = [record for record in self.records() if record["sha256"] == cosmic["sha256"]]
        self.assertEqual(min(group, key=schema.provenance_sort_key)["id"], entry["id"])

    def test_illustrators_and_composers_stay_separate_and_merge_in_provenance_order(self):
        cosmic = fx.find_record(self.root, source_id="wikis:miraheze", title="Cosmic Pulse")
        artist = fx.add_record(
            self.root,
            fx.copy_record(
                self.root,
                cosmic,
                "artists:x:fixture_painter",
                illustrators=["ariiol", "Second Painter"],
                song_titles=["Cosmic Pulse"],
                song_ids=cosmic["song_ids"],
                mapping_status="confirmed_primary",
                mapping_evidence=["https://example.org/x-fixture_painter/post"],
                credit_evidence=[
                    {
                        "role": "illustrator",
                        "name": "Second Painter",
                        "url": None,
                        "field": "caption",
                        "quote": "art: Second Painter",
                    }
                ],
            ),
        )
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
        self.assertEqual(
            (
                by_source["wikis:fandom"]["comparisons"]["sha1_match"],
                by_source["wikis:miraheze"]["comparisons"]["sha1_match"],
            ),
            (False, True),
        )
        self.assertNotEqual(by_source["wikis:fandom"]["source_reported"]["sha1"], entry["sha1"])
        self.assertIn("Fandom API", by_source["wikis:fandom"]["notes"])
        self.assertIsNone(by_source["wikis:miraheze"]["notes"])
        webp = next(asset for asset in catalog["assets"] if asset["format"] == "WEBP")
        self.assertIs(webp["transformed"], True)
        self.assertEqual(webp["quality"]["source_variant"], "unknown")

    def test_revisions_rights_and_mapping_evidence_are_kept(self):
        catalog = build.combine(self.root)
        old = next(asset for asset in catalog["assets"] if asset.get("upstream_status") == "superseded")
        self.assertEqual(
            (old["provenance"][0]["upstream_status"], old["provenance"][0]["superseded_by"]),
            ("superseded", old["superseded_by"]),
        )
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
