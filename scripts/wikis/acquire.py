"""Acquisition state, candidate dispositions, downloads, the crash journal and the run lock.

State = the committed family manifest + the previous discovery snapshots + a replayed journal. Every successful or
failed download is appended to the journal (``<work dir>/journal.jsonl``, fsynced) before the next request, and the
manifest/snapshots are rewritten atomically every few minutes and at exit; the journal is cleared after each such
write. A killed run therefore loses at most the file in flight, and the next run replays the journal first. Files are
placed only by :func:`lib.media.finalize_download` (temp file -> decode/verify -> ``os.replace``), so a crash never
leaves a successful-looking record without verified bytes.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from lib import manifest, media, net, paths, schema

from . import config, discover

FAMILY = "wikis"
DOWNLOAD_KINDS = discover.classify.DOWNLOAD_KINDS
AWAITING = "awaiting_download"
FLUSH_EVERY = 100
FLUSH_SECONDS = 300.0


class LockHeld(RuntimeError):
    pass


class ReclassifiedError(RuntimeError):
    """An archived asset's candidate is no longer accepted (e.g. a page edit made the file Universe-only). The
    contract has no state for "archived but no longer accepted", so the run stops before writing anything and the
    case goes to review instead of silently keeping or dropping the record."""

    def __init__(self, conflicts: list[str]):
        super().__init__("; ".join(conflicts))
        self.conflicts = conflicts


def default_work_dir(root: Path) -> Path:
    digest = hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"dynamix-fetch-wikis-{digest}"


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """``fcntl.flock`` on ``path``; :class:`LockHeld` (naming the holder's PID when recorded) if another run has it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                raise
            holder = os.pread(descriptor, 64, 0).decode("ascii", "replace").strip() or "unknown"
            raise LockHeld(f"another fetch_wikis.py run holds {path} (pid {holder}); monitor it instead of starting "
                           "a second writer") from None
        os.ftruncate(descriptor, 0)
        os.pwrite(descriptor, f"{os.getpid()}\n".encode(), 0)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class Journal:
    """Append-only JSON-lines event log; a torn last line (crash during write) is ignored."""

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> list[dict]:
        if not self.path.is_file():
            return []
        events = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and "event" in event:
                events.append(event)
        return events

    def append(self, event: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


@dataclass
class State:
    """Mutable working copy of the family manifest plus candidate dispositions carried between runs."""

    sources: dict[str, dict]
    assets: dict[str, dict]
    failures: dict[str, dict]
    fetched_at: str | None
    previous: dict[str, dict[str, dict]]  # source_id -> candidate ID -> candidate of the last snapshot
    candidate_events: dict[str, dict] = field(default_factory=dict)  # candidate ID -> replayed disposition

    @classmethod
    def load(cls, root: Path, journal: Journal | None = None) -> "State":
        data = manifest.load_manifest(root, FAMILY)
        previous = {}
        for source in config.SOURCES.values():
            snapshot = manifest.load_discovery(root, source.source_id)
            previous[source.source_id] = {c["id"]: c for c in (snapshot or {}).get("candidates", [])}
        state = cls({s["id"]: s for s in data["sources"]}, {a["id"]: a for a in data["assets"]},
                    {f["id"]: f for f in data["failures"]}, data["fetched_at"], previous)
        for event in journal.read() if journal else []:
            state.apply(event)
        return state

    def apply(self, event: Mapping[str, Any]) -> None:
        kind = event["event"]
        if kind == "asset":
            asset = event["asset"]
            self.assets[asset["id"]] = asset
            if self.fetched_at is None or asset["fetched_at"] > self.fetched_at:
                self.fetched_at = asset["fetched_at"]
        elif kind == "asset_status":
            asset = dict(self.assets[event["id"]])
            asset["upstream_status"] = event["upstream_status"]
            asset.pop("superseded_by", None)
            if event.get("superseded_by"):
                asset["superseded_by"] = event["superseded_by"]
            self.assets[event["id"]] = asset
        elif kind == "failure":
            self.failures[event["failure"]["id"]] = event["failure"]
        elif kind == "clear_failures":
            for failure_id in [f for f, record in self.failures.items() if record["candidate_id"] == event["id"]]:
                del self.failures[failure_id]
        elif kind == "candidate":
            self.candidate_events[event["id"]] = {key: event.get(key) for key in
                                                  ("disposition", "reason", "asset_id", "source_reported")}
        else:
            raise ValueError(f"unknown journal event {kind!r}")

    def current_asset(self, candidate_id: str) -> dict | None:
        found = [asset for asset in self.assets.values() if asset["candidate_id"] == candidate_id
                 and asset.get("upstream_status") not in ("removed", "superseded")]
        return max(found, key=lambda asset: asset["fetched_at"]) if found else None

    def asset_with_sha(self, source_id: str, sha256: str) -> dict | None:
        return self.assets.get(schema.asset_id(source_id, sha256))

    def manifest(self) -> dict:
        data = {"schema_version": schema.SCHEMA_VERSION, "family": FAMILY, "fetched_at": self.fetched_at,
                "sources": list(self.sources.values()), "assets": list(self.assets.values()),
                "failures": list(self.failures.values())}
        return manifest.sort_manifest(data)


def same_revision(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """Two ``source_reported`` objects describe the same upstream file revision (SHA-1, size, timestamp)."""
    if not a or not b:
        return False
    keys = ("sha1", "bytes", "timestamp")
    return all(a.get(key) == b.get(key) for key in keys) and any(a.get(key) is not None for key in keys)


# --- Candidate dispositions -------------------------------------------------------------------------------------------

def awaiting_reason(plan: discover.Plan) -> str:
    return (f"{AWAITING}: accepted {plan.kind} ({plan.decision.game}) candidate; not downloaded yet "
            "(metadata-only discovery or acquisition limit)")


def resolve(plans: list[discover.Plan], state: State, *, complete: bool) -> tuple[list[dict], list[str]]:
    """Candidates with their acquisition dispositions; marks re-uploaded and removed files in ``state``.

    Returns ``(candidates, conflicts)``: a conflict is an archived current asset whose candidate is no longer
    accepted (see :class:`ReclassifiedError`).
    """
    warnings: list[str] = []
    candidates = []
    seen: set[str] = set()
    source_id = plans[0].source.source_id if plans else None
    for plan in plans:
        candidate = dict(plan.candidate)
        cid = candidate["id"]
        seen.add(cid)
        current = state.current_asset(cid)
        if current is not None and not same_revision(current["source_reported"], candidate["source_reported"]):
            # Re-uploaded under the same title: the archived bytes stay, as an earlier revision.
            state.apply({"event": "asset_status", "id": current["id"], "upstream_status": "superseded"})
            current = None
        previous = state.candidate_events.get(cid) or state.previous.get(plan.source.source_id, {}).get(cid)
        if not plan.accepted:
            if current is not None:
                warnings.append(f"{cid}: archived as {current['id']} but now classified {plan.decision.disposition} "
                                f"({plan.decision.reason}); review the classification")
            candidates.append(candidate)
            continue
        if current is not None:
            refreshed = refresh(current, plan)
            if refreshed != current:
                state.assets[current["id"]] = refreshed
            candidate.update(disposition="downloaded", asset_id=current["id"])
            candidate.pop("reason", None)
        elif previous and previous.get("disposition") in ("duplicate_bytes", "failed", "blocked") and same_revision(
                previous.get("source_reported"), candidate["source_reported"]) and (
                previous.get("disposition") != "duplicate_bytes" or previous.get("asset_id") in state.assets):
            candidate.update(disposition=previous["disposition"], reason=previous.get("reason"))
            if previous["disposition"] == "duplicate_bytes":
                candidate["asset_id"] = previous["asset_id"]
            if previous["disposition"] == "failed" and not any(
                    f["candidate_id"] == cid for f in state.failures.values()):
                candidate.update(disposition="pending", reason=awaiting_reason(plan))
        else:
            candidate.update(disposition="pending", reason=awaiting_reason(plan))
        candidates.append(candidate)
    if complete and source_id is not None:
        for asset in list(state.assets.values()):
            if asset["source_id"] == source_id and asset["candidate_id"] not in seen and asset.get(
                    "upstream_status") not in ("removed", "superseded"):
                state.apply({"event": "asset_status", "id": asset["id"], "upstream_status": "removed"})
    return candidates, warnings


# --- Downloads --------------------------------------------------------------------------------------------------------

def _quality(source: config.WikiSource, transformed: bool, fmt: str, reported_mime: str | None,
             comparisons: Mapping[str, Any]) -> dict:
    if transformed:
        return {"source_variant": "unknown", "origin_status": "wiki_copy", "master_status": "unverified",
                "notes": f"The CDN returned {fmt} although {reported_mime} was reported and the stored original was "
                         "requested; these bytes are a platform transform, not the stored upload."}
    if source.key == "fandom" and comparisons["sha1_match"] is False:
        return {"source_variant": "unknown", "origin_status": "wiki_copy", "master_status": "unverified",
                "notes": "Fandom's CDN response to format=original differs from the upload recorded by Fandom's API "
                         "(SHA-1 and size); not the uploaded bytes byte for byte. Pixels were not compared."}
    if source.key == "fandom":
        note = ("Stored original requested from Fandom's CDN with format=original; a wiki copy, not an artist "
                "master.")
    else:
        note = "Original upload as served by the wiki's file host; a wiki copy, not an artist master."
    return {"source_variant": "site_original", "origin_status": "wiki_copy", "master_status": "unverified",
            "notes": note}


DESCRIPTIVE_FIELDS = ("title", "kind", "page_url", "illustrators", "composers", "song_titles", "song_ids",
                      "collections", "mapping_status", "mapping_evidence", "quality", "rights", "credit_evidence",
                      "game_associations", "notes", "transformed")


def describe(plan: discover.Plan, *, fmt: str, sha1: str, nbytes: int, comparisons: Mapping[str, Any]) -> dict:
    """The fields of an asset record that come from classification (not from the bytes): recomputed on every run,
    so better evidence reaches already archived records without downloading them again."""
    source = plan.source
    candidate = plan.candidate
    reported = candidate["source_reported"]
    # A known platform transform: another format than advertised and not the advertised stored bytes.
    transformed = comparisons["format_match"] is False and comparisons["sha1_match"] is False
    status, evidence = discover.mapping(plan)
    fields: dict[str, Any] = {
        "title": candidate["title"], "kind": plan.kind, "page_url": candidate["page_url"],
        "illustrators": list(plan.illustrators), "composers": list(plan.composers),
        "song_titles": list(plan.song_titles), "song_ids": list(plan.song_ids) if status != "unmapped" else [],
        "collections": list(plan.collections), "mapping_status": status, "mapping_evidence": evidence,
        "quality": _quality(source, transformed, fmt, reported.get("mime"), comparisons),
        "rights": schema.default_rights(creator=plan.illustrators[0] if len(plan.illustrators) == 1 else None,
                                        url=candidate["page_url"]),
    }
    if plan.credit_evidence:
        fields["credit_evidence"] = [dict(item) for item in plan.credit_evidence]
    associations = [name for name in plan.decision.associations if name != schema.GAME]
    if plan.decision.game == "mixed":
        fields["game_associations"] = [schema.GAME] + (associations or ["unknown_other"])
    notes = []
    mismatched = [name for name in ("sha1", "bytes") if comparisons[f"{name}_match"] is False]
    if mismatched and not transformed:
        delta = reported["bytes"] - nbytes if reported.get("bytes") is not None else None
        notes.append(f"The wiki API reports different {' and '.join(mismatched)} than the served bytes"
                     + (f" (API {delta:+d} bytes; a constant per-format gap like this is consistent with metadata "
                        "removal by the CDN)" if delta else "")
                     + "; recorded as source_reported versus download_observed, not as a second version.")
    other = plan.counterpart or {}
    if (source.key == "miraheze" and comparisons["sha1_match"] and isinstance(other.get("sha1"), str)
            and other["sha1"].lower() != sha1):
        gap = other["size"] - nbytes if isinstance(other.get("size"), int) else None
        notes.append("Fandom's API records this title's upload with another SHA-1"
                     + (f" and {gap:+d} bytes" if gap else "")
                     + f" (Fandom upload {other.get('timestamp')}); this Miraheze copy is not byte-identical to that "
                       "upload.")
    if plan.decision.game == "mixed":
        notes.append("Shared artwork: the wikis also associate this file with " + (", ".join(associations) or
                                                                                    "another game") + ".")
    if notes:
        fields["notes"] = " ".join(notes)
    if transformed:
        fields["transformed"] = True
    return fields


def build_asset(plan: discover.Plan, info: Mapping[str, Any], result: Any, url: str, fetched_at: str) -> dict:
    source = plan.source
    candidate = plan.candidate
    reported = candidate["source_reported"]
    observed = schema.make_download_observed(info, result.content_type)
    comparisons = schema.compute_comparisons(reported, observed)
    asset = {
        "id": schema.asset_id(source.source_id, info["sha256"]), "source_id": source.source_id,
        "candidate_id": candidate["id"], "game": schema.GAME, "download_url": url,
        "resolved_url": schema.redact_url(result.final_url, strict=False), "path": info["path"],
        "original_filename": str(plan.info.get("name") or discover.file_name(plan.title)),
        "bytes": info["bytes"], "sha256": info["sha256"], "sha1": info["sha1"], "format": info["format"],
        "mime": info["mime"], "width": info["width"], "height": info["height"], "fetched_at": fetched_at,
        "source_reported": reported, "download_observed": observed, "comparisons": comparisons,
        "upstream_status": "current",
        **describe(plan, fmt=info["format"], sha1=info["sha1"], nbytes=info["bytes"], comparisons=comparisons),
    }
    return schema.canonical(asset, "asset")


def refresh(asset: Mapping[str, Any], plan: discover.Plan) -> dict:
    """``asset`` (same upstream revision) with its descriptive fields recomputed from the current plan."""
    kept = {key: value for key, value in asset.items() if key not in DESCRIPTIVE_FIELDS}
    fields = describe(plan, fmt=asset["format"], sha1=asset["sha1"], nbytes=asset["bytes"],
                      comparisons=asset["comparisons"])
    result = schema.canonical({**kept, **fields}, "asset")
    schema.validate_asset(result, FAMILY)
    return result


@dataclass
class Outcome:
    candidate_id: str
    disposition: str
    asset_id: str | None = None
    message: str = ""


class Acquirer:
    """Downloads accepted candidates of one source and records every outcome in the journal and ``state``."""

    def __init__(self, root: Path, state: State, journal: Journal, client: Any, *,
                 now: Callable[[], str] = manifest.now_utc, log: Callable[[str], None] = print):
        self.root, self.state, self.journal, self.client = root, state, journal, client
        self.now, self.log = now, log

    def record(self, event: dict) -> None:
        self.journal.append(event)
        self.state.apply(event)

    def file_ok(self, asset: Mapping[str, Any], *, rehash: bool) -> bool:
        try:
            target = paths.safe_path(self.root, asset["path"], under=f"{paths.ASSET_ROOT}/{FAMILY}")
        except ValueError:
            return False
        if not target.is_file():
            return False
        if not rehash:
            return target.stat().st_size == asset["bytes"]
        try:
            media.verify_asset_file(self.root, asset)
        except media.MediaError as error:
            self.log(f"  re-hash mismatch {asset['id']}: {error}")
            return False
        return True

    def needs_download(self, candidate: Mapping[str, Any], *, resume: bool, retry_failed: bool) -> bool:
        disposition = candidate["disposition"]
        if disposition == "pending":
            return str(candidate.get("reason", "")).startswith(AWAITING)
        if disposition == "downloaded":
            return not self.file_ok(self.state.assets[candidate["asset_id"]], rehash=resume)
        if disposition == "failed":
            return retry_failed and any(f["candidate_id"] == candidate["id"] and f["retriable"]
                                        for f in self.state.failures.values())
        return False

    def download(self, plan: discover.Plan) -> Outcome:
        source = plan.source
        cid = plan.candidate_id
        url = discover.download_url(source, plan.info["url"])
        dest = self.root / paths.ASSET_ROOT / FAMILY / paths.source_slug(source.source_id)
        name = str(plan.info.get("name") or discover.file_name(plan.title))
        attempted = self.now()
        try:
            result = self.client.download(url, dest, paths.filename_stem(name))
        except net.FetchError as error:
            return self._failed(plan, error.failure(source_id=source.source_id, candidate_id=cid, phase="download",
                                                    attempted_at=attempted, url=None if error.url else url),
                                "blocked" if error.error_class in ("blocked", "challenge") else "failed")
        existing = self.state.asset_with_sha(source.source_id, result.sha256)
        if existing is not None and existing["candidate_id"] != cid:
            # Another File: title of this wiki already archived these bytes: no second record or file.
            Path(result.temp_path).unlink(missing_ok=True)
            return self._finish(plan, "duplicate_bytes", existing["id"],
                                f"identical SHA-256 to {existing['candidate_id']} (already archived)")
        if existing is not None and self.file_ok(existing, rehash=False):
            # Same bytes again for this title (refresh, or a revert to an archived earlier revision).
            Path(result.temp_path).unlink(missing_ok=True)
            if existing.get("upstream_status") != "current":
                self.record({"event": "asset_status", "id": existing["id"], "upstream_status": "current"})
            return self._finish(plan, "downloaded", existing["id"])
        try:
            info = media.finalize_download(result, self.root, lambda i: paths.asset_relpath(
                FAMILY, paths.source_slug(source.source_id), name, i["sha256"], i["ext"]))
        except media.MediaError as error:
            return self._failed(plan, error.failure(source_id=source.source_id, candidate_id=cid, url=url,
                                                    attempted_at=attempted), "failed")
        asset = build_asset(plan, info, result, url, attempted)
        schema.validate_asset(asset, FAMILY)
        previous = [a for a in self.state.assets.values() if a["candidate_id"] == cid and a["id"] != asset["id"]
                    and a.get("upstream_status") == "superseded" and not a.get("superseded_by")]
        self.record({"event": "asset", "asset": asset})
        for old in previous:
            self.record({"event": "asset_status", "id": old["id"], "upstream_status": "superseded",
                         "superseded_by": asset["id"]})
        return self._finish(plan, "downloaded", asset["id"])

    def _finish(self, plan: discover.Plan, disposition: str, asset_id: str, reason: str | None = None) -> Outcome:
        self.record({"event": "clear_failures", "id": plan.candidate_id})
        self.record({"event": "candidate", "id": plan.candidate_id, "disposition": disposition, "reason": reason,
                     "asset_id": asset_id, "source_reported": plan.candidate["source_reported"]})
        return Outcome(plan.candidate_id, disposition, asset_id, reason or "")

    def _failed(self, plan: discover.Plan, failure: dict, disposition: str) -> Outcome:
        self.record({"event": "clear_failures", "id": plan.candidate_id})
        self.record({"event": "failure", "failure": failure})
        reason = f"{failure['phase']} {failure['error_class']}: {failure['message']}"[:300]
        self.record({"event": "candidate", "id": plan.candidate_id, "disposition": disposition, "reason": reason,
                     "asset_id": None, "source_reported": plan.candidate["source_reported"]})
        return Outcome(plan.candidate_id, disposition, None, reason)


def apply_outcomes(candidates: list[dict], state: State) -> list[dict]:
    """Candidates with the dispositions recorded during this run (journal events) applied."""
    result = []
    for candidate in candidates:
        event = state.candidate_events.get(candidate["id"])
        if event and same_revision(event.get("source_reported"), candidate["source_reported"]):
            candidate = dict(candidate)
            candidate["disposition"] = event["disposition"]
            candidate.pop("asset_id", None)
            candidate.pop("reason", None)
            if event.get("asset_id"):
                candidate["asset_id"] = event["asset_id"]
            if event.get("reason"):
                candidate["reason"] = event["reason"]
        result.append(candidate)
    return result


class Flusher:
    """Calls ``write`` every ``every`` downloads or ``seconds`` seconds (and once more at the end)."""

    def __init__(self, write: Callable[[], None], *, every: int = FLUSH_EVERY, seconds: float = FLUSH_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self.write, self.every, self.seconds, self.clock = write, every, seconds, clock
        self.count, self.last = 0, clock()

    def tick(self) -> None:
        self.count += 1
        if self.count >= self.every or self.clock() - self.last >= self.seconds:
            self.write()
            self.count, self.last = 0, self.clock()
