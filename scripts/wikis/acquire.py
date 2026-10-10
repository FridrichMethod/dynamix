"""Acquisition state, candidate dispositions and downloads.

State = the committed family manifest + the previous discovery snapshots + a replayed journal. Every successful or
failed download is appended to the journal (``<work dir>/journal.jsonl``, fsynced) before the next request, and the
manifest/snapshots are rewritten atomically every few minutes and at exit; the journal is cleared after each such
write. A killed run therefore loses at most the file in flight, and the next English-wiki run replays the journal
first; :func:`resolve` weighs the replayed outcomes against the current evidence (a candidate the evidence no longer
accepts is not written back as failed), and only outcomes recorded after it are applied over it. Files are placed only
by :func:`lib.media.finalize_download` (temp file -> decode/verify -> ``os.replace``) or, to repair a
damaged archived file, by :meth:`Acquirer.repair` (identical verified bytes -> ``os.replace``), so a crash never leaves
a successful-looking record without verified bytes.

Archived records follow their bytes (asset ID = source + SHA-256), not a title:

- the same bytes under a new upstream revision of the same title (identical re-upload, revert, re-import, or a new
  API timestamp/SHA-1 for unchanged bytes) keep their record, which then describes the current revision; the earlier
  revision is kept in the record's ``Upstream history`` note;
- a title that no longer exists (``removed``) or now has other bytes (``superseded``) hands its record to the title
  that serves the same bytes now (a MediaWiki file move), so current upstream bytes are never recorded only as
  removed or superseded;
- a reviewed withdrawal (``scripts/wikis/reviews.json``, ``withdrawals``) is the only way an archived file leaves the
  manifest: its record and file are deleted when the current evidence excludes it with the reviewed reason, and only
  by an acquiring run (a ``--metadata-only`` run reports it and keeps the file).

Failure records follow the candidate's current upstream revision: a failed/blocked disposition is rebuilt from the
Failure record while it describes the current revision (the previous snapshot, or the revision's upload time not later
than the attempt), and a Failure whose candidate is gone from a complete discovery, re-uploaded or no longer accepted
is dropped, so a transient outage or an upstream change never leaves the outputs unwritable.

The work directory, journal and locks are in :mod:`wikis.workdir`.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from lib import manifest, media, net, paths, schema

from . import config, discover
from .workdir import (STATE_DIR, Journal, LockHeld, UnsafeWorkDir, claim_work_dir,  # noqa: F401 (re-exported)
                      default_work_dir, exclusive_lock, prepare_state_dir, prepare_work_dir, root_lock_path)

FAMILY = "wikis"
DOWNLOAD_KINDS = discover.classify.DOWNLOAD_KINDS
AWAITING = "awaiting_download"
FLUSH_EVERY = 100
FLUSH_SECONDS = 300.0
HISTORY_MARK = "Upstream history: "
HISTORY_SEPARATOR = " | "
KEPT_NOTE = ("classification kept from the previous discovery: this discovery is incomplete, so archived files are "
             "not re-classified")
ANIMATED_NOTE = ("archived by an earlier --include-animated run and kept: the animated_capture exclusion only stops "
                 "new downloads")
WITHDRAWAL_PENDING_NOTE = ("a reviewed withdrawal covers this archived file; a metadata-only run keeps the file, "
                           "record and classification, and the next acquiring run carries the withdrawal out")
NO_URL_MESSAGE = "the wiki API reported no usable file URL for this file; no request was sent"
CLASSIFICATION_FIELDS = ("role", "game", "game_evidence", "song_titles", "illustrators", "composers", "collections",
                         "notes")


class ReclassifiedError(RuntimeError):
    """An archived asset's candidate is no longer accepted by complete evidence (e.g. a page edit made the file
    Universe-only). The contract has no state for "archived but no longer accepted", so the run stops before writing
    anything; the case is resolved by fixing the classification or by a reviewed withdrawal."""

    def __init__(self, conflicts: list[str]):
        super().__init__("; ".join(conflicts))
        self.conflicts = conflicts


def delete_asset_file(root: Path, rel: str) -> None:
    """Delete a withdrawn record's file (idempotent; only inside ``assets/public/wikis``)."""
    try:
        target = paths.safe_path(root, rel, under=f"{paths.ASSET_ROOT}/{FAMILY}")
    except ValueError:
        return
    target.unlink(missing_ok=True)


