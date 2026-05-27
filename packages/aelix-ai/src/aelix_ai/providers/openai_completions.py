"""OpenAI Chat Completions adapter — Sprint 6b (ADR-0045 §F + ADR-0047).

Pi parity: ``providers/openai-completions.ts:1-1074`` (SHA 734e08e).

The adapter is the second runtime provider Aelix ships (Sprint 6a landed
the Anthropic adapter). OpenRouter is served by the **same** adapter via
``provider="openrouter"`` + ``base_url="https://openrouter.ai/api/v1"``
+ auto-detected ``thinking_format == "openrouter"`` (Pi parity, P-48).

The streaming pipeline mirrors Pi:

1. Build SDK client (or use ``options.client`` injected by tests).
2. ``transform_messages`` → ``convert_messages`` → OpenAI param dict.
3. ``options.on_payload(params, model)`` callback (Pi ``onPayload``).
4. Open the SDK stream, fire ``options.on_response(ProviderResponse, model)``.
5. Emit ``AssistantStartEvent``; lazily emit per-block start, delta, end
   events as the SSE chunks arrive (text / thinking / tool calls).
6. Map the terminal ``finish_reason`` to Aelix ``StopReason`` and emit
   ``AssistantDoneEvent``. Errors emit ``AssistantErrorEvent`` with no
   ``done`` event.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from aelix_ai.api_registry import register_provider_object
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
from aelix_ai.models import clamp_thinking_level
from aelix_ai.providers._env_api_keys import get_env_api_key
from aelix_ai.providers._openai_client import create_async_client
from aelix_ai.providers._openai_compat import (
    OpenAICompletionsCompat,
    get_compat,
)
from aelix_ai.providers._sanitize_unicode import sanitize_surrogates
from aelix_ai.providers._streaming_json import parse_streaming_json
from aelix_ai.providers._transform_messages import transform_messages
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    ProviderResponse,
    SimpleStreamOptions,
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
    from aelix_ai.providers._base import Provider


# Pi parity: ``KnownApi`` value (``types.ts:7``).
OPENAI_COMPLETIONS_API: str = "openai-completions"


# Sprint 6a precedent — every adapter Aelix ships registers under the
# same ``source_id`` so ``unregister_providers_by_source`` cleanly
# removes the whole built-in suite.
BUILTIN_SOURCE_ID: str = "aelix-ai.builtin"


# Pi parity: `reasoningFields` (openai-completions.ts:315). Order
# matters — Pi picks the first non-empty match so providers that emit
# both ``reasoning_content`` and ``reasoning`` (chutes.ai) don't double
# the deltas.
REASONING_FIELDS: tuple[str, ...] = (
    "reasoning_content",
    "reasoning",
    "reasoning_text",
)


ToolChoice = Any  # Pi: "auto" | "none" | "required" | {type, function}


@dataclass(frozen=True)
class OpenAICompletionsOptions(SimpleStreamOptions):
    """Pi parity: ``OpenAICompletionsOptions`` (``openai-completions.ts:76``).

    Extends :class:`SimpleStreamOptions` with the two OpenAI-specific
    extras the adapter recognizes — ``tool_choice`` (forwarded verbatim
    onto the SDK ``tool_choice`` parameter) and ``reasoning_effort``
    (which maps to the per-thinking-format param the provider expects).
    """

    tool_choice: ToolChoice | None = None
    reasoning_effort: (
        Literal["minimal", "low", "medium", "high", "xhigh"] | None
    ) = None


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` only when it's a coroutine/awaitable.

    Mirrors the Anthropic adapter's helper. Pi callbacks may be sync or
    async — we accept both.
    """

    if inspect.isawaitable(value):
        return await value
    return value


def _map_stop_reason(reason: str | None) -> tuple[str, str | None]:
    """Pi parity: ``mapStopReason`` (``openai-completions.ts:1031-1055``).

    Returns ``(stop_reason, error_message_or_None)`` where ``stop_reason``
    is one of ``"stop" | "length" | "toolUse" | "error"`` — the Pi enum
    spelling verbatim (Sprint 6b W6 P-57 fix; previously returned
    ``"tool_use"`` which broke byte-parity with Pi). The Aelix agent
    loop only compares against ``"error" | "aborted"`` so the spelling
    change is invisible to terminal-detection.
    """

    if reason is None or reason == "null":
        return ("stop", None)
    if reason in ("stop", "end"):
        return ("stop", None)
    if reason == "length":
        return ("length", None)
    if reason in ("function_call", "tool_calls"):
        return ("toolUse", None)
    if reason == "content_filter":
        return ("error", "Provider finish_reason: content_filter")
    if reason == "network_error":
        return ("error", "Provider finish_reason: network_error")
    return ("error", f"Provider finish_reason: {reason}")


# === Message conversion ===


def _normalize_tool_call_id(
    tool_call_id: str, model: Model, _source: AssistantMessage
) -> str:
    """Pi parity: inline ``normalizeToolCallId``
    (``openai-completions.ts:743-756``).

    OpenAI's API rejects tool-call ids longer than 40 characters; the
    OpenAI Responses pipe-format encodes call ids as ``call_id|id``
    where ``id`` may contain non-allowed characters. Both shapes are
    sanitized to the conservative ``[a-zA-Z0-9_-]{,40}`` envelope.

    Sprint 6b (M-6): the 40-char clamp applies to every caller; the
    OpenAI wire format enforces the limit regardless of upstream
    provider so the previous ``model.provider == "openai"`` gate would
    let an OpenRouter / DeepSeek id through and trip the gateway's
    400 response.
    """

    if "|" in tool_call_id:
        call_id = tool_call_id.split("|", 1)[0]
        sanitized = "".join(
            ch if (ch.isalnum() or ch in "_-") else "_" for ch in call_id
        )
        return sanitized[:40]
    return tool_call_id if len(tool_call_id) <= 40 else tool_call_id[:40]


