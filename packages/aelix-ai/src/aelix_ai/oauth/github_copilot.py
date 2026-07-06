"""GitHub Copilot OAuth flow (device-code grant) — Sprint 6e · Phase 4.5 · §C.

Pi parity: ``packages/ai/src/utils/oauth/github-copilot.ts`` (SHA 734e08e).

Unlike Anthropic's PKCE-callback flow, Copilot uses GitHub's device-code
grant: the user manually enters a code at a verification URL, and the
agent polls GitHub's token endpoint for completion. After the GitHub
access token arrives, Copilot's ``/copilot_internal/v2/token`` endpoint
exchanges it for a Copilot-flavored bearer token that encodes the API
proxy endpoint (``proxy-ep=<host>;...``) in semicolon-delimited fields.

Sprint 6e wires the ``modify_models`` Protocol callback (Pi P-132 carry-
forward): the proxy-ep is parsed out of the token and injected as
``Model.base_url`` for every Copilot-routed model. ``enableGitHubCopilotModel``
automation is deferred to Sprint 6f (per spec §J — needs ModelRegistry).
"""

from __future__ import annotations

import asyncio
import base64
import math
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from aelix_ai.oauth.types import (
    OAuthAuthInfo,
    OAuthCredentials,
    OAuthLoginCallbacks,
    OAuthPrompt,
)

# Pi parity: github-copilot.ts:13-14. base64-decoded at module load for
# the trivial-obfuscation pattern Pi uses verbatim.
_CLIENT_ID_B64: str = "SXYxLmI1MDdhMDhjODdlY2ZlOTg="
CLIENT_ID: str = base64.b64decode(_CLIENT_ID_B64).decode("ascii")

GITHUB_COPILOT_OAUTH_ID: str = "github-copilot"

# Pi parity: github-copilot.ts:16-21 ``COPILOT_HEADERS``.
COPILOT_HEADERS: dict[str, str] = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
}

# Pi parity: github-copilot.ts:23-24.
INITIAL_POLL_INTERVAL_MULTIPLIER: float = 1.2
SLOW_DOWN_POLL_INTERVAL_MULTIPLIER: float = 1.4

# Pi parity: github-copilot.ts:245 default domain fallback.
DEFAULT_DOMAIN: str = "github.com"

# Pi parity: github-copilot.ts:270 — 5-minute safety margin baked into
# ``expires``. ``time.time()*1000 >= creds.expires`` triggers refresh.
_EXPIRES_SAFETY_MARGIN_MS: int = 5 * 60 * 1000

# Pi parity: github-copilot.ts:179 — minimum interval floor 1s.
_MIN_INTERVAL_MS: int = 1000

# 30s timeout on HTTP requests — mirrors the Anthropic flow's pattern.
_HTTP_TIMEOUT_SECONDS: float = 30.0


def normalize_domain(input_str: str) -> str | None:
    """Pi parity: ``github-copilot.ts:46-55`` ``normalizeDomain``.

    Returns the hostname of a URL or domain string, or :data:`None`
    if the input is empty or unparseable. Strings without a scheme
    are prefixed with ``https://`` before parsing.
    """

    trimmed = input_str.strip()
    if not trimmed:
        return None
    try:
        url_str = trimmed if "://" in trimmed else f"https://{trimmed}"
        parsed = urlparse(url_str)
        return parsed.hostname or None
    except Exception:  # noqa: BLE001
        return None


def _get_urls(domain: str) -> dict[str, str]:
    """Pi parity: ``github-copilot.ts:57-67`` ``getUrls``."""

    return {
        "device_code_url": f"https://{domain}/login/device/code",
        "access_token_url": f"https://{domain}/login/oauth/access_token",
        "copilot_token_url": f"https://api.{domain}/copilot_internal/v2/token",
    }


def _get_base_url_from_token(token: str) -> str | None:
    """Pi parity: ``github-copilot.ts:74-81`` ``getBaseUrlFromToken``.

    Copilot tokens encode the API proxy endpoint in semicolon-delimited
    fields::

        tid=...;exp=...;proxy-ep=proxy.individual.githubcopilot.com;...

    Returns the API URL like ``https://api.individual.githubcopilot.com``
    or :data:`None` if the field is absent.
    """

    match = re.search(r"proxy-ep=([^;]+)", token)
    if not match:
        return None
    proxy_host = match.group(1)
    # Convert ``proxy.xxx`` to ``api.xxx`` per Pi.
    api_host = re.sub(r"^proxy\.", "api.", proxy_host)
    return f"https://{api_host}"