@dataclass
class State:
    """Mutable working copy of the family manifest plus candidate dispositions carried between runs."""

    sources: dict[str, dict]
    assets: dict[str, dict]
    failures: dict[str, dict]
    fetched_at: str | None
    previous: dict[str, dict[str, dict]]  # source_id -> candidate ID -> candidate of the last snapshot
    # candidate ID -> outcome of the latest download attempt: replayed from the journal (consumed by resolve(), which
    # weighs it against the current evidence) or recorded by this run's acquisition (applied by apply_outcomes())
    candidate_events: dict[str, dict] = field(default_factory=dict)
    attempts: dict[str, str] = field(default_factory=dict)  # source_id -> latest download attempt (this run/journal)

    @classmethod
    def load(cls, root: Path, journal: Journal | None = None) -> "State":
        data = manifest.load_manifest(root, FAMILY)
        previous = {}
        for source in config.SOURCES.values():
            snapshot = manifest.load_discovery(root, source.source_id)
            previous[source.source_id] = {c["id"]: c for c in (snapshot or {}).get("candidates", [])}
        state = cls({s["id"]: s for s in data["sources"]}, {a["id"]: a for a in data["assets"]},
                    {f["id"]: f for f in data["failures"]}, data["fetched_at"], previous)
        for number, event in enumerate(journal.read() if journal else [], start=2):
            if state.withdrawal_applied(root, event):
                # Written before the crash: the manifest already lacks the record and the file is gone (the run died
                # after writing its outputs and before clearing the journal). Nothing is left to redo.
                continue
            problem = state.replay_problem(event)
            if problem:
                raise UnsafeWorkDir(f"{journal.path}: line {number}: {problem}; the journal does not fit this "
                                    "root's manifest (inspect it, then remove it or use another --work-dir)")
            state.apply(event)
            if event["event"] == "withdraw":  # the file may outlive a crash between the journal entry and unlink
                delete_asset_file(root, event["path"])
        return state

    def withdrawal_applied(self, root: Path, event: Mapping[str, Any]) -> bool:
        """A journaled withdrawal that the written outputs already carry out: the manifest holds no record with its
        asset ID, no record uses its path, and no file exists at that path (a safe path inside the wiki assets). A
        withdrawal naming an archived record, or a path that still holds a file, is replayed or refused instead."""
        if event.get("event") != "withdraw" or not isinstance(event.get("id"), str) \
                or not isinstance(event.get("path"), str) or event["id"] in self.assets:
            return False
        if any(asset["path"] == event["path"] for asset in self.assets.values()):
            return False
        try:
            target = paths.safe_path(root, event["path"], under=f"{paths.ASSET_ROOT}/{FAMILY}")
        except ValueError:
            return False
        return not target.exists() and not target.is_symlink()

    def replay_problem(self, event: Mapping[str, Any]) -> str | None:
        """Why a journal event cannot be replayed onto this state (None when it can): records must validate, belong
        to a registered MediaWiki source and refer to records the manifest (or earlier events) hold; a withdrawal
        must name the archived record's own path."""
        sources = {config.SOURCES[key].source_id for key in config.MEDIAWIKI_KEYS}
        kind = event.get("event")
        try:
            if kind == "asset":
                schema.validate_asset(event.get("asset"), FAMILY)
                if event["asset"]["source_id"] not in sources:
                    return "asset of an unknown source"
            elif kind == "failure":
                schema.validate_failure(event.get("failure"), FAMILY)
                if event["failure"]["source_id"] not in sources:
                    return "failure of an unknown source"
            elif kind == "asset_status":
                if event.get("id") not in self.assets:
                    return f"status of an unknown asset {event.get('id')!r}"
                if event.get("upstream_status") not in ("current", "removed", "superseded"):
                    return "unknown upstream_status"
                if event.get("superseded_by") is not None and event["superseded_by"] not in self.assets:
                    return "superseded_by names an unknown asset"
            elif kind == "withdraw":
                asset = self.assets.get(event.get("id"))
                if asset is None or asset["path"] != event.get("path"):
                    return "withdrawal of an asset/path the manifest does not hold"
            elif kind in ("candidate", "clear_failures"):
                if not isinstance(event.get("id"), str) or not any(
                        event["id"].startswith(source + ":") for source in sources):
                    return "candidate of an unknown source"
            else:
                return f"unknown event {kind!r}"
        except (ValueError, KeyError, TypeError) as error:
            return f"invalid {kind} event ({error})"
        return None

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
            stamp, source_id = event.get("attempted_at"), event.get("source_id")
            if stamp and source_id and stamp > self.attempts.get(source_id, ""):
                self.attempts[source_id] = stamp
        elif kind == "withdraw":
            gone = self.assets.pop(event["id"], None)
            successor = (gone or {}).get("superseded_by")
            for other_id, other in list(self.assets.items()):  # keep superseded_by chains inside the manifest
                if other.get("superseded_by") == event["id"]:
                    other = dict(other)
                    other.pop("superseded_by", None)
                    if successor:
                        other["superseded_by"] = successor
                    self.assets[other_id] = other
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


