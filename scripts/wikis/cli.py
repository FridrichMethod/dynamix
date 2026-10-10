"""Command line of ``scripts/fetch_wikis.py`` (09-frozen-contracts.md, CLI contract).

``--metadata-only`` enumerates and classifies without downloading; the default run also downloads accepted
candidates, one worker thread per selected English wiki (their file hosts differ, so they download concurrently while
each host still sees one request at a time); ``--resume`` re-hashes already archived files instead of trusting their
presence (a normal run checks presence and size) and repairs damaged ones from identical downloaded bytes;
``--verify`` is offline. ``--source`` selects which snapshots are written and which source is acquired: the two
English wikis are always enumerated together because classification pools their evidence.

An incomplete discovery (any list of either English wiki failed) is written with ``discovery_complete: false``, its
continuation and errors; archived files keep their previous classification, nothing is downloaded and the song index
proposal is not rewritten. A reviewed withdrawal (``scripts/wikis/reviews.json``) is carried out only by an acquiring
run: ``--metadata-only`` never changes the archive; it reports the withdrawal (``WITHDRAWAL PENDING``) and keeps the
file, its record and classification. Every run on a ``--root`` takes ``<root>/.cache/fetch_wikis/root.lock``
(git-ignored) besides the ``--work-dir``/``--lock-file`` lock, and refuses to start while another work directory used
for that root holds an interrupted run's journal. The default work directory is ``<root>/.cache/fetch_wikis/work``
(private, mode 0700); see :mod:`wikis.acquire` for the checks on work directories, lock files and journals.

Exit codes: 0 done; 1 failed/blocked downloads, or an archived file that could not be repaired; 2 incomplete
discovery; 3 another run holds a lock, an interrupted run's journal is in another work directory, or the work
directory, a lock file or the journal cannot be used safely (another owner, a symlink, a journal of another root, a
corrupt or contradictory journal); 4 an archived file is no longer accepted by complete evidence and no reviewed
withdrawal covers it (nothing written; review needed); 5 internal error (an unexpected exception; traceback on stderr,
outputs written only up to the last completed write); 130 interrupted (outputs written up to the last completed
download).
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable

from lib import manifest, net, paths, schema

from . import acquire, config, discover, jawikis, outputs, reviews, songindex, verify

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
                        help="journal/lock directory, private to you (default: <root>/.cache/fetch_wikis/work)")
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
        self.journal = acquire.Journal(work_dir / "journal.jsonl", root)
        self.inventories: dict[str, discover.Inventory] = {}
        self.ja: dict[str, list[jawikis.ListPage]] = {}
        self.ja_times: dict[str, tuple[str, str]] = {}
        self.plans: dict[str, list[discover.Plan]] = {}
        self.candidates: dict[str, list[dict]] = {}
        self.index: songindex.SongIndex | None = None
        self.lock = threading.RLock()  # shared state, journal and output writes (see acquire.Acquirer)
        self.repair_failures: list[str] = []

    @property
    def mediawiki_selected(self) -> list[str]:
        return [key for key in config.MEDIAWIKI_KEYS if key in self.selected]

    @property
    def evidence_complete(self) -> bool:
        """Every pooled English-wiki inventory is complete (classification of archived files may change)."""
        return bool(self.inventories) and all(inventory.complete for inventory in self.inventories.values())

    def run(self) -> int:
        if self.mediawiki_selected:
            self.state = acquire.State.load(self.root, self.journal)
            replayed = len(self.journal.read())
            if replayed:
                self.log(f"replayed {replayed} journal events from an interrupted run")
        else:
            # Only English-wiki runs journal anything, and only they can weigh a journaled outcome against the
            # current evidence (acquire.resolve): a Japanese-list run leaves an interrupted run's journal in place.
            self.state = acquire.State.load(self.root)
            if self.journal.has_events():
                self.log("an interrupted English-wiki run left its journal; it is replayed by the next English-wiki "
                         "run (not by this one)")
        with self.client_factory() as client:
            self.discover(client)
            self.write()
            if not self.args.metadata_only and self.mediawiki_selected:
                if self.evidence_complete:
                    self.acquire(client)
                else:
                    self.log("discovery incomplete: nothing is downloaded (classification evidence is partial)")
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
        withdrawals = reviews.load_withdrawals()
        conflicts, withdrawn, deferred = [], [], []
        for key in self.mediawiki_selected:
            resolution = acquire.resolve(self.plans[key], self.state, complete=self.inventories[key].complete,
                                         evidence_complete=self.evidence_complete, withdrawals=withdrawals,
                                         defer_withdrawals=self.args.metadata_only)
            self.candidates[key] = resolution.candidates
            conflicts.extend(resolution.conflicts)
            withdrawn.extend(resolution.withdrawals)
            deferred.extend(resolution.deferred)
        if conflicts:
            raise acquire.ReclassifiedError(conflicts)
        for asset, withdrawal in deferred:  # a metadata-only run never changes the archive
            self.log(f"WITHDRAWAL PENDING {asset['id']} ({asset['candidate_id']}): {withdrawal.reason}: "
                     f"{withdrawal.evidence}; carried out by the next acquiring run")
        for asset, withdrawal in withdrawn:
            self.withdraw(asset, withdrawal)

    def withdraw(self, asset: dict, withdrawal: Any) -> None:
        """Carry out a reviewed withdrawal: journal it, delete the file, drop the record; candidates of the same source
        that pointed at it as ``duplicate_bytes`` become pending again (they are downloaded under their own title)."""
        event = {"event": "withdraw", "id": asset["id"], "path": asset["path"], "reason": withdrawal.reason}
        with self.lock:
            self.journal.append(event)
            acquire.delete_asset_file(self.root, asset["path"])
            self.state.apply(event)
        self.log(f"WITHDRAWN {asset['id']} ({asset['candidate_id']}): {withdrawal.reason}: {withdrawal.evidence}")
        for key in self.mediawiki_selected:
            plans = {plan.candidate_id: plan for plan in self.plans[key]}
            for number, candidate in enumerate(self.candidates[key]):
                if candidate.get("asset_id") == asset["id"] and candidate["disposition"] == "duplicate_bytes":
                    pending = dict(candidate, disposition="pending",
                                   reason=acquire.awaiting_reason(plans[candidate["id"]]))
                    pending.pop("asset_id", None)
                    self.candidates[key][number] = pending

    def write(self) -> None:
        """Validate and atomically write snapshots, the manifest and the song index; then clear the journal."""
        with self.lock:
            self._write()

    def _write(self) -> None:
        snapshots: dict[str, dict] = {}
        for key in self.mediawiki_selected:
            source = config.SOURCES[key]
            others = [config.SOURCES[k].source_id for k in config.MEDIAWIKI_KEYS if k != key]
            snapshot = outputs.mediawiki_snapshot(self.inventories[key], acquire.apply_outcomes(
                self.candidates[key], self.state), pooled_with=others)
            snapshots[source.source_id] = snapshot
            self.state.sources[source.source_id] = outputs.mediawiki_source(
                source, snapshot, self.state.assets.values(), self.state.failures.values(),
                metadata_only=self.args.metadata_only, last_attempt=self.state.attempts.get(source.source_id))
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
        ja_complete = all(page.error is None for pages in self.ja.values() for page in pages)
        if self.index is not None and set(self.selected) == set(config.SOURCES) and not (
                self.evidence_complete and ja_complete):
            self.log("song index proposal not rewritten: the discovery is incomplete")
        elif self.index is not None and set(self.selected) == set(config.SOURCES):
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
        if self.mediawiki_selected:  # the journal's events are in the outputs now (see run())
            self.journal.clear()

    def acquire(self, client: Any) -> None:
        """One worker thread per selected wiki; ``--limit`` bounds the attempts of all workers together. A worker
        error or an interrupt (SIGTERM/^C in the main thread) stops every worker after its current file; the outputs
        are written before the error propagates."""
        acquirer = acquire.Acquirer(self.root, self.state, self.journal, client, now=self.now, log=self.log,
                                    lock=self.lock)
        flusher = acquire.Flusher(self.write, lock=self.lock)
        only = set(self.args.only)
        counter = {"attempts": 0}
        totals: dict[str, int] = {}
        stop = threading.Event()
        errors: list[BaseException] = []

        def take_attempt() -> int | None:
            with self.lock:
                if self.args.limit is not None and counter["attempts"] >= self.args.limit:
                    return None
                counter["attempts"] += 1
                return counter["attempts"]

        def work(key: str) -> None:
            # Earlier failures last, so a --limit batch spends its attempts on new files and a failed file is retried
            # at most once per run after them; otherwise strongest evidence first, so that among same-wiki byte
            # duplicates the template/caption-named file becomes the record and the others point at it
            # (duplicate_bytes); then by candidate ID.
            order = sorted(zip(self.plans[key], self.candidates[key]),
                           key=lambda pair: (pair[1]["disposition"] == "failed", -pair[0].decision.strength,
                                             pair[0].candidate_id))
            try:
                for plan, candidate in order:
                    if stop.is_set():
                        return
                    if only and not ({plan.title, plan.candidate_id, discover.upstream_key(plan.title)} & only):
                        continue
                    if not acquirer.needs_download(plan, candidate, resume=self.args.resume, retry_failed=True):
                        continue
                    number = take_attempt()
                    if number is None:
                        return
                    outcome = acquirer.download(plan)
                    with self.lock:
                        totals[outcome.disposition] = totals.get(outcome.disposition, 0) + 1
                    self.log(f"  [{number}] {outcome.disposition:15} {plan.candidate_id}"
                             + (f" -> {outcome.asset_id}" if outcome.asset_id else "")
                             + (f" ({outcome.message})" if outcome.message else ""))
                    flusher.tick()
            except BaseException as error:  # noqa: BLE001 - re-raised in the main thread
                errors.append(error)
                stop.set()

        workers = [threading.Thread(target=work, args=(key,), name=f"fetch-{key}", daemon=True)
                   for key in self.mediawiki_selected]
        try:
            for worker in workers:
                worker.start()
            while any(worker.is_alive() for worker in workers):
                for worker in workers:
                    worker.join(0.5)
        except BaseException:
            stop.set()
            for worker in workers:
                worker.join()
            raise
        finally:
            self.repair_failures = list(acquirer.repair_failures)
            self.write()
            self.log(f"downloads this run: {counter['attempts']} attempts, " + ", ".join(
                f"{name} {count}" for name, count in sorted(totals.items())))
        if errors:
            raise errors[0]

    def exit_code(self) -> int:
        if any(not self.inventories[key].complete for key in self.mediawiki_selected):
            return 2
        if any(page.error for key in self.selected if key in self.ja for page in self.ja[key]):
            return 2
        failed = [f for f in self.state.failures.values()
                  if any(f["source_id"] == config.SOURCES[key].source_id for key in self.mediawiki_selected)]
        return 1 if failed or self.repair_failures else 0


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
        acquire.prepare_work_dir(work_dir)
        with acquire.exclusive_lock(acquire.root_lock_path(root)), acquire.exclusive_lock(lock):
            acquire.claim_work_dir(root, work_dir)
            return Runner(root, args, work_dir, client_factory=factory, now=now, log=log).run()
    except (acquire.LockHeld, acquire.UnsafeWorkDir) as error:
        print(str(error), file=sys.stderr)
        return 3
    except acquire.ReclassifiedError as error:
        for conflict in error.conflicts:
            print(f"REVIEW {conflict}", file=sys.stderr)
        print("archived files are no longer accepted by the current (complete) evidence; nothing was written. Fix "
              "the classification, or add a reviewed withdrawal to scripts/wikis/reviews.json", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print("interrupted; outputs written up to the last completed download", file=sys.stderr)
        return 130
    except Exception:  # noqa: BLE001 - reported with its own exit code (1 means failed downloads)
        traceback.print_exc()
        print("internal error: fetch_wikis.py stopped; outputs are written only up to the last completed write",
              file=sys.stderr)
        return 5
    finally:
        signal.signal(signal.SIGTERM, previous)
