"""OpenAI **Codex** (ChatGPT Plus/Pro) Responses adapter — pi parity (#15 / Phase B §4.1).

Pi parity: ``packages/ai/src/providers/openai-codex-responses.ts``. This is the
thin per-provider adapter for the ``openai-codex-responses`` API — the ChatGPT
backend Codex endpoint reached with a ChatGPT Plus/Pro **OAuth** token (NO API
key env var; ``openai-codex`` is intentionally absent from
:data:`aelix_ai.providers._env_api_keys.ENV_API_KEYS`).

Why a bespoke transport (not the OpenAI SDK): Codex is served at
``https://chatgpt.com/backend-api/codex/responses`` — the ChatGPT backend, NOT
``api.openai.com`` — and takes a **bespoke request body** (the system prompt
rides in ``instructions``; ``text.verbosity`` / ``tool_choice`` /
``parallel_tool_calls`` are always set) with ChatGPT-specific headers
(``chatgpt-account-id``, ``OpenAI-Beta: responses=experimental``,
``originator``). It also streams codex-flavoured SSE whose terminal event is
``response.done`` rather than the SDK's ``response.completed``. So this adapter
owns client construction, header/auth, request-body assembly, and a raw
``httpx`` SSE transport — but reuses the SHARED Responses engine
(:func:`aelix_ai.providers._openai_responses_shared.process_responses_stream`
and the message/tool converters) for the heavy lifting, exactly like the
:mod:`aelix_ai.providers.openai_responses` adapter.

Auth (self-contained): the OAuth **access token** is a JWT that carries the
``chatgpt_account_id`` claim, so the account-id header is decoded from the
token itself via the existing :func:`aelix_ai.oauth.openai_codex._get_account_id`
helper — the adapter never needs credential-store access. The harness resolves
the (auto-refreshed) access token via ``get_api_key_and_headers`` and hands it
in as :attr:`SimpleStreamOptions.api_key` (pi parity: the codex OAuth provider
defines no ``modify_models``; account-id flows via a request header).

Divergences from pi (intentional, v1 — #15 decisions), inherited from the
shared engine: service-tier cost multipliers are DROPPED; usage is emitted as a
token-count dict; ``thinking_signature`` overloads to hold the full
``ResponseReasoningItem`` JSON. The WebSocket transport pi falls back FROM is
not ported — pi degrades to SSE on WS failure anyway, so SSE-only is faithful
to the steady state.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

import httpx

from aelix_ai.api_registry import register_provider_object
from aelix_ai.messages import AssistantMessage
from aelix_ai.models import clamp_thinking_level
from aelix_ai.oauth.openai_codex import OPENAI_CODEX_OAUTH_ID, _get_account_id
from aelix_ai.providers._error_hints import describe_provider_error
from aelix_ai.providers._openai_prompt_cache import clamp_openai_prompt_cache_key
from aelix_ai.providers._openai_responses_shared import (
    OPENAI_TOOL_CALL_PROVIDERS,
    OpenAIResponsesStreamOptions,
    ResponsesStreamState,
    convert_responses_messages,
    convert_responses_tools,
    process_responses_stream,
)
from aelix_ai.providers._sanitize_unicode import sanitize_surrogates
from aelix_ai.providers.openai_completions import BUILTIN_SOURCE_ID

# Reuse the Responses-family options shape + the reasoning/cache helpers so the
# codex body stays byte-for-byte consistent with the sibling adapter.
from aelix_ai.providers.openai_responses import (
    _OFF_EFFORT_FALLBACK,
    OpenAIResponsesOptions,
    _coerce_options,
    _native_effort,
    _resolve_cache_retention,
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
)

if TYPE_CHECKING:
    from aelix_ai.providers._base import Provider

# Pi parity: ``KnownApi`` value — the codex Responses api id. This is the string
# every ``openai-codex`` catalog model declares in its ``api`` field, and the
# key this adapter registers under (so ``partition_runnable`` stops hiding them).
OPENAI_CODEX_RESPONSES_API: str = "openai-codex-responses"

# Pi parity: the codex endpoint default host. The catalog ships this as each
# model's ``base_url``; :func:`resolve_codex_url` appends ``/codex/responses``.
DEFAULT_CODEX_BASE_URL: str = "https://chatgpt.com/backend-api"

# Advertises the OAuth caller to the ChatGPT backend. The spec (Phase B §4.1)
# specifies ``"aelix"``; because the live ChatGPT backend may validate this
# header against a known client allow-list — and pi is private so we can't
# confirm the accepted value — it's overridable via ``AELIX_CODEX_ORIGINATOR``
# so live-smoke can try alternates (e.g. ``codex_cli_rs``) without a rebuild.
def _originator() -> str:
    return os.environ.get("AELIX_CODEX_ORIGINATOR") or "aelix"

# Pi parity: the codex body always sets a default system instruction when the
# session carries none (the system prompt rides in ``instructions``, NOT as an
# input message).
_DEFAULT_INSTRUCTIONS: str = "You are a helpful assistant."

# Transport retry policy (Phase B §4.1: "MAX_RETRIES=3 exponential backoff
# honoring retry-after"). Applies ONLY to establishing the stream — once bytes
# flow there is no mid-stream retry.
_MAX_RETRIES: int = 3
_RETRYABLE_STATUS: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_RETRY_BASE_DELAY_S: float = 0.5
_RETRY_MAX_DELAY_S: float = 8.0

# Transport-level errors raised by ``client.stream().__aenter__()`` BEFORE any
# response — a TCP reset / TLS handshake drop / read-before-first-byte. These
# carry no status_code, so the status-only retry gate never sees them; the
# sibling ``openai_responses`` adapter retries these via the OpenAI SDK, so the
# bespoke transport must too (review: correctness-transport MEDIUM).
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.WriteError,
    httpx.WriteTimeout,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)

# Read/idle timeout backstop (seconds) when the caller sets no ``timeout_ms``.
# Mirrors the OpenAI SDK's ~600s default the sibling adapter inherits — a long
# generation must not trip it, but a truly hung socket must not hang forever
# either (review: security-robustness LOW). NOT ``None``.
_DEFAULT_READ_TIMEOUT_S: float = 600.0

# Hard ceiling on the SSE frame accumulator: a well-behaved server sends a
# ``\n\n`` boundary every few KB, so a buffer this large means a non-framing /
# adversarial server — fail loudly instead of growing to OOM (review:
# security-robustness LOW).
_MAX_SSE_BUFFER_BYTES: int = 8 * 1024 * 1024

# Only a bounded prefix of an HTTP error body is read before the retry decision
# (it's only ever truncated to a short message anyway) so a slow/never-ending
# error body can't stall the retry path (review: security-robustness LOW).
_MAX_ERROR_BODY_BYTES: int = 8 * 1024


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` only when it's awaitable (callbacks may be sync/async)."""

    if inspect.isawaitable(value):
        return await value
    return value