def _convert_user_message_content(
    msg: UserMessage, model: Model
) -> list[dict[str, Any]] | None:
    """Project Aelix user-message content onto OpenAI parts.

    Pi parity: ``convertMessages`` user branch
    (``openai-completions.ts:779-806``). Returns ``None`` when the
    content list collapsed to empty (e.g. image-only on a non-vision
    model) so the caller can skip the message.
    """

    parts: list[dict[str, Any]] = []
    accepts_image = "image" in (model.input or [])
    for item in msg.content:
        if isinstance(item, TextContent):
            parts.append(
                {"type": "text", "text": sanitize_surrogates(item.text)}
            )
        elif isinstance(item, ImageContent):
            if not accepts_image:
                continue
            # Pi parity (P-61): prefer the new ``mime_type`` + ``data``
            # split fields; the legacy ``source`` data-URL / base64
            # string is a Sprint-6a back-compat seam.
            if item.data:
                mime = item.mime_type or "image/png"
                url = f"data:{mime};base64,{item.data}"
            else:
                src = item.source or ""
                url = (
                    src
                    if src.startswith("data:") or src.startswith("http")
                    else f"data:image/png;base64,{src}"
                )
            parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts or None


def _convert_assistant_message(
    msg: AssistantMessage, compat: OpenAICompletionsCompat, model: Model
) -> dict[str, Any] | None:
    """Pi parity: ``convertMessages`` assistant branch
    (``openai-completions.ts:807-904``).

    Returns ``None`` when the message has no content AND no tool calls;
    Pi skips these (aborted turns that left no observable trace).
    """

    out: dict[str, Any] = {
        "role": "assistant",
        # Pi sends ``null`` for empty assistant content unless the
        # compat requires non-null; we mirror that here.
        "content": "" if compat.requires_assistant_after_tool_result else None,
    }

    # Collect text parts (Pi trims pure-whitespace text blocks).
    text_parts: list[dict[str, Any]] = []
    for block in msg.content:
        if isinstance(block, TextContent) and block.text.strip():
            text_parts.append(
                {"type": "text", "text": sanitize_surrogates(block.text)}
            )
    text_combined = "".join(part["text"] for part in text_parts)

    # Sprint 6b (P-58): ThinkingContent is now a first-class content
    # block on AssistantMessage; the OpenAI adapter populates the
    # ``thinking_signature`` with the captured reasoning_field name so
    # this branch can dispatch on the compat's ``requires_thinking_as_text``
    # toggle byte-for-byte with Pi.
    thinking_blocks: list[ThinkingContent] = [
        b for b in msg.content
        if isinstance(b, ThinkingContent) and b.thinking.strip()
    ]
    if thinking_blocks:
        if compat.requires_thinking_as_text:
            thinking_text = "\n\n".join(
                sanitize_surrogates(b.thinking) for b in thinking_blocks
            )
            out["content"] = [
                {"type": "text", "text": thinking_text},
                *text_parts,
            ]
        else:
            if text_combined:
                out["content"] = text_combined
            signature = thinking_blocks[0].thinking_signature
            if signature:
                out[signature] = "\n".join(b.thinking for b in thinking_blocks)
    elif text_combined:
        out["content"] = text_combined

    # Tool calls — Pi shape is ``[{id, type:"function", function:{name, arguments}}]``.
    tool_calls_blocks: list[ToolCallContent] = [
        b for b in msg.content if isinstance(b, ToolCallContent)
    ]
    if tool_calls_blocks:
        out["tool_calls"] = [
            {
                "id": tc.tool_call_id,
                "type": "function",
                "function": {
                    "name": tc.tool_name,
                    "arguments": json.dumps(tc.input or {}),
                },
            }
            for tc in tool_calls_blocks
        ]

    if (
        compat.requires_reasoning_content_on_assistant_messages
        and getattr(model, "reasoning", False)
        and "reasoning_content" not in out
    ):
        out["reasoning_content"] = ""

    has_content = (
        out.get("content") is not None
        and (
            (isinstance(out.get("content"), str) and len(out["content"]) > 0)
            or (
                isinstance(out.get("content"), list)
                and len(out["content"]) > 0
            )
        )
    )
    if not has_content and "tool_calls" not in out:
        return None
    return out


