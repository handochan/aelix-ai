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


def _wire_str(block: dict[str, Any], key: str) -> str:
    """Read ``key`` off a wire block as a ``str``, never ``None``.

    ``block.get(key, "")`` is NOT sufficient. The default only applies when the
    key is *absent*; an explicit JSON ``null`` — which any non-aelix writer, a
    hand-edited transcript, or a future adapter may emit — returns ``None`` and
    silently violates the ``str`` contract these content dataclasses declare.

    That failure is invisible rather than loud, which is why it is worth a
    helper: ``is_valid_thought_signature(None)`` returns ``False`` without
    raising, so a ``None`` signature is dropped exactly like the empty string
    it replaced. The bug would look fixed while Gemini replay stayed broken.
    Anything that is not a ``str`` degrades to ``""`` — the same value the
    dataclass defaults to when the field was never written.
    """

    value = block.get(key, "")
    return value if isinstance(value, str) else ""


def _wire_dict(block: dict[str, Any], key: str) -> dict[str, Any]:
    """Read ``key`` off a wire block as a ``dict``, never ``None``.

    Same reasoning as :func:`_wire_str`: ``dict(block.get("input", {}))``
    raises ``TypeError`` on an explicit ``null``, which would turn one
    malformed tool call into an unloadable session.
    """

    value = block.get(key, {})
    return dict(value) if isinstance(value, dict) else {}


