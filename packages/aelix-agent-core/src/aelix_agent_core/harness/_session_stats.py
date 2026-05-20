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
"""

from __future__ import annotations

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


def aggregate_session_stats(
    session_id: str,
    messages: list[Message],
    session_file: str | None = None,
    context_usage: Any | None = None,
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
                msg_cost = _read(usage, "cost", None)
                if msg_cost is not None:
                    cost += float(_read(msg_cost, "total", 0.0) or 0.0)
        elif isinstance(msg, ToolResultMessage):
            tool_results_count += 1

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
    )


__all__ = [
    "SessionStats",
    "SessionStatsTokens",
    "aggregate_session_stats",
]
