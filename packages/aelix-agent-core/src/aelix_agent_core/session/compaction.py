"""Pi-parity ``compaction`` module (Sprint 4b / Phase 2.2.2 — ADR-0023,
updated Sprint 6h₁₂-compaction).

Pi source: ``packages/agent/src/harness/compaction/compaction.ts`` at SHA
``734e08e``. Per P-14 (W1 finding), Aelix does NOT add summarizer callbacks
to ``AgentHarnessOptions``. Pi calls ``compact()`` inline using
``this.model`` + ``await this.getApiKeyAndHeaders(model)``; Aelix mirrors
this. A test-only ``_summarizer_override`` callable is supported
(Aelix-additive, documented in ADR-0023).

Sprint 6h₁₂-compaction landed the first real pipeline; P0 #6 (compaction
fidelity) completes it:
- entry-level ``findCutPoint`` / ``findValidCutPoints`` / ``findTurnStartIndex``
  (:func:`find_cut_point` et al.) with the control-entry back-up loop, so the
  first-kept entry is never a ``toolResult`` / control entry;
- ``prepareCompaction`` boundary-start from the previous compaction's
  ``first_kept_entry_id``, split-turn detection, and ``historyEnd``;
- split-turn (turn-prefix) summarization — :func:`_generate_turn_prefix_summary`
  combined with the history summary via the verbatim separator;
- file-op extraction — ``<read-files>`` / ``<modified-files>`` tail +
  ``details={"readFiles", "modifiedFiles"}`` (seeded from the prior compaction);
- ``max_tokens`` cap on the summary (``floor(0.8 * RESERVE_TOKENS)``) and the
  turn-prefix (``floor(0.5 * RESERVE_TOKENS)``) via the new
  :class:`SimpleStreamOptions.max_tokens` field (honored REAL at the provider
  payload level).
- ``first_kept_entry_id`` maps to the **cut entry** (not the head) so
  :func:`build_session_context` drops the summarized prefix correctly.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
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

# Pi ``DEFAULT_COMPACTION_SETTINGS.reserveTokens`` (compaction.ts). The summary
# output cap is ``floor(0.8 * RESERVE_TOKENS)`` and the turn-prefix cap is
# ``floor(0.5 * RESERVE_TOKENS)`` (P0 #6). Mirrors
# ``core._AUTO_COMPACT_RESERVE_TOKENS`` (same value, separate constants because
# core has no SettingsManager).
RESERVE_TOKENS = 16384

# Pi ``TURN_PREFIX_SUMMARIZATION_PROMPT`` (``compaction.ts``) — verbatim. Used
# when the cut lands mid-turn (a "split turn"): the turn PREFIX is summarized
# separately and combined with the history summary so the retained SUFFIX
# (recent work) keeps its context.
TURN_PREFIX_SUMMARIZATION_PROMPT = """This is the PREFIX of a turn that was too large to keep. The SUFFIX (recent work) is retained.

Summarize the prefix to provide context for the retained suffix:

## Original Request
[What did the user ask for in this turn?]

## Early Progress
- [Key decisions and work done in the prefix]

## Context for Suffix
- [Information needed to understand the retained recent work]

Be concise. Focus on what's needed to understand the kept suffix."""


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
    # ``file_ops`` is the P0 #6 :class:`FileOperations` (read/written/edited
    # path sets) computed in :func:`prepare_compaction`; ``compact`` folds it
    # into the ``<read-files>`` / ``<modified-files>`` summary tail + details.
    # Defaults to ``None`` so legacy test constructions (which pass only the
    # first few fields) still work — ``compact`` coerces ``None`` to an empty
    # :func:`create_file_ops`. ``settings`` remains an ``Any`` placeholder
    # (aelix uses module constants ``RESERVE_TOKENS`` / ``KEEP_RECENT_TOKENS``
    # instead of a per-call CompactionSettings object).
    file_ops: FileOperations | None = None
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


# === File-operation extraction (Pi parity, ``utils.ts``) ===================
#
# Pi tracks which files a turn read / wrote / edited and appends
# ``<read-files>`` / ``<modified-files>`` tags to the summary (so the kept
# context still names the files the prefix touched). aelix maps pi
# ``block.name`` → :attr:`ToolCallContent.tool_name` and pi ``block.arguments``
# → :attr:`ToolCallContent.input` (``input["path"]``).


