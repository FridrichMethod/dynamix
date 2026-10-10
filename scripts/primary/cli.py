"""Command line shared by ``fetch_artists.py``, ``fetch_official.py`` and ``fetch_archives.py`` (09 §CLI contract)."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import logging
import os
import sys
import tempfile
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
                        help="exclusive lock file (default: per root and family in the system temp directory); a "
                             "second acquisition of the same family exits instead of writing the same manifest")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every request decision")
    return parser


def default_lock_path(root: Path, family: str) -> Path:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"dynamix-fetch-{family}-{digest}.lock"


def acquire_lock(path: Path) -> IO[str] | None:
    """Hold an exclusive ``fcntl.flock`` on ``path`` for the life of the process; None when another process holds it.

    The kernel releases the lock when the holder exits or is killed, so a crashed run never leaves a stale lock.
    """
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
    try:
        return path.read_text(encoding="utf-8").strip() or "?"
    except OSError:
        return "?"


def print_counts(counts: dict) -> None:
    for source_id, values in sorted(counts.items()):
        summary = ", ".join(f"{name} {value}" for name, value in values.items() if value) or "no candidates"
        print(f"  {source_id}: {summary}")


def verify(root: Path, family: str) -> int:
    errors, warnings, counts = runner.verify_family(root, family)
    print(f"verify {family} ({paths.manifest_relpath(family)} under {root}):")
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
        return verify(root, family)
    seeds_path = args.seeds or runner.SEEDS_DIR / f"{family}.json"
    seeds = runner.load_seeds(seeds_path, family)
    lock_path = (args.lock or default_lock_path(root, family)).resolve()
    lock = acquire_lock(lock_path)
    if lock is None:
        print(f"fetch_{family}.py: another acquisition holds {lock_path} (pid {lock_holder(lock_path)}); "
              "not starting a second writer of the same manifest", file=sys.stderr)
        return 3
    try:
        acquisition = runner.Runner(root, family, seeds, metadata_only=args.metadata_only, resume=args.resume)
        try:
            counts = acquisition.run(args.source)
        except KeyboardInterrupt:
            print("interrupted: completed candidates are saved; rerun with --resume", file=sys.stderr)
            return 130
    finally:
        lock.close()
    print(f"fetch {family}: {acquisition.downloads} download(s)")
    print_counts(counts)
    return verify(root, family)
