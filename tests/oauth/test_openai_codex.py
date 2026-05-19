"""Sprint 6e · Phase 4.5 — OpenAI Codex OAuth flow tests.

Uses stub HTTP client patches + a fake callback server (no actual
``auth.openai.com`` or port 1455 binding).
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any
from unittest.mock import patch

import pytest
from aelix_ai.oauth.openai_codex import (
    AUTHORIZE_URL,
    CALLBACK_PATH,
    CALLBACK_PORT,
    CLIENT_ID,
    JWT_CLAIM_PATH,
    OPENAI_CODEX_OAUTH_ID,
    OPENAI_CODEX_OAUTH_PROVIDER,
    ORIGINATOR_DEFAULT,
    REDIRECT_URI,
    SCOPE,
    TOKEN_URL,
    _create_state,
    _credentials_from_token_response,
    _decode_jwt_payload,
    _get_account_id,
    _parse_authorization_input,
    login_openai_codex,
    refresh_openai_codex_token,
)
from aelix_ai.oauth.types import (
    OAuthAuthInfo,
    OAuthCredentials,
    OAuthLoginCallbacks,
    OAuthPrompt,
)

# === Constants ===


def test_pi_constants_match_spec() -> None:
    """Pi parity: openai-codex.ts:24-30 constants verbatim."""

    assert CLIENT_ID == "app_EMoamEEZ73f0CkXaXp7hrann"
    assert AUTHORIZE_URL == "https://auth.openai.com/oauth/authorize"
    assert TOKEN_URL == "https://auth.openai.com/oauth/token"
    assert CALLBACK_PORT == 1455
    assert CALLBACK_PATH == "/auth/callback"
    assert REDIRECT_URI == "http://localhost:1455/auth/callback"
    assert SCOPE == "openid profile email offline_access"
    assert JWT_CLAIM_PATH == "https://api.openai.com/auth"
    assert OPENAI_CODEX_OAUTH_ID == "openai-codex"
    # Pi parity: Sprint 6e W6 (P-140) — originator default.
    assert ORIGINATOR_DEFAULT == "pi"


def test_provider_object_shape() -> None:
    assert OPENAI_CODEX_OAUTH_PROVIDER.id == "openai-codex"
    assert OPENAI_CODEX_OAUTH_PROVIDER.name == "ChatGPT Plus/Pro (Codex Subscription)"
    assert OPENAI_CODEX_OAUTH_PROVIDER.uses_callback_server is True
    # Codex provider does NOT define modify_models (Pi parity).
    assert getattr(OPENAI_CODEX_OAUTH_PROVIDER, "modify_models", None) is None


def test_get_api_key_returns_access_token() -> None:
    creds = OAuthCredentials(refresh="rt", access="codex-AT", expires=1)
    assert OPENAI_CODEX_OAUTH_PROVIDER.get_api_key(creds) == "codex-AT"


# === _create_state ===


def test_create_state_hex_32_chars() -> None:
    """Pi parity: openai-codex.ts:43-48 — 16-byte hex = 32 chars."""

    state = _create_state()
    assert len(state) == 32
    assert all(c in "0123456789abcdef" for c in state)


def test_create_state_unique() -> None:
    assert _create_state() != _create_state()


# === _parse_authorization_input ===


def test_parse_authorization_input_full_url() -> None:
    r = _parse_authorization_input(
        "http://localhost:1455/auth/callback?code=abc&state=xyz"
    )
    assert r["code"] == "abc"
    assert r["state"] == "xyz"


def test_parse_authorization_input_hash_format() -> None:
    r = _parse_authorization_input("abc#xyz")
    assert r["code"] == "abc"
    assert r["state"] == "xyz"


def test_parse_authorization_input_querystring() -> None:
    r = _parse_authorization_input("code=abc&state=xyz")
    assert r["code"] == "abc"
    assert r["state"] == "xyz"


def test_parse_authorization_input_bare_code() -> None:
    r = _parse_authorization_input("plain-code")
    assert r["code"] == "plain-code"


def test_parse_authorization_input_empty() -> None:
    assert _parse_authorization_input("") == {}


# === _decode_jwt_payload + _get_account_id ===


def _build_jwt(payload: dict[str, Any]) -> str:
    """Build a fake JWT (header.payload.signature) for testing."""

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(payload).encode())
        .rstrip(b"=")
        .decode()
    )
    sig = base64.urlsafe_b64encode(b"sig").rstrip(b"=").decode()
    return f"{header}.{payload_b64}.{sig}"


def test_decode_jwt_payload_happy() -> None:
    """Decodes the base64url payload section without verifying signature."""

    payload = {"sub": "user-123", "exp": 9999999}
    jwt = _build_jwt(payload)
    decoded = _decode_jwt_payload(jwt)
    assert decoded is not None
    assert decoded["sub"] == "user-123"


def test_decode_jwt_payload_invalid_structure_returns_none() -> None:
    """Sprint 6e W6 (P-139): Pi ``decodeJwt`` returns null on any error."""

    assert (
        _decode_jwt_payload("not.a.valid.jwt.with.too.many.parts") is None
    )


def test_decode_jwt_payload_two_parts_returns_none() -> None:
    """Sprint 6e W6 (P-139)."""

    assert _decode_jwt_payload("only.two") is None


def test_decode_jwt_payload_corrupt_base64_returns_none() -> None:
    """Sprint 6e W6 (W4 m6 / P-139): catches base64 + JSON decode errors."""

    assert _decode_jwt_payload("aaa.!!!not-base64!!!.bbb") is None


def test_get_account_id_extracts_from_claim_path() -> None:
    """Pi parity: openai-codex.ts:290-295."""

    jwt = _build_jwt(
        {JWT_CLAIM_PATH: {"chatgpt_account_id": "acct-XYZ"}}
    )
    assert _get_account_id(jwt) == "acct-XYZ"


def test_get_account_id_missing_returns_none() -> None:
    """Sprint 6e W6 (P-139): Pi ``getAccountId`` returns ``string | null``.

    Aelix originally raised ``RuntimeError`` from ``_get_account_id``
    directly. Pi defers the raise to the caller; mirror that exactly.
    """

    jwt = _build_jwt({"unrelated": "data"})
    assert _get_account_id(jwt) is None


def test_get_account_id_empty_string_returns_none() -> None:
    """Sprint 6e W6 (P-139)."""

    jwt = _build_jwt({JWT_CLAIM_PATH: {"chatgpt_account_id": ""}})
    assert _get_account_id(jwt) is None


# === Mock HTTP ===


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self.reason_phrase = "OK" if 200 <= status_code < 300 else "Error"
        self.text = json.dumps(body)
        self._body = body

    def json(self) -> Any:
        return self._body


class _FakeAsyncClient:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: Any = None,
    ) -> _FakeResponse:
        self.calls.append({"url": url, "data": data})
        return self._next_response  # type: ignore[attr-defined]


def _patch_httpx(response: _FakeResponse) -> Any:
    fake = _FakeAsyncClient()
    fake._next_response = response  # type: ignore[attr-defined]
    return patch(
        "aelix_ai.oauth.openai_codex.httpx.AsyncClient", return_value=fake
    )


# === refresh_openai_codex_token ===


async def test_refresh_openai_codex_token_happy() -> None:
    account_jwt = _build_jwt(
        {JWT_CLAIM_PATH: {"chatgpt_account_id": "acct-1"}}
    )
    body = {
        "access_token": account_jwt,
        "refresh_token": "RT2",
        "expires_in": 3600,
    }
    with _patch_httpx(_FakeResponse(200, body)):
        creds = await refresh_openai_codex_token("OLD_RT")
    assert creds.access == account_jwt
    assert creds.refresh == "RT2"
    # Sprint 6e W6 (P-138): persisted key is ``accountId`` (Pi camelCase
    # top-level field on the flat ``OAuthCredentials`` index signature),
    # not Pi's local var name ``chatgpt_account_id``.
    assert creds.extra.get("accountId") == "acct-1"
    assert creds.extra.get("chatgpt_account_id") is None


async def test_refresh_openai_codex_token_http_error() -> None:
    with _patch_httpx(_FakeResponse(401, {"error": "x"})), pytest.raises(
        RuntimeError
    ) as ei:
        await refresh_openai_codex_token("BAD")
    assert "token refresh failed" in str(ei.value).lower()


async def test_refresh_openai_codex_token_missing_account_id_raises() -> None:
    """Sprint 6e W6 (P-138/P-139): caller raises Pi-worded ``RuntimeError``."""

    jwt_no_account = _build_jwt({"unrelated": "x"})
    body = {
        "access_token": jwt_no_account,
        "refresh_token": "RT2",
        "expires_in": 3600,
    }
    with _patch_httpx(_FakeResponse(200, body)), pytest.raises(RuntimeError) as ei:
        await refresh_openai_codex_token("OLD")
    # Pi wording: ``Failed to extract accountId from token``.
    assert "accountId" in str(ei.value)


async def test_refresh_preserves_id_token_in_extras() -> None:
    """``id_token`` lands in ``extra`` (Pi P-86 extensible field)."""

    account_jwt = _build_jwt({JWT_CLAIM_PATH: {"chatgpt_account_id": "a"}})
    body = {
        "access_token": account_jwt,
        "refresh_token": "RT",
        "expires_in": 3600,
        "id_token": "ID-XYZ",
    }
    with _patch_httpx(_FakeResponse(200, body)):
        creds = await refresh_openai_codex_token("R")
    assert creds.extra.get("id_token") == "ID-XYZ"


# === Pi-parity closure pins (Sprint 6e W6) ===


def test_codex_credentials_persisted_shape_pi_compatible() -> None:
    """Sprint 6e W6 (P-138): persisted JSON shape matches Pi verbatim.

    Pi's persisted ``auth.json`` for Codex looks like::

        {"refresh": "...", "access": "...", "expires": 0,
         "accountId": "acc_1", "id_token": "tok"}

    Aelix mirrors that flat top-level shape via :meth:`to_json` merging
    the ``extra`` dict (Pi index signature ``[key: string]: unknown``).
    The KEY name must be ``accountId`` (Pi camelCase) — NOT Pi's local
    variable name ``chatgpt_account_id``.
    """

    from aelix_ai.oauth.types import OAuthCredentials

    creds = OAuthCredentials(
        refresh="r",
        access="a",
        expires=0,
        extra={"accountId": "acc_1", "id_token": "tok"},
    )
    persisted = creds.to_json()
    assert persisted["accountId"] == "acc_1"
    assert persisted["id_token"] == "tok"
    assert "extra" not in persisted  # extras merged flat, not nested.


def test_credentials_from_token_response_raises_pi_message() -> None:
    """Sprint 6e W6 (P-138/P-139): exact Pi error wording on missing claim."""

    bad_jwt = _build_jwt({"unrelated": "x"})
    data = {
        "access_token": bad_jwt,
        "refresh_token": "rt",
        "expires_in": 3600,
    }
    with pytest.raises(RuntimeError) as ei:
        _credentials_from_token_response(data)
    # Pi: ``Failed to extract accountId from token``.
    msg = str(ei.value)
    assert "accountId" in msg
    assert "extract" in msg.lower() or "missing" in msg.lower()


def test_originator_default_is_pi() -> None:
    """Sprint 6e W6 (P-140): ``originator=pi`` is the Pi default."""

    assert ORIGINATOR_DEFAULT == "pi"


# === login_openai_codex ===


async def test_login_openai_codex_via_callback_server_wins() -> None:
    """Browser callback resolves first → that code is exchanged."""

    class _FakeServer:
        redirect_uri = REDIRECT_URI

        def __init__(self) -> None:
            self._fut: asyncio.Future[Any] = (
                asyncio.get_running_loop().create_future()
            )
            self.shutdown_called = False

        def wait_for_code(self) -> asyncio.Future[Any]:
            return self._fut

        def cancel_wait(self) -> None:
            if not self._fut.done():
                self._fut.set_result(None)

        def shutdown(self) -> None:
            self.shutdown_called = True

    fake_server = _FakeServer()

    def on_auth(_info: OAuthAuthInfo) -> None:
        # Resolve the server future from on_auth so test is deterministic.
        if not fake_server._fut.done():
            # ``state`` will be re-checked in login flow against the
            # state arg captured by start_callback_server.
            pass

    async def on_prompt(_p: OAuthPrompt) -> str:
        return ""

    callbacks = OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt)

    account_jwt = _build_jwt(
        {JWT_CLAIM_PATH: {"chatgpt_account_id": "acct-1"}}
    )
    body = {
        "access_token": account_jwt,
        "refresh_token": "RT",
        "expires_in": 3600,
    }

    captured_state: dict[str, str] = {}

    async def _fake_start(state: str, **_kwargs: Any) -> Any:
        captured_state["state"] = state
        # Resolve immediately with the matching state.
        fake_server._fut.set_result(("CB_CODE", state))
        return fake_server

    with patch(
        "aelix_ai.oauth.openai_codex.start_callback_server",
        side_effect=_fake_start,
    ), _patch_httpx(_FakeResponse(200, body)):
        creds = await login_openai_codex(callbacks)

    assert creds.access == account_jwt
    # Pi parity: persisted key is ``accountId`` (camelCase top-level).
    assert creds.extra.get("accountId") == "acct-1"
    assert fake_server.shutdown_called


async def test_login_openai_codex_passes_correct_callback_args() -> None:
    """Codex callback uses port 1455 + path /auth/callback (NOT Anthropic's)."""

    captured: dict[str, Any] = {}

    class _FakeServer:
        redirect_uri = REDIRECT_URI

        def __init__(self) -> None:
            self._fut: asyncio.Future[Any] = (
                asyncio.get_running_loop().create_future()
            )

        def wait_for_code(self) -> asyncio.Future[Any]:
            return self._fut

        def cancel_wait(self) -> None:
            if not self._fut.done():
                self._fut.set_result(None)

        def shutdown(self) -> None:
            return None

    async def _fake_start(state: str, **kwargs: Any) -> Any:
        captured.update(kwargs)
        captured["state"] = state
        fs = _FakeServer()
        fs._fut.set_result(("CODE", state))
        return fs

    account_jwt = _build_jwt({JWT_CLAIM_PATH: {"chatgpt_account_id": "a"}})
    body = {"access_token": account_jwt, "refresh_token": "R", "expires_in": 3600}

    def on_auth(_info: OAuthAuthInfo) -> None:
        return None

    async def on_prompt(_p: OAuthPrompt) -> str:
        return ""

    callbacks = OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt)

    with patch(
        "aelix_ai.oauth.openai_codex.start_callback_server",
        side_effect=_fake_start,
    ), _patch_httpx(_FakeResponse(200, body)):
        await login_openai_codex(callbacks)

    assert captured.get("port") == 1455
    assert captured.get("path") == "/auth/callback"