@dataclass
class FileOperations:
    """Pi ``FileOperations`` (``utils.ts``) — three disjoint path sets."""

    read: set[str] = field(default_factory=set)
    written: set[str] = field(default_factory=set)
    edited: set[str] = field(default_factory=set)


def create_file_ops() -> FileOperations:
    """Pi ``createFileOps`` (``utils.ts``)."""

    return FileOperations()


def extract_file_ops_from_message(message: Any, file_ops: FileOperations) -> None:
    """Pi ``extractFileOpsFromMessage`` (``utils.ts``).

    Scans an assistant message's :class:`ToolCallContent` blocks; a
    ``read`` / ``write`` / ``edit`` tool call whose ``input["path"]`` is a
    string is recorded in the matching set. Non-assistant messages and blocks
    without a string ``path`` are ignored (pi guards identically).
    """

    from aelix_ai.messages import AssistantMessage, ToolCallContent

    if not isinstance(message, AssistantMessage):
        # Pi: ``if (message.role !== "assistant") return;``
        return
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, ToolCallContent):
            continue
        args = block.input or {}
        path = args.get("path") if isinstance(args, dict) else None
        if not isinstance(path, str) or not path:
            continue
        name = block.tool_name or ""
        if name == "read":
            file_ops.read.add(path)
        elif name == "write":
            file_ops.written.add(path)
        elif name == "edit":
            file_ops.edited.add(path)


def compute_file_lists(file_ops: FileOperations) -> tuple[list[str], list[str]]:
    """Pi ``computeFileLists`` (``utils.ts``).

    Returns ``(read_files, modified_files)``. ``modified`` = written ∪ edited;
    ``read_files`` excludes anything that was also modified (a file you wrote
    is reported as modified, not read). Both lists are sorted.
    """

    modified = set(file_ops.edited) | set(file_ops.written)
    read_only = sorted(f for f in file_ops.read if f not in modified)
    modified_files = sorted(modified)
    return read_only, modified_files


def format_file_operations(read_files: list[str], modified_files: list[str]) -> str:
    """Pi ``formatFileOperations`` (``utils.ts``).

    Emits the ``<read-files>`` / ``<modified-files>`` tail (prefixed with two
    newlines) appended to a summary. Returns ``""`` when both lists are empty.
    """

    sections: list[str] = []
    if read_files:
        sections.append("<read-files>\n" + "\n".join(read_files) + "\n</read-files>")
    if modified_files:
        sections.append(
            "<modified-files>\n" + "\n".join(modified_files) + "\n</modified-files>"
        )
    if not sections:
        return ""
    return "\n\n" + "\n\n".join(sections)


def _extract_file_operations(
    messages: list[Any],
    entries: list[SessionTreeEntry],
    prev_compaction_index: int,
) -> FileOperations:
    """Pi ``extractFileOperations`` (``compaction.ts``).

    Seeds the file-op sets from the previous (non-hook) compaction entry's
    ``details`` (so file references carry across consecutive compactions),
    then folds in every message's tool calls. Hook-substituted compactions are
    NOT trusted as a seed (pi: ``!prevCompaction.fromHook``).
    """

    file_ops = create_file_ops()
    if prev_compaction_index >= 0:
        prev_compaction = entries[prev_compaction_index]
        from_hook = getattr(prev_compaction, "from_hook", None)
        details = getattr(prev_compaction, "details", None)
        if not from_hook and isinstance(details, dict):
            read_seed = details.get("readFiles")
            if isinstance(read_seed, list):
                for f in read_seed:
                    if isinstance(f, str):
                        file_ops.read.add(f)
            modified_seed = details.get("modifiedFiles")
            if isinstance(modified_seed, list):
                for f in modified_seed:
                    if isinstance(f, str):
                        file_ops.edited.add(f)
    for msg in messages:
        extract_file_ops_from_message(msg, file_ops)
    return file_ops


# === Entry-level cut-point (Pi parity, ``compaction.ts``) ==================
#
# Pi cuts at the ENTRY level (so control entries — model_change /
# thinking_level_change — and tool-result roles never become the first kept
# entry), and snaps the cut to a turn boundary (split-turn detection).

