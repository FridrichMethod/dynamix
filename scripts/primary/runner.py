"""Shared acquisition runner for the artists, official and archives families.

For every seed source the runner asks its adapter for a :class:`primary.model.Discovery`, then resolves each candidate:
downloads go through :mod:`lib.net` (allowlisted, polite) into a hidden ``.part`` file and :func:`lib.media.
finalize_download` (decode, then atomic move to the contract path). The discovery snapshot and the family manifest are
validated and rewritten after **every** resolved candidate, so an interruption loses at most the download in flight;
``--resume`` then reuses archived files after re-hashing them instead of downloading again.

Robustness rules (07 §Integrity):

* A host that answers 401/403 or a bot challenge is not contacted again in the same run; later candidates on that host
  are recorded ``blocked`` without a request (no retry storm).
* Archived bytes are never dropped. A transient failure while refreshing a candidate that already has an archived file
  keeps the previous record and logs a discovery error; new bytes for the same candidate supersede the old record
  (``upstream_status: superseded``) instead of replacing it; a previous candidate that a run no longer discovers is
  carried over unchanged (with a note) so that its asset stays reconciled.
* ``--metadata-only`` never downloads and never changes archived records; not-yet-archived candidates are ``pending``.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

from PIL import Image

from lib import checks, manifest, media, net, paths, schema

from . import model
from .adapters import ADAPTERS

LOG = logging.getLogger("dynamix.primary")
SEEDS_DIR = Path(__file__).resolve().parent / "seeds"
MIN_INTERVAL = 1.0  # frozen network default: >= 1 s between requests to one host; a seed may only slow down
UNKNOWN_NOTE = ("No longer reached by the seeds of a later run whose listings were all read: upstream status "
                "unknown; the archived bytes are kept.")
KEPT_FOR_REVIEW = "is kept for review"  # discovery-error marker of an archived candidate a refresh no longer admits
WITHDRAWN_RE = re.compile(r"Withdrawn \d{4}-\d{2}-\d{2} by a curated seed rule .*?the bytes remain in the Git history\.")
# Provenance notes a refresh keeps (descriptive notes are otherwise rebuilt from the seed on every refresh).
REFETCH_NOTE = ("Re-fetched {date}: the same URL returned different bytes (sha256 {sha256}) that decode to pixels "
                "identical to the archived file, which is kept as current.")
REFETCH_NOTE_RE = re.compile(r"Re-fetched \d{4}-\d{2}-\d{2}: the same URL returned different bytes \(sha256 [0-9a-f]{64}"
                             r"\) that decode to pixels identical to the archived file, which is kept as current\.")
REREAD_NOTE = ("Re-fetched {date}{via}: the archived bytes again (download_url, resolved_url and fetched_at stay those of "
               "the acquisition that archived them).")
REREAD_NOTE_RE = re.compile(r"Re-fetched \d{4}-\d{2}-\d{2}(?: from \S+| \(same file, re-signed URL\))?: the archived bytes "
                            r"again \(download_url, resolved_url and fetched_at stay those of the acquisition that "
                            r"archived them\)\.")
ADOPTED_NOTE = ("First archived for candidate {old} (upstream_status then {status}); the same bytes are listed upstream "
                "as this record's candidate since {date}.")
ADOPTED_NOTE_RE = re.compile(r"First archived for candidate \S+ \(upstream_status then [a-z]+\); the same bytes are listed "
                             r"upstream as this record's candidate since \d{4}-\d{2}-\d{2}\.")
ALSO_LISTED_NOTE = "The same bytes are also listed upstream as {cid} (duplicate_bytes)."
TAKEN_OVER_NOTE = ("Recorded under this candidate by a curated seed rule (archive_under): the same bytes, archived "
                   "earlier for {other}, are attached to that candidate too, whose text does not name what they show.")
TAKEN_OVER_NOTE_RE = re.compile(r"Recorded under this candidate by a curated seed rule \(archive_under\): the same bytes, "
                                r"archived earlier for \S+, are attached to that candidate too, whose text does not name "
                                r"what they show\.")
SOURCE_SEED_FIELDS = {
    "id", "name", "url", "platform", "adapter", "notes", "api_url", "owner_evidence", "crawl_scope", "items",
    "hosts", "min_interval", "quality", "rights", "rules", "identity_quotes", "lists", "status_on_success",
    "unenumerated_lists", "params",
}


class HostSkipped(net.BlockedError):
    """Raised instead of contacting a host that refused an earlier request in this run (zero attempts made)."""

    skipped = True


class GuardedClient:
    """Wraps a client (``PoliteClient`` or a test double) with run-wide guards.

    * ``blocked_hosts`` (shared by all sources of a run): after a :class:`lib.net.BlockedError` from a host, further
      requests to it raise :class:`HostSkipped` without touching the network.
    * ``last_used`` (shared): at least ``min_interval`` seconds between the end of one request and the next to the same
      host even across the per-source client instances.
    * ``calls`` counts requests per URL (diagnostics and tests).
    * ``per_item_refusal=True`` (an endpoint documented to answer 401/403 for one unavailable item, e.g. YouTube's oEmbed
      for a private or non-embeddable video): a plain 401/403 is that request's result and does not block the host,
      unless :data:`ITEM_REFUSAL_LIMIT` such refusals in a row show a host-wide refusal. A bot challenge always blocks.
    """

    ITEM_REFUSAL_LIMIT = 3

    def __init__(self, client: Any, *, blocked_hosts: dict[str, str], last_used: dict[str, float],
                 min_interval: float, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self._client, self._blocked, self._last = client, blocked_hosts, last_used
        self._interval, self._sleep, self._clock = min_interval, sleep, clock
        self.calls: dict[str, int] = {}
        self._item_refusals: dict[str, int] = {}  # host -> consecutive per-item refusals

    def _host(self, url: str) -> str:
        try:
            return (urlsplit(url).hostname or "").lower()
        except ValueError:
            return ""

    def _call(self, method: str, url: str, *args: Any, per_item_refusal: bool = False, **kwargs: Any) -> Any:
        host = self._host(url)
        if host in self._blocked:
            raise HostSkipped(f"host {host} refused an earlier request in this run ({self._blocked[host]}); "
                              "not contacted again", url=url)
        wait = self._last.get(host, float("-inf")) + self._interval - self._clock()
        if wait > 0:
            self._sleep(wait)
        self.calls[url] = self.calls.get(url, 0) + 1
        try:
            result = getattr(self._client, method)(url, *args, **kwargs)
        except net.BlockedError as error:
            refusals = self._item_refusals.get(host, 0) + 1
            if (per_item_refusal and not isinstance(error, net.ChallengeError) and error.http_status in (401, 403)
                    and refusals < self.ITEM_REFUSAL_LIMIT):
                self._item_refusals[host] = refusals
            else:
                self._blocked.setdefault(host, f"{error.error_class}, HTTP {error.http_status}")
            raise
        except Exception:
            self._item_refusals.pop(host, None)
            raise
        finally:
            self._last[host] = self._clock()
        self._item_refusals.pop(host, None)
        return result

    def mark_blocked(self, url: str, reason: str) -> None:
        """Record a refusal an adapter detected in a 200 response (risk-control code, captcha page)."""
        host = self._host(url)
        if host:
            self._blocked.setdefault(host, reason)

    def get_json(self, url: str, params: Mapping[str, Any] | None = None, *, per_item_refusal: bool = False,
                 **kwargs: Any) -> Any:
        return self._call("get_json", url, params, per_item_refusal=per_item_refusal, **kwargs)

    def get_text(self, url: str, **kwargs: Any) -> Any:
        return self._call("get_text", url, **kwargs)

    def download(self, url: str, dest_dir: Any, stem: str, **kwargs: Any) -> Any:
        return self._call("download", url, dest_dir, stem, **kwargs)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close:
            close()


@dataclass
class AdapterContext:
    """What an adapter may use: the guarded client and the source seed."""

    client: GuardedClient
    seed: Mapping[str, Any]
    source_id: str
    now: Callable[[], str] = manifest.now_utc

    @property
    def quality(self) -> dict:
        return {"source_variant": "site_original", "origin_status": "unknown", "master_status": "unverified",
                "notes": "", **self.seed.get("quality", {})}

    @property
    def rights(self) -> dict:
        return schema.default_rights(**{key: value for key, value in self.seed.get("rights", {}).items()
                                        if key in schema.RIGHTS_FIELDS})


def default_client_factory(hosts: list[str], min_interval: float) -> Any:
    return net.PoliteClient(hosts, min_interval=min_interval)


class OfflineClient:
    """Client for adapters that must not touch the network: every request raises ``HostNotAllowedError``."""

    def _refuse(self, url: str, *args: Any, **kwargs: Any) -> Any:
        raise net.HostNotAllowedError("this adapter is offline and makes no requests", url=url)

    get_json = get_text = download = _refuse

    def close(self) -> None:
        pass


# --- Seeds ------------------------------------------------------------------------------------------------------------

def load_seeds(path: str | os.PathLike[str], family: str) -> list[dict]:
    """Seed sources of ``family`` from ``path`` (validated: IDs, adapters, rules, unique source slugs)."""
    data = manifest.load_json(path)
    if not isinstance(data, dict) or data.get("family") != family or not isinstance(data.get("sources"), list):
        raise ValueError(f"{path}: seed file must be an object with family {family!r} and a sources list")
    seeds = data["sources"]
    ids = []
    for seed in seeds:
        where = f"{path}: source {seed.get('id')!r}"
        unknown = sorted(set(seed) - SOURCE_SEED_FIELDS)
        if unknown:
            raise ValueError(f"{where}: unknown field {unknown[0]!r}")
        if not schema.SOURCE_ID_RE.fullmatch(seed.get("id", "")) or schema.source_family(seed["id"]) != family:
            raise ValueError(f"{where}: not a {family} source ID")
        if seed.get("adapter") not in ADAPTERS:
            raise ValueError(f"{where}: unknown adapter {seed.get('adapter')!r}")
        for name in ("name", "url", "platform"):
            if not isinstance(seed.get(name), str) or not seed[name].strip():
                raise ValueError(f"{where}: {name} is required")
        if checks.http_url(seed["url"]):
            raise ValueError(f"{where}: url {checks.http_url(seed['url'])}")
        interval = seed.get("min_interval", MIN_INTERVAL)
        if (not isinstance(interval, (int, float)) or isinstance(interval, bool) or not math.isfinite(interval)
                or interval < MIN_INTERVAL):
            raise ValueError(f"{where}: min_interval must be a number >= {MIN_INTERVAL:g} s (frozen network policy)")
        for index, rule in enumerate(seed.get("rules", [])):
            model.check_rule(rule, f"{where}: rules[{index}]")
        ADAPTERS[seed["adapter"]].check_seed(seed, where)
        ids.append(seed["id"])
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: duplicate source IDs")
    schema.check_source_slugs([*schema.REGISTERED_SOURCE_IDS, *ids])
    return sorted(seeds, key=lambda seed: seed["id"])


def adapter_hosts(seed: Mapping[str, Any]) -> list[str]:
    """Explicit allowlist: the adapter's fixed hosts plus the seed's declared extra hosts."""
    hosts = [*ADAPTERS[seed["adapter"]].HOSTS, *seed.get("hosts", [])]
    if not hosts and not getattr(ADAPTERS[seed["adapter"]], "OFFLINE", False):
        raise ValueError(f"source {seed['id']}: adapter declares no hosts")
    return sorted(dict.fromkeys(hosts))


# --- Record builders --------------------------------------------------------------------------------------------------

def append_note(notes: str | None, note: str) -> str:
    """``notes`` with ``note`` appended once, as its own sentence."""
    notes = (notes or "").strip()
    if note in notes:
        return notes
    if notes and not notes.endswith((".", "!", "?", "。", ")")):
        notes += "."
    return f"{notes} {note}".strip()


def stored_url(url: str | None) -> str | None:
    """A URL fit for storage (credential parameters redacted) or None when it is not a valid http(s) URL."""
    if not url or checks.url_problem(url):
        return None
    return checks.redact_url(url, strict=False)


def names_file(draft: model.Draft, url: str | None) -> bool:
    """Whether the stored URL ``url`` (an archived ``download_url``) names the file ``draft`` describes: the same URL,
    or one the adapter's ``same_file`` identifies with it (a re-signed CDN URL, an earlier fallback rendition)."""
    if not url:
        return False
    return url == stored_url(draft.file_url) or bool(draft.same_file and draft.same_file(url))