def convert_messages(
    model: Model,
    context: Context,
    compat: OpenAICompletionsCompat,
) -> list[dict[str, Any]]:
    """Aelix :class:`Context` → list of OpenAI ``ChatCompletionMessageParam``.

    Pi parity: ``convertMessages`` (``openai-completions.ts:736-977``).
    """

    out: list[dict[str, Any]] = []
    transformed = transform_messages(
        list(context.messages),
        model,
        normalize_tool_call_id=_normalize_tool_call_id,
    )

    if context.system_prompt:
        use_developer = (
            getattr(model, "reasoning", False) and compat.supports_developer_role
        )
        role = "developer" if use_developer else "system"
        out.append(
            {
                "role": role,
                "content": sanitize_surrogates(context.system_prompt),
            }
        )

    last_role: str | None = None
    i = 0
    n = len(transformed)
    while i < n:
        msg = transformed[i]

        # Pi parity: providers that disallow user-after-toolResult need a
        # synthetic assistant bridge.
        if (
            compat.requires_assistant_after_tool_result
            and last_role == "toolResult"
            and isinstance(msg, UserMessage)
        ):
            out.append(
                {
                    "role": "assistant",
                    "content": "I have processed the tool results.",
                }
            )

        if isinstance(msg, UserMessage):
            content_parts = _convert_user_message_content(msg, model)
            if content_parts is None:
                # Pi skips empty user messages.
                i += 1
                continue
            # Pi sends a bare string when the only content is text and
            # there's exactly one part — see openai-completions.ts:783.
            if len(content_parts) == 1 and content_parts[0].get("type") == "text":
                out.append({"role": "user", "content": content_parts[0]["text"]})
            else:
                out.append({"role": "user", "content": content_parts})
            last_role = "user"
            i += 1
            continue

        if isinstance(msg, AssistantMessage):
            assistant_param = _convert_assistant_message(msg, compat, model)
            if assistant_param is None:
                i += 1
                continue
            out.append(assistant_param)
            last_role = "assistant"
            i += 1
            continue

        if isinstance(msg, ToolResultMessage):
            # Pi coalesces consecutive tool results into individual ``role:tool``
            # entries and may follow them with a single ``role:user`` carrying
            # any image attachments.
            image_blocks: list[dict[str, Any]] = []
            j = i
            while j < n and isinstance(transformed[j], ToolResultMessage):
                tool_msg = transformed[j]
                assert isinstance(tool_msg, ToolResultMessage)
                text_result = "\n".join(
                    block.text
                    for block in tool_msg.content
                    if isinstance(block, TextContent)
                )
                has_images = any(
                    isinstance(c, ImageContent) for c in tool_msg.content
                )
                content_str = (
                    text_result if text_result else "(see attached image)"
                )
                tool_param: dict[str, Any] = {
                    "role": "tool",
                    "content": sanitize_surrogates(content_str),
                    "tool_call_id": tool_msg.tool_call_id,
                }
                if compat.requires_tool_result_name and tool_msg.tool_name:
                    tool_param["name"] = tool_msg.tool_name
                out.append(tool_param)

                if has_images and "image" in (model.input or []):
                    for block in tool_msg.content:
                        if isinstance(block, ImageContent):
                            # Pi parity (P-61): prefer the explicit
                            # mime_type + data split; fall back to the
                            # legacy ``source`` data-URL seam.
                            if block.data:
                                mime = block.mime_type or "image/png"
                                url = f"data:{mime};base64,{block.data}"
                            else:
                                src = block.source or ""
                                url = (
                                    src
                                    if src.startswith(("data:", "http"))
                                    else f"data:image/png;base64,{src}"
                                )
                            image_blocks.append(
                                {"type": "image_url", "image_url": {"url": url}}
                            )
                j += 1

            i = j
            if image_blocks:
                if compat.requires_assistant_after_tool_result:
                    out.append(
                        {
                            "role": "assistant",
                            "content": "I have processed the tool results.",
                        }
                    )
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Attached image(s) from tool result:",
                            },
                            *image_blocks,
                        ],
                    }
                )
                last_role = "user"
            else:
                last_role = "toolResult"
            continue

        # Defensive fallthrough (unknown message types).
        i += 1

    return out


# === Tool conversion ===


def convert_tools(
    tools: list[Any], compat: OpenAICompletionsCompat
) -> list[dict[str, Any]]:
    """Aelix Tool objects → OpenAI ``ChatCompletionTool`` list.

    Pi parity: ``convertTools`` (``openai-completions.ts:979-993``). The
    ``strict`` field is omitted on providers whose compat rejects
    unknown keys (Moonshot, Together, Cloudflare AI Gateway).
    """

    out: list[dict[str, Any]] = []
    for tool in tools:
        # Pi parity (P-63): read ``tool.parameters`` only — the Anthropic
        # ``input_schema`` field is an upstream-leak that doesn't belong
        # in this adapter. Callers that ship a Tool without
        # ``parameters`` surface as a clean empty-object schema rather
        # than dipping into an Anthropic-shape attribute.
        params = getattr(tool, "parameters", None) or {
            "type": "object",
            "properties": {},
        }
        entry: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": getattr(tool, "name", "unknown"),
                "description": getattr(tool, "description", ""),
                "parameters": params,
            },
        }
        if compat.supports_strict_mode is not False:
            entry["function"]["strict"] = False
        out.append(entry)
    return out


# === Cache control (Pi parity, openai-completions.ts:621-734) ===


def _resolve_cache_retention(
    cache_retention: str | None,
) -> str:
    """Pi parity: ``resolveCacheRetention`` (``:101-109``).

    Aelix does not read ``PI_CACHE_RETENTION`` — that's an
    environment-coupled toggle Pi exposes for ops. Sprint 6b honors the
    caller-supplied value, defaulting to ``"short"``.
    """

    return cache_retention or "short"


def _get_compat_cache_control(
    compat: OpenAICompletionsCompat, cache_retention: str
) -> dict[str, Any] | None:
    """Pi parity: ``getCompatCacheControl`` (``:621-631``)."""

    if compat.cache_control_format != "anthropic" or cache_retention == "none":
        return None
    out: dict[str, Any] = {"type": "ephemeral"}
    if cache_retention == "long" and compat.supports_long_cache_retention:
        out["ttl"] = "1h"
    return out


def _apply_anthropic_cache_control(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    cache_control: dict[str, Any],
) -> None:
    """Inject ``cache_control`` markers Pi-style (mutates in place).

    Pi parity: ``applyAnthropicCacheControl`` + helpers
    (``openai-completions.ts:633-734``). The chained helpers walk
    system, last tool, last user/assistant message and inject the
    marker on the appropriate text part.
    """

    # System / developer prompt — first matching message wins.
    for message in messages:
        if message.get("role") in ("system", "developer"):
            _add_cache_control_to_text_content(message, cache_control)
            break

    # Last tool.
    if tools:
        tools[-1]["cache_control"] = cache_control

    # Last user/assistant message.
    for message in reversed(messages):
        if (
            message.get("role") in ("user", "assistant")
            and _add_cache_control_to_text_content(message, cache_control)
        ):
            break


