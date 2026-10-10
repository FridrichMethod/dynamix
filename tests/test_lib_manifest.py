"""Offline tests for scripts/lib/manifest.py: deterministic JSON, atomic writes, sorting and --check helpers."""

import copy
import json
import os
import random
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import manifest, schema  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/contract"


class JsonFormatTests(unittest.TestCase):
    def test_dumps_contract_format(self):
        text = manifest.dumps({"title": "交差して歪む過去と未来", "list": [1, {"a": None}]})
        self.assertTrue(text.endswith("}\n") and not text.endswith("\n\n"))
        self.assertIn("交差して歪む過去と未来", text)
        self.assertIn('\n  "list": [\n    1,', text)
        with self.assertRaises(ValueError):
            manifest.dumps({"x": float("nan")})

    def test_strict_loading(self):
        self.assertEqual(manifest.loads('{"a": 1}'), {"a": 1})
        for bad in ('{"a": 1, "a": 2}', '{"a": NaN}', '{"a": Infinity}'):
            with self.subTest(text=bad), self.assertRaises(ValueError):
                manifest.loads(bad)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bom.json"
            path.write_bytes(b"\xef\xbb\xbf{}")
            with self.assertRaises(ValueError):
                manifest.load_json(path)

    def test_now_utc_format(self):
        self.assertRegex(manifest.now_utc(), re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"))


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data/sources/wikis.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_writes_exact_bytes_without_leftovers(self):
        manifest.write_json_atomic(self.path, {"b": "é", "a": [1]})
        self.assertEqual(self.path.read_bytes(), manifest.dumps({"b": "é", "a": [1]}).encode("utf-8"))
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["wikis.json"])

    def test_failed_serialization_keeps_previous_file(self):
        manifest.write_json_atomic(self.path, {"version": 1})
        before = self.path.read_bytes()
        with self.assertRaises(TypeError):
            manifest.write_json_atomic(self.path, {"bad": object()})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["wikis.json"])

    def test_interrupted_replace_keeps_previous_file(self):
        manifest.write_json_atomic(self.path, {"version": 1})
        before = self.path.read_bytes()
        with mock.patch.object(manifest.os, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            manifest.write_json_atomic(self.path, {"version": 2})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["wikis.json"])

    def test_compare_json(self):
        data = {"a": [1, 2, 3]}
        self.assertIn("missing", manifest.compare_json(self.path, data))
        manifest.write_json_atomic(self.path, data)
        self.assertIsNone(manifest.compare_json(self.path, data))
        diff = manifest.compare_json(self.path, {"a": [1, 2, 4]})
        self.assertIn("-    3", diff)
        self.assertIn("+    4", diff)
        self.path.write_bytes(self.path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertIn("line endings", manifest.compare_json(self.path, data))


class SortingTests(unittest.TestCase):
    def setUp(self):
        self.wikis = manifest.load_json(FIXTURE / "data/sources/wikis.json")

    def shuffled(self, seed):
        value = copy.deepcopy(self.wikis)
        rng = random.Random(seed)
        for field in ("sources", "assets", "failures"):
            rng.shuffle(value[field])
            value[field] = [dict(sorted(record.items(), key=lambda _item: rng.random())) for record in value[field]]
        return dict(reversed(list(value.items())))

    def test_sort_manifest_is_deterministic_and_pure(self):
        expected = manifest.dumps(self.wikis)
        for seed in range(5):
            value = self.shuffled(seed)
            before = copy.deepcopy(value)
            self.assertEqual(manifest.dumps(manifest.sort_manifest(value)), expected)
            self.assertEqual(value, before)
        self.assertEqual(manifest.sort_manifest(self.wikis), self.wikis)

    def test_sort_discovery_and_song_index(self):
        discovery = manifest.load_json(FIXTURE / "data/discovery/miraheze.json")
        scrambled = {**discovery, "candidates": list(reversed(discovery["candidates"]))}
        self.assertEqual(manifest.sort_discovery(scrambled), discovery)
        index = manifest.load_json(FIXTURE / "data/sources/song-index.json")
        self.assertEqual(manifest.sort_song_index({**index, "songs": list(reversed(index["songs"]))}), index)

    def test_upsert_replaces_or_inserts_without_mutation(self):
        records = [{"id": "b", "v": 1}, {"id": "a", "v": 1}]
        before = copy.deepcopy(records)
        self.assertEqual(manifest.upsert(records, {"id": "b", "v": 2}), [{"id": "a", "v": 1}, {"id": "b", "v": 2}])
        self.assertEqual(manifest.upsert(records, {"id": "c", "v": 1})[-1], {"id": "c", "v": 1})
        self.assertEqual(records, before)

    def test_disposition_counts(self):
        counts = manifest.disposition_counts([{"disposition": "excluded"}, {"disposition": "excluded"},
                                              {"disposition": "pending"}])
        self.assertEqual(list(counts), list(schema.DISPOSITIONS))
        self.assertEqual((counts["excluded"], counts["pending"], counts["downloaded"]), (2, 1, 0))
        with self.assertRaises(ValueError):
            manifest.disposition_counts([{"disposition": "skipped"}])


class LoadTests(unittest.TestCase):
    def test_empty_manifest_and_missing_files(self):
        for family in schema.FAMILIES:
            schema.validate_manifest(manifest.empty_manifest(family), family)
        with self.assertRaises(ValueError):
            manifest.empty_manifest("legacy")
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(manifest.load_manifest(temp, "artists"), manifest.empty_manifest("artists"))
            with self.assertRaises(FileNotFoundError):
                manifest.load_manifest(temp, "artists", missing_ok=False)
            self.assertIsNone(manifest.load_discovery(temp, "wikis:miraheze"))

    def test_loads_and_validates_fixture(self):
        wikis = manifest.load_manifest(FIXTURE, "wikis", missing_ok=False)
        self.assertEqual(wikis["family"], "wikis")
        snapshot = manifest.load_discovery(FIXTURE, "artists:x:ariiol_")
        self.assertEqual(snapshot["source_id"], "artists:x:ariiol_")
        with tempfile.TemporaryDirectory() as temp:
            broken = Path(temp) / "data/sources/wikis.json"
            broken.parent.mkdir(parents=True)
            value = json.loads((FIXTURE / "data/sources/wikis.json").read_text(encoding="utf-8"))
            value["assets"][0]["kind"] = "fanart"
            broken.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(ValueError):
                manifest.load_manifest(temp, "wikis")

    def test_symlinked_manifest_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "data").mkdir()
            os.symlink(FIXTURE / "data/sources", Path(temp) / "data/sources")
            with self.assertRaises(ValueError):
                manifest.load_manifest(temp, "wikis")


if __name__ == "__main__":
    unittest.main()