# === URL + header + body assembly (pure, unit-testable) ===


def resolve_codex_url(base_url: str | None) -> str:
    """Build the codex ``/responses`` endpoint from a model ``base_url``.

    Pi parity: codex routes to ``{host}/codex/responses``. The catalog ships
    ``https://chatgpt.com/backend-api`` → ``…/codex/responses``. Idempotent when
    the base already ends in ``/codex`` (so a pre-joined override isn't doubled).
    """

    base = (base_url or DEFAULT_CODEX_BASE_URL).rstrip("/")
    if base.endswith("/codex"):
        return f"{base}/responses"
    return f"{base}/codex/responses"


def build_codex_headers(
    model: Model,
    api_key: str,
    account_id: str,
    options_headers: dict[str, str] | None,
) -> dict[str, str]:
    """Assemble the codex request headers.

    Order: ``model.headers`` < caller ``options.headers`` < the codex-required
    set (LAST so ``Authorization`` / ``chatgpt-account-id`` / ``OpenAI-Beta``
    can never be clobbered by a stray upstream header — a deliberate, safety
    divergence from pi's "options last" for these fixed fields only).
    """

    headers: dict[str, str] = dict(getattr(model, "headers", None) or {})
    if options_headers:
        headers.update(options_headers)
    headers.update(
        {
            "Authorization": f"Bearer {api_key}",
            "chatgpt-account-id": account_id,
            "OpenAI-Beta": "responses=experimental",
            "originator": _originator(),
            "accept": "text/event-stream",
            "content-type": "application/json",
        }
    )
    return headers


