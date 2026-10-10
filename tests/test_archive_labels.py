"""Every token of the frozen catalog contract that the archive page labels (kind, source family, source status,
mapping status, quality labels, upstream status, failure phase and error class, comparison) has an English and a
Chinese label, and every catalog field is either shown by the page or deliberately left out with a reason.

Runs against the UI fixture (tests/fixtures/ui/catalog.json) and, when it exists, the generated data/catalog.json.
Adapted from FridrichMethod/deemo tests/test_archive_labels.py (Apache-2.0, commit b14bf0d5f5a05e5382c319f3fd5019a4936820b8)."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = re.compile(r"DYNAMIX_I18N\.register\((\{.*\})\);\s*$", re.DOTALL)
CATALOGS = [path for path in (ROOT / "tests/fixtures/ui/catalog.json", ROOT / "data/catalog.json") if path.is_file()]
# Enumerations of docs/implementation/09-frozen-contracts.md (schema version 1). A label must exist before a value first
# appears in a catalog, so the full vocabularies are checked, not only the values seen.
CONTRACT_TOKENS = {
    "kind": {"song_art", "pack_cover", "character", "background", "part", "promotional", "store_screenshot", "gameplay_screenshot", "process", "reference"},
    "family": {"artists", "official", "wikis", "archives"},
    "status": {"available", "partially_available", "temporarily_unavailable", "manual_access", "metadata_only", "excluded"},
    "mapping": {"confirmed_primary", "confirmed_wiki", "inferred", "unmapped", "conflict"},
    "quality.source_variant": {"site_original", "preview", "unknown"},
    "quality.origin_status": {"artist_post", "official", "wiki_copy", "repost", "scan", "unknown"},
    "quality.master_status": {"confirmed", "unverified"},
    "upstream": {"current", "removed", "superseded", "unknown"},
    "phase": {"discover", "download", "decode", "verify"},
    "error": {"http_4xx", "http_5xx", "timeout", "blocked", "challenge", "decode_error", "size_limit", "host_not_allowed", "other"},
    "comparison": {"sha1_match", "bytes_match", "dimensions_match", "format_match"},
    "credit.role": {"illustrator", "composer"},
}
# Provenance-record fields src/archive.js shows in the viewer (or uses for search and filters).
SHOWN_PROVENANCE = {
    "asset_id", "source_id", "source_name", "family", "page_url", "download_url", "resolved_url", "quality", "rights", "mapping_status",
    "mapping_evidence", "credit_evidence", "illustrators", "composers", "song_titles", "source_reported", "comparisons",
    "upstream_status", "superseded_by", "notes",
}
IGNORED_PROVENANCE = {}
# Catalog-asset fields the page shows (grid, viewer, file info, links) or uses to decide what to show.
SHOWN_ASSET = {
    "id", "source_id", "title", "kind", "page_url", "path", "original_filename", "bytes", "sha256", "sha1", "format", "mime",
    "width", "height", "fetched_at", "illustrators", "composers", "song_titles", "collections", "download_observed",
    "variant_role", "upstream_revision", "transformed", "game_associations", "family", "source_name", "url", "thumb", "gallery",
    "slideshow", "site_original", "archive_url", "provenance", "variants",
}
IGNORED_ASSET = {
    "candidate_id": "internal key of the discovery snapshot; the source page and file URLs are shown",
    "game": "always dynamix, the catalog's scope",
    "download_url": "the primary record's value, shown in its provenance block",
    "resolved_url": "the primary record's value, shown in its provenance block",
    "song_ids": "project song keys; the verbatim song titles are shown",
    "mapping_status": "the primary record's value, shown in its provenance block",
    "mapping_evidence": "the primary record's value, shown in its provenance block",
    "quality": "the primary record's value, shown in its provenance block",
    "rights": "the primary record's value, shown in its provenance block",
    "source_reported": "the primary record's value, compared in its provenance block",
    "comparisons": "the primary record's value, shown in its provenance block",
    "credit_evidence": "the primary record's value, shown in its provenance block",
    "upstream_status": "the primary record's value, shown in its provenance block",
    "superseded_by": "the primary record's value, shown in its provenance block",
    "notes": "the primary record's value, shown in its provenance block",
    "variant_group": "grouping key; the page lists the group's other members (variants)",
}


def message_tables():
    merged = {"en": {}, "zh-CN": {}}
    for name in ("common", "archive"):
        tables = json.loads(REGISTER.search((ROOT / f"src/i18n/{name}.js").read_text(encoding="utf-8")).group(1))
        for lang in merged:
            merged[lang].update(tables[lang])
    return merged


class ArchiveLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = message_tables()
        cls.script = (ROOT / "src/archive.js").read_text(encoding="utf-8")
        cls.catalogs = {path.relative_to(ROOT).as_posix(): json.loads(path.read_text(encoding="utf-8")) for path in CATALOGS}

    def assert_labelled(self, prefix, tokens):
        self.assertTrue(tokens, f"No {prefix} tokens")
        for language, table in self.tables.items():
            missing = sorted(token for token in tokens if f"{prefix}.{token}" not in table)
            self.assertEqual(missing, [], f"Add {prefix}.<token> labels for {language}; the page would show raw tokens")

    def test_contract_vocabularies_are_labelled(self):
        for prefix, tokens in CONTRACT_TOKENS.items():
            with self.subTest(prefix=prefix):
                self.assert_labelled(prefix, tokens)

    def test_catalog_values_are_labelled(self):
        """Values found in the catalogs, including any the contract may add later."""
        for name, catalog in self.catalogs.items():
            records = [record for asset in catalog["assets"] for record in asset["provenance"]]
            found = {
                "kind": {asset["kind"] for asset in catalog["assets"]},
                "family": {source["family"] for source in catalog["sources"]},
                "status": {source["status"] for source in catalog["sources"]},
                "mapping": {record["mapping_status"] for record in records},
                "quality.source_variant": {record["quality"]["source_variant"] for record in records},
                "quality.origin_status": {record["quality"]["origin_status"] for record in records},
                "quality.master_status": {record["quality"]["master_status"] for record in records},
                "upstream": {record["upstream_status"] for record in records if record.get("upstream_status")},
                "phase": {failure["phase"] for failure in catalog["failures"]},
                "error": {failure["error_class"] for failure in catalog["failures"]},
            }
            for prefix, tokens in found.items():
                with self.subTest(catalog=name, prefix=prefix):
                    if tokens:
                        self.assert_labelled(prefix, tokens)

    def test_type_filter_offers_every_kind(self):
        options = set(re.findall(r'<option value="(\w+)"', (ROOT / "archive.html").read_text(encoding="utf-8")))
        self.assertTrue(CONTRACT_TOKENS["kind"] <= options)
        self.assertTrue(CONTRACT_TOKENS["family"] <= options)

    def test_every_field_is_shown_or_ignored(self):
        self.assertEqual(SHOWN_ASSET & set(IGNORED_ASSET), set())
        self.assertEqual(SHOWN_PROVENANCE & set(IGNORED_PROVENANCE), set())
        for name, catalog in self.catalogs.items():
            asset_keys = set().union(*(asset.keys() for asset in catalog["assets"])) if catalog["assets"] else set()
            record_keys = set().union(*(record.keys() for asset in catalog["assets"] for record in asset["provenance"])) if catalog["assets"] else set()
            with self.subTest(catalog=name):
                self.assertEqual(sorted(asset_keys - SHOWN_ASSET - set(IGNORED_ASSET)), [], "Show these asset fields or list them in IGNORED_ASSET")
                self.assertEqual(sorted(record_keys - SHOWN_PROVENANCE - set(IGNORED_PROVENANCE)), [], "Show these provenance fields or list them")

    def test_shown_fields_are_read_by_the_page(self):
        unread = sorted(key for key in SHOWN_ASSET | SHOWN_PROVENANCE if not re.search(rf'\.{key}\b|"{key}"', self.script))
        self.assertEqual(unread, [], "src/archive.js no longer reads these SHOWN fields")

    def test_every_comparison_state_has_a_sentence(self):
        for lang, table in self.tables.items():
            for state in ("matched", "mismatched", "unknown"):
                with self.subTest(lang=lang, state=state):
                    self.assertIn("{field}", table[f"comparison.{state}"])
            self.assertTrue({"{reported}", "{observed}"} <= set(re.findall(r"\{\w+\}", table["comparison.mismatched"])))


if __name__ == "__main__":
    unittest.main()