def same_content(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """The API reports the same stored bytes (equal, advertised SHA-1 and size), whatever the revision timestamp."""
    if not a or not b:
        return False
    return all(a.get(key) is not None and a.get(key) == b.get(key) for key in ("sha1", "bytes"))


# --- Record history ---------------------------------------------------------------------------------------------------

def history(asset: Mapping[str, Any]) -> list[str]:
    """Entries of the record's ``Upstream history`` note (kept across refreshes; always the last note)."""
    notes = asset.get("notes") or ""
    index = notes.rfind(HISTORY_MARK)
    if index < 0:
        return []
    text = notes[index + len(HISTORY_MARK):].rstrip()
    text = text[:-1] if text.endswith(".") else text
    return [item.strip() for item in text.split(HISTORY_SEPARATOR) if item.strip()]


def _revision_text(reported: Mapping[str, Any]) -> str:
    return (f"upload {reported.get('timestamp') or 'of unknown time'} (API SHA-1 {reported.get('sha1') or 'unknown'}, "
            f"{reported.get('bytes') if reported.get('bytes') is not None else 'unknown'} bytes)")


def _add_history(entries: list[str], entry: str) -> list[str]:
    return entries if entry in entries else entries + [entry]


def adopt_revision(asset: Mapping[str, Any], reported: Mapping[str, Any]) -> dict:
    """``asset`` describing a new upstream revision with the same reported content (SHA-1 and size): an identical
    re-upload, a revert or a re-import. ``source_reported``/``comparisons`` follow the new revision; the earlier one is
    kept in the history note."""
    entries = _add_history(history(asset), "same bytes as the earlier " + _revision_text(asset["source_reported"]))
    result = dict(asset, source_reported=dict(reported),
                  comparisons=schema.compute_comparisons(reported, asset["download_observed"]))
    result["notes"] = _replace_history(asset.get("notes"), entries)
    result = schema.canonical(result, "asset")
    schema.validate_asset(result, FAMILY)
    return result


def _replace_history(notes: str | None, entries: list[str]) -> str:
    base = notes or ""
    index = base.rfind(HISTORY_MARK)
    if index >= 0:
        base = base[:index].rstrip()
    tail = HISTORY_MARK + HISTORY_SEPARATOR.join(entries) + "." if entries else ""
    return " ".join(part for part in (base, tail) if part)


# --- Candidate dispositions -------------------------------------------------------------------------------------------

def awaiting_reason(plan: discover.Plan) -> str:
    return (f"{AWAITING}: accepted {plan.kind} ({plan.decision.game}) candidate; not downloaded yet "
            "(metadata-only discovery, incomplete discovery or acquisition limit)")


@dataclass
class Resolution:
    candidates: list[dict] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)  # archived current assets no longer accepted (review)
    withdrawals: list[tuple[dict, Any]] = field(default_factory=list)  # (asset, reviewed withdrawal) to carry out
    deferred: list[tuple[dict, Any]] = field(default_factory=list)  # withdrawals a metadata-only run only reports


def animated_kept(plan: discover.Plan) -> bool:
    """An accepted-but-animated file: excluded only because of the run option (``--include-animated``)."""
    decision = plan.decision
    return (decision.disposition == "excluded" and decision.reason.startswith("animated_capture")
            and plan.kind in DOWNLOAD_KINDS and decision.game in ("dynamix", "mixed"))


