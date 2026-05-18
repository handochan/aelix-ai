"""Cross-provider message normalization — Sprint 6b (ADR-0047 §C / ADR-0048).

Pi parity: ``packages/ai/src/providers/transform-messages.ts`` (218
lines @ SHA 734e08e). This module runs **before** any per-adapter shape
transform — it handles the provider-agnostic hygiene Pi applies to every
``Context.messages`` list:

1. **Non-vision image downgrade** — when ``model.input`` does not include
   ``"image"``, replace :class:`ImageContent` blocks in user and
   tool-result messages with a placeholder :class:`TextContent` so the
   model still receives a hint that an image was present.
2. **Thinking block handling** — when an assistant turn came from a
   *different* model, drop encrypted/redacted thinking, convert plain
   thinking to text, and keep same-model signed thinking blocks intact
   so the model can replay them faithfully.
3. **Tool-call ID normalization** — adapters that need to rewrite
   ``ToolCallContent.tool_call_id`` (e.g. OpenAI's 40-char limit, or
   the OpenAI Responses pipe-format) pass ``normalize_tool_call_id``
   and the helper propagates the new id onto every subsequent
   :class:`ToolResultMessage` that referenced the original id.
4. **Orphan tool-call synthesis** — if an assistant turn calls tools but
   no matching :class:`ToolResultMessage` ever arrived, insert a
   synthetic ``"No result provided"`` (``is_error=True``) result right
   before the next user message or at the end of the conversation. This
   keeps the wire-format invariant "every tool call is answered" intact.
5. **Skip errored / aborted assistant turns** — when an assistant
   message's ``stop_reason`` is ``"error"`` or ``"aborted"`` Pi drops
   the entire turn from the replay so the next request doesn't carry
   over partial reasoning that would confuse the next call.

This helper is the boundary the Sprint 6d retrofit (P-50-followup) will
route the Anthropic adapter through; today only the OpenAI Completions
adapter calls it.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)

if TYPE_CHECKING:
    from aelix_ai.streaming import Model


# Placeholder text Pi inserts when downgrading image content for a
# non-vision model. Pi sends two slightly different strings depending on
# whether the image came from a user message or a tool result.
NON_VISION_USER_IMAGE_PLACEHOLDER = (
    "(image omitted: model does not support images)"
)
NON_VISION_TOOL_IMAGE_PLACEHOLDER = (
    "(tool image omitted: model does not support images)"
)


def _replace_images_with_placeholder(
    content: list[TextContent | ImageContent], placeholder: str
) -> list[TextContent | ImageContent]:
    """Drop images, collapse runs of placeholders.

    Pi parity: ``replaceImagesWithPlaceholder``
    (``transform-messages.ts:15``). Consecutive images yield a single
    placeholder; an existing placeholder is treated as a
    placeholder-leader so adjacent images don't repeat the hint.
    """

    out: list[TextContent | ImageContent] = []
    previous_was_placeholder = False
    for block in content:
        if isinstance(block, ImageContent):
            if not previous_was_placeholder:
                out.append(TextContent(text=placeholder))
            previous_was_placeholder = True
            continue
        out.append(block)
        previous_was_placeholder = isinstance(block, TextContent) and (
            block.text == placeholder
        )
    return out


def _downgrade_unsupported_images(
    messages: list[Message], model: Model
) -> list[Message]:
    """If the model lacks vision, replace images with placeholders.

    Pi parity: ``downgradeUnsupportedImages``
    (``transform-messages.ts:35``). Non-list user content is left
    untouched (Pi guards with ``Array.isArray``).
    """

    if "image" in (model.input or []):
        return messages
    out: list[Message] = []
    for msg in messages:
        if isinstance(msg, UserMessage) and isinstance(msg.content, list):
            out.append(
                replace(
                    msg,
                    content=_replace_images_with_placeholder(
                        list(msg.content), NON_VISION_USER_IMAGE_PLACEHOLDER
                    ),
                )
            )
        elif isinstance(msg, ToolResultMessage):
            out.append(
                replace(
                    msg,
                    content=_replace_images_with_placeholder(
                        list(msg.content), NON_VISION_TOOL_IMAGE_PLACEHOLDER
                    ),
                )
            )
        else:
            out.append(msg)
    return out


def _is_same_model(assistant: AssistantMessage, model: Model) -> bool:
    """Pi parity: ``isSameModel`` triple check.

    Pi compares the assistant turn's recorded ``provider`` / ``api`` /
    ``model`` against the target model. Sprint 6b (P-68): the provenance
    trio is now first-class on :class:`AssistantMessage`; when an
    assistant turn was minted by an older adapter (or a test fixture)
    without the columns set, the ``None`` defaults cause this to return
    ``False`` against the (non-None) target model fields — that matches
    Pi's "treat unknown provenance as cross-model" behavior.
    """

    return (
        assistant.api == model.api
        and assistant.provider == model.provider
        and assistant.model == model.id
    )


def _transform_assistant_content(
    assistant: AssistantMessage,
    model: Model,
    tool_call_id_map: dict[str, str],
    normalize_tool_call_id: Callable[[str, Model, AssistantMessage], str] | None,
) -> list[TextContent | ToolCallContent]:
    """Project an assistant message's blocks through the cross-model rules.

    Mirrors Pi ``transformMessages``' first-pass ``flatMap`` body
    (``transform-messages.ts:97-145``). Thinking blocks are not part of
    Aelix's content union yet, but we accept them defensively through
    ``getattr`` so a future port that adds them needs no change here.
    """

    same_model = _is_same_model(assistant, model)
    out: list[TextContent | ThinkingContent | ToolCallContent] = []

    for block in assistant.content:
        # Sprint 6b (P-58): ThinkingContent is now first-class on the
        # Aelix content union. The branch shape mirrors Pi exactly:
        # redacted blocks survive only on same-model; signed blocks
        # survive on same-model; empty blocks drop; cross-model with
        # text converts to plain TextContent.
        if isinstance(block, ThinkingContent):
            if block.redacted:
                if same_model:
                    out.append(block)
                continue
            if same_model and block.thinking_signature:
                out.append(block)
                continue
            if not block.thinking.strip():
                continue
            if same_model:
                out.append(block)
            else:
                out.append(TextContent(text=block.thinking))
            continue

        if isinstance(block, TextContent):
            if same_model:
                out.append(block)
            else:
                # Pi clones into a fresh TextContent so downstream
                # mutations don't ripple back into the conversation
                # snapshot — we do the same.
                out.append(TextContent(text=block.text))
            continue

        if isinstance(block, ToolCallContent):
            normalized = block
            # Cross-model: drop the thought signature so the new model
            # does not see opaque encrypted reasoning it cannot decode.
            if not same_model and getattr(block, "thought_signature", None):
                # The Aelix ToolCallContent dataclass has no
                # thought_signature field today; rebuilding through
                # ``replace`` is the safest no-op when the field is
                # absent.
                normalized = replace(block)
                with contextlib.suppress(AttributeError):
                    object.__delattr__(normalized, "thought_signature")

            if not same_model and normalize_tool_call_id is not None:
                new_id = normalize_tool_call_id(
                    normalized.tool_call_id, model, assistant
                )
                if new_id != normalized.tool_call_id:
                    tool_call_id_map[normalized.tool_call_id] = new_id
                    normalized = replace(normalized, tool_call_id=new_id)

            out.append(normalized)
            continue

        # Unknown block types fall through unchanged (defensive).
        out.append(block)  # type: ignore[arg-type]

    return out


def transform_messages(
    messages: list[Message],
    model: Model,
    *,
    normalize_tool_call_id: (
        Callable[[str, Model, AssistantMessage], str] | None
    ) = None,
) -> list[Message]:
    """Apply Pi cross-provider hygiene to ``messages``.

    Pi parity: ``transformMessages`` (``transform-messages.ts:64-220``).

    Args:
        messages: Aelix LLM-facing messages (the same list the per-adapter
            shape-transform receives).
        model: target :class:`Model`. Used for vision support detection
            and same-model comparisons.
        normalize_tool_call_id: optional adapter-supplied hook that
            rewrites a tool-call id for cross-provider compatibility
            (e.g. OpenAI's 40-char limit). When omitted, ids are
            preserved verbatim.

    Returns:
        A new list with the Pi rules applied — image downgrades, thinking
        block normalization, tool-call id rewrites, orphan tool-call
        synthesis, and errored-turn drops.
    """

    tool_call_id_map: dict[str, str] = {}
    image_aware = _downgrade_unsupported_images(messages, model)

    # First pass — transform messages in place. Tool-call ids may be
    # rewritten; the next pass uses ``tool_call_id_map`` to forward the
    # rewrite onto subsequent ToolResultMessages.
    transformed: list[Message] = []
    for msg in image_aware:
        if isinstance(msg, UserMessage):
            transformed.append(msg)
        elif isinstance(msg, ToolResultMessage):
            mapped = tool_call_id_map.get(msg.tool_call_id)
            if mapped is not None and mapped != msg.tool_call_id:
                transformed.append(replace(msg, tool_call_id=mapped))
            else:
                transformed.append(msg)
        elif isinstance(msg, AssistantMessage):
            new_content = _transform_assistant_content(
                msg, model, tool_call_id_map, normalize_tool_call_id
            )
            transformed.append(replace(msg, content=new_content))
        else:  # pragma: no cover — defensive fallthrough
            transformed.append(msg)

    # Second pass — orphan synthesis + errored/aborted assistant drop.
    # Tracks the still-unresolved tool calls from the most recent
    # successful assistant turn; if a user message arrives or the
    # conversation ends with unresolved calls, insert synthetic results.
    result: list[Message] = []
    pending_tool_calls: list[ToolCallContent] = []
    existing_tool_result_ids: set[str] = set()

    def _flush_synthetic() -> None:
        if not pending_tool_calls:
            return
        for tc in pending_tool_calls:
            if tc.tool_call_id in existing_tool_result_ids:
                continue
            # Sprint 6b (P-75): propagate ``tool_name`` onto the
            # synthetic result so adapters that wrap tool responses with
            # the function name (Moonshot, Together, Cloudflare AI
            # Gateway) can read it directly.
            result.append(
                ToolResultMessage(
                    tool_call_id=tc.tool_call_id,
                    content=[TextContent(text="No result provided")],
                    is_error=True,
                    tool_name=tc.tool_name,
                )
            )
        pending_tool_calls.clear()
        existing_tool_result_ids.clear()

    for msg in transformed:
        if isinstance(msg, AssistantMessage):
            _flush_synthetic()
            if msg.stop_reason in ("error", "aborted"):
                continue
            tool_calls = [
                b for b in msg.content if isinstance(b, ToolCallContent)
            ]
            if tool_calls:
                pending_tool_calls.extend(tool_calls)
                existing_tool_result_ids.clear()
            result.append(msg)
        elif isinstance(msg, ToolResultMessage):
            existing_tool_result_ids.add(msg.tool_call_id)
            result.append(msg)
        elif isinstance(msg, UserMessage):
            _flush_synthetic()
            result.append(msg)
        else:  # pragma: no cover
            result.append(msg)

    # Trailing orphans — synthesize before returning so the conversation
    # always ends with every tool call resolved.
    _flush_synthetic()

    return result


__all__ = [
    "NON_VISION_TOOL_IMAGE_PLACEHOLDER",
    "NON_VISION_USER_IMAGE_PLACEHOLDER",
    "transform_messages",
]
