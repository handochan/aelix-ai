"""Pi-parity ``compaction`` module (Sprint 4b / Phase 2.2.2 — ADR-0023,
updated Sprint 6h₁₂-compaction).

Pi source: ``packages/agent/src/harness/compaction/compaction.ts`` at SHA
``734e08e``. Per P-14 (W1 finding), Aelix does NOT add summarizer callbacks
to ``AgentHarnessOptions``. Pi calls ``compact()`` inline using
``this.model`` + ``await this.getApiKeyAndHeaders(model)``; Aelix mirrors
this. A test-only ``_summarizer_override`` callable is supported
(Aelix-additive, documented in ADR-0023).

Sprint 6h₁₂-compaction lands the real Pi pipeline:
- ``findCutPoint`` (backward token-budget walk via :func:`_find_cut_index`)
- ``generateSummary`` (via :func:`_generate_summary` + :func:`stream_simple`)
- ``first_kept_entry_id`` now maps to the **cut entry** (not the head) so
  :func:`build_session_context` drops the summarized prefix correctly.

Known remaining gaps (deferred):
- ``file_ops`` / ``turn_prefix_messages`` / ``is_split_turn`` still absent.
- No ``max_tokens`` cap on the summarization call (``SimpleStreamOptions``
  has no ``max_tokens`` field yet — infra gap, Pi uses
  ``floor(0.8 * reserveTokens)`` here).
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aelix_ai.streaming import Model

from aelix_agent_core.session.entries import SessionTreeEntry
from aelix_agent_core.types import AgentMessage

if TYPE_CHECKING:
    pass


# === Summarization prompt constants (Pi parity, ``compaction.ts``) =========
# Copied verbatim from pi ``compaction.ts`` (SHA 734e08e). The summarizer
# treats the conversation as DATA, not a chat to continue.

SUMMARIZATION_SYSTEM_PROMPT = """You are a context summarization assistant. Your task is to read a conversation between a user and an AI coding assistant, then produce a structured summary following the exact format specified.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary."""

SUMMARIZATION_PROMPT = """The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish? Can be multiple items if the session covers different tasks.]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned by user]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue]
- [Or "(none)" if not applicable]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