# Entry types that are NOT a ``message`` but ARE legal cut points (pi's
# trailing ``branch_summary`` / ``custom_message`` push). Message-role cut
# legality is handled separately because ``toolResult`` is never a cut point.
_CONTROL_CUT_POINT_TYPES = ("branch_summary", "custom_message")


def find_valid_cut_points(
    entries: list[SessionTreeEntry], start_index: int, end_index: int
) -> list[int]:
    """Pi ``findValidCutPoints`` (``compaction.ts``).

    Returns the entry indices in ``[start_index, end_index)`` that are legal
    cut points. A ``message`` entry is a cut point UNLESS its role is
    ``toolResult`` (a tool result must stay with its tool call). The
    ``branch_summary`` / ``custom_message`` control entries are also valid cut
    points; all other control entries (model_change / thinking_level_change /
    compaction / label / session_info / leaf / custom) are not.
    """

    cut_points: list[int] = []
    for i in range(start_index, end_index):
        entry = entries[i]
        etype = entry.type
        if etype == "message":
            role = getattr(getattr(entry, "message", None), "role", None)
            if role != "toolResult":
                cut_points.append(i)
        elif etype in _CONTROL_CUT_POINT_TYPES:
            cut_points.append(i)
    return cut_points


def find_turn_start_index(
    entries: list[SessionTreeEntry], entry_index: int, start_index: int
) -> int:
    """Pi ``findTurnStartIndex`` (``compaction.ts``).

    Walks backward from ``entry_index`` (inclusive) to ``start_index`` looking
    for the entry that STARTS the current turn: a ``branch_summary`` /
    ``custom_message`` entry, or a ``message`` whose role is ``user`` or
    ``bashExecution``. Returns ``-1`` when none is found.
    """

    for i in range(entry_index, start_index - 1, -1):
        entry = entries[i]
        etype = entry.type
        if etype in _CONTROL_CUT_POINT_TYPES:
            return i
        if etype == "message":
            role = getattr(getattr(entry, "message", None), "role", None)
            if role in ("user", "bashExecution"):
                return i
    return -1


@dataclass(frozen=True)
class _CutPointResult:
    """Pi ``CutPointResult`` (``compaction.ts``)."""

    first_kept_entry_index: int
    turn_start_index: int
    is_split_turn: bool


def find_cut_point(
    entries: list[SessionTreeEntry],
    start_index: int,
    end_index: int,
    keep_recent_tokens: int,
) -> _CutPointResult:
    """Pi ``findCutPoint`` (``compaction.ts``).

    Picks the entry index where the kept-recent suffix begins. Walks the
    message entries backward accumulating :func:`estimate_tokens`; once the
    accumulated estimate reaches ``keep_recent_tokens`` it snaps to the first
    valid cut point at or after that message. The cut is then backed up over
    control entries so the first-kept entry is a ``message`` or ``compaction``.
    Finally, if the cut does NOT land on a ``user`` message, the turn-start is
    located and a split turn is reported.
    """

    cut_points = find_valid_cut_points(entries, start_index, end_index)
    if not cut_points:
        return _CutPointResult(
            first_kept_entry_index=start_index,
            turn_start_index=-1,
            is_split_turn=False,
        )

    cut_index = cut_points[0]
    accumulated = 0
    for i in range(end_index - 1, start_index - 1, -1):
        entry = entries[i]
        if entry.type != "message":
            continue
        accumulated += estimate_tokens(entry.message)  # type: ignore[union-attr]
        if accumulated >= keep_recent_tokens:
            for c in cut_points:
                if c >= i:
                    cut_index = c
                    break
            break

    # Back the cut up over control entries so first-kept is message/compaction.
    while cut_index > start_index:
        prev_entry = entries[cut_index - 1]
        if prev_entry.type == "compaction":
            break
        if prev_entry.type == "message":
            break
        cut_index -= 1

    cut_entry = entries[cut_index]
    is_user_message = (
        cut_entry.type == "message"
        and getattr(getattr(cut_entry, "message", None), "role", None) == "user"
    )
    turn_start_index = (
        -1
        if is_user_message
        else find_turn_start_index(entries, cut_index, start_index)
    )
    return _CutPointResult(
        first_kept_entry_index=cut_index,
        turn_start_index=turn_start_index,
        is_split_turn=(not is_user_message and turn_start_index != -1),
    )


