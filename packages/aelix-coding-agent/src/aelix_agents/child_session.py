"""A delegated child's session file, and the parent's receipt for it — #199.

WHAT THIS MODULE OWNS: everything #199 writes, and nothing that decides whether a
delegation may run.

* WHERE a child's session lives (:func:`place_children`): beside its parent, in a
  directory named after the parent's file — ``<bucket>/<stem>.jsonl`` gets
  ``<bucket>/<stem>/<spawn id>.jsonl``. ``JsonlSessionRepo.list`` and
  ``find_most_recent`` read only the ``*.jsonl`` files directly inside a bucket
  and skip directories, so no picker, no ``--continue`` and no id-prefix lookup
  ever sees a child.
* the child file ITSELF (:meth:`SpawnReceipt.open`): published by the PARENT —
  a header and one ``aelix.child_origin`` record, in one atomic publish
  (ADR-0242 §4) — before the child process exists. The child is then launched
  with ``--session <that path>``, so even a child that dies before its first
  turn leaves a findable file, and the parent knows the path without asking.
* the PARENT's records: ``aelix.child_session`` (start, settle) and
  ``aelix.usage`` (pending, final), each one ``CustomEntry`` line with
  everything inside ``data`` (ADR-0242 rule 1).

``runtime._run`` is the only caller. It decides WHEN each record is written;
this module decides WHAT is written and where.

THE RECORDS, EXACTLY. ``key`` is the spawn id (``sub-<12 hex>``), the one
per-child identifier: a ``tool_call_id`` is one per ``agent`` call and is not
unique on the Google adapter across restarts. A reader folds each type by
``key``, last line wins.

* ``aelix.child_session`` start — ``{v, key, phase: "start", tool_call_id,
  index, mode, profile, task_preview, child, requested_model, permission_mode,
  aelix_version, error}``. ``child`` is ``{session_id, path, rel}`` or
  ``null``; ``error`` says why it is ``null`` when that is not simply "the
  parent has no session file" (:data:`PARENT_OUTSIDE_A_BUCKET`, a failed
  create), and is ``null`` otherwise.
* ``aelix.usage`` pending — ``{v, key, state: "pending"}``, written right after
  the start and BEFORE the child can spend anything.
* ``aelix.child_session`` settle — every start field except ``error``, then
  ``phase: "settle"``, ``status`` (``ok`` / ``error`` / ``timeout`` /
  ``aborted`` / ``cancelled``), ``model``, ``provider``, ``usage: {input,
  output, cache_read, cache_write, cost}``, ``cost_known``, ``context_tokens``,
  ``turns``, ``elapsed_ms``, ``exit_code``, ``stop_reason``, ``truncated``,
  ``summary_bytes``, ``details_bytes`` and ``error`` (the run's own, short).
  A COMPLETE record rather than the second half of the start (ADR-0242: the
  file can end between the two, and the prefix must still make sense).
* ``aelix.usage`` final — ``{v, key, state: "final", usage, cost_known}``, the
  same two values the settle carries.

A start with no settle is a delegation whose outcome is UNKNOWN (the process
died, the machine died); a pending with no final is spend that is NOT
CONFIRMED. Neither is a zero.

RECORDING NEVER CHANGES A DELEGATION. Every append is logged at DEBUG and
swallowed (:func:`append_records`), and every allocation failure becomes
``child: null`` and a ``--no-session`` child. The record builders are total
(:func:`_utf8_len`), and ``runtime._run`` still falls back to
:meth:`SpawnReceipt.bare_records` if one raises. The one thing that propagates
is ``CancelledError``, which is not a failure to record but the delegation
ending.
"""

from __future__ import annotations

import logging
import math
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from aelix_agent_core.session.entries import CustomEntry
from aelix_agent_core.session.fs import LocalFileSystem
from aelix_agent_core.session.jsonl_storage import JsonlSessionStorage
from aelix_coding_agent.cli.config import VERSION

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aelix_coding_agent.subagent_contract import SubagentResult

    from aelix_agents.stream import _StreamState

logger = logging.getLogger(__name__)

CHILD_SESSION_TYPE = "aelix.child_session"
"""The parent's receipt for one delegated child: a start and a settle."""

USAGE_TYPE = "aelix.usage"
"""The parent's usage ledger line for one child: a pending and a final.

Named for USAGE, not for delegation, on purpose: it is the one record type the
kernel's session stats read (``harness/_session_stats.py``), and the kernel may
not learn delegation vocabulary."""

