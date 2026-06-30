"""Message and content block types.

Mirrors the pi-ai message model: ``user``, ``assistant``, and ``toolResult``
are the only shapes the LLM itself understands. Higher layers (agent loop,
harness) can extend ``AgentMessage`` with custom types and filter/convert them
before each LLM call (see ADR-0008 for the runtime layering).

Sprint 6b (ADR-0049) — additive dataclass extensions for full Pi parity:

- :class:`ThinkingContent` content block (P-58, P-67).
- :class:`ImageContent.mime_type` + :class:`ImageContent.data` split
  fields so adapters can stop sniffing the legacy data-URL ``source``
  string (P-61). The legacy ``source`` field stays for back-compat with
  Sprint 6a callers; consumers MUST prefer ``mime_type`` + ``data`` when
  both are non-empty.
- :class:`AssistantMessage` provenance trio ``api`` / ``provider`` /
  ``model`` so :func:`_transform_messages._is_same_model` can answer
  reliably (P-68). Sprint 6a's Anthropic adapter does NOT populate these
  in this PR — the cross-adapter wiring is deferred to Sprint 6d per the
  spec §J carry-forward. ``None`` defaults preserve the pre-6b shape.
- :class:`ToolResultMessage.tool_name` field so adapters that wrap tool
  results with the function name (Moonshot et al.) can stop reaching
  into the originating :class:`ToolCallContent` via ``getattr`` (P-75).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class TextContent:
    text: str = ""
    # Pi parity (#15): ``TextContent.textSignature`` (``types.ts``). The
    # OpenAI **Responses** adapter stores a JSON ``TextSignatureV1`` payload
    # (``{"v":1,"id":...,"phase":...}``) here so the assistant message id +
    # phase can be replayed verbatim on the next request. ADDITIVE default
    # ``""`` keeps every existing caller / snapshot / parity fixture intact;
    # all other adapters leave it empty.
    text_signature: str = ""
    type: Literal["text"] = "text"


@dataclass(frozen=True)
class ImageContent:
    """Pi parity: ``ImageContent`` (``packages/ai/src/types.ts``).

    Sprint 6b extends Pi's two-field shape: ``mime_type`` (e.g.
    ``"image/png"``, ``"image/jpeg"``) + ``data`` (raw base64 payload, no
    data-URL prefix). The legacy ``source`` field stays for Sprint 6a
    back-compat — when present and ``data`` is empty, adapters fall back
    to the legacy reader path.
    """

    source: str = ""  # legacy data URL, base64 payload, or external URL
    mime_type: str = ""
    data: str = ""
    type: Literal["image"] = "image"


@dataclass(frozen=True)
class ThinkingContent:
    """Pi parity: ``ThinkingContent`` (``packages/ai/src/types.ts``).

    Sprint 6b additive — the OpenAI Completions adapter populates this
    when the upstream stream emits ``reasoning_content`` /
    ``reasoning_text`` deltas (compat-determined field name lands in
    Sprint 6d per ADR-0049 §Carry-forward). Sprint 6a's Anthropic adapter
    leaves the trio empty for now; the deferred wiring is captured under
    P-58-followup.
    """

    thinking: str = ""
    thinking_signature: str = ""
    redacted: bool = False
    type: Literal["thinking"] = "thinking"


@dataclass(frozen=True)
class ToolCallContent:
    tool_call_id: str = ""
    tool_name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    type: Literal["toolCall"] = "toolCall"


# Any content block that may appear inside a message body.
ContentBlock = TextContent | ImageContent | ThinkingContent | ToolCallContent


@dataclass(frozen=True)
class UserMessage:
    content: list[TextContent | ImageContent] = field(default_factory=list)
    timestamp: float | None = None
    role: Literal["user"] = "user"


@dataclass(frozen=True)
class AssistantMessage:
    """Pi parity: ``AssistantMessage`` (``packages/ai/src/types.ts``).

    Sprint 6b adds the ``api`` / ``provider`` / ``model`` provenance trio
    (P-68): the cross-model thinking-block rewrite in
    :mod:`aelix_ai.providers._transform_messages` needs to know whether a
    historical assistant turn came from the *same* model so it can
    preserve signed thinking blocks intact. Sprint 6a Anthropic adapter
    leaves the trio ``None`` (Sprint 6d cross-adapter hygiene per ADR-0050
    §Carry-forward); the Sprint 6b OpenAI adapter populates them at the
    output build site.
    """

    content: list[TextContent | ThinkingContent | ToolCallContent] = field(
        default_factory=list
    )
    # "end_turn" | "toolUse" | "error" | "aborted" | ...
    stop_reason: str | None = None
    error_message: str | None = None
    usage: dict[str, Any] | None = None
    timestamp: float | None = None
    # Provenance (Sprint 6b additive — Pi parity for same-model checks).
    api: str | None = None
    provider: str | None = None
    model: str | None = None
    # Pi parity (#15): ``AssistantMessage.responseId`` (``types.ts``). The
    # OpenAI **Responses** adapter records the ``response.id`` returned by
    # the stream so subsequent turns can chain via ``previous_response_id``
    # (server-side reasoning continuity). ADDITIVE default ``None`` preserves
    # the pre-#15 shape; all other adapters leave it unset.
    response_id: str | None = None
    role: Literal["assistant"] = "assistant"


@dataclass(frozen=True)
class ToolResultMessage:
    """Pi parity: ``ToolResultMessage``.

    Sprint 6b adds the ``tool_name`` field (P-75) so adapters that need
    to wrap tool results with the function name (Moonshot, Together,
    Cloudflare AI Gateway) can read it directly. The
    :mod:`aelix_ai.providers._transform_messages` orphan-synthesis path
    populates it from the originating :class:`ToolCallContent`. Default
    ``""`` preserves pre-6b call sites.
    """

    tool_call_id: str = ""
    content: list[TextContent | ImageContent] = field(default_factory=list)
    is_error: bool = False
    timestamp: float | None = None
    tool_name: str = ""
    role: Literal["toolResult"] = "toolResult"


# Anything the LLM can directly consume.
Message = UserMessage | AssistantMessage | ToolResultMessage


__all__ = [
    "AssistantMessage",
    "ContentBlock",
    "ImageContent",
    "Message",
    "TextContent",
    "ThinkingContent",
    "ToolCallContent",
    "ToolResultMessage",
    "UserMessage",
]