def _add_cache_control_to_text_content(
    message: dict[str, Any], cache_control: dict[str, Any]
) -> bool:
    """Pi parity: ``addCacheControlToTextContent`` (``:698``).

    Returns ``True`` when a marker was applied (so callers can stop
    walking after the first hit).
    """

    content = message.get("content")
    if isinstance(content, str):
        if not content:
            return False
        message["content"] = [
            {"type": "text", "text": content, "cache_control": cache_control}
        ]
        return True
    if not isinstance(content, list):
        return False
    for part in reversed(content):
        if isinstance(part, dict) and part.get("type") == "text":
            part["cache_control"] = cache_control
            return True
    return False


# === Param assembly ===


def _has_tool_history(messages: list[Message]) -> bool:
    """Pi parity: ``hasToolHistory`` (``openai-completions.ts:47``)."""

    for msg in messages:
        if isinstance(msg, ToolResultMessage):
            return True
        if isinstance(msg, AssistantMessage) and any(
            isinstance(b, ToolCallContent) for b in msg.content
        ):
            return True
    return False


def build_params(
    model: Model,
    context: Context,
    options: OpenAICompletionsOptions | SimpleStreamOptions | None,
    compat: OpenAICompletionsCompat,
    cache_retention: str,
) -> dict[str, Any]:
    """Assemble ``client.chat.completions.create`` kwargs.

    Pi parity: ``buildParams`` (``openai-completions.ts:498-619``).
    """

    messages = convert_messages(model, context, compat)
    cache_control = _get_compat_cache_control(compat, cache_retention)

    base_url = getattr(model, "base_url", "") or ""
    session_id = getattr(options, "session_id", None)

    params: dict[str, Any] = {
        "model": model.id or model.name,
        "messages": messages,
        "stream": True,
    }

    prompt_cache_key: str | None = None
    # Pi parity (M-1): explicit parens so the two branches are obviously
    # OR'd as a whole — "this is an OpenAI host AND retention isn't off"
    # OR "retention is long AND the provider keeps long retention".
    if (
        ("api.openai.com" in base_url and cache_retention != "none")
        or (
            cache_retention == "long" and compat.supports_long_cache_retention
        )
    ):
        prompt_cache_key = session_id
    if prompt_cache_key is not None:
        params["prompt_cache_key"] = prompt_cache_key

    if cache_retention == "long" and compat.supports_long_cache_retention:
        params["prompt_cache_retention"] = "24h"

    if compat.supports_usage_in_streaming:
        params["stream_options"] = {"include_usage": True}

    if compat.supports_store:
        params["store"] = False

    max_tokens = getattr(model, "max_tokens", 0) or 0
    context_window = getattr(model, "context_window", 0) or 0
    # ADR-0114: 127 catalog models list ``maxTokens == contextWindow``.
    # Sending the full context window as the *output* cap leaves no room
    # for the prompt and 400s on strict OpenRouter endpoints ("this
    # endpoint's maximum context length is N tokens. However, you
    # requested ... in the output"). When the cap is meaningless (>= the
    # whole context window), omit it so the provider clamps the output to
    # whatever the context allows. Models with a real, smaller output cap
    # keep sending it unchanged.
    cap_is_meaningful = not (context_window and max_tokens >= context_window)
    if max_tokens > 0 and cap_is_meaningful:
        if compat.max_tokens_field == "max_tokens":
            params["max_tokens"] = max_tokens
        else:
            params["max_completion_tokens"] = max_tokens

    tools = list(context.tools or [])
    if tools:
        params["tools"] = convert_tools(tools, compat)
        if compat.zai_tool_stream:
            params["tool_stream"] = True
    elif _has_tool_history(list(context.messages)):
        # Pi parity: Anthropic-via-proxy requires the field when history
        # contains tool calls / results.
        params["tools"] = []

    if cache_control:
        _apply_anthropic_cache_control(
            messages, params.get("tools"), cache_control
        )

    tool_choice = getattr(options, "tool_choice", None)
    if tool_choice is not None:
        params["tool_choice"] = tool_choice

    reasoning_effort = getattr(options, "reasoning_effort", None)
    is_reasoning = bool(getattr(model, "reasoning", False))

    if is_reasoning and compat.thinking_format in ("zai", "qwen"):
        params["enable_thinking"] = bool(reasoning_effort)
    elif compat.thinking_format == "qwen-chat-template" and is_reasoning:
        params["chat_template_kwargs"] = {
            "enable_thinking": bool(reasoning_effort),
            "preserve_thinking": True,
        }
    elif compat.thinking_format == "deepseek" and is_reasoning:
        params["thinking"] = {
            "type": "enabled" if reasoning_effort else "disabled"
        }
        if reasoning_effort:
            params["reasoning_effort"] = reasoning_effort
    elif compat.thinking_format == "openrouter" and is_reasoning:
        if reasoning_effort:
            params["reasoning"] = {"effort": reasoning_effort}
        else:
            params["reasoning"] = {"effort": "none"}
    elif compat.thinking_format == "together" and is_reasoning:
        params["reasoning"] = {"enabled": bool(reasoning_effort)}
        if reasoning_effort and compat.supports_reasoning_effort:
            params["reasoning_effort"] = reasoning_effort
    elif (
        reasoning_effort
        and is_reasoning
        and compat.supports_reasoning_effort
    ):
        params["reasoning_effort"] = reasoning_effort

    # OpenRouter routing — Pi parity (M-1 / P-59): read the merged
    # compat the caller already resolved via :func:`get_compat`. That
    # path picks up both the auto-detected ``open_router_routing`` and
    # any user-supplied override. The dict-shaped ``model.compat`` legacy
    # path remains valid because ``get_compat`` already merged it onto
    # the dataclass before we got here.
    if "openrouter.ai" in base_url and compat.open_router_routing:
        params["provider"] = compat.open_router_routing

    # Vercel AI Gateway routing — same precedent, read from the merged
    # compat.
    if "ai-gateway.vercel.sh" in base_url:
        gw = compat.vercel_gateway_routing
        if gw and (gw.get("only") or gw.get("order")):
            gateway: dict[str, list[str]] = {}
            if gw.get("only"):
                gateway["only"] = gw["only"]
            if gw.get("order"):
                gateway["order"] = gw["order"]
            params["providerOptions"] = {"gateway": gateway}

    return params