# Pi ``UPDATE_SUMMARIZATION_PROMPT`` (``compaction.ts:487-524``) — used when a
# previous summary is being incrementally updated rather than created fresh.
UPDATE_SUMMARIZATION_PROMPT = """The messages above are NEW conversation messages to incorporate into the existing summary provided in <previous-summary> tags.

Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages
- If something is no longer relevant, you may remove it

Use this EXACT format:

## Goal
[Preserve existing goals, add new ones if the task expanded]

## Constraints & Preferences
- [Preserve existing, add new ones discovered]

## Progress
### Done
- [x] [Include previously done items AND newly completed items]

### In Progress
- [ ] [Current work - update based on progress]

### Blocked
- [Current blockers - remove if resolved]

## Key Decisions
- **[Decision]**: [Brief rationale] (preserve all previous, add new)

## Next Steps
1. [Update based on current state]

## Critical Context
- [Preserve important context, add new if needed]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

# Pi ``TOOL_RESULT_MAX_CHARS`` (``compaction.ts``) — tool results are truncated
# in the serialized conversation so a single huge result can't dominate.
TOOL_RESULT_MAX_CHARS = 2000

# Pi ``findCutPoint`` keep-recent budget. Messages whose accumulated (reverse)
# token estimate is within this budget stay; everything older is summarized.
KEEP_RECENT_TOKENS = 20000


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


# === Conversation serialization (Pi parity, ``compaction.ts`` serialize) ===


def _serialize_conversation(messages: list[Any]) -> str:
    """Render ``messages`` as plain text the summarizer treats as DATA.

    Pi serializes each message into a flat, readable transcript so the
    summarizer model never tries to *continue* the chat — it only reads.
    User/assistant turns become ``User:``/``Assistant:`` lines, thinking and
    tool calls are annotated, and tool results are truncated to
    :data:`TOOL_RESULT_MAX_CHARS` (Pi ``TOOL_RESULT_MAX_CHARS = 2000``).
    """

    from aelix_ai.messages import (
        AssistantMessage,
        ImageContent,
        TextContent,
        ThinkingContent,
        ToolCallContent,
        ToolResultMessage,
        UserMessage,
    )

    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            parts: list[str] = []
            for block in msg.content or []:
                if isinstance(block, TextContent):
                    parts.append(block.text or "")
                elif isinstance(block, ImageContent):
                    parts.append("[image]")
            lines.append(f"User: {' '.join(p for p in parts if p)}")
        elif isinstance(msg, AssistantMessage):
            parts = []
            for block in msg.content or []:
                if isinstance(block, TextContent):
                    if block.text:
                        parts.append(block.text)
                elif isinstance(block, ThinkingContent):
                    parts.append(f"[thinking] {block.thinking or ''}")
                elif isinstance(block, ToolCallContent):
                    args = json.dumps(block.input or {})
                    parts.append(f"[tool_call {block.tool_name or ''}] {args}")
            lines.append(f"Assistant: {' '.join(parts)}")
        elif isinstance(msg, ToolResultMessage):
            text_parts: list[str] = []
            for block in msg.content or []:
                if isinstance(block, TextContent):
                    text_parts.append(block.text or "")
                elif isinstance(block, ImageContent):
                    text_parts.append("[image]")
            text = " ".join(p for p in text_parts if p)
            truncated = False
            if len(text) > TOOL_RESULT_MAX_CHARS:
                text = text[:TOOL_RESULT_MAX_CHARS]
                truncated = True
            if truncated:
                text += " … (truncated)"
            label = "Tool result [error]" if msg.is_error else "Tool result"
            lines.append(f"{label}: {text}")
    return "\n".join(lines)


def _find_cut_index(messages: list[Any], keep_recent_tokens: int) -> int | None:
    """Pi ``findCutPoint`` (simplified backward token-budget walk).

    Walk ``messages`` backward accumulating :func:`estimate_tokens`. Once the
    accumulated estimate reaches ``keep_recent_tokens``, that message is the
    boundary: messages BEFORE it are summarized, it and everything after stay.
    A :class:`ToolResultMessage` must never be the first kept message (it must
    stay with its tool call), so the cut moves earlier to the preceding
    non-tool-result message when it lands on one. Returns ``None`` when the
    total estimate never reaches the budget (nothing worth compacting).
    """

    from aelix_ai.messages import ToolResultMessage

    accumulated = 0
    cut_index: int | None = None
    for i in range(len(messages) - 1, -1, -1):
        accumulated += estimate_tokens(messages[i])
        if accumulated >= keep_recent_tokens:
            cut_index = i
            break
    if cut_index is None:
        return None
    # Never let a ToolResultMessage be the first kept message — back the cut
    # up until the first kept message is not a tool result (or we run out).
    while cut_index < len(messages) and isinstance(
        messages[cut_index], ToolResultMessage
    ):
        cut_index -= 1
    if cut_index <= 0:
        return None
    return cut_index


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

    _ = custom_instructions  # accepted for Pi-parity signature (threaded at summarize time)

    from aelix_agent_core.session.context import (
        create_branch_summary_message,
        create_custom_message,
    )

    # Build (entry_id, message) pairs in the same order build_session_context
    # would — type=="message" → entry.message; type=="custom_message" → its
    # conversion; type=="branch_summary" → create_branch_summary_message.
    # Non-message entry types (thinking_level_change, model_change, compaction)
    # are skipped because they produce no message slot.  The 1:1 mapping lets
    # us resolve first_kept_entry_id to the exact cut entry below.
    pairs: list[tuple[str, Any]] = []
    for entry in path_entries:
        eid = entry.id or ""
        if entry.type == "message":
            pairs.append((eid, entry.message))  # type: ignore[union-attr]
        elif entry.type == "custom_message":
            msg = create_custom_message(
                entry.custom_type,  # type: ignore[union-attr]
                entry.content,  # type: ignore[union-attr]
                entry.display,  # type: ignore[union-attr]
                entry.details,  # type: ignore[union-attr]
                entry.timestamp,
            )
            pairs.append((eid, msg))
        elif entry.type == "branch_summary" and getattr(entry, "summary", None):
            msg = create_branch_summary_message(
                entry.summary,  # type: ignore[union-attr]
                entry.from_id,  # type: ignore[union-attr]
                entry.timestamp,
            )
            pairs.append((eid, msg))

    if not pairs:
        return None

    msgs = [m for _, m in pairs]
    cut_index = _find_cut_index(msgs, KEEP_RECENT_TOKENS)
    if cut_index is None or cut_index <= 0:
        # Below the keep-recent budget (or no valid cut after tool-result
        # back-up) — nothing worth compacting.
        return None

    messages_to_summarize = msgs[:cut_index]
    if not messages_to_summarize:
        return None

    # first_kept_entry_id = the entry id of the first KEPT message (pairs[cut_index]).
    # build_session_context uses this to skip everything before it, so the
    # summarized prefix is dropped from the live context.
    first_kept_entry_id = pairs[cut_index][0]

    tokens_before = estimate_context_tokens(msgs).tokens

    previous_summary: str | None = None
    latest_compaction = get_latest_compaction_entry(path_entries)
    if latest_compaction is not None:
        previous_summary = getattr(latest_compaction, "summary", None)

    return CompactionPreparation(
        first_kept_entry_id=first_kept_entry_id,
        messages_to_summarize=messages_to_summarize,
        tokens_before=tokens_before,
        previous_summary=previous_summary,
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

    summary = await _generate_summary(
        model, get_api_key_and_headers, preparation, custom_instructions
    )
    return CompactResult(
        summary=summary,
        first_kept_entry_id=preparation.first_kept_entry_id,
        tokens_before=preparation.tokens_before,
        details=None,
    )


async def _generate_summary(
    model: Model,
    get_api_key_and_headers: Callable[..., Any],
    preparation: CompactionPreparation,
    custom_instructions: str | None,
) -> str:
    """Pi ``generateSummary`` (``compaction.ts:541-606``).

    Serializes ``preparation.messages_to_summarize`` as DATA, builds the
    summarization context (system prompt + a single user message wrapping the
    conversation in ``<conversation>`` tags), resolves auth, streams via
    :func:`aelix_ai.streaming.stream_simple`, and accumulates the assistant
    text. Raises :class:`AgentHarnessError("compaction", ...)` when the
    provider reports an error or yields an empty summary.
    """

    from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
    from aelix_ai.streaming import (
        AssistantDoneEvent,
        AssistantErrorEvent,
        Context,
        SimpleStreamOptions,
        TextDeltaEvent,
        stream_simple,
    )

    from aelix_agent_core.harness.core import AgentHarnessError

    base_prompt = (
        UPDATE_SUMMARIZATION_PROMPT
        if preparation.previous_summary
        else SUMMARIZATION_PROMPT
    )
    serialized = _serialize_conversation(preparation.messages_to_summarize)

    user_text = f"<conversation>\n{serialized}\n</conversation>"
    if preparation.previous_summary:
        user_text += (
            f"\n\n<previous-summary>\n{preparation.previous_summary}\n"
            "</previous-summary>"
        )
    user_text += f"\n\n{base_prompt}"
    if custom_instructions:
        user_text += f"\n\nAdditional focus: {custom_instructions}"

    auth = get_api_key_and_headers(model)
    if inspect.isawaitable(auth):
        auth = await auth
    # Coerce non-dict (e.g. None returned by a buggy auth callback) to empty.
    auth = auth if isinstance(auth, dict) else {}

    context = Context(
        system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
        messages=[UserMessage(content=[TextContent(text=user_text)])],
        tools=[],
    )
    # NOTE: Pi caps the summarization call at floor(0.8 * reserveTokens) output
    # tokens via SimpleStreamOptions.maxTokens.  Aelix's SimpleStreamOptions
    # has no max_tokens field yet (infra gap) — this is a known divergence.
    options = SimpleStreamOptions(
        api_key=auth.get("apiKey"),
        headers=auth.get("headers") or {},
    )

    summary = ""
    done_message: AssistantMessage | None = None
    iterator = await stream_simple(model, context, options)
    async for event in iterator:
        if isinstance(event, TextDeltaEvent):
            summary += event.delta
        elif isinstance(event, AssistantErrorEvent):
            # Provider adapters emit AssistantErrorEvent (not a done event with
            # stop_reason="error") when the stream fails — raise immediately so
            # partial accumulated text is never silently treated as a summary.
            raise AgentHarnessError(
                "compaction",
                getattr(event, "error_message", None) or "summarization failed",
            )
        elif isinstance(event, AssistantDoneEvent):
            done_message = event.message

    if done_message is not None:
        # Belt-and-suspenders: some adapters may surface errors via a done
        # message with stop_reason="error" rather than AssistantErrorEvent.
        if done_message.stop_reason == "error":
            raise AgentHarnessError(
                "compaction",
                done_message.error_message or "summarization failed",
            )
        # Prefer the terminal message's accumulated TextContent (authoritative)
        # over the delta accumulation when present.
        text_blocks = [
            block.text
            for block in (done_message.content or [])
            if isinstance(block, TextContent) and block.text
        ]
        if text_blocks:
            summary = "\n".join(text_blocks)

    summary = summary.strip()
    if not summary:
        raise AgentHarnessError("compaction", "summarization produced empty summary")
    return summary


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
    "KEEP_RECENT_TOKENS",
    "SUMMARIZATION_PROMPT",
    "SUMMARIZATION_SYSTEM_PROMPT",
    "TOOL_RESULT_MAX_CHARS",
    "UPDATE_SUMMARIZATION_PROMPT",
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
