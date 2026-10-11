"""Helpers for the catalog, thumbnail, coverage and provenance tests (not a test module itself).

Every test works on a temporary copy of the committed contract fixture (tests/fixtures/contract) and adds synthetic
records through these helpers, which write the bytes at their contract path and build schema-valid Asset records
with the shared library, so the builders see exactly what a fetcher would produce. Nothing touches the network or the
repository's own data/ and assets/.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import quote

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
CONTRACT = ROOT / "tests/fixtures/contract"
STUB_DIR = Path(__file__).resolve().parent / "stub"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(STUB_DIR) not in sys.path:
    sys.path.append(str(STUB_DIR))  # after scripts/: the real render_slideshow wins when it exists

from lib import manifest, media, paths, schema  # noqa: E402

FETCHED = "2026-10-10T12:00:00Z"
TEMPLATE_PATH = "templates/slideshow.html"
TEMPLATE_TEXT = "<!doctype html>\n<main>\n@python-work-area\n</main>\n"
NOTES = "Synthetic catalog test record; not real artwork or a real acquisition."


def copy_contract(target: Path) -> Path:
    """A writable copy of the contract fixture's data/ and assets/ at ``target``."""
    target.mkdir(parents=True, exist_ok=True)
    for top in ("data", "assets"):
        shutil.copytree(CONTRACT / top, target / top)
    return target


def write_template(root: Path) -> None:
    template = root / TEMPLATE_PATH
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(TEMPLATE_TEXT, encoding="utf-8")


def tree_hashes(root: Path, top: str = "assets/public") -> dict[str, str]:
    """SHA-256 of every file under ``root/top`` (to prove builders never modify originals)."""
    base = root / top
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(base.rglob("*")) if path.is_file()}


# --- Manifests --------------------------------------------------------------------------------------------------------

def load_manifest(root: Path, family: str) -> dict:
    target = root / paths.manifest_relpath(family)
    return manifest.load_json(target) if target.exists() else manifest.empty_manifest(family)


def save_manifest(root: Path, family: str, value: dict) -> None:
    target = root / paths.manifest_relpath(family)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(manifest.dumps(manifest.sort_manifest(value)), encoding="utf-8")


def update_record(root: Path, record_id: str, **changes) -> dict:
    """Change fields of one manifest record in place (keys mapped to None are removed); returns the new record."""
    family = schema.source_family(record_id.rsplit(":", 1)[0])
    value = load_manifest(root, family)
    for index, record in enumerate(value["assets"]):
        if record["id"] == record_id:
            updated = {**record, **changes}
            value["assets"][index] = {key: item for key, item in updated.items() if item is not None}
            save_manifest(root, family, value)
            return value["assets"][index]
    raise KeyError(record_id)


def find_record(root: Path, **match) -> dict:
    for family in schema.FAMILIES:
        for record in load_manifest(root, family)["assets"]:
            if all(record.get(key) == item for key, item in match.items()):
                return record
    raise KeyError(match)


def source(source_id: str, name: str | None = None, **optional) -> dict:
    return {"id": source_id, "name": name or f"Fixture source {source_id}", "family": schema.source_family(source_id),
            "url": f"https://example.org/{paths.source_slug(source_id)}", "platform": "web", "status": "available",
            "checked_at": FETCHED, "notes": NOTES, **optional}


def add_record(root: Path, record: dict, source_name: str | None = None) -> dict:
    """Append a record to its family manifest, declaring its source when needed and keeping counts current."""
    family = schema.source_family(record["source_id"])
    value = load_manifest(root, family)
    sources = {item["id"]: item for item in value["sources"]}
    if record["source_id"] not in sources:
        value["sources"].append(source(record["source_id"], source_name))
    for item in value["sources"]:
        if item["id"] == record["source_id"] and "asset_count" in item:
            item["asset_count"] += 1
    value["assets"].append(record)
    value["fetched_at"] = max(filter(None, [value["fetched_at"], record["fetched_at"]]))
    save_manifest(root, family, value)
    return record


def default_title(original_filename: str) -> str:
    """A human-readable title for a synthetic record that names none: never the bare file name or stem, which
    ``build_catalog.py --verify`` rejects as a regression."""
    return f"{paths.filename_stem(original_filename).replace('_', ' ')} (fixture record)"