def resolve(plans: list[discover.Plan], state: State, *, complete: bool, evidence_complete: bool = True,
            withdrawals: Mapping[str, Any] | None = None, defer_withdrawals: bool = False) -> Resolution:
    """Candidates with their acquisition dispositions; marks re-uploaded and removed files in ``state`` and drops
    Failure records that no longer describe their candidate (see the module docstring).

    ``complete``: this source's inventory is complete (files missing from it are ``removed``). ``evidence_complete``:
    every pooled inventory is complete; otherwise archived candidates keep their previous classification and their
    records are not refreshed (an outage must not re-classify archived files). A conflict is an archived current
    asset that complete evidence no longer accepts (see :class:`ReclassifiedError`), unless a reviewed withdrawal
    (``withdrawals``: title -> :class:`wikis.reviews.Withdrawal`) covers it; ``defer_withdrawals`` (metadata-only
    runs) keeps such a file and its classification and only reports the withdrawal.

    Removed and re-uploaded titles are marked before any candidate is resolved, so the outcome does not depend on the
    order of titles (a byte duplicate sorting before the title whose record it carries over sees that record's
    final status).
    """
    result = Resolution()
    if not plans:
        return result
    source_id = plans[0].source.source_id
    seen = {plan.candidate_id for plan in plans}
    if complete:
        for asset in list(state.assets.values()):
            if asset["source_id"] == source_id and asset["candidate_id"] not in seen and asset.get(
                    "upstream_status") not in ("removed", "superseded"):
                state.apply({"event": "asset_status", "id": asset["id"], "upstream_status": "removed"})
    for plan in plans:  # re-uploads first (see above)
        reported = plan.candidate["source_reported"]
        current = state.current_asset(plan.candidate_id)
        while current is not None and not same_revision(current["source_reported"], reported):
            if same_content(current["source_reported"], reported):
                state.assets[current["id"]] = adopt_revision(current, reported)
            else:  # re-uploaded under the same title: the archived bytes stay, as an earlier revision
                state.apply({"event": "asset_status", "id": current["id"], "upstream_status": "superseded"})
            current = state.current_asset(plan.candidate_id)
    for plan in plans:
        candidate = dict(plan.candidate)
        cid = candidate["id"]
        current = state.current_asset(cid)
        if current is not None:
            archived = _archived(plan, candidate, current, state, evidence_complete)
            if archived is not None:
                result.candidates.append(archived)
                continue
            withdrawal = (withdrawals or {}).get(plan.title)
            if withdrawal is not None and withdrawal.covers(current["sha256"], plan.decision):
                if defer_withdrawals:
                    result.deferred.append((current, withdrawal))
                    result.candidates.append(_kept_classification(plan, candidate, current, state,
                                                                  note=WITHDRAWAL_PENDING_NOTE))
                    continue
                result.withdrawals.append((current, withdrawal))
            else:
                result.conflicts.append(f"{cid}: archived as {current['id']} but now classified "
                                        f"{plan.decision.disposition} ({plan.decision.reason}); review the "
                                        "classification or add a reviewed withdrawal")
            result.candidates.append(candidate)
            continue
        result.candidates.append(_unarchived(plan, candidate, state, evidence_complete))
    _drop_stale_failures(state, source_id, result.candidates, complete=complete)
    for cid in seen:  # replayed outcomes are part of this resolution now; the current evidence decided over them
        state.candidate_events.pop(cid, None)
    return result


def _drop_stale_failures(state: State, source_id: str, candidates: list[dict], *, complete: bool) -> None:
    """Drop this source's Failure records whose candidate is gone from a complete discovery or is no longer
    failed/blocked (re-uploaded, reclassified, archived): the record described an attempt that no longer applies."""
    by_id = {candidate["id"]: candidate for candidate in candidates}
    for failure in list(state.failures.values()):
        if failure["source_id"] != source_id or failure["phase"] == "discover":
            continue
        candidate = by_id.get(failure["candidate_id"])
        if (candidate is None and complete) or (
                candidate is not None and candidate["disposition"] not in ("failed", "blocked")):
            state.apply({"event": "clear_failures", "id": failure["candidate_id"]})


def _archived(plan: discover.Plan, candidate: dict, current: dict, state: State,
              evidence_complete: bool) -> dict | None:
    """The candidate of a title whose current revision is archived, or None when it is no longer accepted."""
    if not evidence_complete:
        return _kept_classification(plan, candidate, current, state)
    animated = not plan.accepted and animated_kept(plan)
    if not plan.accepted and not animated:
        return None
    refreshed = refresh(current, plan)
    if refreshed != current:
        state.assets[current["id"]] = refreshed
    candidate.update(disposition="downloaded", asset_id=current["id"])
    candidate.pop("reason", None)
    if animated:
        candidate["notes"] = _join_note(candidate.get("notes"), ANIMATED_NOTE)
    return candidate


def _join_note(notes: str | None, note: str) -> str:
    if notes and note in notes:
        return notes
    return discover._clip("; ".join(part for part in (note, notes) if part), discover.NOTES_LIMIT)


