"""Thin SDK wrapper — Sprint 6a (ADR-0045 §A.4), amended by #374 (ADR-0254).

Pi `providers/anthropic.ts:1` imports the official ``@anthropic-ai/sdk``
package. For Aelix we use the official ``anthropic`` Python SDK
(``>=0.40,<1.0``) for the same byte-level Pi parity, wrapped behind this
thin module so future ``httpx``-only swaps don't ripple through every
caller.

#374 (ADR-0254): the client this module builds never picks a credential of its
own. Left alone, the SDK reads ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN``
whenever no credential argument is passed, and (``anthropic`` 0.102) then runs
its default credential chain (``ANTHROPIC_PROFILE``, workload identity
federation, the active profile on disk) bound to whatever ``base_url`` the
client has, so a custom ``anthropic-messages`` gateway with no key of its own
received the user's Anthropic key. The credential a request carries is the one
aelix's key order resolved (ADR-0251), passed here explicitly, or nothing.
pi does the same with ``apiKey: apiKey ?? null`` / ``authToken: null`` on a
``PiAnthropic`` subclass whose ``_shouldResolveDefaultCredentials`` is false
(``packages/ai/src/api/anthropic-messages.ts:335-339`` and ``:1069-1070`` @
b223082bb).

The SDK (0.98 and later) also merges ``ANTHROPIC_CUSTOM_HEADERS`` into every
client's default headers, and that variable can carry ``x-api-key`` or
``Authorization``. A client built with ``env_custom_headers=False`` (the
default; every provider but ``anthropic``) keeps none of them: its headers are
exactly the ones the caller passed. pi's SDK merges them for every provider;
aelix keeps them for ``anthropic`` only (ADR-0254 §2.1, §6).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

# Header names that carry a request's auth on their own (pi ``hasRequestAuth``,
# ``anthropic-messages.ts:316-323``). Compared ignoring case.
AUTH_HEADER_NAMES: frozenset[str] = frozenset(
    {"authorization", "x-api-key", "cf-aig-authorization"}
)


def has_auth_header(headers: Any) -> bool:
    """True when ``headers`` carries a non-blank auth header (pi ``hasHeader``)."""

    if not headers:
        return False
    for name, value in dict(headers).items():
        if str(name).lower() in AUTH_HEADER_NAMES and isinstance(value, str) and value.strip():
            return True
    return False


# The variable the SDK (0.98 and later) merges into every client's default
# headers (``anthropic/_client.py``, ``custom_headers_env``).
ANTHROPIC_CUSTOM_HEADERS_ENV: str = "ANTHROPIC_CUSTOM_HEADERS"


def read_env_custom_headers() -> dict[str, str]:
    """The headers ``ANTHROPIC_CUSTOM_HEADERS`` adds, parsed as the SDK parses it.

    One ``Name: value`` per line, split at the first colon, both sides
    stripped, a line without a colon skipped (``anthropic/_client.py``
    0.98-0.102, ``custom_headers_env``). The adapter asks for these only for
    provider ``anthropic``, the one provider whose client keeps them
    (``env_custom_headers=True``), so an auth header among them is that
    request's auth: a header-only Anthropic proxy is not refused with
    ``No API key for provider`` (#374 review round 2, ADR-0254 §2.2). An SDK
    older than 0.98 does not read the variable; there the request carries no
    auth and the SDK itself refuses it before sending
    (``Could not resolve authentication method``).
    """

    raw = os.environ.get(ANTHROPIC_CUSTOM_HEADERS_ENV)
    if raw is None:
        return {}
    parsed: dict[str, str] = {}
    for line in raw.split("\n"):
        colon = line.find(":")
        if colon >= 0:
            parsed[line[:colon].strip()] = line[colon + 1 :].strip()
    return parsed


@lru_cache(maxsize=2)
def _client_class(env_custom_headers: bool = False) -> type[AsyncAnthropic]:
    """The SDK client subclass every aelix Anthropic request is built on.

    Built lazily so importing this module never imports the SDK. There are two
    classes, one per ``env_custom_headers`` value, so ``copy()`` /
    ``with_options()`` (which rebuild through ``self.__class__``) keep the
    choice.
    """

    from anthropic import AsyncAnthropic

    class AelixAsyncAnthropic(AsyncAnthropic):
        """An ``AsyncAnthropic`` that never reads a credential from the environment.

        - **The constructor** (also reached by ``copy()`` / ``with_options()``,
          which rebuild through ``self.__class__``) always hands the SDK an
          explicit credential argument, so the SDK neither reads
          ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` nor runs its default
          credential chain, then stores exactly what the caller gave: no key
          means no ``x-api-key`` header at all, not an empty one (#363 verify's
          L14). Assigning after ``super().__init__`` also covers SDK versions in
          the pinned range that read ``ANTHROPIC_AUTH_TOKEN`` independently of
          ``api_key``. Being a subclass already keeps 0.102's default chain off
          (``anthropic._client._is_base_client``), as pi's ``PiAnthropic`` does.
          Each of the two layers is pinned by a row that simulates an SDK in
          which it alone stops the leak (``tests/providers/
          test_anthropic_no_env_credential_374.py``).
        - **``ANTHROPIC_CUSTOM_HEADERS``**: unless ``env_custom_headers`` is
          set, the headers the SDK keeps are exactly ``default_headers``: what
          it merged in from that variable (an ``x-api-key`` / ``Authorization``
          in any case, or anything else) is dropped.
        - **Header validation** accepts any auth header the adapter checked
          (pi ``hasRequestAuth``), ignoring case. The SDK's own check looks up
          ``Authorization`` / ``X-Api-Key`` in a plain ``dict`` (case-sensitive),
          so a ``models.json`` header spelled ``authorization`` would otherwise
          be refused once no key rides along.
        """

        def __init__(
            self,
            *,
            api_key: str | None = None,
            auth_token: str | None = None,
            default_headers: Mapping[str, str] | None = None,
            **kwargs: Any,
        ) -> None:
            key = api_key or None
            token = auth_token or None
            # Layer 1: an explicit credential argument ("" when there is no
            # key), so the SDK neither reads the credential variables nor runs
            # its default credential chain.
            super().__init__(
                api_key=key or "", auth_token=token, default_headers=default_headers, **kwargs
            )
            # Layer 2: store exactly what the caller gave. SDKs 0.40-0.97 read
            # ANTHROPIC_AUTH_TOKEN whenever ``auth_token`` is None, whatever
            # ``api_key`` is; "" must not become an empty ``x-api-key``.
            self.api_key = key
            self.auth_token = token
            if not env_custom_headers:
                # ANTHROPIC_CUSTOM_HEADERS (SDK 0.98+): keep only the caller's.
                self._custom_headers = dict(default_headers or {})

        def _validate_headers(self, headers: Any, custom_headers: Any) -> None:
            if has_auth_header(headers) or has_auth_header(custom_headers):
                return
            super()._validate_headers(headers, custom_headers)

    return AelixAsyncAnthropic


def create_async_client(
    *,
    api_key: str | None = None,
    auth_token: str | None = None,
    base_url: str | None = None,
    default_headers: dict[str, str] | None = None,
    timeout_ms: int | None = None,
    max_retries: int | None = None,
    env_custom_headers: bool = False,
) -> AsyncAnthropic:
    """Build an :class:`anthropic.AsyncAnthropic` client.

    Pi parity: ``createClient`` (``anthropic-messages.ts:982-1078`` @ b223082bb).

    Args:
        api_key: sent as ``x-api-key``. ``None`` or ``""`` means **no key**:
            the client sends no ``x-api-key`` of its own and the SDK reads no
            credential variable (``ANTHROPIC_API_KEY`` /
            ``ANTHROPIC_AUTH_TOKEN``) and runs no credential chain (#374).
            ``default_headers`` may still carry one (a ``models.json`` header),
            and so may ``ANTHROPIC_CUSTOM_HEADERS`` when
            ``env_custom_headers`` is set. Callers decide auth first; the
            adapter fails a request with neither a key nor an auth header
            before it gets here (for provider ``anthropic``, an auth header
            in ``ANTHROPIC_CUSTOM_HEADERS`` counts:
            :func:`read_env_custom_headers`).
        auth_token: sent as ``Authorization: Bearer``; same rule.
        base_url: the model's host. ``{ENV_VAR}`` placeholders are expanded.
        default_headers: headers on every request, including header-owned auth
            (OAuth / Copilot ``Authorization``, a ``models.json`` header).
        timeout_ms: per-request timeout in milliseconds.
        max_retries: SDK-level retry count override.
        env_custom_headers: keep the headers the SDK merges in from
            ``ANTHROPIC_CUSTOM_HEADERS``. The adapter sets it for provider
            ``anthropic`` only (a documented SDK feature for your own Anthropic
            proxy, which can replace the key); for any other provider those
            headers would reach a host they were not meant for (#374).
    """

    from aelix_ai.providers._base_url import expand_base_url

    kwargs: dict[str, Any] = {"api_key": api_key, "auth_token": auth_token}
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
    return _client_class(env_custom_headers)(**kwargs)


__all__ = [
    "ANTHROPIC_CUSTOM_HEADERS_ENV",
    "AUTH_HEADER_NAMES",
    "create_async_client",
    "has_auth_header",
    "read_env_custom_headers",
]