# === Streaming body ===


def _coerce_options(
    options: OpenAICompletionsOptions | SimpleStreamOptions | None,
) -> OpenAICompletionsOptions:
    """Widen a generic :class:`SimpleStreamOptions` into the OpenAI shape.

    The harness passes a ``SimpleStreamOptions`` so callers can register
    the adapter under the bare Protocol — we copy the fields onto an
    :class:`OpenAICompletionsOptions` so the rest of the body can access
    ``tool_choice`` / ``reasoning_effort`` uniformly.
    """

    if options is None:
        return OpenAICompletionsOptions()
    if isinstance(options, OpenAICompletionsOptions):
        return options
    return OpenAICompletionsOptions(
        api_key=options.api_key,
        headers=dict(options.headers or {}),
        metadata=dict(options.metadata or {}),
        signal=options.signal,
        cache_retention=options.cache_retention,
        transport=options.transport,
        timeout_ms=options.timeout_ms,
        max_retries=options.max_retries,
        max_retry_delay_ms=options.max_retry_delay_ms,
        reasoning=options.reasoning,
        session_id=options.session_id,
        on_payload=options.on_payload,
        on_response=options.on_response,
        client=options.client,
    )


# OpenRouter / provider extension params that the OpenAI **Python** SDK
# rejects as top-level keyword arguments. Pi's TypeScript SDK forwards
# unknown top-level fields straight into the JSON body, but the Python
# ``openai`` SDK validates kwargs and raises ``TypeError`` on unknown
# ones (e.g. ``AsyncCompletions.create() got an unexpected keyword
# argument 'reasoning'``). The SDK's documented escape hatch is
# ``extra_body``, which is merged verbatim into the request payload — so
# these extensions reach the wire unchanged. ``reasoning_effort`` is a
# first-class OpenAI param (o-series) and stays top-level.
#
# Sprint 6h₁₃ · ADR-0114. Without this relocation, every OpenRouter
# reasoning model (which gets a ``reasoning`` param) and every model with
# provider routing (``provider``) errored on the very first chunk through
# the real SDK — masked until now because the test fakes accept ``**kwargs``.
_EXTRA_BODY_PARAM_KEYS: frozenset[str] = frozenset(
    {
        "reasoning",
        "provider",
        "enable_thinking",
        "chat_template_kwargs",
        "thinking",
        "tool_stream",
        "providerOptions",
        # ``prompt_cache_key`` IS a native OpenAI kwarg, but the
        # ``_retention`` variant is not (verified against ``openai`` SDK):
        # it errors the same way on the long-cache-retention path.
        "prompt_cache_retention",
    }
)


def _relocate_extra_body_params(params: dict[str, Any]) -> dict[str, Any]:
    """Move non-OpenAI extension params into ``extra_body`` (mutates+returns).

    Called at the SDK boundary so :func:`build_params` keeps its Pi-parity
    flat shape (and the build_params unit tests stay valid) while the real
    ``client.chat.completions.create`` only ever sees keyword arguments it
    accepts. Any ``extra_body`` already present (e.g. injected by an
    ``on_payload`` hook) is preserved; on a key collision the relocated
    top-level value takes precedence over the pre-existing ``extra_body``
    entry of the same name.
    """

    extra: dict[str, Any] = dict(params.get("extra_body") or {})
    for key in _EXTRA_BODY_PARAM_KEYS:
        if key in params:
            extra[key] = params.pop(key)
    if extra:
        params["extra_body"] = extra
    return params


