"""Command line shared by ``fetch_artists.py``, ``fetch_official.py`` and ``fetch_archives.py`` (09 §CLI contract).

Every acquisition holds the family lock on ``<root>/assets/public/<family>`` (``--lock`` adds a second lock, it never
replaces this one), so two writers of the same manifest cannot run at once. Exit status: 0 done; 1 ``--verify`` errors,
a source that stopped on an unexpected error, or candidates left ``failed`` or ``blocked`` (incomplete acquisition, as
``fetch_wikis.py`` reports failed/blocked downloads); 2 invalid option combination; 3 a lock is held; 130 interrupted.
"""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import sys
from pathlib import Path
from typing import IO, Sequence

from lib import paths

from . import runner

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def build_parser(family: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"fetch_{family}.py",
        description=f"Acquire the {family} source family from explicit seeds (scripts/primary/seeds/{family}.json) "
                    f"into data/sources/{family}.json, data/discovery/<source>.json and assets/public/{family}/.")
    parser.add_argument("--metadata-only", action="store_true",
                        help="discover and record candidates without downloading; archived records stay unchanged")
    parser.add_argument("--resume", action="store_true",
                        help="reuse archived files (re-hashed and decoded) instead of downloading them again")
    parser.add_argument("--verify", action="store_true",
                        help="offline: validate manifest, discovery snapshots, reconciliation and every file")
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT, help="repository root (default: this checkout)")
    parser.add_argument("--seeds", type=Path, default=None, help="seed file (default: scripts/primary/seeds/...)")
    parser.add_argument("--source", action="append", default=[], metavar="ID",
                        help="only this source ID (repeatable); other sources' records are left as they are")
    parser.add_argument("--lock", type=Path, default=None,
                        help="an additional exclusive lock file or directory; the family lock on its asset "
                             "directory under the root (independent of TMPDIR) is always held, so a second "
                             "acquisition of the same family exits instead of writing the same manifest")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every request decision")
    return parser


def default_lock_path(root: Path, family: str) -> Path:
    """``<root>/assets/public/<family>``: the directory every writer of this family's manifest writes into. It is
    anchored at the checkout (environment variables such as ``TMPDIR`` cannot give two writers different locks) and a
    ``flock`` on a directory creates no file in the working tree."""
    directory = paths.safe_path(root, f"{paths.ASSET_ROOT}/{family}", under=paths.ASSET_ROOT)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class DirectoryLock:
    """An exclusive ``flock`` held on a directory descriptor (released by :meth:`close` or process exit)."""

    def __init__(self, descriptor: int):
        self._descriptor: int | None = descriptor

    def close(self) -> None:
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None


def acquire_lock(path: Path) -> IO[str] | DirectoryLock | None:
    """Hold an exclusive ``fcntl.flock`` on ``path`` (a lock file, or a directory such as the default) for the life of
    the process; None when another process holds it.

    The kernel releases the lock when the holder exits or is killed, so a crashed run never leaves a stale lock.
    """
    if path.is_dir():
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            return None
        return DirectoryLock(descriptor)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def lock_holder(path: Path) -> str:
    if path.is_dir():
        return "?"
    try:
        return path.read_text(encoding="utf-8").strip() or "?"
    except OSError:
        return "?"


def print_counts(counts: dict) -> None:
    for source_id, values in sorted(counts.items()):
        summary = ", ".join(f"{name} {value}" for name, value in values.items() if value) or "no candidates"
        print(f"  {source_id}: {summary}")


def registry_ids(seeds_path: Path | None, root: Path, family: str) -> list[str] | None:
    """Seed IDs to check the manifest against: the given seed file, or this checkout's registry when ``root`` is this
    checkout; None (not checked) for another root without ``--seeds`` (e.g. a test fixture)."""
    if seeds_path is None and root != REPOSITORY_ROOT.resolve():
        return None
    return [seed["id"] for seed in runner.load_seeds(seeds_path or runner.SEEDS_DIR / f"{family}.json", family)]


def verify(root: Path, family: str, seed_ids: list[str] | None = None) -> int:
    errors, warnings, counts = runner.verify_family(root, family, seed_ids)
    print(f"verify {family} ({paths.manifest_relpath(family)} under {root}):")
    if seed_ids is None:
        print("  note: seed registry not compared (another --root without --seeds)")
    print_counts(counts)
    for warning in warnings:
        print(f"  warning: {warning}")
    for error in errors:
        print(f"  ERROR: {error}")
    print(f"  {'FAILED' if errors else 'OK'}: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


def main(family: str, argv: Sequence[str] | None = None) -> int:
    args = build_parser(family).parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    root = args.root.resolve()
    if args.verify:
        if args.metadata_only or args.resume or args.source:
            print("--verify runs offline and cannot be combined with acquisition options", file=sys.stderr)
            return 2
        return verify(root, family, registry_ids(args.seeds, root, family))
    seeds_path = args.seeds or runner.SEEDS_DIR / f"{family}.json"
    seeds = runner.load_seeds(seeds_path, family)
    lock_paths = [default_lock_path(root, family).resolve()]  # always: the root-anchored family lock
    if args.lock is not None and args.lock.resolve() not in lock_paths:
        lock_paths.append(args.lock.resolve())
    locks = []
    try:
        for lock_path in lock_paths:
            lock = acquire_lock(lock_path)
            if lock is None:
                print(f"fetch_{family}.py: another acquisition holds the lock on {lock_path} "
                      f"(pid {lock_holder(lock_path)}); not starting a second writer of the same manifest",
                      file=sys.stderr)
                return 3
            locks.append(lock)
        acquisition = runner.Runner(root, family, seeds, metadata_only=args.metadata_only, resume=args.resume)
        try:
            counts = acquisition.run(args.source)
        except KeyboardInterrupt:
            print("interrupted: completed candidates are saved; rerun with --resume", file=sys.stderr)
            return 130
    finally:
        for lock in reversed(locks):
            lock.close()
    print(f"fetch {family}: {acquisition.downloads} download(s)")
    print_counts(counts)
    code = verify(root, family, [seed["id"] for seed in seeds])
    for source_id, problem in sorted(acquisition.source_errors.items()):
        print(f"fetch_{family}.py: {source_id} stopped early ({problem}); recorded in its snapshot",
              file=sys.stderr)
    unfinished = unfinished_counts(counts)
    for source_id, left in sorted(unfinished.items()):
        print(f"fetch_{family}.py: {source_id} left " + ", ".join(f"{count} {name}" for name, count in left.items())
              + " candidate(s); acquisition incomplete", file=sys.stderr)
    return code or (1 if acquisition.source_errors or unfinished else 0)


def unfinished_counts(counts: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    """Per source, the candidates a run leaves ``failed`` or ``blocked`` (only sources that have any)."""
    result = {}
    for source_id, values in counts.items():
        left = {name: values[name] for name in ("failed", "blocked") if values.get(name)}
        if left:
            result[source_id] = left
    return result