def _get_message_from_entry(entry: SessionTreeEntry) -> Any | None:
    """Pi ``getMessageFromEntry`` (compaction variant — ``compaction.ts``).

    INCLUDES ``message`` entries verbatim (so ``toolResult``-role messages are
    summarized, unlike the branch-summary variant which drops them), and
    converts ``custom_message`` / ``branch_summary`` / ``compaction`` entries
    to their rendered message. Other control entries map to ``None``.
    """

    from aelix_agent_core.session.context import (
        create_branch_summary_message,
        create_compaction_summary_message,
        create_custom_message,
    )

    etype = entry.type
    if etype == "message":
        return entry.message  # type: ignore[union-attr]
    if etype == "custom_message":
        return create_custom_message(
            entry.custom_type,  # type: ignore[union-attr]
            entry.content,  # type: ignore[union-attr]
            entry.display,  # type: ignore[union-attr]
            entry.details,  # type: ignore[union-attr]
            entry.timestamp,
        )
    if etype == "branch_summary":
        return create_branch_summary_message(
            entry.summary or "",  # type: ignore[union-attr]
            entry.from_id,  # type: ignore[union-attr]
            entry.timestamp,
        )
    if etype == "compaction":
        return create_compaction_summary_message(
            entry.summary,  # type: ignore[union-attr]
            entry.tokens_before,  # type: ignore[union-attr]
            entry.timestamp,
        )
    return None


def _get_message_from_entry_for_compaction(entry: SessionTreeEntry) -> Any | None:
    """Pi ``getMessageFromEntryForCompaction`` (``compaction.ts``).

    Like :func:`_get_message_from_entry` but DROPS ``compaction`` entries (a
    prior compaction summary inside the summarized prefix must not be re-fed to
    the summarizer; the previous summary is threaded separately).
    """

    if entry.type == "compaction":
        return None
    return _get_message_from_entry(entry)


# === Public API (Pi parity, ``compaction.ts:541-606`` + ``:626-705``) =====