CHILD_ORIGIN_TYPE = "aelix.child_origin"
"""The first entry of every child session file — who spawned it, and from where.

``cli/entry.py`` spells the same string to warn a human who opens a child file
with ``--session`` (it may not import this module). Pinned equal by a test."""

RECORD_VERSION = 1
"""``v`` on every record this module writes. A reader ignores a version it does
not know, as it ignores a ``customType`` it does not know (ADR-0242 rule 1.6)."""

TASK_PREVIEW_CHARS = 200
"""How much of the task the start record carries. The whole task is in the child
file (its first user message); this keeps the parent's record self-describing
without copying a long prompt into it."""

ERROR_PREVIEW_CHARS = 300
"""How much of an error a record carries. A record states WHAT went wrong; the
stderr tail and the full message stay with the envelope and the child file."""

CHILDREN_SUFFIX = ".children"
"""Appended to a parent file name that does not end in ``.jsonl``, so the child
directory can never share a name with the parent file itself."""

PARENT_OUTSIDE_A_BUCKET = "parent outside a bucket"
"""The start record's ``error`` when the parent file does not sit inside a
session bucket. See :func:`place_children` for why that refuses a child file."""

RECORD_BUILD_FAILED = "the record could not be built"
"""The settle's ``error`` when :meth:`SpawnReceipt.bare_records` stood in for a
builder that raised."""

_SESSION_SUFFIX = ".jsonl"


