"""Anthropic OAuth flow (Claude Pro/Max) — Sprint 6c · Phase 4.3 · §F.

Pi parity: ``packages/ai/src/utils/oauth/anthropic.ts`` (SHA 734e08e).

Direct port of Pi's authorization-code + PKCE flow with the local
callback server. Constants (``CLIENT_ID``, ``AUTHORIZE_URL``,
``TOKEN_URL``, ``CALLBACK_PORT``, ``SCOPES``) match Pi exactly so the
on-the-wire OAuth round-trip is byte-identical.

HTTP client: :class:`httpx.AsyncClient` (transitive dep of openai +
anthropic SDKs). 30-second timeout mirrors Pi's
``signal: AbortSignal.timeout(30_000)`` (anthropic.ts:177).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import inspect
import os
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from aelix_ai.oauth._callback_server import start_callback_server

# Sprint 6e W6 (P-157): single-owner ``maybe_await`` helper. The local
# ``_maybe_await`` name remains importable for back-compat.
from aelix_ai.oauth._helpers import maybe_await as _maybe_await
from aelix_ai.oauth._pkce import generate_pkce
from aelix_ai.oauth.types import (
    OAuthAuthInfo,
    OAuthCredentials,
    OAuthLoginCallbacks,
    OAuthPrompt,
)

# Pi parity: anthropic.ts:28. base64-encoded for trivial obfuscation.
_CLIENT_ID_B64: str = "OWQxYzI1MGEtZTYxYi00NGQ5LTg4ZWQtNTk0NGQxOTYyZjVl"
CLIENT_ID: str = base64.b64decode(_CLIENT_ID_B64).decode("ascii")

# Pi parity: anthropic.ts:29-36.
ANTHROPIC_OAUTH_ID: str = "anthropic"
AUTHORIZE_URL: str = "https://claude.ai/oauth/authorize"
TOKEN_URL: str = "https://platform.claude.com/v1/oauth/token"
CALLBACK_HOST: str = os.environ.get("PI_OAUTH_CALLBACK_HOST", "127.0.0.1")
CALLBACK_PORT: int = 53692
CALLBACK_PATH: str = "/callback"
REDIRECT_URI: str = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"
SCOPES: str = (
    "org:create_api_key user:profile user:inference "
    "user:sessions:claude_code user:mcp_servers user:file_upload"
)

# Pi parity: anthropic.ts:177 — 30s timeout on token exchange.
_TOKEN_TIMEOUT_SECONDS: float = 30.0

# Pi parity: anthropic.ts:223 — 5-minute safety margin baked into
# ``expires``. ``Date.now() >= creds.expires`` triggers refresh BEFORE
# the token actually expires server-side.
_EXPIRES_SAFETY_MARGIN_MS: int = 5 * 60 * 1000


def _parse_authorization_input(input_str: str) -> dict[str, str | None]:
    """Pi parity: ``anthropic.ts:51-79`` ``parseAuthorizationInput``.

    Accepts raw codes, full URLs, ``code#state`` fragment splits, and
    ``code=...&state=...`` query strings.
    """

    value = input_str.strip()
    if not value:
        return {}

    # Try URL parse first.
    try:
        url = urlparse(value)
        if url.scheme and url.netloc:
            from urllib.parse import parse_qs

            params = parse_qs(url.query)
            return {
                "code": (params.get("code") or [None])[0],
                "state": (params.get("state") or [None])[0],
            }
    except Exception:  # noqa: BLE001
        pass

    # ``code#state`` fragment split (Pi anthropic.ts:65-68).
    if "#" in value:
        code, state = value.split("#", 1)
        return {"code": code, "state": state}

    # ``code=...&state=...`` querystring (Pi anthropic.ts:70-76).
    if "code=" in value:
        from urllib.parse import parse_qs

        params = parse_qs(value)
        return {
            "code": (params.get("code") or [None])[0],
            "state": (params.get("state") or [None])[0],
        }

    return {"code": value, "state": None}


def _format_error_details(error: BaseException) -> str:
    """Pi parity: ``anthropic.ts:81-96`` ``formatErrorDetails``."""

    parts: list[str] = [f"{type(error).__name__}: {error}"]
    cause = getattr(error, "__cause__", None) or getattr(error, "__context__", None)
    if cause is not None:
        parts.append(f"cause={_format_error_details(cause)}")
    return "; ".join(parts)


async def _post_json(url: str, body: dict[str, Any]) -> str:
    """Pi parity: ``anthropic.ts:169-187`` ``postJson``.

    POST JSON; raise on non-2xx with a Pi-shape error message
    (``HTTP request failed. status=<n>; url=<u>; body=<b>``).
    """

    async with httpx.AsyncClient(timeout=_TOKEN_TIMEOUT_SECONDS) as client:
        response = await client.post(
            url,
            json=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        response_body = response.text
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"HTTP request failed. status={response.status_code}; "
                f"url={url}; body={response_body}"
            )
        return response_body


async def _exchange_authorization_code(
    code: str,
    state: str,
    verifier: str,
    redirect_uri: str,
) -> OAuthCredentials:
    """Pi parity: ``anthropic.ts:189-225`` ``exchangeAuthorizationCode``."""

    import json as _json
    import time as _time

    try:
        response_body = await _post_json(
            TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "state": state,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
    except Exception as exc:
        raise RuntimeError(
            f"Token exchange request failed. url={TOKEN_URL}; "
            f"redirect_uri={redirect_uri}; response_type=authorization_code; "
            f"details={_format_error_details(exc)}"
        ) from exc

    try:
        token_data = _json.loads(response_body)
    except (_json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            f"Token exchange returned invalid JSON. url={TOKEN_URL}; "
            f"body={response_body}; details={_format_error_details(exc)}"
        ) from exc

    # Sprint 6c W6 (W4 m7): preserve unknown fields (notably ``scope``)
    # in ``extra`` so Pi parity for ``[key: string]: unknown`` round-trips
    # through ``OAuthCredentials.to_json``.
    _known = {"refresh_token", "access_token", "expires_in"}
    extras = {k: v for k, v in token_data.items() if k not in _known}
    return OAuthCredentials(
        refresh=token_data["refresh_token"],
        access=token_data["access_token"],
        expires=int(_time.time() * 1000)
        + int(token_data["expires_in"]) * 1000
        - _EXPIRES_SAFETY_MARGIN_MS,
        extra=extras,
    )


async def login_anthropic(callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
    """Pi parity: ``anthropic.ts:227-343`` ``loginAnthropic``.

    Authorization-code + PKCE flow:

    1. Generate PKCE verifier + challenge.
    2. Start the local callback server (port 53692).
    3. Construct the authorize URL and invoke ``callbacks.on_auth``.
    4. Race ``server.wait_for_code`` against ``callbacks.on_manual_code_input``
       (if provided) — whichever resolves first wins.
    5. Fall back to ``callbacks.on_prompt`` when neither produced a code.
    6. Exchange code → tokens via :func:`_exchange_authorization_code`.

    Always closes the callback server in ``finally``.
    """

    verifier, challenge = generate_pkce()
    # Sprint 6c W6 (P-98 / W4 M6): wire CALLBACK_HOST / CALLBACK_PORT /
    # CALLBACK_PATH explicitly so the module-level ``PI_OAUTH_CALLBACK_HOST``
    # env override actually takes effect (previously dead code).
    server = await start_callback_server(
        verifier,
        host=CALLBACK_HOST,
        port=CALLBACK_PORT,
        path=CALLBACK_PATH,
    )
    redirect_uri_for_exchange = REDIRECT_URI

    code: str | None = None
    state: str | None = None

    try:
        auth_params = urlencode(
            {
                "code": "true",
                "client_id": CLIENT_ID,
                "response_type": "code",
                "redirect_uri": REDIRECT_URI,
                "scope": SCOPES,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": verifier,
            }
        )

        await _maybe_await(
            callbacks.on_auth(
                OAuthAuthInfo(
                    url=f"{AUTHORIZE_URL}?{auth_params}",
                    instructions=(
                        "Complete login in your browser. If the browser is "
                        "on another machine, paste the final redirect URL here."
                    ),
                )
            )
        )

        if callbacks.on_manual_code_input is not None:
            # Race manual input against the callback server (Pi
            # anthropic.ts:261-307).
            manual_input: str | None = None
            manual_error: BaseException | None = None

            async def _run_manual() -> None:
                nonlocal manual_input, manual_error
                try:
                    assert callbacks.on_manual_code_input is not None
                    raw = callbacks.on_manual_code_input()
                    result_val = (
                        await raw if inspect.isawaitable(raw) else raw
                    )
                    manual_input = str(result_val)
                except BaseException as exc:  # noqa: BLE001
                    manual_error = exc
                finally:
                    # Either way, unblock the server wait.
                    server.cancel_wait()

            manual_task = asyncio.create_task(_run_manual())
            result = await server.wait_for_code()

            if result is not None and result[0]:
                code, state = result
                redirect_uri_for_exchange = REDIRECT_URI
            elif manual_input:
                parsed = _parse_authorization_input(manual_input)
                parsed_state = parsed.get("state")
                if parsed_state and parsed_state != verifier:
                    raise RuntimeError("OAuth state mismatch")
                code = parsed.get("code")
                state = parsed_state or verifier
            elif not manual_task.done():
                # Sprint 6c W6 (P-93 / W4 M4): Pi parity
                # (anthropic.ts:294-307) — when ``server.wait_for_code``
                # resolves to None first and ``manual_input`` is still
                # empty, await the manual task ONE MORE TIME so a
                # late-arriving user paste is honored before we fall
                # through to ``on_prompt``. Previously the unconditional
                # cancellation dropped this path.
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await manual_task
                if manual_error is not None:
                    raise manual_error
                if manual_input:
                    parsed = _parse_authorization_input(manual_input)
                    parsed_state = parsed.get("state")
                    if parsed_state and parsed_state != verifier:
                        raise RuntimeError("OAuth state mismatch")
                    code = parsed.get("code")
                    state = parsed_state or verifier

            # Cancel any still-running manual task so it doesn't leak.
            if not manual_task.done():
                manual_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await manual_task

            if manual_error is not None and code is None:
                raise manual_error
        else:
            result = await server.wait_for_code()
            if result is not None and result[0]:
                code, state = result
                redirect_uri_for_exchange = REDIRECT_URI

        if not code:
            input_value = await _maybe_await(
                callbacks.on_prompt(
                    OAuthPrompt(
                        message="Paste the authorization code or full redirect URL:",
                        placeholder=REDIRECT_URI,
                    )
                )
            )
            parsed = _parse_authorization_input(str(input_value))
            parsed_state = parsed.get("state")
            if parsed_state and parsed_state != verifier:
                raise RuntimeError("OAuth state mismatch")
            code = parsed.get("code")
            state = parsed_state or verifier

        if not code:
            raise RuntimeError("Missing authorization code")
        if not state:
            raise RuntimeError("Missing OAuth state")

        if callbacks.on_progress is not None:
            await _maybe_await(
                callbacks.on_progress("Exchanging authorization code for tokens...")
            )

        return await _exchange_authorization_code(
            code, state, verifier, redirect_uri_for_exchange
        )
    finally:
        server.shutdown()


async def refresh_anthropic_token(refresh_token: str) -> OAuthCredentials:
    """Pi parity: ``anthropic.ts:345-379`` ``refreshAnthropicToken``."""

    import json as _json
    import time as _time

    try:
        response_body = await _post_json(
            TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": refresh_token,
            },
        )
    except Exception as exc:
        raise RuntimeError(
            f"Anthropic token refresh request failed. url={TOKEN_URL}; "
            f"details={_format_error_details(exc)}"
        ) from exc

    try:
        data = _json.loads(response_body)
    except (_json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            f"Anthropic token refresh returned invalid JSON. url={TOKEN_URL}; "
            f"body={response_body}; details={_format_error_details(exc)}"
        ) from exc

    # Sprint 6c W6 (W4 m7): preserve unknown fields (``scope``, etc.)
    # in ``extra`` so Pi parity round-trips through ``to_json``.
    _known = {"refresh_token", "access_token", "expires_in"}
    extras = {k: v for k, v in data.items() if k not in _known}
    return OAuthCredentials(
        refresh=data["refresh_token"],
        access=data["access_token"],
        expires=int(_time.time() * 1000)
        + int(data["expires_in"]) * 1000
        - _EXPIRES_SAFETY_MARGIN_MS,
        extra=extras,
    )


class _AnthropicOAuthProvider:
    """Pi parity: ``anthropic.ts:381-402`` ``anthropicOAuthProvider``."""

    id: str = ANTHROPIC_OAUTH_ID
    name: str = "Anthropic (Claude Pro/Max)"
    uses_callback_server: bool = True

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
        return await login_anthropic(callbacks)

    async def refresh_token(
        self, credentials: OAuthCredentials
    ) -> OAuthCredentials:
        return await refresh_anthropic_token(credentials.refresh)

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        return credentials.access


ANTHROPIC_OAUTH_PROVIDER = _AnthropicOAuthProvider()


__all__ = [
    "ANTHROPIC_OAUTH_ID",
    "ANTHROPIC_OAUTH_PROVIDER",
    "AUTHORIZE_URL",
    "CALLBACK_HOST",
    "CALLBACK_PATH",
    "CALLBACK_PORT",
    "CLIENT_ID",
    "REDIRECT_URI",
    "SCOPES",
    "TOKEN_URL",
    "login_anthropic",
    "refresh_anthropic_token",
]
