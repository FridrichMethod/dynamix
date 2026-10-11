"""Code-quality tooling: the pinned configuration agrees with itself, never fixes, and never touches pinned bytes.

pyproject.toml (ruff, mypy, coverage.py), .pre-commit-config.yaml, requirements-lint.txt, package.json with
package-lock.json, eslint.config.mjs, .gitignore and .editorconfig are the executable form of the tooling contract in
docs/implementation/09-frozen-contracts.md ("Tooling contract"). These tests keep the tool versions equal across those
files, keep every hook and formatter away from the byte-pinned (.gitattributes ``-text``) and generated files, and keep
ESLint check-only. Standard library only: .pre-commit-config.yaml is read with the YAML-subset parser of
tests/test_workflows_yaml.py, and its ``# frozen: vX.Y.Z`` comments (which that parser drops) line by line. The tests
that list tracked files ask Git and are skipped only when Git (or a Git checkout) is unavailable.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_workflows_yaml import parse_yaml  # noqa: E402

PRE_COMMIT_CONFIG = ".pre-commit-config.yaml"
STAGE_1_SELECT = ["E4", "E7", "E9", "F", "W", "I", "B", "UP", "PLE"]
RUFF_EXCLUDED = {"assets", "data", "src/vendor", "tests/fixtures"}
LINT_TOOLS = {"coverage", "mypy", "pre-commit", "ruff", "types-requests"}
REMOTE_HOOK_REPOS = {
    "https://github.com/pre-commit/pre-commit-hooks",
    "https://github.com/astral-sh/ruff-pre-commit",
    "https://github.com/pre-commit/mirrors-mypy",
    "https://github.com/rhysd/actionlint",
}
RUFF_REPO, MYPY_REPO = "https://github.com/astral-sh/ruff-pre-commit", "https://github.com/pre-commit/mirrors-mypy"
# Generated outputs (they need not exist): written by the build scripts and compared byte-for-byte by --check.
GENERATED = (
    "data/catalog.json",
    "data/catalog.js",
    "data/thumbs.json",
    "data/coverage.json",
    "index.html",
    "assets/thumbs/0123456789abcdef.webp",
)
# Hand-written sources that every hook must see.
SOURCES = (
    "scripts/build_catalog.py",
    "scripts/lib/net.py",
    "src/archive.js",
    "src/slideshow.css",
    "README.md",
    "docs/coverage.md",
    ".github/workflows/tests.yml",
    "tests/test_tooling.py",
    "licenses/README.md",
    "archive.html",
    "templates/slideshow.html",
    "NOTICE",
)
# pre-commit-hooks fixers that would rewrite or re-mode files that are not ours to change.
FORBIDDEN_HOOKS = {
    "fix-byte-order-marker",
    "pretty-format-json",
    "check-shebang-scripts-are-executable",
    "check-executables-have-shebangs",
}
FIXING_OPTION = re.compile(r"^--(?:fix|fix-only|write|unsafe-fixes)(?:=|$)")
CHECK_ONLY_OPTIONS = {"--fix=no"}  # mixed-line-ending: report mixed line endings, never convert them
FROZEN_REV = re.compile(r"^\s+rev:\s+(\S+)\s+# frozen: v(\d+(?:\.\d+)+)\s*$")
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([0-9][0-9A-Za-z.+!-]*)$")
EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
NODE_ENGINES = "^20.19.0 || ^22.13.0 || >=24"
LIFECYCLE_SCRIPTS = {"preinstall", "install", "postinstall", "prepare", "prepack", "postpack"}
EDITORCONFIG_PINNED = {
    "LICENSE",
    "index.html",
    "licenses/*.txt",
    "assets/**",
    "data/**",
    "src/vendor/**",
    "tests/fixtures/**",
}
# Byproducts of the tools that must never show up in `git status` (the CI "left the checkout unchanged" steps).
IGNORED_BYPRODUCTS = (
    ".coverage",
    ".coverage.runner.1234.XaBcDeFg",
    "node_modules/eslint/package.json",
    ".mypy_cache/3.14/lib/net.data.json",
    ".ruff_cache/0.17.0/1234567890",
)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def pyproject() -> dict:
    return tomllib.loads(read("pyproject.toml"))


def pre_commit_config() -> dict:
    return parse_yaml(read(PRE_COMMIT_CONFIG))


def hooks(config: dict) -> list[tuple[str, dict]]:
    return [(repo["repo"], entry) for repo in config["repos"] for entry in repo["hooks"]]


def hook(config: dict, hook_id: str) -> dict:
    found = [entry for _, entry in hooks(config) if entry["id"] == hook_id]
    if len(found) != 1:
        raise AssertionError(f"expected exactly one {hook_id!r} hook, found {len(found)}")
    return found[0]


def requirement_lines(relative: str) -> list[str]:
    return [line.strip() for line in read(relative).splitlines() if line.strip() and not line.strip().startswith("#")]


def pins(relative: str) -> dict[str, str]:
    """``{lower-case name: version}`` of a requirements file that holds only exact ``name==version`` pins."""
    result = {}
    for line in requirement_lines(relative):
        match = PIN.match(line)
        if not match:
            raise AssertionError(f"{relative}: {line!r} is not an exact name==version pin")
        result[match.group(1).lower()] = match.group(2)
    return result


def frozen_revisions() -> list[tuple[str | None, str, str | None]]:
    """``(repo, rev, frozen version)`` for every ``rev:`` line, the repo taken from the ``- repo:`` line above it."""
    found: list[tuple[str | None, str, str | None]] = []
    repo = None
    for line in read(PRE_COMMIT_CONFIG).splitlines():
        if match := re.match(r"^\s*- repo:\s+(\S+)", line):
            repo = match.group(1)
        elif re.match(r"^\s+rev:", line):
            match = FROZEN_REV.match(line)
            found.append((repo, match.group(1), match.group(2)) if match else (repo, line.strip(), None))
    return found


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def javascript_without_comments(text: str) -> str:
    """JavaScript without its ``//`` and ``/* */`` comments; string literals (``"src/vendor/**"``) are kept whole.

    Enough for eslint.config.mjs, which has no regular-expression literal."""
    kept, index = [], 0
    while index < len(text):
        char = text[index]
        if char in "\"'`":
            end = index + 1
            while end < len(text) and text[end] != char:
                end += 2 if text[end] == "\\" else 1
            kept.append(text[index : end + 1])
            index = end + 1
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
        else:
            kept.append(char)
            index += 1
    return "".join(kept)


def editorconfig_sections() -> list[tuple[str, dict[str, str]]]:
    sections: list[tuple[str, dict[str, str]]] = []
    for raw in read(".editorconfig").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            sections.append((line[1:-1], {}))
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise AssertionError(f".editorconfig: unexpected line {raw!r}")
        if sections:
            sections[-1][1][key.strip().lower()] = value.strip().lower()
    return sections


def git(*args: str, stdin: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, input=stdin, capture_output=True, check=check, timeout=120)


def git_checkout_available() -> bool:
    if shutil.which("git") is None:
        return False
    try:
        return git("rev-parse", "--is-inside-work-tree").stdout.strip() == b"true"
    except OSError, subprocess.SubprocessError:
        return False


GIT = git_checkout_available()
NEEDS_GIT = unittest.skipUnless(GIT, "needs git and a Git checkout to list tracked files")


def tracked_files() -> list[str]:
    return [name for name in git("ls-files", "-z").stdout.decode("utf-8").split("\0") if name]


def unset_text_paths(paths: list[str]) -> set[str]:
    """Tracked paths whose ``text`` attribute is unset (``-text`` or ``binary`` in .gitattributes)."""
    output = git("check-attr", "-z", "--stdin", "text", stdin="\0".join(paths).encode("utf-8")).stdout
    fields = output.decode("utf-8").split("\0")
    triples = list(zip(fields[0::3], fields[1::3], fields[2::3], strict=False))
    if len(triples) != len(paths):
        raise AssertionError(f"git check-attr answered for {len(triples)} of {len(paths)} paths")
    return {path for path, _, value in triples if value == "unset"}


class RuffConfigurationTests(unittest.TestCase):
    ruff = pyproject()["tool"]["ruff"]

    def test_line_length_target_and_stage_1_rules(self):
        self.assertEqual(self.ruff["line-length"], 120)
        self.assertEqual(self.ruff["target-version"], "py314")
        self.assertEqual(self.ruff["lint"]["select"], STAGE_1_SELECT)
        self.assertNotIn("extend-select", self.ruff["lint"], "stage 1 is exactly the select list")

    def test_ruff_reads_python_files_only(self):
        # ruff 0.17 would otherwise also reformat Python code blocks in the hand-edited (partly published) Markdown.
        self.assertEqual(self.ruff["include"], ["*.py", "*.pyi", "**/pyproject.toml"])

    def test_pinned_paths_are_excluded_even_when_passed_explicitly(self):
        self.assertIs(self.ruff["force-exclude"], True)
        self.assertTrue(RUFF_EXCLUDED <= set(self.ruff["extend-exclude"]), self.ruff["extend-exclude"])

    def test_ruff_never_fixes_by_configuration(self):
        for option in ("fix", "unsafe-fixes"):
            with self.subTest(option=option):
                self.assertIs(self.ruff.get(option, False), False)
                self.assertIs(self.ruff["lint"].get(option, False), False)


class MypyAndCoverageConfigurationTests(unittest.TestCase):
    tool = pyproject()["tool"]

    def test_mypy_stage_1_checks_the_shared_library(self):
        self.assertEqual(self.tool["mypy"]["files"], ["scripts/lib"])

    def test_one_python_version_everywhere(self):
        self.assertEqual(
            self.tool["ruff"]["target-version"], "py" + self.tool["mypy"]["python_version"].replace(".", "")
        )
        for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            workflow = parse_yaml(path.read_text(encoding="utf-8"))
            for job_id, job in workflow["jobs"].items():
                for step in job.get("steps") or []:
                    if step.get("uses", "").startswith("actions/setup-python@"):
                        with self.subTest(workflow=path.name, job=job_id):
                            self.assertEqual(step["with"]["python-version"], self.tool["mypy"]["python_version"])

    def test_coverage_measures_scripts_in_every_subprocess(self):
        run = self.tool["coverage"]["run"]
        self.assertEqual(run["source"], ["scripts"])
        self.assertIn("subprocess", run["patch"])
        self.assertIs(run["parallel"], True, "one data file per process, merged by `coverage combine`")
        self.assertIs(run["branch"], True)

    def test_fail_under_is_an_integer_percentage(self):
        fail_under = self.tool["coverage"]["report"]["fail_under"]
        self.assertIs(type(fail_under), int, "the measured baseline, rounded down")
        self.assertGreater(fail_under, 0)
        self.assertLessEqual(fail_under, 100)


class PreCommitConfigurationTests(unittest.TestCase):
    config = pre_commit_config()

    def test_every_remote_hook_repository_is_frozen_to_a_full_sha(self):
        remote = [
            (repo["repo"], repo.get("rev")) for repo in self.config["repos"] if repo["repo"] not in ("local", "meta")
        ]
        self.assertEqual({repo for repo, _ in remote}, REMOTE_HOOK_REPOS)
        frozen = frozen_revisions()
        self.assertEqual([(repo, rev) for repo, rev, _ in frozen], remote, "one `rev:` line per remote repository")
        for repo, rev, version in frozen:
            with self.subTest(repo=repo):
                self.assertRegex(rev, r"^[0-9a-f]{40}$")
                self.assertIsNotNone(version, "end the rev line with `# frozen: vX.Y.Z`")

    def test_frozen_versions_match_requirements_lint(self):
        versions = {repo: version for repo, _, version in frozen_revisions()}
        lint = pins("requirements-lint.txt")
        self.assertEqual(versions[RUFF_REPO], lint["ruff"])
        self.assertEqual(versions[MYPY_REPO], lint["mypy"])
        self.assertLessEqual(
            version_tuple(self.config["minimum_pre_commit_version"]), version_tuple(lint["pre-commit"])
        )

    def test_mypy_hook_uses_the_pinned_runtime_and_stub_versions(self):
        mypy = hook(self.config, "mypy")
        expected = [f"{name}=={version}" for name, version in pins("requirements.txt").items()]
        expected.append(f"types-requests=={pins('requirements-lint.txt')['types-requests']}")
        self.assertEqual(sorted(dep.lower() for dep in mypy["additional_dependencies"]), sorted(expected))
        self.assertEqual(
            mypy["pass_filenames"], "false", "mypy checks [tool.mypy] files, not the files pre-commit passes"
        )
        self.assertEqual(mypy["args"], [])

    def test_no_hook_runs_a_fixing_option(self):
        for repo, entry in hooks(self.config):
            tokens = [*entry.get("args", []), *entry.get("entry", "").split()]
            for token in tokens:
                if token in CHECK_ONLY_OPTIONS:
                    continue
                with self.subTest(repo=repo, hook=entry["id"], token=token):
                    self.assertNotRegex(token, FIXING_OPTION)
        self.assertEqual(hook(self.config, "mixed-line-ending")["args"], ["--fix=no"])

    def test_fixing_option_pattern_is_not_vacuous(self):
        for token in ("--fix", "--fix=lf", "--fix-only", "--write", "--unsafe-fixes"):
            with self.subTest(token=token):
                self.assertRegex(token, FIXING_OPTION)
        for token in ("--max-warnings=0", "--no-install", "--fix-dry-run", "--check"):
            with self.subTest(token=token):
                self.assertNotRegex(token, FIXING_OPTION)

    def test_hooks_that_rewrite_or_re_mode_files_are_not_used(self):
        used = {entry["id"] for _, entry in hooks(self.config)}
        self.assertFalse(used & FORBIDDEN_HOOKS, used & FORBIDDEN_HOOKS)
        self.assertTrue({"ruff-check", "ruff-format", "mypy", "actionlint", "eslint"} <= used, used)

    def test_ruff_format_hook_receives_python_files_only(self):
        # The hook's default types_or includes markdown; ruff would reformat code blocks in the documents.
        self.assertEqual(hook(self.config, "ruff-format")["types_or"], ["python", "pyi"])

    def test_eslint_hook_is_check_only(self):
        eslint = hook(self.config, "eslint")
        self.assertEqual(eslint["language"], "system")
        entry = eslint["entry"].split()
        self.assertEqual(entry[:3], ["npx", "--no-install", "eslint"], "the locked ESLint, never a download")
        self.assertIn("--max-warnings=0", entry)
        self.assertFalse([token for token in [*entry, *eslint.get("args", [])] if token.startswith("--fix")])
        files = re.compile(eslint["files"])
        for path in ("src/archive.js", "src/i18n.js", "src/slideshow.css"):
            with self.subTest(path=path):
                self.assertRegex(path, files)
        for path in ("scripts/build_catalog.py", "eslint.config.mjs", "archive.html"):
            with self.subTest(path=path):
                self.assertNotRegex(path, files)


class ExclusionTests(unittest.TestCase):
    """pre-commit applies ``re.search(exclude, path)`` to repository-relative paths before any hook sees them."""

    exclude = re.compile(pre_commit_config()["exclude"])

    def test_generated_outputs_are_excluded(self):
        for path in GENERATED:
            with self.subTest(path=path):
                self.assertRegex(path, self.exclude)

    def test_sources_are_not_excluded(self):
        for path in SOURCES:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file(), f"{path} is not a file; update SOURCES")
                self.assertNotRegex(path, self.exclude)

    def test_ruff_and_editorconfig_exclusions_are_also_pre_commit_exclusions(self):
        for directory in pyproject()["tool"]["ruff"]["extend-exclude"]:
            with self.subTest(ruff=directory):
                self.assertRegex(f"{directory}/example.py", self.exclude)
        for glob in EDITORCONFIG_PINNED:
            sample = glob.replace("**", "x/y.json").replace("*", "x")
            with self.subTest(editorconfig=glob, sample=sample):
                self.assertRegex(sample, self.exclude)

    @NEEDS_GIT
    def test_exactly_the_pinned_and_generated_tracked_files_are_excluded(self):
        tracked = tracked_files()
        pinned = unset_text_paths(tracked)
        self.assertIn("LICENSE", pinned, "git check-attr found no -text path; the probe itself is broken")
        self.assertTrue(any(path.startswith("tests/fixtures/") for path in pinned))
        not_excluded = sorted(path for path in pinned if not self.exclude.search(path))
        self.assertEqual(not_excluded, [], "every -text path must be excluded from every hook")
        expected = {path for path in tracked if path in pinned or path.startswith("data/") or path == "index.html"}
        excluded = {path for path in tracked if self.exclude.search(path)}
        self.assertEqual(sorted(expected - excluded), [], "every pinned and generated file is excluded")
        self.assertEqual(sorted(excluded - expected), [], "only pinned and generated files are excluded")

    @NEEDS_GIT
    def test_tracked_python_under_pinned_paths_is_excluded_from_ruff(self):
        tracked = tracked_files()
        python = sorted(path for path in unset_text_paths(tracked) if path.endswith(".py"))
        self.assertTrue(python, "no pinned Python file found; the probe itself is broken")
        excluded = pyproject()["tool"]["ruff"]["extend-exclude"]
        for path in python:
            with self.subTest(path=path):
                self.assertTrue(any(path.startswith(f"{directory}/") for directory in excluded))


class PinTests(unittest.TestCase):
    def test_requirements_lint_holds_exact_pins_of_the_lint_tools(self):
        lines = requirement_lines("requirements-lint.txt")
        for line in lines:
            with self.subTest(line=line):
                self.assertRegex(line, PIN)
        names = [PIN.match(line).group(1).lower() for line in lines if PIN.match(line)]
        self.assertEqual(sorted(names), sorted(LINT_TOOLS), "one exact pin per lint tool, no includes")

    def test_package_json_is_private_and_exactly_pinned(self):
        package = json.loads(read("package.json"))
        self.assertIs(package["private"], True)
        self.assertEqual(package["engines"], {"node": NODE_ENGINES})
        self.assertFalse(package.get("dependencies"), "ESLint and its plugins are devDependencies only")
        self.assertFalse(set(package.get("scripts", {})) & LIFECYCLE_SCRIPTS)
        self.assertTrue(package["devDependencies"])
        for name, version in package["devDependencies"].items():
            with self.subTest(package=name):
                self.assertRegex(version, EXACT_VERSION, "exact versions, no ^ or ~ ranges")

    def test_package_lock_matches_package_json(self):
        package = json.loads(read("package.json"))
        lock = json.loads(read("package-lock.json"))
        self.assertEqual(lock["lockfileVersion"], 3)
        self.assertEqual(lock["name"], package["name"])
        root = lock["packages"][""]
        self.assertEqual(root["devDependencies"], package["devDependencies"])
        self.assertEqual(root.get("dependencies", {}), package.get("dependencies", {}))
        self.assertEqual(root.get("engines"), package["engines"])
        for name, version in package["devDependencies"].items():
            with self.subTest(direct=name):
                self.assertEqual(lock["packages"][f"node_modules/{name}"]["version"], version)
        locked = {key: entry for key, entry in lock["packages"].items() if key}
        self.assertGreaterEqual(len(locked), len(package["devDependencies"]))
        for key, entry in locked.items():
            with self.subTest(locked=key):
                self.assertRegex(key, r"^(?:.+/)?node_modules/(?:@[^/]+/)?[^/]+$")
                self.assertRegex(entry.get("integrity", ""), r"^sha512-[A-Za-z0-9+/]+={0,2}$")
                self.assertTrue(
                    entry.get("resolved", "").startswith("https://registry.npmjs.org/"), entry.get("resolved")
                )
                self.assertNotIn("link", entry)
                self.assertFalse(entry.get("hasInstallScript", False), "`npm ci --ignore-scripts` must be complete")


class EslintConfigurationTests(unittest.TestCase):
    code = javascript_without_comments(read("eslint.config.mjs"))

    def test_vendored_code_is_ignored(self):
        self.assertRegex(self.code, r'\{\s*ignores:\s*\[\s*"src/vendor/\*\*"\s*\]\s*,?\s*\}')

    def test_sources_are_classic_scripts(self):
        self.assertIn('sourceType: "script"', self.code)
        self.assertNotIn('sourceType: "module"', self.code)

    def test_no_fix_option(self):
        self.assertNotRegex(self.code, r"\bfix\b", "ESLint never rewrites src/")

    def test_imports_are_locked_dev_dependencies(self):
        imported = set(re.findall(r'^import\s+\w+\s+from\s+"([^"]+)";', self.code, re.MULTILINE))
        self.assertTrue(imported)
        self.assertTrue(imported <= set(json.loads(read("package.json"))["devDependencies"]), imported)

    def test_comment_stripping_is_not_vacuous(self):
        self.assertIn("--fix", read("eslint.config.mjs"), "the header comment says never to use --fix")
        self.assertNotIn("--fix", self.code)
        self.assertIn('"src/vendor/**"', self.code)


class IgnoreAndEditorTests(unittest.TestCase):
    @NEEDS_GIT
    def test_tool_byproducts_are_ignored_by_the_repository_gitignore(self):
        # -v names the deciding pattern and its file; .gitignore outranks info/exclude, so a match there decides.
        arguments = ("-c", f"core.excludesFile={os.devnull}", "check-ignore", "--no-index", "--stdin", "-z", "-v", "-n")
        result = git(*arguments, stdin="\0".join(IGNORED_BYPRODUCTS).encode("utf-8"), check=False)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        fields = result.stdout.decode("utf-8").split("\0")
        answers = {
            path: (source, pattern)
            for source, _, pattern, path in zip(fields[0::4], fields[1::4], fields[2::4], fields[3::4], strict=False)
        }
        self.assertEqual(set(answers), set(IGNORED_BYPRODUCTS))
        for path, (source, pattern) in answers.items():
            with self.subTest(path=path):
                self.assertEqual(source, ".gitignore", f"{path} is not ignored by .gitignore")
                self.assertFalse(pattern.startswith("!"), f"{path} is re-included by {pattern!r}")

    @NEEDS_GIT
    def test_no_tracked_file_is_ignored(self):
        result = git(
            "-c", f"core.excludesFile={os.devnull}", "ls-files", "-z", "--cached", "--ignored", "--exclude-standard"
        )
        self.assertEqual([name for name in result.stdout.decode("utf-8").split("\0") if name], [])

    def test_editorconfig_unsets_the_editing_rules_for_pinned_and_generated_paths(self):
        sections = editorconfig_sections()
        names = [name for name, _ in sections]
        defaults = dict(sections)["*"]
        self.assertTrue({"end_of_line", "insert_final_newline", "trim_trailing_whitespace"} <= set(defaults))
        pinned = [n for n, name in enumerate(names) if name.startswith("{") and "LICENSE" in name]
        self.assertEqual(len(pinned), 1, names)
        name, properties = sections[pinned[0]]
        self.assertEqual(set(name.strip("{}").split(",")), EDITORCONFIG_PINNED)
        self.assertGreater(pinned[0], names.index("*"), "the pinned section must come after [*] to override it")
        for key in defaults:
            with self.subTest(key=key):
                self.assertEqual(properties.get(key), "unset")
        for later_name, later in sections[pinned[0] + 1 :]:
            with self.subTest(later=later_name):
                self.assertFalse(set(later) & set(defaults), "a later section would set the rules again")


if __name__ == "__main__":
    unittest.main()
