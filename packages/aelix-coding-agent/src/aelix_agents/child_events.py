"""What a host sees of a build's delegated children, live and replayed (#612, ADR-0307).

One :class:`ChildEventHub` per harness build, made by the agents extension with
the build's runtime and handed once to every background run the build starts
(``jobs_client._Launch.hub``), so a spawned job's row feeds the same hub as a
foreground one. Its :attr:`~ChildEventHub.epoch` (a uuid) is the build's
generation: a ``/reload`` or a session change builds a new harness and a new
hub, and replay never crosses one (owner, 2026-11-18: "같은 프로세스 + 영속 기록
(권장)").

FED FROM TWO TAPS, BOTH IN ``runtime._publish`` — the sole producer of a child's
progress. The snapshot gives the row's fields (its state, text, stuck mark,
wrap-up mark); the reducer's transition list (``_StreamState.transitions``)
gives the tool calls, as immutable records taken before the display window can
trim them. The hub never diffs ``state.recent``, whose ``ToolCall`` objects are
mutable and trimmed (contract r5 §4.2).

WHAT A HOST IS PROMISED:

* every frame carries ``id``, ``seq`` (per child, from 1) and ``epoch``;
  ``(epoch, id, seq)`` is the key a host merges and deduplicates by;
* a frame is emitted only on a change, and the identity frame (``state``) at
  once — at the first snapshot, at the receipt that names the child session,
  at a Ctrl+B move that names its job — without waiting for the child to speak;
* every emitted ``tool_start`` has exactly one ``tool_end``: its own, or a
  synthetic ``no_result`` when its turn or its run ended without one; a start
  the hub cannot track (no call id, or 1024 calls already open) is not emitted;
* every projected string is control-stripped to one line and capped (120
  characters for a name, an argument, a model; 512 for a text, a reason, a note;
  32 for a stuck mark); a call id goes through :func:`wire_call_id`, which bounds
  it as the wire writes it (JSON-escaped); ``tokens`` and ``elapsedMs`` are
  integers in ``[0, 2**53 - 1]`` and ``cost`` a finite number (fix round 1);
* a child keeps a ring of its latest 256 frames; an ended child keeps it until
  64 ended children accumulate, then leaves a tombstone (at most 1024).

Synchronous throughout: :meth:`ChildEventHub.publish` appends to a ring and
calls the subscribers in one step, and :meth:`ChildEventHub.events` snapshots a
ring in one step, so on a wire written in emit order every frame with a ``seq``
below an answer's ``nextSeq`` precedes the answer (contract r5 §4.6).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sys
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aelix_agents.stream import _strip_control

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from aelix_coding_agent.subagent_contract import SubagentProgress

    from aelix_agents.print_channel import RunningChild
    from aelix_agents.stream import Transition, _StreamState

logger = logging.getLogger(__name__)

RING_MAX = 256
"""Frames kept per child (ADR-0278 D3's window): a host that missed more resyncs
from ``children/list``."""

ENDED_KEPT = 64
"""Ended children whose ring is kept; past it the oldest leaves a tombstone."""

TOMBSTONES_KEPT = 1024
"""Tombstones kept; past it the oldest is forgotten (``no agent … in this session``)."""

OPEN_CALLS_MAX = 1024
"""Tool calls tracked open per child. A start past it is not emitted, so no
start is ever left without its end."""

NAME_CHARS = 120
"""Cap on a tool name, a tool argument, a model, a provider, a profile and the
current tool (the reducer's ``MAX_TRAIL_ARG_CHARS``)."""

TEXT_CHARS = 512
"""Cap on a text, a reason and a note (the reducer's ``LAST_TEXT_MAX_CHARS``)."""

STUCK_CHARS = 32
"""Cap on a stuck mark."""

WIRE_ID_BYTES = 128
"""A call id longer than this as the wire writes it — JSON-escaped, UTF-8, without
its quotes — is sent hashed (:func:`wire_call_id`)."""

SAFE_INTEGER = 2**53 - 1
"""The largest count a row or frame carries (``tokens``, ``elapsedMs``): JSON's
safe integer, so a host reading numbers as doubles reads it exactly, and its
rendering stays 16 digits (fix round 1, Codex C-M6 measured a 4,001-digit one)."""

HASHED_PREFIX = "h:"
"""The namespace of a hashed id. A raw id that starts with it is hashed too, so
a literal id can never equal another id's hashed rendering."""

_TERMINAL = frozenset({"done", "error", "stopped"})
_LONE_SURROGATE = re.compile(r"[\ud800-\udfff]")


def wire_call_id(raw: str) -> str:
    """A call id as a host sees it: ``raw`` when it is at most 128 bytes as the
    wire writes it (``json.dumps(..., ensure_ascii=False)`` without its quotes,
    UTF-8: a control character escapes to six bytes, a quote or backslash to
    two), encodable and outside the ``h:`` namespace, else ``"h:"`` and the first
    32 hex digits of its sha256 (#612, contract r5 §4.4; fix round 1, Codex C-M6:
    128 NULs were 768 bytes on the wire). The same function serves the hub, the
    approval router and aelix-acp's nested id."""

    short = False
    if len(raw) <= WIRE_ID_BYTES:  # each character is at least one byte
        try:
            short = len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) - 2 <= WIRE_ID_BYTES
        except UnicodeEncodeError:
            short = False
    if short and not raw.startswith(HASHED_PREFIX):
        return raw
    digest = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
    return HASHED_PREFIX + digest[:32]


def cap(text: Any, limit: int) -> str | None:
    """``text`` on one line, control characters stripped, lone surrogates replaced,
    at most ``limit`` characters (the last one ``…`` when cut). ``None`` for a
    value that is not a string.

    The work is bounded before the strip, as ``stream._one_line``'s is: a
    3 MiB tool name costs a slice, not a 3 MiB scan."""

    if not isinstance(text, str):
        return None
    sliced = len(text) > limit * 4
    flat = _LONE_SURROGATE.sub("\ufffd", _strip_control(text[: limit * 4]))
    if sliced or len(flat) > limit:
        return flat[: limit - 1] + "…"
    return flat


def _id_or_none(raw: Any) -> str | None:
    return wire_call_id(raw) if isinstance(raw, str) and raw else None


def _count(value: Any) -> int:
    """A count as a row carries it: an integer in ``[0, SAFE_INTEGER]``; anything else ``0``."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    if isinstance(value, float):
        if math.isnan(value):
            return 0
        if math.isinf(value):
            return SAFE_INTEGER if value > 0 else 0
    return min(max(int(value), 0), SAFE_INTEGER)


def _amount(value: Any) -> float:
    """A cost as a row carries it: a finite number, never negative; an overflow is the largest finite one.

    ``Infinity`` and ``NaN`` are not JSON, and strict readers reject the line
    (fix round 1, Codex C-M6: two ``1e308`` costs summed to ``inf``).
    """

    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    try:
        number = float(value)
    except OverflowError:  # an int too large for a float
        number = math.inf
    if math.isnan(number) or number <= 0:
        return 0.0
    return min(number, sys.float_info.max)


def job_id_of(child: RunningChild) -> str | None:
    """The job a row runs as: a spawned run's owner's, or the one a Ctrl+B move made."""

    for holder in (child.owner, child.adopted):
        job_id = getattr(holder, "job_id", None)
        if isinstance(job_id, str) and job_id:
            return job_id
    return None


@dataclass
class _Row:
    """One child as the hub keeps it: the last projection, its ring, its open calls."""

    id: str
    started_at: float
    profile: str = ""
    state: str = "starting"
    end: str | None = None
    outcome: str | None = None
    reason: str | None = None
    note: str | None = None
    current_tool: str | None = None
    elapsed_ms: int = 0
    tokens: int = 0
    cost: float = 0.0
    model: str | None = None
    provider: str | None = None
    activity: list[dict[str, Any]] = field(default_factory=list)
    last_text: str | None = None
    stuck: str | None = None
    wrapping_up: bool = False
    approval: dict[str, Any] | None = None
    session_id: str | None = None
    job_id: str | None = None
    parent_tool_call_id: str | None = None
    ring: deque[tuple[int, str, dict[str, Any]]] = field(
        default_factory=lambda: deque(maxlen=RING_MAX)
    )
    next_seq: int = 1
    open_calls: OrderedDict[str, int] = field(default_factory=OrderedDict)
    """Open calls by their wire id (:func:`wire_call_id`), each with the turn it started in."""
    identity: tuple[Any, ...] | None = None
    ended: bool = False
    runtime: Any = None
    child: Any = None

    @property
    def low_seq(self) -> int:
        return self.ring[0][0] if self.ring else self.next_seq

    def identity_now(self) -> tuple[Any, ...]:
        return (self.state, self.session_id, self.job_id, self.parent_tool_call_id)

    def state_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "state": self.state,
            "profile": self.profile,
            "sessionId": self.session_id,
            "jobId": self.job_id,
            "parentToolCallId": self.parent_tool_call_id,
            "background": self.job_id is not None,
            "wrappingUp": self.wrapping_up,
        }
        if self.end is not None:
            fields.update(end=self.end, outcome=self.outcome, reason=self.reason, note=self.note)
        return fields


class ChildEventHub:
    """The children of one harness build, as frames, rows and rings. See the module docstring."""

    def __init__(self, *, epoch: str | None = None) -> None:
        self.epoch = epoch or uuid.uuid4().hex
        self._rows: dict[str, _Row] = {}
        self._ended: deque[str] = deque()
        self._tombstones: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._subscribers: list[Callable[[str, dict[str, Any]], None]] = []

    # === feeding ===============================================================

    def publish(
        self,
        runtime: Any,
        child: RunningChild,
        progress: SubagentProgress | None,
        transitions: Sequence[Transition] = (),
        *,
        terminal: dict[str, Any] | None = None,
    ) -> None:
        """Fold one snapshot of ``child`` and the transitions drained with it; emit what changed.

        ``terminal`` — the row's final publish — carries ``end``, ``outcome``,
        ``reason`` and ``note``: every call still open is closed ``no_result``,
        the terminal ``state`` frame is the last of the row's frames, and the row
        is kept as ended. Before it, a terminal ``child.state`` (a stop marks the
        row while its run unwinds) is held back, so a host sees exactly one
        terminal frame, the one with its end. ``progress`` is ``None`` for an
        identity-only publish (the receipt, a Ctrl+B move).
        """

        row = self._rows.get(child.id)
        first = row is None
        if row is None:
            if child.id in self._tombstones:
                return
            row = self._rows[child.id] = _Row(id=child.id, started_at=child.started_at)
        if row.ended:
            return
        row.runtime, row.child = runtime, child
        frames: list[tuple[str, dict[str, Any]]] = []
        self._take(row, child, progress, final=terminal is not None)
        if terminal is None and (first or row.identity_now() != row.identity):
            row.identity = row.identity_now()
            frames.append(("state", row.state_fields()))
        self._tools(row, child.stream, transitions, frames)
        if progress is not None:
            self._marks(row, progress, frames)
        if terminal is not None:
            self._close_open(row, None, frames)
            row.end, row.outcome = terminal.get("end"), terminal.get("outcome")
            row.reason = cap(terminal.get("reason"), TEXT_CHARS)
            row.note = cap(terminal.get("note"), TEXT_CHARS)
            row.identity = row.identity_now()
            frames.append(("state", row.state_fields()))
            self._retire(row)
        for event, fields in frames:
            self._emit(row, event, fields)

    def approval(self, child_id: str, call_id: str, tool_name: str, step: str) -> None:
        """A child's approval request opened (``asked``) or ended (#612, ADR-0308): one ``approval`` frame.

        ``asked`` puts ``{callId, toolName}`` on the row; any other step clears it.
        A child the hub does not hold live is ignored.
        """

        row = self._rows.get(child_id)
        if row is None or row.ended:
            return
        open_request = {
            "callId": wire_call_id(call_id),
            "toolName": cap(tool_name, NAME_CHARS) or "",
        }
        row.approval = open_request if step == "asked" else None
        self._emit(row, "approval", {**open_request, "step": step})

    def _take(
        self, row: _Row, child: RunningChild, progress: SubagentProgress | None, *, final: bool
    ) -> None:
        """The row's fields from ``child`` and ``progress``; a terminal state only at the final publish."""

        if child.state not in _TERMINAL or final:
            row.state = child.state
        row.profile = cap(child.profile, NAME_CHARS) or ""
        row.session_id = child.session_id
        row.job_id = job_id_of(child)
        row.parent_tool_call_id = _id_or_none(child.parent_tool_call_id)
        if progress is None:
            return
        row.current_tool = cap(progress.current_tool, NAME_CHARS)
        row.elapsed_ms, row.tokens = _count(progress.elapsed_ms), _count(progress.tokens)
        row.cost = _amount(progress.cost)
        row.model = cap(progress.model, NAME_CHARS)
        row.provider = cap(progress.provider, NAME_CHARS)
        row.activity = [
            {
                "name": cap(call.name, NAME_CHARS) or "",
                "args": cap(call.args, NAME_CHARS) or "",
                "status": call.status,
            }
            for call in progress.activity[:4]
        ]

    def _marks(self, row: _Row, progress: SubagentProgress, frames: list[tuple[str, dict[str, Any]]]) -> None:
        """``text``, ``stuck`` and ``wrap_up`` frames, each on a change."""

        text = cap(progress.last_text, TEXT_CHARS)
        if text is not None and text != row.last_text:
            row.last_text = text
            frames.append(("text", {"text": text}))
        stuck = cap(progress.stuck, STUCK_CHARS)
        if stuck != row.stuck:
            row.stuck = stuck
            frames.append(("stuck", {"stuck": stuck}))
        wrapping = bool(progress.wrapping_up)
        if wrapping != row.wrapping_up:
            row.wrapping_up = wrapping
            frames.append(("wrap_up", {"requested": wrapping}))

    def _tools(
        self,
        row: _Row,
        state: _StreamState,
        transitions: Sequence[Transition],
        frames: list[tuple[str, dict[str, Any]]],
    ) -> None:
        """``tool_start`` / ``tool_end`` from the transitions; ``no_result`` for what a turn or run left open."""

        for item in transitions:
            if item[0] == "start":
                _kind, call_id, name, args, turn = item
                self._close_open(row, turn, frames)
                # Keyed by the bounded wire id, never the raw one (fix round 1, Codex
                # C-M7: 96 half-MiB ids kept 50 MB alive until their calls closed).
                wire_id = _id_or_none(call_id)
                if wire_id is None or wire_id in row.open_calls or len(row.open_calls) >= OPEN_CALLS_MAX:
                    continue
                row.open_calls[wire_id] = turn
                frames.append(
                    (
                        "tool_start",
                        {
                            "callId": wire_id,
                            "name": cap(name, NAME_CHARS) or "",
                            "args": cap(args, NAME_CHARS) or "",
                        },
                    )
                )
            else:
                wire_id = _id_or_none(item[1])
                if wire_id is not None and wire_id in row.open_calls:
                    del row.open_calls[wire_id]
                    frames.append(("tool_end", {"callId": wire_id, "status": item[2]}))
        self._close_open(row, state.turn_starts, frames)
        if state.saw_agent_end and not state.run_open:
            self._close_open(row, None, frames)

    @staticmethod
    def _close_open(row: _Row, turn: int | None, frames: list[tuple[str, dict[str, Any]]]) -> None:
        """Close, ``no_result`` and in start order, every open call of a turn before ``turn`` (``None``: all)."""

        for wire_id, opened in list(row.open_calls.items()):
            if turn is None or opened < turn:
                del row.open_calls[wire_id]
                frames.append(("tool_end", {"callId": wire_id, "status": "no_result"}))

    def _retire(self, row: _Row) -> None:
        """Keep ``row`` as ended; past :data:`ENDED_KEPT` the oldest ended row becomes a tombstone."""

        row.ended = True
        row.runtime = row.child = None
        row.approval = None
        self._ended.append(row.id)
        while len(self._ended) > ENDED_KEPT:
            dropped = self._rows.pop(self._ended.popleft(), None)
            if dropped is None:
                continue
            self._tombstones[dropped.id] = {
                "id": dropped.id,
                "state": dropped.state,
                "end": dropped.end,
                "sessionId": dropped.session_id,
                "jobId": dropped.job_id,
            }
            while len(self._tombstones) > TOMBSTONES_KEPT:
                self._tombstones.popitem(last=False)

    def _emit(self, row: _Row, event: str, fields: dict[str, Any]) -> None:
        seq = row.next_seq
        row.next_seq += 1
        data = {"id": row.id, "seq": seq, "epoch": self.epoch, **fields}
        row.ring.append((seq, event, data))
        for callback in list(self._subscribers):
            try:
                callback(event, data)
            except Exception:  # noqa: BLE001 — a subscriber never breaks a delegation
                logger.debug("a child-event subscriber raised", exc_info=True)

    # === reading ===============================================================

    def subscribe(self, callback: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
        """Call ``callback(event, data)`` for every frame from now on; returns the unsubscribe.

        Synchronous, from the publishing task. ``data`` is the ring's own: read it,
        never change it."""

        self._subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def rows(self) -> list[dict[str, Any]]:
        """``children/list``'s rows: every live child and every ended one still kept."""

        now = time.monotonic()
        return [self._row(row, now) for row in self._rows.values()]

    @staticmethod
    def _row(row: _Row, now: float) -> dict[str, Any]:
        live = not row.ended
        return {
            "id": row.id,
            "profile": row.profile,
            "state": row.state,
            "end": row.end,
            "currentTool": row.current_tool,
            "elapsedMs": max(row.elapsed_ms, int((now - row.started_at) * 1000)) if live else row.elapsed_ms,
            "tokens": row.tokens,
            "cost": row.cost,
            "model": row.model,
            "provider": row.provider,
            "activity": [dict(call) for call in row.activity],
            "lastText": row.last_text,
            "stuck": row.stuck,
            "wrappingUp": row.wrapping_up,
            "approval": dict(row.approval) if row.approval is not None else None,
            "seq": row.next_seq - 1,
            "lowSeq": row.low_seq,
            "sessionId": row.session_id,
            "jobId": row.job_id,
            "parentToolCallId": row.parent_tool_call_id,
            "background": row.job_id is not None,
        }

    def events(self, child_id: str, after_seq: int | None) -> dict[str, Any]:
        """``children/events``: the kept frames after ``after_seq`` (contract r5 §4.6's cursor table).

        Raises :class:`ValueError` for a cursor ahead of the child's events, a
        negative one, a tombstoned child and one this hub never held.
        """

        row = self._rows.get(child_id)
        if row is None:
            raise ValueError(self._not_held(child_id))
        kept = list(row.ring)
        low, next_seq = row.low_seq, row.next_seq
        if after_seq is None:
            slice_, gap = kept, low > 1
        elif after_seq < 0:
            raise ValueError("afterSeq must not be negative")
        elif after_seq >= next_seq:
            raise ValueError(f"afterSeq is ahead of this agent's events (next is {next_seq})")
        elif after_seq < low - 1:
            slice_, gap = kept, True
        else:
            slice_, gap = [item for item in kept if item[0] > after_seq], False
        return {
            "epoch": self.epoch,
            "events": [{"event": event, "data": data} for _seq, event, data in slice_],
            "nextSeq": next_seq,
            "lowSeq": low,
            "gap": gap,
        }

    def live(self, child_id: str) -> tuple[Any, RunningChild] | None:
        """The runtime and row of a child that is still running, or ``None``."""

        row = self._rows.get(child_id)
        if row is None or row.ended or row.runtime is None or row.child is None:
            return None
        return row.runtime, row.child

    def knows(self, child_id: str) -> bool:
        """Whether this hub held ``child_id`` (live, ended, or as a tombstone)."""

        return child_id in self._rows or child_id in self._tombstones

    def state_of(self, child_id: str) -> str:
        """The state the hub holds for ``child_id``; :class:`ValueError` for one it never held."""

        row = self._rows.get(child_id)
        if row is not None:
            return row.state
        tombstone = self._tombstones.get(child_id)
        if tombstone is not None:
            return str(tombstone["state"])
        raise ValueError(self._not_held(child_id))

    def _not_held(self, child_id: str) -> str:
        tombstone = self._tombstones.get(child_id)
        if tombstone is not None:
            return f"agent {shown_id(child_id)}'s events are no longer kept (it ended {tombstone['end']})"
        return no_agent(child_id)


def shown_id(child_id: str) -> str:
    """An id as a sentence names it: quoted, one line, at most 64 characters."""

    return repr(cap(child_id, 64) or "")


def no_agent(child_id: str) -> str:
    """The refusal for an id no child of this build ever had (``no agent … in this session``)."""

    return f"no agent {shown_id(child_id)} in this session"


__all__ = [
    "ENDED_KEPT",
    "HASHED_PREFIX",
    "NAME_CHARS",
    "OPEN_CALLS_MAX",
    "RING_MAX",
    "SAFE_INTEGER",
    "STUCK_CHARS",
    "TEXT_CHARS",
    "TOMBSTONES_KEPT",
    "WIRE_ID_BYTES",
    "ChildEventHub",
    "cap",
    "job_id_of",
    "no_agent",
    "shown_id",
    "wire_call_id",
]
