"""Anthropic provider adapter — Sprint 6a (ADR-0045 §B).

Pi parity: ``providers/anthropic.ts:428-687`` (SHA 734e08e). Ports the
Pi adapter body using the official ``anthropic`` Python SDK
(``>=0.40,<1.0``).

OAuth tokens (``sk-ant-oat…``) are rejected with
``AgentHarnessError("auth")`` in Sprint 6a — the full claude.ai OAuth
login + refresh flow lands Sprint 6c (ADR-0020).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from aelix_ai.api_registry import register_provider_object
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
)
from aelix_ai.providers._anthropic_client import create_async_client
from aelix_ai.providers._anthropic_transforms import (
    build_params,
    is_oauth_token,
    map_stop_reason,
)
from aelix_ai.providers._base import Provider
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
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

if TYPE_CHECKING:
    pass


# Pi ``KnownApi`` value (``types.ts:6``).
ANTHROPIC_API: str = "anthropic-messages"


# Source identifier used by ``register_all`` so
# ``unregister_providers_by_source("aelix-ai.builtin")`` cleanly removes
# everything Aelix ships out of the box.
BUILTIN_SOURCE_ID: str = "aelix-ai.builtin"


class _AuthError(Exception):
    """Internal helper — surfaces as ``AgentHarnessError("auth", ...)`` upstream.

    The adapter raises this before any streaming begins; the harness's
    ``_make_stream_fn`` wraps it into the proper ``AgentHarnessError``.
    Within the adapter's ``try/except`` body it propagates so the error
    is surfaced eagerly — OAuth detection MUST NOT swallow into a
    benign ``AssistantErrorEvent``.
    """


def _build_partial(model: Model) -> AssistantMessage:
    """Construct the initial empty ``AssistantMessage`` for ``AssistantStartEvent``."""

    return AssistantMessage(content=[])


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` only when it's a coroutine/awaitable."""

    import inspect

    if inspect.isawaitable(value):
        return await value
    return value


