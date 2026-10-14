"""Thin OpenAI SDK wrapper — Sprint 6b (ADR-0047 §E).

Pi parity: ``providers/openai-completions.ts:443-496`` ``createClient``
uses the official ``openai`` npm package. The Aelix port targets the
official ``openai`` Python SDK wrapped behind this module so a future
``httpx`` swap does not ripple through every caller. The range is
``>=2.7.2,<3`` since #278 (ADR-0265): 2.7.2 is the first release that runs on
Python 3.14 (#262), and 3.0 moved to HTTPX2.

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

    Pi parity: ``createClient`` (``openai-completions.ts:443``). A ``None``
    ``api_key`` is passed on as ``""``, never as ``None``: given ``None`` the
    SDK reads ``OPENAI_API_KEY`` from the environment and would send the
    user's OpenAI key to any provider (#374). From openai 2.34 the SDK
    refuses ``""`` outright ("Missing credentials"), so callers MUST settle a
    keyless request before calling this; both adapters do (Responses raises
    ``No API key`` or uses ``"unused"`` beside an auth header; completions
    builds with a placeholder that never reaches the wire, #278).

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
