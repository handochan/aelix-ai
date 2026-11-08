"""``aelix.job`` facts in the owning session, written confirmed, read by one fold (#722, ADR-0293).

A job leaves facts as ``custom`` entries of type :data:`JOB_FACT_TYPE` in the
session that owns it. Each is one complete record (ADR-0242 rule 2), ``v: 1``:

``start``       ``{job_id, kind, kind_version, owner, mode, notify, title, created, settings_key}``
``process``     ``{job_id, seq, evidence: {pid, started, host, pgid?, win_job?}}``
``cleanup``     ``{job_id, helpers: confirmed|unverified|none, reason?}`` — before the outcome
``supervision`` ``{job_id, state: "unresolved", reason, notice?: {id, body, brief}}``
``outcome``     ``{job_id, status, brief, elapsed_ms, client_records: ok|missing, notice?: {id, body, brief}}``

The delivery is not a fact: it is the ``custom_message`` the kernel places under
the notice's derived id (``nt-…``), so "was it delivered" is one ``get_entry``.

**Confirmed and placed once.** :func:`append_fact` raises
:class:`RecordWriteFailed` unless the fact is in the file. Every record is
placed under an entry id derived from its own ``fid`` (:func:`record_entry_id`)
through the store's idempotent placement (``Session.place_custom_entry``,
#721's ``place_entry``): a write whose outcome was unknown is settled under the
store's lock and adopted when it landed, so a retry — at once, or by the
outbox much later — never writes a fact twice and never assumes it written
(Codex #13).

**Fold** (:func:`fold`), in file order: grouped by ``job_id``; identity comes
from ``start``, and a fact that comes before its job's ``start`` (or has none)
is ignored; ``process`` facts accumulate by ``seq``; the first VALID ``outcome``
wins — one whose status is not a known one is ignored, and a later conflicting
one is logged and ignored; a fact of an unknown ``v`` or of a malformed shape is
ignored, and a ``start`` of an unknown ``v`` or shape marks the job unreadable
(Codex #14). A fold reads, it never writes.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from aelix_agent_core.harness.inbox import notice_entry_id

_log = logging.getLogger(__name__)

JOB_FACT_TYPE = "aelix.job"
NOTICE_CUSTOM_TYPE = "aelix.job_notice"
FACT_VERSION = 1

FACT_KINDS = ("start", "process", "mode", "cleanup", "supervision", "outcome")

#: The statuses an ``outcome`` fact may record. ``recovered`` is a restart's
#: reading of a job's own records; it shows as ``done``.
OUTCOME_STATUSES = frozenset({"done", "failed", "stopped", "lost", "recovered"})
_HELPERS = frozenset({"confirmed", "unverified", "none"})

#: The prefix of a record's derived entry id; the store mints bare hex ids.
RECORD_ENTRY_PREFIX = "jr-"


class RecordWriteFailed(Exception):
    """A fact could not be confirmed in the session file."""


def make_fact(fact: str, job_id: str, **fields: Any) -> dict[str, Any]:
    if fact not in FACT_KINDS:
        raise ValueError(f"unknown job fact {fact!r}")
    return {"v": FACT_VERSION, "fact": fact, "job_id": job_id, "fid": uuid.uuid4().hex, **fields}


def record_entry_id(fid: str) -> str:
    """The entry id a record with ``fid`` is placed under: ``jr-`` and 16 hex of its sha256."""

    return RECORD_ENTRY_PREFIX + hashlib.sha256(fid.encode("utf-8")).hexdigest()[:16]


async def append_record(session: Any, custom_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """Place one ``custom`` entry once and confirm it, or raise :class:`RecordWriteFailed`.

    ``data`` gets a ``fid`` when it has none; the entry id is derived from it,
    so writing the same record again — a retry now or the outbox's later —
    adopts the line that landed instead of writing a second one. Returns the
    data as placed (with its ``fid``).
    """

    from aelix_agent_core.session.storage import PlacementConflict, SessionError

    fid = data.get("fid")
    if not isinstance(fid, str) or not fid:
        fid = uuid.uuid4().hex
        data = {**data, "fid": fid}

    def same(existing: Any) -> bool:
        payload = getattr(existing, "data", None)
        return (
            getattr(existing, "type", None) == "custom"
            and isinstance(payload, Mapping)
            and payload.get("fid") == fid
        )

    entry_id = record_entry_id(fid)
    for attempt in (1, 2):
        try:
            await session.place_custom_entry(entry_id, custom_type, data, same=same)
            return data
        except PlacementConflict as exc:
            raise RecordWriteFailed(f"entry {entry_id} holds another record") from exc
        except SessionError as exc:
            # #721 — the line may have landed: the same placement again settles
            # the outcome under the store's lock and adopts it, or writes it.
            if exc.code == "outcome_unknown" and attempt == 1:
                continue
            raise RecordWriteFailed(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — every other failure is "not written"
            raise RecordWriteFailed(str(exc)) from exc
    raise RecordWriteFailed("the record's outcome stayed unknown")  # pragma: no cover


async def append_fact(session: Any, data: dict[str, Any]) -> None:
    await append_record(session, JOB_FACT_TYPE, data)


def _valid_start(data: Mapping[str, Any]) -> bool:
    version = data.get("kind_version", 1)
    return (
        isinstance(data.get("kind"), str)
        and bool(data.get("kind"))
        and isinstance(version, int)
        and not isinstance(version, bool)
        and version >= 1
        and isinstance(data.get("owner", ""), str)
        and isinstance(data.get("title", ""), str)
        and isinstance(data.get("notify", True), bool)
    )


def _valid(kind: str, data: Mapping[str, Any]) -> bool:
    if kind == "process":
        seq = data.get("seq")
        return (
            isinstance(seq, int)
            and not isinstance(seq, bool)
            and seq >= 1
            and isinstance(data.get("evidence"), Mapping)
        )
    if kind == "cleanup":
        return data.get("helpers") in _HELPERS
    if kind == "supervision":
        return data.get("state") == "unresolved"
    if kind == "outcome":
        return data.get("status") in OUTCOME_STATUSES
    return True


async def delivered(session: Any, notice_id: str) -> bool:
    """Whether the notice's entry is anywhere in the file (any branch)."""

    try:
        return await session.get_entry(notice_entry_id(notice_id)) is not None
    except Exception:  # noqa: BLE001 — unknown reads as not delivered
        return False