def _attr_or_key(obj: Any, name: str) -> Any:
    """Read ``name`` from an attr (SDK object) or a key (dict mock)."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _usage_to_dict(usage: Any) -> dict[str, Any] | None:
    """Map an OpenAI/OpenRouter ``usage`` payload to the Aelix usage dict.

    Keys match what :func:`session.compaction.calculate_context_tokens` and
    the session-stats aggregator read (``total_tokens`` / ``input_tokens`` /
    ``output_tokens`` / ``cache_read``), so the context-usage meter and
    ``/cost`` reflect real numbers. Returns ``None`` for an empty payload.
    """
    if usage is None:
        return None
    prompt = _attr_or_key(usage, "prompt_tokens") or 0
    completion = _attr_or_key(usage, "completion_tokens") or 0
    total = _attr_or_key(usage, "total_tokens") or (prompt + completion)
    cached = 0
    details = _attr_or_key(usage, "prompt_tokens_details")
    if details is not None:
        cached = _attr_or_key(details, "cached_tokens") or 0
    if not (prompt or completion or total):
        return None
    # Emit BOTH spellings: the session-stats aggregator reads ``input``/
    # ``output`` (Usage-dataclass field names) while
    # ``calculate_context_tokens`` accepts ``total_tokens`` /
    # ``input_tokens``. Covering both keeps /cost and the meter consistent.
    return {
        "input": int(prompt),
        "output": int(completion),
        "input_tokens": int(prompt),
        "output_tokens": int(completion),
        "total_tokens": int(total),
        "cache_read": int(cached),
    }


async def _open_stream(
    client: Any, params: dict[str, Any], request_options: dict[str, Any]
) -> tuple[AsyncIterator[Any], Any]:
    """Open the SDK stream + return ``(iterator, raw_response)``.

    Pi parity (C-1 / P-60): the real ``openai>=1.50`` SDK exposes
    ``client.chat.completions.with_raw_response.create(**params,
    **request_options)`` which returns a raw response wrapper whose
    ``.parse()`` gives the ``AsyncStream`` and whose ``.http_response``
    gives the underlying httpx ``Response``. ``params`` is forwarded as
    keyword arguments so the SDK can validate them.

    Test fakes mirror the same surface — see
    ``tests/providers/test_openai_completions_streaming.py`` for the
    ``_WithRawResponse`` shape they implement.
    """

    create_fn = client.chat.completions.with_raw_response.create
    raw = create_fn(**params, **request_options)
    if inspect.isawaitable(raw):
        raw = await raw

    iterator = raw.parse()
    if inspect.isawaitable(iterator):
        iterator = await iterator
    return iterator, raw


async def stream_openai_completions(
    model: Model,
    context: Context,
    options: OpenAICompletionsOptions | SimpleStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Pi parity: ``streamOpenAICompletions``
    (``openai-completions.ts:111-419``).

    The body iterates SDK chunks and lazily emits the Aelix events as
    the first ``content`` / ``reasoning_content`` / ``tool_calls`` delta
    of each kind arrives. The terminal ``finish_reason`` maps onto Pi's
    success-stop enum; any exception emits :class:`AssistantErrorEvent`
    with no :class:`AssistantDoneEvent`.
    """

    opts = _coerce_options(options)
    output_content: list[Any] = []
    output = AssistantMessage(content=[])
    stop_reason: str | None = "stop"
    error_message: str | None = None

    # Block-buffer registry — Pi's lazy ``ensureTextBlock`` /
    # ``ensureThinkingBlock`` / ``ensureToolCallBlock`` translated to
    # explicit state.
    text_index: int | None = None
    thinking_index: int | None = None
    tool_blocks_by_stream_index: dict[int, dict[str, Any]] = {}
    tool_blocks_by_id: dict[str, dict[str, Any]] = {}
    # The block dicts carry: ``content_index`` (position in output_content),
    # ``id``, ``name``, ``partial_args``, ``stream_index``.
    has_finish_reason = False
    captured_usage: dict[str, Any] | None = None

    try:
        compat = get_compat(model)
        cache_retention = _resolve_cache_retention(opts.cache_retention)

        # Resolve API key — Pi parity: ``apiKey || getEnvApiKey(model.provider) || ""``.
        api_key = opts.api_key or get_env_api_key(model.provider) or ""

        # Build SDK client (or use injected ``options.client``).
        client = opts.client or create_async_client(
            api_key=api_key,
            base_url=getattr(model, "base_url", "") or None,
            default_headers=opts.headers or None,
            timeout_ms=opts.timeout_ms,
            max_retries=opts.max_retries,
        )

        # Assemble params + run on_payload callback (Pi parity ``:143-147``).
        params = build_params(model, context, opts, compat, cache_retention)
        if opts.on_payload is not None:
            next_params = await _maybe_await(opts.on_payload(params, model))
            if next_params is not None:
                params = next_params

        # Pi parity gap (ADR-0114): relocate OpenRouter/provider extension
        # params into ``extra_body`` AFTER on_payload (the hook sees the
        # Pi-shaped flat params) so the Python SDK accepts the call.
        params = _relocate_extra_body_params(params)

        # Pi parity (P-60): ``signal`` and ``max_retries`` are NOT valid
        # per-request kwargs on ``openai>=1.50`` SDK. ``max_retries`` is
        # a client-level setting (use ``client.with_options(max_retries=N)``
        # if you need per-call override). The harness signal is observed
        # via :attr:`SimpleStreamOptions.signal` at the loop level; the
        # SDK has no native AbortSignal binding.
        request_options: dict[str, Any] = {}
        if opts.timeout_ms is not None:
            request_options["timeout"] = opts.timeout_ms / 1000.0

        # Open the SDK stream + fire on_response (Pi parity ``:153-156``).
        iterator, raw_response = await _open_stream(
            client, params, request_options
        )
        if opts.on_response is not None:
            # Pi parity (W4 M-5): the raw wrapper exposes ``.http_response``
            # (the underlying httpx Response). Some test fakes attach the
            # status / headers directly to the wrapper for ergonomic mocks
            # — that legacy shape stays observable through the
            # ``status_code`` / ``headers`` attribute hop.
            http_response = getattr(raw_response, "http_response", raw_response)
            status_code = getattr(http_response, "status_code", None)
            if status_code is None:
                status_code = getattr(http_response, "status", 200)
            provider_response = ProviderResponse(
                status=int(status_code),
                headers={
                    str(k): str(v)
                    for k, v in dict(
                        getattr(http_response, "headers", {}) or {}
                    ).items()
                },
            )
            await _maybe_await(opts.on_response(provider_response, model))

        # AssistantStart (Pi parity ``:157``).
        partial = AssistantMessage(content=list(output_content))
        yield AssistantStartEvent(partial=partial)

        async for chunk in iterator:
            if not chunk:
                continue

            # Capture the streaming usage payload. The final usage-only chunk
            # (``stream_options.include_usage``) has EMPTY choices, so read it
            # BEFORE the choices guard below. Populates AssistantMessage.usage
            # → the context-window meter + /cost reflect real token counts
            # (ADR-0116; the Sprint-6b "ignore usage" deferral is now closed).
            chunk_usage = _usage_to_dict(getattr(chunk, "usage", None))
            if chunk_usage is not None:
                captured_usage = chunk_usage

            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            choice = choices[0]

            finish_reason = getattr(choice, "finish_reason", None)
            if finish_reason:
                sr, em = _map_stop_reason(finish_reason)
                stop_reason = sr
                if em:
                    error_message = em
                has_finish_reason = True

            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            # --- Text content delta ---
            text_chunk = getattr(delta, "content", None)
            if text_chunk:
                if text_index is None:
                    text_index = len(output_content)
                    output_content.append(TextContent(text=""))
                    partial = AssistantMessage(content=list(output_content))
                    yield TextStartEvent(
                        content_index=text_index, partial=partial
                    )
                # Update the buffer + replace the (frozen) block.
                current = output_content[text_index]
                if isinstance(current, TextContent):
                    output_content[text_index] = TextContent(
                        text=current.text + text_chunk
                    )
                partial = AssistantMessage(content=list(output_content))
                yield TextDeltaEvent(
                    delta=text_chunk,
                    content_index=text_index,
                    partial=partial,
                )

            # --- Reasoning / thinking delta ---
            reasoning_value: str | None = None
            reasoning_field: str | None = None
            for field_name in REASONING_FIELDS:
                value = getattr(delta, field_name, None)
                if isinstance(value, str) and len(value) > 0:
                    reasoning_value = value
                    reasoning_field = field_name
                    break
                # Also support dict-shaped deltas (test mocks).
                if isinstance(delta, dict):
                    dv = delta.get(field_name)
                    if isinstance(dv, str) and len(dv) > 0:
                        reasoning_value = dv
                        reasoning_field = field_name
                        break
            if reasoning_value:
                if thinking_index is None:
                    thinking_index = len(output_content)
                    # Sprint 6b (P-58/P-67): ThinkingContent is now a
                    # first-class block on the Aelix content union; the
                    # captured ``reasoning_field`` name doubles as the
                    # ``thinking_signature`` Pi uses to flag the wire
                    # field on the replay assistant message.
                    output_content.append(
                        ThinkingContent(
                            thinking="",
                            thinking_signature=reasoning_field or "",
                        )
                    )
                    partial = AssistantMessage(content=list(output_content))
                    yield ThinkingStartEvent(
                        content_index=thinking_index, partial=partial
                    )
                # Extend the buffer (frozen dataclass → replace).
                current = output_content[thinking_index]
                if isinstance(current, ThinkingContent):
                    output_content[thinking_index] = ThinkingContent(
                        thinking=current.thinking + reasoning_value,
                        thinking_signature=current.thinking_signature,
                        redacted=current.redacted,
                    )
                partial = AssistantMessage(content=list(output_content))
                yield ThinkingDeltaEvent(
                    delta=reasoning_value,
                    content_index=thinking_index,
                    partial=partial,
                )

            # --- Tool call deltas ---
            tool_calls = getattr(delta, "tool_calls", None)
            if tool_calls is None and isinstance(delta, dict):
                tool_calls = delta.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    stream_index = _get_attr(tc, "index", None)
                    tc_id = _get_attr(tc, "id", "") or ""
                    function = _get_attr(tc, "function", None)
                    fn_name = _get_attr(function, "name", "") or ""
                    fn_arguments = _get_attr(function, "arguments", "") or ""

                    block = None
                    if stream_index is not None:
                        block = tool_blocks_by_stream_index.get(stream_index)
                    if block is None and tc_id:
                        block = tool_blocks_by_id.get(tc_id)
                    if block is None:
                        # New tool call → lazy-emit toolcall_start.
                        block = {
                            "content_index": len(output_content),
                            "id": tc_id,
                            "name": fn_name,
                            "partial_args": "",
                            "stream_index": stream_index,
                        }
                        if stream_index is not None:
                            tool_blocks_by_stream_index[stream_index] = block
                        if tc_id:
                            tool_blocks_by_id[tc_id] = block
                        output_content.append(
                            ToolCallContent(
                                tool_call_id=tc_id,
                                tool_name=fn_name,
                                input={},
                            )
                        )
                        partial = AssistantMessage(content=list(output_content))
                        yield ToolCallStartEvent(
                            content_index=block["content_index"],
                            partial=partial,
                        )
                    # Pi updates stale id/name/stream_index in place.
                    if not block["id"] and tc_id:
                        block["id"] = tc_id
                        tool_blocks_by_id[tc_id] = block
                    if not block["name"] and fn_name:
                        block["name"] = fn_name
                    if (
                        stream_index is not None
                        and block.get("stream_index") is None
                    ):
                        block["stream_index"] = stream_index
                        tool_blocks_by_stream_index[stream_index] = block

                    delta_str = ""
                    if fn_arguments:
                        delta_str = fn_arguments
                        block["partial_args"] = (
                            (block.get("partial_args") or "") + fn_arguments
                        )
                        parsed = parse_streaming_json(block["partial_args"])
                        idx = block["content_index"]
                        if idx < len(output_content) and isinstance(
                            output_content[idx], ToolCallContent
                        ):
                            output_content[idx] = ToolCallContent(
                                tool_call_id=block["id"],
                                tool_name=block["name"],
                                input=parsed,
                            )
                    partial = AssistantMessage(content=list(output_content))
                    yield ToolCallDeltaEvent(
                        delta=delta_str,
                        content_index=block["content_index"],
                        partial=partial,
                        tool_call_id=block["id"],
                        tool_name=block["name"],
                    )

        # End-of-stream: emit per-block end events (Pi parity ``:382-384``).
        for index, block in enumerate(list(output_content)):
            partial = AssistantMessage(content=list(output_content))
            if isinstance(block, TextContent):
                yield TextEndEvent(
                    content_index=index,
                    content=block.text,
                    partial=partial,
                )
            elif isinstance(block, ThinkingContent):
                yield ThinkingEndEvent(
                    content_index=index,
                    content=block.thinking,
                    partial=partial,
                )
            elif isinstance(block, ToolCallContent):
                yield ToolCallEndEvent(
                    content_index=index,
                    tool_call=block,
                    partial=partial,
                )

        # Abort detection — Pi parity ``:385-387``.
        if opts.signal is not None and getattr(opts.signal, "aborted", False):
            raise RuntimeError("Request was aborted")
        if stop_reason == "aborted":
            raise RuntimeError("Request was aborted")
        if stop_reason == "error":
            raise RuntimeError(
                error_message or "Provider returned an error stop reason"
            )
        if not has_finish_reason:
            raise RuntimeError("Stream ended without finish_reason")

        # Success — emit Done with the assembled message. Sprint 6b (P-68)
        # populates the provenance trio so ``_transform_messages._is_same_model``
        # can keep signed thinking blocks intact on the next request.
        output = AssistantMessage(
            content=list(output_content),
            stop_reason=stop_reason,
            error_message=error_message,
            usage=captured_usage,
            api=model.api,
            provider=model.provider,
            model=model.id,
        )
        # Pi parity: ``stop_reason`` is already in Pi spelling per
        # :func:`_map_stop_reason`; the ``done`` event reuses it
        # verbatim (one of ``"stop" | "length" | "toolUse"``). Unknown
        # reasons would have routed to the error path above.
        done_reason: Literal["stop", "length", "toolUse"]
        if stop_reason == "toolUse":
            done_reason = "toolUse"
        elif stop_reason == "length":
            done_reason = "length"
        else:
            done_reason = "stop"
        yield AssistantDoneEvent(reason=done_reason, message=output)

    except Exception as exc:  # noqa: BLE001
        aborted = bool(
            opts.signal is not None and getattr(opts.signal, "aborted", False)
        )
        reason: Literal["aborted", "error"] = "aborted" if aborted else "error"
        err_msg = str(exc) if str(exc) else type(exc).__name__

        # Pi parity (M-4): scratch state lives off-block in the
        # ``tool_blocks_by_*`` registries; no per-block stripping needed
        # (Pi parity at :402-407 N/A because Aelix scratch is
        # structurally off-block, never copied onto the frozen
        # ToolCallContent dataclass).
        cleaned: list[Any] = list(output_content)

        # OpenRouter shape — surface ``error.metadata.raw`` if present.
        raw_meta = None
        err_attr = getattr(exc, "error", None)
        if isinstance(err_attr, dict):
            meta = err_attr.get("metadata")
            if isinstance(meta, dict):
                raw_meta = meta.get("raw")
        if raw_meta:
            err_msg = f"{err_msg}\n{raw_meta}"

        error_output = AssistantMessage(
            content=cleaned,
            stop_reason=reason,
            error_message=err_msg,
            api=model.api,
            provider=model.provider,
            model=model.id,
        )
        yield AssistantErrorEvent(
            reason=reason, error=error_output, error_message=err_msg
        )