def build_request_body(
    model: Model,
    context: Context,
    options: OpenAIResponsesOptions | SimpleStreamOptions | None,
) -> dict[str, Any]:
    """Assemble the bespoke codex ``/responses`` request body.

    Pi parity (Phase B §4.1 "Codex"): the system prompt rides in
    ``instructions`` (so :func:`convert_responses_messages` is called with
    ``include_system_prompt=False``); ``store`` is always ``False``; ``stream``
    always ``True``; ``text.verbosity="low"``, ``include``,
    ``tool_choice="auto"`` and ``parallel_tool_calls=True`` are always present.
    Tools carry ``strict=null`` (codex-specific). ``prompt_cache_key`` /
    ``reasoning`` / ``service_tier`` gate exactly as the sibling
    ``openai-responses`` adapter.
    """

    opts = _coerce_options(options)
    messages = convert_responses_messages(
        model, context, OPENAI_TOOL_CALL_PROVIDERS, include_system_prompt=False
    )
    cache_retention = _resolve_cache_retention(opts.cache_retention)
    instructions = context.system_prompt or _DEFAULT_INSTRUCTIONS

    body: dict[str, Any] = {
        "model": model.id,
        "instructions": sanitize_surrogates(instructions),
        "input": messages,
        "store": False,
        "stream": True,
        "text": {"verbosity": "low"},
        "include": ["reasoning.encrypted_content"],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }

    if cache_retention != "none":
        cache_key = clamp_openai_prompt_cache_key(opts.session_id)
        if cache_key is not None:
            body["prompt_cache_key"] = cache_key

    tools = list(context.tools or [])
    if tools:
        codex_tools = convert_responses_tools(tools)
        # Codex takes ``strict: null`` (not the shared default ``false``).
        for tool in codex_tools:
            tool["strict"] = None
        body["tools"] = codex_tools

    if opts.service_tier is not None:
        body["service_tier"] = opts.service_tier

    # Reasoning block — mirrors ``openai_responses.build_params`` exactly.
    # ``include`` is set unconditionally above (codex always wants encrypted
    # reasoning), so this only adds the ``reasoning`` param.
    if getattr(model, "reasoning", False):
        if opts.reasoning_effort or opts.reasoning_summary:
            effort: str | int = (
                _native_effort(model, opts.reasoning_effort)
                if opts.reasoning_effort
                else "medium"
            )
            body["reasoning"] = {
                "effort": effort,
                "summary": opts.reasoning_summary or "auto",
            }
        elif model.provider != "github-copilot":
            thinking_map = model.thinking_level_map or {}
            off_is_explicit_null = "off" in thinking_map and thinking_map["off"] is None
            if not off_is_explicit_null:
                off_value = thinking_map.get("off")
                body["reasoning"] = {
                    "effort": _OFF_EFFORT_FALLBACK if off_value is None else off_value
                }

    return body


# === SSE parsing + codex event normalization (pure, unit-testable) ===


