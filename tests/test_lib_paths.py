"""Offline tests for scripts/lib/paths.py: contract slugs, asset/discovery paths, path safety and song IDs."""

import os
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import paths, schema  # noqa: E402

SHA = "0123456789abcdef" * 4


class SlugTests(unittest.TestCase):
    def test_ascii_punctuation_and_spaces_collapse(self):
        self.assertEqual(paths.slug("Cosmic Pulse (crop) ver.2!!"), "Cosmic-Pulse-crop-ver.2")
        self.assertEqual(paths.slug("a - b -- c"), "a-b-c")
        self.assertEqual(paths.slug("keep_under.score-and.dots"), "keep_under.score-and.dots")

    def test_nfkc_and_non_latin(self):
        self.assertEqual(paths.slug("Ｄｙｎａｍｉｘ　ﬁle"), "Dynamix-file")
        self.assertEqual(paths.slug("交差して歪む過去と未来"), "file")
        self.assertEqual(paths.slug("Moonlight 砂の城"), "Moonlight")
        self.assertEqual(paths.slug(""), "file")

    def test_trim_length_and_reserved_names(self):
        self.assertEqual(paths.slug("..hidden.."), "hidden")
        self.assertEqual(paths.slug("..."), "file")
        long = paths.slug("x" * 79 + " tail")
        self.assertLessEqual(len(long), 80)
        self.assertFalse(long.endswith(("-", ".")))
        self.assertEqual(paths.slug("CON"), "file-CON")
        self.assertEqual(paths.slug("nul.txt"), "file-nul.txt")

    def test_idempotent_and_lower(self):
        for text in ("Cosmic Pulse", "交差して歪む過去と未来", "A--B", "x" * 200, "CON", " .Trailing. "):
            once = paths.slug(text)
            self.assertEqual(paths.slug(once), once, text)
        self.assertEqual(paths.slug("Cosmic Pulse", lower=True), "cosmic-pulse")

    def test_rejects_non_string(self):
        with self.assertRaises(TypeError):
            paths.slug(None)


class ContractPathTests(unittest.TestCase):
    def test_source_slugs_and_discovery_paths(self):
        for source_id in schema.REGISTERED_SOURCE_IDS:
            self.assertEqual(paths.discovery_relpath(source_id), f"data/discovery/{source_id.split(':', 1)[1]}.json")
        self.assertEqual(paths.source_slug("artists:x:ariiol_"), "x-ariiol_")
        self.assertEqual(paths.source_slug("artists:artstation:sandgodoven"), "artstation-sandgodoven")
        self.assertEqual(paths.manifest_relpath("wikis"), "data/sources/wikis.json")
        for bad in ("miraheze", "legacy:x", "wikis:", None):
            with self.assertRaises(ValueError):
                paths.source_slug(bad)
        with self.assertRaises(ValueError):
            paths.manifest_relpath("legacy")

    def test_registered_discovery_paths_do_not_collide(self):
        names = [paths.discovery_relpath(source_id) for source_id in schema.REGISTERED_SOURCE_IDS]
        names.append(paths.discovery_relpath("artists:x:ariiol_"))
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(paths.source_slug_collisions([*schema.REGISTERED_SOURCE_IDS, "artists:x:ariiol_"]), [])

    def test_source_slug_collisions(self):
        """data/discovery is flat across families, and slug() collapses hyphen runs."""
        self.assertEqual(
            paths.source_slug_collisions(["official:x", "archives:x", "wikis:miraheze"]), [["archives:x", "official:x"]]
        )
        self.assertEqual(
            paths.source_slug_collisions(["artists:x:a--b", "artists:x:a-b"]), [["artists:x:a--b", "artists:x:a-b"]]
        )
        self.assertEqual(
            paths.source_slug_collisions(["artists:x:foo-bar", "artists:x-foo:bar"]),
            [["artists:x-foo:bar", "artists:x:foo-bar"]],
        )
        self.assertEqual(paths.source_slug_collisions(["artists:x:foo-bar", "artists:pixiv:foo-bar"]), [])

    def test_asset_relpath(self):
        self.assertEqual(
            paths.asset_relpath("wikis", "miraheze", "File:Cosmic Pulse.png", SHA, "webp"),
            f"assets/public/wikis/miraheze/File-Cosmic-Pulse--{SHA[:12]}.webp",
        )
        self.assertEqual(
            paths.asset_relpath("artists", "x-ariiol_", "dir/sub\\交差.jpg", SHA, "jpg"),
            f"assets/public/artists/x-ariiol_/file--{SHA[:12]}.jpg",
        )
        self.assertEqual(paths.filename_stem("archive.tar.gz"), "archive.tar")
        self.assertEqual(paths.filename_stem(".hidden"), ".hidden")
        self.assertEqual(paths.filename_stem("noext"), "noext")
        bad_calls = [
            ("legacy", "x", "a.png", SHA, "png"),
            ("wikis", "Mira heze", "a.png", SHA, "png"),
            ("wikis", "miraheze", "a.png", SHA.upper(), "png"),
            ("wikis", "miraheze", "a.png", SHA[:-1], "png"),
            ("wikis", "miraheze", "a.png", SHA, "PNG"),
            ("wikis", "miraheze", "", SHA, "png"),
            ("wikis", "miraheze", "a.png", SHA + "\n", "png"),
        ]
        for args in bad_calls:
            with self.subTest(args=args), self.assertRaises(ValueError):
                paths.asset_relpath(*args)

    def test_constants_match_schema(self):
        self.assertEqual(paths.FAMILIES, schema.FAMILIES)
        self.assertEqual(set(paths.ASSET_EXTENSIONS), set(schema.FORMAT_EXT.values()))


class SafePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        (self.root / "assets/public/wikis").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_syntax_rejections(self):
        for bad in (
            "",
            "/etc/passwd",
            "C:/x.png",
            "a\\b.png",
            "a/../b.png",
            "./a.png",
            "a//b.png",
            "a/.",
            "..",
            "a/b\x00.png",
            "a/b:c.png",
            "a/b?.png",
            None,
        ):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                paths.safe_path(self.root, bad)

    def test_under_prefix(self):
        self.assertEqual(
            paths.safe_path(self.root, "assets/public/wikis/new/x.png", under="assets/public"),
            self.root.resolve() / "assets/public/wikis/new/x.png",
        )
        for bad in ("assets/publicity/x.png", "data/x.json", "assets/public"):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                paths.safe_path(self.root, bad, under="assets/public")

    def test_symlinked_directory_and_file_rejected(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "secret.png").write_bytes(b"x")
        os.symlink(outside, self.root / "assets/public/wikis/linked")
        os.symlink(outside / "secret.png", self.root / "assets/public/wikis/file.png")
        for rel in (
            "assets/public/wikis/linked/secret.png",
            "assets/public/wikis/linked/new.png",
            "assets/public/wikis/file.png",
        ):
            with self.subTest(path=rel), self.assertRaises(ValueError):
                paths.safe_path(self.root, rel)

    def test_symlinked_root_itself_is_resolved(self):
        alias = Path(self.temp.name) / "alias"
        os.symlink(self.root, alias)
        self.assertEqual(
            paths.safe_path(alias, "assets/public/wikis/a.png"), self.root.resolve() / "assets/public/wikis/a.png"
        )

    def test_file_used_as_directory(self):
        (self.root / "assets/public/wikis/plain").write_bytes(b"x")
        with self.assertRaises(ValueError):
            paths.safe_path(self.root, "assets/public/wikis/plain/child.png")


