"""Thin SDK wrapper — Sprint 6a (ADR-0045 §A.4).

Pi `providers/anthropic.ts:1` imports the official ``@anthropic-ai/sdk``
package. For Aelix we use the official ``anthropic`` Python SDK
(``>=0.40,<1.0``) for the same byte-level Pi parity, wrapped behind this
thin module so future ``httpx``-only swaps don't ripple through every
caller.

Sprint 6c may revisit this seam if OAuth-token flows require us to bypass
the SDK's auth layer; for now the SDK handles streaming, retries, and
header injection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


def create_async_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    default_headers: dict[str, str] | None = None,
    timeout_ms: int | None = None,
    max_retries: int | None = None,
) -> AsyncAnthropic:
    """Build an :class:`anthropic.AsyncAnthropic` client.

    Pi parity: ``providers/anthropic.ts:476-487`` ``createClient``. ``api_key``
    is optional because OAuth flows (Sprint 6c) may forward only headers
    (``Authorization: Bearer …``) instead of a static API key.
    """

    from anthropic import AsyncAnthropic

    from aelix_ai.providers._base_url import expand_base_url

    kwargs: dict[str, Any] = {}
    if api_key is not None:
        kwargs["api_key"] = api_key
    # Pi parity (cloudflare-auth.ts ``resolveCloudflareBaseUrl``): expand any
    # ``{ENV_VAR}`` placeholder from the environment before the SDK sees the URL.
    base_url = expand_base_url(base_url)
    if base_url:
        kwargs["base_url"] = base_url
    if default_headers:
        kwargs["default_headers"] = dict(default_headers)
    if timeout_ms is not None:
        # The SDK takes ``timeout`` in seconds (float). ``None`` ==
        # SDK default; we only override when caller requested one.
        kwargs["timeout"] = timeout_ms / 1000.0
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return AsyncAnthropic(**kwargs)


__all__ = ["create_async_client"]