def archived_draft(draft: model.Draft, asset: Mapping[str, Any]) -> model.Draft:
    """``draft`` naming the archived file of ``asset``: its ``file_url`` becomes the asset's ``download_url`` (the URL the
    archived bytes came from), and a ``source_reported.url`` that named the drafted file URL itself names the archived
    record's advertised URL. A refresh thus never pairs this run's (re-signed, moved or fallback) URL with the
    redirect target and time of an earlier fetch."""
    archived = asset.get("download_url")
    if not archived or archived == stored_url(draft.file_url):
        return draft
    moved = copy.copy(draft)
    reported = draft.source_reported.get("url")
    if reported and stored_url(reported) == stored_url(draft.file_url):
        moved.source_reported = {**draft.source_reported,
                                 "url": (asset.get("source_reported") or {}).get("url") or archived}
    moved.file_url = archived
    return moved


def candidate_record(source_id: str, draft: model.Draft, disposition: str, *, reason: str | None = None,
                     asset_id: str | None = None) -> dict:
    record = {
        "id": schema.candidate_id(source_id, draft.upstream_key), "source_id": source_id,
        "upstream_key": draft.upstream_key, "page_url": stored_url(draft.page_url), "file_url": stored_url(
            draft.file_url), "title": draft.title, "role": draft.role, "game": draft.game,
        "game_evidence": [model.clip(item) for item in dict.fromkeys(draft.game_evidence) if str(item).strip()],
        "source_reported": draft.source_reported, "disposition": disposition,
    }
    if disposition != "downloaded" or reason:
        record["reason"] = reason or draft.reason or disposition
    if asset_id is not None:
        record["asset_id"] = asset_id
    for name in ("song_titles", "illustrators", "composers", "collections"):
        if draft.candidate_extra.get(name):
            record[name] = list(draft.candidate_extra[name])
    if draft.candidate_extra.get("notes"):
        record["notes"] = draft.candidate_extra["notes"]
    schema.validate_candidate(record, source_id)
    return schema.canonical(record, "candidate")


def asset_record(family: str, source_id: str, draft: model.Draft, info: Mapping[str, Any], *, content_type: str | None,
                 resolved_url: str, fetched_at: str, extra_notes: Iterable[str] = ()) -> dict:
    meta = copy.deepcopy(draft.meta)
    observed = schema.make_download_observed(info, content_type)
    quality = meta.pop("quality")
    notes = [quality.get("notes", ""), *extra_notes]
    quality["notes"] = " ".join(note for note in notes if note).strip()
    candidate_notes = draft.candidate_extra.get("notes")  # storefront lists, positions, identifications ...
    if candidate_notes and candidate_notes not in (meta.get("notes") or ""):
        meta["notes"] = " ".join(filter(None, [meta.get("notes"), candidate_notes]))
    record = {
        "id": schema.asset_id(source_id, info["sha256"]), "source_id": source_id,
        "candidate_id": schema.candidate_id(source_id, draft.upstream_key), "title": draft.title, "kind": draft.role,
        "game": schema.GAME, "page_url": stored_url(draft.page_url), "download_url": stored_url(draft.file_url),
        "resolved_url": stored_url(resolved_url) or stored_url(draft.file_url), "path": info["path"],
        "original_filename": draft.original_filename, "bytes": info["bytes"], "sha256": info["sha256"],
        "sha1": info["sha1"], "format": info["format"], "mime": info["mime"], "width": info["width"],
        "height": info["height"], "fetched_at": fetched_at, "illustrators": meta.pop("illustrators"),
        "composers": meta.pop("composers"), "song_titles": meta.pop("song_titles"), "song_ids": meta.pop("song_ids"),
        "collections": meta.pop("collections"), "mapping_status": meta.pop("mapping_status"),
        "mapping_evidence": meta.pop("mapping_evidence"), "quality": quality, "rights": meta.pop("rights"),
        "source_reported": draft.source_reported, "download_observed": observed,
        "comparisons": schema.compute_comparisons(draft.source_reported, observed), "upstream_status": "current",
    }
    record.update(meta)
    schema.validate_asset(record, family)
    return schema.canonical(record, "asset")


def failure_for(error: Any, source_id: str, draft: model.Draft, phase: str, attempted_at: str) -> dict | None:
    """Failure record for ``error`` (None for a request that was skipped and never attempted)."""
    if getattr(error, "skipped", False):
        return None
    candidate = schema.candidate_id(source_id, draft.upstream_key)
    fallback = stored_url(draft.file_url) or stored_url(draft.page_url)
    if isinstance(error, net.FetchError):
        url = error.url if error.url and not checks.url_problem(error.url) else fallback
        return error.failure(source_id=source_id, candidate_id=candidate, phase=phase, attempted_at=attempted_at,
                             url=url)
    if isinstance(error, media.MediaError):
        return error.failure(source_id=source_id, candidate_id=candidate, url=fallback, phase="decode",
                             attempted_at=attempted_at)
    return schema.failure_record(source_id=source_id, candidate_id=candidate, phase=phase, url=fallback,
                                 attempted_at=attempted_at, attempts=1, error_class="other", http_status=None,
                                 retriable=True, manual=False, message=f"{type(error).__name__}: {error}")


def failed_discovery(seed: Mapping[str, Any], error: BaseException) -> model.Discovery:
    """The discovery of a source whose adapter raised unexpectedly: no new candidates, an open ``adapter`` list (so the
    snapshot is never complete) and the error; the runner carries the previous snapshot's candidates over."""
    message = f"adapter error {type(error).__name__}: {error}; previous candidates carried over"
    return model.Discovery(lists=["adapter"], continuation={"adapter": f"error:{type(error).__name__}"},
                           errors=[model.discovery_error("discover", stored_url(seed.get("url")), message)],
                           evidence_level="not_established",
                           scope_notes="Discovery failed with an unexpected error; nothing was enumerated this run.")


# --- Per-source state -------------------------------------------------------------------------------------------------

