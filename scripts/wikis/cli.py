"""Command line of ``scripts/fetch_wikis.py`` (09-frozen-contracts.md, CLI contract).

``--metadata-only`` enumerates and classifies without downloading; the default run also downloads accepted
candidates; ``--resume`` re-hashes already archived files instead of trusting their presence (a normal run checks
presence and size); ``--verify`` is offline. ``--source`` selects which snapshots are written and which source is
acquired: the two English wikis are always enumerated together because classification pools their evidence.
Exit codes: 0 done; 1 failed/blocked downloads or a classification warning; 2 incomplete discovery; 3 another run
holds the lock; 130 interrupted (outputs written first).
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path
from typing import Any, Callable

from lib import manifest, net, paths, schema

from . import acquire, config, discover, jawikis, outputs, songindex, verify

REPO_ROOT = Path(__file__).resolve().parents[2]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="fetch_wikis.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--metadata-only", action="store_true", help="discover and classify; download nothing")
    parser.add_argument("--resume", action="store_true",
                        help="continue an acquisition, re-hashing archived files instead of trusting existence")
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="at most N download attempts")
    parser.add_argument("--source", choices=sorted(config.SOURCES), help="write/acquire only this source")
    parser.add_argument("--include-animated", action="store_true",
                        help="also accept animated GIF captures (excluded as animated_capture by default)")
    parser.add_argument("--verify", action="store_true", help="offline verification of manifest, snapshots, files")
    parser.add_argument("--root", default=None, help="repository root (default: this checkout)")
    parser.add_argument("--only", action="append", default=[], metavar="TITLE",
                        help="download only these candidates (File: title or candidate ID; repeatable)")
    parser.add_argument("--work-dir", default=None,
                        help="journal/lock directory outside the repository (default: a per-root temp directory)")
    parser.add_argument("--lock-file", default=None, help="lock file (default: <work dir>/lock)")
    parser.add_argument("--cache-dir", default=None,
                        help="reuse raw API responses stored here (crash recovery of one discovery; never reuse "
                             "an old cache for a fresh snapshot)")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be >= 0")
    if args.verify and (args.metadata_only or args.resume or args.only):
        parser.error("--verify runs alone")
    return args


class _Interrupted(Exception):
    pass


class Runner:
    def __init__(self, root: Path, args: argparse.Namespace, work_dir: Path, *,
                 client_factory: Callable[[], Any], now: Callable[[], str] = manifest.now_utc,
                 log: Callable[[str], None] = print):
        self.root, self.args, self.work_dir = root, args, work_dir
        self.client_factory, self.now, self.log = client_factory, now, log
        self.selected = [args.source] if args.source else list(config.SOURCES)
        self.journal = acquire.Journal(work_dir / "journal.jsonl")
        self.inventories: dict[str, discover.Inventory] = {}
        self.ja: dict[str, list[jawikis.ListPage]] = {}
        self.ja_times: dict[str, tuple[str, str]] = {}
        self.plans: dict[str, list[discover.Plan]] = {}
        self.candidates: dict[str, list[dict]] = {}
        self.index: songindex.SongIndex | None = None
        self.warnings: list[str] = []

    @property
    def mediawiki_selected(self) -> list[str]:
        return [key for key in config.MEDIAWIKI_KEYS if key in self.selected]

    def run(self) -> int:
        self.state = acquire.State.load(self.root, self.journal)
        replayed = len(self.journal.read())
        if replayed:
            self.log(f"replayed {replayed} journal events from an interrupted run")
        with self.client_factory() as client:
            self.discover(client)
            self.write()
            if not self.args.metadata_only and self.mediawiki_selected:
                self.acquire(client)
        return self.exit_code()

    def discover(self, client: Any) -> None:
        if self.mediawiki_selected:
            for key in config.MEDIAWIKI_KEYS:
                self.log(f"discovering {config.SOURCES[key].source_id} ...")
                inventory = discover.fetch_inventory(client, config.SOURCES[key], cache_dir=self.args.cache_dir,
                                                     now=self.now)
                self.inventories[key] = inventory
                self.log(f"  {len(inventory.files)} files, {len(inventory.pages)} articles, {inventory.requests} "
                         f"requests, complete={inventory.complete}, errors={len(inventory.errors)}")
        for key in config.HTML_KEYS:
            if self.mediawiki_selected or key in self.selected:
                started = self.now()
                self.ja[key] = jawikis.fetch_lists(client, config.SOURCES[key])
                self.ja_times[key] = (started, self.now())
                self.log(f"  {key}: {sum(len(page.rows) for page in self.ja[key])} song rows, "
                         f"{sum(1 for page in self.ja[key] if page.error)} page errors")
        if not self.inventories:
            return
        self.index = songindex.build(self.inventories, self.ja)
        self.plans = discover.build_plans(self.inventories, self.index, include_animated=self.args.include_animated)
        for key in self.mediawiki_selected:
            candidates, warnings = acquire.resolve(self.plans[key], self.state,
                                                   complete=self.inventories[key].complete)
            self.candidates[key] = candidates
            self.warnings.extend(warnings)
        for warning in self.warnings:
            self.log(f"WARNING {warning}")

    def write(self) -> None:
        """Validate and atomically write snapshots, the manifest and the song index; then clear the journal."""
        snapshots: dict[str, dict] = {}
        for key in self.mediawiki_selected:
            source = config.SOURCES[key]
            others = [config.SOURCES[k].source_id for k in config.MEDIAWIKI_KEYS if k != key]
            snapshot = outputs.mediawiki_snapshot(self.inventories[key], acquire.apply_outcomes(
                self.candidates[key], self.state), pooled_with=others)
            snapshots[source.source_id] = snapshot
            self.state.sources[source.source_id] = outputs.mediawiki_source(
                source, snapshot, self.state.assets.values(), self.state.failures.values(),
                metadata_only=self.args.metadata_only)
        for key in config.HTML_KEYS:
            if key in self.selected and key in self.ja:
                source = config.SOURCES[key]
                started, finished = self.ja_times[key]
                snapshot = outputs.html_snapshot(source, self.ja[key], started_at=started, finished_at=finished)
                snapshots[source.source_id] = snapshot
                self.state.sources[source.source_id] = outputs.html_source(source, snapshot, self.ja[key],
                                                                           self.state.failures.values())
        data = self.state.manifest()
        schema.validate_manifest(data, "wikis")
        all_snapshots = {}
        for source_id in self.state.sources:
            all_snapshots[source_id] = snapshots.get(source_id) or manifest.load_discovery(self.root, source_id)
        schema.reconcile(data, {key: value for key, value in all_snapshots.items() if value is not None})
        index = None
        if self.index is not None and set(self.selected) == set(config.SOURCES):
            index = self.index.to_json(as_of=min(s["finished_at"] for s in snapshots.values())[:10],
                                       generated_from=outputs.GENERATED_FROM,
                                       artwork=songindex.artwork_ids(data["assets"]))
            schema.validate_song_index(index, {asset["id"] for asset in data["assets"]})
            schema.validate_song_links(index, data["assets"])
        for source_id, snapshot in snapshots.items():
            outputs.write_json(self.root, paths.discovery_relpath(source_id), snapshot)
        outputs.write_json(self.root, paths.manifest_relpath("wikis"), data)
        if index is not None:
            outputs.write_json(self.root, outputs.SONG_INDEX_PATH, index)
        self.journal.clear()

    def acquire(self, client: Any) -> None:
        acquirer = acquire.Acquirer(self.root, self.state, self.journal, client, now=self.now, log=self.log)
        flusher = acquire.Flusher(self.write)
        only = set(self.args.only)
        attempts = 0
        totals: dict[str, int] = {}
        try:
            for key in self.mediawiki_selected:
                for plan, candidate in zip(self.plans[key], self.candidates[key]):
                    if self.args.limit is not None and attempts >= self.args.limit:
                        break
                    if only and not ({plan.title, plan.candidate_id, discover.upstream_key(plan.title)} & only):
                        continue
                    if not acquirer.needs_download(candidate, resume=self.args.resume, retry_failed=True):
                        continue
                    attempts += 1
                    outcome = acquirer.download(plan)
                    totals[outcome.disposition] = totals.get(outcome.disposition, 0) + 1
                    self.log(f"  [{attempts}] {outcome.disposition:15} {plan.candidate_id}"
                             + (f" -> {outcome.asset_id}" if outcome.asset_id else "")
                             + (f" ({outcome.message})" if outcome.message else ""))
                    flusher.tick()
        finally:
            self.write()
            self.log(f"downloads this run: {attempts} attempts, " + ", ".join(
                f"{name} {count}" for name, count in sorted(totals.items())))

    def exit_code(self) -> int:
        if any(not self.inventories[key].complete for key in self.mediawiki_selected):
            return 2
        if any(page.error for key in self.selected if key in self.ja for page in self.ja[key]):
            return 2
        failed = [f for f in self.state.failures.values()
                  if any(f["source_id"] == config.SOURCES[key].source_id for key in self.mediawiki_selected)]
        return 1 if failed or self.warnings else 0


def _raise_interrupt(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt(f"signal {signum}")


def main(argv: list[str] | None = None, *, client_factory: Callable[[], Any] | None = None,
         now: Callable[[], str] = manifest.now_utc, log: Callable[[str], None] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve() if args.root else REPO_ROOT
    log = log or (lambda message: print(message, flush=True))
    if args.verify:
        return verify.run(root, log=log)
    work_dir = Path(args.work_dir) if args.work_dir else acquire.default_work_dir(root)
    lock = Path(args.lock_file) if args.lock_file else work_dir / "lock"
    factory = client_factory or (lambda: net.PoliteClient(config.ALL_HOSTS))
    previous = signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        with acquire.exclusive_lock(lock):
            return Runner(root, args, work_dir, client_factory=factory, now=now, log=log).run()
    except acquire.LockHeld as error:
        print(str(error), file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("interrupted; outputs written up to the last completed download", file=sys.stderr)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous)
