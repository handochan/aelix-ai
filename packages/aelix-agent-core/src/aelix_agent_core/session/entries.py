"""Pi-parity session tree entry dataclasses (Sprint 4a / Phase 2.2.1).

Pi source: ``packages/agent/src/harness/types.ts:339-419`` (SHA ``734e08e``).

There are 11 entry types. All are ``@dataclass(frozen=True)`` and share the
common base fields ``type``, ``id``, ``parent_id``, ``timestamp``. The JSON
on-disk representation uses **camelCase** keys for Pi cross-runtime
compatibility (``parentId``, ``thinkingLevel``, ``firstKeptEntryId``,
``customType``, ``targetId``, ``fromId``, ``fromHook``, ``modelId``).

The Python in-memory representation uses snake_case throughout; the
:func:`entry_to_json` / :func:`entry_from_json` helpers translate at the
JSONL boundary so the wire format stays Pi-equivalent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from aelix_agent_core.types import AgentMessage

# === Base + 10 entry types ===============================================

# Pi parity: every entry's wire-form has type/id/parentId/timestamp + payload.
# Python uses snake_case (parent_id); JSON helpers below translate to camelCase.


@dataclass(frozen=True)
class MessageEntry:
    """Pi ``MessageEntry`` (``types.ts:346-349``)."""

    id: str
    parent_id: str | None
    timestamp: str
    message: AgentMessage
    type: Literal["message"] = "message"


@dataclass(frozen=True)
class ThinkingLevelChangeEntry:
    """Pi ``ThinkingLevelChangeEntry`` (``types.ts:351-354``)."""

    id: str
    parent_id: str | None
    timestamp: str
    thinking_level: str
    type: Literal["thinking_level_change"] = "thinking_level_change"


@dataclass(frozen=True)
class ModelChangeEntry:
    """Pi ``ModelChangeEntry`` (``types.ts:356-360``)."""

    id: str
    parent_id: str | None
    timestamp: str
    provider: str
    model_id: str
    type: Literal["model_change"] = "model_change"


@dataclass(frozen=True)
class CompactionEntry:
    """Pi ``CompactionEntry<T>`` (``types.ts:362-369``).

    Has the 5-param signature per P-13 — ``summary`` /
    ``first_kept_entry_id`` / ``tokens_before`` / ``details?`` /
    ``from_hook?``.
    """

    id: str
    parent_id: str | None
    timestamp: str
    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: Any | None = None
    from_hook: bool | None = None
    type: Literal["compaction"] = "compaction"


@dataclass(frozen=True)
class BranchSummaryEntry:
    """Pi ``BranchSummaryEntry<T>`` (``types.ts:371-377``)."""

    id: str
    parent_id: str | None
    timestamp: str
    from_id: str
    summary: str
    details: Any | None = None
    from_hook: bool | None = None
    type: Literal["branch_summary"] = "branch_summary"


@dataclass(frozen=True)
class CustomEntry:
    """Pi ``CustomEntry<T>`` (``types.ts:379-383``)."""

    id: str
    parent_id: str | None
    timestamp: str
    custom_type: str
    data: Any | None = None
    type: Literal["custom"] = "custom"


@dataclass(frozen=True)
class CustomMessageEntry:
    """Pi ``CustomMessageEntry<T>`` (``types.ts:385-391``).

    ``content`` is the wire shape — either a string OR a list of
    ``TextContent | ImageContent``.
    """

    id: str
    parent_id: str | None
    timestamp: str
    custom_type: str
    content: Any
    display: bool
    details: Any | None = None
    type: Literal["custom_message"] = "custom_message"


@dataclass(frozen=True)
class LabelEntry:
    """Pi ``LabelEntry`` (``types.ts:393-397``)."""

    id: str
    parent_id: str | None
    timestamp: str
    target_id: str
    label: str | None
    type: Literal["label"] = "label"


@dataclass(frozen=True)
class SessionInfoEntry:
    """Pi ``SessionInfoEntry`` (``types.ts:399-402``)."""

    id: str
    parent_id: str | None
    timestamp: str
    name: str | None = None
    type: Literal["session_info"] = "session_info"


@dataclass(frozen=True)
class LeafEntry:
    """Pi ``LeafEntry`` (``types.ts:404-407``)."""

    id: str
    parent_id: str | None
    timestamp: str
    target_id: str | None
    type: Literal["leaf"] = "leaf"


SessionTreeEntry = (
    MessageEntry
    | ThinkingLevelChangeEntry
    | ModelChangeEntry
    | CompactionEntry
    | BranchSummaryEntry
    | CustomEntry
    | CustomMessageEntry
    | LabelEntry
    | SessionInfoEntry
    | LeafEntry
)


# === Message <-> dict helpers ===========================================
#
# ``MessageEntry.message`` is an ``AgentMessage`` (the
# ``aelix_ai.messages.Message`` union). On disk it must round-trip as a JSON
# dict. We only need shallow conversion (the message dataclasses are frozen
# and contain primitives + content lists).


def _message_to_dict(message: AgentMessage) -> dict[str, Any]:
    """Convert an ``AgentMessage`` dataclass into a plain dict.

    Mirrors Pi's structural JSON serialization. We avoid
    ``dataclasses.asdict`` because it recurses into our ``Content`` types
    which already round-trip cleanly via ``asdict``; but we want explicit
    ``role`` handling and to filter unset optional ``None`` fields like
    ``timestamp``.
    """

    from dataclasses import asdict

    return asdict(message)


def _message_from_dict(data: dict[str, Any]) -> AgentMessage:
    """Reconstruct an ``AgentMessage`` from a wire dict.

    Pi message shapes ``user`` / ``assistant`` / ``toolResult`` round-trip
    here. We dispatch on ``role`` and re-hydrate the content blocks. Custom
    message shapes (``branchSummary`` / ``compactionSummary`` / ``custom`` /
    ``bashExecution``) are NOT MessageEntry payloads on disk (Pi keeps them
    on the entry itself, not nested inside ``message``).
    """

    from aelix_ai.messages import (
        AssistantMessage,
        ImageContent,
        TextContent,
        ToolCallContent,
        ToolResultMessage,
        UserMessage,
    )

    role = data.get("role")

    def _user_content(blocks: list[dict[str, Any]]) -> list[Any]:
        out: list[Any] = []
        for block in blocks:
            t = block.get("type")
            if t == "text":
                out.append(TextContent(text=block.get("text", "")))
            elif t == "image":
                out.append(ImageContent(source=block.get("source", "")))
            else:  # pragma: no cover — unknown content block
                out.append(block)
        return out

    def _assistant_content(blocks: list[dict[str, Any]]) -> list[Any]:
        out: list[Any] = []
        for block in blocks:
            t = block.get("type")
            if t == "text":
                out.append(TextContent(text=block.get("text", "")))
            elif t == "toolCall":
                out.append(
                    ToolCallContent(
                        tool_call_id=block.get("tool_call_id", ""),
                        tool_name=block.get("tool_name", ""),
                        input=dict(block.get("input", {})),
                    )
                )
            else:  # pragma: no cover
                out.append(block)
        return out

    if role == "user":
        return UserMessage(
            content=_user_content(list(data.get("content", []))),
            timestamp=data.get("timestamp"),
        )
    if role == "assistant":
        return AssistantMessage(
            content=_assistant_content(list(data.get("content", []))),
            stop_reason=data.get("stop_reason"),
            error_message=data.get("error_message"),
            usage=data.get("usage"),
            timestamp=data.get("timestamp"),
        )
    if role == "toolResult":
        return ToolResultMessage(
            tool_call_id=data.get("tool_call_id", ""),
            content=_user_content(list(data.get("content", []))),
            is_error=bool(data.get("is_error", False)),
            timestamp=data.get("timestamp"),
        )
    # Unknown role — return the raw dict so callers can surface a clear
    # error at the JSONL boundary (parse fail rather than silent loss).
    raise ValueError(f"unsupported message role on wire: {role!r}")


# === Entry <-> dict (camelCase wire form) ===============================
#
# Pi entries always use camelCase keys on the wire. The translation table is
# limited and explicit so we can keep the snake_case API surface in Python
# without the asdict round-trip leaking snake_case onto disk.
#
# Note: ``MessageEntry.message`` is delegated to ``_message_to_dict`` /
# ``_message_from_dict``; nothing else nests.


_SNAKE_TO_CAMEL = {
    "parent_id": "parentId",
    "thinking_level": "thinkingLevel",
    "model_id": "modelId",
    "first_kept_entry_id": "firstKeptEntryId",
    "tokens_before": "tokensBefore",
    "from_hook": "fromHook",
    "from_id": "fromId",
    "custom_type": "customType",
    "target_id": "targetId",
}


def _to_camel(key: str) -> str:
    return _SNAKE_TO_CAMEL.get(key, key)


def _from_camel(key: str) -> str:
    for snake, camel in _SNAKE_TO_CAMEL.items():
        if key == camel:
            return snake
    return key


def entry_to_json(entry: SessionTreeEntry) -> dict[str, Any]:
    """Translate a snake_case Python entry to its camelCase wire dict.

    Pi keeps optional ``details`` / ``fromHook`` keys on disk only when set
    (``undefined`` fields are omitted by ``JSON.stringify``). We mirror that
    by dropping ``None`` for the optional fields.
    """

    from dataclasses import fields

    out: dict[str, Any] = {}
    for f in fields(entry):
        value = getattr(entry, f.name)
        camel = _to_camel(f.name)
        if f.name == "message":
            out[camel] = _message_to_dict(value)
        elif value is None and f.name in (
            "details",
            "from_hook",
            "name",
        ):
            # Pi omits these when undefined; preserve that on the wire.
            continue
        else:
            out[camel] = value
    return out


def entry_from_json(data: dict[str, Any]) -> SessionTreeEntry:
    """Translate a camelCase wire dict to a snake_case dataclass entry."""

    entry_type = data.get("type")
    # Common fields shared by every entry type.
    common = {
        "id": data["id"],
        "parent_id": data.get("parentId"),
        "timestamp": data["timestamp"],
    }
    if entry_type == "message":
        return MessageEntry(
            **common,
            message=_message_from_dict(dict(data["message"])),
        )
    if entry_type == "thinking_level_change":
        return ThinkingLevelChangeEntry(
            **common, thinking_level=data["thinkingLevel"]
        )
    if entry_type == "model_change":
        return ModelChangeEntry(
            **common,
            provider=data["provider"],
            model_id=data["modelId"],
        )
    if entry_type == "compaction":
        return CompactionEntry(
            **common,
            summary=data["summary"],
            first_kept_entry_id=data["firstKeptEntryId"],
            tokens_before=int(data["tokensBefore"]),
            details=data.get("details"),
            from_hook=data.get("fromHook"),
        )
    if entry_type == "branch_summary":
        return BranchSummaryEntry(
            **common,
            from_id=data["fromId"],
            summary=data["summary"],
            details=data.get("details"),
            from_hook=data.get("fromHook"),
        )
    if entry_type == "custom":
        return CustomEntry(
            **common,
            custom_type=data["customType"],
            data=data.get("data"),
        )
    if entry_type == "custom_message":
        return CustomMessageEntry(
            **common,
            custom_type=data["customType"],
            content=data["content"],
            display=bool(data["display"]),
            details=data.get("details"),
        )
    if entry_type == "label":
        return LabelEntry(
            **common,
            target_id=data["targetId"],
            label=data.get("label"),
        )
    if entry_type == "session_info":
        return SessionInfoEntry(**common, name=data.get("name"))
    if entry_type == "leaf":
        return LeafEntry(**common, target_id=data.get("targetId"))
    raise ValueError(f"unknown session entry type: {entry_type!r}")


__all__ = [
    "BranchSummaryEntry",
    "CompactionEntry",
    "CustomEntry",
    "CustomMessageEntry",
    "LabelEntry",
    "LeafEntry",
    "MessageEntry",
    "ModelChangeEntry",
    "SessionInfoEntry",
    "SessionTreeEntry",
    "ThinkingLevelChangeEntry",
    "entry_from_json",
    "entry_to_json",
]