@dataclass
class SourceState:
    family: str
    seed: Mapping[str, Any]
    started_at: str
    discovery: model.Discovery
    previous: dict | None  # previous discovery snapshot
    assets: dict[str, dict]  # this source's asset records (previous ones kept)
    failures: dict[str, dict] = field(default_factory=dict)
    candidates: dict[str, dict] = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)
    pending_ids: set[str] = field(default_factory=set)
    keep_previous: bool = False  # discovery failed: every previous candidate is carried over unchanged
    # candidate ID -> upstream key of the candidate whose record holds its bytes (seed rule ``archive_under``)
    yields: dict[str, str] = field(default_factory=dict)

    @property
    def source_id(self) -> str:
        return self.seed["id"]


class Runner:
    def __init__(self, root: str | os.PathLike[str], family: str, seeds: list[dict], *,
                 client_factory: Callable[[list[str], float], Any] = default_client_factory,
                 metadata_only: bool = False, resume: bool = False, now: Callable[[], str] = manifest.now_utc,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 interrupt_after: int | None = None):
        if family not in ("artists", "official", "archives"):
            raise ValueError(f"not a primary-source family: {family!r}")
        self.root, self.family, self.seeds = Path(root), family, seeds
        self.client_factory, self.metadata_only, self.resume, self.now = client_factory, metadata_only, resume, now
        self.sleep, self.clock = sleep, clock
        self.blocked_hosts: dict[str, str] = {}
        self.last_used: dict[str, float] = {}
        self.manifest = manifest.load_manifest(self.root, family, missing_ok=True)
        self.clients: list[GuardedClient] = []
        self.downloads = 0
        self.interrupt_after = interrupt_after  # tests: raise KeyboardInterrupt after N downloads
        self.source_errors: dict[str, str] = {}  # source ID -> unexpected error that ended its run early
        self._md5_index: dict[str, str] | None = None  # MD5 -> asset ID of this family's archived files (lazy)
        self._pixels: dict[str, str | None] = {}  # file SHA-256 -> decoded-pixel digest (pixel twins)

    # -- public --

    def run(self, only: Iterable[str] | None = None) -> dict[str, dict[str, int]]:
        """Run every (or every ``only``) seed source. One source's unexpected error never stops the others: it is
        recorded in that source's snapshot (open continuation, discovery error) and listed in ``source_errors``."""
        wanted = set(only or [])
        unknown = wanted - {seed["id"] for seed in self.seeds}
        if unknown:
            raise ValueError(f"unknown source(s): {sorted(unknown)}")
        result = {}
        for seed in self.seeds:
            if wanted and seed["id"] not in wanted:
                continue
            result[seed["id"]] = self.run_source(seed)
        return result

    def run_source(self, seed: Mapping[str, Any]) -> dict[str, int]:
        source_id = seed["id"]
        started = self.now()
        previous = manifest.load_discovery(self.root, source_id)
        offline = getattr(ADAPTERS[seed["adapter"]], "OFFLINE", False)
        inner = OfflineClient() if offline else self.client_factory(adapter_hosts(seed),
                                                                    float(seed.get("min_interval", 1.0)))
        client = GuardedClient(inner,
                               blocked_hosts=self.blocked_hosts, last_used=self.last_used,
                               min_interval=float(seed.get("min_interval", 1.0)), sleep=self.sleep, clock=self.clock)
        self.clients.append(client)
        crashed = False
        try:
            context = AdapterContext(client=client, seed=seed, source_id=source_id, now=self.now)
            LOG.info("%s: discovering", source_id)
            try:
                discovery = ADAPTERS[seed["adapter"]].discover(context, seed)
            except Exception as error:  # an adapter bug or unexpected upstream data must not abort the family run
                LOG.exception("%s: discovery failed", source_id)
                self.source_errors[source_id] = f"discovery: {type(error).__name__}: {error}"
                discovery, crashed = failed_discovery(seed, error), True
            state = SourceState(self.family, seed, started, discovery, previous, {
                asset["id"]: asset for asset in self.manifest["assets"] if asset["source_id"] == source_id},
                keep_previous=crashed)
            state.errors = list(discovery.errors)
            if crashed and not state.assets:
                discovery.status = "temporarily_unavailable"
            try:
                self._resolve(state, client)
            except Exception as error:  # e.g. a record that fails validation: keep the source's last valid state
                LOG.exception("%s: resolution failed", source_id)
                self.source_errors[source_id] = f"resolution: {type(error).__name__}: {error}"
                self._abort_source(state, error)
        finally:
            client.close()
        counts = schema.disposition_counts(state.candidates.values())
        LOG.info("%s: %s", source_id, ", ".join(f"{key} {value}" for key, value in counts.items() if value))
        return counts

    def _abort_source(self, state: SourceState, error: Exception) -> None:
        """Record an unexpected resolution error on the source (open continuation) and persist when still valid."""
        state.errors.append(model.discovery_error("discover", stored_url(state.seed["url"]),
                                                  f"source run aborted: {type(error).__name__}: {error}"))
        state.discovery.continuation["run"] = f"error:{type(error).__name__}"
        try:
            self._persist(state)
        except Exception:  # the in-memory state is not valid: the files keep the last persisted state
            LOG.exception("%s: could not record the aborted run; the previous snapshot stays", state.source_id)

    # -- resolution --

    def _resolve(self, state: SourceState, client: GuardedClient) -> None:
        drafts, duplicates = [], []
        for draft in sorted(state.discovery.candidates, key=lambda draft: draft.upstream_key):
            if drafts and drafts[-1].upstream_key == draft.upstream_key:
                duplicates.append(draft.upstream_key)  # the first draft of a key wins; the adapter is at fault
                continue
            drafts.append(draft)
        if duplicates:
            state.errors.append(model.discovery_error(
                "discover", stored_url(state.seed["url"]), f"adapter produced duplicate candidate keys "
                f"{sorted(set(duplicates))[:5]}; only the first draft of each was recorded"))
            state.discovery.continuation["duplicate-keys"] = f"dropped-{len(duplicates)}"
        keys = [draft.upstream_key for draft in drafts]
        for draft in drafts:  # first pass: everything not needing a download, and placeholders
            cid = schema.candidate_id(state.source_id, draft.upstream_key)
            if draft.action == "download":
                previous_asset = self._previous_current_asset(state, cid)
                if previous_asset is not None:  # until refreshed, the candidate still names its archived file
                    state.candidates[cid] = self._previous_candidate(state, cid)
                else:
                    state.candidates[cid] = candidate_record(
                        state.source_id, draft, "pending", reason="pending: download not attempted yet (run in "
                                                                  "progress or interrupted; rerun with --resume)")
                    state.pending_ids.add(cid)
            else:
                self._record_non_download(state, draft)
        self._carry_over(state, {schema.candidate_id(state.source_id, key) for key in keys})
        self._persist(state)
        state.yields = {schema.candidate_id(state.source_id, draft.upstream_key): draft.archive_under
                        for draft in drafts if draft.action == "download" and draft.archive_under}
        # A candidate that archives its file under another one resolves after it (the other one holds the record).
        for draft in sorted(drafts, key=lambda draft: bool(draft.archive_under)):
            if draft.action == "download":
                try:
                    self._resolve_download(state, client, draft)
                except Exception as error:  # one candidate's unexpected error becomes its own failure record
                    LOG.exception("%s: %s failed unexpectedly", state.source_id, draft.upstream_key)
                    self._download_failed(state, draft, error, "download", self.now(), self._previous_current_asset(
                        state, schema.candidate_id(state.source_id, draft.upstream_key)))
                state.pending_ids.discard(schema.candidate_id(state.source_id, draft.upstream_key))
                self._persist(state)
                self._maybe_interrupt()
        if self.metadata_only:
            return
        drafts_by_id = {schema.candidate_id(state.source_id, draft.upstream_key): draft for draft in drafts
                        if draft.action == "download"}
        changed = self._settle_duplicates(state, drafts_by_id)
        if self._link_pixel_twins(state) or changed:
            self._persist(state)

    def _pixel_digest(self, asset: Mapping[str, Any]) -> str | None:
        """SHA-256 of the decoded first frame (RGBA) of a raster asset; None for a PDF or an unreadable file."""
        if asset["format"] not in schema.RASTER_FORMATS:
            return None
        if asset["sha256"] not in self._pixels:
            self._pixels[asset["sha256"]] = pixel_digest(paths.safe_path(self.root, asset["path"]), asset["format"])
        return self._pixels[asset["sha256"]]

    def _same_pixels(self, asset: Mapping[str, Any], result: Any) -> str | None:
        """SHA-256 of a fresh download (``result``) that decodes to exactly the pixels of the archived ``asset`` (whose
        file verifies), else None. The download is decoded with the same checks as an acquisition (:func:`lib.media.
        inspect`); an undecodable download is left to the normal path, which records the decode failure."""
        if asset["format"] not in schema.RASTER_FORMATS or self._file_problem(asset) is not None:
            return None
        try:
            info = media.inspect(result.temp_path)
        except (media.MediaError, OSError):
            return None
        if info["format"] not in schema.RASTER_FORMATS or (info["width"], info["height"]) != (asset["width"],
                                                                                                asset["height"]):
            return None
        archived = self._pixel_digest(asset)
        return info["sha256"] if archived and pixel_digest(Path(result.temp_path), info["format"]) == archived else None

    def _keep_same_pixels(self, state: SourceState, draft: model.Draft, previous_asset: dict, sha256: str) -> None:
        """A re-fetch of the candidate's URL returned other bytes with identical pixels (an App Store re-render writes a
        new embedded identifier): the archived record stays current (no second copy, no supersession) and notes the
        re-fetched SHA-256 (only the latest such note is kept)."""
        sid = state.source_id
        cid = schema.candidate_id(sid, draft.upstream_key)
        refreshed = self._refresh_record(state.assets.get(previous_asset["id"], previous_asset), draft)
        notes = REFETCH_NOTE_RE.sub("", refreshed.get("notes") or "")
        refreshed["notes"] = append_note(" ".join(notes.split()), REFETCH_NOTE.format(date=self.now()[:10],
                                                                                         sha256=sha256))
        schema.validate_asset(refreshed, self.family)
        state.assets[refreshed["id"]] = schema.canonical(refreshed, "asset")
        state.candidates[cid] = candidate_record(sid, archived_draft(draft, previous_asset), "downloaded",
                                                 asset_id=refreshed["id"])
        for phase in ("download", "decode", "verify"):
            state.failures.pop(schema.failure_id(cid, phase), None)
        LOG.info("%s: re-fetched bytes %s decode to the archived pixels; %s kept", cid, sha256[:12],
                 refreshed["id"])

    def _settle_duplicates(self, state: SourceState, drafts: Mapping[str, model.Draft]) -> bool:
        """No file that upstream currently lists may be represented only by a removed, superseded or unknown record.

        A candidate of this run that is ``duplicate_bytes`` of such a record of this source (the file moved to a new
        key, e.g. a renamed page link, or a candidate now serves another candidate's earlier revision) either restores
        the record (still named by its own candidate: ``current`` again, noting the other listing) or adopts it: the
        record becomes the candidate's current asset (same asset ID and file, ``upstream_status: current``) with a note
        naming the candidate it was first archived for, and the candidate is ``downloaded``. Returns whether a record
        changed."""
        changed = False
        for cid in sorted(state.candidates):
            candidate, draft = state.candidates[cid], drafts.get(cid)
            owner = state.assets.get(candidate.get("asset_id") or "")
            if (candidate["disposition"] != "duplicate_bytes" or draft is None or owner is None
                    or owner.get("upstream_status") in (None, "current")):
                continue
            holder = state.candidates.get(owner["candidate_id"])
            if holder is not None and holder["disposition"] == "downloaded" and holder.get("asset_id") == owner["id"]:
                self._mark_listed(state, owner)
                restored = dict(state.assets[owner["id"]])
                restored["notes"] = append_note(restored.get("notes"), ALSO_LISTED_NOTE.format(cid=cid))
                schema.validate_asset(restored, self.family)
                state.assets[owner["id"]] = schema.canonical(restored, "asset")
                changed = True
                continue
            pointing = [asset for asset in state.assets.values()
                        if asset.get("superseded_by") == owner["id"] and asset["candidate_id"] != cid]
            if pointing and not owner.get("superseded_by"):
                state.errors.append(model.discovery_error(
                    "discover", stored_url(draft.page_url), f"{cid} lists the bytes of {owner['id']} "
                    f"({owner.get('upstream_status')}), which ends the supersession chain of "
                    + ", ".join(asset["id"] for asset in pointing) + "; kept as duplicate_bytes for review"))
                continue
            for asset in pointing:  # the adopted record leaves its former candidate's revision chain
                relinked = dict(asset)
                relinked["superseded_by"] = owner["superseded_by"]
                state.assets[asset["id"]] = schema.canonical(relinked, "asset")
            adopted = self._refresh_record(owner, draft)
            adopted["notes"] = append_note(adopted.get("notes"), ADOPTED_NOTE.format(
                old=owner["candidate_id"], status=owner.get("upstream_status"), date=self.now()[:10]))
            schema.validate_asset(adopted, self.family)
            state.assets[owner["id"]] = schema.canonical(adopted, "asset")
            state.candidates[cid] = candidate_record(state.source_id, draft, "downloaded", asset_id=owner["id"])
            LOG.warning("%s: adopted %s (first archived for %s, %s)", cid, owner["id"], owner["candidate_id"],
                        owner.get("upstream_status"))
            changed = True
        return changed

    def _link_pixel_twins(self, state: SourceState) -> bool:
        """Link current assets of one source whose files differ but decode to identical pixels (e.g. App Store
        renditions of two uploads of one image, each carrying its own embedded identifier): each names its twins in
        ``notes`` and the group shares a ``variant_group`` (the members' one existing group, else ``pixels:<digest>``),
        so coverage does not count them as distinct artworks. Returns whether a record changed."""
        groups: dict[str, list[dict]] = {}
        for asset in sorted(state.assets.values(), key=lambda a: a["id"]):
            if asset.get("upstream_status") in (None, "current", "unknown"):
                digest = self._pixel_digest(asset)
                if digest:
                    groups.setdefault(digest, []).append(asset)
        changed = False
        for digest, members in sorted(groups.items()):
            if len({member["sha256"] for member in members}) < 2:
                continue
            existing = sorted({member["variant_group"] for member in members if member.get("variant_group")})
            group = existing[0] if len(existing) == 1 else None if existing else f"pixels:{digest[:16]}"
            for member in members:
                others = ", ".join(other["id"] for other in members if other["id"] != member["id"])
                note = (f"Decodes to pixels identical to {others} (different file bytes, e.g. embedded metadata); "
                        "not a distinct artwork.")
                updated = dict(member)
                updated["notes"] = append_note(updated.get("notes"), note)
                if group and not updated.get("variant_group"):
                    updated["variant_group"] = group
                if updated != member:
                    schema.validate_asset(updated, self.family)
                    state.assets[member["id"]] = schema.canonical(updated, "asset")
                    changed = True
        return changed

    def _record_non_download(self, state: SourceState, draft: model.Draft) -> None:
        sid = state.source_id
        cid = schema.candidate_id(sid, draft.upstream_key)
        previous_asset = self._previous_current_asset(state, cid)
        if previous_asset is not None and draft.withdraw_archived and not self.metadata_only:
            draft = self._withdraw(state, draft)
            previous_asset = None
        elif draft.withdraw_archived:  # keep the record of an earlier withdrawal on the candidate
            earlier = WITHDRAWN_RE.search((self._previous_candidate(state, cid) or {}).get("notes") or "")
            if earlier and earlier.group(0) not in (draft.candidate_extra.get("notes") or ""):
                draft = copy.copy(draft)
                draft.candidate_extra = {**draft.candidate_extra, "notes": " ".join(
                    filter(None, [draft.candidate_extra.get("notes"), earlier.group(0)]))}
        if previous_asset is not None:
            # Archived bytes are never dropped by a refresh: a transient error, a missing evidence quote or a changed
            # classification keeps the archived record and is reported for review instead (``--verify`` fails on an
            # archived candidate that is now excluded; a curated rule resolves it with ``withdraw_archived``).
            state.candidates[cid] = self._previous_candidate(state, cid) or candidate_record(
                sid, draft, "downloaded", asset_id=previous_asset["id"])
            if not self.metadata_only and draft.error is None:  # observed upstream again (not a failed read)
                self._mark_listed(state, previous_asset)
            state.errors.append(model.discovery_error(draft.error_phase if draft.error is not None else "discover",
                                                      stored_url(draft.page_url),
                                                      f"{cid} is now {draft.action} ({draft.reason}); the archived "
                                                      f"record {previous_asset['id']} {KEPT_FOR_REVIEW}"))
            return
        state.candidates[cid] = candidate_record(sid, draft, draft.action)
        if draft.action in ("blocked", "failed") and draft.error is not None:
            failure = failure_for(draft.error, sid, draft, draft.error_phase, self.now())
            if failure is not None:
                state.failures[failure["id"]] = failure

    def _resolve_download(self, state: SourceState, client: GuardedClient, draft: model.Draft) -> None:
        sid, family = state.source_id, self.family
        cid = schema.candidate_id(sid, draft.upstream_key)
        previous_asset = self._previous_current_asset(state, cid)
        if self.metadata_only:
            if previous_asset is not None:
                state.candidates[cid] = self._previous_candidate(state, cid) or candidate_record(
                    sid, draft, "downloaded", asset_id=previous_asset["id"])
            else:
                state.candidates[cid] = candidate_record(sid, draft, "pending",
                                                         reason="pending: not downloaded (--metadata-only run)")
            return
        if self.resume and previous_asset is not None and self._reusable(previous_asset, draft):
            refreshed = self._refresh_record(previous_asset, draft)
            state.assets[refreshed["id"]] = refreshed
            state.candidates[cid] = candidate_record(sid, archived_draft(draft, previous_asset), "downloaded",
                                                     asset_id=refreshed["id"])
            return
        dest = paths.safe_path(self.root, f"{paths.ASSET_ROOT}/{family}/{paths.source_slug(sid)}",
                               under=paths.ASSET_ROOT)
        attempted_at = self.now()
        urls = [draft.file_url, *draft.fallback_urls]
        for position, url in enumerate(urls):
            try:
                result = client.download(url, dest, paths.filename_stem(draft.original_filename))
                if url != draft.file_url:
                    draft = self._fallback_draft(draft, url)
                break
            except net.FetchError as error:
                if position + 1 < len(urls) and isinstance(error, net.HttpError) and error.http_status == 404:
                    LOG.info("%s: %s not found, trying the next rendition", cid, url)
                    continue
                self._download_failed(state, draft, error, "download", attempted_at, previous_asset)
                return
        self.downloads += 1
        held = draft.check_download(result) if draft.check_download else None
        if held:  # not the file the draft describes: nothing is archived from this response
            Path(result.temp_path).unlink(missing_ok=True)
            self._hold_download(state, draft, held, previous_asset)
            return
        new_id = schema.asset_id(sid, result.sha256)
        owner = state.assets.get(new_id)
        if (owner is not None and owner["candidate_id"] != cid
                and state.yields.get(owner["candidate_id"]) == draft.upstream_key):
            self._take_over(state, draft, owner, result, attempted_at, previous_asset)
            return
        if owner is None and previous_asset is not None and names_file(draft, previous_asset.get("download_url")):
            same = self._same_pixels(state.assets.get(previous_asset["id"], previous_asset), result)
            if same:  # other bytes, same picture: not a new upstream revision
                Path(result.temp_path).unlink(missing_ok=True)
                self._keep_same_pixels(state, draft, previous_asset, same)
                return
        repaired = False
        if owner is not None:
            try:
                repaired = self._repair(state, owner, result)
            except (media.MediaError, OSError) as error:
                Path(result.temp_path).unlink(missing_ok=True)
                self._download_failed(state, draft, error, "verify", attempted_at, previous_asset)
                return
        if owner is not None and owner["candidate_id"] != cid:  # same source already archived these bytes
            Path(result.temp_path).unlink(missing_ok=True)
            state.candidates[cid] = candidate_record(
                sid, draft, "duplicate_bytes", asset_id=new_id,
                reason=f"duplicate_bytes: identical bytes already archived for {owner['candidate_id']}")
            self._supersede(state, previous_asset, new_id)
            return
        if not repaired:
            try:
                # Same candidate, same bytes as an archived record: the file stays at that record's path even when
                # the upstream name changed since (no second copy, no unreferenced file).
                info = media.finalize_download(result, self.root, lambda measured: owner["path"] if owner is not None
                                               else paths.asset_relpath(family, paths.source_slug(sid),
                                                                        draft.original_filename, measured["sha256"],
                                                                        measured["ext"]))
            except media.MediaError as error:
                self._download_failed(state, draft, error, "decode", attempted_at, previous_asset)
                return
        if owner is not None:  # keep the first acquisition's measurements (a reverted upstream file is current again)
            refreshed = self._refresh_record(owner, draft)
            if stored_url(draft.file_url) != owner.get("download_url"):  # the same bytes from a re-signed/moved URL
                via = (" (same file, re-signed URL)" if names_file(draft, owner.get("download_url"))
                       else f" from {stored_url(draft.file_url)}")
                notes = " ".join(REREAD_NOTE_RE.sub("", refreshed.get("notes") or "").split())
                refreshed["notes"] = append_note(notes, REREAD_NOTE.format(date=self.now()[:10], via=via))
                schema.validate_asset(refreshed, self.family)
                refreshed = schema.canonical(refreshed, "asset")
            state.assets[new_id] = refreshed
            draft = archived_draft(draft, owner)
        else:
            draft, notes = self._md5_evidence(draft, paths.safe_path(self.root, info["path"]))
            state.assets[new_id] = asset_record(family, sid, draft, info, content_type=result.content_type,
                                                resolved_url=result.final_url, fetched_at=attempted_at,
                                                extra_notes=notes)
        self._supersede(state, previous_asset, new_id)
        state.candidates[cid] = candidate_record(sid, draft, "downloaded", asset_id=new_id)
        state.failures.pop(schema.failure_id(cid, "download"), None)
        state.failures.pop(schema.failure_id(cid, "decode"), None)
        state.failures.pop(schema.failure_id(cid, "verify"), None)

    @staticmethod
    def _fallback_draft(draft: model.Draft, url: str) -> model.Draft:
        """``draft`` naming the fallback ``url`` that was archived (its ``file_url`` answered HTTP 404); the adapter's
        ``fallback_note`` is added to the record's and the candidate's notes."""
        used = copy.copy(draft)
        used.file_url = url
        if draft.fallback_note:
            note = draft.fallback_note.format(url=stored_url(url), file_url=stored_url(draft.file_url))
            used.candidate_extra = {**draft.candidate_extra, "notes": append_note(draft.candidate_extra.get("notes"),
                                                                                  note)}
            if used.action == "download":
                used.meta = {**draft.meta, "notes": append_note(draft.meta.get("notes"), note)}
        return used

    def _take_over(self, state: SourceState, draft: model.Draft, owner: Mapping[str, Any], result: Any,
                   attempted_at: str, previous_asset: dict | None) -> None:
        """This candidate's bytes are archived under another candidate whose seed rule says ``archive_under`` this one:
        the record (same asset ID) is rewritten from this candidate's own fetch at its own contract path, the earlier
        file is removed when its path differs, and the other candidate becomes ``duplicate_bytes`` when it resolves."""
        sid, family = state.source_id, self.family
        cid = schema.candidate_id(sid, draft.upstream_key)
        try:
            info = media.finalize_download(result, self.root, lambda measured: paths.asset_relpath(
                family, paths.source_slug(sid), draft.original_filename, measured["sha256"], measured["ext"]))
        except media.MediaError as error:
            self._download_failed(state, draft, error, "decode", attempted_at, previous_asset)
            return
        if info["path"] != owner["path"]:
            paths.safe_path(self.root, owner["path"], under=f"{paths.ASSET_ROOT}/{family}").unlink(missing_ok=True)
        draft, notes = self._md5_evidence(draft, paths.safe_path(self.root, info["path"]))
        record = asset_record(family, sid, draft, info, content_type=result.content_type,
                              resolved_url=result.final_url, fetched_at=attempted_at,
                              extra_notes=notes)
        record["notes"] = append_note(record.get("notes"), TAKEN_OVER_NOTE.format(other=owner["candidate_id"]))
        schema.validate_asset(record, family)
        state.assets[record["id"]] = schema.canonical(record, "asset")
        state.candidates[cid] = candidate_record(sid, draft, "downloaded", asset_id=record["id"])
        other = state.candidates.get(owner["candidate_id"])
        if other is not None and other.get("asset_id") == record["id"]:  # until it resolves itself
            yielded = {**other, "disposition": "duplicate_bytes",
                       "reason": f"duplicate_bytes: identical bytes already archived for {cid}"}
            schema.validate_candidate(yielded, sid)
            state.candidates[other["id"]] = schema.canonical(yielded, "candidate")
        LOG.warning("%s: took over %s from %s (archive_under)", cid, record["id"], owner["candidate_id"])

    def _supersede(self, state: SourceState, previous_asset: dict | None, new_id: str) -> None:
        """Mark the candidate's previous current asset as superseded by ``new_id`` (new bytes, bytes that revert to an
        earlier archived record, or bytes another candidate already archived). Its file is kept; a damaged file is
        reported, since an earlier upstream version cannot be fetched again."""
        if previous_asset is None or previous_asset["id"] == new_id:
            return
        superseded = dict(state.assets.get(previous_asset["id"], previous_asset))
        superseded["upstream_status"] = "superseded"
        superseded["superseded_by"] = new_id
        state.assets[superseded["id"]] = schema.canonical(superseded, "asset")
        problem = self._file_problem(superseded)
        if problem:
            state.errors.append(model.discovery_error(
                "verify", stored_url(superseded["download_url"]), f"the archived file {superseded['path']} of the "
                f"superseded record {superseded['id']} does not verify ({problem}); upstream now serves other bytes "
                f"({new_id}), so it cannot be repaired from upstream"))

    def _file_problem(self, asset: Mapping[str, Any]) -> str | None:
        try:
            media.verify_asset_file(self.root, asset)
        except (media.MediaError, OSError) as error:
            return str(error)
        return None

    def _repair(self, state: SourceState, asset: Mapping[str, Any], result: Any) -> bool:
        """Replace the damaged file of ``asset`` with freshly downloaded bytes whose SHA-256 equals the record's (one
        atomic ``os.replace`` in the same directory). False, with nothing changed, when the archived file verifies."""
        problem = self._file_problem(asset)
        if problem is None:
            return False
        temp = Path(result.temp_path)
        info = media.inspect(temp)
        if (info["sha256"], info["bytes"]) != (asset["sha256"], asset["bytes"]) or not asset["path"].endswith(
                f".{info['ext']}"):
            raise media.MediaError(f"{asset['id']}: the archived file does not verify ({problem}) and the new download "
                                   f"(sha256 {info['sha256']}) does not match the record")
        target = paths.safe_path(self.root, asset["path"], under=f"{paths.ASSET_ROOT}/{self.family}")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(temp, 0o644)
        os.replace(temp, target)
        media.verify_asset_file(self.root, asset)
        LOG.warning("%s: repaired the archived file %s (%s)", asset["id"], asset["path"], problem)
        state.errors.append(model.discovery_error(
            "verify", stored_url(asset["download_url"]), f"the archived file {asset['path']} of {asset['id']} did not "
            f"verify ({problem}); replaced by a fresh download with the recorded SHA-256"))
        self._md5_index = None
        return True

    def _download_failed(self, state: SourceState, draft: model.Draft, error: Any, phase: str, attempted_at: str,
                         previous_asset: dict | None) -> None:
        sid = state.source_id
        cid = schema.candidate_id(sid, draft.upstream_key)
        failed = model.error_draft(draft.upstream_key, draft.page_url, draft.title, error, phase=phase,
                                   file_url=draft.file_url, role=draft.role, game=draft.game,
                                   game_evidence=draft.game_evidence, source_reported=draft.source_reported)
        failed.candidate_extra = draft.candidate_extra
        if previous_asset is not None:
            # Archived bytes are never dropped: the previous candidate record stays exactly as it was (a changed seed
            # role or title applies with the next successful refresh) and the failure is reported on the snapshot. A
            # damaged archived file is named, so --verify's file error has its cause next to it.
            state.candidates[cid] = self._previous_candidate(state, cid) or candidate_record(
                sid, draft, "downloaded", asset_id=previous_asset["id"])
            self._mark_listed(state, state.assets.get(previous_asset["id"], previous_asset))
            message = f"refresh of {cid} failed, previous archive kept: {failed.reason}"
            problem = self._file_problem(previous_asset)
            if problem:
                message += (f"; the archived file {previous_asset['path']} does not verify ({problem}) and could not be "
                            "repaired")
            state.errors.append(model.discovery_error("verify" if problem else phase, stored_url(draft.file_url),
                                                      model.clip(message, 600)))
            return
        state.candidates[cid] = candidate_record(sid, failed, failed.action)
        failure = failure_for(error, sid, failed, phase, attempted_at)
        if failure is not None:
            state.failures[failure["id"]] = failure
        LOG.warning("%s: %s", cid, failed.reason)

    def _hold_download(self, state: SourceState, draft: model.Draft, reason: str, previous_asset: dict | None) -> None:
        """A completed download that the adapter's ``check_download`` rejects (``reason``): nothing is archived. A
        candidate with an archived file keeps it (reported for review); otherwise the candidate is ``pending``."""
        sid = state.source_id
        cid = schema.candidate_id(sid, draft.upstream_key)
        LOG.warning("%s: download held: %s", cid, reason)
        if previous_asset is not None:
            state.candidates[cid] = self._previous_candidate(state, cid) or candidate_record(
                sid, draft, "downloaded", asset_id=previous_asset["id"])
            state.errors.append(model.discovery_error("download", stored_url(draft.file_url), model.clip(
                f"refresh of {cid} held, previous archive kept: {reason}", 600)))
            return
        held = copy.copy(draft)
        held.action, held.reason = "pending", model.clip(reason, 400)
        state.candidates[cid] = candidate_record(sid, held, "pending")
        state.errors.append(model.discovery_error("download", stored_url(draft.file_url), f"{cid}: {reason}"))

    def _maybe_interrupt(self) -> None:
        if self.interrupt_after is not None and self.downloads >= self.interrupt_after:
            raise KeyboardInterrupt(f"test interrupt after {self.downloads} downloads")

    # -- previous state helpers --

    def _previous_candidate(self, state: SourceState, cid: str) -> dict | None:
        if not state.previous:
            return None
        return next((c for c in state.previous["candidates"] if c["id"] == cid), None)

    def _previous_current_asset(self, state: SourceState, cid: str) -> dict | None:
        """The archived asset the previous snapshot names for ``cid`` (downloaded), if it still exists here."""
        candidate = self._previous_candidate(state, cid)
        if candidate and candidate["disposition"] == "downloaded":
            return state.assets.get(candidate.get("asset_id"))
        return None

    def _reusable(self, asset: Mapping[str, Any], draft: model.Draft) -> bool:
        if not names_file(draft, asset.get("download_url")):
            return False
        try:
            media.verify_asset_file(self.root, asset)
        except media.MediaError as error:
            LOG.warning("%s: archived file does not verify, downloading again (%s)", asset["id"], error)
            return False
        return True

    def _refresh_record(self, asset: Mapping[str, Any], draft: model.Draft) -> dict:
        """Descriptive fields from the current seed/draft on top of the archived measurements. The archived file name
        (hence the contract path) is kept; a changed upstream name is noted instead. ``download_url``,
        ``resolved_url`` and ``fetched_at`` stay those of the acquisition that archived the bytes (see
        :func:`archived_draft`), so the three always describe one and the same fetch."""
        info = {name: asset[name] for name in ("path", "bytes", "sha256", "sha1", "format", "mime", "width",
                                               "height")}
        renamed = []
        if draft.original_filename and draft.original_filename != asset["original_filename"]:
            renamed.append(f"The upstream file name is now {draft.original_filename}; the archived copy keeps "
                           f"{asset['original_filename']}.")
        draft = copy.copy(archived_draft(draft, asset))
        draft.original_filename = asset["original_filename"]
        draft, notes = self._md5_evidence(draft, paths.safe_path(self.root, asset["path"]))
        record = asset_record(self.family, asset["source_id"], draft, info,
                              content_type=asset["download_observed"]["content_type"],
                              resolved_url=asset["resolved_url"], fetched_at=asset["fetched_at"],
                              extra_notes=[*notes, *renamed])
        carried = [match.group(0)
                   for pattern in (ADOPTED_NOTE_RE, REFETCH_NOTE_RE, REREAD_NOTE_RE, TAKEN_OVER_NOTE_RE)
                   for match in pattern.finditer(asset.get("notes") or "")]
        if not carried:
            return record
        for note in carried:  # provenance of these bytes, not seed description: kept across refreshes
            record["notes"] = append_note(record.get("notes"), note)
        schema.validate_asset(record, self.family)
        return schema.canonical(record, "asset")

    def _md5_evidence(self, draft: model.Draft, file: Path) -> tuple[model.Draft, list[str]]:
        """Compare the file's MD5 with the upstream name token (TapTap names uploads by their MD5). A mismatch means
        the platform re-encoded the upload: the record is a known transform (``transformed: true``, not called the
        site's original) and names the archived byte-exact upload when this family holds it."""
        token = (draft.md5_token or "").lower()
        if not token or not draft.meta:
            return draft, []
        digest = hashlib.md5(file.read_bytes(), usedforsecurity=False).hexdigest()
        if digest == token:
            return draft, [f"File MD5 equals the upstream name token {token}, consistent with the uploaded original."]
        adjusted = copy.copy(draft)
        adjusted.meta = copy.deepcopy(draft.meta)
        adjusted.meta["transformed"] = True
        adjusted.meta["quality"]["source_variant"] = "unknown"
        notes = [f"File MD5 {digest} differs from the upstream name token {token}: the platform re-encoded the upload "
                 "(a transform, not the uploaded original)."]
        twin = self._md5_twin(token)
        if twin:
            notes.append(f"The byte-exact upload (MD5 {token}) is archived as {twin}.")
        return adjusted, notes

    def _md5_twin(self, digest: str) -> str | None:
        """ID of a current archived asset of this family whose file has MD5 ``digest`` (index built on first use)."""
        if self._md5_index is None:
            self._md5_index = {}
            for asset in sorted(self.manifest["assets"], key=lambda a: a.get("upstream_status") != "current"):
                try:
                    data = paths.safe_path(self.root, asset["path"]).read_bytes()
                except (OSError, ValueError):
                    continue
                self._md5_index.setdefault(hashlib.md5(data, usedforsecurity=False).hexdigest(), asset["id"])
        return self._md5_index.get(digest)

    def _withdraw(self, state: SourceState, draft: model.Draft) -> model.Draft:
        """Apply a curated ``withdraw_archived`` rule: remove the candidate's archived records and files (the bytes
        stay in the Git history) and say so in the candidate notes."""
        cid = schema.candidate_id(state.source_id, draft.upstream_key)
        removed = sorted((asset for asset in state.assets.values() if asset["candidate_id"] == cid),
                         key=lambda asset: asset["id"])
        for asset in removed:
            del state.assets[asset["id"]]
            paths.safe_path(self.root, asset["path"], under=f"{paths.ASSET_ROOT}/{self.family}").unlink(
                missing_ok=True)
            LOG.warning("%s: withdrew archived record %s (%s) per curated rule", cid, asset["id"], asset["path"])
        note = (f"Withdrawn {self.now()[:10]} by a curated seed rule ({draft.reason}): archived record(s) "
                + ", ".join(f"{asset['id']} (sha256 {asset['sha256']})" for asset in removed)
                + " removed from the manifest and asset tree; the bytes remain in the Git history.")
        withdrawn = copy.copy(draft)
        withdrawn.candidate_extra = {**draft.candidate_extra, "notes": " ".join(
            filter(None, [draft.candidate_extra.get("notes"), note]))}
        return withdrawn

    def _carry_over(self, state: SourceState, current_ids: set[str]) -> None:
        """Account for previous candidates that this run did not rediscover (all of them are kept when this run's
        discovery failed).

        A candidate with archived bytes that a **complete** enumeration covering it no longer lists (``Discovery.
        coverage``: the list names whose full read would list it again) is gone upstream: its current asset becomes
        ``upstream_status: removed`` (bytes kept, note added) and the candidate leaves the snapshot. Otherwise the
        candidate is carried over; when this run read every list completely and still did not reach it (the seeds no
        longer name it), its current asset becomes ``unknown``. A failed or partial read is no observation and changes
        no status; neither does ``--metadata-only``."""
        if not state.previous:
            return
        asset_candidates = {asset["candidate_id"] for asset in state.assets.values()}
        note = ("Carried over from an earlier run: this run's discovery failed." if state.keep_previous else
                "Carried over from an earlier run: not rediscovered by this run's seeds/enumeration.")
        for candidate in state.previous["candidates"]:
            if candidate["id"] in current_ids:
                continue
            if (not state.keep_previous and candidate.get("asset_id") not in state.assets
                    and candidate["id"] not in asset_candidates):
                continue
            if not state.keep_previous and not self.metadata_only:
                covering = sorted(name for name, covers in state.discovery.coverage.items() if covers(candidate))
                if covering and all(state.discovery.continuation.get(name) is None for name in covering):
                    self._mark_removed(state, candidate["id"], covering)
                    continue
                if state.discovery.complete:  # every list was read and none reaches it: the seeds changed
                    self._mark_upstream(state, candidate["id"], "unknown", UNKNOWN_NOTE)
            kept = dict(candidate)
            if note not in kept.get("notes", ""):
                kept["notes"] = " ".join(filter(None, [kept.get("notes"), note]))
            state.candidates[kept["id"]] = kept
            for failure in self.manifest["failures"]:
                if failure["candidate_id"] == kept["id"]:
                    state.failures[failure["id"]] = failure

    def _mark_upstream(self, state: SourceState, cid: str, status: str, note: str) -> None:
        """Set ``upstream_status`` of the candidate's current (or unknown) archived assets and add ``note`` once."""
        for asset in list(state.assets.values()):
            if asset["candidate_id"] != cid or asset.get("upstream_status") not in (None, "current", "unknown"):
                continue
            updated = dict(asset)
            updated["upstream_status"] = status
            updated["notes"] = append_note(updated.get("notes"), note)
            schema.validate_asset(updated, self.family)
            state.assets[asset["id"]] = schema.canonical(updated, "asset")

    def _mark_removed(self, state: SourceState, cid: str, lists: list[str]) -> None:
        self._mark_upstream(state, cid, "removed", f"Not listed by the complete {', '.join(lists)} enumeration of "
                                                   f"{state.started_at[:10]}: removed upstream; the archived bytes "
                                                   "are kept.")
        LOG.warning("%s: no longer listed by %s; archived record kept as removed", cid, ", ".join(lists))

    def _mark_listed(self, state: SourceState, asset: Mapping[str, Any]) -> None:
        """A rediscovered candidate's archived asset whose status was ``unknown`` is listed upstream again."""
        if asset.get("upstream_status") != "unknown":
            return
        updated = dict(asset)
        updated["upstream_status"] = "current"
        notes = " ".join((updated.pop("notes", None) or "").replace(UNKNOWN_NOTE, "").split())
        if notes:
            updated["notes"] = notes
        state.assets[asset["id"]] = schema.canonical(updated, "asset")

    # -- output --

    def _source_record(self, state: SourceState, counts: Mapping[str, int]) -> dict:
        seed, discovery = state.seed, state.discovery
        status = discovery.status or compute_status(counts, discovery.complete, bool(state.assets),
                                                    list(state.failures.values()))
        notes = " ".join(filter(None, [seed.get("notes", ""), *discovery.notes]))
        record = {"id": seed["id"], "name": seed["name"], "family": self.family, "url": seed["url"],
                  "platform": seed["platform"], "status": status, "checked_at": state.started_at, "notes": notes}
        if seed.get("api_url") or discovery.api:
            record["api_url"] = stored_url(seed.get("api_url") or discovery.api)
        for name in ("owner_evidence", "crawl_scope"):
            if name in seed:
                record[name] = seed[name]
        interval = float(seed.get("min_interval", 1.0))
        record["rate_policy"] = (f"one request at a time per host, >= {interval:g} s spacing, Retry-After <= 60 s, "
                                 "<= 3 attempts for 429/5xx/timeouts, no retry on 401/403/challenge")
        record.update(discovery_complete=discovery.complete and not state.pending_ids,
                      candidate_count=len(state.candidates), asset_count=len(state.assets),
                      failure_count=len(state.failures), evidence_level=discovery.evidence_level)
        schema.validate_source(record, self.family)
        return schema.canonical(record, "source")

    def _persist(self, state: SourceState) -> None:
        sid = state.source_id
        counts = schema.disposition_counts(state.candidates.values())
        continuation = dict(state.discovery.continuation)
        if state.pending_ids:
            continuation["downloads"] = "in-progress"
        snapshot = {
            "schema_version": schema.SCHEMA_VERSION, "source_id": sid, "started_at": state.started_at,
            "finished_at": max(self.now(), state.started_at),
            "scope": {"lists": list(state.discovery.lists), "api": stored_url(state.discovery.api),
                      "notes": state.discovery.scope_notes or "explicit seeds only; no whole-platform crawl"},
            "continuation": continuation, "discovery_complete": state.discovery.complete and not state.pending_ids,
            "candidates": list(state.candidates.values()), "errors": state.errors,
        }
        snapshot = manifest.sort_discovery(snapshot)
        schema.validate_discovery(snapshot, sid)
        source = self._source_record(state, counts)
        current = self.manifest
        sources = [s for s in current["sources"] if s["id"] != sid] + [source]
        assets = [a for a in current["assets"] if a["source_id"] != sid] + list(state.assets.values())
        failures = [f for f in current["failures"] if f["source_id"] != sid] + list(state.failures.values())
        fetched = max((a["fetched_at"] for a in assets), default=None)
        updated = manifest.sort_manifest({"schema_version": schema.SCHEMA_VERSION, "family": self.family,
                                          "fetched_at": fetched, "sources": sources, "assets": assets,
                                          "failures": failures})
        schema.validate_manifest(updated, self.family)
        schema.reconcile(updated, {sid: snapshot})
        manifest.write_json_atomic(paths.safe_path(self.root, paths.discovery_relpath(sid)), snapshot)
        manifest.write_json_atomic(paths.safe_path(self.root, paths.manifest_relpath(self.family)), updated)
        self.manifest = updated