def get_github_copilot_base_url(
    token: str | None = None, enterprise_domain: str | None = None
) -> str:
    """Pi parity: ``github-copilot.ts:83-92`` ``getGitHubCopilotBaseUrl``.

    Resolution order:

    1. Parse ``proxy-ep=`` from ``token`` if provided.
    2. Enterprise fallback: ``https://copilot-api.<enterprise_domain>``.
    3. Default: ``https://api.individual.githubcopilot.com``.
    """

    if token:
        url_from_token = _get_base_url_from_token(token)
        if url_from_token:
            return url_from_token
    if enterprise_domain:
        return f"https://copilot-api.{enterprise_domain}"
    return "https://api.individual.githubcopilot.com"


async def _start_device_flow(domain: str) -> dict[str, Any]:
    """Pi parity: ``github-copilot.ts:103-145`` ``startDeviceFlow``.

    POSTs to ``/login/device/code`` with ``client_id`` + ``scope=read:user``
    (form-urlencoded) and returns the parsed response containing
    ``device_code``, ``user_code``, ``verification_uri``, ``interval``,
    and ``expires_in``.
    """

    urls = _get_urls(domain)
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        response = await client.post(
            urls["device_code_url"],
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "GitHubCopilotChat/0.35.0",
            },
            data={
                "client_id": CLIENT_ID,
                "scope": "read:user",
            },
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"{response.status_code} {response.reason_phrase}: {response.text}"
            )
        data = response.json()

    if not isinstance(data, dict):
        raise RuntimeError("Invalid device code response")

    device_code = data.get("device_code")
    user_code = data.get("user_code")
    verification_uri = data.get("verification_uri")
    interval = data.get("interval")
    expires_in = data.get("expires_in")

    if (
        not isinstance(device_code, str)
        or not isinstance(user_code, str)
        or not isinstance(verification_uri, str)
        or not isinstance(interval, (int, float))
        or not isinstance(expires_in, (int, float))
    ):
        raise RuntimeError("Invalid device code response fields")

    return {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": verification_uri,
        "interval": int(interval),
        "expires_in": int(expires_in),
    }


