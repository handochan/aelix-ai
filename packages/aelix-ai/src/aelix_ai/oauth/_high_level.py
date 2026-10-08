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
from aelix_ai.utils.terminal_text import Controls, safe_for_terminal


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
            # The cause goes in the MESSAGE, not just in ``__cause__``, so the
            # user sees why ("502 Bad Gateway from https://api.github.com", "401
            # Unauthorized ..."). The old message dropped it.
            #
            # And in ``__cause__`` too (``raise ... from exc``): whether the turn
            # retries is decided on the chain, never on this text (#379,
            # ADR-0251 §12). ``OAuthRefreshError`` walks it with
            # ``_helpers.refresh_retry_reason`` - the token endpoint's status
            # (``oauth_http_error``) or a transport error - and the harness
            # retries by that answer. An earlier note here said the harness
            # read ``str(exc)`` against ``_RETRYABLE_ERROR_PATTERN``; it never
            # did (the auth raise came before any assistant message, ADR-0251
            # §9 B1), and a text match would retry a 401 whose quoted body
            # happens to say "502".
            #
            # An extension's refresh is classified the same way: an httpx
            # transport error or ``raise_for_status()`` counts, a bare
            # ``RuntimeError("502 ...")`` does not.
            #
            # Neutered as well as cut (#186). Every refresh goes through here,
            # including an extension-registered provider's, whose message may
            # quote its server raw: ``[:300]`` alone kept ``ESC[2J`` - it sits in
            # the first bytes - and cleared the screen from ``-p`` stderr.
            # SPACE, not delete: this text is a whole message, and deleting its
            # newlines would glue the last word of one line to the next.
            detail = str(exc).strip() or type(exc).__name__
            detail = safe_for_terminal(detail, controls=Controls.SPACE, max_chars=300)
            raise RuntimeError(
                f"Failed to refresh OAuth token for {provider_id}: {detail}"
            ) from exc

    api_key = provider.get_api_key(creds)
    return OAuthRefreshResult(new_credentials=creds, api_key=api_key)


__all__ = [
    "OAuthRefreshResult",
    "get_oauth_api_key_from_credentials",
]