def _message_from_dict(data: dict[str, Any]) -> AgentMessage:
    """Reconstruct an ``AgentMessage`` from a wire dict.

    Pi message shapes ``user`` / ``assistant`` / ``toolResult`` round-trip
    here. We dispatch on ``role`` and re-hydrate the content blocks. Custom
    message shapes (``branchSummary`` / ``compactionSummary`` / ``custom`` /
    ``bashExecution``) are NOT MessageEntry payloads on disk (Pi keeps them
    on the entry itself, not nested inside ``message``).

    **This decoder is an aelix-original divergence** (#135, ADR-0211). pi has
    no message decoder at all: ``jsonl-storage.ts:90-106`` validates the four
    envelope fields and then ``return parsed as unknown as SessionTreeEntry``,
    a TypeScript assertion erased at runtime, so pi's parsed JSON *is* the
    entry and pi is structurally incapable of dropping a content field. Python
    cannot copy that shape — the whole downstream contract is ``isinstance``-based
    (``_transform_messages.py:174``, ``_google_shared.py:296``) and needs real
    dataclass instances. So the obligation this decoder takes on instead is to
    be **total**: every field of every content dataclass must be read back.
    ``tests/session/test_message_roundtrip_content.py`` walks
    ``dataclasses.fields()`` and fails if a newly added field is not, which is
    what recovers pi's can't-lose-a-field property without pi's untyped shape.
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

    role = data.get("role")

    def _text(block: dict[str, Any]) -> Any:
        # ``text_signature`` carries the OpenAI Responses ``TextSignatureV1``
        # replay payload AND Gemini's per-text-part ``thoughtSignature``
        # (``_google_shared.py:296``), so it is load-bearing in both roles.
        return TextContent(
            text=_wire_str(block, "text"),
            text_signature=_wire_str(block, "text_signature"),
        )

    def _user_content(blocks: list[dict[str, Any]]) -> list[Any]:
        out: list[Any] = []
        for block in blocks:
            t = block.get("type")
            if t == "text":
                out.append(_text(block))
            elif t == "image":
                # ``mime_type``/``data`` are the pi-canonical pair and the ONLY
                # ones the Read tool populates (``tools/read.py:141,170,184``
                # passes ``source=""``). Reading ``source`` alone meant a
                # resumed session re-sent an image with an empty payload, which
                # the provider rejects outright rather than degrading. Legacy
                # ``source``-only blocks keep working: it is still read, and the
                # two new fields default to ``""`` when absent.
                out.append(
                    ImageContent(
                        source=_wire_str(block, "source"),
                        mime_type=_wire_str(block, "mime_type"),
                        data=_wire_str(block, "data"),
                    )
                )
            else:  # pragma: no cover — unknown content block
                out.append(block)
        return out

    def _assistant_content(blocks: list[dict[str, Any]]) -> list[Any]:
        out: list[Any] = []
        for block in blocks:
            t = block.get("type")
            if t == "text":
                out.append(_text(block))
            elif t == "thinking":
                # Without this branch a persisted thinking block came back as a
                # raw dict, so it never satisfied the
                # ``isinstance(block, ThinkingContent)`` test at
                # ``_transform_messages.py:174`` and ADR-0190's signed-thinking
                # replay was dead on every resumed session.
                out.append(
                    ThinkingContent(
                        thinking=_wire_str(block, "thinking"),
                        thinking_signature=_wire_str(block, "thinking_signature"),
                        redacted=bool(block.get("redacted", False)),
                    )
                )
            elif t == "toolCall":
                out.append(
                    ToolCallContent(
                        tool_call_id=_wire_str(block, "tool_call_id"),
                        tool_name=_wire_str(block, "tool_name"),
                        input=_wire_dict(block, "input"),
                        # Gemini's multi-turn signature, which the adapter must
                        # echo back verbatim; ``resolve_thought_signature``
                        # drops anything falsy, so losing it here silently ended
                        # the model's chain of thought across a resume.
                        thought_signature=_wire_str(block, "thought_signature"),
                    )
                )
            else:  # pragma: no cover
                # Forward compatibility, and pi-consistent: a block type only a
                # NEWER aelix writes passes through verbatim rather than
                # crashing this reader. Do not turn this into a raise.
                out.append(block)
        # No ``image`` branch here on purpose: pi's ``AssistantMessage.content``
        # union is ``(TextContent | ThinkingContent | ToolCall)[]`` and excludes
        # images (``packages/ai/src/types.ts``). Adding one would be a silent
        # parity divergence.
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
            # ``_message_to_dict`` is ``asdict``, so these were always WRITTEN;
            # rebuilding field-by-field simply never read them back and the
            # values died at the reload boundary.
            #
            # ``api``/``provider``/``model`` are ADR-0190's same-model
            # provenance. Restoring them has two effects, and it is worth being
            # exact about which:
            #   • They are the only record of which price list a turn should be
            #     billed at, so WITHOUT them a resumed session cannot be costed
            #     at all (``harness/_session_stats.py::_message_cost``). This is
            #     the effect this change actually delivers.
            #   • They also gate ``_transform_messages``' same-model branch.
            #     ADR-0210 recorded that the branch was unreachable anyway,
            #     because ``_assistant_content`` had no ``"thinking"`` case and a
            #     persisted thinking block returned as a raw dict. That is no
            #     longer true: #135 / ADR-0211 added the case, so a restored
            #     block is a real ``ThinkingContent`` and now does satisfy the
            #     ``isinstance`` test at ``_transform_messages.py:174``. The two
            #     halves are complementary — provenance decides *whether* the
            #     same-model branch is taken, the thinking case decides whether
            #     there is a typed block left for it to preserve. Both are
            #     needed; neither alone delivers replay.
            # The one live behavioural delta here is that a same-model resumed
            # turn no longer receives cross-model ``tool_call_id``
            # renormalisation (``:218``), which is the pi-correct outcome.
            #
            # ``response_id`` is the OpenAI Responses adapter's
            # ``previous_response_id`` chain.
            api=data.get("api"),
            provider=data.get("provider"),
            model=data.get("model"),
            response_id=data.get("response_id"),
        )
    if role == "toolResult":
        return ToolResultMessage(
            tool_call_id=data.get("tool_call_id", ""),
            content=_user_content(list(data.get("content", []))),
            is_error=bool(data.get("is_error", False)),
            timestamp=data.get("timestamp"),
            # Same omission: adapters that must wrap a result with its function
            # name (Moonshot, Together, Cloudflare) read this.
            tool_name=data.get("tool_name", "") or "",
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
