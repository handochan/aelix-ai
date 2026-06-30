"""Google **Gemini Developer API** thin adapter — pi parity (#15).

Pi parity: ``packages/ai/src/api/google-generative-ai.ts`` (~509 LOC) at SHA
``3d6acb37b93d2ceedfcc170b2d212c34fedbf193``.

Thin per-provider adapter for the ``google-generative-ai`` API (provider id
``google``, ``GEMINI_API_KEY``). The heavy lifting — message/tool conversion,
thought-signature replay, the streaming state machine, thinking-family
branching, and usage arithmetic — lives in the shared engine
(:mod:`aelix_ai.providers._google_shared`); this module owns client
construction, param assembly, and the ``start`` / ``done`` / ``error`` event
envelope.

DORMANT (Workflow A, #15): :func:`register_all` is **defined but NOT called**
from :func:`aelix_coding_agent.cli.runtime_bootstrap.register_providers`. The
adapter exists, is importable, and is fully tested, but the
``google-generative-ai`` models stay hidden from the ``/model`` picker until a
later sprint (Workflow B) wires the registration on. User-facing behavior is
unchanged.

Divergences from pi (intentional, v1 — #15 decisions):

- **No ``on_response``.** pi's openai-responses adapter surfaces the raw HTTP
  response; the ``google-genai`` streaming SDK does not expose the underlying
  HTTP response, so this adapter fires only ``on_payload`` (not
  ``on_response``).
- **Token-dict usage.** Cost is resolved by a higher layer; the engine emits a
  token-count dict (incl. ``reasoning``) rather than running ``calculateCost``.
  See the shared engine docstring.
- **Abort is best-effort / post-hoc (P-60).** pi forwards ``config.abortSignal``
  to cancel an in-flight generation. Matching the established
  :mod:`aelix_ai.providers.openai_responses` precedent, the signal is observed
  after the stream drains rather than forwarded to the wire.
- **``thinkingBudgets`` not plumbed.** pi reads custom per-effort budgets from
  ``options.thinkingBudgets``; Aelix's :class:`SimpleStreamOptions` does not yet
  expose them, so :func:`get_google_budget` always uses the per-family default
  tables (``custom_budgets=None``).
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
from aelix_ai.providers._google_client import (
    create_client,
    open_generate_content_stream,
)
from aelix_ai.providers._google_shared import (
    GoogleStreamState,
    GoogleThinking,
    build_google_params,
    get_google_budget,
    get_thinking_level,
    is_gemini3_flash_model,
    is_gemini3_pro_model,
    is_gemma4_model,
    process_google_stream,
)
from aelix_ai.providers.openai_completions import BUILTIN_SOURCE_ID
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)

if TYPE_CHECKING:
    from aelix_ai.providers._base import Provider


# Pi parity: ``KnownApi`` value — the Gemini Developer API id.
GOOGLE_GENERATIVE_AI_API: str = "google-generative-ai"


@dataclass(frozen=True)
class GoogleOptions(SimpleStreamOptions):
    """Extends :class:`SimpleStreamOptions` with Gemini-only extras.

    Pi parity: ``GoogleOptions`` (google-generative-ai.ts:38-45). ``thinking``
    is precomputed by :func:`stream_simple_google` (the family-branching
    selection of ``level`` vs ``budget_tokens``) and consumed by
    :func:`build_params`.
    """

    tool_choice: Literal["auto", "none", "any"] | None = None
    thinking: GoogleThinking | None = None


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` only when it's awaitable (callbacks may be sync/async)."""

    if inspect.isawaitable(value):
        return await value
    return value


