"""OpenAI **Responses**-API thin adapter — pi parity (#15).

Pi parity: ``packages/ai/src/api/openai-responses.ts`` (~297 LOC) at SHA
``927e98068cda276bf9188f4774fb927c89823388``.

This is the thin per-provider adapter for the OpenAI **Responses** API
family. The heavy lifting — message/tool conversion and the streaming
state machine — lives in the shared engine
(:mod:`aelix_ai.providers._openai_responses_shared`); this module owns
client construction, header/auth quirks, param assembly, and the
``start`` / ``done`` / ``error`` event envelope around the engine.

REGISTERED (Workflow B, #15): the adapter is wired on at startup via
:func:`aelix_coding_agent.cli.runtime_bootstrap.register_providers`, which
calls :func:`register_all`. This surfaces the previously-hidden
``openai-responses`` models (OpenAI / GitHub Copilot / cloudflare-ai-gateway /
opencode) in the ``/model`` picker. Cloudflare's templated base_url
(``{CLOUDFLARE_ACCOUNT_ID}`` / ``{CLOUDFLARE_GATEWAY_ID}``) is expanded from
the environment at client construction, and its models stay hidden until those
env vars are set (see
:func:`aelix_coding_agent.core.runnable_models.is_runnable`).

Divergences from pi (intentional, v1 — #15 decisions):

- **Service-tier cost multipliers are DROPPED.** pi threads
  ``serviceTier`` / ``resolveServiceTier`` / ``applyServiceTierPricing``
  through the engine to scale cost by ``flex`` (0.5×) / ``priority``
  (2×/2.5×). Aelix v1 keeps the token-dict usage convention (see
  :mod:`aelix_ai.providers._openai_responses_shared`) and does NOT model
  service tiers. The ``service_tier`` request param is still forwarded
  verbatim when set (so the provider applies its own server-side tiering);
  only the client-side *cost multiplier* is dropped.
- **Env-key + auth-header fallback.** pi's ``getClientApiKey`` resolves
  ``options.apiKey`` or an ``authorization`` / ``cf-aig-authorization``
  header (``"unused"``), then throws. Aelix additionally falls back to the
  env API key (``get_env_api_key``) before the header check — matching the
  established :mod:`aelix_ai.providers.openai_completions` precedent so a
  bare ``OPENAI_API_KEY`` / ``OPENCODE_API_KEY`` / ``CLOUDFLARE_API_KEY``
  env var resolves auth for these in-scope providers.
- **``thinking_signature`` semantic overload.** The Responses adapter
  reuses :attr:`ThinkingContent.thinking_signature` to hold the FULL
  ``ResponseReasoningItem`` JSON (incl. ``encrypted_content``) rather than
  a wire-field name — see the shared engine. This adapter only stamps
  :attr:`AssistantMessage.response_id` from the stream's ``response.id``.
- **Abort is best-effort / post-hoc.** pi forwards ``options.signal`` into
  the per-request SDK options (openai-responses.ts:125-130) to HTTP-cancel an
  in-flight generation. The Python ``openai`` SDK exposes no AbortSignal
  binding for ``responses.create``, so — matching the
  :mod:`aelix_ai.providers.openai_completions` precedent (P-60) — the signal
  is observed after the stream drains rather than forwarded to the wire. An
  abort therefore stops consumption at the next event boundary instead of
  tearing down the live HTTP request.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from aelix_ai.api_registry import register_provider_object
from aelix_ai.messages import AssistantMessage
from aelix_ai.models import clamp_thinking_level
from aelix_ai.providers._env_api_keys import get_env_api_key
from aelix_ai.providers._github_copilot_headers import (
    build_copilot_dynamic_headers,
    has_copilot_vision_input,
)
from aelix_ai.providers._openai_client import create_async_client
from aelix_ai.providers._openai_prompt_cache import clamp_openai_prompt_cache_key
from aelix_ai.providers._openai_responses_compat import (
    OpenAIResponsesCompat,
    get_responses_compat,
)
from aelix_ai.providers._openai_responses_shared import (
    OPENAI_TOOL_CALL_PROVIDERS,
    OpenAIResponsesStreamOptions,
    ResponsesStreamState,
    convert_responses_messages,
    convert_responses_tools,
    process_responses_stream,
)
from aelix_ai.providers.openai_completions import BUILTIN_SOURCE_ID
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    ProviderResponse,
    SimpleStreamOptions,
)

if TYPE_CHECKING:
    from aelix_ai.providers._base import Provider


# Pi parity: ``KnownApi`` value — the Responses-family api id.
OPENAI_RESPONSES_API: str = "openai-responses"

# Pi parity: ``model.thinkingLevelMap?.off ?? "none"`` fallback.
_OFF_EFFORT_FALLBACK: str = "none"

# OpenRouter/provider extension params the OpenAI **Python** SDK rejects as
# top-level kwargs. Verified against the installed ``openai`` SDK (1.66+):
# ``responses.create`` accepts ``store`` / ``reasoning`` / ``include`` /
# ``prompt_cache_key`` / ``max_output_tokens`` / ``service_tier`` natively,
# but NOT ``prompt_cache_retention`` — that one must ride in ``extra_body``
# (the SDK merges it verbatim into the JSON body). Mirrors the completions
# adapter's ``_relocate_extra_body_params``.
_EXTRA_BODY_PARAM_KEYS: frozenset[str] = frozenset({"prompt_cache_retention"})


# OpenAI Responses-specific options (pi ``OpenAIResponsesOptions``,
# openai-responses.ts:70-74).
@dataclass(frozen=True)
class OpenAIResponsesOptions(SimpleStreamOptions):
    """Extends :class:`SimpleStreamOptions` with Responses-only extras.

    ``reasoning_summary`` defaults to ``None`` (NOT ``"auto"``) so the
    reasoning-block gate stays pi-faithful: pi keys the first branch on
    ``reasoningEffort || reasoningSummary``, and a non-``None`` default
    would force every reasoning model down the encrypted-reasoning path
    even with no effort requested. The ``"auto"`` default the #15 decision
    calls for is applied at the *use site* (``reasoning_summary or "auto"``)
    in :func:`build_params`, exactly as pi does.
    """

    reasoning_effort: (
        Literal["minimal", "low", "medium", "high", "xhigh"] | None
    ) = None
    reasoning_summary: Literal["auto", "detailed", "concise"] | None = None
    service_tier: str | None = None


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` only when it's awaitable (callbacks may be sync/async)."""

    if inspect.isawaitable(value):
        return await value
    return value