def pixel_digest(path: Path, fmt: str) -> str | None:
    """SHA-256 of the decoded first frame of a raster file as RGBA (with its size), None when it cannot be decoded."""
    try:
        with Image.open(path, formats=["JPEG", "MPO"] if fmt == "JPEG" else [fmt]) as image:
            image.seek(0)
            frame = image.convert("RGBA")
            return hashlib.sha256(f"{frame.width}x{frame.height}:".encode() + frame.tobytes()).hexdigest()
    except (OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError):
        return None


def compute_status(counts: Mapping[str, int], complete: bool, has_assets: bool, failures: list[dict]) -> str:
    """Source status from the candidate outcome (adapters may override it with a more specific one)."""
    accepted = counts["downloaded"] + counts["duplicate_bytes"]
    unresolved = counts["blocked"] + counts["failed"] + counts["pending"]
    if accepted or has_assets:
        return "partially_available" if unresolved or not complete else "available"
    if counts["blocked"]:
        return "manual_access"
    if counts["failed"]:
        permanent = all(f["error_class"] == "http_4xx" and f["http_status"] not in (408, 429) for f in failures)
        return "metadata_only" if failures and permanent else "temporarily_unavailable"
    if counts["pending"]:
        return "metadata_only"
    if counts["excluded"]:
        return "excluded"
    return "metadata_only"


