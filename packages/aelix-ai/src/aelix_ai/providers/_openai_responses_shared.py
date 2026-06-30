"""OpenAI **Responses**-API shared engine — pi parity.

Pi parity: ``packages/ai/src/api/openai-responses-shared.ts`` (552 LOC) at
SHA ``927e98068cda276bf9188f4774fb927c89823388``.

This is the engine the OpenAI Responses family of adapters
(``openai-responses``, ``openai-codex``, ``opencode``,
``cloudflare-ai-gateway`` …) all share. The thin per-provider adapter
handles client construction, header/auth quirks, and param assembly; the
heavy lifting — message conversion, tool conversion, and the streaming
state machine — lives here.

Three pieces have byte-sensitive behavior that MUST stay identical to pi
across turns or encrypted-reasoning continuity silently breaks:

1. **Out-of-order slot map.** The stream state machine keys every block by
   ``event.output_index`` in a dict (NOT linear arrival order) — pi #6009.
   A ``reasoning`` item that finalizes *after* a ``function_call`` started
   still routes to the right block.
2. **Encrypted-reasoning roundtrip.** On ``output_item.done`` for a
   reasoning item the FULL ``ResponseReasoningItem`` (including
   ``encrypted_content``) is serialized to JSON and stored on
   :attr:`ThinkingContent.thinking_signature`. On the next request
   :func:`convert_responses_messages` parses it back and re-pushes it
   verbatim as a reasoning input item (pi shared.ts:173-177).
3. **Composite tool-call id.** A tool call's id is the composite
   ``"{call_id}|{item_id}"``; the pipe split + ``fc_``-prefixed item-id
   normalization is gated on :data:`OPENAI_TOOL_CALL_PROVIDERS`.

Divergences from pi (intentional, v1 — #15 decisions):

- **Service-tier cost multipliers are DROPPED.** pi threads
  ``serviceTier`` / ``resolveServiceTier`` / ``applyServiceTierPricing``
  through the engine to apply ``flex`` (0.5×) / ``priority`` (2×/2.5×)
  cost multipliers. Aelix v1 does not model service tiers; the
  :class:`OpenAIResponsesStreamOptions` shape is kept for signature
  parity but pricing is a no-op.
- **Token-dict usage convention.** pi builds a ``Usage`` object and calls
  ``calculateCost``. Aelix follows the established adapter convention
  (see ``openai_completions._usage_to_dict``): usage is emitted as a
  plain ``dict`` of token counts (now including a ``reasoning`` key), and
  cost is resolved by a higher layer. The reasoning count is a *subset*
  of ``output`` tokens, mirroring pi's ``Usage.reasoning``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCallContent,
)
from aelix_ai.providers._openai_responses_compat import get_responses_compat
from aelix_ai.providers._sanitize_unicode import sanitize_surrogates
from aelix_ai.providers._short_hash import short_hash
from aelix_ai.providers._streaming_json import parse_streaming_json
from aelix_ai.providers._transform_messages import transform_messages
from aelix_ai.streaming import (
    AssistantMessageEvent,
    Context,
    Model,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

# Pi parity: ``OPENAI_TOOL_CALL_PROVIDERS`` (openai-responses.ts:26). Only
# these providers get the pipe-id (``call_id|item_id``) normalization in
# :func:`convert_responses_messages`. pi defines this in the thin adapter
# and passes it in; we expose it here so every Responses-family adapter
# can reuse the same gate.
OPENAI_TOOL_CALL_PROVIDERS: frozenset[str] = frozenset(
    {"openai", "openai-codex", "opencode"}
)

# Valid ``TextSignatureV1.phase`` values (pi ``types.ts``).
_VALID_PHASES: frozenset[str] = frozenset({"commentary", "final_answer"})

# The Responses engine only ever produces assistant-side blocks (text /
# thinking / tool-call); it never appends ImageContent. Narrowing the
# accumulator to the same union as :attr:`AssistantMessage.content` keeps the
# frozen snapshots assignment-compatible without a cast (list invariance).
_ResponsesBlock = TextContent | ThinkingContent | ToolCallContent


# =============================================================================
# Text-signature codec
# =============================================================================


def encode_text_signature_v1(item_id: str, phase: str | None = None) -> str:
    """Encode a ``TextSignatureV1`` JSON payload.

    Pi parity: ``encodeTextSignatureV1`` (shared.ts:40-44). Shape is
    ``{"v": 1, "id": item_id}`` with an optional ``"phase"`` key appended
    only when ``phase`` is truthy. The key order (``v``, ``id``, ``phase``)
    matches pi's object-literal order so the serialized string is
    byte-identical.
    """

    payload: dict[str, Any] = {"v": 1, "id": item_id}
    if phase:
        payload["phase"] = phase
    return json.dumps(payload, separators=(",", ":"))


def parse_text_signature(
    signature: str | None,
) -> dict[str, str] | None:
    """Decode a stored text signature back to ``{"id", "phase"?}``.

    Pi parity: ``parseTextSignature`` (shared.ts:46-64). Accepts both the
    JSON ``TextSignatureV1`` shape and the legacy plain-string id (any
    value that does not start with ``{`` or fails to parse is treated as a
    bare id). Returns ``None`` only for an empty/``None`` signature.
    """

    if not signature:
        return None
    if signature.startswith("{"):
        try:
            parsed = json.loads(signature)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get("v") == 1 and isinstance(
            parsed.get("id"), str
        ):
            phase = parsed.get("phase")
            if phase in _VALID_PHASES:
                return {"id": parsed["id"], "phase": phase}
            return {"id": parsed["id"]}
    return {"id": signature}


# =============================================================================
# Tool conversion
# =============================================================================


def convert_responses_tools(
    tools: Iterable[Any], strict: bool = False
) -> list[dict[str, Any]]:
    """Aelix tools → OpenAI Responses tool schema.

    Pi parity: ``convertResponsesTools`` (shared.ts:273-282). The Responses
    tool shape is **flat** (``{type, name, description, parameters,
    strict}``) — distinct from the Chat Completions nested
    ``{type, function: {...}}`` shape. ``strict`` defaults to ``False``
    (pi's ``options?.strict === undefined ? false : ...``).
    """

    out: list[dict[str, Any]] = []
    for tool in tools:
        params = getattr(tool, "parameters", None)
        if params is None:
            params = {"type": "object", "properties": {}}
        out.append(
            {
                "type": "function",
                "name": getattr(tool, "name", "unknown"),
                "description": getattr(tool, "description", ""),
                "parameters": params,
                "strict": strict,
            }
        )
    return out


# =============================================================================
# Message conversion
# =============================================================================


def _normalize_id_part(part: str) -> str:
    """Pi parity: inline ``normalizeIdPart`` (shared.ts:98-102).

    Replace every char outside ``[a-zA-Z0-9_-]`` with ``_``, clamp to 64
    chars, then strip trailing underscores (``replace(/_+$/, "")``).
    """

    sanitized = "".join(
        ch if (ch.isalnum() and ch.isascii()) or ch in "_-" else "_"
        for ch in part
    )
    if len(sanitized) > 64:
        sanitized = sanitized[:64]
    return sanitized.rstrip("_")


def _build_foreign_responses_item_id(item_id: str) -> str:
    """Pi parity: inline ``buildForeignResponsesItemId`` (shared.ts:104-107).

    A foreign (non-OpenAI-origin) tool-call item id is replaced with a
    stable ``fc_<short_hash>`` so cross-turn pairing resolves identically
    on both runtimes.
    """

    normalized = f"fc_{short_hash(item_id)}"
    return normalized[:64] if len(normalized) > 64 else normalized


def convert_responses_messages(
    model: Model,
    context: Context,
    allowed_tool_call_providers: frozenset[str] | set[str] = OPENAI_TOOL_CALL_PROVIDERS,
    *,
    include_system_prompt: bool = True,
) -> list[dict[str, Any]]:
    """Aelix :class:`Context` → OpenAI Responses ``input`` items.

    Pi parity: ``convertResponsesMessages`` (shared.ts:90-267).

    The assistant branch is the load-bearing one: it replays stored
    reasoning by parsing :attr:`ThinkingContent.thinking_signature` (a full
    ``ResponseReasoningItem`` JSON incl. ``encrypted_content``) and
    re-pushing it as a reasoning input item — this is the encrypted-reasoning
    continuity (shared.ts:173-177).
    """

    messages: list[dict[str, Any]] = []
    accepts_image = "image" in (model.input or [])

    def _image_url(block: ImageContent) -> str:
        if block.data:
            mime = block.mime_type or "image/png"
            return f"data:{mime};base64,{block.data}"
        src = block.source or ""
        if src.startswith(("data:", "http")):
            return src
        return f"data:image/png;base64,{src}"

    def _normalize_tool_call_id(
        tool_call_id: str, _target: Model, source: AssistantMessage
    ) -> str:
        # Pi parity: ``normalizeToolCallId`` (shared.ts:109-121). Only the
        # allowed providers get pipe-id normalization; everything else is
        # a flat ``normalizeIdPart``.
        if model.provider not in allowed_tool_call_providers:
            return _normalize_id_part(tool_call_id)
        if "|" not in tool_call_id:
            return _normalize_id_part(tool_call_id)
        parts = tool_call_id.split("|")
        call_id, item_id = parts[0], parts[1]
        normalized_call_id = _normalize_id_part(call_id)
        is_foreign = (
            source.provider != model.provider or source.api != model.api
        )
        if is_foreign:
            normalized_item_id = _build_foreign_responses_item_id(item_id)
        else:
            normalized_item_id = _normalize_id_part(item_id)
        # OpenAI Responses requires the function-call item id to start "fc".
        if not normalized_item_id.startswith("fc_"):
            normalized_item_id = _normalize_id_part(f"fc_{normalized_item_id}")
        return f"{normalized_call_id}|{normalized_item_id}"

    transformed = transform_messages(
        list(context.messages),
        model,
        normalize_tool_call_id=_normalize_tool_call_id,
    )

    if include_system_prompt and context.system_prompt:
        compat = get_responses_compat(model)
        role = (
            "developer"
            if (getattr(model, "reasoning", False) and compat.supports_developer_role)
            else "system"
        )
        messages.append(
            {"role": role, "content": sanitize_surrogates(context.system_prompt)}
        )

    msg_index = 0
    for msg in transformed:
        if msg.role == "user":
            content: list[dict[str, Any]] = []
            for item in msg.content:
                if isinstance(item, TextContent):
                    content.append(
                        {
                            "type": "input_text",
                            "text": sanitize_surrogates(item.text),
                        }
                    )
                elif isinstance(item, ImageContent):
                    content.append(
                        {
                            "type": "input_image",
                            "detail": "auto",
                            "image_url": _image_url(item),
                        }
                    )
            if not content:
                # Pi parity: skip empty user messages (no msg_index bump).
                continue
            messages.append({"role": "user", "content": content})

        elif msg.role == "assistant":
            output: list[dict[str, Any]] = []
            assistant_msg = msg
            is_different_model = (
                assistant_msg.model != model.id
                and assistant_msg.provider == model.provider
                and assistant_msg.api == model.api
            )
            text_block_index = 0

            for block in msg.content:
                if isinstance(block, ThinkingContent):
                    if block.thinking_signature:
                        # Replay the full ResponseReasoningItem verbatim
                        # (incl. encrypted_content) — pi shared.ts:173-177.
                        try:
                            reasoning_item = json.loads(block.thinking_signature)
                        except (json.JSONDecodeError, ValueError):
                            continue
                        output.append(reasoning_item)
                elif isinstance(block, TextContent):
                    parsed = parse_text_signature(block.text_signature)
                    fallback = (
                        f"msg_pi_{msg_index}"
                        if text_block_index == 0
                        else f"msg_pi_{msg_index}_{text_block_index}"
                    )
                    text_block_index += 1
                    msg_id = parsed.get("id") if parsed else None
                    if not msg_id:
                        msg_id = fallback
                    elif len(msg_id) > 64:
                        msg_id = f"msg_{short_hash(msg_id)}"
                    item_dict: dict[str, Any] = {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": sanitize_surrogates(block.text),
                                "annotations": [],
                            }
                        ],
                        "status": "completed",
                        "id": msg_id,
                    }
                    phase = parsed.get("phase") if parsed else None
                    if phase:
                        item_dict["phase"] = phase
                    output.append(item_dict)
                elif isinstance(block, ToolCallContent):
                    parts = block.tool_call_id.split("|")
                    call_id = parts[0]
                    item_id: str | None = parts[1] if len(parts) > 1 else None
                    # Pi parity (shared.ts:204-209): for different-model
                    # messages, omit the fc_ id to dodge pairing validation.
                    if is_different_model and item_id and item_id.startswith("fc_"):
                        item_id = None
                    fc: dict[str, Any] = {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": block.tool_name,
                        "arguments": json.dumps(block.input or {}),
                    }
                    if item_id is not None:
                        fc["id"] = item_id
                    output.append(fc)

            if not output:
                # Pi parity: skip empty assistant turns (no msg_index bump).
                continue
            messages.extend(output)

        elif msg.role == "toolResult":
            text_result = "\n".join(
                c.text for c in msg.content if isinstance(c, TextContent)
            )
            has_images = any(isinstance(c, ImageContent) for c in msg.content)
            has_text = len(text_result) > 0
            call_id = msg.tool_call_id.split("|")[0]

            output_value: str | list[dict[str, Any]]
            if has_images and accepts_image:
                content_parts: list[dict[str, Any]] = []
                if has_text:
                    content_parts.append(
                        {
                            "type": "input_text",
                            "text": sanitize_surrogates(text_result),
                        }
                    )
                for block in msg.content:
                    if isinstance(block, ImageContent):
                        content_parts.append(
                            {
                                "type": "input_image",
                                "detail": "auto",
                                "image_url": _image_url(block),
                            }
                        )
                output_value = content_parts
            else:
                output_value = sanitize_surrogates(
                    text_result if has_text else "(see attached image)"
                )

            messages.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output_value,
                }
            )

        msg_index += 1

    return messages


# =============================================================================
# Stop-reason mapping
# =============================================================================


def map_stop_reason(status: str | None) -> str:
    """Pi parity: ``mapStopReason`` (shared.ts:533-552).

    ``completed`` → ``"stop"``; ``incomplete`` (e.g. ``max_output_tokens``)
    → ``"length"``; ``failed`` / ``cancelled`` → ``"error"``; the wonky
    ``in_progress`` / ``queued`` and a missing status → ``"stop"``. An
    unknown status raises (pi's exhaustiveness guard).
    """

    if not status:
        return "stop"
    if status == "completed":
        return "stop"
    if status == "incomplete":
        return "length"
    if status in ("failed", "cancelled"):
        return "error"
    if status in ("in_progress", "queued"):
        return "stop"
    raise ValueError(f"Unhandled stop reason: {status}")


# =============================================================================
# Stream processing
# =============================================================================


@dataclass
class ResponsesStreamState:
    """Mutable accumulator the engine fills (pi's ``output`` param).

    The thin adapter creates one, drives the engine to exhaustion, then
    reads the finalized fields to emit the terminal ``done`` event. Keeping
    this as a plain mutable object (vs. the frozen :class:`AssistantMessage`)
    mirrors pi mutating ``output`` in place while still letting the engine
    hand frozen snapshots to each event's ``partial``.
    """

    content: list[_ResponsesBlock] = field(default_factory=list)
    response_id: str | None = None
    usage: dict[str, Any] | None = None
    stop_reason: str = "stop"


@dataclass(frozen=True)
class OpenAIResponsesStreamOptions:
    """Engine options (pi ``OpenAIResponsesStreamOptions``, shared.ts:66-76).

    pi carries ``serviceTier`` / ``resolveServiceTier`` /
    ``applyServiceTierPricing`` here to apply service-tier cost multipliers.
    Aelix v1 **drops** service-tier pricing (see module docstring), so this
    shape is intentionally empty — it exists only so the engine signature
    stays forward-compatible if tiers are reintroduced.
    """


@dataclass
class _Slot:
    """Per-``output_index`` streaming slot (pi ``ResponsesOutputSlot``)."""

    kind: str  # "thinking" | "text" | "toolCall"
    content_index: int
    thinking: str = ""
    text: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    partial_json: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from an attr (SDK object) or a key (dict mock)."""

    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _js_str(value: Any) -> str:
    """Mirror JS template-literal string coercion of a value.

    pi builds the composite tool-call id with ``${item.call_id}|${item.id}``
    (shared.ts:335). When ``item.id`` is ``undefined`` JS stringifies it to the
    literal ``"undefined"`` (and ``null`` -> ``"null"``); reproducing that keeps
    the composite id byte-identical to pi instead of emitting a trailing-empty
    ``call_id|``.
    """

    if value is None:
        return "undefined"
    return str(value)


def _item_to_dict(item: Any) -> dict[str, Any]:
    """Serialize a ``ResponseReasoningItem`` to a plain JSON-able dict.

    pi calls ``JSON.stringify(item)`` (shared.ts:481), which **omits**
    ``undefined`` keys — so the stored reasoning item never carries
    ``content`` / ``status`` when the SDK left them unset. The Python OpenAI
    SDK models those wire-absent optionals as ``None``, and a naive
    ``model_dump(mode="json")`` materializes them as explicit JSON ``null``.

    On the next turn :func:`convert_responses_messages` replays the stored
    item verbatim into the Responses ``input`` array; a stray ``content:null``
    / ``status:null`` makes OpenAI reject it ("Invalid value: null"), breaking
    the encrypted-reasoning roundtrip. To match pi's undefined-omitting
    semantics we drop every ``None``-valued key (``exclude_none`` for pydantic
    models, an explicit filter for dict mocks). The surviving keys
    (``type`` / ``id`` / ``summary`` / ``encrypted_content``) are exactly what
    OpenAI accepts back as a reasoning input item.
    """

    if isinstance(item, dict):
        return {k: v for k, v in item.items() if v is not None}
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        try:
            result = dump(mode="json", exclude_none=True)  # pydantic v2
        except TypeError:
            result = dump(exclude_none=True)
        if isinstance(result, dict):
            return {k: v for k, v in result.items() if v is not None}
        # ``model_dump`` is contractually a dict; guard anyway rather than
        # leak a non-dict into ``json.dumps``.
        return {}
    # Last-resort fallback for a non-pydantic, non-dict object. Prefer a
    # concrete ``__dict__`` (drop ``None`` for consistency); never fall back
    # to ``str(repr(...))`` garbage in the serialized signature.
    raw = getattr(item, "__dict__", None)
    if isinstance(raw, dict):
        return {k: v for k, v in raw.items() if v is not None}
    raise TypeError(
        f"Cannot serialize Responses reasoning item of type {type(item)!r}"
    )


async def process_responses_stream(
    openai_stream: AsyncIterable[Any],
    state: ResponsesStreamState,
    model: Model,
    options: OpenAIResponsesStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Drive the Responses SSE stream — the core state machine.

    Pi parity: ``processResponsesStream`` (shared.ts:295-531). pi mutates a
    shared ``output`` and ``stream.push(event)``; the Python port mutates
    ``state`` and ``yield``\\s each block event (the thin adapter wraps with
    the ``start`` / ``done`` envelope). All slot state is keyed by
    ``event.output_index`` so reasoning that finalizes after a later
    function_call still routes to the right block (out-of-order tolerance,
    pi #6009).

    Raises on an ``error`` / ``response.failed`` event, and — critically —
    if the stream ends without any terminal ``response.completed`` /
    ``response.incomplete`` / ``response.failed`` event (the saw-terminal
    guard, shared.ts:528-530).
    """

    _ = options  # service-tier pricing dropped in v1 (see module docstring)
    saw_terminal = False
    slots: dict[int, _Slot] = {}

    def _snapshot() -> AssistantMessage:
        return AssistantMessage(
            content=list(state.content),
            response_id=state.response_id,
            api=model.api,
            provider=model.provider,
            model=model.id,
        )

    def _set_block(index: int, block: _ResponsesBlock) -> None:
        state.content[index] = block

    def _create_slot(
        output_index: int, item: Any
    ) -> tuple[_Slot | None, AssistantMessageEvent | None]:
        item_type = _get(item, "type")
        if item_type == "reasoning":
            block = ThinkingContent(thinking="")
            state.content.append(block)
            slot = _Slot(kind="thinking", content_index=len(state.content) - 1)
            slots[output_index] = slot
            return slot, ThinkingStartEvent(
                content_index=slot.content_index, partial=_snapshot()
            )
        if item_type == "message":
            block = TextContent(text="")
            state.content.append(block)
            slot = _Slot(kind="text", content_index=len(state.content) - 1)
            slots[output_index] = slot
            return slot, TextStartEvent(
                content_index=slot.content_index, partial=_snapshot()
            )
        if item_type == "function_call":
            # pi: ``id: `${item.call_id}|${item.id}``` (shared.ts:335). Use
            # JS string coercion so an absent ``item.id`` becomes the literal
            # "undefined" (matching pi) rather than a trailing-empty segment.
            call_id = _get(item, "call_id")
            item_id = _get(item, "id")
            block = ToolCallContent(
                tool_call_id=f"{_js_str(call_id)}|{_js_str(item_id)}",
                tool_name=_get(item, "name", "") or "",
                input={},
            )
            state.content.append(block)
            slot = _Slot(
                kind="toolCall",
                content_index=len(state.content) - 1,
                tool_call_id=block.tool_call_id,
                tool_name=block.tool_name,
                partial_json=_get(item, "arguments", "") or "",
            )
            slots[output_index] = slot
            return slot, ToolCallStartEvent(
                content_index=slot.content_index, partial=_snapshot()
            )
        return None, None

    def _slot_of(output_index: int, kind: str) -> _Slot | None:
        slot = slots.get(output_index)
        return slot if slot is not None and slot.kind == kind else None

    def _finalize_response(response: Any) -> None:
        nonlocal saw_terminal
        saw_terminal = True
        response_id = _get(response, "id")
        if response_id:
            state.response_id = response_id
        usage = _get(response, "usage")
        if usage is not None:
            input_details = _get(usage, "input_tokens_details")
            cached = _get(input_details, "cached_tokens", 0) or 0
            output_details = _get(usage, "output_tokens_details")
            reasoning_tokens = _get(output_details, "reasoning_tokens", 0) or 0
            raw_input = _get(usage, "input_tokens", 0) or 0
            raw_output = _get(usage, "output_tokens", 0) or 0
            total = _get(usage, "total_tokens", 0) or 0
            non_cached_input = raw_input - cached
            # Token-dict convention (see module docstring). Emit both the
            # ``input``/``output`` (Usage-field) and ``input_tokens``/
            # ``output_tokens``/``total_tokens`` spellings so the context
            # meter and /cost aggregator both read real numbers. ``reasoning``
            # is a subset of ``output`` — never added on top.
            state.usage = {
                "input": non_cached_input,
                "output": raw_output,
                "input_tokens": non_cached_input,
                "output_tokens": raw_output,
                "cache_read": cached,
                "cache_write": 0,
                "reasoning": reasoning_tokens,
                "total_tokens": total,
            }
        state.stop_reason = map_stop_reason(_get(response, "status"))
        if (
            any(isinstance(b, ToolCallContent) for b in state.content)
            and state.stop_reason == "stop"
        ):
            state.stop_reason = "toolUse"

    async for event in openai_stream:
        event_type = _get(event, "type")

        if event_type == "response.created":
            state.response_id = _get(_get(event, "response"), "id")

        elif event_type == "response.output_item.added":
            _, start_event = _create_slot(
                _get(event, "output_index"), _get(event, "item")
            )
            if start_event is not None:
                yield start_event

        elif event_type in (
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        ):
            slot = _slot_of(_get(event, "output_index"), "thinking")
            if slot is None:
                continue
            delta = _get(event, "delta", "") or ""
            slot.thinking += delta
            _set_block(slot.content_index, ThinkingContent(thinking=slot.thinking))
            yield ThinkingDeltaEvent(
                delta=delta, content_index=slot.content_index, partial=_snapshot()
            )

        elif event_type == "response.reasoning_summary_part.done":
            slot = _slot_of(_get(event, "output_index"), "thinking")
            if slot is None:
                continue
            slot.thinking += "\n\n"
            _set_block(slot.content_index, ThinkingContent(thinking=slot.thinking))
            yield ThinkingDeltaEvent(
                delta="\n\n",
                content_index=slot.content_index,
                partial=_snapshot(),
            )

        elif event_type in (
            "response.output_text.delta",
            "response.refusal.delta",
        ):
            slot = _slot_of(_get(event, "output_index"), "text")
            if slot is None:
                continue
            delta = _get(event, "delta", "") or ""
            slot.text += delta
            _set_block(slot.content_index, TextContent(text=slot.text))
            yield TextDeltaEvent(
                delta=delta, content_index=slot.content_index, partial=_snapshot()
            )

        elif event_type == "response.function_call_arguments.delta":
            slot = _slot_of(_get(event, "output_index"), "toolCall")
            if slot is None:
                continue
            delta = _get(event, "delta", "") or ""
            slot.partial_json += delta
            slot.arguments = parse_streaming_json(slot.partial_json)
            _set_block(
                slot.content_index,
                ToolCallContent(
                    tool_call_id=slot.tool_call_id,
                    tool_name=slot.tool_name,
                    input=slot.arguments,
                ),
            )
            yield ToolCallDeltaEvent(
                delta=delta,
                content_index=slot.content_index,
                partial=_snapshot(),
                tool_call_id=slot.tool_call_id,
                tool_name=slot.tool_name,
            )

        elif event_type == "response.function_call_arguments.done":
            slot = _slot_of(_get(event, "output_index"), "toolCall")
            if slot is None:
                continue
            arguments = _get(event, "arguments", "") or ""
            previous_partial = slot.partial_json
            slot.partial_json = arguments
            slot.arguments = parse_streaming_json(arguments)
            _set_block(
                slot.content_index,
                ToolCallContent(
                    tool_call_id=slot.tool_call_id,
                    tool_name=slot.tool_name,
                    input=slot.arguments,
                ),
            )
            # Emit the trailing suffix ONLY when the final arguments extend
            # the streamed partial (pi shared.ts:462-472) — avoids replaying
            # a divergent full payload as a delta.
            if arguments.startswith(previous_partial):
                suffix = arguments[len(previous_partial):]
                if suffix:
                    yield ToolCallDeltaEvent(
                        delta=suffix,
                        content_index=slot.content_index,
                        partial=_snapshot(),
                        tool_call_id=slot.tool_call_id,
                        tool_name=slot.tool_name,
                    )

        elif event_type == "response.output_item.done":
            item = _get(event, "item")
            output_index = _get(event, "output_index")
            slot = slots.get(output_index)
            if slot is None:
                slot, start_event = _create_slot(output_index, item)
                if start_event is not None:
                    yield start_event
            item_type = _get(item, "type")

            if item_type == "reasoning" and slot is not None and slot.kind == "thinking":
                summary = _get(item, "summary") or []
                content_list = _get(item, "content") or []
                summary_text = "\n\n".join(
                    _get(s, "text", "") or "" for s in summary
                )
                content_text = "\n\n".join(
                    _get(c, "text", "") or "" for c in content_list
                )
                thinking = summary_text or content_text or slot.thinking
                signature = json.dumps(_item_to_dict(item), default=str)
                _set_block(
                    slot.content_index,
                    ThinkingContent(thinking=thinking, thinking_signature=signature),
                )
                slot.thinking = thinking
                yield ThinkingEndEvent(
                    content_index=slot.content_index,
                    content=thinking,
                    partial=_snapshot(),
                )
                slots.pop(output_index, None)

            elif item_type == "message" and slot is not None and slot.kind == "text":
                content_list = _get(item, "content") or []
                text = ""
                for c in content_list:
                    if _get(c, "type") == "output_text":
                        text += _get(c, "text", "") or ""
                    else:
                        text += _get(c, "refusal", "") or ""
                signature = encode_text_signature_v1(
                    _get(item, "id", "") or "", _get(item, "phase")
                )
                _set_block(
                    slot.content_index,
                    TextContent(text=text, text_signature=signature),
                )
                slot.text = text
                yield TextEndEvent(
                    content_index=slot.content_index,
                    content=text,
                    partial=_snapshot(),
                )
                slots.pop(output_index, None)

            elif (
                item_type == "function_call"
                and slot is not None
                and slot.kind == "toolCall"
            ):
                raw_args = _get(item, "arguments", "") or slot.partial_json or "{}"
                slot.arguments = parse_streaming_json(raw_args)
                final_block = ToolCallContent(
                    tool_call_id=slot.tool_call_id,
                    tool_name=slot.tool_name,
                    input=slot.arguments,
                )
                _set_block(slot.content_index, final_block)
                yield ToolCallEndEvent(
                    content_index=slot.content_index,
                    tool_call=final_block,
                    partial=_snapshot(),
                )
                slots.pop(output_index, None)

        elif event_type in ("response.completed", "response.incomplete"):
            _finalize_response(_get(event, "response"))

        elif event_type == "error":
            code = _get(event, "code")
            message = _get(event, "message")
            raise RuntimeError(f"Error Code {code}: {message}")

        elif event_type == "response.failed":
            saw_terminal = True
            response = _get(event, "response")
            error = _get(response, "error")
            details = _get(response, "incomplete_details")
            if error is not None:
                code = _get(error, "code") or "unknown"
                emsg = _get(error, "message") or "no message"
                raise RuntimeError(f"{code}: {emsg}")
            reason = _get(details, "reason")
            if reason:
                raise RuntimeError(f"incomplete: {reason}")
            raise RuntimeError(
                "Unknown error (no error details in response)"
            )

    if not saw_terminal:
        raise RuntimeError(
            "OpenAI Responses stream ended before a terminal response event"
        )


__all__ = [
    "OPENAI_TOOL_CALL_PROVIDERS",
    "OpenAIResponsesStreamOptions",
    "ResponsesStreamState",
    "convert_responses_messages",
    "convert_responses_tools",
    "encode_text_signature_v1",
    "map_stop_reason",
    "parse_text_signature",
    "process_responses_stream",
]