def _coerce_options(
    options: OpenAIResponsesOptions | SimpleStreamOptions | None,
) -> OpenAIResponsesOptions:
    """Widen a generic :class:`SimpleStreamOptions` into the Responses shape."""

    if options is None:
        return OpenAIResponsesOptions()
    if isinstance(options, OpenAIResponsesOptions):
        return options
    return OpenAIResponsesOptions(
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
        temperature=options.temperature,
        session_id=options.session_id,
        on_payload=options.on_payload,
        on_response=options.on_response,
        client=options.client,
        max_tokens=options.max_tokens,
    )


# === Auth + cache helpers ===


def _has_header(headers: dict[str, str] | None, name: str) -> bool:
    """Pi parity: ``hasHeader`` (openai-responses.ts:28-35).

    Case-insensitive presence check for a header with a non-empty value.
    """

    if not headers:
        return False
    expected = name.lower()
    for key, value in headers.items():
        if key.lower() == expected and value is not None and str(value).strip():
            return True
    return False


def _resolve_client_api_key(
    provider: str, api_key: str | None, headers: dict[str, str] | None
) -> str:
    """Pi parity: ``getClientApiKey`` (openai-responses.ts:37-41) + env fallback.

    Resolution order: explicit ``api_key`` → env API key (aelix divergence,
    matching the completions adapter) → ``"unused"`` when an
    ``authorization`` / ``cf-aig-authorization`` header is present
    (cloudflare-ai-gateway path) → raise.
    """

    if api_key:
        return api_key
    env_key = get_env_api_key(provider)
    if env_key:
        return env_key
    if _has_header(headers, "authorization") or _has_header(
        headers, "cf-aig-authorization"
    ):
        return "unused"
    raise RuntimeError(f"No API key for provider: {provider}")