def _kept_classification(plan: discover.Plan, candidate: dict, current: Mapping[str, Any], state: State, *,
                         note: str = KEPT_NOTE) -> dict:
    """``candidate`` (current upstream fields) with the classification of the discovery that archived it; ``note``
    says why it is kept."""
    previous = state.previous.get(plan.source.source_id, {}).get(plan.candidate_id)
    kept = {key: value for key, value in candidate.items() if key not in CLASSIFICATION_FIELDS}
    if previous and previous.get("disposition") == "downloaded" and previous.get("asset_id") == current["id"]:
        kept.update({key: previous[key] for key in CLASSIFICATION_FIELDS if key in previous})
    elif plan.accepted and plan.kind == current["kind"]:
        kept.update({key: candidate[key] for key in CLASSIFICATION_FIELDS if key in candidate})
    else:
        kept["game_evidence"] = [f"archived record {current['id']} (classified by the discovery that downloaded it)"]
        for name in ("song_titles", "illustrators", "composers", "collections"):
            if current.get(name):
                kept[name] = list(current[name])
    kept["role"] = current["kind"]
    if kept.get("game") not in ("dynamix", "mixed"):
        kept["game"] = "mixed" if current.get("game_associations") else "dynamix"
    kept["notes"] = _join_note(kept.get("notes"), note)
    kept.update(disposition="downloaded", asset_id=current["id"])
    kept.pop("reason", None)
    return kept


def failure_disposition(failure: Mapping[str, Any]) -> tuple[str, str]:
    """``(disposition, reason)`` of a candidate whose current revision has this Failure record (as :meth:`Acquirer.
    _failed` records them)."""
    disposition = "blocked" if failure["error_class"] in ("blocked", "challenge") else "failed"
    return disposition, f"{failure['phase']} {failure['error_class']}: {failure['message']}"[:300]


def current_failure(state: State, candidate: Mapping[str, Any], previous: Mapping[str, Any] | None
                    ) -> dict | None:
    """The candidate's Failure record when it describes the candidate's current upstream revision, else None.

    The previous outcome (this run's journal or the last snapshot) decides when it lists the candidate as failed or
    blocked (same revision or not). Otherwise (e.g. an incomplete snapshot left the candidate out) the record applies
    when the current revision was uploaded no later than the attempt. A "no usable file URL" failure no longer applies
    once the API reports a URL."""
    found = [failure for failure in state.failures.values()
             if failure["candidate_id"] == candidate["id"] and failure["phase"] != "discover"]
    if not found:
        return None
    failure = max(found, key=lambda record: record["attempted_at"])
    reported = candidate["source_reported"]
    if failure["message"] == NO_URL_MESSAGE and candidate.get("file_url"):
        return None
    if previous and previous.get("disposition") in ("failed", "blocked") and previous.get("source_reported"):
        return failure if same_revision(previous["source_reported"], reported) else None
    stamp = reported.get("timestamp")
    return failure if isinstance(stamp, str) and stamp and stamp <= failure["attempted_at"] else None


def _unarchived(plan: discover.Plan, candidate: dict, state: State, evidence_complete: bool = True) -> dict:
    """The candidate of a title whose current revision has no record of its own."""
    cid = candidate["id"]
    previous = state.candidate_events.get(cid) or state.previous.get(plan.source.source_id, {}).get(cid)
    if not plan.accepted and not plan.probe:
        failure = None if evidence_complete else current_failure(state, candidate, previous)
        if failure is not None:  # partial evidence must not re-classify a failed file either
            disposition, reason = failure_disposition(failure)
            candidate.update(disposition=disposition, reason=reason)
        return candidate
    carried = previous.get("disposition") if previous and same_revision(
        previous.get("source_reported"), candidate["source_reported"]) else None
    if carried == "duplicate_bytes" and plan.accepted and previous.get("asset_id") in state.assets:
        target = state.assets[previous["asset_id"]]
        if target.get("upstream_status") in ("removed", "superseded"):
            # These bytes were seen under this title before (identical SHA-256) and their record's own title is gone
            # or changed: the record now belongs to this title.
            moved = retarget(target, plan)
            for event in relink_events(state, moved, previous_owner=target):
                state.apply(event)
            state.assets[moved["id"]] = moved
            candidate.update(disposition="downloaded", asset_id=moved["id"])
            candidate.pop("reason", None)
            return candidate
        candidate.update(disposition="duplicate_bytes", reason=previous.get("reason"), asset_id=target["id"])
        return candidate
    failure = current_failure(state, candidate, previous)
    if failure is not None:
        disposition, reason = failure_disposition(failure)
        candidate.update(disposition=disposition, reason=reason)
        return candidate
    if plan.accepted:
        candidate.update(disposition="pending", reason=awaiting_reason(plan))
    return candidate


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