def _get_attr(obj: Any, name: str, default: Any) -> Any:
    """Read ``name`` from ``obj``, supporting dicts and dataclass-style attrs."""

    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def stream_simple_openai_completions(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Pi parity: ``streamSimpleOpenAICompletions``
    (``openai-completions.ts:421-441``).

    Pi parity (P-62): this is a **sync factory** that resolves the env
    API key + clamps the reasoning level eagerly BEFORE returning the
    async generator — matching Pi's ``streamSimple`` which throws
    synchronously when ``getEnvApiKey`` returns ``None``. Callers see
    the auth error on the bare call, not on the first ``__anext__``.
    """

    opts = _coerce_options(options)
    if not opts.api_key:
        api_key = get_env_api_key(model.provider)
        if not api_key:
            raise RuntimeError(
                f"No API key for provider: {model.provider}"
            )
        opts = replace(opts, api_key=api_key)

    # Pi parity (P-62): use the shared :func:`clamp_thinking_level` so
    # ``xhigh`` clamps to ``high`` and unknown values surface as ``None``.
    reasoning_effort = opts.reasoning_effort
    if reasoning_effort is None and isinstance(opts.reasoning, str):
        reasoning_effort = clamp_thinking_level(model, opts.reasoning)
    elif reasoning_effort is not None:
        reasoning_effort = clamp_thinking_level(model, reasoning_effort)
    opts = replace(opts, reasoning_effort=reasoning_effort)

    return stream_openai_completions(model, context, opts)


# === Provider registration ===


class _OpenAICompletionsProvider:
    """Concrete :class:`Provider` Protocol implementer for ``openai-completions``."""

    api: str = OPENAI_COMPLETIONS_API
    source_id: str | None = None

    def stream(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        return stream_openai_completions(model, context, options)

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        return stream_simple_openai_completions(model, context, options)


OPENAI_COMPLETIONS_PROVIDER: Provider = _OpenAICompletionsProvider()


def register_all() -> None:
    """Register the OpenAI Completions adapter on the global registry.

    Pi parity: ``providers/openai-completions.ts`` register entry-point.
    Idempotent — subsequent calls replace the registry entry under
    ``api == "openai-completions"`` with the same singleton.
    """

    register_provider_object(
        OPENAI_COMPLETIONS_PROVIDER, source_id=BUILTIN_SOURCE_ID
    )


__all__ = [
    "BUILTIN_SOURCE_ID",
    "OPENAI_COMPLETIONS_API",
    "OPENAI_COMPLETIONS_PROVIDER",
    "OpenAICompletionsOptions",
    "build_params",
    "convert_messages",
    "convert_tools",
    "register_all",
    "stream_openai_completions",
    "stream_simple_openai_completions",
]


# Coerce the Callable to satisfy lint (unused-import-guard for
# Callable). ``_normalize_tool_call_id`` already returns ``str``.
_: Callable[..., str] = _normalize_tool_call_id
