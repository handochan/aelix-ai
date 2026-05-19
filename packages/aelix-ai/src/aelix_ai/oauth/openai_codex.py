"""OpenAI Codex (ChatGPT Plus/Pro) OAuth flow — Sprint 6e · Phase 4.5 · §D.

Pi parity: ``packages/ai/src/utils/oauth/openai-codex.ts`` (SHA 734e08e).

PKCE callback flow similar to Anthropic, but at port 1455 path
``/auth/callback`` (NOT 53692/``/callback``), with ChatGPT-specific JWT
account_id extraction post-exchange. The ``chatgpt_account_id`` is
extracted from the JWT payload at claim path
``https://api.openai.com/auth`` and stored in
:attr:`OAuthCredentials.extra` (Sprint 6c P-86 extensible field).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import inspect
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from aelix_ai.oauth._callback_server import start_callback_server
from aelix_ai.oauth._pkce import generate_pkce
from aelix_ai.oauth.types import (
    OAuthAuthInfo,
    OAuthCredentials,
    OAuthLoginCallbacks,
    OAuthPrompt,
)

# Pi parity: openai-codex.ts:24-30.
CLIENT_ID: str = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL: str = "https://auth.openai.com/oauth/authorize"
TOKEN_URL: str = "https://auth.openai.com/oauth/token"
CALLBACK_HOST: str = os.environ.get(
    "PI_OAUTH_CODEX_CALLBACK_HOST",
    # Sprint 6e W6 (P-149): Pi uses a single ``PI_OAUTH_CALLBACK_HOST``
    # for both the Anthropic and Codex flows. Aelix introduced
    # ``PI_OAUTH_CODEX_CALLBACK_HOST`` for forward-compat but must fall
    # back to the shared name when only the shared name is exported.
    os.environ.get("PI_OAUTH_CALLBACK_HOST", "127.0.0.1"),
)
CALLBACK_PORT: int = 1455
CALLBACK_PATH: str = "/auth/callback"
REDIRECT_URI: str = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"
SCOPE: str = "openid profile email offline_access"
JWT_CLAIM_PATH: str = "https://api.openai.com/auth"

# Pi parity: openai-codex.ts — Sprint 6e W6 (P-140). Pi sets
# ``url.searchParams.set("originator", "pi")`` on the auth URL; the
# default value advertises the OAuth caller to auth.openai.com and was
# missing from the Aelix port.
ORIGINATOR_DEFAULT: str = "pi"

OPENAI_CODEX_OAUTH_ID: str = "openai-codex"

# Pi parity: anthropic.ts:177 — 30s timeout on token exchange.
_TOKEN_TIMEOUT_SECONDS: float = 30.0


def _create_state() -> str:
    """Pi parity: ``openai-codex.ts:43-48`` ``createState``.

    Returns a 16-byte hex string (32 chars) suitable for the OAuth
    ``state`` parameter.
    """

    return secrets.token_hex(16)


def _parse_authorization_input(input_str: str) -> dict[str, str | None]:
    """Pi parity: ``openai-codex.ts:50-78`` ``parseAuthorizationInput``.

    Accepts full URLs, ``code#state`` fragment splits, ``code=...&state=...``
    query strings, or bare codes.
    """

    value = input_str.strip()
    if not value:
        return {}

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

    if "#" in value:
        code, state = value.split("#", 1)
        return {"code": code, "state": state}

    if "code=" in value:
        from urllib.parse import parse_qs

        params = parse_qs(value)
        return {
            "code": (params.get("code") or [None])[0],
            "state": (params.get("state") or [None])[0],
        }

    return {"code": value, "state": None}


def _decode_jwt_payload(jwt_token: str) -> dict[str, Any] | None:
    """Decode the payload of a JWT WITHOUT signature verification.

    Pi parity: ``openai-codex.ts:80-89`` ``decodeJwt``. Used solely to
    extract ``chatgpt_account_id`` from the access token — signature
    verification is not required since the token was just issued to us
    by ``auth.openai.com``.

    Sprint 6e W6 (P-139 / W4 m6): returns :data:`None` on ANY decode
    failure (wrong part count, base64 error, JSON parse error) to match
    Pi's ``try { ... } catch { return null }`` shape. Callers must
    null-coalesce; ``_get_account_id`` propagates :data:`None` and the
    high-level token-exchange path raises the Pi error message.
    """

    try:
        parts = jwt_token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(padded)
        return json.loads(payload_bytes)
    except Exception:  # noqa: BLE001 — Pi wraps everything in try/catch.
        return None


def _get_account_id(access_token: str) -> str | None:
    """Pi parity: ``openai-codex.ts:290-295`` ``getAccountId``.

    Extracts ``chatgpt_account_id`` from the JWT at claim path
    ``https://api.openai.com/auth``. Returns :data:`None` if the claim
    is missing — Pi's ``getAccountId`` returns ``string | null`` and the
    caller (``loginOpenAICodex`` / ``refreshOpenAICodexToken``) is
    responsible for raising on null.

    Sprint 6e W6 (P-139): switched from raising to null-returning to
    match Pi exactly. The caller (``_credentials_from_token_response``)
    raises ``RuntimeError`` with Pi's wording when this returns
    :data:`None`.
    """

    payload = _decode_jwt_payload(access_token)
    if not isinstance(payload, dict):
        return None
    auth_claims = payload.get(JWT_CLAIM_PATH)
    if not isinstance(auth_claims, dict):
        return None
    account_id = auth_claims.get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id:
        return None
    return account_id


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` only when it's a coroutine/awaitable."""

    if inspect.isawaitable(value):
        return await value
    return value