async def stream_anthropic(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Pi parity ``providers/anthropic.ts:428-687`` — Anthropic SSE adapter.

    Sprint 6a body covers the Anthropic Messages API streaming protocol:

    1. Build SDK client (or use ``options.client`` when injected by tests).
    2. Map Aelix context → ``messages.stream`` kwargs (system / messages /
       tools / max_tokens).
    3. ``options.on_payload`` callback (Pi ``onPayload``, harness wires this
       to ``before_provider_payload``).
    4. Open the SDK stream, surface ``ProviderResponse`` via
       ``options.on_response`` (Pi ``onResponse``, harness wires to
       ``after_provider_response``).
    5. Emit ``AssistantStartEvent`` then translate each SDK event into
       the matching Aelix variant (text / thinking / toolcall families).
    6. Terminate with ``AssistantDoneEvent`` (success) or
       ``AssistantErrorEvent`` (failure / abort).

    Raises:
        Nothing — every failure path is captured and surfaced as
        :class:`AssistantErrorEvent`. The single Sprint-6a exception is
        OAuth-token detection which raises :class:`_AuthError`
        synchronously before any streaming begins so the harness can
        translate it into ``AgentHarnessError("auth", ...)``.
    """

    opts = options if options is not None else SimpleStreamOptions()
    output = _build_partial(model)
    # AssistantMessage is frozen; we mutate via dataclasses.replace at
    # the few points the adapter needs to (stop_reason / error_message /
    # usage / content extend). The ``output_content`` mutable list is
    # the staging area — we re-issue a fresh AssistantMessage when we
    # need to surface a new "partial" object on an event.
    output_content: list[Any] = []
    # Index → live block being built (TextContent / ToolCallContent /
    # ThinkingContent equivalent). The Anthropic stream emits
    # content_block_delta events keyed by ``index``.
    block_buffers: dict[int, Any] = {}
    # Track Anthropic ``index`` → Aelix ``content_index``. For Sprint 6a
    # we use Anthropic's index verbatim since it's already 0-based and
    # monotonically increasing per stream.
    # OAuth gate — surface BEFORE creating any SDK client or emitting
    # any event so the harness's ``AgentHarnessError("auth", …)``
    # propagation path stays clean.
    if is_oauth_token(opts.api_key):
        raise _AuthError(
            "OAuth tokens (sk-ant-oat…) are not supported in Sprint 6a — "
            "claude.ai OAuth login lands Sprint 6c (ADR-0020)."
        )

    try:
        # 1) Build / use SDK client.
        client = opts.client or create_async_client(
            api_key=opts.api_key,
            base_url=model.base_url or None,
            default_headers=opts.headers or None,
            timeout_ms=opts.timeout_ms,
            max_retries=opts.max_retries,
        )

        # 2) Map context → SDK params.
        params = build_params(
            model=model,
            system_prompt=context.system_prompt,
            messages=list(context.messages),
            tools=list(context.tools),
            max_tokens=model.max_tokens or 4096,
        )

        # 3) before_provider_payload callback (Pi ``onPayload``).
        if opts.on_payload is not None:
            next_params = await _maybe_await(opts.on_payload(params, model))
            if next_params is not None:
                params = next_params

        # 4) Open the SDK stream. ``messages.stream`` returns a manager
        # whose ``response`` attribute carries the HTTP headers.
        stream_mgr = client.messages.stream(**params)
        async with stream_mgr as sdk_stream:
            # ``sdk_stream.response`` is the underlying httpx response.
            sdk_response = getattr(sdk_stream, "response", None)
            if sdk_response is not None and opts.on_response is not None:
                provider_response = ProviderResponse(
                    status=getattr(sdk_response, "status_code", 200),
                    headers={
                        str(k): str(v)
                        for k, v in dict(
                            getattr(sdk_response, "headers", {})
                        ).items()
                    },
                )
                await _maybe_await(opts.on_response(provider_response, model))

            # 5) Emit AssistantStart, then translate each SDK event.
            partial = AssistantMessage(content=list(output_content))
            yield AssistantStartEvent(partial=partial)

            async for raw_event in sdk_stream:
                async for translated in _translate_event(
                    raw_event, block_buffers, output_content
                ):
                    yield translated

            # 6a) Success: snapshot the SDK's final assistant message.
            final_message = await sdk_stream.get_final_message()
            stop_reason = map_stop_reason(getattr(final_message, "stop_reason", None))
            output = replace(
                output,
                content=list(output_content),
                stop_reason=stop_reason,
            )

        # Abort detection — check after the stream context exits.
        if opts.signal is not None and getattr(opts.signal, "aborted", False):
            raise RuntimeError("Request was aborted")

        # If the adapter detected an error stop_reason mid-stream, route
        # to the error path (Pi parity ``providers/anthropic.ts:660``).
        if output.stop_reason == "error":
            raise RuntimeError(
                output.error_message or "An unknown error occurred"
            )

        # Map the three success reasons onto the Pi ``done`` enum value
        # (``stop`` / ``length`` / ``toolUse``). Other reasons fall
        # through as ``stop`` so the loop terminates cleanly.
        # Sprint 6b W6 (P-57): ``map_stop_reason`` now returns Pi's
        # ``"toolUse"`` spelling verbatim across every adapter.
        if output.stop_reason == "toolUse":
            done_reason: Any = "toolUse"
        elif output.stop_reason == "length":
            done_reason = "length"
        else:
            done_reason = "stop"
        yield AssistantDoneEvent(reason=done_reason, message=output)

    except _AuthError:
        # OAuth detection — re-raise so the harness wrapper converts to
        # AgentHarnessError("auth", …).
        raise
    except Exception as exc:  # noqa: BLE001
        aborted = bool(
            opts.signal is not None and getattr(opts.signal, "aborted", False)
        )
        reason: Any = "aborted" if aborted else "error"
        error_msg = str(exc)
        # Snapshot whatever we managed to assemble so observers can see
        # partial content alongside the failure.
        error_output = replace(
            output,
            content=list(output_content),
            stop_reason=reason,
            error_message=error_msg,
        )
        yield AssistantErrorEvent(
            reason=reason, error=error_output, error_message=error_msg
        )


async def _translate_event(
    raw_event: Any,
    block_buffers: dict[int, Any],
    output_content: list[Any],
) -> AsyncIterator[AssistantMessageEvent]:
    """Translate a single Anthropic SDK event → Aelix events.

    Pi parity: ``providers/anthropic.ts:506-660`` case-by-case mapping.

    The SDK's typed events expose a ``type`` discriminator:

    - ``message_start`` — no Aelix projection (usage tracked separately).
    - ``content_block_start`` (text / thinking / tool_use) → push the
      matching ``*_start`` event and stash a buffer for future deltas.
    - ``content_block_delta`` (text_delta / thinking_delta /
      input_json_delta) → push ``*_delta`` events.
    - ``content_block_stop`` → push ``*_end`` events and lock the
      buffer into ``output_content``.
    - ``message_delta`` / ``message_stop`` — terminal SDK markers, no
      Aelix projection (we read the final state from
      ``stream.get_final_message`` instead).
    """

    ev_type = getattr(raw_event, "type", None)

    if ev_type == "content_block_start":
        block = getattr(raw_event, "content_block", None)
        index = int(getattr(raw_event, "index", 0))
        if block is None:
            return
        bt = getattr(block, "type", None)
        if bt == "text":
            buffer = {"type": "text", "text": ""}
            block_buffers[index] = buffer
            output_content.append(TextContent(text=""))
            partial = AssistantMessage(content=list(output_content))
            yield TextStartEvent(content_index=index, partial=partial)
        elif bt == "thinking":
            # ThinkingContent isn't yet part of the Aelix `ContentBlock`
            # union (Sprint 6b extension); for Sprint 6a we surface the
            # event family but do NOT append a content block — observers
            # see ``thinking_*`` events for telemetry.
            buffer = {"type": "thinking", "thinking": ""}
            block_buffers[index] = buffer
            partial = AssistantMessage(content=list(output_content))
            from aelix_ai.streaming import ThinkingStartEvent

            yield ThinkingStartEvent(content_index=index, partial=partial)
        elif bt == "tool_use":
            buffer = {
                "type": "tool_use",
                "id": getattr(block, "id", ""),
                "name": getattr(block, "name", ""),
                "input_json": "",
            }
            block_buffers[index] = buffer
            output_content.append(
                ToolCallContent(
                    tool_call_id=buffer["id"],
                    tool_name=buffer["name"],
                    input={},
                )
            )
            partial = AssistantMessage(content=list(output_content))
            yield ToolCallStartEvent(content_index=index, partial=partial)

    elif ev_type == "content_block_delta":
        delta = getattr(raw_event, "delta", None)
        index = int(getattr(raw_event, "index", 0))
        if delta is None:
            return
        dt = getattr(delta, "type", None)
        buffer = block_buffers.get(index)
        if buffer is None:
            return
        if dt == "text_delta":
            text_piece = getattr(delta, "text", "")
            buffer["text"] = buffer.get("text", "") + text_piece
            # Mutate the matching content block in output_content.
            if index < len(output_content) and isinstance(
                output_content[index], TextContent
            ):
                output_content[index] = TextContent(text=buffer["text"])
            partial = AssistantMessage(content=list(output_content))
            yield TextDeltaEvent(
                delta=text_piece, content_index=index, partial=partial
            )
        elif dt == "thinking_delta":
            thinking_piece = getattr(delta, "thinking", "")
            buffer["thinking"] = buffer.get("thinking", "") + thinking_piece
            partial = AssistantMessage(content=list(output_content))
            from aelix_ai.streaming import ThinkingDeltaEvent

            yield ThinkingDeltaEvent(
                delta=thinking_piece, content_index=index, partial=partial
            )
        elif dt == "input_json_delta":
            json_piece = getattr(delta, "partial_json", "")
            buffer["input_json"] = buffer.get("input_json", "") + json_piece
            partial = AssistantMessage(content=list(output_content))
            yield ToolCallDeltaEvent(
                delta=json_piece,
                content_index=index,
                partial=partial,
                tool_call_id=buffer.get("id", ""),
                tool_name=buffer.get("name", ""),
            )

    elif ev_type == "content_block_stop":
        index = int(getattr(raw_event, "index", 0))
        buffer = block_buffers.get(index)
        if buffer is None:
            return
        bt = buffer.get("type")
        partial = AssistantMessage(content=list(output_content))
        if bt == "text":
            yield TextEndEvent(
                content_index=index, content=buffer.get("text", ""), partial=partial
            )
        elif bt == "thinking":
            from aelix_ai.streaming import ThinkingEndEvent

            yield ThinkingEndEvent(
                content_index=index,
                content=buffer.get("thinking", ""),
                partial=partial,
            )
        elif bt == "tool_use":
            # Parse the accumulated input_json into a dict.
            import json as _json

            from aelix_ai.providers._anthropic_transforms import (
                _ANTHROPIC_STOP_REASON_MAP,  # noqa: F401  (keep import side-effect)
            )

            try:
                parsed_input = _json.loads(buffer.get("input_json") or "{}")
            except (_json.JSONDecodeError, ValueError):
                parsed_input = {}
            finalized = ToolCallContent(
                tool_call_id=buffer.get("id", ""),
                tool_name=buffer.get("name", ""),
                input=parsed_input,
            )
            # Overwrite the placeholder ToolCallContent created at start.
            if index < len(output_content) and isinstance(
                output_content[index], ToolCallContent
            ):
                output_content[index] = finalized
            partial = AssistantMessage(content=list(output_content))
            yield ToolCallEndEvent(
                content_index=index, tool_call=finalized, partial=partial
            )
        block_buffers.pop(index, None)


# === Provider registration ===


class _AnthropicProvider:
    """Concrete :class:`Provider` for ``anthropic-messages``."""

    api: str = ANTHROPIC_API
    source_id: str | None = None

    def stream(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        return stream_anthropic(model, context, options)

    # Sprint 6a: ``stream_simple`` == ``stream`` (one shape).
    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        return stream_anthropic(model, context, options)


ANTHROPIC_PROVIDER: Provider = _AnthropicProvider()


def register_all() -> None:
    """Register the Anthropic adapter for ``model.api == "anthropic-messages"``.

    Pi parity: ``providers/anthropic.ts`` ``registerAnthropicProvider`` /
    ``registerAll`` family. Re-registering is idempotent — the registry
    replaces the entry by ``api`` key.
    """

    register_provider_object(ANTHROPIC_PROVIDER, source_id=BUILTIN_SOURCE_ID)


__all__ = [
    "ANTHROPIC_API",
    "ANTHROPIC_PROVIDER",
    "BUILTIN_SOURCE_ID",
    "register_all",
    "stream_anthropic",
]
