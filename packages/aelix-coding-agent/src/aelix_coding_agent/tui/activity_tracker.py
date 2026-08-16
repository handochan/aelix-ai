"""Session-scoped activity tracker for the ``/stats`` dashboard (WP-8, Feature 2).

The harness :class:`SessionStats` (``await get_session_stats()``) exposes
aggregate counts/tokens/cost but carries **no** per-tool success/failure split,
**no** per-model breakdown, and **no** wall-clock timing. This module fills that
gap entirely TUI-side by observing the agent event stream.

Design constraints (from the WP-8 spec):

- **Pure + injected clock.** ``__init__`` takes ``clock=time.monotonic`` and an
  optional ``model_provider`` callable so the tracker is fully unit-testable by
  feeding scripted event objects and a fake clock — no harness, no
  prompt-toolkit, no wall-clock dependency.
- **Read-only consumer.** It only reads duck-typed fields off the events the
  renderer already sees (``render.py:458-484``): ``tool_execution_start`` /
  ``tool_execution_end`` (``.tool_name`` / ``.is_error``), ``message_end``
  (``.message.usage`` / ``.message.model``), and ``turn_end``. Unknown event
  types are ignored.
- **Token reads mirror** ``aelix_agent_core/harness/_session_stats.py::_read``
  (dict-or-attr) so usage payloads work whether they arrive as a typed
  :class:`aelix_ai.streaming.Usage` dataclass or a plain ``dict``.

The tracker never raises on a malformed event — a best-effort accumulator that
degrades silently is preferable to crashing the REPL's event pump.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


def _read(obj: Any, key: str, default: Any = 0) -> Any:
    """Read a field from either a dict-shape or dataclass/duck-typed object.

    Mirrors ``aelix_agent_core/harness/_session_stats.py::_read``: assistant
    ``usage`` may arrive as a typed :class:`aelix_ai.streaming.Usage` dataclass
    **or** as a plain ``dict`` (legacy fixtures / provider passthrough). Branch
    on :func:`isinstance` then fall back to :func:`getattr`.
    """

    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


@dataclass(frozen=True)
class ToolStat:
    """Per-tool call/failure tally + latency for the efficiency leaderboard.

    ``total_duration`` / ``timed_calls`` accumulate wall-clock latency from
    ``tool_execution_start`` → ``tool_execution_end`` pairs matched by
    ``tool_call_id``. ``timed_calls`` can be < ``calls``: a call only counts
    toward latency when BOTH its start and end were observed (a replayed
    transcript, or an end whose start was missed, still bumps ``calls`` but not
    the latency average). Both default to 0 so a tally built without any timing
    compares/constructs exactly as before (WP-8 backward-compat).
    """

    name: str
    calls: int
    failures: int
    total_duration: float = 0.0
    timed_calls: int = 0

    @property
    def avg_duration(self) -> float | None:
        """Mean wall-clock seconds per *timed* call, or None if none were timed."""

        if self.timed_calls <= 0:
            return None
        return self.total_duration / self.timed_calls


@dataclass(frozen=True)
class ModelStat:
    """Per-model request count + token accumulation."""

    model: str
    requests: int
    input: int
    output: int
    cache_read: int


@dataclass(frozen=True)
class ActivitySnapshot:
    """Immutable point-in-time view of the session's tracked activity.

    ``per_tool`` / ``per_model`` are returned sorted (busiest first) so the
    dashboard formatters render a stable leaderboard.
    """

    tool_calls: int
    tool_failures: int
    per_tool: list[ToolStat]
    per_model: list[ModelStat]
    turns: int
    wall_seconds: float

    @property
    def success_rate(self) -> float | None:
        """Fraction of tool calls that did NOT error, in ``[0.0, 1.0]``.

        :data:`None` when no tool calls have been observed (avoids a misleading
        ``0%`` / division-by-zero before any tool runs).
        """

        if self.tool_calls <= 0:
            return None
        return (self.tool_calls - self.tool_failures) / self.tool_calls

    @property
    def avg_tool_seconds(self) -> float | None:
        """Mean latency across ALL *timed* tool calls, or None if none were timed.

        Aggregates ``per_tool`` (each tool's ``total_duration`` / ``timed_calls``)
        rather than dividing by ``tool_calls``, so untimed calls (no paired start)
        don't drag the average toward zero.
        """

        timed = sum(t.timed_calls for t in self.per_tool)
        if timed <= 0:
            return None
        return sum(t.total_duration for t in self.per_tool) / timed


@dataclass(frozen=True)
class ResumedActivity:
    """Turns + active seconds reconstructed from a resumed session's branch."""

    turns: int = 0
    seconds: float = 0.0


def _entry_epoch(entry: object) -> float | None:
    """Parse a persisted entry's ISO ``timestamp`` into epoch seconds.

    Every session entry carries one (``session/entries.py``), spelled
    ``2026-08-07T10:00:00.000Z``. Returns :data:`None` for an absent or
    unparseable value so a single bad row narrows the span instead of
    discarding the whole reconstruction.
    """

    raw = getattr(entry, "timestamp", None)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        # ``fromisoformat`` accepts the trailing ``Z`` from Python 3.11 on,
        # which is this project's floor.
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def derive_resumed_activity(entries: Iterable[object]) -> ResumedActivity:
    """Reconstruct turn count + active time from a persisted session branch.

    :attr:`SessionActivityTracker.turns` counts live ``turn_end`` events and
    :attr:`ActivitySnapshot.wall_seconds` spans :func:`time.monotonic` — both
    per-PROCESS, so a resumed session reported 0 for each while its tokens and
    message counts read correctly. Neither is persisted, but both are derivable
    from the branch, which is why this reconstructs rather than reports zero:

    - **turns** — one user message is one prompt is one turn, so the user
      messages on the branch are the turns that produced it. This counts a turn
      that was interrupted mid-flight: the prompt was still issued.
    - **seconds** — first to last MESSAGE entry. Bookkeeping entries
      (``session``, ``leaf``, ``label``) are skipped so the idle between opening
      a session and typing into it is not billed as active time, matching the
      live tracker, which starts at the first event of the first turn.

    Both are spans over the branch the user resumed, so a fork reports its own
    ancestry rather than the whole tree. Pure + duck-typed.

    COMPACTION — the caller supplies ``await session.get_branch()``, which is the
    RAW path to root, NOT ``build_context``'s display selection. Compaction
    entries are skipped here only because they are not ``type == "message"``, so
    turns and active time span the FULL history including summarized-away turns.
    That is the more useful answer, but it makes one ``/stats`` panel internally
    inconsistent: Turns and Active time cover everything, while tokens and cost
    cover only what survives in ``state.messages``, which a ``/compact`` truncates
    (see ``harness/_session_stats.py``'s ``cost_complete``, which is why cost
    renders as a floor there). Anyone changing either side should move both.
    """

    turns = 0
    first: float | None = None
    last: float | None = None
    for entry in entries:
        if getattr(entry, "type", None) != "message":
            continue
        message = getattr(entry, "message", None)
        if getattr(message, "role", None) == "user":
            turns += 1
        stamp = _entry_epoch(entry)
        if stamp is None:
            continue
        if first is None or stamp < first:
            first = stamp
        if last is None or stamp > last:
            last = stamp
    seconds = 0.0 if first is None or last is None else max(0.0, last - first)
    return ResumedActivity(turns=turns, seconds=seconds)


@dataclass
class _ToolAccum:
    calls: int = 0
    failures: int = 0
    total_duration: float = 0.0
    timed_calls: int = 0


@dataclass
class _ModelAccum:
    requests: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0


class SessionActivityTracker:
    """Observes the agent event stream and accumulates per-session activity.

    Call :meth:`on_event` at the TOP of the TUI's ``_on_agent_event`` (before
    the renderer) for every event; call :meth:`snapshot` to read an immutable
    :class:`ActivitySnapshot` for the ``/stats`` dashboard; call :meth:`reset`
    when the session hot-swaps (``_rebind`` / ``/resume``) to start fresh.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        model_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self._clock = clock
        self._model_provider = model_provider
        self._tools: dict[str, _ToolAccum] = {}
        self._models: dict[str, _ModelAccum] = {}
        self._turns = 0
        self._first_ts: float | None = None
        self._last_ts: float | None = None
        # Open ``tool_execution_start`` timestamps keyed by ``tool_call_id`` (the
        # pairing key for latency); popped when the matching end arrives.
        self._pending_starts: dict[str, float] = {}
        # Baseline carried in from a RESUMED session (see :meth:`seed_resumed`).
        # Held apart from the live counters so ``reset`` can drop it wholesale.
        self._prior = ResumedActivity()

    def reset(self) -> None:
        """Clear all accumulated activity (new/hot-swapped session)."""

        self._tools = {}
        self._models = {}
        self._turns = 0
        self._first_ts = None
        self._last_ts = None
        # Drop unpaired starts too, or a post-reset end could pair against a
        # pre-reset start and bleed a stale duration into the new session.
        self._pending_starts = {}
        # The baseline belongs to the session being left, not the one arriving;
        # the caller re-seeds from the NEW session's branch after resetting.
        self._prior = ResumedActivity()

    def seed_resumed(self, prior: ResumedActivity) -> None:
        """Adopt a resumed session's prior turns + active time as the baseline.

        Turns and wall time are the two ``/stats`` fields with no persisted
        counterpart to reload (unlike tokens/messages, which #122 restored by
        rebuilding ``_state.messages``), so a resume reported 0 for both.
        :func:`derive_resumed_activity` reconstructs them from the branch and
        this adopts the result; live events then accumulate ON TOP, so ``/stats``
        reads correctly both before and after the next turn.

        Per-tool and per-model breakdowns are deliberately NOT seeded: the branch
        records that a tool was called, not whether it errored or how long it
        took, so a reconstructed leaderboard would invent numbers this module has
        no basis for. They stay live-only and restart at zero on resume.
        """

        self._prior = ResumedActivity(
            turns=max(0, prior.turns), seconds=max(0.0, prior.seconds)
        )

    # -- event ingestion ----------------------------------------------------

    def on_event(self, event: object) -> None:
        """Dispatch a single agent event onto the accumulators.

        Best-effort: a malformed event is swallowed rather than allowed to
        crash the event pump.
        """

        try:
            self._stamp()
            event_type = getattr(event, "type", None)
            if event_type == "tool_execution_start":
                self._on_tool_start(event)
            elif event_type == "tool_execution_end":
                self._on_tool_end(event)
            elif event_type == "message_end":
                self._on_message_end(event)
            elif event_type == "turn_end":
                self._turns += 1
            # message_start / message_update / turn_start / agent_* / unknown →
            # timing-only (already stamped).
        except Exception:  # noqa: BLE001 — never crash the event pump
            return

    def _stamp(self) -> None:
        """Advance the wall-clock window (first event → latest event)."""

        now = self._clock()
        if self._first_ts is None:
            self._first_ts = now
        self._last_ts = now

    def _on_tool_start(self, event: object) -> None:
        # Record the start instant against the call id for latency pairing. Reuse
        # ``_last_ts`` (just advanced by ``_stamp``) instead of re-reading the
        # clock — a scripted test clock pops a tick per read, so an extra read
        # would desync the wall-clock window.
        tcid = getattr(event, "tool_call_id", None)
        if tcid is not None and self._last_ts is not None:
            self._pending_starts[str(tcid)] = self._last_ts

    def _on_tool_end(self, event: object) -> None:
        name = getattr(event, "tool_name", None) or "(unknown)"
        accum = self._tools.get(name)
        if accum is None:
            accum = _ToolAccum()
            self._tools[name] = accum
        accum.calls += 1
        if getattr(event, "is_error", False):
            accum.failures += 1
        # Pair with the open start (if any) to accumulate latency. ``_last_ts`` is
        # the end instant; an end without a matched start (replay / missed start)
        # bumps ``calls`` but not ``timed_calls``.
        tcid = getattr(event, "tool_call_id", None)
        if tcid is not None:
            start = self._pending_starts.pop(str(tcid), None)
            if start is not None and self._last_ts is not None:
                accum.total_duration += max(0.0, self._last_ts - start)
                accum.timed_calls += 1

    def _on_message_end(self, event: object) -> None:
        message = getattr(event, "message", None)
        if message is None:
            return
        # The loop emits message_end for EVERY persisting message (the user
        # prompt, follow-ups, AND every tool-result message — see loop.py:89-90),
        # not just assistant responses. UserMessage / ToolResultMessage carry no
        # ``.model`` and no ``.usage``, so counting them would inflate the
        # per-model ``requests`` (and the busiest-first leaderboard ordering) by
        # several-fold and mis-attribute them to ``current_model.id`` via the
        # ``model_provider`` fallback. Count assistant turns ONLY.
        if getattr(message, "role", None) != "assistant":
            return
        usage = getattr(message, "usage", None)
        model = getattr(message, "model", None)
        if not model and self._model_provider is not None:
            try:
                model = self._model_provider()
            except Exception:  # noqa: BLE001 — provider must not break ingest
                model = None
        model = model or "(unknown)"
        accum = self._models.get(model)
        if accum is None:
            accum = _ModelAccum()
            self._models[model] = accum
        accum.requests += 1
        if usage is not None:
            accum.input += int(_read(usage, "input", 0) or 0)
            accum.output += int(_read(usage, "output", 0) or 0)
            accum.cache_read += int(_read(usage, "cache_read", 0) or 0)

    # -- read-out -----------------------------------------------------------

    def snapshot(self) -> ActivitySnapshot:
        """Build an immutable view of the tracked activity so far."""

        per_tool = [
            ToolStat(
                name=name,
                calls=accum.calls,
                failures=accum.failures,
                total_duration=accum.total_duration,
                timed_calls=accum.timed_calls,
            )
            for name, accum in self._tools.items()
        ]
        # Busiest tools first; ties broken by name for stable ordering.
        per_tool.sort(key=lambda t: (-t.calls, t.name))

        per_model = [
            ModelStat(
                model=model,
                requests=accum.requests,
                input=accum.input,
                output=accum.output,
                cache_read=accum.cache_read,
            )
            for model, accum in self._models.items()
        ]
        per_model.sort(key=lambda m: (-m.requests, m.model))

        tool_calls = sum(t.calls for t in per_tool)
        tool_failures = sum(t.failures for t in per_tool)

        if self._first_ts is None or self._last_ts is None:
            wall_seconds = 0.0
        else:
            wall_seconds = max(0.0, self._last_ts - self._first_ts)

        # A resumed baseline (if any) plus this process's activity. The two spans
        # are added rather than bridged: the gap between the sessions is the user
        # away from the terminal, which is not active time.
        return ActivitySnapshot(
            tool_calls=tool_calls,
            tool_failures=tool_failures,
            per_tool=per_tool,
            per_model=per_model,
            turns=self._prior.turns + self._turns,
            wall_seconds=self._prior.seconds + wall_seconds,
        )


__all__ = [
    "ActivitySnapshot",
    "ModelStat",
    "ResumedActivity",
    "SessionActivityTracker",
    "ToolStat",
    "derive_resumed_activity",
]
