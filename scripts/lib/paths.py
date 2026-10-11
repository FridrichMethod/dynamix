"""Slugs, contract paths and filesystem safety for the Dynamix archive (schema version 1).

Path rules come from docs/implementation/09-frozen-contracts.md (Identifiers and paths):

* assets live at ``assets/public/<family>/<source-slug>/<slug(original-name-stem)>--<sha256[:12]>.<ext>``;
* a discovery snapshot lives at ``data/discovery/<source-slug>.json``;
* ``<source-slug>`` is the part of the source ID after the family, with ``:`` turned into ``-``
  (``wikis:miraheze`` -> ``miraheze``, ``artists:x:ariiol_`` -> ``x-ariiol_``).

Only the standard library is used. Every function is pure except :func:`safe_path`, which inspects the filesystem.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

SLUG_MAX_LENGTH = 80
SLUG_FALLBACK = "file"
SONG_HASH_LENGTH = 8
ASSET_ROOT = "assets/public"
DISCOVERY_ROOT = "data/discovery"
SOURCES_ROOT = "data/sources"
# Kept in step with schema.FAMILIES / schema.FORMAT_EXT (tests/test_lib_paths.py asserts it); duplicated here so this
# module stays a dependency-free leaf that schema.py can import.
FAMILIES = ("wikis", "artists", "official", "archives")
ASSET_EXTENSIONS = ("png", "jpg", "webp", "gif", "pdf")

_DISALLOWED = re.compile(r"[^A-Za-z0-9._-]+")
_HYPHENS = re.compile(r"-{2,}")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# Windows device names cannot be used as a file or directory name, with or without an extension.
_RESERVED = re.compile(r"^(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?$", re.IGNORECASE)
# Characters that are unsafe in a path component on some supported platform (Windows), plus controls.
_UNSAFE_COMPONENT = re.compile(r'[<>:"|?*\x00-\x1f\x7f]')
_DRIVE = re.compile(r"^[A-Za-z]:")


def slug(text: str, *, lower: bool = False, max_length: int = SLUG_MAX_LENGTH) -> str:
    """Contract slug: NFKC, every run of characters outside ``[A-Za-z0-9._-]`` becomes one ``-``, hyphen runs collapse,
    leading/trailing ``-``/``.`` are trimmed, at most ``max_length`` characters, ``file`` when nothing remains.

    ``lower=True`` additionally lowercases (used for song IDs, e.g. ``song:cosmic-pulse``). Non-Latin titles such as
    ``交差して歪む過去と未来`` have no allowed characters and therefore become ``file``; asset paths stay unique through
    their hash suffix and song IDs through the title hash of :func:`song_id_base`.
    """
    value = _slug_body(text, lower=lower, max_length=max_length)
    if not value:
        return SLUG_FALLBACK
    if _RESERVED.match(value):  # e.g. "CON" or "nul.txt" cannot be a file or directory name on Windows
        value = f"{SLUG_FALLBACK}-{value}"[:max_length].rstrip("-.")
    return value


def _slug_body(text: str, *, lower: bool, max_length: int) -> str:
    """:func:`slug` before the ``file`` fallback and the reserved-name prefix ("" when nothing remains)."""
    if not isinstance(text, str):
        raise TypeError(f"slug() needs a string, got {type(text).__name__}")
    value = unicodedata.normalize("NFKC", text)
    value = _HYPHENS.sub("-", _DISALLOWED.sub("-", value)).strip("-.")
    if lower:
        value = value.lower()
    return value[:max_length].rstrip("-.")


def source_family(source_id: str) -> str:
    """The family prefix of a source ID (``wikis:miraheze`` -> ``wikis``); ValueError when it is not a family."""
    family, separator, rest = source_id.partition(":") if isinstance(source_id, str) else ("", "", "")
    if not separator or not rest or family not in FAMILIES:
        raise ValueError(f"not a source ID with a known family: {source_id!r}")
    return family


def source_slug(source_id: str) -> str:
    """Slug of a source for directories/files: the ID after the family with ``:`` -> ``-`` (``x-ariiol_``)."""
    source_family(source_id)
    return slug(source_id.split(":", 1)[1].replace(":", "-"))


def source_slug_collisions(source_ids: Iterable[str]) -> list[list[str]]:
    """Groups of distinct source IDs with the same source slug after casefold (they would share a discovery file and,
    within a family, an asset directory). Sorted groups in sorted order; empty when every slug is unique."""
    groups: dict[str, set[str]] = defaultdict(set)
    for source_id in source_ids:
        groups[source_slug(source_id).casefold()].add(source_id)
    return sorted(sorted(members) for members in groups.values() if len(members) > 1)


def discovery_relpath(source_id: str) -> str:
    """``data/discovery/<source-slug>.json`` for a source ID."""
    return f"{DISCOVERY_ROOT}/{source_slug(source_id)}.json"


def manifest_relpath(family: str) -> str:
    """``data/sources/<family>.json`` for a family."""
    if family not in FAMILIES:
        raise ValueError(f"unknown family: {family!r}")
    return f"{SOURCES_ROOT}/{family}.json"


def filename_stem(original_filename: str) -> str:
    """The upstream file name without directories or its last extension (``File:A b.png`` keeps ``File:A b``)."""
    name = re.split(r"[\\/]", original_filename)[-1]
    stem, dot, extension = name.rpartition(".")
    return stem if dot and stem and extension else name


def asset_relpath(family: str, source_slug_value: str, original_filename: str, sha256: str, ext: str) -> str:
    """``assets/public/<family>/<source-slug>/<slug(stem)>--<sha256[:12]>.<ext>`` (``ext``: the sniffed one)."""
    if family not in FAMILIES:
        raise ValueError(f"unknown family: {family!r}")
    if not source_slug_value or slug(source_slug_value) != source_slug_value:
        raise ValueError(f"not a canonical source slug: {source_slug_value!r}")
    if not isinstance(original_filename, str) or not original_filename:
        raise ValueError("original_filename must be a non-empty string")
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise ValueError(f"not a lowercase SHA-256 hex digest: {sha256!r}")
    if ext not in ASSET_EXTENSIONS:
        raise ValueError(f"unsupported asset extension: {ext!r}")
    return f"{ASSET_ROOT}/{family}/{source_slug_value}/{slug(filename_stem(original_filename))}--{sha256[:12]}.{ext}"


def check_relpath(rel: str, *, under: str | None = None) -> tuple[str, ...]:
    """Syntactic checks of a repository-relative POSIX path; returns its components.

    Rejects non-strings, empty paths, absolute paths and drive letters, backslashes, empty/``.``/``..`` components,
    control and Windows-reserved characters. ``under`` (e.g. ``assets/public``) requires that prefix component-wise.
    """
    if not isinstance(rel, str) or not rel:
        raise ValueError(f"path must be a non-empty string: {rel!r}")
    if "\\" in rel:
        raise ValueError(f"backslash in path: {rel!r}")
    if rel.startswith("/") or _DRIVE.match(rel):
        raise ValueError(f"absolute path: {rel!r}")
    parts = tuple(rel.split("/"))
    for part in parts:
        if part in ("", ".", ".."):
            raise ValueError(f"empty, '.' or '..' component in path: {rel!r}")
        if _UNSAFE_COMPONENT.search(part):
            raise ValueError(f"unsafe character in path: {rel!r}")
    if under is not None:
        prefix = tuple(under.strip("/").split("/"))
        if parts[: len(prefix)] != prefix or len(parts) == len(prefix):
            raise ValueError(f"path is not inside {under}/: {rel!r}")
    return parts


def safe_path(root: str | os.PathLike[str], rel: str, *, under: str | None = None) -> Path:
    """Absolute path of ``rel`` inside ``root`` after :func:`check_relpath` and a filesystem check.

    No existing component below ``root`` may be a symlink, and the resolved target must stay inside the resolved root,
    so a planted link cannot redirect a write. Components that do not exist yet are allowed (callers create them).
    """
    parts = check_relpath(rel, under=under)
    base = Path(root).resolve()
    current = base
    for part in parts:
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            break
        except NotADirectoryError as error:
            raise ValueError(f"a parent of {rel!r} is not a directory") from error
        if stat.S_ISLNK(mode):
            raise ValueError(f"symlinked component in path: {rel!r} ({current.relative_to(base).as_posix()})")
    target = base.joinpath(*parts)
    if not target.resolve().is_relative_to(base):
        raise ValueError(f"path escapes the root: {rel!r}")
    return target


def casefold_collisions(rel_paths: Iterable[str]) -> list[list[str]]:
    """Groups of distinct paths (or parent directories) that are equal after NFC + casefold, as on Windows/macOS.

    Every directory prefix is compared too (``a/B/x.png`` and ``a/b/y.png`` collide on ``a/B`` / ``a/b``). Each group is
    sorted and the list of groups is sorted; an empty list means no collision.
    """
    groups: dict[str, set[str]] = defaultdict(set)
    for rel in rel_paths:
        parts = PurePosixPath(rel).parts
        for end in range(1, len(parts) + 1):
            prefix = "/".join(parts[:end])
            groups[unicodedata.normalize("NFC", prefix).casefold()].add(prefix)
    return sorted(sorted(members) for members in groups.values() if len(members) > 1)


def song_title_hash(title: str) -> str:
    """First :data:`SONG_HASH_LENGTH` hex digits of SHA-256 over the NFC-normalized exact title (UTF-8)."""
    return hashlib.sha256(unicodedata.normalize("NFC", title).encode("utf-8")).hexdigest()[:SONG_HASH_LENGTH]


def song_id_base(title: str) -> str:
    """Song ID without a collision suffix; it depends on the title alone, never on which other titles exist.

    ``song:<slug(title, lower=True)>`` when the slug keeps every letter/digit of the title. When the slug is lossy
    (it would be empty, i.e. ``file``, or it drops non-ASCII letters/digits) a title hash is appended:
    ``交差して歪む過去と未来`` -> ``song:file-<8 hex>``, ``Moonlight 砂の城`` -> ``song:moonlight-<8 hex>``. Only ASCII
    titles that differ in case or punctuation can still share a base; :func:`assign_song_ids` numbers those.
    """
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"song title must be a non-empty string: {title!r}")
    body = _slug_body(title, lower=True, max_length=SLUG_MAX_LENGTH)
    lossy = not body or any(char.isalnum() and not char.isascii() for char in unicodedata.normalize("NFKC", title))
    base = slug(title, lower=True)
    return f"song:{base}-{song_title_hash(title)}" if lossy else f"song:{base}"


def assign_song_ids(titles: Iterable[str]) -> list[str]:
    """Deterministic song IDs, parallel to ``titles``.

    Titles are processed in sorted exact-title order (ties in input order). First every base ID (:func:`song_id_base`)
    goes to the first title that produces it; then each remaining title gets the smallest ``-2``, ``-3``, ... suffix
    not already taken, as the contract specifies. A title whose natural slug ends in ``-2`` therefore keeps it.
    Non-Latin and partly non-Latin titles carry a title hash, so they never collide or renumber; only ASCII titles
    differing in case/punctuation (``Cosmic Pulse``/``COSMIC PULSE``) depend on the title set, which is why song IDs
    in manifests must agree with the reconciled song index (:func:`lib.schema.validate_song_links`).
    """
    titles = list(titles)
    order = sorted(range(len(titles)), key=lambda index: (titles[index], index))
    bases = [song_id_base(title) for title in titles]
    result = [""] * len(titles)
    taken: set[str] = set()
    for index in order:
        if bases[index] not in taken:
            taken.add(bases[index])
            result[index] = bases[index]
    for index in order:
        if result[index]:
            continue
        number = 2
        while f"{bases[index]}-{number}" in taken:
            number += 1
        result[index] = f"{bases[index]}-{number}"
        taken.add(result[index])
    return result
