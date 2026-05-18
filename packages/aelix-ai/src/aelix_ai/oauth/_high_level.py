"""High-level OAuth helpers — Sprint 6c · Phase 4.3 · §J.

Pi parity: ``packages/ai/src/utils/oauth/index.ts:104-152`` (SHA 734e08e).

The Pi ``getOAuthApiKey(providerId, credentials)`` helper takes an
already-loaded credentials dict and returns the (possibly refreshed)
API key. Aelix splits the same surface into:

- :func:`get_oauth_api_key_from_credentials` — the direct port (takes
  a credentials dict, returns the refreshed pair).
- :class:`AuthStorage.get_oauth_api_key` — the wrapper that reads /
  writes ``auth.json`` and forwards to this helper (lives in
  :mod:`auth_storage`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from aelix_ai.oauth._registry import get_oauth_provider
from aelix_ai.oauth.types import OAuthCredentials


@dataclass
class OAuthRefreshResult:
    """Pi parity: ``index.ts:130`` ``{newCredentials, apiKey}``."""

    new_credentials: OAuthCredentials
    api_key: str


async def get_oauth_api_key_from_credentials(
    provider_id: str,
    credentials: dict[str, OAuthCredentials],
) -> OAuthRefreshResult | None:
    """Pi parity: ``index.ts:127-152`` ``getOAuthApiKey``.

    Looks up credentials by ``provider_id``, refreshes when expired
    (``time.time()*1000 >= creds.expires``), and returns the
    ``{newCredentials, apiKey}`` pair. Returns :data:`None` when no
    credentials are stored.

    Raises:
        RuntimeError: when the provider id is unknown OR refresh fails.
    """

    provider = get_oauth_provider(provider_id)
    if provider is None:
        raise RuntimeError(f"Unknown OAuth provider: {provider_id}")

    creds = credentials.get(provider_id)
    if creds is None:
        return None

    # Pi parity: anthropic.ts:223 — ``Date.now() >= creds.expires``.
    now_ms = int(time.time() * 1000)
    if now_ms >= creds.expires:
        try:
            creds = await provider.refresh_token(creds)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to refresh OAuth token for {provider_id}"
            ) from exc

    api_key = provider.get_api_key(creds)
    return OAuthRefreshResult(new_credentials=creds, api_key=api_key)


__all__ = [
    "OAuthRefreshResult",
    "get_oauth_api_key_from_credentials",
]
