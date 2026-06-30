"""Google **Vertex AI** thin adapter — pi parity (#15).

Pi parity: ``packages/ai/src/api/google-vertex.ts`` (~584 LOC) at SHA
``3d6acb37b93d2ceedfcc170b2d212c34fedbf193``.

Thin per-provider adapter for the ``google-vertex`` API (provider id
``google-vertex``, ``GOOGLE_CLOUD_API_KEY`` or Application Default
Credentials). REUSES the shared engine
(:mod:`aelix_ai.providers._google_shared`) for message/tool conversion,
thought-signature replay, the streaming state machine, and usage — only the
client construction (Vertex mode) and auth resolution differ from the Gemini
Developer API adapter.

DORMANT (Workflow A, #15): :func:`register_all` is **defined but NOT called**
from :func:`aelix_coding_agent.cli.runtime_bootstrap.register_providers`. The
``google-vertex`` models stay hidden until a later sprint (Workflow B) wires
the registration on.

Vertex-specific divergences from the Gemini adapter, faithful to pi:

- **Auth**: an explicit Vertex ``apiKey`` (``GOOGLE_CLOUD_API_KEY`` /
  stored credential), else Application Default Credentials requiring a project
  (``GOOGLE_CLOUD_PROJECT`` / ``GCLOUD_PROJECT``) and location
  (``GOOGLE_CLOUD_LOCATION``). A placeholder (``<...>``) or the
  ``gcp-vertex-credentials`` marker is treated as "no key" → ADC.
- **Thinking family**: Vertex's ``streamSimple`` only special-cases Gemini 3
  Pro / Flash (no Gemma 4 branch) and its budget table has no flash-lite entry
  (flash-lite ids fall through to the ``2.5-flash`` table), matching pi
  ``google-vertex.ts`` — so this adapter uses its own thinking helpers rather
  than the shared (Gemini-flavored) ones.
- No ``on_response`` (the streaming SDK exposes no raw HTTP response); abort is
  best-effort / post-hoc (P-60). See the Gemini adapter docstring.
"""

from __future__ import annotations