def make_record(root: Path, source_id: str, data: bytes, original_filename: str, *, title: str | None = None,
                kind: str = "song_art", key: str | None = None, illustrators=(), composers=(), song_titles=(),
                song_ids=(), collections=(), mapping_status: str = "unmapped", mapping_evidence=(),
                quality: dict | None = None, reported: dict | None = None, content_type: str | None = None,
                **extra) -> dict:
    """Write ``data`` at its contract path under ``root`` and return a schema-valid Asset record for it."""
    family = schema.source_family(source_id)
    fmt, _mime, ext = media.sniff(data[:media.HEAD_BYTES])
    sha256 = hashlib.sha256(data).hexdigest()
    rel = paths.asset_relpath(family, paths.source_slug(source_id), original_filename, sha256, ext)
    file = paths.safe_path(root, rel, under=paths.ASSET_ROOT)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(data)
    info = media.inspect(file)
    upstream_key = key or original_filename.replace(" ", "_")
    page_url = f"https://example.org/{paths.source_slug(source_id)}/{quote(upstream_key)}"
    reported_value = schema.make_source_reported(**(reported or {}))
    observed = schema.make_download_observed(info, content_type or info["mime"])
    record = {
        "id": schema.asset_id(source_id, sha256), "source_id": source_id,
        "candidate_id": schema.candidate_id(source_id, upstream_key),
        "title": title or default_title(original_filename), "kind": kind, "game": schema.GAME, "page_url": page_url,
        "download_url": page_url + "/file", "resolved_url": page_url + "/file", "path": rel,
        "original_filename": original_filename, "bytes": info["bytes"], "sha256": sha256, "sha1": info["sha1"],
        "format": info["format"], "mime": info["mime"], "width": info["width"], "height": info["height"],
        "fetched_at": FETCHED, "illustrators": list(illustrators), "composers": list(composers),
        "song_titles": list(song_titles), "song_ids": list(song_ids), "collections": list(collections),
        "mapping_status": mapping_status, "mapping_evidence": list(mapping_evidence),
        "quality": quality or {"source_variant": "site_original", "origin_status": "unknown",
                               "master_status": "unverified", "notes": ""},
        "rights": schema.default_rights(url=page_url), "source_reported": reported_value,
        "download_observed": observed, "comparisons": schema.compute_comparisons(reported_value, observed),
        **extra,
    }
    schema.validate_asset(record, family)
    return schema.canonical(record, "asset")


def copy_record(root: Path, original: dict, source_id: str, **fields) -> dict:
    """A record of ``source_id`` holding exactly the bytes of ``original`` (an exact cross-source duplicate). Without
    an explicit title it keeps the original's title within the family; another family titles it from its own
    (synthetic) post or page text, since only wiki titles may follow the file name."""
    data = (root / original["path"]).read_bytes()
    same_family = schema.source_family(source_id) == schema.source_family(original["source_id"])
    title = fields.pop("title", original["title"] if same_family else f"{original['title']} ({source_id} post)")
    return make_record(root, source_id, data, original["original_filename"], title=title,
                       kind=fields.pop("kind", original["kind"]), **fields)


# --- Deterministic images ---------------------------------------------------------------------------------------------

def encode(image: Image.Image, fmt: str, **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    return buffer.getvalue()


def pattern(size: tuple[int, int], mode: str = "RGB") -> Image.Image:
    """A deterministic image with detail in every channel (so the encoder has real work to do)."""
    width, height = size
    channels = [Image.linear_gradient("L").resize(size), Image.radial_gradient("L").resize(size),
                Image.linear_gradient("L").rotate(90).resize(size)]
    image = Image.merge("RGB", channels)
    if mode == "RGBA":
        alpha = Image.new("L", size, 255)
        alpha.paste(0, (0, 0, width // 3, height))
        image.putalpha(alpha)
    return image


def solid_png(size: tuple[int, int], colour: tuple[int, int, int]) -> bytes:
    return encode(Image.new("RGB", size, colour), "PNG")


def animated_gif(size: tuple[int, int], colours: list[tuple[int, int, int]]) -> bytes:
    frames = [Image.new("RGB", size, colour).convert("P") for colour in colours]
    return encode(frames[0], "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)


# --- Processes --------------------------------------------------------------------------------------------------------

def clean_environment() -> dict[str, str]:
    """The environment without GIT_* variables (a hook or linked worktree exports them)."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def run_script(name: str, *args: str) -> subprocess.CompletedProcess:
    """Run ``scripts/<name>.py`` exactly as documented: ``python -I scripts/<name>.py ...``."""
    return subprocess.run([sys.executable, "-I", str(SCRIPTS / f"{name}.py"), *args], capture_output=True, text=True,
                          timeout=300, env=clean_environment())


def git_environment() -> dict[str, str]:
    """clean_environment() without the user's and the system's Git configuration (a global core.hooksPath or
    init.templateDir, signing, identity), so a throwaway fixture repository behaves the same on every machine. A fresh
    fixture repository has no hooks of its own (GIT_TEMPLATE_DIR is dropped with the other GIT_* variables)."""
    return {**clean_environment(), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(root: Path, *args: str) -> None:
    """Run git in the fixture repository ``root`` with git_environment(). The test is skipped only when the git
    executable is missing; any failing git command fails the test with git's own message."""
    executable = shutil.which("git")
    if executable is None:
        raise unittest.SkipTest("git is not installed")
    command = [executable, "-C", str(root), *args]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, env=git_environment())
    except FileNotFoundError:
        raise unittest.SkipTest("git is not installed") from None
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed with exit code {result.returncode}: "
                             f"{(result.stderr or result.stdout).strip()}")