def describe(plan: discover.Plan, *, fmt: str, sha1: str, nbytes: int, comparisons: Mapping[str, Any],
             history_entries: list[str] | None = None) -> dict:
    """The fields of an asset record that come from classification (not from the bytes): recomputed on every run,
    so better evidence reaches already archived records without downloading them again. ``history_entries`` (the
    record's upstream history) are kept as the last note."""
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
                     + (f" (the API reports {abs(delta)} {'more' if delta > 0 else 'fewer'} bytes than were served; "
                        "cause not established)" if delta else "")
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
        notes.append("Shared artwork: this file is also associated with " + (", ".join(associations) or
                                                                             "another game") + " (see game evidence).")
    notes.extend(plan.decision.rule_notes)
    if plan.description:
        description = plan.description.replace(HISTORY_MARK.strip(), "Upstream history -")
        notes.append(f"File description page: {description!r}.")
    notes.extend(plan.archivist_notes)
    if history_entries:
        notes.append(HISTORY_MARK + HISTORY_SEPARATOR.join(history_entries) + ".")
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
                      comparisons=asset["comparisons"], history_entries=history(asset))
    result = schema.canonical({**kept, **fields}, "asset")
    schema.validate_asset(result, FAMILY)
    return result


def retarget(asset: Mapping[str, Any], plan: discover.Plan, *, download: tuple[str, Any, str] | None = None) -> dict:
    """``asset`` (identical bytes) as the current record of ``plan``'s title and revision.

    Used when the bytes reappear under a new revision of the same title (``download`` given: the bytes were just
    fetched again) or under another title whose own record is gone or changed (a file move). ``path`` and
    ``original_filename`` keep the name the bytes were first archived under; ``download`` = ``(url, result,
    fetched_at)`` updates where and when the identical bytes were last fetched."""
    reported = plan.candidate["source_reported"]
    entries = history(asset)
    if asset["candidate_id"] != plan.candidate_id:
        entries = _add_history(entries, f"first archived as {asset['candidate_id']} (that title's "
                                        f"{_revision_text(asset['source_reported'])}); original_filename and path "
                                        "keep that name")
    elif not same_revision(asset["source_reported"], reported):
        entries = _add_history(entries, "same bytes as the earlier " + _revision_text(asset["source_reported"]))
    kept = {key: value for key, value in asset.items() if key not in DESCRIPTIVE_FIELDS}
    kept.update(candidate_id=plan.candidate_id, source_reported=dict(reported), upstream_status="current",
                comparisons=schema.compute_comparisons(reported, asset["download_observed"]))
    kept.pop("superseded_by", None)
    if download is not None:
        url, result, fetched_at = download
        kept.update(download_url=url, resolved_url=schema.redact_url(result.final_url, strict=False),
                    fetched_at=fetched_at)
    fields = describe(plan, fmt=asset["format"], sha1=asset["sha1"], nbytes=asset["bytes"],
                      comparisons=kept["comparisons"], history_entries=entries)
    result = schema.canonical({**kept, **fields}, "asset")
    schema.validate_asset(result, FAMILY)
    return result


def relink_events(state: State, record: Mapping[str, Any], *, previous_owner: Mapping[str, Any] | None = None
                  ) -> list[dict]:
    """``asset_status`` events that keep ``superseded_by`` chains valid once ``record`` is the current record of its
    candidate: every other record of that candidate that is current, or superseded without a successor, is
    superseded by it; records that named ``record`` as their successor under its previous candidate
    (``previous_owner``) now name that record's own successor (or none)."""
    events = []
    for other in state.assets.values():
        if other["id"] == record["id"]:
            continue
        if other["candidate_id"] == record["candidate_id"] and (
                other.get("upstream_status") in (None, "current")
                or (other.get("upstream_status") == "superseded" and not other.get("superseded_by"))):
            events.append({"event": "asset_status", "id": other["id"], "upstream_status": "superseded",
                           "superseded_by": record["id"]})
        elif (previous_owner is not None and previous_owner["candidate_id"] != record["candidate_id"]
              and other.get("superseded_by") == record["id"]):
            events.append({"event": "asset_status", "id": other["id"], "upstream_status": other["upstream_status"],
                           "superseded_by": previous_owner.get("superseded_by")})
    return events


@dataclass
class Outcome:
    candidate_id: str
    disposition: str
    asset_id: str | None = None
    message: str = ""