# --- Offline verification ---------------------------------------------------------------------------------------------

def artist_candidates(root: Path) -> list[dict]:
    """Every candidate of the ``artists`` discovery snapshots under ``root`` (unreadable files are skipped)."""
    discovery_dir = root / paths.DISCOVERY_ROOT
    candidates: list[dict] = []
    for path in sorted(discovery_dir.glob("*.json")) if discovery_dir.is_dir() else []:
        try:
            data = manifest.load_json(path)
        except (ValueError, OSError):
            continue
        if isinstance(data, dict) and str(data.get("source_id", "")).startswith("artists:"):
            candidates.extend(c for c in data.get("candidates", []) if isinstance(c, dict))
    return candidates


WAYBACK_RAW_RE = re.compile(r"^https://web\.archive\.org/web/\d{14}id_/(https?://\S+)$")


def _same_file(candidate: Mapping[str, Any], original: str | None) -> bool:
    """Whether an artists candidate stands for the primary file a copy reports as its original URL: the candidate's
    file URL equals it or is an Internet Archive raw capture of it, or (pixiv withholds file URLs from logged-out
    readers) the candidate key ``<work>:p<n>`` names the original's ``<work>_p<n>`` file. Without an original URL
    nothing contradicts the candidate."""
    if not original:
        return True
    if candidate.get("file_url"):
        archived = WAYBACK_RAW_RE.match(candidate["file_url"])
        return candidate["file_url"] == original or bool(archived and archived.group(1) == original)
    stem = original.rsplit("/", 1)[-1].split("?", 1)[0].rsplit(".", 1)[0]
    return str(candidate.get("upstream_key", "")).replace(":p", "_p", 1) == stem