def _resolve_cache_retention(cache_retention: str | None) -> str:
    """Pi parity: ``resolveCacheRetention`` (openai-responses.ts:47-56).

    Aelix does not read ``PI_CACHE_RETENTION`` (an ops-coupled env toggle);
    it honors the caller value, defaulting to ``"short"`` — same as the
    completions adapter.
    """

    return cache_retention or "short"


def _get_prompt_cache_retention(
    compat: OpenAIResponsesCompat, cache_retention: str
) -> str | None:
    """Pi parity: ``getPromptCacheRetention`` (openai-responses.ts:66-71)."""

    if cache_retention == "long" and compat.supports_long_cache_retention:
        return "24h"
    return None


def _native_effort(model: Model, effort: str) -> str | int:
    """Pi parity: ``model.thinkingLevelMap?.[effort] ?? effort``.

    Faithful to JS ``??``: only a missing / explicitly-``None`` map value
    falls back; any present value is used verbatim.
    """

    value = (model.thinking_level_map or {}).get(effort)
    return effort if value is None else value


# === Param assembly ===


def build_params(
    model: Model,
    context: Context,
    options: OpenAIResponsesOptions | SimpleStreamOptions | None,
) -> dict[str, Any]:
    """Assemble ``client.responses.create`` kwargs.

    Pi parity: ``buildParams`` (openai-responses.ts:201-273). ``store`` is
    **always** ``False``. ``prompt_cache_key`` is set (clamped) only when
    retention isn't ``"none"``; ``prompt_cache_retention`` only when
    retention is ``"long"`` and the compat allows it. ``max_output_tokens``
    rides only when truthy. The reasoning block mirrors pi exactly,
    including the **github-copilot exclusion** from the reasoning-off
    branch.
    """

    opts = _coerce_options(options)
    messages = convert_responses_messages(model, context, OPENAI_TOOL_CALL_PROVIDERS)
    cache_retention = _resolve_cache_retention(opts.cache_retention)
    compat = get_responses_compat(model)

    params: dict[str, Any] = {
        "model": model.id,
        "input": messages,
        "stream": True,
        # Pi parity: ``store: false`` ALWAYS (openai-responses.ts:213).
        "store": False,
    }

    if cache_retention != "none":
        cache_key = clamp_openai_prompt_cache_key(opts.session_id)
        if cache_key is not None:
            params["prompt_cache_key"] = cache_key

    retention = _get_prompt_cache_retention(compat, cache_retention)
    if retention is not None:
        params["prompt_cache_retention"] = retention

    # Pi parity: ``if (options?.maxTokens) params.max_output_tokens = …`` —
    # truthy-gated, so ``0`` / ``None`` omit the cap entirely.
    if opts.max_tokens:
        params["max_output_tokens"] = opts.max_tokens

    if opts.temperature is not None:
        params["temperature"] = opts.temperature

    if opts.service_tier is not None:
        params["service_tier"] = opts.service_tier

    tools = list(context.tools or [])
    if tools:
        params["tools"] = convert_responses_tools(tools)

    # Reasoning block — pi openai-responses.ts:250-265.
    if getattr(model, "reasoning", False):
        if opts.reasoning_effort or opts.reasoning_summary:
            effort: str | int = (
                _native_effort(model, opts.reasoning_effort)
                if opts.reasoning_effort
                else "medium"
            )
            params["reasoning"] = {
                "effort": effort,
                "summary": opts.reasoning_summary or "auto",
            }
            params["include"] = ["reasoning.encrypted_content"]
        elif model.provider != "github-copilot":
            # github-copilot is EXCLUDED from the reasoning-off branch.
            thinking_map = model.thinking_level_map or {}
            off_is_explicit_null = (
                "off" in thinking_map and thinking_map["off"] is None
            )
            if not off_is_explicit_null:
                off_value = thinking_map.get("off")
                params["reasoning"] = {
                    "effort": _OFF_EFFORT_FALLBACK
                    if off_value is None
                    else off_value
                }

    return params