def _iso_now() -> str:
    """The session store's own timestamp spelling (``jsonl_storage._iso_now``)."""

    return (
        datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def _short(text: str | None) -> str | None:
    """``text`` cut to :data:`ERROR_PREVIEW_CHARS`, or ``None`` when empty."""

    if not text:
        return None
    stripped = text.strip()
    return stripped[:ERROR_PREVIEW_CHARS] or None


def _utf8_len(text: str | None) -> int:
    """The UTF-8 size of ``text``, and it never raises.

    A lone surrogate is a legal ``str``, and a child's stream can carry one:
    the child writes JSON with ``ensure_ascii`` on, so ``"\\ud83d"`` comes back
    into :attr:`_StreamState.summary` as that code point. A bare
    ``encode("utf-8")`` raised ``UnicodeEncodeError`` from ``_run``'s
    ``finally``, which turned a Ctrl+C into that error and lost the settle.
    ``surrogatepass`` counts the code point as the three bytes it takes.
    """

    if not isinstance(text, str) or not text:
        return 0
    return len(text.encode("utf-8", "surrogatepass"))


def _is_bucket(name: str) -> bool:
    """Does a directory NAME have the shape ``JsonlSessionRepo`` gives a bucket?

    ``_encode_cwd`` always wraps the encoded cwd in ``--…--`` (``--``, the path
    with its separators turned into ``-``, ``--``), and it is the only thing
    that creates the directories a sessions root holds.
    """

    return len(name) >= 4 and name.startswith("--") and name.endswith("--")


@dataclass(frozen=True)
class Placement:
    """Where one parent's children go."""

    directory: str
    """ABSOLUTE path of the children's directory."""
    rel_dir: str
    """Its name, which is its path relative to the parent file's directory."""


def place_children(parent_path: str) -> Placement | None:
    """The directory a parent's children live in, or ``None`` to refuse one.

    ``<dir>/<stem>.jsonl`` → ``<dir>/<stem>``. Only an exact ``.jsonl`` suffix
    is stripped; any other name gets :data:`CHILDREN_SUFFIX` appended instead,
    so the directory can never be the parent file itself.

    RESOLVED AGAINST THE PARENT'S CWD, HERE. A parent opened with a relative
    ``--session sessions/…/x.jsonl`` holds a relative path, and a child that
    runs in a subdirectory would resolve the same string against its own cwd
    and find nothing (measured in the design critique: ``Failed to read session
    header``, exit 1). So the directory is derived from ``os.path.abspath`` in
    the parent, and every path this module writes is absolute.

    REFUSED OUTSIDE A BUCKET. A parent sitting directly in a sessions root would
    turn ``<stem>/`` into a new top-level bucket, which the global ``list()``
    scans — every child file would then feed ``--session <id prefix>`` and
    ``--resume`` lookups. The sessions root itself is not visible from here
    (``--session-dir`` is not), so the test is the conservative one: the
    parent's own directory must have a bucket's shape (:func:`_is_bucket`). A
    parent the repo created always passes; a hand-placed parent anywhere else
    gets no child file, and the start record says
    :data:`PARENT_OUTSIDE_A_BUCKET` — never a directory a picker might scan.
    """

    absolute = os.path.abspath(parent_path)
    parent_dir, name = os.path.split(absolute)
    if not _is_bucket(os.path.basename(parent_dir)):
        return None
    if name.endswith(_SESSION_SUFFIX) and len(name) > len(_SESSION_SUFFIX):
        stem = name[: -len(_SESSION_SUFFIX)]
    else:
        stem = name + CHILDREN_SUFFIX
    return Placement(directory=os.path.join(parent_dir, stem), rel_dir=stem)


@dataclass(frozen=True)
class ChildSessionFile:
    """The child session file one spawn was given."""

    session_id: str
    path: str
    """ABSOLUTE — see :func:`place_children`."""
    rel: str
    """``<stem>/<spawn id>.jsonl``, relative to the PARENT file's directory and
    always ``/``-separated. A reader tries this first and :attr:`path` second,
    so a parent that moved together with its ``<stem>/`` directory still finds
    its children (#199 design §A.7)."""

    def as_record(self) -> dict[str, str]:
        return {"session_id": self.session_id, "path": self.path, "rel": self.rel}


def usage_record(
    *, input: int, output: int, cache_read: int, cache_write: int, cost: float
) -> dict[str, Any]:
    """The ``usage`` object a settle and a final carry — the FLOWS only.

    ``SubagentUsage.tokens`` is a context LEVEL (the last message's whole
    context) and is deliberately not here; it rides the settle as
    ``context_tokens``, where nothing sums it. Counters are ints and ``cost`` a
    finite float, because ADR-0242 rule 1.7 leaves JSON hygiene to the writer.
    """

    return {
        "input": _count(input),
        "output": _count(output),
        "cache_read": _count(cache_read),
        "cache_write": _count(cache_write),
        "cost": _money(cost),
    }


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def _money(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


#: Outcomes in which the PARENT cut the run: Ctrl+C (``cancelled``), a
#: ``stop``/``stop_all`` (``aborted``) or the delegation's own deadline
#: (``timeout``). The parent stops reading the child's stream at once there.
CUT_BY_THE_PARENT = frozenset({"cancelled", "aborted", "timeout"})


def usage_is_complete(state: _StreamState, *, status: str | None = None) -> bool:
    """Did every model call the child made reach the parent with its usage?

    A child that never began a run (no ``agent_start``, which precedes the first
    model request) spent nothing, so its zeros are complete. Otherwise:

    * **the parent cut the run** (``status`` in :data:`CUT_BY_THE_PARENT`) —
      never complete. The parent stopped reading, and a request in flight then
      may have been billed without its ``message_end`` arriving. Not even an
      ``agent_end`` proves otherwise: the child's own abort path emits one
      without a ``message_end`` for the request it abandoned (Codex review).
    * **anything else** — complete only when the child's LATEST run closed on its
      own (``run_open`` false). ``run_open`` is last-event-wins, so a stream cut
      during an auto-retry, which is a second run after an ``agent_end``, still
      reads as unfinished; a child that died mid-turn leaves it open too.

    Before this check a Ctrl+C before any usage arrived recorded zeros as a
    confirmed "nothing spent" (Codex review of #199).
    """

    if not state.saw_agent_start:
        return True
    if status in CUT_BY_THE_PARENT:
        return False
    return not state.run_open


def cost_is_known(
    state: _StreamState,
    *,
    usage: dict[str, Any],
    cost: float,
    status: str | None = None,
) -> bool:
    """A.3b: is the recorded ``cost`` the whole bill, or only a floor?

    Never known while the usage itself is incomplete
    (:func:`usage_is_complete`, given the run's ``status``).
    Otherwise known when ANY of: a ``message_end`` carried a cost of its own
    (:attr:`_StreamState.cost_reported`); the registry fallback priced the run
    (:attr:`_StreamState.cost_priced`); a positive cost exists at all — which in
    the shipped channels can only have come from one of the first two, and is
    stated so an injected channel that reports a cost is read the same way; or
    every token counter is zero, i.e. nothing was spent.

    Never known when ``cost`` itself is not a finite, non-negative number: a
    wrong bill is worse than an absent one (``SessionStats.cost_known``).
    """

    if not usage_is_complete(state, status=status):
        return False
    if isinstance(cost, bool) or not isinstance(cost, int | float):
        return False
    try:
        finite = math.isfinite(float(cost))
    except (OverflowError, ValueError):
        return False
    if not finite or cost < 0:
        return False
    if state.cost_reported or state.cost_priced or cost > 0:
        return True
    return not any(
        usage[key] for key in ("input", "output", "cache_read", "cache_write")
    )


@dataclass
class SpawnReceipt:
    """Everything one ADMITTED spawn records, and the session it records into.

    Built by ``runtime._run`` synchronously, right after the registry insert and
    the first progress publish, so it exists on every path a registry row
    exists on — the only spawns that get records at all. Refusals before the
    insert (drain, live cap, budget), consent declines and batch refusals spent
    nothing, and their tool-result text already persists them.

    MUTABLE, and filled in by :meth:`open`: the parent :attr:`session` is
    captured ONCE there and never re-read, so a settle that arrives after
    ``/new``, ``/resume`` or quit still lands in the session that ran the child.
    """

    key: str
    tool_call_id: str | None
    index: int | None
    mode: str | None
    profile: str
    task_preview: str
    requested_model: str | None
    permission_mode: str
    aelix_version: str = VERSION
    session: Any | None = None
    """The parent ``Session`` every record goes to, or ``None`` (nothing is
    recorded). A session with no file — ``--no-session``, in-memory — still
    takes the records; only its child runs ``--no-session``."""
    parent_session_id: str | None = None
    parent_path: str | None = None
    """ABSOLUTE path of the parent's file, or ``None`` when it has none."""
    child: ChildSessionFile | None = None
    child_error: str | None = None
    opening_attempted: int = 0
    """How many of the opening records (start, then pending) were ATTEMPTED.

    Counted one record at a time, after its append returns. A cancel that
    lands while one is being written leaves that one, and every one after it,
    to the ``finally``, which writes them ahead of the settle (at least once:
    a record the store had already taken lands twice, and a reader folds by
    key). Attempted, not confirmed: a storage error that swallowed one is
    logged, and re-trying it from the ``finally`` would only log it again."""

    # ── the parent, captured once ─────────────────────────────────

    async def open(self, session: Any | None, *, cwd: str) -> None:
        """Capture the parent, allocate the child file, write start + pending.

        ``cwd`` is the CHILD's working directory — it goes into the child's
        header, as ``repo.create`` would have written it.
        """

        self.session = session
        if session is not None:
            await self._describe_parent(session)
            await self._allocate(cwd=cwd)
        for record in self.opening_records():
            await append_records(self.session, [record])
            self.opening_attempted += 1

    async def _describe_parent(self, session: Any) -> None:
        try:
            raw_path = session.session_file
        except Exception:  # noqa: BLE001 — no path is a parent with no file
            raw_path = None
        if isinstance(raw_path, str) and raw_path:
            self.parent_path = os.path.abspath(raw_path)
        try:
            metadata = await session.get_metadata()
        except Exception:  # noqa: BLE001 — the id is descriptive, never required
            metadata = None
        session_id = getattr(metadata, "id", None)
        self.parent_session_id = session_id if isinstance(session_id, str) else None

    async def _allocate(self, *, cwd: str) -> None:
        """Publish the child's session file, or record why there is none.

        ONE atomic publish (``JsonlSessionStorage.create(entries=…)``, ADR-0242
        §4): the header and the origin appear together or not at all, 0600 in a
        0700 directory (``LocalFileSystem``). The header's ``parentSession``
        stays unset — that field is FORK lineage, and a delegated child is not a
        fork of its parent; the link is the origin record instead.

        EVERY failure is a result, never a raise: an ``OSError``, a path over
        Windows' ``MAX_PATH`` (the child name is the 22-character spawn id for
        exactly that reason), a name that is already taken. The child then runs
        ``--no-session`` and :attr:`child_error` says why.
        """

        if self.parent_path is None:
            return
        placement = place_children(self.parent_path)
        if placement is None:
            self.child_error = PARENT_OUTSIDE_A_BUCKET
            return
        name = f"{self.key}{_SESSION_SUFFIX}"
        path = os.path.join(placement.directory, name)
        session_id = str(uuid.uuid4())
        origin = CustomEntry(
            id=uuid.uuid4().hex[:8],
            # ``None``, and it has to be: the child's first ``get_branch()``
            # walks from its leaf to the root, and a parent id naming an entry
            # that is not in the file raises ``invalid_session`` before the
            # child's first turn.
            parent_id=None,
            timestamp=_iso_now(),
            custom_type=CHILD_ORIGIN_TYPE,
            data=self.origin_record(),
        )
        try:
            # ``os.replace`` would silently take over an existing file, so a
            # collision (two 48-bit spawn ids in one parent's directory) is a
            # refusal rather than a lost child record.
            if os.path.lexists(path):
                self.child_error = "a child session file with this name already exists"
                return
            await JsonlSessionStorage.create(
                LocalFileSystem(),
                path,
                cwd=cwd,
                session_id=session_id,
                entries=[origin],
            )
        except Exception as exc:  # noqa: BLE001 — allocation never fails a delegation
            self.child_error = _short(f"could not create the child session file: {exc}")
            return
        self.child = ChildSessionFile(
            session_id=session_id, path=path, rel=f"{placement.rel_dir}/{name}"
        )

    # ── the records ───────────────────────────────────────────────

    def origin_record(self) -> dict[str, Any]:
        """``aelix.child_origin`` — the child file's first entry."""

        return {
            "v": RECORD_VERSION,
            "key": self.key,
            "parent": {"session_id": self.parent_session_id, "path": self.parent_path},
            "tool_call_id": self.tool_call_id,
            "index": self.index,
            "mode": self.mode,
            "profile": self.profile,
            "permission_mode": self.permission_mode,
            "aelix_version": self.aelix_version,
        }

    def _identity(self, phase: str) -> dict[str, Any]:
        return {
            "v": RECORD_VERSION,
            "key": self.key,
            "phase": phase,
            "tool_call_id": self.tool_call_id,
            "index": self.index,
            "mode": self.mode,
            "profile": self.profile,
            "task_preview": self.task_preview,
            "child": self.child.as_record() if self.child is not None else None,
            "requested_model": self.requested_model,
            "permission_mode": self.permission_mode,
            "aelix_version": self.aelix_version,
        }

    def start_record(self) -> dict[str, Any]:
        return {**self._identity("start"), "error": self.child_error}

    def pending_record(self) -> dict[str, Any]:
        return {"v": RECORD_VERSION, "key": self.key, "state": "pending"}

    def opening_records(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (CHILD_SESSION_TYPE, self.start_record()),
            (USAGE_TYPE, self.pending_record()),
        ]

    def _unwritten_opening(self) -> list[tuple[str, dict[str, Any]]]:
        """The opening records :meth:`open` never got as far as attempting."""

        return self.opening_records()[self.opening_attempted :]

    def _closing(
        self, settle: dict[str, Any], usage: dict[str, Any], cost_known: bool
    ) -> list[tuple[str, dict[str, Any]]]:
        final = {
            "v": RECORD_VERSION,
            "key": self.key,
            "state": "final",
            "usage": usage,
            "cost_known": cost_known,
        }
        return [(CHILD_SESSION_TYPE, settle), (USAGE_TYPE, final)]

    def outcome_records(
        self, result: SubagentResult, *, state: _StreamState
    ) -> list[tuple[str, dict[str, Any]]]:
        """Settle + final for a delegation that returned an envelope.

        The envelope is what ACTUALLY happened — its ``model`` and ``provider``
        are what the child ran, not what was asked for — and the stream state
        is the evidence behind ``cost_known`` (:func:`cost_is_known`).
        """

        spend = result.usage
        usage = usage_record(
            input=spend.input,
            output=spend.output,
            cache_read=spend.cache_read,
            cache_write=spend.cache_write,
            cost=spend.cost,
        )
        known = cost_is_known(state, usage=usage, cost=spend.cost, status=result.status)
        settle = {
            **self._identity("settle"),
            "status": result.status,
            "model": result.model,
            "provider": result.provider,
            "usage": usage,
            "cost_known": known,
            "context_tokens": _count(spend.tokens),
            "turns": _count(spend.turns),
            "elapsed_ms": _count(result.elapsed_ms),
            "exit_code": result.exit_code,
            "stop_reason": result.stop_reason,
            "truncated": bool(result.truncated),
            "summary_bytes": _utf8_len(result.summary),
            "details_bytes": _utf8_len(result.details),
            "error": _short(result.error),
        }
        return self._closing(settle, usage, known)

    def unsettled_records(
        self,
        state: _StreamState,
        *,
        status: str,
        error: str | None,
        elapsed_ms: int,
        exit_code: int | None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """What the ``finally`` writes for a delegation that got no envelope.

        A cancel (``status="cancelled"``) or an exception out of the channel
        (``"error"``): the only evidence is the live stream state the channel
        was folding into, so that is what is recorded — partial usage included,
        which the caller has priced first. ``summary_bytes`` is 0 because no
        result reached the parent; ``details_bytes`` is the partial answer the
        child had streamed. Whatever of the start and the pending :meth:`open`
        never got as far as attempting comes first.
        """

        usage = usage_record(
            input=state.input,
            output=state.output,
            cache_read=state.cache_read,
            cache_write=state.cache_write,
            cost=state.cost,
        )
        known = cost_is_known(state, usage=usage, cost=state.cost, status=status)
        settle = {
            **self._identity("settle"),
            "status": status,
            "model": state.model,
            "provider": state.provider,
            "usage": usage,
            "cost_known": known,
            "context_tokens": _count(state.tokens),
            "turns": _count(state.turns),
            "elapsed_ms": _count(elapsed_ms),
            "exit_code": exit_code if isinstance(exit_code, int) else None,
            "stop_reason": state.stop_reason,
            "truncated": False,
            "summary_bytes": 0,
            "details_bytes": _utf8_len(state.summary),
            "error": _short(error),
        }
        return [*self._unwritten_opening(), *self._closing(settle, usage, known)]

    def bare_records(
        self, spend: Any, *, status: str
    ) -> list[tuple[str, dict[str, Any]]]:
        """Settle + final for when building the real ones RAISED.

        The fallback that keeps "recording never changes a delegation" true
        whatever a builder does: the identity, the status and the flows, read
        from ``spend`` (the envelope's usage or the live stream) through the
        total :func:`usage_record`; every other settle field is ``null``. And
        ``cost_known`` is ``false``: a record that could not be built is no
        evidence that its cost is the whole bill.
        """

        usage = usage_record(
            input=getattr(spend, "input", 0),
            output=getattr(spend, "output", 0),
            cache_read=getattr(spend, "cache_read", 0),
            cache_write=getattr(spend, "cache_write", 0),
            cost=getattr(spend, "cost", 0.0),
        )
        settle = {
            **self._identity("settle"),
            "status": status,
            "model": None,
            "provider": None,
            "usage": usage,
            "cost_known": False,
            "context_tokens": None,
            "turns": None,
            "elapsed_ms": None,
            "exit_code": None,
            "stop_reason": None,
            "truncated": None,
            "summary_bytes": None,
            "details_bytes": None,
            "error": RECORD_BUILD_FAILED,
        }
        return [*self._unwritten_opening(), *self._closing(settle, usage, False)]


async def append_records(
    session: Any | None, records: Sequence[tuple[str, dict[str, Any]]]
) -> None:
    """Append each record as one ``CustomEntry`` line. Logs and swallows failure.

    ``Session.append_custom_entry`` is awaited directly rather than through
    ``ExtensionAPI.append_entry``, which is fire-and-forget (its order against
    the tool result is not fixed) and raises when no session is attached.

    NO LOCK. ``JsonlSessionStorage`` already serialises its appends and
    ``LocalFileSystem`` never yields inside one, so a record lands whole and in
    order. A lock would add a suspension point on the cancel path, which is
    where a second Ctrl+C measurably lost both settles of a batch (design
    critique, probe P.3 case C).

    One record's failure does not stop the next: a settle that could not be
    written is still worth a final.
    """

    if session is None:
        return
    for custom_type, data in records:
        try:
            await session.append_custom_entry(custom_type, data)
        except Exception:  # noqa: BLE001 — recording never changes a delegation
            logger.debug(
                "could not record %s for %s", custom_type, data.get("key"), exc_info=True
            )


__all__ = [
    "CHILDREN_SUFFIX",
    "CHILD_ORIGIN_TYPE",
    "CHILD_SESSION_TYPE",
    "ERROR_PREVIEW_CHARS",
    "PARENT_OUTSIDE_A_BUCKET",
    "RECORD_BUILD_FAILED",
    "RECORD_VERSION",
    "TASK_PREVIEW_CHARS",
    "USAGE_TYPE",
    "ChildSessionFile",
    "Placement",
    "SpawnReceipt",
    "append_records",
    "CUT_BY_THE_PARENT",
    "cost_is_known",
    "usage_is_complete",
    "place_children",
    "usage_record",
]
