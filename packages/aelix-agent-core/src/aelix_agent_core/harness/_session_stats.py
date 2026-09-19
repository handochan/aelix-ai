"""Pi parity: ``agent-session.ts:212-223`` SessionStats + ``:2901-2945`` getSessionStats.

Sprint 6h₃ (ADR-0073, P-268/P-269/P-272/P-276/P-283) ports the Pi
``SessionStats`` shape + the ``getSessionStats`` aggregator.

The shape mirrors Pi's ``packages/coding-agent/src/core/agent-session.ts:212-223``
interface byte-for-byte:

- ``sessionFile`` / ``sessionId`` — session metadata.
- ``userMessages`` / ``assistantMessages`` / ``toolCalls`` / ``toolResults``
  / ``totalMessages`` — per-role aggregation.
- ``tokens.{input,output,cacheRead,cacheWrite,total}`` — token totals
  across every assistant message.
- ``cost`` — accumulated USD across every assistant message
  (``usage.cost.total`` per Pi).
- ``contextUsage`` — optional :class:`ContextUsage` reused from the
  existing Aelix surface (``extensions/api.py:ContextUsage``).

The aggregator walks an in-memory message list; the harness owns the
plumbing (reading ``self._session.messages`` / ``self.session_file``)
and forwards the data via :func:`aggregate_session_stats`.

ADR-0243 (#199) adds one input the messages cannot supply: spend a TOOL
reported for work it ran outside this session's own model calls — another
model, a paid service. Such a tool writes ``aelix.usage`` records
(:data:`USAGE_RECORD_TYPE`, one ``CustomEntry`` line each — ADR-0242 rule 1),
the harness collects their ``data`` from the session branch, and
:func:`fold_usage_records` folds them into :class:`ToolUsage`. The fold is added
into ``tokens`` and ``cost`` so every consumer sees one total, and is kept
apart as ``SessionStats.tool_usage`` so a display can break it out.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from aelix_ai.messages import (
    AssistantMessage,
    Message,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)


@dataclass(frozen=True)
class SessionStatsTokens:
    """Pi parity: ``SessionStats.tokens`` sub-shape
    (``agent-session.ts:219``).

    ``total = input + output + cacheRead + cacheWrite`` — Pi's
    accumulator sums all four buckets into ``tokens.total``.
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0


#: ``customType`` of a usage record (ADR-0243). Its ``data`` is
#: ``{"v": 1, "key": str, "state": "pending"}`` before the work can spend, then
#: ``{"v": 1, "key": str, "state": "final", "usage": {"input", "output",
#: "cache_read", "cache_write", "cost"}, "cost_known": bool}`` once it settled.
#: :func:`fold_usage_records` states exactly what is read and how.
USAGE_RECORD_TYPE = "aelix.usage"


@dataclass(frozen=True)
class ToolUsage:
    """Usage that tools reported through ``aelix.usage`` records (ADR-0243).

    Aelix-only and off the RPC wire. It is a BREAKDOWN, not an addition:
    :func:`aggregate_session_stats` has already added it into
    ``SessionStats.tokens`` and ``SessionStats.cost``.

    - ``tokens`` — flows only (input / output / cache read / cache write),
      summed, with ``total`` their sum. A context LEVEL is never folded in.
    - ``cost`` — USD, summed over the settled (``final``) records.
    - ``cost_known`` — ``False`` when ``cost`` is short of what the tools spent:
      a record is still pending, one says its own cost is unknown, or one could
      not be read. Same meaning as ``SessionStats.cost_known``.
    - ``runs`` — records after the fold: one per ``key``, one per keyless line.
    - ``pending`` — how many of ``runs`` never settled: spend that exists but
      was never confirmed (still running, or a process that died mid-run).
    """

    tokens: SessionStatsTokens = field(default_factory=SessionStatsTokens)
    cost: float = 0.0
    cost_known: bool = True
    runs: int = 0
    pending: int = 0