def _format_error_details(error: BaseException) -> str:
    """Pi parity: ``anthropic.ts:81-96`` ``formatErrorDetails`` (reused)."""

    parts: list[str] = [f"{type(error).__name__}: {error}"]
    cause = getattr(error, "__cause__", None) or getattr(error, "__context__", None)
    if cause is not None:
        parts.append(f"cause={_format_error_details(cause)}")
    return "; ".join(parts)


async def _exchange_authorization_code(
    code: str, verifier: str, redirect_uri: str = REDIRECT_URI
) -> dict[str, Any]:
    """Pi parity: ``openai-codex.ts:92-137`` ``exchangeAuthorizationCode``.

    Returns a dict with ``access_token``, ``refresh_token``, ``expires_in``,
    and (optionally) ``id_token`` (plus other fields per the response).
    """

    async with httpx.AsyncClient(timeout=_TOKEN_TIMEOUT_SECONDS) as client:
        response = await client.post(
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
        )
        body_text = response.text
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"OpenAI Codex token exchange failed ({response.status_code}): "
                f"{body_text or response.reason_phrase}"
            )
        data = response.json()

    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in")
    if (
        not isinstance(access_token, str)
        or not isinstance(refresh_token, str)
        or not isinstance(expires_in, (int, float))
    ):
        raise RuntimeError(
            f"OpenAI Codex token exchange response missing fields: {data}"
        )
    return data


def _credentials_from_token_response(data: dict[str, Any]) -> OAuthCredentials:
    """Build :class:`OAuthCredentials` from a token exchange response.

    Pi parity: ``openai-codex.ts:131-136`` + ``:404-409``. Extracts
    ``accountId`` from the JWT, stores ``id_token`` (if present) in
    extras, and computes the expires timestamp.

    Sprint 6e W6 (P-138 / P-139): the persisted shape preserves Pi's
    flat camelCase key ``accountId`` (not Pi's local var name
    ``chatgpt_account_id``). Pi's ``OAuthCredentials`` carries the
    index signature ``[key: string]: unknown`` so the JWT-extracted
    account id is stored as a top-level ``accountId`` field after
    ``to_json()`` flattens ``extra``. The caller raises the Pi error
    message when ``_get_account_id`` returns :data:`None`.
    """

    access_token = str(data["access_token"])
    refresh_token = str(data["refresh_token"])
    expires_in = int(data["expires_in"])

    account_id = _get_account_id(access_token)
    if account_id is None:
        # Pi parity: ``openai-codex.ts:404-409`` — the caller of
        # ``getAccountId`` raises the exact message when the claim is
        # absent. Aelix mirrors that wording verbatim.
        raise RuntimeError("Failed to extract accountId from token")

    extra: dict[str, Any] = {"accountId": account_id}
    id_token = data.get("id_token")
    if isinstance(id_token, str) and id_token:
        extra["id_token"] = id_token
    # Preserve any other unknown response fields (Pi parity: ``[key:
    # string]: unknown`` index signature on OAuthCredentials).
    _known = {"access_token", "refresh_token", "expires_in", "id_token"}
    for k, v in data.items():
        if k not in _known and k not in extra:
            extra[k] = v

    return OAuthCredentials(
        refresh=refresh_token,
        access=access_token,
        expires=int(time.time() * 1000) + expires_in * 1000,
        extra=extra,
    )


