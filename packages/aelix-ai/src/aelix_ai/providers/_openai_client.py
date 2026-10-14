"""Thin OpenAI SDK wrapper — Sprint 6b (ADR-0047 §E), amended by #278 (ADR-0265).

Pi parity: ``providers/openai-completions.ts:443-496`` ``createClient``
uses the official ``openai`` npm package. The Aelix port targets the
official ``openai`` Python SDK wrapped behind this module so a future
``httpx`` swap does not ripple through every caller. The range is
``>=2.7.2,<3`` since #278 (ADR-0265): 2.7.2 is the first release that runs on
Python 3.14 (#262), and 3.0 moved to HTTPX2.

The OAuth-flow questions Pi answers in ``providers/anthropic.ts`` do not
apply here — every supported provider in Sprint 6b authenticates via a
static bearer or API key.

#278 review round 2: every client this module builds is an
``AelixAsyncOpenAI``, which keeps two things openai 2.x changed off aelix's
wire:

* **Two variables 2.34.0 started reading in the constructor.**
  ``OPENAI_CUSTOM_HEADERS`` is merged into the client's headers after the auth
  header, so an exported ``Authorization`` replaced any provider's key
  (OpenRouter, a local server, a ``models.json`` provider) and every other line
  in it went along. ``OPENAI_ADMIN_KEY`` becomes the client's admin key (sent
  only by the admin endpoints, which aelix never calls). Neither is kept, for
  any provider: openai 1.x read neither, so the wire is what it was
  (ADR-0254 §2.3, §3). ``os.environ`` is not touched.
* **The retry wait.** 2.52.0 honours a ``Retry-After`` of up to 120 s (1.x: up
  to 60 s) and stops retrying above that. The SDK's own retries here honour a
  server-requested delay only up to the request's ``max_retry_delay_ms`` (pi
  ``maxRetryDelayMs``; unset, 60000 ms - openai 1.x's fixed 60 s) and back off
  as 1.x did above it, so a rate-limited turn takes the time it took on 1.x.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import AsyncOpenAI

#: pi ``DEFAULT_MAX_RETRY_DELAY_MS`` (``packages/ai/src/utils/provider-retry.ts``
#: at 3daa20280) and the settings default of ``retry.provider.maxRetryDelayMs``;
#: openai 1.x honoured a ``Retry-After`` of up to the same 60 s.
DEFAULT_MAX_RETRY_DELAY_MS = 60_000

#: The variables openai 2.34.0 and later read in ``AsyncOpenAI.__init__`` that
#: 1.x did not. A client built here keeps neither (#278).
DROPPED_ENV_VARIABLES: tuple[str, ...] = ("OPENAI_CUSTOM_HEADERS", "OPENAI_ADMIN_KEY")

_RETRY_AFTER_HEADERS = frozenset({"retry-after", "retry-after-ms"})


def retry_after_cap_seconds(max_retry_delay_ms: Any) -> float | None:
    """The longest server-requested retry wait the SDK honours, in seconds.

    ``None`` means no limit. pi's meaning of ``maxRetryDelayMs``: unset is
    60000 ms, and zero or less disables the limit (``provider-retry.ts``
    ``validateServerRetryDelayMs``: ``maxDelayMs > 0 && delayMs > maxDelayMs``).
    A value that is not a finite number (a string, a boolean, NaN) counts as
    unset, as #408's retry settings do.
    """

    value = max_retry_delay_ms
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        value = DEFAULT_MAX_RETRY_DELAY_MS
    if value <= 0:
        return None
    return value / 1000.0


def sdk_retry_options(opts: Any) -> dict[str, Any]:
    """A request's retry knobs, as :func:`create_async_client` takes them (#278)."""

    return {
        "max_retries": getattr(opts, "max_retries", None),
        "max_retry_delay_ms": getattr(opts, "max_retry_delay_ms", None),
    }


