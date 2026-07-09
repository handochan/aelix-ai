"""Anthropic provider adapter — Sprint 6a (ADR-0045 §B), amended Sprint 6c.

Pi parity: ``providers/anthropic.ts:428-687`` (SHA 734e08e). Ports the
Pi adapter body using the official ``anthropic`` Python SDK
(``>=0.40,<1.0``).

Sprint 6c amendment (P-91, ADR-0052):

- OAuth tokens (``sk-ant-oat…``) are NO LONGER eager-rejected.
- :class:`_AuthError` is retained as the vehicle for SDK 401/403
  translation — the harness ``_make_stream_fn`` still catches
  :class:`_AuthError` and converts to ``AgentHarnessError("auth", …)``.
  The trigger condition shifted from "bare token detection" to
  "SDK ``APIStatusError`` with status 401 or 403".

Sprint 6c W6 amendment (P-94, ADR-0052 §"Bearer header injection"):

- The official Anthropic Python SDK (``>=0.40,<1.0``) does NOT
  auto-detect OAuth tokens — it puts whatever was passed as ``api_key``
  into the ``x-api-key`` header. Anthropic's OAuth endpoint rejects
  bearer tokens delivered via ``x-api-key`` with 401. To make OAuth
  actually work in production, this adapter, when ``is_oauth_token``
  is true, builds the SDK client with empty ``api_key`` plus a
  manually injected ``Authorization: Bearer <token>`` default header
  (and the ``anthropic-beta: oauth-2025-04-20`` header that Pi's
  ``providers/anthropic.ts`` also injects in the OAuth branch).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from aelix_ai.api_registry import register_provider_object
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
)
from aelix_ai.providers._anthropic_client import create_async_client
from aelix_ai.providers._anthropic_compat import get_compat
from aelix_ai.providers._anthropic_transforms import (
    INTERLEAVED_THINKING_BETA,
    build_params,
    is_oauth_token,
    map_stop_reason_with_details,
    resolve_anthropic_thinking,
)
from aelix_ai.providers._base import Provider
from aelix_ai.providers._github_copilot_headers import (
    build_copilot_dynamic_headers,
    has_copilot_vision_input,
)
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


def _attr_or_key(obj: Any, name: str) -> Any:
    """Read ``name`` as an attribute (SDK object) or a dict key (test mock)."""

    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _anthropic_usage_to_dict(usage: Any) -> dict[str, Any] | None:
    """Map an Anthropic SDK ``Message.usage`` payload → the Aelix usage dict.

    Pi parity: ``api/anthropic-messages.ts:549-559`` reads these exact fields
    off ``event.message.usage`` (``message_start``) and re-reads the output
    subset off ``event.usage`` (``message_delta``). The Anthropic Python SDK
    accumulates both into ``stream.get_final_message().usage``, so the adapter
    extracts once from the final message.

    Field mapping (pi → Aelix usage dict):

    * ``input_tokens`` → ``input``
    * ``output_tokens`` → ``output``
    * ``cache_read_input_tokens`` → ``cache_read``
    * ``cache_creation_input_tokens`` → ``cache_write`` (all cache writes)
    * ``cache_creation.ephemeral_1h_input_tokens`` → ``cache_write_1h`` (the
      1h-TTL subset — :func:`aelix_ai.models.calculate_cost` prices it at 2×
      the model's base input rate, pi #5738)

    Keys mirror :func:`openai_completions._usage_to_dict` (``input`` / ``output``
    plus the ``*_tokens`` spellings the context meter / ``/cost`` read) and add
    the Anthropic-only cache-write buckets. Returns ``None`` for an empty or
    absent payload so the adapter leaves ``AssistantMessage.usage`` untouched.
    """

    if usage is None:
        return None
    input_tokens = int(_attr_or_key(usage, "input_tokens") or 0)
    output_tokens = int(_attr_or_key(usage, "output_tokens") or 0)
    cache_read = int(_attr_or_key(usage, "cache_read_input_tokens") or 0)
    cache_write = int(_attr_or_key(usage, "cache_creation_input_tokens") or 0)
    cache_creation = _attr_or_key(usage, "cache_creation")
    cache_write_1h = (
        int(_attr_or_key(cache_creation, "ephemeral_1h_input_tokens") or 0)
        if cache_creation is not None
        else 0
    )
    if not (input_tokens or output_tokens or cache_read or cache_write):
        return None
    # Anthropic doesn't return total_tokens — compute from the components
    # (pi anthropic-messages.ts:557-558 sums input+output+cacheRead+cacheWrite).
    total = input_tokens + output_tokens + cache_read + cache_write
    return {
        "input": input_tokens,
        "output": output_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "cache_write_1h": cache_write_1h,
    }


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` only when it's a coroutine/awaitable."""

    import inspect

    if inspect.isawaitable(value):
        return await value
    return value


def _with_interleaved_beta(
    headers: dict[str, str] | None, needs: bool
) -> dict[str, str] | None:
    """Append the interleaved-thinking beta to ``anthropic-beta`` when a
    budget-based reasoning request needs it (ADR-0135; pi anthropic.ts:784-790).

    Returns ``headers`` unchanged when no beta is needed so the caller keeps
    the existing ``opts.headers or None`` default-header semantics.
    """

    if not needs:
        return headers
    merged = dict(headers or {})
    existing = [b for b in (merged.get("anthropic-beta") or "").split(",") if b]
    if INTERLEAVED_THINKING_BETA not in existing:
        existing.append(INTERLEAVED_THINKING_BETA)
    merged["anthropic-beta"] = ",".join(existing)
    return merged


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
    # Sprint 6c W6 amendment (P-94): OAuth tokens MUST be sent via
    # ``Authorization: Bearer <token>`` — the SDK does NOT auto-detect
    # them and putting the bearer token in ``x-api-key`` produces 401.
    # We build OAuth-flavored client params here so the request reaches
    # Anthropic with the correct auth header. ``anthropic-beta`` mirrors
    # Pi (``providers/anthropic.ts``) OAuth branch.
    oauth_mode = is_oauth_token(opts.api_key)
    # ADR-0135 (P0 #1): resolve the per-turn thinking level into the Anthropic
    # request thinking param (adaptive effort for Opus 4.6+/Sonnet 4.6,
    # budget_tokens for older reasoning models) and decide whether the
    # interleaved-thinking beta header is needed. Computed before client
    # creation so the header can be attached to the SDK client default headers.
    # P0 #6 (compaction fidelity): pi ``base.maxTokens = options.maxTokens ??
    # model.maxTokens``. When the caller (e.g. the compaction summarizer)
    # supplies ``options.max_tokens``, it becomes the base output cap that the
    # thinking-budget math carves from and the request ``max_tokens`` value —
    # replacing the ``model.max_tokens or 4096`` default.
    default_max_tokens = (
        opts.max_tokens
        if opts.max_tokens is not None and opts.max_tokens > 0
        else (model.max_tokens or 4096)
    )
    thinking_extra, thinking_max_tokens, needs_interleaved = (
        resolve_anthropic_thinking(model, opts.reasoning, default_max_tokens)
    )
    if oauth_mode:
        import logging as _logging

        _logging.getLogger(__name__).debug(
            "Anthropic adapter received OAuth token (sk-ant-oat…); "
            "routing via Authorization: Bearer header (P-94).",
        )

    try:
        # 1) Build / use SDK client.
        if opts.client is not None:
            client = opts.client
        elif model.provider == "github-copilot":
            # Pi parity ``providers/anthropic.ts`` github-copilot branch:
            # ``new Anthropic({ apiKey: null, authToken: apiKey, ... })``. The
            # GitHub Copilot proxy authenticates via ``Authorization: Bearer
            # <token>`` — NOT ``x-api-key`` (the SDK default whenever ``api_key``
            # is set) — so a Copilot Claude model (``api="anthropic-messages"``)
            # that fell through to the plain-api-key ``else`` branch below was
            # rejected by the proxy with 401. ``is_oauth_token`` only matches
            # Anthropic ``sk-ant-oat…`` tokens, so this branch MUST key off
            # ``model.provider`` (mirroring the OpenAI adapters), not the token
            # shape. We reuse the OAuth branch's ``api_key="" + manual
            # Authorization`` technique so no SDK ``authToken`` param is needed.
            # Pi sets ``isOAuthToken:false`` here → NO ``oauth-2025-04-20`` /
            # claude-code identity betas, but the interleaved-thinking beta
            # still applies. Header order mirrors
            # ``openai_responses._build_client_headers``: static
            # ``model.headers`` (Copilot-Integration-Id / Editor-Version / …) →
            # dynamic copilot headers (X-Initiator / Openai-Intent /
            # Copilot-Vision-Request) → ``options.headers`` → forced Bearer.
            copilot_headers: dict[str, str] = dict(
                getattr(model, "headers", None) or {}
            )
            copilot_headers.update(
                build_copilot_dynamic_headers(
                    context.messages,
                    has_copilot_vision_input(context.messages),
                )
            )
            if opts.headers:
                copilot_headers.update(opts.headers)
            copilot_headers["Authorization"] = f"Bearer {opts.api_key}"
            copilot_headers = dict(
                _with_interleaved_beta(copilot_headers, needs_interleaved) or {}
            )
            client = create_async_client(
                # Blank api_key — auth lives in the Authorization header.
                api_key="",
                base_url=model.base_url or None,
                default_headers=copilot_headers,
                timeout_ms=opts.timeout_ms,
                max_retries=opts.max_retries,
            )
        elif oauth_mode:
            oauth_headers: dict[str, str] = dict(opts.headers or {})
            oauth_headers["Authorization"] = f"Bearer {opts.api_key}"
            oauth_headers.setdefault("anthropic-beta", "oauth-2025-04-20")
            oauth_headers = dict(
                _with_interleaved_beta(oauth_headers, needs_interleaved) or {}
            )
            client = create_async_client(
                # Blank api_key — auth lives in the Authorization header.
                api_key="",
                base_url=model.base_url or None,
                default_headers=oauth_headers,
                timeout_ms=opts.timeout_ms,
                max_retries=opts.max_retries,
            )
        else:
            default_headers = _with_interleaved_beta(
                opts.headers, needs_interleaved
            )
            # ADR-0190 (B-lite): inject the session-affinity header on the
            # API-key branch when a ``session_id`` is present and the model's
            # compat opts in (fireworks / cloudflare-ai-gateway anthropic).
            # Mirrors pi anthropic.ts:862-863. ``x-session-affinity`` is a
            # plain header — NOT an ``anthropic-beta`` value — so it never
            # touches the delicate beta CSV. Deliberately omitted on the OAuth
            # branch (pi omits it there too — ADR-0190 divergence #1).
            if (
                opts.session_id
                and get_compat(model).send_session_affinity_headers
            ):
                default_headers = dict(default_headers or {})
                default_headers["x-session-affinity"] = opts.session_id
            client = create_async_client(
                api_key=opts.api_key,
                base_url=model.base_url or None,
                default_headers=default_headers or None,
                timeout_ms=opts.timeout_ms,
                max_retries=opts.max_retries,
            )

        # 2) Map context → SDK params. ``thinking_extra`` injects the
        # resolved ADR-0135 thinking param (+ ``output_config`` for adaptive
        # models); ``thinking_max_tokens`` is the budget-adjusted cap.
        # pi #5251: temperature is gated on "thinking off" — derive that from
        # the resolved thinking param (enabled/adaptive ⇒ thinking is active).
        thinking_type = (thinking_extra or {}).get("thinking", {}).get("type")
        thinking_enabled = thinking_type in ("enabled", "adaptive")
        params = build_params(
            model=model,
            system_prompt=context.system_prompt,
            messages=list(context.messages),
            tools=list(context.tools),
            max_tokens=thinking_max_tokens,
            extra=thinking_extra or None,
            temperature=opts.temperature,
            thinking_enabled=thinking_enabled,
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
            # pi #5666: a ``refusal`` stop reason maps to ``"error"`` AND carries
            # ``stop_details.explanation`` — preserve that text into
            # ``error_message`` so the surfaced error says *why* the model
            # refused (it is re-raised below as the error path's message).
            final_message = await sdk_stream.get_final_message()
            stop_reason, refusal_message = map_stop_reason_with_details(
                getattr(final_message, "stop_reason", None),
                getattr(final_message, "stop_details", None),
            )
            # pi #5738 (anthropic-messages.ts:549-559): snapshot the SDK's
            # accumulated token usage onto ``AssistantMessage.usage`` so the
            # context meter / ``/cost`` see real numbers — including the
            # ``cache_write_1h`` slice (``cache_creation.ephemeral_1h_input_tokens``)
            # that :func:`aelix_ai.models.calculate_cost` prices at 2× input.
            usage_dict = _anthropic_usage_to_dict(
                getattr(final_message, "usage", None)
            )
            # ADR-0190: stamp the provenance trio so
            # ``_transform_messages._is_same_model`` can recognise a prior
            # anthropic turn as same-model and preserve its signed thinking
            # blocks on replay. Without this the shared transform always
            # treats prior thinking as cross-model → downgrades to text →
            # signatures never travel back. Mirrors openai_completions.py:1300-1308.
            output = replace(
                output,
                content=list(output_content),
                stop_reason=stop_reason,
                error_message=refusal_message or output.error_message,
                usage=usage_dict if usage_dict is not None else output.usage,
                api=model.api,
                provider=model.provider,
                model=model.id,
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
        # SDK 401/403 translation (Sprint 6c amendment) — re-raise so the
        # harness wrapper converts to AgentHarnessError("auth", …).
        raise
    except Exception as exc:  # noqa: BLE001
        # Sprint 6c: detect SDK 401/403 and surface as _AuthError so the
        # harness translates to AgentHarnessError("auth", …). Catches
        # both bare api_key 401s and OAuth tokens that failed to refresh
        # upstream (auth.json bad, network error, etc.).
        status = getattr(exc, "status_code", None) or getattr(
            getattr(exc, "response", None), "status_code", None
        )
        if status in (401, 403):
            raise _AuthError(str(exc) or f"Anthropic SDK returned {status}") from exc
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
            # ADR-0190: stamp provenance on the error path too so a partial
            # turn surfaced to observers still carries the same-model markers
            # (mirrors the success build). openai_completions.py:1300-1308.
            api=model.api,
            provider=model.provider,
            model=model.id,
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
            # ADR-0190: append a ThinkingContent at start (alongside the
            # buffer + ThinkingStartEvent) so EVERY block type adds exactly
            # one ``output_content`` entry. This keeps the Anthropic
            # ``index`` aligned with the list position so the text/tool
            # write-back guards (``index < len(output_content)``) fire for
            # any block *after* a thinking block — otherwise a following
            # ``tool_use`` persists with empty ``input={}`` (the latent
            # off-by-one data-loss bug). Mirrors anthropic.ts:527-535.
            buffer = {"type": "thinking", "thinking": ""}
            block_buffers[index] = buffer
            output_content.append(
                ThinkingContent(
                    thinking="", thinking_signature="", redacted=False
                )
            )
            partial = AssistantMessage(content=list(output_content))
            from aelix_ai.streaming import ThinkingStartEvent

            yield ThinkingStartEvent(content_index=index, partial=partial)
        elif bt == "redacted_thinking":
            # ADR-0190: redacted thinking arrives as a single opaque block
            # (no deltas). Capture its ``data`` payload into
            # ``thinking_signature`` (with ``redacted=True``) so replay can
            # echo it back as ``{type:redacted_thinking, data}``. Appending
            # here is mandatory too — the append-at-start invariant must hold
            # for BOTH thinking flavors or the off-by-one persists on
            # redacted turns. Mirrors anthropic.ts:536-545.
            redacted_data = getattr(block, "data", "") or ""
            buffer = {
                "type": "thinking",
                "thinking": "[Reasoning redacted]",
                "thinking_signature": redacted_data,
                "redacted": True,
            }
            block_buffers[index] = buffer
            output_content.append(
                ThinkingContent(
                    thinking="[Reasoning redacted]",
                    thinking_signature=redacted_data,
                    redacted=True,
                )
            )
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
            # ADR-0190: rebuild the ThinkingContent at ``output_content[index]``
            # (same positional guard as ``text_delta``) so the accumulated
            # thinking text survives into ``AssistantDoneEvent.message`` —
            # preserving any signature/redacted already on the buffer.
            # Mirrors anthropic.ts:573-584.
            if index < len(output_content) and isinstance(
                output_content[index], ThinkingContent
            ):
                output_content[index] = ThinkingContent(
                    thinking=buffer["thinking"],
                    thinking_signature=buffer.get("thinking_signature", ""),
                    redacted=buffer.get("redacted", False),
                )
            partial = AssistantMessage(content=list(output_content))
            from aelix_ai.streaming import ThinkingDeltaEvent

            yield ThinkingDeltaEvent(
                delta=thinking_piece, content_index=index, partial=partial
            )
        elif dt == "signature_delta":
            # ADR-0190: accumulate the thinking signature and rebuild the
            # ThinkingContent preserving thinking+redacted. Pi emits NO public
            # event for signature deltas (anthropic.ts:598-604), so this arm
            # yields nothing — it only mutates the captured block.
            sig_piece = getattr(delta, "signature", "")
            buffer["thinking_signature"] = (
                buffer.get("thinking_signature", "") + sig_piece
            )
            if index < len(output_content) and isinstance(
                output_content[index], ThinkingContent
            ):
                output_content[index] = ThinkingContent(
                    thinking=buffer.get("thinking", ""),
                    thinking_signature=buffer["thinking_signature"],
                    redacted=buffer.get("redacted", False),
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
            # ADR-0190: lock the final ThinkingContent (thinking + signature
            # + redacted) into ``output_content[index]`` before emitting the
            # end event, so the persisted block matches the buffer even when
            # the signature arrived after the last thinking delta. Mirrors
            # anthropic.ts:618-624.
            if index < len(output_content) and isinstance(
                output_content[index], ThinkingContent
            ):
                output_content[index] = ThinkingContent(
                    thinking=buffer.get("thinking", ""),
                    thinking_signature=buffer.get("thinking_signature", ""),
                    redacted=buffer.get("redacted", False),
                )
            from aelix_ai.streaming import ThinkingEndEvent

            yield ThinkingEndEvent(
                content_index=index,
                content=buffer.get("thinking", ""),
                partial=AssistantMessage(content=list(output_content)),
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
