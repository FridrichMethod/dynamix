"""Line endings: archived bytes are never converted, generated text is LF, and committed text has no CRLF.

Archived media, previews, license texts, vendored code and test fixtures are pinned by SHA-256, and generated outputs
are compared byte-for-byte by ``--check``. With ``core.autocrlf=true`` (the Git for Windows default) or ``core.eol=crlf``
Git rewrites LF as CRLF in files it treats as text, so ``.gitattributes`` must exempt pinned files (``-text``/``binary``)
and pin generated text to ``eol=lf``. These tests ask Git itself, for representative paths and for every tracked file.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".ico", ".ttf", ".woff", ".woff2")
PINNED_PREFIXES = ("assets/", "tests/fixtures/", "src/vendor/")
PINNED_FILES = ("LICENSE",)
# Representative paths (they need not exist): attribute -> expected value.
NEVER_CONVERTED = {
    "assets/public/wikis/miraheze/Cosmic-Pulse--0123456789ab.png": {"text": "unset", "diff": "unset"},
    "assets/public/wikis/fandom/Moonlight--0123456789ab.webp": {"text": "unset", "diff": "unset"},
    "assets/public/artists/x-ariiol_/idle--0123456789ab.gif": {"text": "unset", "diff": "unset"},
    "assets/public/official/website/key-art--0123456789ab.jpg": {"text": "unset", "diff": "unset"},
    "assets/public/archives/hkstp/press-kit--0123456789ab.pdf": {"text": "unset", "diff": "unset"},
    "assets/thumbs/0123456789abcdef.webp": {"text": "unset", "diff": "unset"},
    "assets/site/icons/icon.svg": {"text": "unset"},
    "assets/site/site.json": {"text": "unset"},
    "docs/images/screenshot.png": {"text": "unset", "diff": "unset"},
    "tests/fixtures/contract/data/sources/wikis.json": {"text": "unset"},
    "tests/fixtures/wikis/allimages-page-1.json": {"text": "unset"},
    "tests/fixtures/ui/catalog.json": {"text": "unset"},
    "LICENSE": {"text": "unset"},
    "licenses/pace-MIT.txt": {"text": "unset"},
    "src/vendor/legacy-ui.js": {"text": "unset"},
}
LF_TEXT = ("data/catalog.json", "data/catalog.js", "data/coverage.json", "data/thumbs.json", "index.html",
           "archive.html", "data/sources/wikis.json", "data/discovery/miraheze.json", "scripts/build_catalog.py",
           "src/archive.js", ".github/workflows/tests.yml", "README.md")
FILTER_SAMPLE = 200  # pinned blobs re-read through CRLF filters (evenly spread; attributes are checked for all)
FILTER_MAX_BYTES = 1 << 20
CRLF_CHECKOUTS = (("core.autocrlf=true",), ("core.autocrlf=false", "core.eol=crlf"))


def git(*args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *args], input=stdin, capture_output=True, check=True).stdout


def in_git_checkout() -> bool:
    if not shutil.which("git"):
        return False
    try:
        return Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve() == ROOT
    except (OSError, subprocess.CalledProcessError):
        return False


def attributes(paths: list[str], *names: str) -> dict[str, dict[str, str]]:
    fields = git("check-attr", "-z", "--stdin", *names, stdin="\0".join(paths).encode()).split(b"\0")[:-1]
    result: dict[str, dict[str, str]] = {path: {} for path in paths}
    for path, name, value in zip(fields[::3], fields[1::3], fields[2::3]):
        result[path.decode()][name.decode()] = value.decode()
    return result


def tracked() -> list[tuple[str, str, int]]:
    """``(path, blob, size)`` of every tracked regular file."""
    rows = []
    for record in git("ls-files", "-s", "-z").split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, blob, _ = meta.decode().split(" ")
        if mode in ("100644", "100755"):
            rows.append((name.decode(), blob, 0))
    sizes = git("cat-file", "--batch-check=%(objectname) %(objectsize)",
                stdin="".join(f"{blob}\n" for _, blob, _ in rows).encode()).decode().split("\n")
    return [(path, blob, int(line.split()[1])) for (path, blob, _), line in zip(rows, sizes)]


def raw_contents(blobs: list[str]) -> list[bytes]:
    """Blob contents as stored, through one ``git cat-file --batch``."""
    data, contents, offset = git("cat-file", "--batch", stdin="".join(f"{blob}\n" for blob in blobs).encode()), [], 0
    for _ in blobs:
        header_end = data.index(b"\n", offset)
        size = int(data[offset:header_end].split()[2])
        contents.append(data[header_end + 1:header_end + 1 + size])
        offset = header_end + 1 + size + 1
    return contents


def checkout_bytes(path: str, blob: str, *config: str) -> bytes:
    """The bytes a checkout with ``config`` would write for ``blob`` at ``path`` (one call per file: in batch mode
    ``--filters`` reports the unfiltered size, so its output cannot be split reliably)."""
    options = [part for setting in config for part in ("-c", setting)]
    return git(*options, "cat-file", "--filters", f"--path={path}", blob)


def is_pinned(path: str) -> bool:
    return (path.startswith(PINNED_PREFIXES) or path in PINNED_FILES or path.lower().endswith(MEDIA_EXTENSIONS)
            or (path.startswith("licenses/") and path.endswith(".txt")))


@unittest.skipUnless(in_git_checkout(), "needs git and a Git checkout of this repository")
class AttributeTests(unittest.TestCase):
    def test_archived_and_pinned_paths_are_never_converted(self):
        found = attributes(list(NEVER_CONVERTED), "text", "diff")
        for path, expected in NEVER_CONVERTED.items():
            for name, value in expected.items():
                with self.subTest(path=path, attribute=name):
                    self.assertEqual(found[path][name], value)

    def test_generated_and_source_text_is_normalized_to_lf(self):
        found = attributes(list(LF_TEXT), "text", "eol")
        for path in LF_TEXT:
            with self.subTest(path=path):
                self.assertNotEqual(found[path]["text"], "unset")
                self.assertEqual(found[path]["eol"], "lf")

    def test_every_tracked_pinned_file_is_exempt_from_conversion(self):
        pinned = [path for path, _, _ in tracked() if is_pinned(path)]
        found = attributes(pinned, "text")
        converted = [path for path in pinned if found[path]["text"] != "unset"]
        self.assertEqual(converted, [])


@unittest.skipUnless(in_git_checkout(), "needs git and a Git checkout of this repository")
class ContentTests(unittest.TestCase):
    def test_no_crlf_in_committed_text(self):
        bad = []
        for record in git("ls-files", "--eol", "-z").split(b"\0"):
            if not record:
                continue
            info, _, path = record.decode().partition("\t")
            index_eol, attr = info.split()[0], info.split()[2]
            if index_eol in ("i/crlf", "i/mixed") and attr != "attr/-text":
                bad.append(f"{path} ({index_eol})")
        self.assertEqual(bad, [], "commit these files with LF line endings")

    def test_crlf_checkouts_keep_pinned_bytes(self):
        pinned = [(path, blob) for path, blob, size in tracked() if is_pinned(path) and size <= FILTER_MAX_BYTES]
        if len(pinned) > FILTER_SAMPLE:
            step = len(pinned) / FILTER_SAMPLE
            pinned = [pinned[int(index * step)] for index in range(FILTER_SAMPLE)]
        self.assertTrue(pinned, "the repository tracks pinned files (at least LICENSE and the contract fixture)")
        raw = raw_contents([blob for _, blob in pinned])
        for settings in CRLF_CHECKOUTS:
            with self.subTest(settings=settings):
                changed = [path for (path, blob), before in zip(pinned, raw)
                           if checkout_bytes(path, blob, *settings) != before]
                self.assertEqual(changed, [])

    def test_crlf_filter_probe_detects_conversion(self):
        """The probe above is not vacuous: without these attributes the same Git call does produce CRLF."""
        with tempfile.TemporaryDirectory() as temporary:
            def scratch(*args: str, stdin: bytes | None = None) -> bytes:
                return subprocess.run(["git", "-C", temporary, *args], input=stdin, capture_output=True,
                                      check=True).stdout
            scratch("init", "-q")
            blob = scratch("hash-object", "-w", "--stdin", stdin=b"a\nb\n").decode().strip()
            self.assertEqual(scratch("-c", "core.autocrlf=true", "cat-file", "--filters", "--path=t.txt", blob),
                             b"a\r\nb\r\n")
        readme = next(blob for path, blob, _ in tracked() if path == "README.md")
        self.assertEqual(checkout_bytes("README.md", readme, "core.autocrlf=true"), raw_contents([readme])[0],
                         "README.md is eol=lf, so even core.autocrlf=true keeps LF")

if __name__ == "__main__":
    unittest.main()