def parse_sse_block(raw: bytes) -> dict[str, Any] | None:
    """Parse one SSE event block (the bytes between two ``\\n\\n`` separators).

    Collects every ``data:`` line (concatenated with ``\\n`` when an event
    spans multiple data lines), skips the ``[DONE]`` sentinel and blanks, and
    JSON-decodes the payload. Returns :data:`None` for a block that carries no
    decodable ``data:`` payload (comment/keep-alive/``event:``-only frames).
    """

    data_lines: list[bytes] = []
    for line in raw.split(b"\n"):
        stripped = line.strip()
        if stripped.startswith(b"data:"):
            data_lines.append(stripped[len(b"data:") :].strip())
    if not data_lines:
        return None
    payload = b"\n".join(data_lines).decode("utf-8", "replace").strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, ValueError, RecursionError):
        # RecursionError: a deeply-nested adversarial payload — skip the frame
        # rather than aborting the whole stream (review: robustness NIT).
        return None
    return parsed if isinstance(parsed, dict) else None


def map_codex_events(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize codex-specific event names to the shared engine's vocabulary.

    Codex's terminal success event is ``response.done``; the shared
    :func:`process_responses_stream` finalizes on ``response.completed`` /
    ``response.incomplete`` (reading the real stop reason from the payload's
    ``response.status``). So renaming ``response.done`` → ``response.completed``
    is sufficient — ``response.incomplete`` / ``error`` / ``response.failed``
    are already understood verbatim and pass through unchanged. (Remapping
    ``response.incomplete`` to ``completed``, as one reading of the spec
    suggests, would be a no-op since ``status`` drives the length distinction.)
    """

    if event.get("type") == "response.done":
        renamed = dict(event)
        renamed["type"] = "response.completed"
        return renamed
    return event


async def _iter_codex_events(response: Any) -> AsyncIterator[dict[str, Any]]:
    """Byte-stream → normalized event dicts, framed on the SSE ``\\n\\n`` boundary."""

    buffer = b""
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        buffer += chunk
        if len(buffer) > _MAX_SSE_BUFFER_BYTES:
            raise RuntimeError(
                "OpenAI Codex SSE stream exceeded the maximum frame buffer "
                f"({_MAX_SSE_BUFFER_BYTES} bytes) without a frame boundary — "
                "aborting to avoid unbounded memory growth."
            )
        while b"\n\n" in buffer:
            raw_block, buffer = buffer.split(b"\n\n", 1)
            event = parse_sse_block(raw_block)
            if event is not None:
                yield map_codex_events(event)
    # Flush a trailing block that arrived without a final blank-line separator.
    tail = parse_sse_block(buffer)
    if tail is not None:
        yield map_codex_events(tail)


# === Transport ===


class _CodexHTTPError(RuntimeError):
    """A non-retryable (or retries-exhausted) codex HTTP error response."""

    def __init__(self, status: int, body: str) -> None:
        detail = body.strip()
        if len(detail) > 500:
            detail = detail[:500] + "…"
        super().__init__(
            f"OpenAI Codex request failed ({status})"
            + (f": {detail}" if detail else "")
        )
        self.status = status


def _create_codex_client(opts: OpenAIResponsesOptions) -> httpx.AsyncClient:
    """Build the streaming ``httpx`` client.

    The ``read`` timeout is a generous BACKSTOP (not ``None``): a long
    generation must not trip it, but a truly hung socket must eventually fail
    instead of hanging forever (review: robustness LOW). A caller-supplied
    ``timeout_ms`` overrides every phase.
    """

    if opts.timeout_ms is not None:
        secs = opts.timeout_ms / 1000.0
        timeout = httpx.Timeout(secs)
    else:
        timeout = httpx.Timeout(
            connect=30.0, read=_DEFAULT_READ_TIMEOUT_S, write=30.0, pool=30.0
        )
    return httpx.AsyncClient(timeout=timeout)


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff (0-based attempt), capped at ``_RETRY_MAX_DELAY_S``."""

    return min(_RETRY_BASE_DELAY_S * (2**attempt), _RETRY_MAX_DELAY_S)


def _retry_delay(response: Any, attempt: int) -> float:
    """Honor a ``retry-after`` header (seconds), else exponential backoff."""

    headers = getattr(response, "headers", {}) or {}
    retry_after = None
    with contextlib.suppress(Exception):
        retry_after = dict(headers).get("retry-after") or dict(headers).get(
            "Retry-After"
        )
    if retry_after is not None:
        with contextlib.suppress(ValueError, TypeError):
            return min(float(retry_after), _RETRY_MAX_DELAY_S)
    return _backoff_delay(attempt)


async def _read_error_body(response: Any) -> bytes:
    """Read only a bounded prefix of an HTTP error body (never unbounded).

    The body is only ever surfaced as a short truncated message, so reading a
    few KB is plenty and a slow/never-ending error body can't stall the retry
    path (review: robustness LOW).
    """

    chunks: list[bytes] = []
    total = 0
    try:
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            total += len(chunk)
            if total >= _MAX_ERROR_BODY_BYTES:
                break
    except Exception:  # noqa: BLE001 — a drain failure must never mask the status
        pass
    return b"".join(chunks)[:_MAX_ERROR_BODY_BYTES]


@contextlib.asynccontextmanager
async def _open_codex_stream(
    client: Any,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
) -> AsyncGenerator[Any, None]:
    """Open the codex SSE POST with bounded retry, yielding the live response.

    Retries only the *connection establishment*, up to ``_MAX_RETRIES`` times,
    on BOTH a retryable HTTP status AND a transport-level error (TCP reset / TLS
    drop / read-before-first-byte — these carry no status). On a retryable
    status the (bounded) error body is drained and the response closed before
    the backoff. A successful (``< 400``) response is yielded for streaming; an
    exhausted transport error re-raises and a terminal error status raises
    :class:`_CodexHTTPError`.
    """

    attempt = 0
    while True:
        cm = client.stream("POST", url, json=body, headers=headers)
        try:
            response = await cm.__aenter__()
        except _RETRYABLE_EXCEPTIONS:
            # No response yet — nothing to close. Retry the establishment.
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_backoff_delay(attempt))
                attempt += 1
                continue
            raise
        status = int(getattr(response, "status_code", 200))
        if status < 400:
            try:
                yield response
            finally:
                await cm.__aexit__(None, None, None)
            return
        # Error status: bounded-drain + close before deciding on a retry.
        error_body = await _read_error_body(response)
        delay = _retry_delay(response, attempt)
        await cm.__aexit__(None, None, None)
        if status in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
            await asyncio.sleep(delay)
            attempt += 1
            continue
        raise _CodexHTTPError(status, error_body.decode("utf-8", "replace"))