def _relocate_extra_body_params(params: dict[str, Any]) -> dict[str, Any]:
    """Move non-OpenAI extension params into ``extra_body`` (mutates+returns).

    Called at the SDK boundary so :func:`build_params` keeps its Pi-parity
    flat shape (and the build_params unit tests stay valid) while the real
    ``client.responses.create`` only ever sees kwargs it accepts.
    """

    extra: dict[str, Any] = dict(params.get("extra_body") or {})
    for key in _EXTRA_BODY_PARAM_KEYS:
        if key in params:
            extra[key] = params.pop(key)
    if extra:
        params["extra_body"] = extra
    return params


# === Client + stream ===


def _build_client_headers(
    model: Model,
    context: Context,
    compat: OpenAIResponsesCompat,
    options_headers: dict[str, str] | None,
    cache_session_id: str | None,
) -> dict[str, str]:
    """Pi parity: ``createClient`` header assembly (openai-responses.ts:175-200).

    ``{...model.headers}`` → copilot dynamic headers (provider-gated) →
    session-affinity trio (``session_id`` only when compat opts in;
    ``x-client-request-id`` always) → ``options.headers`` last (override).
    """

    headers: dict[str, str] = dict(getattr(model, "headers", None) or {})

    if model.provider == "github-copilot":
        has_images = has_copilot_vision_input(context.messages)
        headers.update(
            build_copilot_dynamic_headers(context.messages, has_images)
        )

    if cache_session_id:
        if compat.send_session_id_header:
            headers["session_id"] = cache_session_id
        headers["x-client-request-id"] = cache_session_id

    if options_headers:
        headers.update(options_headers)

    return headers


async def _open_responses_stream(
    client: Any, params: dict[str, Any], request_options: dict[str, Any]
) -> tuple[AsyncIterator[Any], Any]:
    """Open the Responses SDK stream + return ``(iterator, raw_response)``.

    Pi parity: the real ``openai>=1.66`` SDK exposes
    ``client.responses.with_raw_response.create(**params, **request_options)``
    (NOT ``chat.completions``) which returns a raw wrapper whose
    ``.parse()`` yields the ``AsyncStream`` and whose ``.http_response``
    gives the underlying httpx ``Response``.
    """

    create_fn = client.responses.with_raw_response.create
    raw = create_fn(**params, **request_options)
    if inspect.isawaitable(raw):
        raw = await raw

    iterator = raw.parse()
    if inspect.isawaitable(iterator):
        iterator = await iterator
    return iterator, raw


