"""Pi-parity ``branch_summarization`` module (Sprint 4b / Phase 2.2.2).

Pi source: ``packages/agent/src/harness/compaction/branch-summarization.ts``
at SHA ``734e08e``. Sprint 4b ships the data-shape parity needed by
:meth:`AgentHarness.navigate_tree` plus a minimum
:func:`collect_entries_for_branch_summary` / :func:`generate_branch_summary`
surface.

Per P-14 (W1 finding), Aelix does NOT add summarizer callbacks. Pi calls
``generateBranchSummary()`` inline using ``this.model`` +
``await this.getApiKeyAndHeaders(model)``. :func:`generate_branch_summary`
mirrors this and performs the real LLM call via
:func:`aelix_ai.streaming.stream_simple`, reusing the proven
``compaction._generate_summary`` flow (ADR-0117). A test-only
``_summarizer_override`` seam (Aelix-additive) lets fixtures substitute a
deterministic summarizer without a network call.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aelix_ai.streaming import Model

from aelix_agent_core.session.entries import BranchSummaryEntry, SessionTreeEntry

if TYPE_CHECKING:
    from aelix_agent_core.session.session import Session


# === Result dataclasses (Pi parity, ``branch-summarization.ts:23-66``) ===


@dataclass(frozen=True)
class BranchSummaryPreparation:
    """Pi ``preparation`` object (``agent-harness.ts:771-780``).

    Pi's preparation is an inline object literal at the navigateTree call
    site; Aelix promotes it to a named dataclass so :meth:`Session.move_to`
    callers + hook handlers have a typed surface.
    """

    target_id: str
    old_leaf_id: str | None
    common_ancestor_id: str | None
    entries_to_summarize: list[SessionTreeEntry] = field(default_factory=list)
    user_wants_summary: bool = False
    custom_instructions: str | None = None
    replace_instructions: bool | None = None
    label: str | None = None


# Pi exposes ``SummaryEntry`` as ``BranchSummaryEntry`` directly — the
# narrowing happens at the navigateTree return value (`entry?.type ===
# "branch_summary"`). Aelix mirrors that by re-exporting the entry type as a
# domain-specific alias used by the hook payload + result types.
SummaryEntry = BranchSummaryEntry


# === Test-only seam (Aelix-additive — ADR-0023 §"Aelix-additive divergences") ===

BranchSummarizerOverride = Callable[
    [Model, list[SessionTreeEntry], str | None],
    "Awaitable[str] | str",
]


# === Prompts (Pi parity, ``branch-summarization.ts:165-198``, verbatim) ===

BRANCH_SUMMARY_PREAMBLE = (
    "The user explored a different conversation branch before returning here.\n"
    "Summary of that exploration:\n\n"
)

BRANCH_SUMMARY_PROMPT = """Create a structured summary of this conversation branch for context when returning later.

Use this EXACT format:

## Goal
[What was the user trying to accomplish in this branch?]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Work that was started but not finished]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [What should happen next to continue this work]