def prepare_compaction(
    path_entries: list[SessionTreeEntry],
    custom_instructions: str | None = None,
) -> CompactionPreparation | None:
    """Pi ``prepareCompaction`` (``compaction.ts``).

    Full entry-level port (P0 #6). Computes the cut at the ENTRY level via
    :func:`find_cut_point`, snapping to a turn boundary (split-turn) so the
    first-kept entry is never a ``toolResult``/control entry. Threads:

    - ``boundary_start`` = the previous compaction's ``first_kept_entry_id``
      index (so a second compaction only re-summarizes what the first kept),
      falling back to ``prev_compaction_index + 1``;
    - ``history_end`` = ``turn_start_index`` on a split turn, else the cut;
    - ``messages_to_summarize`` over ``[boundary_start, history_end)`` and
      ``turn_prefix_messages`` over ``[turn_start_index, cut)`` (split turn
      only), both via :func:`_get_message_from_entry_for_compaction` (which
      INCLUDES ``toolResult`` messages but EXCLUDES prior compaction entries);
    - ``file_ops`` seeded from the previous compaction's ``details`` then
      folded over the summarized + turn-prefix messages.

    Returns ``None`` when there is nothing to compact (empty entries, the tail
    is already a compaction, or the cut yields no first-kept id) so the harness
    short-circuits with "Nothing to compact".

    The ``custom_instructions`` parameter is accepted to match the call site
    (``agent-harness.ts``) but threaded into the summarizer call separately.
    """

    if not path_entries:
        return None
    if path_entries[-1].type == "compaction":
        # Already compacted to the tail.
        return None

    _ = custom_instructions  # accepted for Pi-parity signature (threaded at summarize time)

    # Locate the most recent compaction so a second pass only re-summarizes
    # what the first kept (boundary_start) + can seed file-ops from its details.
    prev_compaction_index = -1
    for i in range(len(path_entries) - 1, -1, -1):
        if path_entries[i].type == "compaction":
            prev_compaction_index = i
            break

    previous_summary: str | None = None
    boundary_start = 0
    if prev_compaction_index >= 0:
        prev_compaction = path_entries[prev_compaction_index]
        previous_summary = getattr(prev_compaction, "summary", None)
        prev_first_kept_id = getattr(prev_compaction, "first_kept_entry_id", None)
        first_kept_index = -1
        for idx, e in enumerate(path_entries):
            if e.id == prev_first_kept_id:
                first_kept_index = idx
                break
        boundary_start = (
            first_kept_index
            if first_kept_index >= 0
            else prev_compaction_index + 1
        )

    boundary_end = len(path_entries)

    # tokens_before is over the WHOLE live context (build_session_context honors
    # the prior compaction boundary already), matching pi's
    # estimateContextTokens(buildSessionContext(pathEntries).messages).
    from aelix_agent_core.session.context import build_session_context

    tokens_before = estimate_context_tokens(
        build_session_context(path_entries).messages
    ).tokens

    cut_point = find_cut_point(
        path_entries, boundary_start, boundary_end, KEEP_RECENT_TOKENS
    )
    first_kept_entry = path_entries[cut_point.first_kept_entry_index]
    if not first_kept_entry.id:
        # Pi raises CompactionError("invalid_session"); aelix returns None so
        # the harness surfaces the standard "Nothing to compact" path rather
        # than a hard error on an un-migrated session.
        return None
    first_kept_entry_id = first_kept_entry.id

    history_end = (
        cut_point.turn_start_index
        if cut_point.is_split_turn
        else cut_point.first_kept_entry_index
    )

    messages_to_summarize: list[AgentMessage] = []
    for i in range(boundary_start, history_end):
        msg = _get_message_from_entry_for_compaction(path_entries[i])
        if msg is not None:
            messages_to_summarize.append(msg)

    turn_prefix_messages: list[AgentMessage] = []
    if cut_point.is_split_turn:
        for i in range(
            cut_point.turn_start_index, cut_point.first_kept_entry_index
        ):
            msg = _get_message_from_entry_for_compaction(path_entries[i])
            if msg is not None:
                turn_prefix_messages.append(msg)

    # Nothing actually summarizable (the kept-recent budget covers everything
    # in-bounds) — short-circuit so the harness reports "Nothing to compact".
    if not messages_to_summarize and not turn_prefix_messages:
        return None

    file_ops = _extract_file_operations(
        messages_to_summarize, path_entries, prev_compaction_index
    )
    if cut_point.is_split_turn:
        for msg in turn_prefix_messages:
            extract_file_ops_from_message(msg, file_ops)

    return CompactionPreparation(
        first_kept_entry_id=first_kept_entry_id,
        messages_to_summarize=messages_to_summarize,
        turn_prefix_messages=turn_prefix_messages,
        is_split_turn=cut_point.is_split_turn,
        tokens_before=tokens_before,
        previous_summary=previous_summary,
        file_ops=file_ops,
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

    # Pi parity (``compaction.ts`` ``compact``): on a split turn, summarize the
    # history prefix and the turn prefix concurrently (asyncio.gather), then
    # combine with the verbatim separator. When the history is empty (the cut
    # is at the very start of a fresh turn) pi substitutes "No prior history.".
    if preparation.is_split_turn and preparation.turn_prefix_messages:
        if preparation.messages_to_summarize:
            history_coro = _generate_summary(
                model, get_api_key_and_headers, preparation, custom_instructions
            )
        else:
            history_coro = _resolved("No prior history.")
        turn_prefix_coro = _generate_turn_prefix_summary(
            model, get_api_key_and_headers, preparation.turn_prefix_messages
        )
        history_summary, turn_prefix_summary = await asyncio.gather(
            history_coro, turn_prefix_coro
        )
        summary = (
            f"{history_summary}\n\n---\n\n"
            f"**Turn Context (split turn):**\n\n{turn_prefix_summary}"
        )
    else:
        summary = await _generate_summary(
            model, get_api_key_and_headers, preparation, custom_instructions
        )

    file_ops = preparation.file_ops or create_file_ops()
    read_files, modified_files = compute_file_lists(file_ops)
    summary += format_file_operations(read_files, modified_files)
    return CompactResult(
        summary=summary,
        first_kept_entry_id=preparation.first_kept_entry_id,
        tokens_before=preparation.tokens_before,
        details={"readFiles": read_files, "modifiedFiles": modified_files},
    )


async def _resolved(value: str) -> str:
    """Awaitable wrapper so :func:`asyncio.gather` can mix a constant with a
    real coroutine (pi's ``Promise.resolve(ok("No prior history."))``)."""

    return value


def _summary_max_tokens(model: Model, fraction: float) -> int:
    """Pi parity: ``Math.min(floor(fraction * reserveTokens), model.maxTokens
    > 0 ? model.maxTokens : +Infinity)``.

    Used for the summary cap (``fraction = 0.8``) and the turn-prefix cap
    (``fraction = 0.5``). ``model.max_tokens == 0`` means "no model cap"
    (``+Infinity``), so the floor-of-reserve value stands.
    """

    cap = math.floor(fraction * RESERVE_TOKENS)
    model_max = getattr(model, "max_tokens", 0) or 0
    if model_max > 0:
        cap = min(cap, model_max)
    return cap


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
    # Pi caps the summarization call at floor(0.8 * reserveTokens) output tokens
    # (clamped by model.maxTokens when > 0). Wired through the new
    # SimpleStreamOptions.max_tokens (P0 #6) so the cap reaches the adapter.
    options = SimpleStreamOptions(
        api_key=auth.get("apiKey"),
        headers=auth.get("headers") or {},
        max_tokens=_summary_max_tokens(model, 0.8),
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


async def _generate_turn_prefix_summary(
    model: Model,
    get_api_key_and_headers: Callable[..., Any],
    messages: list[AgentMessage],
) -> str:
    """Pi ``generateTurnPrefixSummary`` (``compaction.ts``).

    Summarizes the PREFIX of a split turn with
    :data:`TURN_PREFIX_SUMMARIZATION_PROMPT` (no previous-summary / custom-focus
    threading — pi keeps it minimal). Caps output at
    ``floor(0.5 * reserveTokens)`` (clamped by ``model.max_tokens`` when > 0)
    via :class:`SimpleStreamOptions.max_tokens` (P0 #6). Shares the
    ``AssistantErrorEvent``-raises + terminal-text-preferred event loop.
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

    serialized = _serialize_conversation(messages)
    user_text = (
        f"<conversation>\n{serialized}\n</conversation>\n\n"
        f"{TURN_PREFIX_SUMMARIZATION_PROMPT}"
    )

    auth = get_api_key_and_headers(model)
    if inspect.isawaitable(auth):
        auth = await auth
    auth = auth if isinstance(auth, dict) else {}

    context = Context(
        system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
        messages=[UserMessage(content=[TextContent(text=user_text)])],
        tools=[],
    )
    options = SimpleStreamOptions(
        api_key=auth.get("apiKey"),
        headers=auth.get("headers") or {},
        max_tokens=_summary_max_tokens(model, 0.5),
    )

    summary = ""
    done_message: AssistantMessage | None = None
    iterator = await stream_simple(model, context, options)
    async for event in iterator:
        if isinstance(event, TextDeltaEvent):
            summary += event.delta
        elif isinstance(event, AssistantErrorEvent):
            raise AgentHarnessError(
                "compaction",
                getattr(event, "error_message", None)
                or "turn-prefix summarization failed",
            )
        elif isinstance(event, AssistantDoneEvent):
            done_message = event.message

    if done_message is not None:
        if done_message.stop_reason == "error":
            raise AgentHarnessError(
                "compaction",
                done_message.error_message or "turn-prefix summarization failed",
            )
        text_blocks = [
            block.text
            for block in (done_message.content or [])
            if isinstance(block, TextContent) and block.text
        ]
        if text_blocks:
            summary = "\n".join(text_blocks)

    return summary.strip()


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
    # Issue #4 (FU2 Guard-B): the index of the message whose usage seeded the
    # estimate (pi ``lastUsageIndex``), so a caller can verify that source
    # message is post-compaction before re-triggering compaction. ``None`` when
    # no valid-usage assistant was found (the all-heuristic estimate).
    last_usage_index: int | None = None


def _valid_assistant_usage(msg: Any) -> dict[str, Any] | None:
    """Pi parity: ``getAssistantUsage`` (``compaction.ts:121-134``).

    pi #5526. Return an assistant turn's :attr:`AssistantMessage.usage`
    ONLY when the message is a non-``"aborted"`` / non-``"error"`` assistant
    whose usage is present AND sums to a POSITIVE token count
    (``calculateContextTokens(usage) > 0``). A truncated / malformed response
    can leave an assistant message whose ``usage`` is missing or all-zero;
    trusting that block would UNDER-estimate the live context and can suppress
    threshold auto-compaction. Callers walk back to the previous VALID
    assistant instead (returns :data:`None` here so the reverse walk
    continues).
    """

    from aelix_ai.messages import AssistantMessage

    if not isinstance(msg, AssistantMessage):
        return None
    if getattr(msg, "stop_reason", None) in ("aborted", "error"):
        return None
    usage = getattr(msg, "usage", None)
    if not usage:
        return None
    if calculate_context_tokens(usage) <= 0:
        return None
    return usage


def estimate_context_tokens(messages: list[Any]) -> _EstimateResult:
    """Pi parity: ``estimateContextTokens`` (``compaction.ts:186-214``).

    Sprint 6h₅c (ADR-0085, P-369). Walk ``messages`` in reverse, find the
    last assistant message whose usage is *valid* per
    :func:`_valid_assistant_usage` (pi #5526 — non-aborted/non-error AND a
    positive token sum), and add that assistant turn's
    :attr:`AssistantMessage.usage` tokens to the heuristic estimate for any
    trailing messages. When no eligible assistant message is found, the
    result is the heuristic estimate over every message.

    pi #5526: previously the walk stopped at the first non-aborted/non-error
    assistant even when its usage was all-zero / malformed (a truncated
    response), under-reporting the context. The validity guard now skips
    those and continues back to the previous VALID assistant.
    """

    last_idx: int | None = None
    last_usage: dict[str, Any] | None = None
    for i in range(len(messages) - 1, -1, -1):
        usage = _valid_assistant_usage(messages[i])
        if usage is not None:
            last_idx = i
            last_usage = usage
            break
    if last_idx is None:
        return _EstimateResult(tokens=sum(estimate_tokens(m) for m in messages))
    usage_tokens = calculate_context_tokens(last_usage)
    trailing = sum(estimate_tokens(m) for m in messages[last_idx + 1 :])
    return _EstimateResult(tokens=usage_tokens + trailing, last_usage_index=last_idx)


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


def get_latest_compaction_boundary_ms(branch_entries: list[Any]) -> float | None:
    """Issue #4 (FU2): the latest compaction entry's timestamp as unix-millis.

    Pi parity: ``new Date(getLatestCompactionEntry(...).timestamp).getTime()`` —
    the staleness boundary a message's :attr:`AssistantMessage.timestamp` must be
    AFTER to count as post-compaction. Returns ``None`` when there is no
    compaction entry yet (no boundary to enforce) or the entry timestamp does not
    parse. The :func:`_iso_to_unix_ms` import is function-local to avoid a
    ``session.compaction`` <-> ``session.context`` module-load cycle.
    """

    entry = get_latest_compaction_entry(branch_entries)
    if entry is None:
        return None
    from aelix_agent_core.session.context import _iso_to_unix_ms

    return _iso_to_unix_ms(getattr(entry, "timestamp", "") or "")


__all__ = [
    "KEEP_RECENT_TOKENS",
    "RESERVE_TOKENS",
    "SUMMARIZATION_PROMPT",
    "SUMMARIZATION_SYSTEM_PROMPT",
    "TOOL_RESULT_MAX_CHARS",
    "TURN_PREFIX_SUMMARIZATION_PROMPT",
    "UPDATE_SUMMARIZATION_PROMPT",
    "CompactResult",
    "CompactionPreparation",
    "FileOperations",
    "SummarizerOverride",
    "calculate_context_tokens",
    "compact",
    "compute_file_lists",
    "create_file_ops",
    "estimate_context_tokens",
    "estimate_tokens",
    "extract_file_ops_from_message",
    "find_cut_point",
    "find_turn_start_index",
    "find_valid_cut_points",
    "format_file_operations",
    "get_latest_compaction_boundary_ms",
    "get_latest_compaction_entry",
    "prepare_compaction",
]
