"""Stage the allowlisted, Git-tracked public website into a new directory outside the checkout (GitHub Pages payload).

    python -I scripts/prepare_pages.py --output PATH [--root PATH] [--summary PATH] [--manifest PATH] [--allow-dirty]

Only files tracked by Git are candidates, and of those only:

* the page entry points and attribution files (REQUIRED_FILES must all be present; OPTIONAL_FILES when tracked);
* everything under PUBLIC_TREES (local UI, presentation assets, public source docs, license texts), except
  ``docs/implementation/**`` and any hidden or ``__pycache__`` component;
* under ``assets/public/`` exactly the files named by the ``url`` of a ``data/catalog.json`` asset whose
  ``site_original`` is true. The catalog is deduplicated by SHA-256, so the site holds one copy per SHA-256: identical
  copies kept under other sources' paths and archive-tier originals (``site_original: false``, served from the
  repository through ``archive_url``) stay out of the site;
* under ``assets/thumbs/`` exactly the catalog's ``thumb`` previews.

Scripts, tests, workflows, templates, discovery snapshots and source manifests are never staged. Symlinks, submodules,
unmerged entries and unsafe paths are refused, as is an existing output directory or one inside the checkout. Before
anything is copied the payload must fit the 950,000,000-byte budget (no single file above 100 MiB), ``data/catalog.js``
must encode the same catalog as ``data/catalog.json``, and every staged original must hash to its catalog SHA-256 and
size; after copying, every staged file is hashed again. Any failure removes the partial output.

The JSON summary (stdout, and ``--summary``) reports files and bytes per tier, what was left out, and
``manifest_sha256``: the SHA-256 of the sorted ``sha256sum``-format manifest of staged files (``--manifest`` writes it;
``sha256sum --check`` verifies a staged tree against it). The digest does not depend on time or the output path.

Adapted from FridrichMethod/deemo scripts/prepare_pages.py (Apache-2.0, commit b14bf0d5f5a05e5382c319f3fd5019a4936820b8).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:  # `python -I` does not put the script's directory on sys.path
    sys.path.insert(0, str(SCRIPTS))

from lib import manifest, paths, schema  # noqa: E402

ROOT = SCRIPTS.parent

REQUIRED_FILES = (".nojekyll", "LICENSE", "NOTICE", "README.md", "README.zh-CN.md", "archive.html",
                  "data/catalog.js", "data/catalog.json", "index.html")
OPTIONAL_FILES = ("browserconfig.xml", "data/coverage.json", "site.webmanifest")
PUBLIC_TREES = ("assets/site", "docs", "licenses", "src")
# Never staged, even if a public tree or file list were widened by mistake.
EXCLUDED_PREFIXES = ("docs/implementation/", "scripts/", "tests/", ".github/", "templates/", "data/sources/",
                     "data/discovery/")
ORIGINALS_ROOT = paths.ASSET_ROOT  # assets/public
THUMBS_ROOT = schema.THUMB_DIR  # assets/thumbs
CATALOG_JSON = "data/catalog.json"
CATALOG_JS = "data/catalog.js"
CATALOG_JS_PREFIX = "window.DYNAMIX_CATALOG = "
CATALOG_JS_SUFFIX = ";\n"
BUDGET_BYTES = schema.PAGES_BUDGET_BYTES
MAX_FILE_BYTES = schema.MAX_FILE_BYTES
TIERS = ("site_original", "preview", "page")
REGULAR_MODES = frozenset({"100644", "100755"})
CATALOG_ASSET_FIELDS = ("id", "path", "url", "sha256", "bytes", "kind", "format", "site_original", "thumb")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CHUNK_BYTES = 1 << 20
MANIFEST_FORMAT = "sha256sum: '<sha256>  <path>' per staged file, sorted by path, UTF-8, LF line endings"


class PagesError(ValueError):
    """The payload cannot be staged as requested; nothing is left in the output directory."""


@dataclass(frozen=True)
class Entry:
    """One file to stage. ``sha256``/``bytes`` are the catalog's values for a site original, else None."""

    path: str
    tier: str
    sha256: str | None = None
    bytes: int | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _excluded(rel: str) -> bool:
    parts = rel.split("/")
    hidden = any(part.startswith(".") or part == "__pycache__" for part in parts)
    return rel.startswith(EXCLUDED_PREFIXES) or (hidden and rel not in REQUIRED_FILES)