def _coerce_options(
    options: GoogleOptions | SimpleStreamOptions | None,
) -> GoogleOptions:
    """Widen a generic :class:`SimpleStreamOptions` into the Google shape."""

    if options is None:
        return GoogleOptions()
    if isinstance(options, GoogleOptions):
        return options
    return GoogleOptions(
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


def _resolve_api_key(model: Model, api_key: str | None) -> str:
    """Pi parity: ``if (!apiKey) throw`` + env fallback.

    pi requires ``options.apiKey`` (filled by the harness from
    ``GEMINI_API_KEY``). Matching the :mod:`aelix_ai.providers.openai_completions`
    precedent, Aelix additionally falls back to the env key so a bare
    ``GEMINI_API_KEY`` resolves auth, then raises.
    """

    if api_key:
        return api_key
    env_key = get_env_api_key(model.provider)
    if env_key:
        return env_key
    raise RuntimeError(f"No API key for provider: {model.provider}")


def build_params(
    model: Model,
    context: Context,
    options: GoogleOptions | SimpleStreamOptions | None,
) -> dict[str, Any]:
    """Assemble ``generateContentStream`` params.

    Pi parity: ``buildParams`` (google-generative-ai.ts:343-401), delegated to
    the shared :func:`build_google_params`.
    """

    opts = _coerce_options(options)
    return build_google_params(
        model,
        context,
        temperature=opts.temperature,
        max_tokens=opts.max_tokens,
        tool_choice=opts.tool_choice,
        thinking=opts.thinking,
    )


async def stream_google(
    model: Model,
    context: Context,
    options: GoogleOptions | SimpleStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Pi parity: ``stream`` (google-generative-ai.ts:50-282).

    Drives the shared :func:`process_google_stream` engine wrapped with the
    ``start`` / ``done`` envelope. Any exception emits
    :class:`AssistantErrorEvent` with no :class:`AssistantDoneEvent`.
    """

    opts = _coerce_options(options)
    state = GoogleStreamState()

    try:
        api_key = _resolve_api_key(model, opts.api_key)
        headers = dict(getattr(model, "headers", None) or {})
        if opts.headers:
            headers.update(opts.headers)

        client = opts.client or create_client(
            api_key=api_key,
            base_url=getattr(model, "base_url", "") or None,
            headers=headers or None,
            timeout_ms=opts.timeout_ms,
        )

        params = build_params(model, context, opts)
        if opts.on_payload is not None:
            next_params = await _maybe_await(opts.on_payload(params, model))
            if next_params is not None:
                params = next_params

        chunk_iter = await open_generate_content_stream(client, params)

        # Pi parity: push ``start`` AFTER the stream is opened.
        yield AssistantStartEvent(
            partial=AssistantMessage(
                api=model.api, provider=model.provider, model=model.id
            )
        )

        async for event in process_google_stream(chunk_iter, state, model):
            yield event

        # Abort detection — pi google-generative-ai.ts:257-263.
        if opts.signal is not None and getattr(opts.signal, "aborted", False):
            raise RuntimeError("Request was aborted")
        if state.stop_reason in ("aborted", "error"):
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


def _thinking_for_simple(
    model: Model, options: SimpleStreamOptions
) -> GoogleThinking:
    """Pi parity: the thinking-family selection in ``streamSimple``.

    Pi parity: google-generative-ai.ts:284-320. No ``reasoning`` requested →
    disabled. Otherwise clamp the effort (``off`` → ``high``) and pick a
    ``thinkingLevel`` for Gemini 3 Pro / Flash / Gemma 4 models, else a
    ``thinkingBudget`` for Gemini 2.x.
    """

    if not options.reasoning:
        return GoogleThinking(enabled=False)

    clamped = clamp_thinking_level(model, options.reasoning)
    effort = "high" if clamped in ("off", None) else clamped

    if (
        is_gemini3_pro_model(model.id)
        or is_gemini3_flash_model(model.id)
        or is_gemma4_model(model.id)
    ):
        return GoogleThinking(
            enabled=True, level=get_thinking_level(effort, model.id)
        )
    return GoogleThinking(
        enabled=True, budget_tokens=get_google_budget(model.id, effort)
    )


def stream_simple_google(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Pi parity: ``streamSimple`` (google-generative-ai.ts:284-320).

    SYNC factory — resolves auth eagerly (raising at call time when no key is
    available, mirroring pi's ``if (!apiKey) throw``) and precomputes the
    thinking config before returning the async generator.
    """

    opts = _coerce_options(options)
    # Pi parity: ``getClientApiKey`` is called for its throw side-effect.
    _resolve_api_key(model, opts.api_key)
    opts = replace(opts, thinking=_thinking_for_simple(model, opts))
    return stream_google(model, context, opts)


# === Provider registration (DORMANT — register_all defined, NOT called) ===


class _GoogleProvider:
    """Concrete :class:`Provider` implementer for ``google-generative-ai``."""

    api: str = GOOGLE_GENERATIVE_AI_API
    source_id: str | None = None

    def stream(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        return stream_google(model, context, options)

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        return stream_simple_google(model, context, options)


GOOGLE_GENERATIVE_AI_PROVIDER: Provider = _GoogleProvider()


def register_all() -> None:
    """Register the Gemini Developer API adapter on the global registry.

    DORMANT (Workflow A, #15): defined but **NOT** called from
    :func:`aelix_coding_agent.cli.runtime_bootstrap.register_providers`. A
    later sprint (Workflow B) wires the call on to surface the
    ``google-generative-ai`` models. Idempotent — replaces the registry entry
    under ``api == "google-generative-ai"``.
    """

    register_provider_object(
        GOOGLE_GENERATIVE_AI_PROVIDER, source_id=BUILTIN_SOURCE_ID
    )


__all__ = [
    "GOOGLE_GENERATIVE_AI_API",
    "GOOGLE_GENERATIVE_AI_PROVIDER",
    "GoogleOptions",
    "build_params",
    "register_all",
    "stream_google",
    "stream_simple_google",
]