async def stream_openai_responses(
    model: Model,
    context: Context,
    options: OpenAIResponsesOptions | SimpleStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Pi parity: ``stream`` (openai-responses.ts:80-172).

    Drives the shared :func:`process_responses_stream` engine and wraps it
    with the ``start`` / ``done`` envelope. Any exception emits
    :class:`AssistantErrorEvent` with no :class:`AssistantDoneEvent`.
    """

    opts = _coerce_options(options)
    state = ResponsesStreamState()

    def _snapshot() -> AssistantMessage:
        return AssistantMessage(
            content=list(state.content),
            response_id=state.response_id,
            api=model.api,
            provider=model.provider,
            model=model.id,
        )

    try:
        compat = get_responses_compat(model)
        cache_retention = _resolve_cache_retention(opts.cache_retention)
        api_key = _resolve_client_api_key(model.provider, opts.api_key, opts.headers)
        # Pi parity: cacheSessionId is undefined when retention is "none".
        cache_session_id = None if cache_retention == "none" else opts.session_id
        default_headers = _build_client_headers(
            model, context, compat, opts.headers, cache_session_id
        )

        client = opts.client or create_async_client(
            api_key=api_key,
            base_url=getattr(model, "base_url", "") or None,
            default_headers=default_headers or None,
            timeout_ms=opts.timeout_ms,
            max_retries=opts.max_retries,
        )

        params = build_params(model, context, opts)
        if opts.on_payload is not None:
            next_params = await _maybe_await(opts.on_payload(params, model))
            if next_params is not None:
                params = next_params

        # Relocate non-OpenAI kwargs AFTER on_payload (the hook sees the
        # Pi-shaped flat params) so the Python SDK accepts the call.
        params = _relocate_extra_body_params(params)

        # Pi parity (P-60): pi forwards ``options.signal`` into the per-request
        # SDK options (openai-responses.ts:125-130) so an in-flight generation
        # is HTTP-cancelled. The Python ``openai>=1.50`` SDK has no native
        # AbortSignal binding for ``responses.create``, so — matching the
        # established :mod:`aelix_ai.providers.openai_completions` precedent —
        # ``signal`` is NOT a per-request kwarg here. Abort is best-effort /
        # post-hoc: the harness signal is observed via
        # :attr:`SimpleStreamOptions.signal` after the stream drains (see the
        # abort-detection guard below) and via the ``except`` reason mapping.
        request_options: dict[str, Any] = {}
        if opts.timeout_ms is not None:
            request_options["timeout"] = opts.timeout_ms / 1000.0

        iterator, raw_response = await _open_responses_stream(
            client, params, request_options
        )

        if opts.on_response is not None:
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

        # Pi parity: push ``start`` AFTER on_response (openai-responses.ts:158).
        yield AssistantStartEvent(partial=_snapshot())

        async for event in process_responses_stream(
            iterator, state, model, OpenAIResponsesStreamOptions()
        ):
            yield event

        # Abort detection — pi openai-responses.ts:160-167.
        if opts.signal is not None and getattr(opts.signal, "aborted", False):
            raise RuntimeError("Request was aborted")
        if state.stop_reason == "aborted":
            raise RuntimeError("Request was aborted")
        if state.stop_reason == "error":
            raise RuntimeError("An unknown error occurred")

        output = AssistantMessage(
            content=list(state.content),
            stop_reason=state.stop_reason,
            usage=state.usage,
            response_id=state.response_id,
            api=model.api,
            provider=model.provider,
            model=model.id,
        )
        done_reason: Literal["stop", "length", "toolUse"]
        if state.stop_reason == "toolUse":
            done_reason = "toolUse"
        elif state.stop_reason == "length":
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
        error_output = AssistantMessage(
            content=list(state.content),
            stop_reason=reason,
            error_message=err_msg,
            response_id=state.response_id,
            api=model.api,
            provider=model.provider,
            model=model.id,
        )
        yield AssistantErrorEvent(
            reason=reason, error=error_output, error_message=err_msg
        )


def stream_simple_openai_responses(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Pi parity: ``streamSimple`` (openai-responses.ts:174-191).

    SYNC factory — resolves auth eagerly (raising at call time when no key /
    auth header is available, mirroring the completions adapter) and clamps
    the reasoning level BEFORE returning the async generator. ``off`` clamps
    to ``None`` so the reasoning-off branch in :func:`build_params` engages.
    """

    opts = _coerce_options(options)
    # Pi parity: ``getClientApiKey`` is called for its throw side-effect.
    # The full ``stream`` re-resolves (incl. env fallback) for the client.
    _resolve_client_api_key(model.provider, opts.api_key, opts.headers)

    reasoning_effort: str | None = None
    if opts.reasoning:
        clamped = clamp_thinking_level(model, opts.reasoning)
        reasoning_effort = None if clamped == "off" else clamped
    opts = replace(opts, reasoning_effort=reasoning_effort)  # type: ignore[arg-type]

    return stream_openai_responses(model, context, opts)


# === Provider registration (REGISTERED at startup via register_providers) ===


class _OpenAIResponsesProvider:
    """Concrete :class:`Provider` Protocol implementer for ``openai-responses``."""

    api: str = OPENAI_RESPONSES_API
    source_id: str | None = None

    def stream(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        return stream_openai_responses(model, context, options)

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        return stream_simple_openai_responses(model, context, options)


OPENAI_RESPONSES_PROVIDER: Provider = _OpenAIResponsesProvider()


def register_all() -> None:
    """Register the OpenAI Responses adapter on the global registry.

    REGISTERED (Workflow B, #15): called at startup from
    :func:`aelix_coding_agent.cli.runtime_bootstrap.register_providers`.
    Idempotent — replaces the registry entry under ``api == "openai-responses"``.
    """

    register_provider_object(
        OPENAI_RESPONSES_PROVIDER, source_id=BUILTIN_SOURCE_ID
    )


__all__ = [
    "BUILTIN_SOURCE_ID",
    "OPENAI_RESPONSES_API",
    "OPENAI_RESPONSES_PROVIDER",
    "OpenAIResponsesOptions",
    "build_params",
    "register_all",
    "stream_openai_responses",
    "stream_simple_openai_responses",
]