@dataclass(frozen=True)
class SessionStats:
    """Pi parity: ``SessionStats`` (``agent-session.ts:212-223``).

    Sprint 6h₃ (ADR-0073, P-268) — 10-field shape; ``session_file`` and
    ``context_usage`` are optional (Pi ``string | undefined`` and
    ``ContextUsage | undefined`` respectively). The RPC serializer at
    :func:`aelix_coding_agent.rpc.rpc_mode._session_stats_to_dict`
    OMITs the optional fields from the wire when ``None`` so Pi's
    ``JSON.stringify`` undefined-skip behaviour is preserved.

    ``context_usage`` is typed ``Any`` to avoid the
    ``aelix_agent_core`` → ``aelix_coding_agent`` import cycle —
    callers pass an ``ExtensionContext.ContextUsage`` instance (or
    :data:`None`); the runtime never reads it as anything but a
    duck-typed object.
    """

    session_id: str = ""
    user_messages: int = 0
    assistant_messages: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    total_messages: int = 0
    tokens: SessionStatsTokens = field(default_factory=SessionStatsTokens)
    cost: float = 0.0
    session_file: str | None = None
    context_usage: Any | None = None  # ContextUsage | None — avoid import cycle
    # Aelix-additive (NOT on the Pi wire — ``_session_stats_to_dict`` enumerates
    # its keys explicitly, so this stays off the RPC response). ``False`` means
    # token usage was seen that no price could be found for, so ``cost`` is short
    # of the real bill: a display MUST then show a placeholder rather than render
    # ``cost`` as an authoritative figure. A wrong bill is worse than an absent
    # one. ``True`` on an all-zero session is correct — nothing was spent.
    cost_known: bool = True
    # Aelix-additive, off the Pi wire for the same reason as ``cost_known``: the
    # share of ``tokens`` / ``cost`` that tools reported (ADR-0243). Already
    # INSIDE those totals — a display that adds it again double counts.
    tool_usage: ToolUsage = field(default_factory=ToolUsage)


def _read(obj: Any, key: str, default: Any = 0) -> Any:
    """Read field from either dataclass-like or dict-shape usage payload.

    Sprint 6h₃ W6 (P-283): assistant ``usage`` may arrive as a fully
    typed :class:`Usage` dataclass (Sprint 6f streaming path) **or**
    as a plain ``dict`` (legacy JSONL fixtures + provider passthrough).
    Use ``isinstance(obj, dict)`` to branch; otherwise fall back to
    :func:`getattr` so dataclasses + duck-typed objects both work.
    """

    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _message_cost(msg: Any, usage: Any) -> float | None:
    """Cost of ONE assistant message in USD, or :data:`None` if unpriceable.

    Pi prices a turn inside the adapter (``calculateCost``); no Aelix adapter
    does, so a persisted ``usage`` carries ``input``/``output``/``cache_*`` and
    **no** ``cost`` key, and the old ``usage.cost.total`` sum was structurally
    0.0 for the main session however many tokens it burned. The pricing helper
    itself was never at fault — callers that invoke ``calculate_cost`` directly
    have always reported real figures; nothing invoked it on this path.

    A cost the provider DID resolve always wins; otherwise the message is priced
    from its own persisted ``provider``/``model`` provenance, which every adapter
    stamps. Pricing per message rather than per session is what keeps a session
    that switched models — or resumed one — honest.

    Returns :data:`None` (never 0.0) when the model is absent from the registry,
    so the caller can distinguish "no price known" from "nothing spent".
    """

    resolved = _read(usage, "cost", None)
    if resolved is not None:
        total = _read(resolved, "total", None)
        # A provider that resolved a cost of exactly 0.0 (free tier, cached-only
        # turn) HAS answered, and re-pricing would overrule it with a catalog
        # rate it did not charge. But a zero is only an answer when it was
        # actually WRITTEN: the live ``Usage`` dataclass defaults ``cost`` to an
        # all-zero ``UsageCost``, so on that shape a 0.0 is indistinguishable
        # from "never priced" — the normal state of every turn, since no adapter
        # prices. So treat an explicit ``cost`` KEY on a dict payload as the
        # deliberate answer, and otherwise trust only a non-zero total. Reading
        # a bare ``is not None`` as the answer would return 0.0 for every live
        # turn and silently re-break the main-session cost.
        if total is not None and (
            float(total) or (isinstance(usage, dict) and "cost" in usage)
        ):
            return float(total)

    provider = getattr(msg, "provider", None)
    model_id = getattr(msg, "model", None)
    if not provider or not model_id:
        return None

    # Local import: module scope would make the pricing catalog a hard import of
    # every consumer of this module, and ``models_generated`` is a large static
    # table. No I/O either way.
    from aelix_ai.models import calculate_cost, get_model
    from aelix_ai.streaming import Usage

    model = get_model(str(provider), str(model_id))
    if model is None:
        return None
    # Round-trip through the canonical calculator rather than re-deriving the
    # arithmetic here — the 1h-cache-write rate (2x base input) lives in exactly
    # one place and a second copy would drift from it.
    return float(
        calculate_cost(
            model,
            Usage(
                input=int(_read(usage, "input", 0) or 0),
                output=int(_read(usage, "output", 0) or 0),
                cache_read=int(_read(usage, "cache_read", 0) or 0),
                cache_write=int(_read(usage, "cache_write", 0) or 0),
                cache_write_1h=int(_read(usage, "cache_write_1h", 0) or 0),
            ),
        ).total
    )


#: The four token FLOWS a usage record carries, in :class:`SessionStatsTokens`
#: order. Nothing else in a record is summed as tokens.
_USAGE_FLOWS = ("input", "output", "cache_read", "cache_write")