def regular_file(root: Path, rel: str, tracked: Mapping[str, str]) -> Path:
    """Checked absolute path of a tracked, regular, non-symlinked file inside ``root``."""
    mode = tracked.get(rel)
    if mode is None:
        raise PagesError(f"not tracked by Git: {rel}")
    if mode not in REGULAR_MODES:
        raise PagesError(f"symlinks, submodules and other non-regular Git entries must not be published: {rel} "
                         f"(mode {mode})")
    try:
        path = paths.safe_path(root, rel)
    except ValueError as error:
        raise PagesError(f"unsafe path {rel!r}: {error}") from None
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise PagesError(f"tracked file is missing from the checkout: {rel}") from None
    if not stat.S_ISREG(info.st_mode):
        raise PagesError(f"symlinks and special files must not be published: {rel}")
    if info.st_size > MAX_FILE_BYTES:
        raise PagesError(f"{rel} is {info.st_size} bytes, above the {MAX_FILE_BYTES}-byte single-file limit")
    return path


def load_catalog(root: Path, tracked: Mapping[str, str]) -> dict:
    """``data/catalog.json``, after checking that ``data/catalog.js`` encodes the same catalog."""
    try:
        catalog = manifest.load_json(regular_file(root, CATALOG_JSON, tracked))
        script = regular_file(root, CATALOG_JS, tracked).read_bytes().decode("utf-8")
    except (UnicodeDecodeError, ValueError) as error:
        raise PagesError(f"unreadable catalog: {error}") from None
    if not script.startswith(CATALOG_JS_PREFIX) or not script.endswith(CATALOG_JS_SUFFIX):
        raise PagesError(f"{CATALOG_JS} must be {CATALOG_JS_PREFIX!r} + JSON + {CATALOG_JS_SUFFIX!r}")
    try:
        encoded = manifest.loads(script[len(CATALOG_JS_PREFIX):-len(CATALOG_JS_SUFFIX)])
    except ValueError as error:
        raise PagesError(f"{CATALOG_JS} does not contain valid JSON: {error}") from None
    if encoded != catalog:
        raise PagesError(f"{CATALOG_JS} is stale: it does not encode the catalog in {CATALOG_JSON}; rebuild both")
    if not isinstance(catalog, dict) or catalog.get("schema_version") != schema.SCHEMA_VERSION:
        raise PagesError(f"{CATALOG_JSON}: schema_version must be {schema.SCHEMA_VERSION}")
    if not isinstance(catalog.get("assets"), list):
        raise PagesError(f"{CATALOG_JSON}: assets must be a list")
    return catalog


def _asset_problem(asset: Any) -> str | None:
    """Why a catalog asset cannot be staged safely, or None. Guards against a stale or hand-edited catalog."""
    if not isinstance(asset, dict) or any(field not in asset for field in CATALOG_ASSET_FIELDS):
        return f"needs the fields {', '.join(CATALOG_ASSET_FIELDS)}"
    try:
        paths.check_relpath(asset["path"], under=ORIGINALS_ROOT)
        tier = schema.tier_for(asset["kind"], asset["format"])
    except (TypeError, ValueError) as error:
        return str(error)
    if asset["url"] != schema.catalog_url(asset["path"]):
        return f"url must be {schema.catalog_url(asset['path'])!r} (the URL-encoded path)"
    if not isinstance(asset["sha256"], str) or not SHA256_RE.match(asset["sha256"]):
        return "sha256 must be 64 lowercase hex digits"
    if type(asset["bytes"]) is not int or asset["bytes"] < 1:
        return "bytes must be a positive integer"
    if asset["site_original"] is not (tier == "site"):
        return f"site_original must be {tier == 'site'} for kind {asset['kind']!r} and format {asset['format']!r}"
    if asset["thumb"] is not None and asset["thumb"] != schema.thumb_relpath(asset["sha256"]):
        return f"thumb must be null or {schema.thumb_relpath(asset['sha256'])!r}"
    return None


def catalog_entries(catalog: Mapping[str, Any]) -> tuple[list[Entry], list[Entry], list[dict]]:
    """Site originals and previews to stage, plus the archive-only catalog assets that stay out of the site."""
    originals: list[Entry] = []
    previews: dict[str, Entry] = {}
    archive_only: list[dict] = []
    seen_sha: dict[str, str] = {}
    for index, asset in enumerate(catalog["assets"]):
        problem = _asset_problem(asset)
        label = asset.get("id", f"#{index}") if isinstance(asset, dict) else f"#{index}"
        if problem:
            raise PagesError(f"{CATALOG_JSON}: asset {label!r}: {problem}")
        if asset["sha256"] in seen_sha:
            raise PagesError(f"{CATALOG_JSON}: assets {seen_sha[asset['sha256']]!r} and {label!r} share a SHA-256; "
                             "the catalog must be deduplicated")
        seen_sha[asset["sha256"]] = label
        if asset["site_original"]:
            originals.append(Entry(asset["path"], "site_original", asset["sha256"], asset["bytes"]))
        else:
            archive_only.append(asset)
        if asset["thumb"] is not None:
            previews.setdefault(asset["thumb"], Entry(asset["thumb"], "preview"))
    return originals, list(previews.values()), archive_only