Keep each section concise. Preserve exact file paths, function names, and error messages."""


def _message_from_entry(entry: SessionTreeEntry) -> Any | None:
    """Pi ``getMessageFromEntry`` (``branch-summarization.ts:101-121``).

    Converts a tree entry to an LLM message, or ``None`` for control entries
    and ``toolResult`` messages (which Pi excludes from branch summaries).
    """

    from aelix_ai.messages import ToolResultMessage

    from aelix_agent_core.session.context import (
        create_branch_summary_message,
        create_compaction_summary_message,
        create_custom_message,
    )

    if entry.type == "message":
        msg = entry.message  # type: ignore[union-attr]
        if isinstance(msg, ToolResultMessage) or getattr(msg, "role", None) == "toolResult":
            return None
        return msg
    if entry.type == "custom_message":
        return create_custom_message(
            entry.custom_type,  # type: ignore[union-attr]
            entry.content,  # type: ignore[union-attr]
            entry.display,  # type: ignore[union-attr]
            entry.details,  # type: ignore[union-attr]
            entry.timestamp,
        )
    if entry.type == "branch_summary":
        # Pi includes branch_summary entries unconditionally (getMessageFromEntry
        # :107-108); coerce a None summary to "" to avoid a str+None crash in
        # create_branch_summary_message (Pi's TS tolerates undefined).
        return create_branch_summary_message(
            entry.summary or "",  # type: ignore[union-attr]
            entry.from_id,  # type: ignore[union-attr]
            entry.timestamp,
        )
    if entry.type == "compaction":
        return create_compaction_summary_message(
            entry.summary,  # type: ignore[union-attr]
            entry.tokens_before,  # type: ignore[union-attr]
            entry.timestamp,
        )
    # thinking_level_change / model_change / custom / label / session_info / leaf
    return None


# === Public API (Pi parity, ``branch-summarization.ts:69-98 / 199-262``) ==


async def collect_entries_for_branch_summary(
    session: Session,
    old_leaf_id: str | None,
    target_id: str,
) -> tuple[list[SessionTreeEntry], str | None]:
    """Pi ``collectEntriesForBranchSummary`` (``branch-summarization.ts:69-98``).

    Returns ``(entries_in_chronological_order, common_ancestor_id)``. The
    Pi return value is a dict; Aelix returns a tuple to keep the call site
    in :meth:`AgentHarness.navigate_tree` ergonomic.
    """

    from aelix_agent_core.session.storage import SessionError

    if not old_leaf_id:
        return [], None
    old_branch = await session.get_branch(old_leaf_id)
    old_path = {e.id for e in old_branch}
    target_path = await session.get_branch(target_id)
    common_ancestor_id: str | None = None
    for entry in reversed(target_path):
        if entry.id in old_path:
            common_ancestor_id = entry.id
            break
    entries: list[SessionTreeEntry] = []
    current: str | None = old_leaf_id
    while current and current != common_ancestor_id:
        entry = await session.get_entry(current)
        if entry is None:
            raise SessionError("invalid_session", f"Entry {current} not found")
        entries.append(entry)
        current = entry.parent_id
    entries.reverse()
    return entries, common_ancestor_id


async def generate_branch_summary(
    model: Model,
    get_api_key_and_headers: Callable[..., Any] | None,
    entries: list[SessionTreeEntry],
    custom_instructions: str | None = None,
    *,
    replace_instructions: bool | None = None,
    _summarizer_override: BranchSummarizerOverride | None = None,
) -> str:
    """Pi ``generateBranchSummary`` (``branch-summarization.ts:189-262``).

    Converts the abandoned-branch entries to messages (Pi ``getMessageFromEntry``),
    serializes them as DATA wrapped in ``<conversation>`` tags, appends the
    structured :data:`BRANCH_SUMMARY_PROMPT` (or replaces it when
    ``replace_instructions`` + ``custom_instructions``), streams via
    :func:`aelix_ai.streaming.stream_simple` reusing the proven
    ``compaction._generate_summary`` flow, and prepends
    :data:`BRANCH_SUMMARY_PREAMBLE` to the result.

    A test-only ``_summarizer_override`` seam returns a deterministic summary
    (without the preamble) for fixtures.

    Known bounded divergences from Pi (deferred, not silently dropped):

    - returns ``str``, not Pi's ``{summary, readFiles, modifiedFiles}`` — no
      file-operation extraction / ``formatFileOperations`` tail.
    - no ``prepareBranchEntries`` token-budget backward walk; all entries are
      included.
    - no ``max_tokens`` cap (``SimpleStreamOptions`` has no field — the same
      infra gap documented in ``compaction.py``; Pi uses ``maxTokens: 2048``).
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
    from aelix_agent_core.session.compaction import (
        SUMMARIZATION_SYSTEM_PROMPT,
        _serialize_conversation,
    )

    if _summarizer_override is not None:
        raw = _summarizer_override(model, entries, custom_instructions)
        if hasattr(raw, "__await__"):
            summary = await raw  # type: ignore[misc]
        else:
            summary = raw  # type: ignore[assignment]
        if not isinstance(summary, str):
            raise AgentHarnessError(
                "invalid_state",
                "_summarizer_override must return str",
            )
        return summary

    if get_api_key_and_headers is None:
        raise AgentHarnessError(
            "invalid_state",
            "generate_branch_summary requires options.get_api_key_and_headers "
            "(Phase 4 owner)",
        )

    messages = [m for e in entries if (m := _message_from_entry(e)) is not None]
    if not messages:
        return "No content to summarize"

    serialized = _serialize_conversation(messages)
    if replace_instructions and custom_instructions:
        instructions = custom_instructions
    elif custom_instructions:
        instructions = f"{BRANCH_SUMMARY_PROMPT}\n\nAdditional focus: {custom_instructions}"
    else:
        instructions = BRANCH_SUMMARY_PROMPT
    user_text = f"<conversation>\n{serialized}\n</conversation>\n\n{instructions}"

    auth = get_api_key_and_headers(model)
    if hasattr(auth, "__await__"):
        auth = await auth
    # Coerce non-dict (e.g. None from a buggy auth callback) to empty.
    auth = auth if isinstance(auth, dict) else {}

    context = Context(
        system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
        messages=[UserMessage(content=[TextContent(text=user_text)])],
        tools=[],
    )
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
            # partial accumulated text is never returned as a summary.
            raise AgentHarnessError(
                "invalid_state",
                getattr(event, "error_message", None) or "branch summary failed",
            )
        elif isinstance(event, AssistantDoneEvent):
            done_message = event.message

    if done_message is not None:
        if done_message.stop_reason == "error":
            raise AgentHarnessError(
                "invalid_state",
                done_message.error_message or "branch summary failed",
            )
        # Prefer the terminal message's TextContent (authoritative) over the
        # delta accumulation when present (Pi joins text parts with "\n").
        text_blocks = [
            block.text
            for block in (done_message.content or [])
            if isinstance(block, TextContent) and block.text
        ]
        if text_blocks:
            summary = "\n".join(text_blocks)

    return BRANCH_SUMMARY_PREAMBLE + summary


__all__ = [
    "BRANCH_SUMMARY_PREAMBLE",
    "BRANCH_SUMMARY_PROMPT",
    "BranchSummarizerOverride",
    "BranchSummaryPreparation",
    "SummaryEntry",
    "collect_entries_for_branch_summary",
    "generate_branch_summary",
]