@lru_cache(maxsize=1)
def _client_class() -> type[AsyncOpenAI]:
    """The ``AsyncOpenAI`` subclass every client built here is (#278).

    Built lazily so importing this module never imports the SDK. ``copy()`` /
    ``with_options()`` rebuild through ``self.__class__``, so a copy drops the
    variables too, and it keeps the retry cap.
    """

    import httpx
    from openai import AsyncOpenAI

    class AelixAsyncOpenAI(AsyncOpenAI):
        """An ``AsyncOpenAI`` that sends and waits as openai 1.x did (#278)."""

        #: Seconds; ``None`` is no limit. Set by :func:`create_async_client`.
        _aelix_retry_after_cap: float | None = DEFAULT_MAX_RETRY_DELAY_MS / 1000.0

        def __init__(self, *, default_headers: Any = None, **kwargs: Any) -> None:
            super().__init__(default_headers=default_headers, **kwargs)
            # OPENAI_CUSTOM_HEADERS (2.34+): the custom headers are the caller's.
            self._custom_headers = dict(default_headers or {})
            # OPENAI_ADMIN_KEY (2.34+): no admin key, whatever is exported.
            if getattr(self, "admin_api_key", None) is not None:
                self.admin_api_key = None

        def copy(self, *args: Any, **kwargs: Any) -> Any:
            copied = super().copy(*args, **kwargs)
            copied._aelix_retry_after_cap = self._aelix_retry_after_cap
            return copied

        with_options = copy

        def _calculate_retry_timeout(
            self, remaining_retries: int, options: Any, response_headers: Any = None
        ) -> float:
            retry_after = self._parse_retry_after_header(response_headers)
            cap = self._aelix_retry_after_cap
            if (
                retry_after is not None
                and math.isfinite(retry_after)
                and retry_after > 0
                and (cap is None or retry_after <= cap)
            ):
                return retry_after
            # Otherwise the SDK's own backoff (0.5 s doubling up to 8 s, with
            # jitter), as openai 1.x did above its 60 s.
            return super()._calculate_retry_timeout(remaining_retries, options, None)

        def _should_retry(self, response: httpx.Response) -> bool:
            # 2.52+ refuses to retry when the server asks for more than 120 s;
            # 1.x retried, with the backoff above. The rest of the rule is the
            # SDK's own.
            headers = [
                (name, value)
                for name, value in response.headers.multi_items()
                if name.lower() not in _RETRY_AFTER_HEADERS
            ]
            return super()._should_retry(httpx.Response(response.status_code, headers=headers))

    return AelixAsyncOpenAI


def create_async_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    default_headers: dict[str, str] | None = None,
    timeout_ms: int | None = None,
    max_retries: int | None = None,
    max_retry_delay_ms: int | None = None,
    http_client: Any = None,
) -> AsyncOpenAI:
    """Build an :class:`openai.AsyncOpenAI` client (an ``AelixAsyncOpenAI``).

    Pi parity: ``createClient`` (``openai-completions.ts:443``). A ``None``
    ``api_key`` is passed on as ``""``, never as ``None``: given ``None`` the
    SDK reads ``OPENAI_API_KEY`` from the environment and would send the
    user's OpenAI key to any provider (#374). From openai 2.34 the SDK
    refuses ``""`` when it builds the client ("Missing credentials"), or, with
    ``OPENAI_ADMIN_KEY`` exported, builds it and refuses the request before
    sending ("Could not resolve authentication method"). So callers MUST settle
    a keyless request before calling this; both adapters do (Responses raises
    ``No API key`` or uses ``"unused"`` beside an auth header; completions
    builds with a placeholder that never reaches the wire, #278).

    Args:
        api_key: bearer / API key. The SDK injects it as
            ``Authorization: Bearer …`` for the default OpenAI endpoint.
        base_url: optional override for OpenAI-compatible endpoints
            (OpenRouter, Together, DeepSeek, …).
        default_headers: extra headers (e.g. session-affinity, copilot
            machine id) merged onto every request. They are all the custom
            headers the client sends: ``OPENAI_CUSTOM_HEADERS`` adds none (#278).
        timeout_ms: per-request timeout in milliseconds; converted to
            seconds for the SDK's ``timeout`` parameter.
        max_retries: SDK-level retry count override.
        max_retry_delay_ms: the longest ``Retry-After`` / ``retry-after-ms``
            the SDK's own retries wait for (:func:`retry_after_cap_seconds`);
            a longer one gets the SDK's backoff instead (#278).
        http_client: an ``httpx.AsyncClient`` of the caller's (a private CA,
            say: ``examples/selfhosted``). Closing the client closes it too.
    """

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
    if http_client is not None:
        kwargs["http_client"] = http_client
    client: Any = _client_class()(**kwargs)
    client._aelix_retry_after_cap = retry_after_cap_seconds(max_retry_delay_ms)
    return client


__all__ = [
    "DEFAULT_MAX_RETRY_DELAY_MS",
    "DROPPED_ENV_VARIABLES",
    "create_async_client",
    "retry_after_cap_seconds",
    "sdk_retry_options",
]