import inspect
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from aelix_ai.api_registry import register_provider_object
from aelix_ai.messages import AssistantMessage
from aelix_ai.models import clamp_thinking_level
from aelix_ai.providers._env_api_keys import get_env_api_key
from aelix_ai.providers._google_client import (
    create_vertex_client,
    open_generate_content_stream,
)
from aelix_ai.providers._google_shared import (
    GoogleStreamState,
    GoogleThinking,
    build_google_params,
    is_gemini3_flash_model,
    is_gemini3_pro_model,
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


# Pi parity: ``KnownApi`` value — the Vertex AI api id.
GOOGLE_VERTEX_API: str = "google-vertex"

# Pi parity: ``GCP_VERTEX_CREDENTIALS_MARKER`` (google-vertex.ts:55).
_GCP_VERTEX_CREDENTIALS_MARKER = "gcp-vertex-credentials"

# Pi parity: ``isPlaceholderApiKey`` (google-vertex.ts:417-419).
_PLACEHOLDER_KEY = re.compile(r"^<[^>]+>$")


@dataclass(frozen=True)
class GoogleVertexOptions(SimpleStreamOptions):
    """Extends :class:`SimpleStreamOptions` with Vertex-only extras.

    Pi parity: ``GoogleVertexOptions`` (google-vertex.ts:43-52). ``project`` /
    ``location`` override the env-derived ADC values; ``thinking`` is
    precomputed by :func:`stream_simple_google_vertex`.
    """

    tool_choice: Literal["auto", "none", "any"] | None = None
    thinking: GoogleThinking | None = None
    project: str | None = None
    location: str | None = None


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` only when it's awaitable (callbacks may be sync/async)."""

    if inspect.isawaitable(value):
        return await value
    return value


def _coerce_options(
    options: GoogleVertexOptions | SimpleStreamOptions | None,
) -> GoogleVertexOptions:
    """Widen a generic :class:`SimpleStreamOptions` into the Vertex shape."""

    if options is None:
        return GoogleVertexOptions()
    if isinstance(options, GoogleVertexOptions):
        return options
    return GoogleVertexOptions(
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


# === Vertex auth resolution (pi google-vertex.ts:409-440) ===


def _resolve_vertex_api_key(api_key: str | None) -> str | None:
    """Pi parity: ``resolveApiKey`` (google-vertex.ts:409-415).

    A blank key, the ``gcp-vertex-credentials`` marker, or a ``<...>``
    placeholder all resolve to ``None`` → fall back to ADC.
    """

    if not api_key:
        return None
    trimmed = api_key.strip()
    if (
        not trimmed
        or trimmed == _GCP_VERTEX_CREDENTIALS_MARKER
        or _PLACEHOLDER_KEY.match(trimmed)
    ):
        return None
    return trimmed


def _resolve_project(options: GoogleVertexOptions) -> str:
    """Pi parity: ``resolveProject`` (google-vertex.ts:421-432)."""

    project = (
        options.project
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
    )
    if not project:
        raise RuntimeError(
            "Vertex AI requires a project ID. Set "
            "GOOGLE_CLOUD_PROJECT/GCLOUD_PROJECT or pass project in options."
        )
    return project


def _resolve_location(options: GoogleVertexOptions) -> str:
    """Pi parity: ``resolveLocation`` (google-vertex.ts:434-440)."""

    location = options.location or os.environ.get("GOOGLE_CLOUD_LOCATION")
    if not location:
        raise RuntimeError(
            "Vertex AI requires a location. Set GOOGLE_CLOUD_LOCATION or "
            "pass location in options."
        )
    return location


def _create_client(model: Model, opts: GoogleVertexOptions) -> Any:
    """Resolve Vertex auth and build the client (pi stream() 95-99).

    An explicit Vertex API key (option or ``GOOGLE_CLOUD_API_KEY``) takes
    precedence; otherwise ADC with a resolved project + location.
    """

    raw_key = opts.api_key or get_env_api_key(model.provider)
    api_key = _resolve_vertex_api_key(raw_key)
    headers = dict(getattr(model, "headers", None) or {})
    if opts.headers:
        headers.update(opts.headers)
    base_url = getattr(model, "base_url", "") or None

    if api_key:
        return create_vertex_client(
            api_key=api_key, base_url=base_url, headers=headers or None
        )
    return create_vertex_client(
        project=_resolve_project(opts),
        location=_resolve_location(opts),
        base_url=base_url,
        headers=headers or None,
    )


def build_params(
    model: Model,
    context: Context,
    options: GoogleVertexOptions | SimpleStreamOptions | None,
) -> dict[str, Any]:
    """Assemble ``generateContentStream`` params.

    Pi parity: ``buildParams`` (google-vertex.ts:442-499), delegated to the
    shared :func:`build_google_params` (identical config assembly to the
    Gemini adapter).
    """

    opts = _coerce_options(options)
    return build_google_params(
        model,
        context,
        temperature=opts.temperature,
        max_tokens=opts.max_tokens,
        tool_choice=opts.tool_choice,
        thinking=opts.thinking,
        disabled_thinking_config=_vertex_disabled_thinking_config,
    )


async def stream_google_vertex(
    model: Model,
    context: Context,
    options: GoogleVertexOptions | SimpleStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Pi parity: ``stream`` (google-vertex.ts:68-299).

    Reuses the shared :func:`process_google_stream` engine; only the client
    (Vertex mode) differs from the Gemini adapter. Auth/project/location
    resolution errors surface as :class:`AssistantErrorEvent` (pi catches them
    in the same try/catch), not as synchronous raises.
    """

    opts = _coerce_options(options)
    state = GoogleStreamState()

    try:
        client = opts.client or _create_client(model, opts)

        params = build_params(model, context, opts)
        if opts.on_payload is not None:
            next_params = await _maybe_await(opts.on_payload(params, model))
            if next_params is not None:
                params = next_params

        chunk_iter = await open_generate_content_stream(client, params)

        yield AssistantStartEvent(
            partial=AssistantMessage(
                api=model.api, provider=model.provider, model=model.id
            )
        )

        async for event in process_google_stream(chunk_iter, state, model):
            yield event

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


# === Vertex thinking-family helpers (pi google-vertex.ts:528-584) ===


def _vertex_thinking_level(effort: str, model_id: str) -> str:
    """Pi parity: ``getGemini3ThinkingLevel`` (google-vertex.ts:528-552).

    Vertex collapses Gemini 3 Pro to two levels (LOW / HIGH); all other models
    map 1:1. Note the **absence of a Gemma 4 branch** (vs the Gemini adapter).
    """

    if is_gemini3_pro_model(model_id):
        if effort in ("minimal", "low"):
            return "LOW"
        return "HIGH"
    if effort == "minimal":
        return "MINIMAL"
    if effort == "low":
        return "LOW"
    if effort == "medium":
        return "MEDIUM"
    return "HIGH"


# Pi parity: the Vertex budget tables (google-vertex.ts:554-584). NOTE: no
# flash-lite table — a ``2.5-flash-lite`` id matches the ``2.5-flash`` check.
_VERTEX_BUDGET_2_5_PRO: dict[str, int] = {
    "minimal": 128,
    "low": 2048,
    "medium": 8192,
    "high": 32768,
}
_VERTEX_BUDGET_2_5_FLASH: dict[str, int] = {
    "minimal": 128,
    "low": 2048,
    "medium": 8192,
    "high": 24576,
}


def _vertex_disabled_thinking_config(model_id: str) -> dict[str, Any]:
    """Pi parity: ``getDisabledThinkingConfig`` (google-vertex.ts:512-526).

    Unlike the shared (Gemini-flavored)
    :func:`aelix_ai.providers._google_shared.get_disabled_thinking_config`,
    Vertex has **no Gemma 4 branch** — a Gemma id falls through to
    ``thinkingBudget: 0`` like Gemini 2.x. Gemini 3 Pro/Flash can't fully
    disable thinking, so use the lowest ``thinkingLevel`` (no
    ``includeThoughts``).
    """

    if is_gemini3_pro_model(model_id):
        return {"thinkingLevel": "LOW"}
    if is_gemini3_flash_model(model_id):
        return {"thinkingLevel": "MINIMAL"}
    return {"thinkingBudget": 0}


def _vertex_google_budget(model_id: str, effort: str) -> int:
    """Pi parity: ``getGoogleBudget`` (google-vertex.ts:554-584).

    No flash-lite branch (so flash-lite ids use the ``2.5-flash`` table) and no
    custom-budget plumbing (``options.thinkingBudgets`` is not exposed by
    Aelix's :class:`SimpleStreamOptions`).
    """

    if "2.5-pro" in model_id:
        return _VERTEX_BUDGET_2_5_PRO[effort]
    if "2.5-flash" in model_id:
        return _VERTEX_BUDGET_2_5_FLASH[effort]
    return -1


def _thinking_for_simple(
    model: Model, options: GoogleVertexOptions
) -> GoogleThinking:
    """Pi parity: the thinking-family selection in ``streamSimple``.

    Pi parity: google-vertex.ts:301-335. No ``reasoning`` → disabled.
    Otherwise clamp the effort (``off`` → ``high``); Gemini 3 Pro / Flash use a
    ``thinkingLevel``, all others a ``thinkingBudget``.
    """

    if not options.reasoning:
        return GoogleThinking(enabled=False)

    clamped = clamp_thinking_level(model, options.reasoning)
    effort = "high" if clamped in ("off", None) else clamped

    if is_gemini3_pro_model(model.id) or is_gemini3_flash_model(model.id):
        return GoogleThinking(
            enabled=True, level=_vertex_thinking_level(effort, model.id)
        )
    return GoogleThinking(
        enabled=True, budget_tokens=_vertex_google_budget(model.id, effort)
    )


def stream_simple_google_vertex(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Pi parity: ``streamSimple`` (google-vertex.ts:301-335).

    SYNC factory. Unlike the Gemini adapter, Vertex does **NOT** raise on a
    missing API key — it can authenticate via ADC, so auth resolution (and any
    project/location error) is deferred into :func:`stream_google_vertex`,
    where pi catches it as an error event.
    """

    opts = _coerce_options(options)
    opts = replace(opts, thinking=_thinking_for_simple(model, opts))
    return stream_google_vertex(model, context, opts)


# === Provider registration (DORMANT — register_all defined, NOT called) ===


class _GoogleVertexProvider:
    """Concrete :class:`Provider` implementer for ``google-vertex``."""

    api: str = GOOGLE_VERTEX_API
    source_id: str | None = None

    def stream(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        return stream_google_vertex(model, context, options)

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        return stream_simple_google_vertex(model, context, options)


GOOGLE_VERTEX_PROVIDER: Provider = _GoogleVertexProvider()


def register_all() -> None:
    """Register the Vertex AI adapter on the global registry.

    DORMANT (Workflow A, #15): defined but **NOT** called from
    :func:`aelix_coding_agent.cli.runtime_bootstrap.register_providers`.
    Idempotent — replaces the registry entry under ``api == "google-vertex"``.
    """

    register_provider_object(GOOGLE_VERTEX_PROVIDER, source_id=BUILTIN_SOURCE_ID)


__all__ = [
    "GOOGLE_VERTEX_API",
    "GOOGLE_VERTEX_PROVIDER",
    "GoogleVertexOptions",
    "build_params",
    "register_all",
    "stream_google_vertex",
    "stream_simple_google_vertex",
]