def _record_flow(value: Any) -> int | None:
    """A token count from a record, or :data:`None` when it cannot be one.

    A count is a whole, finite, non-negative JSON number. ``bool`` is refused
    although Python calls it an ``int``; ``12.0`` is accepted as ``12`` (a JSON
    writer may spell a whole number that way), ``12.5`` is not. Never raises.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and math.isfinite(value) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _record_amount(value: Any) -> float | None:
    """A USD amount from a record, or :data:`None` when it cannot be one.

    NaN and ±Infinity are written bare and read back (ADR-0242 rule 1.7), so the
    finite check is load-bearing, not paranoia. Never raises: an integer too
    large for a float is refused rather than overflowing.
    """

    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        amount = float(value)
    except OverflowError:
        return None
    if not math.isfinite(amount) or amount < 0:
        return None
    return amount


def fold_usage_records(records: Iterable[Any]) -> ToolUsage:
    """Fold ``aelix.usage`` payloads (``CustomEntry.data``, root→leaf order).

    Exactly what is read, and how:

    - ``state`` — ``"pending"`` or ``"final"``. A line with any other ``state``,
      or whose ``data`` is not an object, is skipped and makes the cost unknown:
      it may be spend a newer writer recorded in a shape this reader predates.
    - ``key`` — a non-empty string. Lines are grouped by it and the LAST line of
      a key wins, so a line appended twice (the store is at-least-once,
      ADR-0242 §3) counts once and a ``final`` supersedes its ``pending``. A
      line without one counts on its own.
    - A key whose last line is ``pending`` adds nothing, is counted in
      ``pending``, and makes the cost unknown: the spend was never confirmed.
    - A ``final`` adds ``usage.input`` / ``output`` / ``cache_read`` /
      ``cache_write`` to the token flows and ``usage.cost`` to the cost. A
      number that is missing, non-finite, negative, or (for a token count) not
      whole adds nothing and makes the cost unknown; a ``usage`` that is not an
      object adds nothing and makes the cost unknown.
    - ``cost_known`` on a ``final`` — anything but ``true`` makes the cost
      unknown. The priced part is still added, so a display can say "at least".

    Nothing else is read. ``v`` is not consulted: a later version keeps these
    fields' meaning or it is a different ``customType`` (ADR-0242 rule 1.3).
    Unknown keys are ignored (rule 1.6), which is how a context LEVEL such as
    ``tokens`` / ``context_tokens`` stays out: only the four flows are summed.
    """

    # First-appearance order with last-wins values: re-assigning a dict key keeps
    # its original position, so the sums below run in the order the records
    # were first written. A keyless line gets its own integer slot, which can
    # never collide with a string key.
    latest: dict[str | int, dict[str, Any]] = {}
    readable = True
    for index, data in enumerate(records):
        if not isinstance(data, dict) or data.get("state") not in ("pending", "final"):
            readable = False
            continue
        key = data.get("key")
        latest[key if isinstance(key, str) and key else index] = data

    flows = [0, 0, 0, 0]
    cost = 0.0
    cost_known = readable
    pending = 0
    for data in latest.values():
        if data["state"] == "pending":
            pending += 1
            cost_known = False
            continue
        usage = data.get("usage")
        if not isinstance(usage, dict):
            cost_known = False
            continue
        for position, name in enumerate(_USAGE_FLOWS):
            count = _record_flow(usage.get(name))
            if count is None:
                cost_known = False
            else:
                flows[position] += count
        amount = _record_amount(usage.get("cost"))
        if amount is None:
            cost_known = False
        else:
            cost += amount
        if data.get("cost_known") is not True:
            cost_known = False

    tokens_in, tokens_out, cache_r, cache_w = flows
    return ToolUsage(
        tokens=SessionStatsTokens(
            input=tokens_in,
            output=tokens_out,
            cache_read=cache_r,
            cache_write=cache_w,
            total=tokens_in + tokens_out + cache_r + cache_w,
        ),
        cost=cost,
        cost_known=cost_known,
        runs=len(latest),
        pending=pending,
    )


def aggregate_session_stats(
    session_id: str,
    messages: list[Message],
    session_file: str | None = None,
    context_usage: Any | None = None,
    cost_complete: bool = True,
    usage_records: Iterable[Any] = (),
) -> SessionStats:
    """Pi parity: ``agent-session.ts:2901-2945`` ``getSessionStats``.

    Walks the ``messages`` list, accumulates counts/tokens/cost per
    Pi's algorithm:

    1. ``UserMessage`` increments ``userMessages``.
    2. ``AssistantMessage`` increments ``assistantMessages``; each
       ``ToolCallContent`` block adds to ``toolCalls``; ``usage`` (if
       present) feeds ``tokens.{input,output,cacheRead,cacheWrite}``
       and ``cost`` (from ``usage.cost.total``).
    3. ``ToolResultMessage`` increments ``toolResults``.
    4. ``totalMessages = len(messages)`` (Pi parity — Pi reads
       ``state.messages.length`` at ``agent-session.ts:2935``;
       see :data:`SessionStats.total_messages` below) and
       ``tokens.total = input + output + cacheRead + cacheWrite``.

    Pi parity: assistant ``usage`` is the per-call :class:`Usage`
    instance (Sprint 6f from ``streaming.py``) carrying
    ``input``/``output``/``cache_read``/``cache_write`` plus
    ``cost: UsageCost``. Sprint 6h₃ W6 (P-283) extracts the field
    reads through :func:`_read` so dict-shape usage payloads (legacy
    fixtures, provider passthrough) work the same as dataclasses.

    ``cost_complete=False`` tells the aggregator that ``messages`` is a SUBSET
    of what the session actually spent, so the resulting ``cost`` is a floor
    rather than a bill and :attr:`SessionStats.cost_known` goes ``False``. The
    caller owns that judgement because only it can see the session: after a
    ``/compact`` the harness rebuilds ``state.messages`` from the post-compaction
    branch (``core.py:1683-1687`` via ``select_display_entries``, which drops
    everything before ``first_kept_entry_id``), so the summarized-away turns are
    no longer countable here and nothing in ``messages`` reveals their absence.

    ``usage_records`` are the ``data`` payloads of the branch's ``aelix.usage``
    records (ADR-0243), in root→leaf order. :func:`fold_usage_records` folds
    them; the result is ADDED to ``tokens`` and ``cost``, clears ``cost_known``
    when its own cost is not known, and is returned as ``tool_usage``.
    ``cost_complete`` does not touch that part: the caller reads the records
    over the whole branch, compacted entries included, so compaction removes
    none of them.
    """

    user = 0
    assistant = 0
    tool_results_count = 0
    tool_calls = 0
    tokens_in = 0
    tokens_out = 0
    cache_r = 0
    cache_w = 0
    cost = 0.0
    # Assistant messages that reported token usage no price could be found for.
    unpriced = 0

    for msg in messages:
        if isinstance(msg, UserMessage):
            user += 1
        elif isinstance(msg, AssistantMessage):
            assistant += 1
            for block in msg.content or []:
                if isinstance(block, ToolCallContent):
                    tool_calls += 1
            usage = getattr(msg, "usage", None)
            if usage is not None:
                tokens_in += int(_read(usage, "input", 0) or 0)
                tokens_out += int(_read(usage, "output", 0) or 0)
                cache_r += int(_read(usage, "cache_read", 0) or 0)
                cache_w += int(_read(usage, "cache_write", 0) or 0)
                msg_cost = _message_cost(msg, usage)
                if msg_cost is None:
                    unpriced += 1
                else:
                    cost += msg_cost
        elif isinstance(msg, ToolResultMessage):
            tool_results_count += 1

    # Tool-reported spend joins the same totals, so every consumer — footer,
    # ``/cost``, ``/stats``, History, RPC — reads one number and none of them has
    # to know the records exist. Counted once: no message carries this spend.
    tool = fold_usage_records(usage_records)
    tokens_in += tool.tokens.input
    tokens_out += tool.tokens.output
    cache_r += tool.tokens.cache_read
    cache_w += tool.tokens.cache_write
    cost += tool.cost

    tokens_total = tokens_in + tokens_out + cache_r + cache_w
    return SessionStats(
        session_id=session_id,
        user_messages=user,
        assistant_messages=assistant,
        tool_calls=tool_calls,
        tool_results=tool_results_count,
        # Pi parity: agent-session.ts:2935 — Pi uses state.messages.length,
        # the total array cardinality (not type-filtered sum). For the
        # current Aelix 3-type universe the numerical result is identical;
        # future-proofed against additive message types (custom /
        # bashExecution / compaction).
        total_messages=len(messages),
        tokens=SessionStatsTokens(
            input=tokens_in,
            output=tokens_out,
            cache_read=cache_r,
            cache_write=cache_w,
            total=tokens_total,
        ),
        cost=cost,
        session_file=session_file,
        context_usage=context_usage,
        # Known only if every message with usage could be priced, the caller
        # says the message list is the whole spend, AND every tool-reported
        # record settled with a known cost.
        cost_known=unpriced == 0 and cost_complete and tool.cost_known,
        tool_usage=tool,
    )


__all__ = [
    "USAGE_RECORD_TYPE",
    "SessionStats",
    "SessionStatsTokens",
    "ToolUsage",
    "aggregate_session_stats",
    "fold_usage_records",
]
