"""Pi-parity ``compaction`` module (Sprint 4b / Phase 2.2.2 — ADR-0023).

Pi source: ``packages/agent/src/harness/compaction/compaction.ts`` at SHA
``734e08e``. Sprint 4b ships the data-shape parity (``CompactionPreparation``
+ ``CompactResult``) and a minimal :func:`prepare_compaction` /
:func:`compact` surface that the harness calls from :meth:`AgentHarness.compact`.

Per P-14 (W1 finding), Aelix does NOT add summarizer callbacks to
``AgentHarnessOptions``. Pi calls ``compact()`` inline using ``this.model`` +
``await this.getApiKeyAndHeaders(model)``; Aelix mirrors this. Sprint 4b
ships a thin LLM-driver placeholder that raises ``AgentHarnessError(
"invalid_state")`` when no auth is available and supports a test-only
``_summarizer_override`` callable injected via fixture (Aelix-additive,
documented).

The full Pi compaction pipeline (``estimateContextTokens``, ``findCutPoint``,
``generateSummary`` with file-ops accounting) is deferred to Phase 4 when the
provider adapter (ADR-0038) lands. Sprint 4b ships a Pi-shape preparation
result with the minimum fields needed for the emit + persist path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aelix_ai.streaming import Model

from aelix_agent_core.session.entries import SessionTreeEntry
from aelix_agent_core.types import AgentMessage

if TYPE_CHECKING:
    pass


# === Result dataclasses (Pi parity, ``compaction.ts:89-99 / 521-538``) ===


@dataclass(frozen=True)
class CompactionPreparation:
    """Pi ``CompactionPreparation`` (``compaction.ts:521-538``).

    Sprint 4b ships the four fields the harness needs to thread the emit +
    persist path (``first_kept_entry_id``, ``messages_to_summarize``,
    ``tokens_before``, ``settings``). The remaining Pi fields
    (``turn_prefix_messages``, ``is_split_turn``, ``previous_summary``,
    ``file_ops``) are added with sane defaults so future Phase 4 work can
    populate them without breaking the dataclass shape.
    """

    first_kept_entry_id: str
    messages_to_summarize: list[AgentMessage] = field(default_factory=list)
    turn_prefix_messages: list[AgentMessage] = field(default_factory=list)
    is_split_turn: bool = False
    tokens_before: int = 0
    previous_summary: str | None = None
    # ``file_ops`` and ``settings`` are Pi dicts/dataclasses — ship as Any
    # placeholders to keep the cross-runtime shape stable until Phase 4 wires
    # the real CompactionSettings + FileOperations ports.
    file_ops: Any | None = None
    settings: Any | None = None


@dataclass(frozen=True)
class CompactResult:
    """Pi ``CompactionResult`` (``compaction.ts:89-99``).

    The Pi field is named ``firstKeptEntryId``; Aelix uses snake_case
    ``first_kept_entry_id`` per ADR-0035 idiomatic-Python policy.
    """

    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: Any | None = None


# === Test-only seam (Aelix-additive, documented per ADR-0023) =============

# Sprint 4b ships a test-only override hook so tests can substitute a
# deterministic summarizer instead of standing up a real provider. The
# harness wires this through :class:`AgentHarnessOptions` only when a test
# explicitly opts in by setting ``_summarizer_override``. Documented as
# Aelix-additive divergence in ADR-0023 §"Aelix-additive divergences".
SummarizerOverride = Callable[
    [Model, "CompactionPreparation", str | None],
    "Awaitable[CompactResult] | CompactResult",
]


# === Public API (Pi parity, ``compaction.ts:541-606`` + ``:626-705``) =====


def prepare_compaction(
    path_entries: list[SessionTreeEntry],
    custom_instructions: str | None = None,
) -> CompactionPreparation | None:
    """Pi ``prepareCompaction`` (``compaction.ts:541-606``).

    Sprint 4b ships a minimal port — the heavy logic (``findCutPoint``,
    ``estimateContextTokens``, ``extractFileOperations``) lands when Phase 4
    provider adapter (ADR-0038) wires real token accounting. For now we
    return ``None`` when there is nothing to compact (mirroring Pi's empty /
    already-compacted short-circuits) and otherwise a preparation that names
    the **first** entry as the cut point. This keeps the emit + persist
    pipeline exercisable in unit tests without depending on a real LLM.

    The ``custom_instructions`` parameter is accepted to match Pi's
    Sprint 4b call site (``agent-harness.ts:706-708``) but not yet threaded
    into the preparation — the harness passes it independently into the
    summarizer call.
    """

    if not path_entries:
        return None
    if path_entries[-1].type == "compaction":
        # Already compacted to the tail.
        return None

    first_kept = path_entries[0]
    if not first_kept.id:
        return None
    # spec-deviation: full ``findCutPoint`` / ``estimateContextTokens`` port
    # deferred to Phase 4 (ADR-0038). Sprint 4b uses the head entry as the
    # cut point so the persist path can be exercised end-to-end.
    _ = custom_instructions  # accepted for Pi-parity signature
    return CompactionPreparation(
        first_kept_entry_id=first_kept.id,
        messages_to_summarize=[],
        tokens_before=0,
    )


async def compact(
    model: Model,
    get_api_key_and_headers: Callable[..., Any] | None,
    preparation: CompactionPreparation,
    custom_instructions: str | None = None,
    *,
    _summarizer_override: SummarizerOverride | None = None,
) -> CompactResult:
    """Pi ``compact`` (``compaction.ts:626-705``).

    Calls into the provider via ``get_api_key_and_headers`` per P-14 (no
    summarizer callback on :class:`AgentHarnessOptions`). Test fixtures can
    short-circuit the LLM call via ``_summarizer_override`` (Aelix-additive
    test-only seam documented in ADR-0023).

    Raises :class:`AgentHarnessError("invalid_state", ...)` when no override
    is supplied and ``get_api_key_and_headers`` is ``None``.
    """

    from aelix_agent_core.harness.core import AgentHarnessError

    if _summarizer_override is not None:
        raw = _summarizer_override(model, preparation, custom_instructions)
        if hasattr(raw, "__await__"):
            result = await raw  # type: ignore[misc]
        else:
            result = raw  # type: ignore[assignment]
        if not isinstance(result, CompactResult):
            raise AgentHarnessError(
                "invalid_state",
                "_summarizer_override must return CompactResult",
            )
        return result

    if get_api_key_and_headers is None:
        raise AgentHarnessError(
            "invalid_state",
            "compact requires options.get_api_key_and_headers (Phase 4 owner)",
        )

    # spec-deviation: Phase 4 provider adapter (ADR-0038) ports the real
    # ``generateSummary`` call. Sprint 4b raises a clear error so test
    # paths use ``_summarizer_override`` and production paths surface the
    # missing-provider state explicitly.
    raise AgentHarnessError(
        "invalid_state",
        "compact() LLM provider not yet implemented (Phase 4 / ADR-0038)",
    )


# === Sprint 6h₅c (ADR-0085 P-369) — context-token helpers ===================


def calculate_context_tokens(usage: dict[str, Any] | None) -> int:
    """Pi parity: ``calculateContextTokens`` (``compaction.ts:135-137``).

    Sprint 6h₅c (ADR-0085, P-369). Pi sums ``total_tokens`` directly when
    present, otherwise falls back to ``input + output + cache_read +
    cache_write``. Both camelCase and snake_case keys are read defensively
    because the Aelix :class:`Usage` payload is ``dict[str, Any]`` and the
    provider adapters have not yet converged on a single spelling.
    """

    if usage is None:
        return 0
    total = usage.get("total_tokens") or usage.get("totalTokens") or 0
    if total:
        return int(total)
    return int(
        (usage.get("input_tokens") or usage.get("input") or 0)
        + (usage.get("output_tokens") or usage.get("output") or 0)
        + (usage.get("cache_read") or usage.get("cacheRead") or 0)
        + (usage.get("cache_write") or usage.get("cacheWrite") or 0)
    )


def estimate_tokens(message: Any) -> int:
    """Pi parity: ``estimateTokens`` (``compaction.ts:232-279``).

    Sprint 6h₅c (ADR-0085, P-369). Heuristic character-based estimate —
    Pi line ``:264`` treats an ``ImageContent`` block as a flat 4800-char
    contribution (~1200 tokens at the 4-chars-per-token Pi heuristic).
    String content is counted by length; structured blocks are
    serialized via ``json.dumps`` for :class:`ToolCallContent`.
    """

    from aelix_ai.messages import (
        ImageContent,
        TextContent,
        ThinkingContent,
        ToolCallContent,
    )

    chars = 0
    content = getattr(message, "content", None)
    if isinstance(content, str):
        chars = len(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, TextContent):
                chars += len(block.text or "")
            elif isinstance(block, ImageContent):
                chars += 4800  # Pi line :264
            elif isinstance(block, ToolCallContent):
                import json as _json

                chars += len(_json.dumps(block.input or {}))
                chars += len(block.tool_name or "")
            elif isinstance(block, ThinkingContent):
                # Sprint 6h₅c W4 MEDIUM (ADR-0085) — explicit branch BEFORE
                # the ``hasattr(block, "text")`` catch-all so the
                # ``thinking`` attribute is counted (the catch-all reads
                # ``block.text`` which :class:`ThinkingContent` lacks).
                chars += len(block.thinking or "")
            elif hasattr(block, "text"):
                chars += len(getattr(block, "text", "") or "")
    return chars // 4


@dataclass(frozen=True)
class _EstimateResult:
    """Pi parity: :func:`estimate_context_tokens` return shape."""

    tokens: int


def estimate_context_tokens(messages: list[Any]) -> _EstimateResult:
    """Pi parity: ``estimateContextTokens`` (``compaction.ts:186-214``).

    Sprint 6h₅c (ADR-0085, P-369). Walk ``messages`` in reverse, find the
    last assistant message whose :attr:`AssistantMessage.stop_reason` is
    not ``"aborted"`` or ``"error"``, and sum that assistant turn's
    :attr:`AssistantMessage.usage` tokens with the heuristic estimate for
    any trailing messages. When no eligible assistant message is found,
    the result is the heuristic estimate over every message.
    """

    from aelix_ai.messages import AssistantMessage

    last_idx: int | None = None
    last_usage: dict[str, Any] | None = None
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, AssistantMessage):
            stop = getattr(msg, "stop_reason", None)
            if stop in ("aborted", "error"):
                continue
            last_idx = i
            last_usage = getattr(msg, "usage", None)
            break
    if last_idx is None:
        return _EstimateResult(tokens=sum(estimate_tokens(m) for m in messages))
    usage_tokens = calculate_context_tokens(last_usage)
    trailing = sum(estimate_tokens(m) for m in messages[last_idx + 1 :])
    return _EstimateResult(tokens=usage_tokens + trailing)


def get_latest_compaction_entry(branch_entries: list[Any]) -> Any | None:
    """Pi parity: walk ``branch_entries`` in reverse for the most recent
    entry whose ``type == "compaction"``.

    Sprint 6h₅c (ADR-0085, P-369). Aelix uses snake_case
    :class:`CompactionEntry` (``session/entries.py:64-80``) — Pi's
    ``CompactionEntry`` matches by string discriminator so the
    ``getattr(entry, "type", None) == "compaction"`` probe is correct.
    """

    for entry in reversed(branch_entries):
        if getattr(entry, "type", None) == "compaction":
            return entry
    return None


__all__ = [
    "CompactResult",
    "CompactionPreparation",
    "SummarizerOverride",
    "calculate_context_tokens",
    "compact",
    "estimate_context_tokens",
    "estimate_tokens",
    "get_latest_compaction_entry",
    "prepare_compaction",
]
