"""Thin OpenAI SDK wrapper — Sprint 6b (ADR-0047 §E).

Pi parity: ``providers/openai-completions.ts:443-496`` ``createClient``
uses the official ``openai`` npm package. The Aelix port targets the
official ``openai`` Python SDK (``>=1.50,<2.0``) wrapped behind this
module so a future ``httpx`` swap does not ripple through every caller.

The OAuth-flow questions Pi answers in ``providers/anthropic.ts`` do not
apply here — every supported provider in Sprint 6b authenticates via a
static bearer or API key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import AsyncOpenAI


def create_async_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    default_headers: dict[str, str] | None = None,
    timeout_ms: int | None = None,
    max_retries: int | None = None,
) -> AsyncOpenAI:
    """Build an :class:`openai.AsyncOpenAI` client.

    Pi parity: ``createClient`` (``openai-completions.ts:443``). ``api_key``
    is required by the SDK constructor, so when it's ``None`` we pass
    ``""`` — callers MUST surface a meaningful error before reaching the
    network (the Sprint 6b adapter checks for an empty key upstream).

    Args:
        api_key: bearer / API key. The SDK injects it as
            ``Authorization: Bearer …`` for the default OpenAI endpoint.
        base_url: optional override for OpenAI-compatible endpoints
            (OpenRouter, Together, DeepSeek, …).
        default_headers: extra headers (e.g. session-affinity, copilot
            machine id) merged onto every request.
        timeout_ms: per-request timeout in milliseconds; converted to
            seconds for the SDK's ``timeout`` parameter.
        max_retries: SDK-level retry count override.
    """

    from openai import AsyncOpenAI

    from aelix_ai.providers._base_url import expand_base_url

    kwargs: dict[str, Any] = {"api_key": api_key or ""}
    # Pi parity (cloudflare-auth.ts ``resolveCloudflareBaseUrl``): expand any
    # ``{ENV_VAR}`` placeholder (e.g. cloudflare-ai-gateway's
    # ``{CLOUDFLARE_ACCOUNT_ID}`` / ``{CLOUDFLARE_GATEWAY_ID}``) from the
    # environment before the SDK sees the URL.
    base_url = expand_base_url(base_url)
    if base_url:
        kwargs["base_url"] = base_url
    if default_headers:
        kwargs["default_headers"] = dict(default_headers)
    if timeout_ms is not None:
        kwargs["timeout"] = timeout_ms / 1000.0
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return AsyncOpenAI(**kwargs)


__all__ = ["create_async_client"]
