"""``buildSessionContext`` + message helpers (Sprint 4a).

Pi source: ``session/session.ts:21-76`` (``buildSessionContext``) +
``harness/messages.ts`` for ``create*Message`` helpers. Pure functions:
given a path of entries, derive ``messages`` / ``thinking_level`` / ``model``
honoring the compaction ``first_kept_entry_id`` boundary.

The compaction / branch / custom helpers return ``UserMessage`` instances
wrapping the rendered text so the LLM-facing conversion (``convert_to_llm``)
sees them as standard ``user`` messages. This is the Aelix shape — Pi uses
custom message ``role`` variants (``branchSummary`` / ``compactionSummary`` /
``custom``) that are converted in ``messages.ts`` ``convertToLlm``. The
4a Aelix port renders them eagerly to plain ``user`` text so the existing
:func:`aelix_agent_core.default_convert.default_convert_to_llm` pipeline
handles them without divergence. See ADR-0022 §"Aelix-additive
divergences".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aelix_ai.messages import ImageContent, TextContent, UserMessage

from aelix_agent_core.session.entries import (
    CompactionEntry,
    SessionTreeEntry,
)

if TYPE_CHECKING:
    from aelix_agent_core.session.session import SessionContext


# === Pi messages.ts string prefixes (verbatim) ==========================

COMPACTION_SUMMARY_PREFIX = (
    "The conversation history before this point was compacted into the "
    "following summary:\n\n<summary>\n"
)
COMPACTION_SUMMARY_SUFFIX = "\n</summary>"

BRANCH_SUMMARY_PREFIX = (
    "The following is a summary of a branch that this conversation came "
    "back from:\n\n<summary>\n"
)
BRANCH_SUMMARY_SUFFIX = "</summary>"


def create_compaction_summary_message(
    summary: str, tokens_before: int, timestamp: str
) -> UserMessage:
    """Pi `createCompactionSummaryMessage` (``messages.ts:90-101``).

    Aelix-additive: ``tokens_before`` is intentionally not surfaced on the
    rendered ``UserMessage`` — Pi keeps it on the ``compactionSummary``
    custom role; Aelix renders directly to text.
    """

    _ = tokens_before  # reserved for telemetry in Phase 2.2 follow-ups
    return UserMessage(
        content=[
            TextContent(
                text=(
                    COMPACTION_SUMMARY_PREFIX + summary + COMPACTION_SUMMARY_SUFFIX
                )
            )
        ],
        timestamp=_iso_to_unix_ms(timestamp),
    )


def create_branch_summary_message(
    summary: str, from_id: str, timestamp: str
) -> UserMessage:
    """Pi `createBranchSummaryMessage` (``messages.ts:81-88``)."""

    _ = from_id  # Pi keeps fromId on custom role; Aelix renders to user text
    return UserMessage(
        content=[
            TextContent(text=BRANCH_SUMMARY_PREFIX + summary + BRANCH_SUMMARY_SUFFIX)
        ],
        timestamp=_iso_to_unix_ms(timestamp),
    )


def create_custom_message(
    custom_type: str,
    content: object,
    display: bool,
    details: object,
    timestamp: str,
) -> UserMessage:
    """Pi `createCustomMessage` (``messages.ts:103-118``).

    Aelix-additive: when ``content`` is a string, wrap as a single
    :class:`TextContent`. When it is already a list of content blocks (Pi
    allows ``string | (TextContent | ImageContent)[]``), pass through.
    """

    _ = custom_type, display, details  # custom_type/display/details retained Pi-style elsewhere
    if isinstance(content, str):
        blocks: list[TextContent | ImageContent] = [TextContent(text=content)]
        return UserMessage(content=blocks, timestamp=_iso_to_unix_ms(timestamp))
    if isinstance(content, list):
        return UserMessage(content=list(content), timestamp=_iso_to_unix_ms(timestamp))
    # Unknown content shape — fall through to a stringified TextContent so
    # the LLM still sees something rather than dropping the entry silently.
    return UserMessage(
        content=[TextContent(text=str(content))],
        timestamp=_iso_to_unix_ms(timestamp),
    )


def _iso_to_unix_ms(timestamp: str) -> float | None:
    """Convert an ISO 8601 timestamp to unix millis (Pi semantics).

    Pi `new Date(timestamp).getTime()` returns NaN for unparseable values;
    Aelix prefers ``None`` so the typed ``timestamp: float | None`` stays
    honest about missing data.
    """

    try:
        from datetime import datetime

        # Accept both "...Z" and "+00:00" forms.
        normalized = timestamp.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp() * 1000.0
    except Exception:  # noqa: BLE001 — defensive parse boundary
        return None


# === buildSessionContext ================================================


def build_session_context(path_entries: list[SessionTreeEntry]) -> SessionContext:
    """Pi `buildSessionContext` (``session.ts:21-76``).

    Pure derivation from a list of entries. Honors the compaction
    ``first_kept_entry_id`` boundary by skipping pre-boundary entries
    BEFORE the compaction marker and resuming after it.
    """

    # Late import to break a Session ↔ SessionContext cycle.
    from aelix_agent_core.session.session import SessionContext

    thinking_level = "off"
    model: dict[str, str] | None = None
    compaction: CompactionEntry | None = None

    for entry in path_entries:
        if entry.type == "thinking_level_change":
            thinking_level = entry.thinking_level  # type: ignore[union-attr]
        elif entry.type == "model_change":
            model = {
                "provider": entry.provider,  # type: ignore[union-attr]
                "modelId": entry.model_id,  # type: ignore[union-attr]
            }
        elif entry.type == "message":
            msg = entry.message  # type: ignore[union-attr]
            if getattr(msg, "role", None) == "assistant":
                # Pi mirrors AgentMessage.provider / .model on assistant
                # messages; Aelix AssistantMessage doesn't carry those, so
                # leave ``model`` resolved by ``model_change`` entries only.
                # This is a known Aelix-additive narrowing (see ADR-0022).
                pass
        elif entry.type == "compaction":
            compaction = entry  # type: ignore[assignment]

    messages: list = []

    def _append_message(entry: SessionTreeEntry) -> None:
        if entry.type == "message":
            messages.append(entry.message)  # type: ignore[union-attr]
        elif entry.type == "custom_message":
            messages.append(
                create_custom_message(
                    entry.custom_type,  # type: ignore[union-attr]
                    entry.content,  # type: ignore[union-attr]
                    entry.display,  # type: ignore[union-attr]
                    entry.details,  # type: ignore[union-attr]
                    entry.timestamp,
                )
            )
        elif entry.type == "branch_summary" and entry.summary:  # type: ignore[union-attr]
            messages.append(
                create_branch_summary_message(
                    entry.summary,  # type: ignore[union-attr]
                    entry.from_id,  # type: ignore[union-attr]
                    entry.timestamp,
                )
            )

    if compaction is not None:
        messages.append(
            create_compaction_summary_message(
                compaction.summary,
                compaction.tokens_before,
                compaction.timestamp,
            )
        )
        compaction_idx = next(
            (
                i
                for i, e in enumerate(path_entries)
                if e.type == "compaction" and e.id == compaction.id
            ),
            -1,
        )
        found_first_kept = False
        for i in range(compaction_idx):
            entry = path_entries[i]
            if entry.id == compaction.first_kept_entry_id:
                found_first_kept = True
            if found_first_kept:
                _append_message(entry)
        for i in range(compaction_idx + 1, len(path_entries)):
            _append_message(path_entries[i])
    else:
        for entry in path_entries:
            _append_message(entry)

    return SessionContext(
        messages=messages, thinking_level=thinking_level, model=model
    )


__all__ = [
    "BRANCH_SUMMARY_PREFIX",
    "BRANCH_SUMMARY_SUFFIX",
    "COMPACTION_SUMMARY_PREFIX",
    "COMPACTION_SUMMARY_SUFFIX",
    "build_session_context",
    "create_branch_summary_message",
    "create_compaction_summary_message",
    "create_custom_message",
]