def _provider_response(response: Any) -> ProviderResponse:
    status = int(getattr(response, "status_code", 200))
    raw_headers = getattr(response, "headers", {}) or {}
    return ProviderResponse(
        status=status,
        headers={str(k): str(v) for k, v in dict(raw_headers).items()},
    )


# === Stream ===


async def stream_openai_codex_responses(
    model: Model,
    context: Context,
    options: OpenAIResponsesOptions | SimpleStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """Drive a codex turn: bespoke SSE transport → shared Responses engine.

    Wraps :func:`process_responses_stream` with the ``start`` / ``done``
    envelope, mirroring :func:`aelix_ai.providers.openai_responses.stream_openai_responses`.
    Any exception emits :class:`AssistantErrorEvent` with no
    :class:`AssistantDoneEvent`.
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
        api_key = opts.api_key
        if not api_key:
            raise RuntimeError(
                "No OAuth token for openai-codex — run /login and sign in to "
                "ChatGPT Plus/Pro (Codex subscription)."
            )
        account_id = _get_account_id(api_key)
        if not account_id:
            raise RuntimeError(
                "openai-codex access token is missing the chatgpt_account_id "
                "claim — re-run /login to refresh the ChatGPT credentials."
            )

        url = resolve_codex_url(getattr(model, "base_url", "") or None)
        headers = build_codex_headers(model, api_key, account_id, opts.headers)
        body = build_request_body(model, context, opts)

        if opts.on_payload is not None:
            next_body = await _maybe_await(opts.on_payload(body, model))
            if next_body is not None:
                body = next_body

        client = opts.client or _create_codex_client(opts)
        owns_client = opts.client is None
        try:
            async with _open_codex_stream(client, url, body, headers) as response:
                if opts.on_response is not None:
                    await _maybe_await(
                        opts.on_response(_provider_response(response), model)
                    )
                # Pi parity: push ``start`` AFTER on_response.
                yield AssistantStartEvent(partial=_snapshot())
                async for event in process_responses_stream(
                    _iter_codex_events(response),
                    state,
                    model,
                    OpenAIResponsesStreamOptions(),
                ):
                    yield event
        finally:
            if owns_client:
                with contextlib.suppress(Exception):
                    await client.aclose()

        # Abort detection — mirrors the openai-responses adapter.
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
        err_msg = describe_provider_error(exc)
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


def stream_simple_openai_codex_responses(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AsyncIterator[AssistantMessageEvent]:
    """SYNC factory — resolves auth eagerly + clamps reasoning, then streams.

    Pi parity with the sibling adapter's ``streamSimple``: raises at call time
    when no OAuth token is available (rather than on the first ``__anext__``),
    and clamps the reasoning level BEFORE returning the async generator
    (``off`` → ``None`` so the reasoning-off branch in
    :func:`build_request_body` engages; ``xhigh`` clamps per the model map).
    """

    opts = _coerce_options(options)
    if not opts.api_key:
        raise RuntimeError(
            "No OAuth token for openai-codex — run /login and sign in to "
            "ChatGPT Plus/Pro (Codex subscription)."
        )

    reasoning_effort: str | None = None
    if opts.reasoning:
        clamped = clamp_thinking_level(model, opts.reasoning)
        reasoning_effort = None if clamped == "off" else clamped
    opts = replace(opts, reasoning_effort=reasoning_effort)  # type: ignore[arg-type]

    return stream_openai_codex_responses(model, context, opts)


# === Provider registration (REGISTERED at startup via register_providers) ===


class _OpenAICodexResponsesProvider:
    """Concrete :class:`Provider` for ``openai-codex-responses``."""

    api: str = OPENAI_CODEX_RESPONSES_API
    source_id: str | None = None

    def stream(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        return stream_openai_codex_responses(model, context, options)

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        return stream_simple_openai_codex_responses(model, context, options)


OPENAI_CODEX_RESPONSES_PROVIDER: Provider = _OpenAICodexResponsesProvider()


def register_all() -> None:
    """Register the codex Responses adapter on the global registry.

    Called at startup from
    :func:`aelix_coding_agent.cli.runtime_bootstrap.register_providers`. This is
    what stops :func:`aelix_coding_agent.core.runnable_models.partition_runnable`
    from hiding the 10 ``openai-codex`` catalog models in the ``/model`` picker.
    Idempotent — replaces the registry entry under ``openai-codex-responses``.
    """

    register_provider_object(
        OPENAI_CODEX_RESPONSES_PROVIDER, source_id=BUILTIN_SOURCE_ID
    )


__all__ = [
    "DEFAULT_CODEX_BASE_URL",
    "OPENAI_CODEX_OAUTH_ID",
    "OPENAI_CODEX_RESPONSES_API",
    "OPENAI_CODEX_RESPONSES_PROVIDER",
    "build_codex_headers",
    "build_request_body",
    "map_codex_events",
    "parse_sse_block",
    "register_all",
    "resolve_codex_url",
    "stream_openai_codex_responses",
    "stream_simple_openai_codex_responses",
]
