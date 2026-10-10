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
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

from lib import checks, manifest, media, net, paths, schema

from . import model
from .adapters import ADAPTERS

LOG = logging.getLogger("dynamix.primary")
SEEDS_DIR = Path(__file__).resolve().parent / "seeds"
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
    """

    def __init__(self, client: Any, *, blocked_hosts: dict[str, str], last_used: dict[str, float],
                 min_interval: float, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self._client, self._blocked, self._last = client, blocked_hosts, last_used
        self._interval, self._sleep, self._clock = min_interval, sleep, clock
        self.calls: dict[str, int] = {}

    def _host(self, url: str) -> str:
        try:
            return (urlsplit(url).hostname or "").lower()
        except ValueError:
            return ""

    def _call(self, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        host = self._host(url)
        if host in self._blocked:
            raise HostSkipped(f"host {host} refused an earlier request in this run ({self._blocked[host]}); "
                              "not contacted again", url=url)
        wait = self._last.get(host, float("-inf")) + self._interval - self._clock()
        if wait > 0:
            self._sleep(wait)
        self.calls[url] = self.calls.get(url, 0) + 1
        try:
            return getattr(self._client, method)(url, *args, **kwargs)
        except net.BlockedError as error:
            self._blocked.setdefault(host, f"{error.error_class}, HTTP {error.http_status}")
            raise
        finally:
            self._last[host] = self._clock()

    def mark_blocked(self, url: str, reason: str) -> None:
        """Record a refusal an adapter detected in a 200 response (risk-control code, captcha page)."""
        host = self._host(url)
        if host:
            self._blocked.setdefault(host, reason)

    def get_json(self, url: str, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return self._call("get_json", url, params, **kwargs)

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

def stored_url(url: str | None) -> str | None:
    """A URL fit for storage (credential parameters redacted) or None when it is not a valid http(s) URL."""
    if not url or checks.url_problem(url):
        return None
    return checks.redact_url(url, strict=False)


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


def md5_note(path: Path, token: str | None) -> str | None:
    if not token:
        return None
    digest = hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
    if digest == token.lower():
        return f"File MD5 equals the upstream name token {token}, consistent with the uploaded original."
    return f"File MD5 {digest} differs from the upstream name token {token}."


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

    # -- public --

    def run(self, only: Iterable[str] | None = None) -> dict[str, dict[str, int]]:
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
        try:
            context = AdapterContext(client=client, seed=seed, source_id=source_id, now=self.now)
            LOG.info("%s: discovering", source_id)
            discovery = ADAPTERS[seed["adapter"]].discover(context, seed)
            state = SourceState(self.family, seed, started, discovery, previous, {
                asset["id"]: asset for asset in self.manifest["assets"] if asset["source_id"] == source_id})
            state.errors = list(discovery.errors)
            self._resolve(state, client)
        finally:
            client.close()
        counts = schema.disposition_counts(state.candidates.values())
        LOG.info("%s: %s", source_id, ", ".join(f"{key} {value}" for key, value in counts.items() if value))
        return counts

    # -- resolution --

    def _resolve(self, state: SourceState, client: GuardedClient) -> None:
        drafts = sorted(state.discovery.candidates, key=lambda draft: draft.upstream_key)
        keys = [draft.upstream_key for draft in drafts]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{state.source_id}: adapter produced duplicate candidate keys")
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
        for draft in drafts:
            if draft.action == "download":
                self._resolve_download(state, client, draft)
                state.pending_ids.discard(schema.candidate_id(state.source_id, draft.upstream_key))
                self._persist(state)
                self._maybe_interrupt()

    def _record_non_download(self, state: SourceState, draft: model.Draft) -> None:
        sid = state.source_id
        cid = schema.candidate_id(sid, draft.upstream_key)
        previous_asset = self._previous_current_asset(state, cid)
        if previous_asset is not None:
            # Archived bytes are never dropped by a refresh: a transient error, a missing evidence quote or a changed
            # classification keeps the archived record and is reported for review instead.
            state.candidates[cid] = self._previous_candidate(state, cid) or candidate_record(
                sid, draft, "downloaded", asset_id=previous_asset["id"])
            state.errors.append(model.discovery_error(draft.error_phase if draft.error is not None else "discover",
                                                      stored_url(draft.page_url),
                                                      f"{cid} is now {draft.action} ({draft.reason}); the archived "
                                                      f"record {previous_asset['id']} is kept for review"))
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
            state.candidates[cid] = candidate_record(sid, draft, "downloaded", asset_id=refreshed["id"])
            return
        dest = paths.safe_path(self.root, f"{paths.ASSET_ROOT}/{family}/{paths.source_slug(sid)}",
                               under=paths.ASSET_ROOT)
        attempted_at = self.now()
        urls = [draft.file_url, *draft.fallback_urls]
        for position, url in enumerate(urls):
            try:
                result = client.download(url, dest, paths.filename_stem(draft.original_filename))
                if url != draft.file_url:
                    draft = copy.copy(draft)
                    draft.file_url = url
                break
            except net.FetchError as error:
                if position + 1 < len(urls) and isinstance(error, net.HttpError) and error.http_status == 404:
                    LOG.info("%s: %s not found, trying the next rendition", cid, url)
                    continue
                self._download_failed(state, draft, error, "download", attempted_at, previous_asset)
                return
        self.downloads += 1
        new_id = schema.asset_id(sid, result.sha256)
        owner = state.assets.get(new_id)
        if owner is not None and owner["candidate_id"] != cid:  # same source already archived these bytes
            Path(result.temp_path).unlink(missing_ok=True)
            state.candidates[cid] = candidate_record(
                sid, draft, "duplicate_bytes", asset_id=new_id,
                reason=f"duplicate_bytes: identical bytes already archived for {owner['candidate_id']}")
            return
        try:
            info = media.finalize_download(result, self.root, lambda measured: paths.asset_relpath(
                family, paths.source_slug(sid), draft.original_filename, measured["sha256"], measured["ext"]))
        except media.MediaError as error:
            self._download_failed(state, draft, error, "decode", attempted_at, previous_asset)
            return
        if owner is not None:  # same candidate, same bytes as before: keep the first acquisition's measurements
            refreshed = self._refresh_record(owner, draft)
            state.assets[new_id] = refreshed
        else:
            notes = [note for note in [md5_note(paths.safe_path(self.root, info["path"]), draft.md5_token)] if note]
            state.assets[new_id] = asset_record(family, sid, draft, info, content_type=result.content_type,
                                                resolved_url=result.final_url, fetched_at=attempted_at,
                                                extra_notes=notes)
            if previous_asset is not None and previous_asset["id"] != new_id:
                superseded = dict(previous_asset)
                superseded["upstream_status"] = "superseded"
                superseded["superseded_by"] = new_id
                state.assets[superseded["id"]] = schema.canonical(superseded, "asset")
        state.candidates[cid] = candidate_record(sid, draft, "downloaded", asset_id=new_id)
        state.failures.pop(schema.failure_id(cid, "download"), None)
        state.failures.pop(schema.failure_id(cid, "decode"), None)

    def _download_failed(self, state: SourceState, draft: model.Draft, error: Any, phase: str, attempted_at: str,
                         previous_asset: dict | None) -> None:
        sid = state.source_id
        cid = schema.candidate_id(sid, draft.upstream_key)
        failed = model.error_draft(draft.upstream_key, draft.page_url, draft.title, error, phase=phase,
                                   file_url=draft.file_url, role=draft.role, game=draft.game,
                                   game_evidence=draft.game_evidence, source_reported=draft.source_reported)
        failed.candidate_extra = draft.candidate_extra
        if previous_asset is not None:
            state.candidates[cid] = candidate_record(sid, draft, "downloaded", asset_id=previous_asset["id"])
            state.errors.append(model.discovery_error(phase, stored_url(draft.file_url),
                                                      f"refresh of {cid} failed, previous archive kept: "
                                                      f"{failed.reason}"))
            return
        state.candidates[cid] = candidate_record(sid, failed, failed.action)
        failure = failure_for(error, sid, failed, phase, attempted_at)
        if failure is not None:
            state.failures[failure["id"]] = failure
        LOG.warning("%s: %s", cid, failed.reason)

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
        if asset.get("download_url") != stored_url(draft.file_url):
            return False
        try:
            media.verify_asset_file(self.root, asset)
        except media.MediaError as error:
            LOG.warning("%s: archived file does not verify, downloading again (%s)", asset["id"], error)
            return False
        return True

    def _refresh_record(self, asset: Mapping[str, Any], draft: model.Draft) -> dict:
        """Descriptive fields from the current seed/draft on top of the archived measurements."""
        info = {name: asset[name] for name in ("path", "bytes", "sha256", "sha1", "format", "mime", "width",
                                               "height")}
        notes = [note for note in [md5_note(paths.safe_path(self.root, asset["path"]), draft.md5_token)] if note]
        return asset_record(self.family, asset["source_id"], draft, info,
                            content_type=asset["download_observed"]["content_type"],
                            resolved_url=asset["resolved_url"], fetched_at=asset["fetched_at"], extra_notes=notes)

    def _carry_over(self, state: SourceState, current_ids: set[str]) -> None:
        """Keep previous candidates that this run did not rediscover when they still name an archived asset."""
        if not state.previous:
            return
        asset_candidates = {asset["candidate_id"] for asset in state.assets.values()}
        note = "Carried over from an earlier run: not rediscovered by this run's seeds/enumeration."
        for candidate in state.previous["candidates"]:
            if candidate["id"] in current_ids:
                continue
            if candidate.get("asset_id") not in state.assets and candidate["id"] not in asset_candidates:
                continue
            kept = dict(candidate)
            if note not in kept.get("notes", ""):
                kept["notes"] = " ".join(filter(None, [kept.get("notes"), note]))
            state.candidates[kept["id"]] = kept
            for failure in self.manifest["failures"]:
                if failure["candidate_id"] == kept["id"]:
                    state.failures[failure["id"]] = failure

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

def primary_evidence_problems(root: Path, asset: Mapping[str, Any]) -> list[str]:
    """Warnings for ``primary_post`` mapping evidence (curated quotes for copies of a primary post's file) that the
    primary post's own snapshot does not corroborate: some ``artists`` discovery candidate with that page URL must
    carry the quote in its game evidence."""
    problems = []
    entries = [entry for entry in asset.get("mapping_evidence", [])
               if isinstance(entry, dict) and entry.get("field") == "primary_post"]
    if not entries:
        return problems
    discovery_dir = root / paths.DISCOVERY_ROOT
    candidates: list[dict] = []
    for path in sorted(discovery_dir.glob("*.json")) if discovery_dir.is_dir() else []:
        try:
            data = manifest.load_json(path)
        except (ValueError, OSError):
            continue
        if isinstance(data, dict) and str(data.get("source_id", "")).startswith("artists:"):
            candidates.extend(c for c in data.get("candidates", []) if isinstance(c, dict))
    for entry in entries:
        quote = model.normalize_text(entry["quote"]).rstrip("…")
        if not any(c.get("page_url") == entry["url"] and any(quote in model.normalize_text(str(item))
                                                             for item in c.get("game_evidence", []))
                   for c in candidates):
            problems.append(f"{asset['id']}: primary-post evidence {entry['url']} is not corroborated by an artists "
                            "discovery snapshot")
    return problems


def verify_family(root: str | os.PathLike[str], family: str) -> tuple[list[str], list[str], dict]:
    """Offline ``--verify``: (errors, warnings, per-source disposition counts). Never touches the network."""
    root = Path(root)
    errors: list[str] = []
    warnings: list[str] = []
    try:
        current = manifest.load_manifest(root, family, missing_ok=True)
    except (ValueError, OSError) as error:
        return [f"{paths.manifest_relpath(family)}: {error}"], warnings, {}
    snapshots: dict[str, dict] = {}
    discovery_dir = root / paths.DISCOVERY_ROOT
    for path in sorted(discovery_dir.glob("*.json")) if discovery_dir.is_dir() else []:
        try:
            data = manifest.load_json(path)
            source_id = data.get("source_id") if isinstance(data, dict) else None
            if not isinstance(source_id, str) or not schema.SOURCE_ID_RE.fullmatch(source_id):
                continue
            if schema.source_family(source_id) != family:
                continue
            if path.relative_to(root).as_posix() != paths.discovery_relpath(source_id):
                errors.append(f"{path.relative_to(root).as_posix()}: holds {source_id}, expected at "
                              f"{paths.discovery_relpath(source_id)}")
                continue
            schema.validate_discovery(data, source_id)
            snapshots[source_id] = data
        except (ValueError, OSError) as error:
            errors.append(f"{path.relative_to(root).as_posix()}: {error}")
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
    for asset in current["assets"]:
        try:
            media.verify_asset_file(root, asset)
        except (media.MediaError, OSError) as error:
            errors.append(f"asset file: {error}")
        for title, song_id in zip(asset["song_titles"], asset["song_ids"]):
            if not song_id.startswith(paths.song_id_base(title)):
                warnings.append(f"{asset['id']}: song ID {song_id} does not derive from title {title!r}")
        warnings.extend(primary_evidence_problems(root, asset))
    recorded = {asset["path"] for asset in current["assets"]}
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
        if source["id"] not in snapshots:
            warnings.append(f"{source['id']}: no discovery snapshot at {paths.discovery_relpath(source['id'])}")
    return errors, warnings, counts