async def login_openai_codex(callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
    """Pi parity: ``openai-codex.ts:308-413`` ``loginOpenAICodex``.

    PKCE callback flow:

    1. Generate PKCE verifier + challenge + random state.
    2. Start the local callback server (port 1455, path /auth/callback).
    3. Construct the authorize URL with custom params
       (``id_token_add_organizations=true`` +
       ``codex_cli_simplified_flow=true``) and invoke
       ``callbacks.on_auth``.
    4. Race ``server.wait_for_code`` against ``callbacks.on_manual_code_input``
       (if provided) — whichever resolves first wins.
    5. Fall back to ``callbacks.on_prompt`` when neither produced a code.
    6. Exchange code → tokens, extract ``chatgpt_account_id`` from JWT.
    """

    verifier, challenge = generate_pkce()
    state = _create_state()
    server = await start_callback_server(
        state, host=CALLBACK_HOST, port=CALLBACK_PORT, path=CALLBACK_PATH
    )

    code: str | None = None

    try:
        auth_params = urlencode(
            {
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "scope": SCOPE,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                # Pi parity: openai-codex.ts:201-202.
                "id_token_add_organizations": "true",
                "codex_cli_simplified_flow": "true",
                # Pi parity: openai-codex.ts — Sprint 6e W6 (P-140):
                # the ``originator`` parameter defaults to ``"pi"`` on
                # the Pi CLI; the parameter advertises the OAuth caller
                # to OpenAI's auth.openai.com and was silently dropped
                # from the Aelix port. Restored verbatim.
                "originator": ORIGINATOR_DEFAULT,
            }
        )

        await _maybe_await(
            callbacks.on_auth(
                OAuthAuthInfo(
                    url=f"{AUTHORIZE_URL}?{auth_params}",
                    instructions=(
                        "A browser window should open. Complete login to finish."
                    ),
                )
            )
        )

        if callbacks.on_manual_code_input is not None:
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
                    server.cancel_wait()

            manual_task = asyncio.create_task(_run_manual())
            result = await server.wait_for_code()

            if result is not None and result[0]:
                code = result[0]
            elif manual_input:
                parsed = _parse_authorization_input(manual_input)
                ms = parsed.get("state")
                if ms and ms != state:
                    raise RuntimeError("State mismatch")
                code = parsed.get("code")
            elif not manual_task.done():
                # Pi parity: openai-codex.ts:357-369 — re-await manual
                # task if server cancelled first but manual hasn't
                # arrived yet.
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await manual_task
                if manual_error is not None:
                    raise manual_error
                if manual_input:
                    parsed = _parse_authorization_input(manual_input)
                    ms = parsed.get("state")
                    if ms and ms != state:
                        raise RuntimeError("State mismatch")
                    code = parsed.get("code")

            if not manual_task.done():
                manual_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await manual_task

            if manual_error is not None and code is None:
                raise manual_error
        else:
            result = await server.wait_for_code()
            if result is not None and result[0]:
                code = result[0]

        if not code:
            input_value = await _maybe_await(
                callbacks.on_prompt(
                    OAuthPrompt(
                        message="Paste the authorization code (or full redirect URL):",
                        placeholder=REDIRECT_URI,
                    )
                )
            )
            parsed = _parse_authorization_input(str(input_value))
            ms = parsed.get("state")
            if ms and ms != state:
                raise RuntimeError("State mismatch")
            code = parsed.get("code")

        if not code:
            raise RuntimeError("Missing authorization code")

        if callbacks.on_progress is not None:
            await _maybe_await(
                callbacks.on_progress("Exchanging authorization code for tokens...")
            )

        try:
            token_data = await _exchange_authorization_code(code, verifier)
        except Exception as exc:
            raise RuntimeError(
                f"Token exchange request failed. url={TOKEN_URL}; "
                f"details={_format_error_details(exc)}"
            ) from exc

        return _credentials_from_token_response(token_data)
    finally:
        server.shutdown()


async def refresh_openai_codex_token(refresh_token: str) -> OAuthCredentials:
    """Pi parity: ``openai-codex.ts:418-435`` ``refreshOpenAICodexToken``."""

    async with httpx.AsyncClient(timeout=_TOKEN_TIMEOUT_SECONDS) as client:
        response = await client.post(
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
        )
        body_text = response.text
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"OpenAI Codex token refresh failed ({response.status_code}): "
                f"{body_text or response.reason_phrase}"
            )
        data = response.json()

    access_token = data.get("access_token")
    new_refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in")
    if (
        not isinstance(access_token, str)
        or not isinstance(new_refresh_token, str)
        or not isinstance(expires_in, (int, float))
    ):
        raise RuntimeError(
            f"OpenAI Codex token refresh response missing fields: {data}"
        )
    return _credentials_from_token_response(data)


class _OpenAICodexOAuthProvider:
    """Pi parity: ``openai-codex.ts:437-458`` ``openaiCodexOAuthProvider``."""

    id: str = OPENAI_CODEX_OAUTH_ID
    name: str = "ChatGPT Plus/Pro (Codex Subscription)"
    uses_callback_server: bool = True

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
        return await login_openai_codex(callbacks)

    async def refresh_token(
        self, credentials: OAuthCredentials
    ) -> OAuthCredentials:
        return await refresh_openai_codex_token(credentials.refresh)

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        return credentials.access

    # ``modify_models`` intentionally not defined — Codex's
    # ``chatgpt_account_id`` flows via request header (Sprint 6f),
    # not via ``Model.base_url``. Pi-parity: openai-codex.ts:437-458
    # omits ``modifyModels`` on the provider definition.


OPENAI_CODEX_OAUTH_PROVIDER = _OpenAICodexOAuthProvider()


__all__ = [
    "AUTHORIZE_URL",
    "CALLBACK_HOST",
    "CALLBACK_PATH",
    "CALLBACK_PORT",
    "CLIENT_ID",
    "JWT_CLAIM_PATH",
    "OPENAI_CODEX_OAUTH_ID",
    "OPENAI_CODEX_OAUTH_PROVIDER",
    "ORIGINATOR_DEFAULT",
    "REDIRECT_URI",
    "SCOPE",
    "TOKEN_URL",
    "_decode_jwt_payload",
    "_get_account_id",
    "login_openai_codex",
    "refresh_openai_codex_token",
]