ADMITTING_DISPOSITIONS = ("downloaded", "duplicate_bytes", "blocked", "failed")  # a download rule admitted these


def admits_dynamix(candidate: Mapping[str, Any]) -> bool:
    """Whether the artists source admits the candidate as original-Dynamix material: its seed rule admitted it
    (``game`` dynamix or mixed) and it was not excluded or held (``pending``) -- a blocked or failed *download* of an
    admitted file still counts, since the access failure says nothing about the post's content."""
    return candidate.get("game") in model.ADMITTED_GAMES and candidate.get("disposition") in ADMITTING_DISPOSITIONS


def primary_evidence_problems(root: Path, asset: Mapping[str, Any],
                              candidates: list[dict] | None = None) -> list[str]:
    """Problems with ``primary_post`` mapping evidence (curated quotes for copies of a primary post's file): some
    ``artists`` discovery candidate with that page URL must carry the quote in its game evidence, be admitted by its
    source for Dynamix (:func:`admits_dynamix`; an excluded rejected submission or a held candidate corroborates
    nothing) **and** stand for the same file (:func:`_same_file`), otherwise the copy borrows another work's evidence."""
    problems = []
    entries = [entry for entry in asset.get("mapping_evidence", [])
               if isinstance(entry, dict) and entry.get("field") == "primary_post"]
    if not entries:
        return problems
    if candidates is None:
        candidates = artist_candidates(root)
    original = (asset.get("source_reported") or {}).get("url")
    for entry in entries:
        quote = model.normalize_text(entry["quote"]).rstrip("…")
        found = [c for c in candidates if c.get("page_url") == entry["url"] and any(
            quote in model.normalize_text(str(item)) for item in c.get("game_evidence", []))]
        quoting = [c for c in found if admits_dynamix(c)]
        if found and not quoting:
            problems.append(f"{asset['id']}: primary-post evidence {entry['url']} is quoted only by candidates the "
                            "artists source does not admit for Dynamix (" + ", ".join(
                                f"{c['id']}: {c.get('disposition')}, game {c.get('game')}" for c in found) + ")")
        elif not quoting:
            problems.append(f"{asset['id']}: primary-post evidence {entry['url']} is not corroborated by an artists "
                            "discovery snapshot")
        elif not any(_same_file(c, original) for c in quoting):
            problems.append(f"{asset['id']}: primary-post evidence {entry['url']} is corroborated only for other "
                            f"files ({', '.join(sorted(c['id'] for c in quoting))}), not for {original}")
    return problems