def public_entries(tracked: Iterable[str]) -> list[Entry]:
    """Entry points, attribution files and public trees (not catalog-driven)."""
    selected = [Entry(rel, "page") for rel in REQUIRED_FILES]
    for rel in sorted(tracked):
        if rel in REQUIRED_FILES or _excluded(rel):
            continue
        if rel in OPTIONAL_FILES or any(rel.startswith(tree + "/") for tree in PUBLIC_TREES):
            selected.append(Entry(rel, "page"))
    return selected


def _unstaged(root: Path, tracked: Iterable[str], prefix: str, staged: Collection[str]) -> dict:
    left = [rel for rel in tracked if rel.startswith(prefix + "/") and rel not in staged]
    size = 0
    for rel in left:
        try:
            size += os.lstat(root / rel).st_size
        except OSError:
            pass  # reported as missing only if it were staged
    return {"files": len(left), "bytes": size}


def plan(root: Path, tracked: Mapping[str, str]) -> tuple[list[Entry], dict]:
    """Entries to stage (sorted by path, checked, budget not yet applied) and the exclusion summary."""
    missing = [rel for rel in REQUIRED_FILES if rel not in tracked]
    if missing:
        raise PagesError(f"required public files are not tracked: {missing}")
    catalog = load_catalog(root, tracked)
    originals, previews, archive_only = catalog_entries(catalog)
    entries = sorted(public_entries(tracked) + originals + previews, key=lambda entry: entry.path)
    names = [entry.path for entry in entries]
    if len(set(names)) != len(names):
        raise PagesError("a file would be staged twice")
    for entry in entries:
        if entry.path.startswith(EXCLUDED_PREFIXES):
            raise PagesError(f"excluded path selected for publication: {entry.path}")
        regular_file(root, entry.path, tracked)
    collisions = paths.casefold_collisions(names)
    if collisions:
        raise PagesError(f"staged paths collide on case-insensitive file systems: {collisions[:5]}")
    staged = set(names)
    excluded = {
        "archive_only_originals": {"files": len(archive_only), "bytes": sum(a["bytes"] for a in archive_only)},
        "unstaged_public_files": _unstaged(root, tracked, ORIGINALS_ROOT, staged),
        "unstaged_previews": _unstaged(root, tracked, THUMBS_ROOT, staged),
        "other_tracked_files": sum(1 for rel in tracked if rel not in staged and not rel.startswith(
            (ORIGINALS_ROOT + "/", THUMBS_ROOT + "/"))),
    }
    counts = {"assets": len(catalog["assets"]), "site_originals": len(originals),
              "archive_only": len(archive_only), "previews": len(previews)}
    return entries, {"excluded": excluded, "catalog": counts}


def verify_originals(root: Path, entries: Iterable[Entry]) -> None:
    """Every original to stage must still be the catalog's bytes (stale catalog or edited file otherwise)."""
    for entry in entries:
        if entry.tier != "site_original":
            continue
        path = root / entry.path
        actual_bytes, actual_sha = path.stat().st_size, sha256_file(path)
        if (actual_sha, actual_bytes) != (entry.sha256, entry.bytes):
            raise PagesError(f"{entry.path} does not match {CATALOG_JSON} (sha256 {actual_sha}, {actual_bytes} bytes; "
                             f"catalog {entry.sha256}, {entry.bytes} bytes); rebuild the catalog or restore the file")


def check_output(root: Path, output: Path) -> Path:
    if os.path.lexists(output):
        raise FileExistsError(f"output already exists (never overwritten): {output}")
    resolved, base = output.resolve(), root.resolve()
    if resolved == base or resolved.is_relative_to(base):
        raise PagesError(f"output must be outside the checkout: {output}")
    return resolved