async def _poll_for_github_access_token(
    domain: str, device_code: str, interval_seconds: int, expires_in: int
) -> str:
    """Pi parity: ``github-copilot.ts:170-236`` ``pollForGitHubAccessToken``.

    Polls ``/login/oauth/access_token`` until either:

    - ``access_token`` is present in the response → success.
    - ``error == "authorization_pending"`` → continue polling.
    - ``error == "slow_down"`` → back off by SLOW_DOWN multiplier.
    - any other error → raise.
    - deadline (``expires_in`` seconds from start) elapses → timeout.

    Tracks ``slow_down`` responses for clock-drift diagnostics in the
    timeout message (Pi parity: github-copilot.ts:229-233).
    """

    urls = _get_urls(domain)
    deadline_ms = int(time.time() * 1000) + expires_in * 1000
    interval_ms = max(_MIN_INTERVAL_MS, int(interval_seconds * 1000))
    interval_multiplier = INITIAL_POLL_INTERVAL_MULTIPLIER
    slow_down_responses = 0

    # Sprint 6e W6 (W4 M1): Pi polls in the order ``fetch → check →
    # sleep`` (github-copilot.ts:188-226). The Aelix port originally
    # slept BEFORE the first fetch, which added one interval's worth of
    # latency to every Copilot login. The ordering now matches Pi.
    while int(time.time() * 1000) < deadline_ms:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                urls["access_token_url"],
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "GitHubCopilotChat/0.35.0",
                },
                data={
                    "client_id": CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise RuntimeError(
                    f"{response.status_code} {response.reason_phrase}: {response.text}"
                )
            raw = response.json()

        if isinstance(raw, dict) and isinstance(raw.get("access_token"), str):
            return str(raw["access_token"])

        if isinstance(raw, dict) and isinstance(raw.get("error"), str):
            error = raw["error"]
            description = raw.get("error_description")
            new_interval = raw.get("interval")
            if error == "authorization_pending":
                pass  # fall through to the post-iteration sleep.
            elif error == "slow_down":
                slow_down_responses += 1
                if isinstance(new_interval, (int, float)) and new_interval > 0:
                    interval_ms = int(new_interval) * 1000
                else:
                    interval_ms = max(_MIN_INTERVAL_MS, interval_ms + 5000)
                interval_multiplier = SLOW_DOWN_POLL_INTERVAL_MULTIPLIER
            else:
                suffix = f": {description}" if description else ""
                raise RuntimeError(f"Device flow failed: {error}{suffix}")

        # Sprint 6e W6 (W4 M1 + P-144): sleep AFTER the fetch+check, Pi
        # parity. ``math.ceil`` matches Pi's ``Math.ceil(intervalMs *
        # intervalMultiplier)``; the previous truncating ``int()`` could
        # silently shave milliseconds off the backoff. The
        # ``remaining_ms <= 0`` guard prevents oversleeping the deadline.
        remaining_ms = deadline_ms - int(time.time() * 1000)
        if remaining_ms <= 0:
            break
        wait_ms = min(math.ceil(interval_ms * interval_multiplier), remaining_ms)
        await asyncio.sleep(wait_ms / 1000.0)

    if slow_down_responses > 0:
        raise RuntimeError(
            "Device flow timed out after one or more slow_down responses. "
            "This is often caused by clock drift in WSL or VM environments. "
            "Please sync or restart the VM clock and try again."
        )
    raise RuntimeError("Device flow timed out")


async def refresh_github_copilot_token(
    refresh_token: str, enterprise_url: str | None = None
) -> OAuthCredentials:
    """Pi parity: ``github-copilot.ts:241-273`` ``refreshGitHubCopilotToken``.

    POSTs to ``/copilot_internal/v2/token`` with the GitHub access token
    as a bearer credential and returns the Copilot-flavored bearer that
    encodes the API proxy endpoint in semicolon-delimited fields.

    Sprint 6e W6 (P-147): the persisted extras key is ``enterpriseUrl``
    (Pi camelCase, matching the raw user input) — not the prior
    ``enterprise_domain`` (snake_case + normalized hostname). The Pi
    persisted shape preserves the user-supplied string verbatim; the
    normalization happens at refresh time.
    """

    # Normalize the raw user input ONLY for URL construction (so
    # ``ghe.example.com`` and ``https://ghe.example.com`` both work);
    # the original ``enterprise_url`` value flows into ``extra`` so the
    # persisted shape matches Pi byte-for-byte.
    normalized = (
        normalize_domain(enterprise_url) if enterprise_url else None
    )
    domain = normalized or DEFAULT_DOMAIN
    urls = _get_urls(domain)

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        response = await client.get(
            urls["copilot_token_url"],
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {refresh_token}",
                **COPILOT_HEADERS,
            },
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"{response.status_code} {response.reason_phrase}: {response.text}"
            )
        raw = response.json()

    if not isinstance(raw, dict):
        raise RuntimeError("Invalid Copilot token response")

    token = raw.get("token")
    expires_at = raw.get("expires_at")

    if not isinstance(token, str) or not isinstance(expires_at, (int, float)):
        raise RuntimeError("Invalid Copilot token response fields")

    # Pi parity: github-copilot.ts:267-272 — preserve ``enterpriseUrl``
    # (camelCase) so ``refresh_token`` can be re-invoked with the right
    # enterprise URL later. Pi stores the raw user-supplied string
    # verbatim; normalization happens at every refresh.
    extra: dict[str, Any] = {}
    if enterprise_url:
        extra["enterpriseUrl"] = enterprise_url

    return OAuthCredentials(
        refresh=refresh_token,
        access=token,
        expires=int(expires_at) * 1000 - _EXPIRES_SAFETY_MARGIN_MS,
        extra=extra,
    )


async def enable_github_copilot_model(
    token: str, model_id: str, enterprise_domain: str | None = None
) -> bool:
    """Pi parity: ``github-copilot.ts:279-300`` ``enableGitHubCopilotModel``.

    Enable a model for the user's GitHub Copilot account by POSTing
    ``{base_url}/models/{model_id}/policy`` with ``{"state": "enabled"}``.
    Some Copilot models (Claude, Grok, newer GPT previews) require this
    per-account policy acceptance before they can be used — an un-enabled
    model is rejected by the completions endpoint with HTTP 400
    ``model_not_supported``.

    Best-effort: any transport/HTTP error resolves to ``False`` and never
    raises, so a single un-enablable model (e.g. one the account has no
    access to) cannot fail the whole login. Returns ``True`` on a 2xx.
    """

    base_url = get_github_copilot_base_url(token, enterprise_domain)
    url = f"{base_url}/models/{model_id}/policy"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                    **COPILOT_HEADERS,
                    "openai-intent": "chat-policy",
                    "x-interaction-type": "chat-policy",
                },
                json={"state": "enabled"},
            )
        return 200 <= response.status_code < 300
    except Exception:  # noqa: BLE001
        return False