def review_conflicts(snapshot: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """(errors, warnings) for archived candidates a refresh no longer admits (``KEPT_FOR_REVIEW`` discovery errors):
    an archived file whose candidate is now *excluded* (a scope decision such as ``universe_only``) is an error until a
    curator restores the rule or withdraws the record; a now pending/failed/blocked one is a warning."""
    errors, warnings = [], []
    for entry in snapshot.get("errors", []):
        message = str(entry.get("message", ""))
        if KEPT_FOR_REVIEW not in message:
            continue
        line = f"{snapshot['source_id']}: {message}"
        (errors if " is now excluded (" in message else warnings).append(line)
    return errors, warnings


NON_SNAPSHOT_FILES = re.compile(r"^song-index(?:\.[a-z]+)?\.json$")  # proposed song indexes share the directory


def load_snapshots(root: Path, family: str, owned_ids: Iterable[str]) -> tuple[dict[str, dict], list[str], list[str]]:
    """(``{source_id: snapshot}``, errors, warnings) for ``family``'s discovery snapshots under ``root``.

    ``owned_ids`` are the family's sources (manifest records and seeds): the file at each one's
    :func:`lib.paths.discovery_relpath` must hold a valid snapshot of exactly that source. Any other file holding a
    snapshot of this family must sit at its own path. A file that cannot be attributed to a valid source ID (unreadable,
    no or malformed ``source_id``) is an error when its name belongs to this family (an owned path or a registered
    source of the family) and a warning when the name is unknown (it may belong to another family's worker)."""
    owned: dict[str, str] = {}
    for source_id in owned_ids:
        try:
            owned[paths.discovery_relpath(source_id)] = source_id
        except ValueError:
            continue
    registered = {paths.discovery_relpath(source_id): schema.source_family(source_id)
                  for source_id in schema.REGISTERED_SOURCE_IDS}
    snapshots: dict[str, dict] = {}
    errors: list[str] = []
    warnings: list[str] = []
    discovery_dir = root / paths.DISCOVERY_ROOT
    for path in sorted(discovery_dir.glob("*.json")) if discovery_dir.is_dir() else []:
        rel = path.relative_to(root).as_posix()
        if NON_SNAPSHOT_FILES.match(path.name):
            continue
        ours = rel in owned or registered.get(rel) == family
        try:
            data = manifest.load_json(path)
            problem = None
        except (ValueError, OSError) as error:
            data, problem = None, f"unreadable: {error}"
        source_id = data.get("source_id") if isinstance(data, dict) else None
        if not isinstance(source_id, str) or not schema.SOURCE_ID_RE.fullmatch(source_id):
            message = f"{rel}: not a discovery snapshot of a valid source ID ({problem or f'source_id {source_id!r}'})"
            if ours:
                errors.append(message)
            elif rel not in registered:
                warnings.append(message)
            continue
        if schema.source_family(source_id) != family:
            if ours:
                errors.append(f"{rel}: holds {source_id}, expected a snapshot of "
                              f"{owned.get(rel) or 'a ' + family + ' source'}")
            continue
        if rel != paths.discovery_relpath(source_id):
            errors.append(f"{rel}: holds {source_id}, expected at {paths.discovery_relpath(source_id)}")
            continue
        try:
            schema.validate_discovery(data, source_id)
        except ValueError as error:
            errors.append(f"{rel}: {error}")
            continue
        snapshots[source_id] = data
    return snapshots, errors, warnings


def credit_problems(asset: Mapping[str, Any]) -> list[str]:
    """Credit entries that cite no source: every credited name needs a URL (fetched page, curated or cited source)."""
    return [f"{asset['id']}: credit {entry.get('name')!r} ({entry.get('role')}) cites no source URL"
            for entry in asset.get("credit_evidence") or [] if not entry.get("url")]


def verify_family(root: str | os.PathLike[str], family: str,
                  seed_ids: Iterable[str] | None = None) -> tuple[list[str], list[str], dict]:
    """Offline ``--verify``: (errors, warnings, per-source disposition counts). Never touches the network.

    With ``seed_ids`` (the family's seed registry) a seed source without a source record is an error (a run that
    stopped before reaching it) and a source record without a seed a warning."""
    root = Path(root)
    errors: list[str] = []
    warnings: list[str] = []
    try:
        current = manifest.load_manifest(root, family, missing_ok=True)
    except (ValueError, OSError) as error:
        return [f"{paths.manifest_relpath(family)}: {error}"], warnings, {}
    snapshots, snapshot_errors, snapshot_warnings = load_snapshots(
        root, family, [*(source["id"] for source in current["sources"]), *(seed_ids or [])])
    errors.extend(snapshot_errors)
    warnings.extend(snapshot_warnings)
    for snapshot in snapshots.values():
        conflict_errors, conflict_warnings = review_conflicts(snapshot)
        errors.extend(conflict_errors)
        warnings.extend(conflict_warnings)
    others = []
    for other in schema.FAMILIES:
        if other != family:
            try:
                others.extend(manifest.load_manifest(root, other, missing_ok=True)["assets"])
            except (ValueError, OSError) as error:
                warnings.append(f"{paths.manifest_relpath(other)} not usable for cross-references: {error}")
    counts: dict = {}
    try:
        counts = schema.reconcile(current, snapshots, other_assets=others)
    except ValueError as error:
        errors.append(f"reconcile: {error}")
    own = {asset["id"]: asset for asset in current["assets"]}
    for snapshot in snapshots.values():
        for candidate in snapshot["candidates"]:
            target = own.get(candidate.get("asset_id") or "")
            if (candidate["disposition"] == "duplicate_bytes" and target is not None
                    and target.get("upstream_status") not in (None, "current")):
                warnings.append(f"{candidate['id']}: duplicate_bytes of {target['id']}, whose upstream_status is "
                                f"{target['upstream_status']!r}: a listed file is represented only by that record")
    artists = artist_candidates(root)
    for asset in current["assets"]:
        try:
            media.verify_asset_file(root, asset)
        except (media.MediaError, OSError) as error:
            errors.append(f"asset file: {error}")
        for title, song_id in zip(asset["song_titles"], asset["song_ids"]):
            if not song_id.startswith(paths.song_id_base(title)):
                warnings.append(f"{asset['id']}: song ID {song_id} does not derive from title {title!r}")
        errors.extend(primary_evidence_problems(root, asset, artists))
        errors.extend(credit_problems(asset))
    recorded ={asset["path"] for asset in current["assets"]}
    family_dir = root / paths.ASSET_ROOT / family
    for path in sorted(family_dir.rglob("*")) if family_dir.is_dir() else []:
        if path.is_symlink():
            errors.append(f"symlink in asset tree: {path.relative_to(root).as_posix()}")
        elif path.is_file():
            rel = path.relative_to(root).as_posix()
            if path.name.startswith(".") and path.name.endswith(".part"):
                warnings.append(f"leftover partial download (interrupted run): {rel}")
            elif rel not in recorded:
                errors.append(f"file not referenced by any {family} asset record: {rel}")
    for source in current["sources"]:
        if source["id"] not in snapshots:  # its assets/failures could not be reconciled with any candidate
            errors.append(f"{source['id']}: no valid discovery snapshot at {paths.discovery_relpath(source['id'])}")
    if seed_ids is not None:
        seeded, recorded_sources = set(seed_ids), {source["id"] for source in current["sources"]}
        for source_id in sorted(seeded - recorded_sources):
            errors.append(f"{source_id}: seed source has no source record in {paths.manifest_relpath(family)} "
                          "(not processed yet, or the run stopped before it)")
        for source_id in sorted(recorded_sources - seeded):
            warnings.append(f"{source_id}: source record has no seed in the registry")
    return errors, warnings, counts