@dataclass
class FoldedJob:
    job_id: str
    start: dict[str, Any] | None = None
    processes: dict[int, dict[str, Any]] = field(default_factory=dict)
    mode: list[dict[str, Any]] = field(default_factory=list)
    cleanup: dict[str, Any] | None = None
    supervision: list[dict[str, Any]] = field(default_factory=list)
    outcome: dict[str, Any] | None = None
    unreadable: bool = False
    conflicts: int = 0

    @property
    def kind(self) -> str:
        return str((self.start or {}).get("kind", ""))

    @property
    def kind_version(self) -> int:
        value = (self.start or {}).get("kind_version", 1)
        return value if isinstance(value, int) else 1

    @property
    def owner(self) -> str:
        return str((self.start or {}).get("owner", ""))

    @property
    def notify(self) -> bool:
        return bool((self.start or {}).get("notify", True))

    def process_list(self) -> list[dict[str, Any]]:
        return [self.processes[k] for k in sorted(self.processes)]


def fold(entries: list[Any]) -> dict[str, FoldedJob]:
    """Every job the entries record, by ``job_id``, in first-start order."""

    jobs: dict[str, FoldedJob] = {}
    for entry in entries:
        if getattr(entry, "type", None) != "custom":
            continue
        if getattr(entry, "custom_type", None) != JOB_FACT_TYPE:
            continue
        data = getattr(entry, "data", None)
        if not isinstance(data, Mapping):
            continue
        job_id = data.get("job_id")
        kind = data.get("fact")
        if not isinstance(job_id, str) or not job_id or kind not in FACT_KINDS:
            continue
        if kind == "start":
            job = jobs.setdefault(job_id, FoldedJob(job_id))
            if job.start is not None or job.unreadable:
                continue
            if data.get("v") != FACT_VERSION or not _valid_start(data):
                job.unreadable = True
            else:
                job.start = dict(data)
            continue
        job = jobs.get(job_id)
        if job is None or job.start is None:
            continue  # before its start, or of a job whose start is unreadable
        if data.get("v") != FACT_VERSION:
            continue
        if not _valid(kind, data):
            if kind == "outcome":
                job.conflicts += 1
                _log.warning("job %s: an outcome with status %r is ignored", job_id, data.get("status"))
            continue
        record = dict(data)
        if kind == "process":
            seq = record.get("seq")
            if isinstance(seq, int) and seq not in job.processes:
                job.processes[seq] = record
        elif kind == "mode":
            job.mode.append(record)
        elif kind == "cleanup":
            job.cleanup = record
        elif kind == "supervision":
            job.supervision.append(record)
        elif kind == "outcome":
            if job.outcome is None:
                job.outcome = record
            elif job.outcome.get("status") != record.get("status"):
                job.conflicts += 1
                _log.warning(
                    "job %s: a second outcome (%s) after %s is ignored",
                    job_id,
                    record.get("status"),
                    job.outcome.get("status"),
                )
    return {job_id: job for job_id, job in jobs.items() if job.start is not None or job.unreadable}


def client_records_of(
    entries: list[Any], job_id: str, custom_types: tuple[str, ...] = ()
) -> list[Mapping[str, Any]]:
    """A kind's own records that carry ``job_id`` (any custom type but the job facts)."""

    found: list[Mapping[str, Any]] = []
    for entry in entries:
        if getattr(entry, "type", None) != "custom":
            continue
        custom_type = getattr(entry, "custom_type", "")
        if custom_type == JOB_FACT_TYPE or (custom_types and custom_type not in custom_types):
            continue
        data = getattr(entry, "data", None)
        if isinstance(data, Mapping) and data.get("job_id") == job_id:
            found.append({"custom_type": custom_type, **data})
    return found


__all__ = [
    "FACT_VERSION",
    "JOB_FACT_TYPE",
    "NOTICE_CUSTOM_TYPE",
    "OUTCOME_STATUSES",
    "RECORD_ENTRY_PREFIX",
    "FoldedJob",
    "RecordWriteFailed",
    "append_fact",
    "append_record",
    "client_records_of",
    "delivered",
    "fold",
    "make_fact",
    "record_entry_id",
]
