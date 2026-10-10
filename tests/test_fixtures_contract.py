"""The committed contract fixture (tests/fixtures/contract) validates, reconciles, matches its bytes, covers every
required scenario and is reproduced byte-for-byte by its generator."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import manifest, media, paths, schema  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/contract"
GENERATOR = FIXTURE / "make_fixture.py"
GENERATED_DIRS = ("data", "assets")
MAX_MEDIA_BYTES = 5000


def tree(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for top in GENERATED_DIRS for path in sorted((root / top).rglob("*")) if path.is_file()}


class FixtureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifests = {family: manifest.load_manifest(FIXTURE, family, missing_ok=False)
                         for family in schema.FAMILIES}
        cls.assets = [asset for value in cls.manifests.values() for asset in value["assets"]]
        cls.by_id = {asset["id"]: asset for asset in cls.assets}
        cls.discoveries = {}
        for path in sorted((FIXTURE / "data/discovery").glob("*.json")):
            snapshot = manifest.load_json(path)
            cls.discoveries[snapshot["source_id"]] = snapshot
        cls.song_index = manifest.load_json(FIXTURE / "data/sources/song-index.json")

    def candidates(self, source_id, disposition=None):
        return [c for c in self.discoveries[source_id]["candidates"]
                if disposition is None or c["disposition"] == disposition]

    def find(self, **match):
        return [asset for asset in self.assets if all(asset.get(key) == value for key, value in match.items())]

    # -- contract validity --

    def test_every_json_file_validates_and_reconciles(self):
        totals = {}
        for source_id, snapshot in self.discoveries.items():
            self.assertEqual((FIXTURE / paths.discovery_relpath(source_id)).is_file(), True, source_id)
            schema.validate_discovery(snapshot, source_id)
        for family, value in self.manifests.items():
            snapshots = {sid: snap for sid, snap in self.discoveries.items() if schema.source_family(sid) == family}
            totals.update(schema.reconcile(value, snapshots, other_assets=self.assets))
        for source_id, counts in totals.items():
            self.assertEqual(sum(counts.values()), len(self.discoveries[source_id]["candidates"]), source_id)
        self.assertEqual(totals["wikis:miraheze"], {"downloaded": 4, "duplicate_bytes": 1, "excluded": 2,
                                                    "failed": 0, "blocked": 0, "pending": 1})
        schema.validate_song_index(self.song_index, self.by_id)
        json_files = sorted(p.relative_to(FIXTURE).as_posix() for p in (FIXTURE / "data").rglob("*.json"))
        expected = sorted([paths.manifest_relpath(f) for f in schema.FAMILIES] + ["data/sources/song-index.json"]
                          + [paths.discovery_relpath(sid) for sid in self.discoveries])
        self.assertEqual(json_files, expected)

    def test_files_match_records_and_have_no_orphans(self):
        for asset in self.assets:
            info = media.verify_asset_file(FIXTURE, asset)
            self.assertEqual(asset["download_observed"]["sha256"], info["sha256"])
        on_disk = sorted(p.relative_to(FIXTURE).as_posix() for p in (FIXTURE / "assets").rglob("*") if p.is_file())
        self.assertEqual(on_disk, sorted(asset["path"] for asset in self.assets))
        for rel in on_disk:
            self.assertLess((FIXTURE / rel).stat().st_size, MAX_MEDIA_BYTES, rel)
        self.assertEqual(paths.casefold_collisions(on_disk), [])

    def test_song_index_matches_asset_mappings(self):
        songs = {song["id"]: song for song in self.song_index["songs"]}
        for asset in self.assets:
            for song_id in asset["song_ids"]:
                self.assertIn(asset["id"], songs[song_id]["artwork_asset_ids"])
        for song in songs.values():
            for asset_id in song["artwork_asset_ids"]:
                self.assertIn(song["id"], self.by_id[asset_id]["song_ids"])

    # -- required scenarios --

    def test_a_exact_duplicate_across_wikis_with_fandom_api_mismatch(self):
        miraheze = self.find(source_id="wikis:miraheze", title="Cosmic Pulse")[0]
        fandom = self.find(source_id="wikis:fandom", title="Cosmic Pulse")[0]
        self.assertEqual(miraheze["sha256"], fandom["sha256"])
        self.assertNotEqual(miraheze["id"], fandom["id"])
        self.assertEqual((FIXTURE / miraheze["path"]).read_bytes(), (FIXTURE / fandom["path"]).read_bytes())
        self.assertNotEqual(fandom["source_reported"]["sha1"], fandom["sha1"])
        self.assertNotEqual(fandom["source_reported"]["bytes"], fandom["bytes"])
        self.assertEqual((fandom["comparisons"]["sha1_match"], fandom["comparisons"]["bytes_match"]), (False, False))
        self.assertEqual((miraheze["comparisons"]["sha1_match"], miraheze["comparisons"]["bytes_match"]), (True, True))
        duplicate = self.candidates("wikis:miraheze", "duplicate_bytes")[0]
        self.assertEqual(duplicate["asset_id"], miraheze["id"])

    def test_b_distinct_crop_shares_variant_group(self):
        group = self.find(variant_group="song:cosmic-pulse/cover")
        self.assertEqual(len({asset["sha256"] for asset in group}), 3)
        crop = self.find(variant_role="crop")[0]
        full = self.find(source_id="wikis:miraheze", title="Cosmic Pulse")[0]
        self.assertNotEqual(crop["sha256"], full["sha256"])
        self.assertLess(crop["width"], full["width"])

    def test_c_artist_original_larger_than_wiki_copy(self):
        artist = self.find(source_id="artists:x:ariiol_", kind="song_art")[0]
        wiki = self.find(source_id="wikis:miraheze", title="Cosmic Pulse")[0]
        self.assertEqual((artist["mapping_status"], artist["quality"]["origin_status"]),
                         ("confirmed_primary", "artist_post"))
        self.assertEqual(artist["quality"]["master_status"], "unverified")
        self.assertGreater(artist["width"] * artist["height"], wiki["width"] * wiki["height"])
        self.assertTrue(schema.is_slideshow(artist))
        self.assertFalse(schema.is_slideshow(wiki))
        self.assertEqual(artist["illustrators"], ["ariiol"])
        self.assertEqual(wiki["composers"], ["Yunosuke"])

    def test_d_universe_only_and_animated_exclusions(self):
        reasons = {c["reason"].split(":", 1)[0]: c for c in self.candidates("wikis:miraheze", "excluded")}
        self.assertEqual(reasons["universe_only"]["game"], "dynamix_universe")
        self.assertTrue(reasons["animated_capture"]["upstream_key"].endswith(".gif"))
        for candidate in reasons.values():
            self.assertNotIn("asset_id", candidate)
        self.assertEqual(self.candidates("artists:x:ariiol_", "excluded")[0]["game"], "not_dynamix")

    def test_e_unknown_illustrator_and_unmapped(self):
        background = self.find(kind="background")[0]
        self.assertEqual((background["illustrators"], background["mapping_status"], background["song_ids"]),
                         ([], "unmapped", []))

    def test_f_failed_download(self):
        failed = self.candidates("wikis:fandom", "failed")
        self.assertEqual(len(failed), 1)
        failure = self.manifests["wikis"]["failures"][0]
        self.assertEqual((failure["candidate_id"], failure["phase"], failure["error_class"]),
                         (failed[0]["id"], "download", "http_5xx"))
        self.assertFalse(any(asset["candidate_id"] == failed[0]["id"] for asset in self.assets))
        self.assertFalse(self.discoveries["wikis:fandom"]["discovery_complete"])
        self.assertIsNotNone(self.discoveries["wikis:fandom"]["continuation"]["allimages"])

    def test_g_reference_pdf_in_archive_tier(self):
        pdf = self.find(format="PDF")[0]
        self.assertEqual((pdf["kind"], pdf["width"], pdf["height"], schema.source_family(pdf["source_id"])),
                         ("reference", None, None, "archives"))
        self.assertEqual(schema.tier_for(pdf["kind"], pdf["format"]), "archive")
        self.assertFalse(schema.is_gallery(pdf))

    def test_h_webp_behind_png_url(self):
        webp = self.find(format="WEBP")[0]
        self.assertTrue(webp["original_filename"].endswith(".png"))
        self.assertIn(".png", webp["download_url"])
        self.assertTrue(webp["path"].endswith(".webp"))
        self.assertEqual((webp["mime"], webp["source_reported"]["mime"]), ("image/webp", "image/png"))
        self.assertIs(webp["comparisons"]["format_match"], False)
        self.assertIs(webp["transformed"], True)

    def test_i_non_ascii_and_markup_titles(self):
        titles = {asset["title"] for asset in self.assets}
        self.assertIn("交差して歪む過去と未来", titles)
        self.assertIn("Moonlight of Sand Castle", titles)
        markup = [title for title in titles if "<script>" in title]
        self.assertEqual(len(markup), 1)
        self.assertIn("</script>", markup[0])
        kousa = self.find(title="交差して歪む過去と未来")[0]
        self.assertEqual(kousa["original_filename"], "交差して歪む過去と未来.jpg")
        self.assertTrue(Path(kousa["path"]).name.startswith("file--"))
        self.assertIn("交差して歪む過去と未来", (FIXTURE / "data/sources/wikis.json").read_text(encoding="utf-8"))

    def test_j_song_index_denominator_cases(self):
        songs = {song["title"]: song for song in self.song_index["songs"]}
        universe = songs["Fixture Universe-only Song"]
        self.assertEqual((universe["in_original_game"], universe["universe_also"]), (False, True))
        missing = songs["Fixture Song Without Artwork"]
        self.assertEqual((missing["in_original_game"], missing["artwork_asset_ids"]), (True, []))
        self.assertEqual(songs["交差して歪む過去と未来"]["id"], "song:file")
        self.assertTrue(self.song_index["disagreements"])

    def test_pending_and_partial_states_stay_visible(self):
        self.assertEqual(len(self.candidates("wikis:miraheze", "pending")), 1)
        official = self.manifests["official"]
        self.assertEqual((official["fetched_at"], official["assets"]), (None, []))
        self.assertEqual({source["status"] for source in official["sources"]}, {"metadata_only", "manual_access"})

    # -- determinism --

    def test_generator_reproduces_committed_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, "-I", str(GENERATOR), "--output", temp],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            generated, committed = tree(Path(temp)), tree(FIXTURE)
            self.assertEqual(sorted(generated), sorted(committed))
            changed = sorted(rel for rel in generated if generated[rel] != committed[rel])
            self.assertEqual(changed, [], "regenerate with tests/fixtures/contract/make_fixture.py")
            self.assertEqual(sorted(result.stdout.split()), sorted(committed))


if __name__ == "__main__":
    unittest.main()