async def enable_all_github_copilot_models(
    token: str,
    enterprise_domain: str | None = None,
    on_model: Callable[[str, bool], None] | None = None,
) -> None:
    """Pi parity: ``github-copilot.ts:302-320`` ``enableAllGitHubCopilotModels``.

    Enable every catalog model whose provider is ``github-copilot`` (the same
    set pi enumerates via ``getModels("github-copilot")``) so policy-gated
    models are usable without a manual GitHub-settings toggle. Called after a
    successful login. The per-model policy POSTs run concurrently and are each
    best-effort (see :func:`enable_github_copilot_model`); ``on_model`` is
    invoked with ``(model_id, success)`` as each completes.

    The catalog import is local to keep the OAuth module free of an
    import-time dependency on :mod:`aelix_ai.models` (mirrors pi's runtime
    ``getModels`` call rather than a top-level import).
    """

    from aelix_ai.models import get_models

    models = get_models(GITHUB_COPILOT_OAUTH_ID)

    async def _enable_one(model_id: str) -> None:
        success = await enable_github_copilot_model(
            token, model_id, enterprise_domain
        )
        if on_model is not None:
            on_model(model_id, success)

    await asyncio.gather(*(_enable_one(model.id) for model in models))


async def login_github_copilot(callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
    """Pi parity: ``github-copilot.ts:327-366`` ``loginGitHubCopilot``.

    Device-code flow:

    1. Prompt for optional GitHub Enterprise domain (blank = ``github.com``).
    2. POST ``/login/device/code`` → receive verification info.
    3. ``callbacks.on_auth`` with the verification URL + user-code instruction.
    4. Poll ``/login/oauth/access_token`` until success or timeout.
    5. Exchange GitHub access token for Copilot-flavored bearer via
       :func:`refresh_github_copilot_token`.

    6. Enable every catalog Copilot model on the account via
       :func:`enable_all_github_copilot_models` (Pi github-copilot.ts:362-364)
       so policy-gated models are usable without a manual GitHub-settings
       toggle. Best-effort — a failed enable never breaks the login.
    """

    # Sprint 6e W6 (P-157): use the shared single-owner helper.
    from aelix_ai.oauth._helpers import maybe_await as _maybe_await_helper

    input_str = await _maybe_await_helper(
        callbacks.on_prompt(
            OAuthPrompt(
                message="GitHub Enterprise URL/domain (blank for github.com)",
                placeholder="company.ghe.com",
                allow_empty=True,
            )
        )
    )

    # Sprint 6e W6 (P-147 / W4 m2): Pi parity — store the RAW user input
    # in ``extra["enterpriseUrl"]`` (so it round-trips byte-for-byte) and
    # normalize via :func:`normalize_domain` only for URL construction.
    trimmed = str(input_str).strip()
    normalized = normalize_domain(trimmed) if trimmed else None
    if trimmed and not normalized:
        raise RuntimeError("Invalid GitHub Enterprise URL/domain")
    domain = normalized or DEFAULT_DOMAIN

    device = await _start_device_flow(domain)

    await _maybe_await_helper(
        callbacks.on_auth(
            OAuthAuthInfo(
                url=device["verification_uri"],
                instructions=f"Enter code: {device['user_code']}",
            )
        )
    )

    github_access_token = await _poll_for_github_access_token(
        domain,
        device["device_code"],
        device["interval"],
        device["expires_in"],
    )

    if callbacks.on_progress is not None:
        await _maybe_await_helper(callbacks.on_progress("Exchanging device token..."))

    # Pass the RAW user input (not the normalized hostname) so the
    # persisted shape matches Pi byte-for-byte.
    credentials = await refresh_github_copilot_token(
        github_access_token, trimmed if trimmed else None
    )

    # Pi parity (github-copilot.ts:362-364): enable every catalog Copilot
    # model on the account after login. Without this, policy-gated models
    # (Claude, Grok, newer GPT previews) are rejected by the completions
    # endpoint with HTTP 400 ``model_not_supported``. Best-effort — a failed
    # enable never breaks the login. ``normalized`` is the hostname form
    # (matches :func:`_modify_copilot_models`); the token's ``proxy-ep`` wins
    # for base-URL resolution in the common case.
    if callbacks.on_progress is not None:
        await _maybe_await_helper(callbacks.on_progress("Enabling models..."))
    await enable_all_github_copilot_models(credentials.access, normalized)

    return credentials


def _modify_copilot_models(
    models: list[Any], credentials: OAuthCredentials
) -> list[Any]:
    """Pi parity: ``github-copilot.ts:390-395`` ``modifyModels``.

    Returns a NEW list with ``base_url`` injected on every Model whose
    ``provider`` field equals ``"github-copilot"``. Non-Copilot models
    pass through unchanged.

    Sprint 6e wires this as the first use of the :class:`OAuthProvider`
    ``modify_models`` Protocol callback (P-132 carry-forward). The
    injected base_url is read from the token's ``proxy-ep=`` field via
    :func:`_get_base_url_from_token`.

    Pi uses ``{...m, baseUrl}`` (JS object spread, unconditional). Aelix
    mirrors this with :func:`dataclasses.replace` since :class:`Model`
    is a dataclass in the Aelix runtime.

    Sprint 6e W6 (P-145 / P-146): the prior ``is_dataclass(model)``
    guard silently passed non-dataclass Copilot-routed models through
    untouched, hiding callable-misuse bugs. Aelix now raises
    :class:`TypeError` when a Copilot-routed model is NOT a dataclass —
    matching Pi's invariant that every model in the registry has a
    spreadable shape.
    """

    import dataclasses as _dc

    # Sprint 6e W6 (P-147): persisted extras key is ``enterpriseUrl``
    # (camelCase, raw user input). Normalize via :func:`normalize_domain`
    # so ``https://ghe.example.com`` and ``ghe.example.com`` both work.
    enterprise_url = credentials.extra.get("enterpriseUrl")
    normalized_domain = (
        normalize_domain(str(enterprise_url)) if enterprise_url else None
    )
    base_url = get_github_copilot_base_url(credentials.access, normalized_domain)

    result: list[Any] = []
    for model in models:
        provider = getattr(model, "provider", None)
        if provider == GITHUB_COPILOT_OAUTH_ID:
            if not _dc.is_dataclass(model):
                raise TypeError(
                    "modify_models expected a dataclass Model for provider "
                    f"{GITHUB_COPILOT_OAUTH_ID}, got {type(model).__name__}"
                )
            result.append(_dc.replace(model, base_url=base_url))  # type: ignore[type-var]
        else:
            result.append(model)
    return result


class _GithubCopilotOAuthProvider:
    """Pi parity: ``github-copilot.ts:368-396`` ``githubCopilotOAuthProvider``."""

    id: str = GITHUB_COPILOT_OAUTH_ID
    name: str = "GitHub Copilot"
    uses_callback_server: bool = False  # device-code flow has no callback

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
        return await login_github_copilot(callbacks)

    async def refresh_token(
        self, credentials: OAuthCredentials
    ) -> OAuthCredentials:
        # Pi parity: Sprint 6e W6 (P-147) — persisted extras key is
        # ``enterpriseUrl`` (camelCase, raw user input).
        enterprise = credentials.extra.get("enterpriseUrl")
        enterprise_str = (
            str(enterprise) if isinstance(enterprise, str) else None
        )
        return await refresh_github_copilot_token(
            credentials.refresh, enterprise_str
        )

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        return credentials.access

    def modify_models(
        self, models: list[Any], credentials: OAuthCredentials
    ) -> list[Any]:
        return _modify_copilot_models(models, credentials)


GITHUB_COPILOT_OAUTH_PROVIDER = _GithubCopilotOAuthProvider()


__all__ = [
    "CLIENT_ID",
    "COPILOT_HEADERS",
    "DEFAULT_DOMAIN",
    "GITHUB_COPILOT_OAUTH_ID",
    "GITHUB_COPILOT_OAUTH_PROVIDER",
    "enable_all_github_copilot_models",
    "enable_github_copilot_model",
    "INITIAL_POLL_INTERVAL_MULTIPLIER",
    "SLOW_DOWN_POLL_INTERVAL_MULTIPLIER",
    "_modify_copilot_models",
    "get_github_copilot_base_url",
    "login_github_copilot",
    "normalize_domain",
    "refresh_github_copilot_token",
]