async def test_login_openai_codex_state_mismatch_raises() -> None:
    """on_prompt fallback with wrong state raises ``State mismatch``."""

    class _FakeServer:
        redirect_uri = REDIRECT_URI

        def __init__(self) -> None:
            self._fut: asyncio.Future[Any] = (
                asyncio.get_running_loop().create_future()
            )

        def wait_for_code(self) -> asyncio.Future[Any]:
            return self._fut

        def cancel_wait(self) -> None:
            if not self._fut.done():
                self._fut.set_result(None)

        def shutdown(self) -> None:
            return None

    async def _fake_start(state: str, **_kwargs: Any) -> Any:
        fs = _FakeServer()
        fs._fut.set_result(None)  # server resolves to None → fall through
        return fs

    def on_auth(_info: OAuthAuthInfo) -> None:
        return None

    async def on_prompt(_p: OAuthPrompt) -> str:
        return "code=abc&state=DEFINITELY_WRONG"

    callbacks = OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt)

    with patch(
        "aelix_ai.oauth.openai_codex.start_callback_server",
        side_effect=_fake_start,
    ), pytest.raises(RuntimeError) as ei:
        await login_openai_codex(callbacks)
    assert "state mismatch" in str(ei.value).lower()


async def test_login_openai_codex_authorize_url_has_custom_params() -> None:
    """Pi parity: ``id_token_add_organizations`` + ``codex_cli_simplified_flow``."""

    auth_seen: list[OAuthAuthInfo] = []

    class _FakeServer:
        redirect_uri = REDIRECT_URI

        def __init__(self) -> None:
            self._fut: asyncio.Future[Any] = (
                asyncio.get_running_loop().create_future()
            )

        def wait_for_code(self) -> asyncio.Future[Any]:
            return self._fut

        def cancel_wait(self) -> None:
            if not self._fut.done():
                self._fut.set_result(None)

        def shutdown(self) -> None:
            return None

    async def _fake_start(state: str, **_kwargs: Any) -> Any:
        fs = _FakeServer()
        fs._fut.set_result(("CODE", state))
        return fs

    account_jwt = _build_jwt({JWT_CLAIM_PATH: {"chatgpt_account_id": "a"}})
    body = {"access_token": account_jwt, "refresh_token": "R", "expires_in": 3600}

    def on_auth(info: OAuthAuthInfo) -> None:
        auth_seen.append(info)

    async def on_prompt(_p: OAuthPrompt) -> str:
        return ""

    callbacks = OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt)

    with patch(
        "aelix_ai.oauth.openai_codex.start_callback_server",
        side_effect=_fake_start,
    ), _patch_httpx(_FakeResponse(200, body)):
        await login_openai_codex(callbacks)

    assert len(auth_seen) == 1
    url = auth_seen[0].url
    assert "id_token_add_organizations=true" in url
    assert "codex_cli_simplified_flow=true" in url
    assert f"client_id={CLIENT_ID}" in url
    assert "code_challenge_method=S256" in url
    # Pi parity: Sprint 6e W6 (P-140) — ``originator=pi`` URL param.
    assert "originator=pi" in url
