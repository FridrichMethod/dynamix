"""Offline tests for scripts/prepare_pages.py: the Pages publication boundary, budget, integrity and determinism."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import prepare_pages as pages  # noqa: E402
from lib import schema  # noqa: E402


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def catalog_js(catalog: dict) -> str:
    """The frozen reference encoding (compact JSON, ``<`` as \\u003c, U+2028/U+2029 escaped)."""
    body = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    body = body.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return pages.CATALOG_JS_PREFIX + body + pages.CATALOG_JS_SUFFIX


ORIGINAL = b"\x89PNG site original </script>"
ARCHIVE_PDF = b"%PDF-1.4 archive-only reference %%EOF"
ARCHIVE_PNG = b"\x89PNG archive-only gameplay screenshot"
SITE_ORIGINAL_PATH = f"assets/public/wikis/miraheze/Cosmic-Pulse--{sha256(ORIGINAL)[:12]}.png"
DUPLICATE_COPY_PATH = f"assets/public/wikis/fandom/Cosmic-Pulse--{sha256(ORIGINAL)[:12]}.png"
ARCHIVE_PDF_PATH = f"assets/public/archives/hkstp/press-kit--{sha256(ARCHIVE_PDF)[:12]}.pdf"
ARCHIVE_PNG_PATH = f"assets/public/archives/hkstp/gameplay--{sha256(ARCHIVE_PNG)[:12]}.png"

PAGE_FILES = {
    ".nojekyll": b"",
    "LICENSE": b"Apache License\nVersion 2.0, January 2004\n",
    "NOTICE": b"notice\n",
    "README.md": b"# readme\n",
    "README.zh-CN.md": "# 说明\n".encode(),
    "archive.html": b"<!doctype html><title>archive</title>\n",
    "index.html": b"<!doctype html><title>slideshow</title>\n",
    "site.webmanifest": b"{}\n",
    "browserconfig.xml": b"<browserconfig/>\n",
    "data/coverage.json": b"{}\n",
    "src/archive.js": b"// ui\n",
    "src/vendor/legacy-ui.js": b"// vendor\n",
    "assets/site/icons/icon.svg": b"<svg/>\n",
    "licenses/pace-MIT.txt": b"MIT\n",
    "docs/sources-wikis.md": b"# public source notes\n",
    "docs/sources-wikis.zh-CN.md": "# 公开来源说明\n".encode(),
}
NEVER_STAGED = {
    "docs/implementation/README.md": b"# planning packet\n",
    "docs/implementation/06-validation-and-release.md": b"# release hold\n",
    "scripts/build_catalog.py": b"print()\n",
    "scripts/lib/schema.py": b"x = 1\n",
    "tests/test_x.py": b"pass\n",
    ".github/workflows/pages.yml": b"name: x\n",
    ".gitattributes": b"* text=auto\n",
    "templates/slideshow.html": b"<!-- @python-work-area -->\n",
    "data/sources/wikis.json": b"{}\n",
    "data/discovery/miraheze.json": b"{}\n",
    "data/thumbs.json": b"{}\n",
    "src/__pycache__/cached.pyc": b"\0",
    "assets/site/.hidden": b"secret\n",
    "requirements.txt": b"Pillow\n",
    DUPLICATE_COPY_PATH: ORIGINAL,
    ARCHIVE_PDF_PATH: ARCHIVE_PDF,
    ARCHIVE_PNG_PATH: ARCHIVE_PNG,
}


def catalog_asset(asset_id, path, data, kind, fmt, thumb):
    site = schema.tier_for(kind, fmt) == "site"
    return {"id": asset_id, "path": path, "url": schema.catalog_url(path), "sha256": sha256(data),
            "bytes": len(data), "kind": kind, "format": fmt, "site_original": site,
            "thumb": schema.thumb_relpath(sha256(data)) if thumb else None, "title": "x"}


class Checkout:
    """A synthetic repository tree; ``tracked`` maps path -> Git mode as prepare() receives it from Git."""

    def __init__(self, base: Path):
        self.root = base / "checkout"
        self.root.mkdir()
        self.tracked: dict[str, str] = {}
        self.catalog = {"schema_version": 1, "assets": [
            catalog_asset("wikis:miraheze:" + sha256(ORIGINAL)[:16], SITE_ORIGINAL_PATH, ORIGINAL, "song_art", "PNG",
                          True),
            catalog_asset("archives:hkstp:" + sha256(ARCHIVE_PDF)[:16], ARCHIVE_PDF_PATH, ARCHIVE_PDF, "reference",
                          "PDF", False),
            catalog_asset("archives:hkstp:" + sha256(ARCHIVE_PNG)[:16], ARCHIVE_PNG_PATH, ARCHIVE_PNG,
                          "gameplay_screenshot", "PNG", True),
        ]}
        for rel, data in {**PAGE_FILES, **NEVER_STAGED, SITE_ORIGINAL_PATH: ORIGINAL}.items():
            self.write(rel, data)
        self.write(schema.thumb_relpath(sha256(ORIGINAL)), b"RIFF thumb a")
        self.write(schema.thumb_relpath(sha256(ARCHIVE_PNG)), b"RIFF thumb c")
        self.write("assets/thumbs/ffffffffffffffff.webp", b"RIFF stale thumb")
        self.write("src/untracked.js", b"// local only\n", tracked=False)
        self.write_catalog()

    def write(self, rel: str, data: bytes, tracked: bool = True) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if tracked:
            self.tracked[rel] = "100644"

    def write_catalog(self, js_catalog: dict | None = None) -> None:
        self.write("data/catalog.json", (json.dumps(self.catalog, ensure_ascii=False, indent=2) + "\n").encode())
        self.write("data/catalog.js", catalog_js(js_catalog or self.catalog).encode())


EXPECTED = sorted({*PAGE_FILES, "data/catalog.json", "data/catalog.js", SITE_ORIGINAL_PATH,
                   schema.thumb_relpath(sha256(ORIGINAL)), schema.thumb_relpath(sha256(ARCHIVE_PNG))})


def staged_files(output: Path) -> list[str]:
    return sorted(path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file())


class PagesTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.checkout = Checkout(self.base)
        self.output = self.base / "site"

    def prepare(self, **kwargs):
        return pages.prepare(self.checkout.root, self.output, self.checkout.tracked, **kwargs)

    def assertRefused(self, pattern, **kwargs):
        with self.assertRaisesRegex(pages.PagesError, pattern):
            self.prepare(**kwargs)
        self.assertFalse(os.path.lexists(self.output), "a refused run must not leave an output directory")


class AllowlistTests(PagesTestCase):
    def test_stages_exactly_the_allowlist_with_identical_bytes(self):
        summary, text = self.prepare()
        self.assertEqual(staged_files(self.output), EXPECTED)
        for rel in EXPECTED:
            self.assertEqual((self.output / rel).read_bytes(), (self.checkout.root / rel).read_bytes(), rel)
        self.assertEqual(summary["files"], len(EXPECTED))
        self.assertEqual(summary["bytes"], sum((self.output / rel).stat().st_size for rel in EXPECTED))
        self.assertEqual(summary["tiers"]["site_original"], {"files": 1, "bytes": len(ORIGINAL)})
        self.assertEqual(summary["tiers"]["preview"]["files"], 2)
        self.assertEqual(summary["tiers"]["page"]["files"], len(EXPECTED) - 3)
        self.assertEqual(sum(tier["bytes"] for tier in summary["tiers"].values()), summary["bytes"])
        self.assertEqual(summary["budget_remaining_bytes"], pages.BUDGET_BYTES - summary["bytes"])
        self.assertEqual(summary["catalog"], {"assets": 3, "site_originals": 1, "archive_only": 2, "previews": 2})

    def test_implementation_docs_tooling_and_snapshots_are_never_staged(self):
        self.prepare()
        staged = set(staged_files(self.output))
        for rel in NEVER_STAGED:
            self.assertNotIn(rel, staged)
        self.assertFalse((self.output / "docs/implementation").exists())
        self.assertIn("docs/sources-wikis.md", staged)
        self.assertNotIn("src/untracked.js", staged)

    def test_archive_only_originals_and_other_sources_copies_stay_out(self):
        summary, _ = self.prepare()
        for rel in (ARCHIVE_PDF_PATH, ARCHIVE_PNG_PATH, DUPLICATE_COPY_PATH):
            self.assertFalse((self.output / rel).exists(), rel)
        # The archive-tier screenshot is shown through its preview; the original stays in the repository only.
        self.assertTrue((self.output / schema.thumb_relpath(sha256(ARCHIVE_PNG))).is_file())
        originals = [rel for rel in staged_files(self.output) if rel.startswith("assets/public/")]
        self.assertEqual(originals, [SITE_ORIGINAL_PATH])
        self.assertEqual(summary["excluded"]["archive_only_originals"],
                         {"files": 2, "bytes": len(ARCHIVE_PDF) + len(ARCHIVE_PNG)})
        self.assertEqual(summary["excluded"]["unstaged_public_files"],
                         {"files": 3, "bytes": len(ORIGINAL) + len(ARCHIVE_PDF) + len(ARCHIVE_PNG)})
        self.assertEqual(summary["excluded"]["unstaged_previews"]["files"], 1)

    def test_widened_selection_still_cannot_publish_excluded_trees(self):
        with mock.patch.object(pages, "PUBLIC_TREES", pages.PUBLIC_TREES + ("scripts",)):
            self.prepare()
        self.assertFalse((self.output / "scripts").exists())
        with mock.patch.object(pages, "OPTIONAL_FILES", pages.OPTIONAL_FILES + ("docs/implementation/README.md",)):
            self.output = self.base / "site-2"
            self.prepare()
        self.assertFalse((self.output / "docs/implementation").exists())

    def test_every_required_file_is_required(self):
        for rel in pages.REQUIRED_FILES:
            with self.subTest(missing=rel):
                mode = self.checkout.tracked.pop(rel)
                self.assertRefused("required public files are not tracked")
                self.checkout.tracked[rel] = mode

    def test_tracked_but_deleted_file_fails(self):
        (self.checkout.root / "src/archive.js").unlink()
        self.assertRefused("missing from the checkout")


class SafetyTests(PagesTestCase):
    def test_symlinked_file_is_rejected(self):
        (self.checkout.root / "assets/site/leak.txt").symlink_to(self.checkout.root / "NOTICE")
        self.checkout.tracked["assets/site/leak.txt"] = "100644"
        self.assertRefused("unsafe path|symlink")

    def test_symlinked_directory_component_is_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        (self.checkout.root / "docs/linked").symlink_to(outside, target_is_directory=True)
        self.checkout.tracked["docs/linked/secret.txt"] = "100644"
        self.assertRefused("unsafe path|symlink")

    def test_git_symlink_and_submodule_entries_are_rejected(self):
        for mode in ("120000", "160000"):
            with self.subTest(mode=mode):
                self.checkout.tracked["src/archive.js"] = mode
                self.assertRefused("non-regular Git entries")
        self.checkout.tracked["src/archive.js"] = "100644"

    def test_traversal_and_out_of_tree_catalog_paths_are_rejected(self):
        for bad in ("assets/public/../../../etc/passwd", "data/catalog.json", "/etc/passwd",
                    "assets/public/wikis\\x.png", "assets/public/wikis/a:b.png"):
            with self.subTest(path=bad):
                checkout = self.checkout
                original = checkout.catalog["assets"][0]
                checkout.catalog["assets"][0] = {**original, "path": bad, "url": schema.catalog_url(bad)}
                checkout.write_catalog()
                self.assertRefused("asset 'wikis:miraheze:")
                checkout.catalog["assets"][0] = original
        self.checkout.write_catalog()
        self.prepare()

    def test_unsafe_public_path_is_rejected(self):
        self.checkout.write("docs/a:b.md", b"x")
        self.assertRefused("unsafe path")

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        (self.output / "keep.txt").write_text("user data", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual((self.output / "keep.txt").read_text(encoding="utf-8"), "user data")
        self.assertEqual(staged_files(self.output), ["keep.txt"])

    def test_dangling_symlink_output_is_refused(self):
        self.output.symlink_to(self.base / "nowhere")
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_output_inside_the_checkout_is_refused(self):
        self.output = self.checkout.root / "build/site"
        self.assertRefused("outside the checkout")

    def test_dirty_public_file_is_refused(self):
        self.assertRefused("differ from the commit", dirty={"index.html"})
        self.prepare(dirty={"scripts/build_catalog.py"})  # not published, so not relevant


class BudgetTests(PagesTestCase):
    def payload_bytes(self) -> int:
        summary, _ = pages.prepare(self.checkout.root, self.base / "probe", self.checkout.tracked)
        return summary["bytes"]

    def test_budget_is_inclusive_and_overflow_fails_before_copying(self):
        total = self.payload_bytes()
        self.assertRefused("exceeds the [0-9]+-byte budget", budget=total - 1)
        summary, _ = self.prepare(budget=total)
        self.assertEqual(summary["budget_remaining_bytes"], 0)

    def test_default_budget_is_the_contract_value(self):
        self.assertEqual(pages.BUDGET_BYTES, 950_000_000)
        self.assertEqual(schema.PAGES_BUDGET_BYTES, pages.BUDGET_BYTES)

    def test_single_file_limit(self):
        with mock.patch.object(pages, "MAX_FILE_BYTES", len(ORIGINAL) - 1):
            self.assertRefused("single-file limit")


class IntegrityTests(PagesTestCase):
    def test_stale_original_bytes_fail_before_copying(self):
        (self.checkout.root / SITE_ORIGINAL_PATH).write_bytes(ORIGINAL + b"changed")
        self.assertRefused("does not match data/catalog.json")

    def test_same_size_different_bytes_fail(self):
        (self.checkout.root / SITE_ORIGINAL_PATH).write_bytes(ORIGINAL[:-1] + b"!")
        self.assertRefused("does not match data/catalog.json")

    def test_stale_catalog_js_fails(self):
        changed = json.loads(json.dumps(self.checkout.catalog))
        changed["assets"].pop()
        self.checkout.write_catalog(js_catalog=changed)
        self.assertRefused("catalog.js is stale")

    def test_malformed_catalog_js_fails(self):
        self.checkout.write("data/catalog.js", b"var DYNAMIX_CATALOG = {};\n")
        self.assertRefused("must be 'window.DYNAMIX_CATALOG = '")

    def test_site_original_flag_must_follow_the_storage_tier(self):
        self.checkout.catalog["assets"][1]["site_original"] = True  # reference PDF is archive tier
        self.checkout.write_catalog()
        self.assertRefused("site_original must be False")

    def test_url_must_be_the_encoded_path(self):
        self.checkout.catalog["assets"][0]["url"] = DUPLICATE_COPY_PATH
        self.checkout.write_catalog()
        self.assertRefused("url must be")

    def test_untracked_catalog_preview_fails(self):
        del self.checkout.tracked[schema.thumb_relpath(sha256(ORIGINAL))]
        self.assertRefused("not tracked by Git")

    def test_duplicate_sha256_in_catalog_fails(self):
        twin = dict(self.checkout.catalog["assets"][0], id="wikis:fandom:" + sha256(ORIGINAL)[:16],
                    path=DUPLICATE_COPY_PATH, url=schema.catalog_url(DUPLICATE_COPY_PATH))
        self.checkout.catalog["assets"].append(twin)
        self.checkout.write_catalog()
        self.assertRefused("share a SHA-256")

    def test_staged_copy_is_verified_and_partial_output_removed(self):
        real = pages.copy_and_hash

        def corrupt(source, destination):
            digest = real(source, destination)
            return "0" * 64 if destination.name.endswith(".png") else digest

        with mock.patch.object(pages, "copy_and_hash", corrupt):
            self.assertRefused("staged assets/public/.* does not match the catalog")

    def test_interrupted_copy_removes_partial_output(self):
        real, calls = pages.copy_and_hash, []

        def interrupted(source, destination):
            calls.append(destination)
            if len(calls) == 3:
                raise OSError("disk full")
            return real(source, destination)

        with mock.patch.object(pages, "copy_and_hash", interrupted), self.assertRaises(OSError):
            self.prepare()
        self.assertFalse(os.path.lexists(self.output))


class DigestTests(PagesTestCase):
    def test_digest_is_deterministic_and_matches_a_sha256sum_manifest(self):
        first, text = self.prepare()
        second, text_again = pages.prepare(self.checkout.root, self.base / "elsewhere/site", self.checkout.tracked)
        self.assertEqual(text, text_again)
        self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])
        self.assertEqual(first["manifest_sha256"], sha256(text.encode("utf-8")))
        lines = text.splitlines()
        self.assertEqual([line.split("  ", 1)[1] for line in lines], EXPECTED)
        for line in lines:
            digest, rel = line.split("  ", 1)
            self.assertEqual(digest, sha256((self.output / rel).read_bytes()))
        self.assertTrue(text.endswith("\n"))

    def test_digest_changes_with_content(self):
        before, _ = self.prepare()
        self.checkout.write("src/archive.js", b"// ui changed\n")
        after, _ = pages.prepare(self.checkout.root, self.base / "site-2", self.checkout.tracked)
        self.assertNotEqual(before["manifest_sha256"], after["manifest_sha256"])


def git_available() -> bool:
    return shutil.which("git") is not None


@unittest.skipUnless(git_available(), "needs git to build a temporary repository")
class CommandLineTests(PagesTestCase):
    ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_NOSYSTEM": "1", "HOME": "/nonexistent"}

    def git(self, *args):
        env = {**os.environ, **self.ENV}
        return subprocess.run(["git", "-C", str(self.checkout.root), *args], check=True, capture_output=True,
                              env=env).stdout.decode()

    def setUp(self):
        super().setUp()
        self.git("init", "-q")
        self.git("add", "--", *sorted(self.checkout.tracked))
        self.git("commit", "-q", "-m", "fixture")

    def main(self, *extra):
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            return pages.main(["--root", str(self.checkout.root), "--output", str(self.output), *extra])

    def test_cli_stages_the_committed_tree_and_writes_evidence(self):
        summary_path, manifest_path = self.base / "summary.json", self.base / "manifest.sha256"
        self.assertEqual(self.main("--summary", str(summary_path), "--manifest", str(manifest_path)), 0)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["commit"], self.git("rev-parse", "HEAD").strip())
        self.assertEqual(staged_files(self.output), EXPECTED)
        text = manifest_path.read_text(encoding="utf-8")
        self.assertEqual(summary["manifest_sha256"], sha256(text.encode("utf-8")))
        if shutil.which("sha256sum"):
            subprocess.run(["sha256sum", "--check", "--quiet", "--strict", str(manifest_path)], cwd=self.output,
                           check=True)

    def test_script_runs_isolated_as_ci_invokes_it(self):
        result = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/prepare_pages.py"), "--root",
                                 str(self.checkout.root), "--output", str(self.output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["files"], len(EXPECTED))

    def test_cli_refuses_dirty_public_files_unless_allowed(self):
        (self.checkout.root / "archive.html").write_text("edited", encoding="utf-8")
        self.assertEqual(self.main(), 1)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.main("--allow-dirty"), 0)

    def test_cli_refuses_a_committed_symlink(self):
        (self.checkout.root / "assets/site/link.svg").symlink_to("icons/icon.svg")
        self.git("add", "assets/site/link.svg")
        self.git("commit", "-q", "-m", "symlink")
        self.assertEqual(self.main(), 1)
        self.assertFalse(self.output.exists())

    def test_cli_ignores_untracked_files(self):
        (self.checkout.root / "docs/draft.md").write_text("draft", encoding="utf-8")
        self.assertEqual(self.main(), 0)
        self.assertFalse((self.output / "docs/draft.md").exists())

    def test_cli_refuses_evidence_inside_the_checkout_or_output(self):
        self.assertEqual(self.main("--summary", str(self.checkout.root / "summary.json")), 1)
        self.assertEqual(self.main("--manifest", str(self.output / "manifest.sha256")), 1)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