def copy_and_hash(source: Path, destination: Path) -> str:
    """Copy into a new file (never overwriting) and return the SHA-256 of the bytes read back from the copy."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(source, "rb") as reader, open(destination, "xb") as writer:
        shutil.copyfileobj(reader, writer, CHUNK_BYTES)
    return sha256_file(destination)


def stage(root: Path, output: Path, entries: list[Entry]) -> list[tuple[str, str, int]]:
    """Copy every entry; returns ``(path, sha256, bytes)`` of each staged file, hashed from the staged copy."""
    staged = []
    for entry in entries:
        source = root / entry.path
        digest = copy_and_hash(source, output / entry.path)
        size = (output / entry.path).stat().st_size
        if entry.tier == "site_original" and (digest, size) != (entry.sha256, entry.bytes):
            raise PagesError(f"staged {entry.path} does not match the catalog (sha256 {digest}, {size} bytes)")
        if size != source.stat().st_size:
            raise PagesError(f"staged {entry.path} differs in size from the checkout")
        staged.append((entry.path, digest, size))
    return staged


def manifest_text(staged: Iterable[tuple[str, str, int]]) -> str:
    return "".join(f"{digest}  {rel}\n" for rel, digest, _ in sorted(staged))


def summarize(output: Path, staged: list[tuple[str, str, int]], entries: list[Entry], extra: dict,
              budget: int, text: str) -> dict:
    tiers = {tier: {"files": 0, "bytes": 0} for tier in TIERS}
    tier_of = {entry.path: entry.tier for entry in entries}
    for rel, _, size in staged:
        tiers[tier_of[rel]]["files"] += 1
        tiers[tier_of[rel]]["bytes"] += size
    total = sum(size for _, _, size in staged)
    return {
        "schema_version": schema.SCHEMA_VERSION, "output": str(output), "files": len(staged), "bytes": total,
        "budget_bytes": budget, "budget_remaining_bytes": budget - total, "tiers": tiers, **extra,
        "manifest_format": MANIFEST_FORMAT,
        "manifest_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def prepare(root: Path, output: Path, tracked: Mapping[str, str] | Iterable[str], *, budget: int = BUDGET_BYTES,
            dirty: Collection[str] = ()) -> tuple[dict, str]:
    """Stage the site; returns ``(summary, manifest text)``. ``tracked`` maps path -> Git mode (or lists paths)."""
    root = Path(root).resolve()
    if not isinstance(tracked, Mapping):
        tracked = dict.fromkeys(tracked, "100644")
    output = check_output(root, Path(output))
    entries, extra = plan(root, tracked)
    changed = sorted(entry.path for entry in entries if entry.path in dirty)
    if changed:
        raise PagesError(f"public files differ from the commit (commit them or pass --allow-dirty): {changed[:10]}")
    total = sum((root / entry.path).stat().st_size for entry in entries)
    if total > budget:
        raise PagesError(f"Pages payload of {total} bytes exceeds the {budget}-byte budget")
    verify_originals(root, entries)
    output.mkdir(parents=True, exist_ok=False)
    try:
        staged = stage(root, output, entries)
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise
    text = manifest_text(staged)
    return summarize(output, staged, entries, extra, budget, text), text


def git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout
    except OSError as error:  # git is not installed or cannot be started
        raise PagesError(f"cannot run git: {error}") from None


def _path_name(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PagesError(f"tracked path is not valid UTF-8: {raw!r}") from None


def tracked_entries(root: Path) -> dict[str, str]:
    """``git ls-files --stage``: path -> mode; unmerged entries are refused."""
    entries: dict[str, str] = {}
    for record in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, _, stage_number = meta.decode("ascii").split(" ")
        rel = _path_name(name)
        if stage_number != "0":
            raise PagesError(f"unmerged path in the index: {rel}")
        entries[rel] = mode
    return entries


def dirty_paths(root: Path) -> set[str]:
    """Tracked paths whose index or work-tree content differs from HEAD."""
    tokens = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=no").split(b"\0")
    dirty: set[str] = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        dirty.add(_path_name(token[3:]))
        if token[:1] in (b"R", b"C"):  # a rename/copy is followed by its source path
            dirty.add(_path_name(tokens[index]))
            index += 1
    return dirty


def head_commit(root: Path) -> str | None:
    try:
        return git(root, "rev-parse", "HEAD").decode("ascii").strip()
    except (PagesError, subprocess.CalledProcessError):
        return None


def _report_path(value: Path | None, root: Path, output: Path) -> Path | None:
    if value is None:
        return None
    resolved = value.resolve()
    for forbidden in (root.resolve(), output.resolve()):
        if resolved == forbidden or resolved.is_relative_to(forbidden):
            raise PagesError(f"{value} must be outside the checkout and the output directory")
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True, type=Path, help="new directory outside the checkout")
    parser.add_argument("--root", type=Path, default=ROOT, help="checkout to stage (default: this repository)")
    parser.add_argument("--summary", type=Path, help="also write the JSON summary here")
    parser.add_argument("--manifest", type=Path, help="write the sha256sum manifest of staged files here")
    parser.add_argument("--allow-dirty", action="store_true", help="stage public files that differ from HEAD")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        summary_path = _report_path(args.summary, root, args.output)
        manifest_path = _report_path(args.manifest, root, args.output)
        tracked = tracked_entries(root)
        dirty = set() if args.allow_dirty else dirty_paths(root)
        summary, text = prepare(root, args.output, tracked, dirty=dirty)
    except (PagesError, FileExistsError, subprocess.CalledProcessError) as error:
        print(f"prepare_pages: {error}", file=sys.stderr)
        return 1
    summary = {"commit": head_commit(root), **summary}
    rendered = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if summary_path:
        summary_path.write_text(rendered, encoding="utf-8")
    if manifest_path:
        manifest_path.write_bytes(text.encode("utf-8"))
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
