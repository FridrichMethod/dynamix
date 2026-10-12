"""scripts/update_docs_stats.py: statistics blocks rendered from data/coverage.json and data/catalog.json only, in both
languages from the same numbers, deterministic, idempotent, stale-block detection, refusal of mismatched or unsafe
inputs and of broken markers, and documents that still satisfy the public-documentation rules after rendering.

Offline. The inputs are a real build of the contract fixture (tests/fixtures/contract) by scripts/build_catalog.py and
scripts/build_coverage.py, so the field names checked here are the ones the builders write; edge cases mutate copies.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_catalog  # noqa: E402
import build_coverage  # noqa: E402
import test_docs as rules  # noqa: E402 - the documentation rules (its TestCase classes are not collected here)
import test_docs_release as release  # noqa: E402 - the READMEs (its TestCase is not collected here either)
import update_docs_stats as stats  # noqa: E402

CONTRACT = ROOT / "tests/fixtures/contract"
TOKENS = re.compile(r"\d+(?:[.,:-]\d+)*")


def quiet(function, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return function(*args)


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = stats.main(list(argv))
    return status, out.getvalue(), err.getvalue()


def block_of(text: str) -> str:
    return text[text.index(stats.BEGIN) + len(stats.BEGIN) : text.index(stats.END)]


def outside(text: str) -> str:
    return text[: text.index(stats.BEGIN)] + text[text.index(stats.END) :]


def setUpModule():
    """One real build of the contract fixture; every test copies its two outputs."""
    global BUILD, BUILD_DIR
    BUILD_DIR = tempfile.TemporaryDirectory()
    root = Path(BUILD_DIR.name)
    for top in ("data", "assets"):
        shutil.copytree(CONTRACT / top, root / top)
    if quiet(build_catalog.main, ["--root", str(root)]) != 0 or quiet(build_coverage.main, ["--root", str(root)]) != 0:
        raise RuntimeError("the contract fixture did not build")
    BUILD = {
        name: json.loads((root / name).read_text(encoding="utf-8"))
        for name in (stats.COVERAGE_JSON, stats.CATALOG_JSON)
    }


def tearDownModule():
    BUILD_DIR.cleanup()


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for relative in stats.DOCUMENTS:
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, self.root / relative)
        self.coverage = copy.deepcopy(BUILD[stats.COVERAGE_JSON])
        self.catalog = copy.deepcopy(BUILD[stats.CATALOG_JSON])
        self.save()

    def save(self):
        (self.root / "data").mkdir(exist_ok=True)
        for name, value in ((stats.COVERAGE_JSON, self.coverage), (stats.CATALOG_JSON, self.catalog)):
            (self.root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read(self, relative: str) -> str:
        return (self.root / relative).read_text(encoding="utf-8")

    def snapshot(self) -> dict[str, bytes]:
        return {relative: (self.root / relative).read_bytes() for relative in stats.DOCUMENTS}

    def render(self) -> None:
        status, _, err = run("--root", str(self.root))
        self.assertEqual(status, 0, err)

    def assertRefused(self, fragment: str) -> None:
        before = self.snapshot()
        status, _, err = run("--root", str(self.root))
        self.assertEqual(status, 2, err)
        self.assertIn(fragment, err)
        self.assertEqual(self.snapshot(), before, "nothing is written when an input or a document is refused")


class RenderTests(Base):
    def test_every_document_gets_a_generated_block_and_keeps_its_other_text(self):
        originals = {relative: (ROOT / relative).read_text(encoding="utf-8") for relative in stats.DOCUMENTS}
        self.render()
        for relative in stats.DOCUMENTS:
            text = self.read(relative)
            with self.subTest(document=relative):
                self.assertEqual(outside(text), outside(originals[relative]), "text outside the markers changed")
                block = block_of(text).strip("\n").splitlines()
                self.assertEqual(block[0], stats.GENERATED)
                self.assertTrue(any(line.startswith("| ") for line in block), "the block holds a table")
                self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"))
                self.assertFalse([line for line in text.splitlines() if line != line.rstrip()], "trailing whitespace")
                self.assertEqual(block_of(text).split("\n")[-2:], ["", ""], "a blank line before the END marker")

    def test_check_passes_after_rendering_and_a_second_run_changes_nothing(self):
        self.render()
        after = self.snapshot()
        self.assertEqual(run("--root", str(self.root), "--check")[0], 0)
        status, out, _ = run("--root", str(self.root))
        self.assertEqual((status, out), (0, ""), "a current document is not rewritten")
        self.assertEqual(self.snapshot(), after)

    def test_both_languages_carry_the_same_numbers_and_table_rows(self):
        self.render()
        for english, chinese in rules.PAIRS:
            if english not in stats.DOCUMENTS:
                continue
            en, zh = block_of(self.read(english)), block_of(self.read(chinese))
            with self.subTest(pair=english):
                self.assertEqual(TOKENS.findall(en), TOKENS.findall(zh), "the same numbers in the same order")
                self.assertEqual(
                    [line.count("|") for line in en.splitlines() if line.startswith("|")],
                    [line.count("|") for line in zh.splitlines() if line.startswith("|")],
                )
                self.assertEqual(len(en.splitlines()), len(zh.splitlines()))

    def test_rendered_documents_still_follow_the_documentation_rules(self):
        self.render()
        for english, chinese in rules.PAIRS:
            if english not in stats.DOCUMENTS:
                continue
            for relative in (english, chinese):
                text = self.read(relative)
                with self.subTest(document=relative):
                    # The READMEs describe the release in their own prose; their generated block never does.
                    scope = block_of(text) if relative in release.READMES else text
                    self.assertIsNone(rules.FLAT_CLAIMS.search(scope))
                    for sentence in rules.sentences(rules.prose_lines(block_of(text))):
                        if rules.RELEASE_WORDS.search(sentence):
                            self.assertRegex(sentence, rules.GUARDS)
                    self.assertEqual(text.count("STATS:"), 2)
                    self.assertIsNone(re.search(r"^#{1,6} ", block_of(text), re.M), "no headings in a block")
                    for line in block_of(text).splitlines():
                        self.assertEqual(rules.unrendered_bold(line), [], f"bold that prints literally: {line}")
            en, zh = self.read(english), self.read(chinese)
            with self.subTest(pair=english):
                self.assertEqual([level for level, _ in rules.headings(en)], [level for level, _ in rules.headings(zh)])
                self.assertEqual(
                    sum(line.startswith("|") for line in rules.prose_lines(en)),
                    sum(line.startswith("|") for line in rules.prose_lines(zh)),
                )

    def test_summary_reports_the_coverage_numbers(self):
        self.render()
        readme, coverage = self.read("README.md"), self.coverage
        expected = [
            f"| Unique files (distinct SHA-256) | {coverage['assets']['unique_files']:,} |",
            f"| Asset records (one per source and file) | {coverage['assets']['source_asset_records']:,} |",
            f"| Songs in the original-game denominator | {coverage['songs']['denominator']:,} |",
            f"| Pending candidates (not decided yet) | {coverage['candidates']['by_disposition']['pending']:,} |",
            f"Song index of {coverage['as_of']}.",
        ]
        for line in expected:
            self.assertIn(line, readme)
        self.assertIn(
            f"{coverage['candidates']['total']:,}: {coverage['candidates']['by_disposition']['downloaded']:,}"
            " downloaded",
            readme,
        )
        self.assertIn("within the budget", readme)
        self.assertIn("在预算之内", self.read("README.zh-CN.md"))

    def test_family_blocks_list_only_their_family(self):
        self.render()
        records = self.coverage["sources"]["records"]
        for family in stats.FAMILIES:
            block = block_of(self.read(f"docs/sources-{family}.md"))
            with self.subTest(family=family):
                for record in records:
                    row = f"| `{record['id']}` |"
                    if record["family"] == family:
                        self.assertIn(row, block)
                    else:
                        self.assertNotIn(row, block)
                self.assertIn(f"Family `{family}`:", block)

    def test_family_blocks_count_entries_shared_by_sources_of_the_family(self):
        self.render()
        for family in stats.FAMILIES:
            expected = sum(
                len(
                    {
                        record["source_id"]
                        for record in asset["provenance"]
                        if record["source_id"].split(":", 1)[0] == family
                    }
                )
                > 1
                for asset in self.catalog["assets"]
            )
            with self.subTest(family=family):
                self.assertIn(f"source records): {expected:,}.", block_of(self.read(f"docs/sources-{family}.md")))
                self.assertIn(f"多条来源记录）：{expected:,}。", block_of(self.read(f"docs/sources-{family}.zh-CN.md")))

    def test_shared_entries_count_distinct_sources_within_one_family(self):
        def asset(*sources):
            return {"source_id": sources[0], "bytes": 1, "provenance": [{"source_id": s} for s in sources]}

        catalog = {
            "assets": [
                asset("wikis:miraheze", "wikis:fandom"),
                asset("wikis:miraheze"),
                asset("artists:x:a", "wikis:fandom"),
                asset("official:x", "official:x"),
                asset("official:apple-store", "official:google-play", "wikis:fandom"),
            ]
        }
        self.assertEqual([stats.shared_entries(catalog, family) for family in stats.FAMILIES], [1, 0, 1, 0])

    def test_coverage_totals_match_the_report(self):
        self.render()
        block = block_of(self.read("docs/coverage.md"))
        by_disposition = self.coverage["candidates"]["by_disposition"]
        total = " | ".join(f"{value:,}" for value in by_disposition.values())
        self.assertIn(f"| Total | {self.coverage['candidates']['total']:,} | {total} |", block)
        for key, value in self.coverage["songs"].items():
            if isinstance(value, int):
                self.assertRegex(block, rf"\| `{key}` \| {value:,} \|")


class InputTests(Base):
    def test_only_the_two_inputs_are_read(self):
        (self.root / "data/thumbs.json").write_text("{not json", encoding="utf-8")
        (self.root / "data/sources").mkdir()
        (self.root / "data/sources/song-index.json").write_text("{not json", encoding="utf-8")
        read, original = [], stats.load_json

        def recording(root, relative):
            read.append(relative)
            return original(root, relative)

        with mock.patch.object(stats, "load_json", recording):
            self.render()
        self.assertEqual(sorted(read), sorted([stats.COVERAGE_JSON, stats.CATALOG_JSON]))

    def test_no_network_clock_or_process_modules(self):
        tree = ast.parse((ROOT / "scripts/update_docs_stats.py").read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        }
        imported |= {
            node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertFalse(
            imported
            & {
                "socket",
                "urllib",
                "http",
                "requests",
                "ssl",
                "time",
                "datetime",
                "random",
                "subprocess",
                "lib",
                "build_catalog",
                "build_coverage",
            },
            imported,
        )

    def test_missing_input(self):
        (self.root / stats.CATALOG_JSON).unlink()
        self.assertRefused("Missing required input: data/catalog.json")

    def test_malformed_json(self):
        for text, fragment in (
            ("{", "data/coverage.json"),
            ('{"a": NaN}', "NaN"),
            ('{"a": 1, "a": 2}', "duplicate"),
            ("[]", "top level"),
        ):
            with self.subTest(text=text):
                (self.root / stats.COVERAGE_JSON).write_text(text, encoding="utf-8")
                self.assertRefused(fragment)

    def test_inputs_from_different_builds(self):
        self.catalog["summary"]["unique_files"] += 1
        self.save()
        self.assertRefused("come from different builds")

    def test_a_cut_down_catalog_is_refused(self):
        """Only the summary would agree: the entries themselves no longer add up to the coverage totals."""
        self.catalog["assets"] = self.catalog["assets"][:1]
        self.save()
        self.assertRefused("catalog assets vs assets.unique_files")

    def test_catalog_entries_must_match_the_coverage_totals(self):
        def changed_bytes(catalog):
            catalog["assets"][0]["bytes"] += 1

        def moved_record(catalog):
            records = [record for asset in catalog["assets"] for record in asset["provenance"]]
            ids = sorted({record["source_id"] for record in records})
            other = next(source_id for source_id in ids if source_id != records[0]["source_id"])
            records[0]["source_id"] = other

        def moved_primary(catalog):
            ids = sorted({asset["source_id"] for asset in catalog["assets"]})
            first = catalog["assets"][0]
            first["source_id"] = next(source_id for source_id in ids if source_id != first["source_id"])

        def changed_status(catalog):
            source = catalog["sources"][0]
            source["status"] = "excluded" if source["status"] != "excluded" else "available"

        cases = (
            (changed_bytes, "bytes.unique_total"),
            (moved_record, "sources.records[].asset_records"),
            (moved_primary, "sources.records[].primary_unique_files"),
            (changed_status, "sources.records[].status"),
        )
        for mutate, fragment in cases:
            with self.subTest(case=mutate.__name__):
                catalog = copy.deepcopy(self.catalog)
                mutate(catalog)
                (self.root / stats.CATALOG_JSON).write_text(json.dumps(catalog), encoding="utf-8")
                self.assertRefused(fragment)

    def test_catalog_only_fields_are_printed_as_the_catalog_states_them(self):
        """checked_at exists only in the catalog, so it cannot be cross-checked; the documents say so."""
        source = next(source for source in self.catalog["sources"] if source["family"] == "official")
        source["checked_at"] = "2027-01-01T00:00:00Z"
        self.save()
        self.render()
        self.assertIn("2027-01-01T00:00:00Z", block_of(self.read("docs/coverage.md")))
        for relative in ("docs/coverage.md", "docs/coverage.zh-CN.md"):
            self.assertIn("`checked_at`", outside(self.read(relative)))

    def test_malformed_catalog_assets(self):
        cases = (
            (lambda c: c.update(assets={}), "'assets' must be a list of objects"),
            (lambda c: c["assets"][0].update(bytes=-1), "assets[0].bytes must be a non-negative integer"),
            (lambda c: c["assets"][0].update(provenance=[]), "assets[0].provenance must be a non-empty list"),
            (lambda c: c["assets"][0]["provenance"][0].update(source_id="wikis:a|b"), "not a plain identifier"),
        )
        for mutate, fragment in cases:
            with self.subTest(fragment=fragment):
                catalog = copy.deepcopy(self.catalog)
                mutate(catalog)
                (self.root / stats.CATALOG_JSON).write_text(json.dumps(catalog), encoding="utf-8")
                self.assertRefused(fragment)

    def test_timestamps_and_dates_take_ascii_digits_only(self):
        """``\\d`` matches every Unicode decimal digit: a fullwidth or Arabic-Indic date would be printed as the
        acquisition date (scripts/lib/checks.py refuses them as well)."""
        for value in ("２０２６-10-10T12:00:00Z", "٢٠٢٦-10-10T12:00:00Z", "2026-10-10T12:00:0０Z"):
            with self.subTest(timestamp=value), self.assertRaises(stats.StatsError):
                stats.timestamp(value, "checked_at")
        self.assertEqual(stats.timestamp("2026-10-10T12:00:00Z", "checked_at"), "2026-10-10T12:00:00Z")
        for value in ("٢٠٢٦-10-10", "２０２６-10-10"):
            with self.subTest(date=value), self.assertRaises(stats.StatsError):
                stats.as_of({"as_of": value})
        self.assertEqual(stats.as_of({"as_of": "2026-10-10"}), "2026-10-10")
        self.coverage["as_of"] = "２０２６-10-10"
        self.save()
        self.assertRefused("'as_of' is not a date")

    def test_snapshot_dates_must_agree(self):
        family = next(iter(self.catalog["snapshots"]))
        self.catalog["snapshots"][family] = "2000-01-01T00:00:00Z"
        self.save()
        self.assertRefused("snapshots vs inputs.manifests")

    def test_unsafe_identifier(self):
        for bad in ("wikis:a|b", "wikis:`x`", "wikis:<script>"):
            with self.subTest(identifier=bad):
                coverage = copy.deepcopy(self.coverage)
                coverage["sources"]["records"][0]["id"] = bad
                (self.root / stats.COVERAGE_JSON).write_text(json.dumps(coverage), encoding="utf-8")
                self.assertRefused("not a plain identifier")

    def test_missing_or_wrongly_typed_field(self):
        cases = (
            ("assets", "unique_files", None, "must be a non-negative integer"),
            ("assets", "gallery_images", True, "must be a non-negative integer"),
            ("songs", "denominator", -1, "must be a non-negative integer"),
        )
        for section, key, value, fragment in cases:
            with self.subTest(field=key):
                coverage = copy.deepcopy(self.coverage)
                coverage[section][key] = value
                (self.root / stats.COVERAGE_JSON).write_text(json.dumps(coverage), encoding="utf-8")
                self.assertRefused(fragment)
        del self.coverage["bytes"]["staged_site_estimate"]
        self.save()
        self.assertRefused("missing field 'bytes.staged_site_estimate'")

    def test_inconsistent_budget_flag(self):
        self.coverage["bytes"]["within_pages_budget"] = False
        self.save()
        self.assertRefused("within_pages_budget")

    def test_records_that_do_not_add_up(self):
        self.coverage["candidates"]["total"] += 1
        self.save()
        self.assertRefused("do not add up")


class DocumentTests(Base):
    def test_stale_block_is_reported_and_not_rewritten(self):
        self.render()
        self.coverage["songs"]["unknown_illustrator"] = max(0, self.coverage["songs"]["unknown_illustrator"] - 1)
        self.coverage["gaps"]["unknown_illustrator_songs"] = self.coverage["gaps"]["unknown_illustrator_songs"][1:]
        self.save()
        before = self.snapshot()
        status, _, err = run("--root", str(self.root), "--check")
        self.assertEqual(status, 1)
        self.assertIn("Out-of-date statistics block: README.md", err)
        self.assertIn("Out-of-date statistics block: docs/coverage.zh-CN.md", err)
        self.assertEqual(self.snapshot(), before, "--check never writes")

    def test_hand_edited_block_is_stale(self):
        self.render()
        path = self.root / "docs/coverage.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(stats.GENERATED, stats.GENERATED + "\nedited"), encoding="utf-8"
        )
        status, _, err = run("--root", str(self.root), "--check")
        self.assertEqual(status, 1)
        self.assertEqual(err.strip(), "Out-of-date statistics block: docs/coverage.md")

    def test_broken_markers_are_refused_before_anything_is_written(self):
        cases = (("", "needs exactly one"), (f"{stats.BEGIN}\n{stats.END}\n", "needs exactly one"))
        for extra, fragment in cases:
            with self.subTest(extra=extra):
                path = self.root / "docs/sources-official.zh-CN.md"
                original = (ROOT / "docs/sources-official.zh-CN.md").read_text(encoding="utf-8")
                text = original.replace(stats.END + "\n", "") if not extra else original + extra
                path.write_text(text, encoding="utf-8")
                self.assertRefused(fragment)
                path.write_text(original, encoding="utf-8")

    def test_markers_in_the_wrong_order(self):
        path = self.root / "README.md"
        text = path.read_text(encoding="utf-8").replace(stats.BEGIN, "@@").replace(stats.END, stats.BEGIN)
        path.write_text(text.replace("@@", stats.END), encoding="utf-8")
        self.assertRefused("needs exactly one")

    def test_symlinked_document_is_refused(self):
        target = self.root / "elsewhere.md"
        shutil.copyfile(self.root / "README.md", target)
        (self.root / "README.md").unlink()
        (self.root / "README.md").symlink_to(target)
        self.assertRefused("README.md: not a regular file")

    def test_crlf_document_keeps_its_line_ends(self):
        path = self.root / "docs/sources-archives.md"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.render()
        data = path.read_bytes()
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""), "every line end stays CRLF")
        self.assertIn(stats.GENERATED.encode("utf-8") + b"\r\n", data)
        self.assertEqual(run("--root", str(self.root), "--check")[0], 0)


class FormattingTests(unittest.TestCase):
    def test_percent_rounds_half_up_with_integers(self):
        self.assertEqual(
            [
                stats.percent(1, 3),
                stats.percent(2, 3),
                stats.percent(1, 8),
                stats.percent(1, 2000),
                stats.percent(0, 5),
                stats.percent(5, 5),
                stats.percent(1, 0),
            ],
            ["33.3%", "66.7%", "12.5%", "0.1%", "0.0%", "100.0%", stats.DASH],
        )

    def test_sizes_in_bytes_and_decimal_megabytes(self):
        self.assertEqual(stats.size("en", 950_000_000), "950,000,000 B (950.0 MB)")
        self.assertEqual(stats.size("en", 1_050_000), "1,050,000 B (1.1 MB)")
        self.assertEqual(stats.size("en", 49_999), "49,999 B (0.0 MB)")
        self.assertEqual(stats.size("zh-CN", 2_225_467_483), "2,225,467,483 字节（2,225.5 MB）")
        self.assertEqual(stats.size("en", None), "not built")

    def test_over_budget_and_empty_denominator(self):
        coverage = copy.deepcopy(BUILD[stats.COVERAGE_JSON])
        coverage["bytes"].update(staged_site_estimate=960_000_000, within_pages_budget=False)
        self.assertIn("over the budget", stats.staged_verdict("en", coverage))
        self.assertIn("超出预算", stats.staged_verdict("zh-CN", coverage))
        coverage["songs"] = {
            key: (0 if isinstance(value, int) else {k: 0 for k in value}) if not isinstance(value, str) else value
            for key, value in coverage["songs"].items()
        }
        self.assertEqual(stats.song_share("en", coverage, "songs.with_any_artwork"), f"0 ({stats.DASH})")

    def test_reasons_rank_by_count_then_name_and_skip_zero(self):
        values = {"b": 2, "a": 2, "c": 5, "z": 0}
        self.assertEqual(stats.reasons("en", values), "`c` 5, `a` 2, `b` 2")
        self.assertEqual(stats.reasons("zh-CN", values, 1), "`c` 5")
        self.assertEqual(stats.reasons("en", {"z": 0}), "none")

    def test_unknown_enumeration_values_print_as_code(self):
        self.assertEqual(
            stats.labelled("en", "status", {"available": 2, "new_status": 1}), "2 available, 1 `new_status`"
        )
        self.assertEqual(stats.labelled("zh-CN", "status", {"available": 2}), "可获取 2")

    def test_document_list_matches_the_documentation_tests(self):
        self.assertEqual(set(stats.DOCUMENTS), set(rules.STATS_DOCUMENTS))
        self.assertTrue(stats.GENERATED.startswith(rules.GENERATED_NOTICE))
        for english, chinese in rules.PAIRS:
            if english in stats.DOCUMENTS:
                self.assertEqual(stats.DOCUMENTS[english][0], stats.DOCUMENTS[chinese][0])
                self.assertEqual((stats.DOCUMENTS[english][1], stats.DOCUMENTS[chinese][1]), ("en", "zh-CN"))

    def test_both_languages_define_the_same_labels(self):
        en, zh = stats.TEXT["en"], stats.TEXT["zh-CN"]
        self.assertEqual(set(en), set(zh))
        for key, value in en.items():
            if isinstance(value, dict):
                self.assertEqual(set(value), set(zh[key]), key)
            else:
                self.assertEqual(
                    re.findall(r"\{\w+\}", value),
                    re.findall(r"\{\w+\}", zh[key]),
                    f"{key}: the same placeholders in the same order",
                )


if __name__ == "__main__":
    unittest.main()
