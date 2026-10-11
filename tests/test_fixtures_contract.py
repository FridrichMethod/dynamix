"""The committed contract fixture (tests/fixtures/contract) validates, reconciles, matches its bytes, covers every
required scenario and is reproduced byte-for-byte by its generator, whichever zlib the interpreter loads."""

import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests/fixtures"))

from PIL import Image  # noqa: E402

from lib import manifest, media, paths, schema  # noqa: E402
from pinned_png import PinnedPNG  # noqa: E402

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
        schema.validate_song_links(self.song_index, self.assets)
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
        background = self.find(kind="background", upstream_status="current")[0]
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
        kousa = songs["交差して歪む過去と未来"]["id"]
        self.assertEqual(kousa, "song:file-" + paths.song_title_hash("交差して歪む過去と未来"))
        self.assertEqual(paths.assign_song_ids(["交差して歪む過去と未来"]), [kousa])  # independent of the title set
        self.assertTrue(self.song_index["disagreements"])

    def test_k_superseded_revision_under_the_same_candidate(self):
        old = self.find(upstream_status="superseded")[0]
        current = self.by_id[old["superseded_by"]]
        self.assertEqual((old["candidate_id"], old["title"], old["kind"]),
                         (current["candidate_id"], current["title"], current["kind"]))
        self.assertEqual(current["upstream_status"], "current")
        self.assertNotEqual(old["sha256"], current["sha256"])
        self.assertLess(old["upstream_revision"], current["upstream_revision"])
        self.assertTrue((FIXTURE / old["path"]).is_file())
        candidate = next(c for c in self.candidates("wikis:miraheze") if c["id"] == old["candidate_id"])
        self.assertEqual((candidate["disposition"], candidate["asset_id"]), ("downloaded", current["id"]))
        source = next(s for s in self.manifests["wikis"]["sources"] if s["id"] == "wikis:miraheze")
        self.assertEqual(source["asset_count"], len(self.candidates("wikis:miraheze", "downloaded")) + 1)

    def test_l_raster_archive_tier_screenshot(self):
        shot = self.find(kind="gameplay_screenshot")[0]
        self.assertEqual((shot["format"], schema.tier_for(shot["kind"], shot["format"])), ("PNG", "archive"))
        self.assertTrue(schema.is_gallery(shot))
        self.assertFalse(schema.is_slideshow(shot))

    def test_m_blocked_candidate_with_challenge_failure(self):
        blocked = self.candidates("official:facebook", "blocked")
        self.assertEqual(len(blocked), 1)
        self.assertTrue(blocked[0]["reason"].startswith("challenge"))
        failure = self.manifests["official"]["failures"][0]
        self.assertEqual((failure["candidate_id"], failure["error_class"], failure["http_status"],
                          failure["retriable"], failure["manual"]), (blocked[0]["id"], "challenge", 403, False, True))
        source = next(s for s in self.manifests["official"]["sources"] if s["id"] == "official:facebook")
        self.assertEqual((source["status"], source["evidence_level"], source["discovery_complete"]),
                         ("manual_access", "blocked", False))
        self.assertTrue(self.discoveries["official:facebook"]["errors"])

    def test_n_animated_gif(self):
        gif = self.find(format="GIF")[0]
        info = media.inspect(FIXTURE / gif["path"])
        self.assertEqual((info["ext"], info["animated"], info["frames"]), ("gif", True, 3))
        self.assertTrue(schema.is_gallery(gif))
        self.assertEqual(schema.tier_for(gif["kind"], gif["format"]), "site")

    def test_o_inferred_mapping(self):
        inferred = self.find(mapping_status="inferred")
        self.assertEqual(len(inferred), 1)
        self.assertTrue(inferred[0]["mapping_evidence"])
        self.assertTrue(inferred[0]["song_ids"])

    def test_pending_and_partial_states_stay_visible(self):
        self.assertEqual(len(self.candidates("wikis:miraheze", "pending")), 1)
        official = self.manifests["official"]
        self.assertEqual((official["fetched_at"], official["assets"]), (None, []))
        self.assertEqual({source["status"] for source in official["sources"]}, {"metadata_only", "manual_access"})

    # -- determinism --

    def test_generator_reproduces_committed_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, "-I", "-B", str(GENERATOR), "--output", temp],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            generated, committed = tree(Path(temp)), tree(FIXTURE)
            self.assertEqual(sorted(generated), sorted(committed))
            changed = sorted(rel for rel in generated if generated[rel] != committed[rel])
            self.assertEqual(changed, [], "regenerate with tests/fixtures/contract/make_fixture.py")
            self.assertEqual(sorted(result.stdout.split()), sorted(committed))