class CollisionTests(unittest.TestCase):
    def test_file_and_directory_collisions(self):
        groups = paths.casefold_collisions(
            [
                "assets/public/wikis/miraheze/A--1.png",
                "assets/public/wikis/miraheze/a--1.png",
                "assets/public/wikis/Fandom/x.png",
                "assets/public/wikis/fandom/y.png",
                "assets/public/wikis/miraheze/unique.png",
                "assets/public/wikis/miraheze/unique.png",
            ]
        )
        self.assertEqual(
            groups,
            [
                ["assets/public/wikis/Fandom", "assets/public/wikis/fandom"],
                ["assets/public/wikis/miraheze/A--1.png", "assets/public/wikis/miraheze/a--1.png"],
            ],
        )

    def test_unicode_normalization_collision(self):
        composed = "assets/" + unicodedata.normalize("NFC", "Café.png")
        decomposed = "assets/" + unicodedata.normalize("NFD", "Café.png")
        self.assertNotEqual(composed, decomposed)
        self.assertEqual(paths.casefold_collisions([composed, decomposed]), [sorted([composed, decomposed])])

    def test_no_collision(self):
        self.assertEqual(paths.casefold_collisions(["a/b.png", "a/c.png", "b/b.png"]), [])


class SongIdTests(unittest.TestCase):
    def test_contract_example_and_collisions(self):
        titles = ["Cosmic Pulse", "cosmic pulse", "COSMIC PULSE", "Cosmic Pulse 2", "交差して歪む過去と未来", "歪む"]
        ids = dict(zip(titles, paths.assign_song_ids(titles), strict=True))
        self.assertEqual(ids["COSMIC PULSE"], "song:cosmic-pulse")  # sorts first (uppercase code points)
        self.assertEqual(ids["Cosmic Pulse 2"], "song:cosmic-pulse-2")  # natural slug keeps its ID
        self.assertEqual(ids["Cosmic Pulse"], "song:cosmic-pulse-3")
        self.assertEqual(ids["cosmic pulse"], "song:cosmic-pulse-4")
        self.assertEqual(ids["交差して歪む過去と未来"], "song:file-" + paths.song_title_hash("交差して歪む過去と未来"))
        self.assertEqual(ids["歪む"], "song:file-" + paths.song_title_hash("歪む"))
        self.assertEqual(paths.song_title_hash("交差して歪む過去と未来"), "568a196c")  # SHA-256 of the UTF-8 title

    def test_non_latin_ids_do_not_depend_on_the_title_set(self):
        """Parallel workers see different title sets; a title's ID must not change when another title appears."""
        alone = paths.assign_song_ids(["交差して歪む過去と未来"])
        self.assertEqual(paths.assign_song_ids(["交差して歪む過去と未来", "ユメ", "歪む"])[0], alone[0])
        self.assertEqual(paths.assign_song_ids(["ユメ", "交差して歪む過去と未来"])[1], alone[0])
        self.assertEqual(alone[0], paths.song_id_base("交差して歪む過去と未来"))

    def test_lossy_slugs_get_a_title_hash(self):
        self.assertEqual(paths.song_id_base("Moonlight"), "song:moonlight")
        partial = paths.song_id_base("Moonlight 砂の城")
        self.assertEqual(partial, "song:moonlight-" + paths.song_title_hash("Moonlight 砂の城"))
        self.assertEqual(paths.assign_song_ids(["Moonlight", "Moonlight 砂の城"]), ["song:moonlight", partial])
        self.assertTrue(paths.song_id_base("Café").startswith("song:caf-"))
        self.assertTrue(paths.song_id_base("∞").startswith("song:file-"))
        self.assertEqual(paths.song_id_base("Cosmic Pulse!"), "song:cosmic-pulse")  # punctuation only: no hash
        nfd = unicodedata.normalize("NFD", "パズル")
        self.assertNotEqual(nfd, "パズル")
        self.assertEqual(paths.song_id_base(nfd), paths.song_id_base("パズル"))  # hash over NFC

    def test_independent_of_input_order(self):
        titles = ["b", "A", "a", "a 2", "交差", "歪む", "Z"]
        expected = dict(zip(titles, paths.assign_song_ids(titles), strict=True))
        reordered = list(reversed(titles))
        self.assertEqual(dict(zip(reordered, paths.assign_song_ids(reordered), strict=True)), expected)
        self.assertEqual(len(set(expected.values())), len(titles))
        for value in expected.values():
            self.assertRegex(value, schema.SONG_ID_RE)

    def test_empty_title_rejected(self):
        with self.assertRaises(ValueError):
            paths.song_id_base("  ")


if __name__ == "__main__":
    unittest.main()