class Acquirer:
    """Downloads accepted candidates and records every outcome in the journal and ``state``.

    Thread-safe for one worker per source (different hosts may download concurrently): ``lock`` guards the journal and
    every read or write of the shared ``state``; network transfers and file decoding run outside it. Each source writes
    only its own asset directory, records and candidates, so workers never contend for the same file."""

    def __init__(self, root: Path, state: State, journal: Journal, client: Any, *,
                 now: Callable[[], str] = manifest.now_utc, log: Callable[[str], None] = print,
                 lock: Any = None):
        self.root, self.state, self.journal, self.client = root, state, journal, client
        self.now, self.log = now, log
        self.lock = lock if lock is not None else threading.RLock()
        self.repair_failures: list[str] = []  # archived files that could not be re-downloaded (record kept)

    def record(self, event: dict) -> None:
        with self.lock:
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

    def needs_download(self, plan: discover.Plan, candidate: Mapping[str, Any], *, resume: bool,
                       retry_failed: bool) -> bool:
        disposition = candidate["disposition"]
        if disposition == "pending":
            return plan.probe or str(candidate.get("reason", "")).startswith(AWAITING)
        if disposition == "downloaded":
            with self.lock:
                asset = self.state.assets[candidate["asset_id"]]
            return not self.file_ok(asset, rehash=resume)
        if disposition == "failed":
            with self.lock:
                return retry_failed and any(f["candidate_id"] == candidate["id"] and f["retriable"]
                                            for f in self.state.failures.values())
        return False

    def download(self, plan: discover.Plan) -> Outcome:
        source = plan.source
        cid = plan.candidate_id
        attempted = self.now()
        with self.lock:
            archived = self.state.current_asset(cid)  # a damaged archived file is being re-downloaded
        file_url = plan.candidate.get("file_url")
        if not file_url:  # retriable: the next discovery may report a URL again (then the Failure no longer applies)
            failure = schema.failure_record(
                source_id=source.source_id, candidate_id=cid, phase="download", url=plan.candidate["page_url"],
                attempted_at=attempted, attempts=1, error_class="other", http_status=None, retriable=True,
                manual=False, message=NO_URL_MESSAGE)
            return self._failed(plan, failure, "failed", attempted, archived)
        url = discover.download_url(source, file_url)
        dest = self.root / paths.ASSET_ROOT / FAMILY / paths.source_slug(source.source_id)
        name = str(plan.info.get("name") or discover.file_name(plan.title))
        try:
            result = self.client.download(url, dest, paths.filename_stem(name))
        except net.FetchError as error:
            failure = error.failure(source_id=source.source_id, candidate_id=cid, phase="download",
                                    attempted_at=attempted, url=None if error.url else url)
            return self._failed(plan, failure, "blocked" if error.error_class in ("blocked", "challenge") else "failed",
                                attempted, archived)
        if plan.probe:
            Path(result.temp_path).unlink(missing_ok=True)
            reason = (f"{plan.candidate['reason']}; an inspection download on {attempted} succeeded (SHA-256 "
                      f"{result.sha256}, {result.bytes} bytes): review the bytes")
            self.log(f"REVIEW {cid}: the file downloads again; review its bytes")
            return self._finish(plan, "pending", None, attempted, reason=reason)
        with self.lock:
            existing = self.state.asset_with_sha(source.source_id, result.sha256)
            if existing is not None and existing["candidate_id"] != cid and existing.get("upstream_status") not in (
                    "removed", "superseded"):
                # Another File: title of this wiki serves these bytes now and owns their record: no second record.
                Path(result.temp_path).unlink(missing_ok=True)
                return self._finish(plan, "duplicate_bytes", existing["id"], attempted,
                                    reason=f"identical SHA-256 to {existing['candidate_id']} (already archived)")
        if existing is not None:
            return self._same_bytes(plan, existing, result, url, attempted)
        try:
            info = media.finalize_download(result, self.root, lambda i: paths.asset_relpath(
                FAMILY, paths.source_slug(source.source_id), name, i["sha256"], i["ext"]))
        except media.MediaError as error:
            return self._failed(plan, error.failure(source_id=source.source_id, candidate_id=cid, url=url,
                                                    attempted_at=attempted), "failed", attempted, archived)
        asset = build_asset(plan, info, result, url, attempted)
        schema.validate_asset(asset, FAMILY)
        with self.lock:
            self.record({"event": "asset", "asset": asset})
            for event in relink_events(self.state, asset):
                self.record(event)
            return self._finish(plan, "downloaded", asset["id"], attempted)

    def _same_bytes(self, plan: discover.Plan, existing: dict, result: Any, url: str, attempted: str) -> Outcome:
        """The bytes of an archived record again: this title's own record (a damaged file being repaired, or the same
        bytes under a new revision) or the record of a title that is gone or changed (a move). A damaged file is
        replaced by the verified identical bytes; the record then describes this title's current revision."""
        try:
            if self.file_ok(existing, rehash=True):
                Path(result.temp_path).unlink(missing_ok=True)
            else:
                self.repair(existing, result)
                self.log(f"  repaired {existing['path']} with verified identical bytes")
        except (media.MediaError, OSError, ValueError) as error:
            Path(result.temp_path).unlink(missing_ok=True)
            return self._repair_failed(plan, f"replacing the damaged file failed: {error}")
        with self.lock:
            existing = self.state.assets[existing["id"]]
            updated = retarget(existing, plan, download=(url, result, attempted))
            if updated != existing:
                self.record({"event": "asset", "asset": updated})
            for event in relink_events(self.state, updated, previous_owner=existing):
                self.record(event)
            return self._finish(plan, "downloaded", updated["id"], attempted)

    def repair(self, asset: Mapping[str, Any], result: Any) -> None:
        """Replace ``asset``'s damaged file with the downloaded temp file after checking that the temp file has
        exactly the recorded bytes, hashes, format and dimensions (``os.replace``; the temp file is gone after)."""
        temp = Path(result.temp_path)
        try:
            info = media.inspect(temp)
            for name in ("bytes", "sha256", "sha1", "format", "mime", "width", "height"):
                if info[name] != asset[name]:
                    raise media.MediaError(f"{asset['id']}: downloaded {name} {info[name]!r} differs from the record")
            target = paths.safe_path(self.root, asset["path"], under=f"{paths.ASSET_ROOT}/{FAMILY}")
            target.parent.mkdir(parents=True, exist_ok=True)
            paths.safe_path(self.root, asset["path"], under=f"{paths.ASSET_ROOT}/{FAMILY}")
            os.chmod(temp, 0o644)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)

    def _finish(self, plan: discover.Plan, disposition: str, asset_id: str | None, attempted: str, *,
                reason: str | None = None) -> Outcome:
        with self.lock:
            self.record({"event": "clear_failures", "id": plan.candidate_id})
            self.record({"event": "candidate", "id": plan.candidate_id, "disposition": disposition, "reason": reason,
                         "asset_id": asset_id, "source_reported": plan.candidate["source_reported"],
                         "source_id": plan.source.source_id, "attempted_at": attempted})
        return Outcome(plan.candidate_id, disposition, asset_id, reason or "")

    def _failed(self, plan: discover.Plan, failure: dict, disposition: str, attempted: str,
                archived: Mapping[str, Any] | None) -> Outcome:
        if archived is not None:
            # The title's current revision is archived (its file is being repaired): the record stays and the
            # candidate stays downloaded; the problem is reported (exit 1) instead of a Failure record.
            return self._repair_failed(plan, f"{failure['error_class']}: {failure['message']}")
        reason = f"{failure['phase']} {failure['error_class']}: {failure['message']}"[:300]
        with self.lock:
            self.record({"event": "clear_failures", "id": plan.candidate_id})
            self.record({"event": "failure", "failure": failure})
            self.record({"event": "candidate", "id": plan.candidate_id, "disposition": disposition, "reason": reason,
                         "asset_id": None, "source_reported": plan.candidate["source_reported"],
                         "source_id": plan.source.source_id, "attempted_at": attempted})
        return Outcome(plan.candidate_id, disposition, None, reason)

    def _repair_failed(self, plan: discover.Plan, message: str) -> Outcome:
        text = f"{plan.candidate_id}: archived file could not be re-downloaded ({message}); the record is kept"
        with self.lock:
            self.repair_failures.append(text)
        self.log(f"REPAIR FAILED {text}")
        return Outcome(plan.candidate_id, "repair_failed", None, message)


def apply_outcomes(candidates: list[dict], state: State) -> list[dict]:
    """Candidates with the outcomes this run's downloads recorded (after :func:`resolve`) applied.

    Outcomes replayed from an interrupted run's journal are not re-applied here: :func:`resolve` already weighed them
    against the current evidence and consumed them, so a candidate that the evidence no longer accepts (and whose
    Failure record :func:`_drop_stale_failures` removed) is never written back as failed or blocked."""
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
    """Calls ``write`` every ``every`` downloads or ``seconds`` seconds (and once more at the end); ``lock`` (shared
    with the :class:`Acquirer`) serializes ticks from concurrent workers with state changes."""

    def __init__(self, write: Callable[[], None], *, every: int = FLUSH_EVERY, seconds: float = FLUSH_SECONDS,
                 clock: Callable[[], float] = time.monotonic, lock: Any = None):
        self.write, self.every, self.seconds, self.clock = write, every, seconds, clock
        self.lock = lock if lock is not None else threading.RLock()
        self.count, self.last = 0, clock()

    def tick(self) -> None:
        with self.lock:
            self.count += 1
            if self.count >= self.every or self.clock() - self.last >= self.seconds:
                self.write()
                self.count, self.last = 0, self.clock()