class PinnedPNGTests(unittest.TestCase):
    """Fixture PNGs keep their committed bytes across zlib builds (stock zlib and zlib-ng compress differently)."""

    @staticmethod
    def picture(shade=0):
        image = Image.new("RGB", (96, 64))
        image.putdata([((x * 7 + shade) % 256, (y * 5) % 256, (x * y + shade) % 256)
                       for y in range(64) for x in range(96)])
        return image

    @staticmethod
    def grey_palette_image(index):
        """A P image whose palette maps every index to that grey: stored values and RGBA equal an L image's."""
        image = Image.new("P", (4, 4), index)
        image.putpalette([value for grey in range(256) for value in (grey, grey, grey)])
        return image

    @staticmethod
    def png(image, **options):
        buffer = io.BytesIO()
        image.save(buffer, "PNG", **options)
        return buffer.getvalue()

    def test_the_identical_image_keeps_the_committed_bytes(self):
        image = self.picture()
        committed, fresh = self.png(image, compress_level=1), self.png(image, compress_level=9)
        self.assertNotEqual(committed, fresh)  # a different compressor setting stands in for a different zlib
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "nested").mkdir()
            (Path(temp) / "nested/picture.png").write_bytes(committed)
            self.assertEqual(PinnedPNG(Path(temp)).encode(image, compress_level=9), committed)

    def test_a_changed_drawing_is_encoded_afresh(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "picture.png").write_bytes(self.png(self.picture(), compress_level=1))
            changed = self.picture(shade=1)
            data = PinnedPNG(Path(temp)).encode(changed, compress_level=9)
        self.assertEqual(data, self.png(changed, compress_level=9))
        with Image.open(io.BytesIO(data)) as decoded:
            self.assertEqual(decoded.tobytes(), changed.tobytes())

    def test_a_different_shape_or_mode_is_never_taken_for_the_committed_image(self):
        cases = [(Image.new("RGB", (2, 3), (40, 90, 200)), Image.new("RGB", (3, 2), (40, 90, 200))),  # transposed
                 (Image.new("L", (4, 4), 128), Image.new("RGB", (4, 4), (128, 128, 128))),  # same grey, other mode
                 (Image.new("L", (4, 4), 7), self.grey_palette_image(7))]  # same stored values and colours
        for committed_image, drawn in cases:
            with self.subTest(committed=committed_image.mode, drawn=drawn.mode, size=drawn.size):
                with tempfile.TemporaryDirectory() as temp:
                    (Path(temp) / "picture.png").write_bytes(self.png(committed_image, compress_level=1))
                    data = PinnedPNG(Path(temp)).encode(drawn, compress_level=9)
                self.assertEqual(data, self.png(drawn, compress_level=9))
                with Image.open(io.BytesIO(data)) as decoded:
                    self.assertEqual((decoded.mode, decoded.size), (drawn.mode, drawn.size))

    def test_values_above_eight_bits_are_not_clipped_into_one_key(self):
        from pinned_png import pixel_key
        self.assertNotEqual(pixel_key(Image.new("I;16", (2, 2), 300)), pixel_key(Image.new("I;16", (2, 2), 400)))

    def test_two_committed_encodings_of_one_image_are_refused(self):
        image = self.picture()
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "a.png").write_bytes(self.png(image, compress_level=1))
            (Path(temp) / "b.png").write_bytes(self.png(image, compress_level=9))
            with self.assertRaisesRegex(ValueError, "same image with different bytes"):
                PinnedPNG(Path(temp)).encode(image)


if __name__ == "__main__":
    unittest.main()
