"""Sprint 6c · Phase 4.3 — Anthropic OAuth flow tests.

Uses a stub HTTP client that intercepts ``httpx.AsyncClient.post`` so
we don't actually hit ``platform.claude.com``.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aelix_ai.oauth.anthropic import (
    ANTHROPIC_OAUTH_ID,
    ANTHROPIC_OAUTH_PROVIDER,
    AUTHORIZE_URL,
    CALLBACK_PORT,
    CLIENT_ID,
    REDIRECT_URI,
    SCOPES,
    TOKEN_URL,
    _exchange_authorization_code,
    _parse_authorization_input,
    login_anthropic,
    refresh_anthropic_token,
)
from aelix_ai.oauth.types import (
    OAuthAuthInfo,
    OAuthCredentials,
    OAuthLoginCallbacks,
    OAuthPrompt,
)

# === Constants ===


def test_pi_constants_match_spec() -> None:
    """Pi parity: spec §F constant table — verbatim match."""

    assert ANTHROPIC_OAUTH_ID == "anthropic"
    assert AUTHORIZE_URL == "https://claude.ai/oauth/authorize"
    assert TOKEN_URL == "https://platform.claude.com/v1/oauth/token"
    assert CALLBACK_PORT == 53692
    assert REDIRECT_URI == "http://localhost:53692/callback"
    assert "org:create_api_key" in SCOPES
    assert "user:inference" in SCOPES
    # CLIENT_ID is base64-decoded; UUID-shape 36 chars.
    assert len(CLIENT_ID) == 36
    assert CLIENT_ID.count("-") == 4


def test_provider_object_shape() -> None:
    assert ANTHROPIC_OAUTH_PROVIDER.id == "anthropic"
    assert ANTHROPIC_OAUTH_PROVIDER.name == "Anthropic (Claude Pro/Max)"
    assert ANTHROPIC_OAUTH_PROVIDER.uses_callback_server is True


def test_get_api_key_returns_access_token() -> None:
    """Pi parity ``anthropic.ts:397-399`` ``getApiKey``."""

    creds = OAuthCredentials(refresh="r", access="sk-ant-oat-xxx", expires=1)
    assert ANTHROPIC_OAUTH_PROVIDER.get_api_key(creds) == "sk-ant-oat-xxx"


# === parseAuthorizationInput ===


def test_parse_authorization_input_full_url() -> None:
    r = _parse_authorization_input(
        "http://localhost:53692/callback?code=abc&state=xyz"
    )
    assert r["code"] == "abc"
    assert r["state"] == "xyz"


def test_parse_authorization_input_hash_format() -> None:
    """Pi parity (anthropic.ts:65-68): ``code#state`` fragment split."""

    r = _parse_authorization_input("abc#xyz")
    assert r["code"] == "abc"
    assert r["state"] == "xyz"


def test_parse_authorization_input_querystring() -> None:
    """Pi parity (anthropic.ts:70-76): ``code=...&state=...`` querystring."""

    r = _parse_authorization_input("code=abc&state=xyz")
    assert r["code"] == "abc"
    assert r["state"] == "xyz"


def test_parse_authorization_input_bare_code() -> None:
    r = _parse_authorization_input("plain-code")
    assert r["code"] == "plain-code"


def test_parse_authorization_input_empty() -> None:
    assert _parse_authorization_input("") == {}
    assert _parse_authorization_input("   ") == {}


# === Token exchange (mocked HTTP) ===


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self.text = json.dumps(body)


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient that records the last call."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.last_url: str | None = None
        self.last_body: Any = None

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    async def post(
        self, url: str, *, json: Any, headers: dict[str, str]
    ) -> _FakeResponse:
        self.last_url = url
        self.last_body = json
        # Return the canned response stored on the instance.
        return self._next_response  # type: ignore[attr-defined]


def _patch_httpx(response: _FakeResponse) -> Any:
    """Patch ``httpx.AsyncClient`` to return ``response`` from .post()."""

    fake = _FakeAsyncClient()
    fake._next_response = response  # type: ignore[attr-defined]
    return patch(
        "aelix_ai.oauth.anthropic.httpx.AsyncClient", return_value=fake
    )


async def test_exchange_authorization_code_happy() -> None:
    body = {
        "access_token": "AT",
        "refresh_token": "RT",
        "expires_in": 3600,
    }
    with _patch_httpx(_FakeResponse(200, body)):
        creds = await _exchange_authorization_code(
            code="C", state="S", verifier="V", redirect_uri=REDIRECT_URI
        )
    assert creds.access == "AT"
    assert creds.refresh == "RT"
    # 5-min safety margin baked in.
    now_ms = int(time.time() * 1000)
    assert creds.expires <= now_ms + 3600 * 1000 - 5 * 60 * 1000 + 1000
    assert creds.expires >= now_ms + 3600 * 1000 - 5 * 60 * 1000 - 1000


async def test_exchange_authorization_code_http_error() -> None:
    with _patch_httpx(_FakeResponse(400, {"error": "bad"})), pytest.raises(RuntimeError) as ei:
        await _exchange_authorization_code(
            code="C", state="S", verifier="V", redirect_uri=REDIRECT_URI
        )
    assert "Token exchange request failed" in str(ei.value)


async def test_refresh_anthropic_token_happy() -> None:
    body = {
        "access_token": "AT2",
        "refresh_token": "RT2",
        "expires_in": 7200,
    }
    with _patch_httpx(_FakeResponse(200, body)):
        creds = await refresh_anthropic_token("OLD_RT")
    assert creds.access == "AT2"
    assert creds.refresh == "RT2"


# === W4 m7 — scope (and other unknown fields) preserved in extras ===


async def test_exchange_authorization_code_preserves_scope_in_extras() -> None:
    """W4 m7: ``scope`` (and other unknown response fields) round-trip
    through ``OAuthCredentials.extra`` so Pi parity for
    ``[key: string]: unknown`` is honored.
    """

    body = {
        "access_token": "AT",
        "refresh_token": "RT",
        "expires_in": 3600,
        "scope": "user:profile user:inference",
        "id_token": "ID-X",
    }
    with _patch_httpx(_FakeResponse(200, body)):
        creds = await _exchange_authorization_code(
            code="C", state="S", verifier="V", redirect_uri=REDIRECT_URI
        )
    assert creds.extra.get("scope") == "user:profile user:inference"
    assert creds.extra.get("id_token") == "ID-X"


async def test_refresh_anthropic_token_preserves_scope_in_extras() -> None:
    """W4 m7: refresh response ``scope`` field preserved in extras."""

    body = {
        "access_token": "AT2",
        "refresh_token": "RT2",
        "expires_in": 7200,
        "scope": "user:profile",
    }
    with _patch_httpx(_FakeResponse(200, body)):
        creds = await refresh_anthropic_token("OLD_RT")
    assert creds.extra.get("scope") == "user:profile"


async def test_refresh_anthropic_token_http_error() -> None:
    with _patch_httpx(_FakeResponse(401, {"error": "unauthorized"})), pytest.raises(
        RuntimeError
    ) as ei:
        await refresh_anthropic_token("OLD_RT")
    assert "Anthropic token refresh request failed" in str(ei.value)


# === login flow ===


async def test_login_anthropic_via_manual_input_wins() -> None:
    """Manual-code-input race: when user pastes URL, server.wait is cancelled."""

    # Patch out the callback server so it doesn't actually bind a port.
    class _FakeServer:
        redirect_uri = REDIRECT_URI

        def __init__(self) -> None:
            self._fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            self.shutdown_called = False

        def wait_for_code(self) -> asyncio.Future[Any]:
            return self._fut

        def cancel_wait(self) -> None:
            if not self._fut.done():
                self._fut.set_result(None)

        def shutdown(self) -> None:
            self.shutdown_called = True

    fake_server = _FakeServer()

    auth_info_seen: list[OAuthAuthInfo] = []

    def on_auth(info: OAuthAuthInfo) -> None:
        auth_info_seen.append(info)

    def on_prompt(_p: OAuthPrompt) -> str:
        return ""

    async def on_manual_code_input() -> str:
        # Simulate user pasting the full URL.
        return "http://localhost:53692/callback?code=MANUAL&state="

    callbacks = OAuthLoginCallbacks(
        on_auth=on_auth,
        on_prompt=on_prompt,
        on_manual_code_input=on_manual_code_input,
    )

    body = {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}

    with patch(
        "aelix_ai.oauth.anthropic.start_callback_server",
        AsyncMock(return_value=fake_server),
    ), _patch_httpx(_FakeResponse(200, body)):
        creds = await login_anthropic(callbacks)

    assert creds.access == "AT"
    assert fake_server.shutdown_called
    # The auth URL was passed to on_auth.
    assert len(auth_info_seen) == 1
    assert AUTHORIZE_URL in auth_info_seen[0].url
    assert f"client_id={CLIENT_ID}" in auth_info_seen[0].url
    assert "code_challenge_method=S256" in auth_info_seen[0].url


async def test_login_anthropic_via_callback_server_wins() -> None:
    """When the browser callback resolves first, that code is used."""

    class _FakeServer:
        redirect_uri = REDIRECT_URI

        def __init__(self) -> None:
            self._fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
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
        # Resolve the callback server future from the auth callback.
        fake_server._fut.set_result(("CB_CODE", "VERIFIER_STATE"))

    def on_prompt(_p: OAuthPrompt) -> str:
        return ""

    # No manual input — server should win.
    callbacks = OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt)

    body = {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}

    with patch(
        "aelix_ai.oauth.anthropic.start_callback_server",
        AsyncMock(return_value=fake_server),
    ), _patch_httpx(_FakeResponse(200, body)):
        creds = await login_anthropic(callbacks)

    assert creds.access == "AT"


async def test_login_anthropic_manual_state_mismatch_raises() -> None:
    """Manual input with wrong state raises ``OAuth state mismatch``."""

    class _FakeServer:
        redirect_uri = REDIRECT_URI

        def __init__(self) -> None:
            self._fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
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
        return None

    def on_prompt(_p: OAuthPrompt) -> str:
        return ""

    async def on_manual_code_input() -> str:
        # State that won't match the random PKCE verifier.
        return "code=abc&state=DEFINITELY_WRONG_STATE"

    callbacks = OAuthLoginCallbacks(
        on_auth=on_auth,
        on_prompt=on_prompt,
        on_manual_code_input=on_manual_code_input,
    )

    with patch(
        "aelix_ai.oauth.anthropic.start_callback_server",
        AsyncMock(return_value=fake_server),
    ), pytest.raises(RuntimeError) as ei:
        await login_anthropic(callbacks)
    assert "state mismatch" in str(ei.value).lower()
    assert fake_server.shutdown_called


# === P-93 / W4 M4 — late-manual-input race fallback ===


async def test_login_late_manual_input_after_server_cancel_wins() -> None:
    """W6 P-93: server.wait_for_code resolves to ``None`` first (e.g.,
    the manual-input task called ``cancel_wait`` immediately on start),
    but the manual input arrives a tick later. Pi parity
    (anthropic.ts:294-307) awaits the manual task ONE MORE TIME before
    falling through to ``on_prompt``.

    Previously the unconditional cancellation dropped this late-arrival
    path; the regression is that the prompt fires even though the user
    DID paste a valid URL.
    """

    class _FakeServer:
        redirect_uri = REDIRECT_URI

        def __init__(self) -> None:
            self._fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            self.shutdown_called = False

        def wait_for_code(self) -> asyncio.Future[Any]:
            return self._fut

        def cancel_wait(self) -> None:
            if not self._fut.done():
                self._fut.set_result(None)

        def shutdown(self) -> None:
            self.shutdown_called = True

    fake_server = _FakeServer()

    on_prompt_called = False

    def on_auth(_info: OAuthAuthInfo) -> None:
        return None

    def on_prompt(_p: OAuthPrompt) -> str:
        nonlocal on_prompt_called
        on_prompt_called = True
        return ""

    manual_arrived = asyncio.Event()

    async def on_manual_code_input() -> str:
        # Immediately cancel the server-wait so it resolves to ``None``…
        fake_server.cancel_wait()
        # …then take a tick before returning. The new code path must
        # re-await this task BEFORE falling through to ``on_prompt``.
        await asyncio.sleep(0)
        manual_arrived.set()
        return "http://localhost:53692/callback?code=LATE_MANUAL&state="

    callbacks = OAuthLoginCallbacks(
        on_auth=on_auth,
        on_prompt=on_prompt,
        on_manual_code_input=on_manual_code_input,
    )

    body = {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}

    with patch(
        "aelix_ai.oauth.anthropic.start_callback_server",
        AsyncMock(return_value=fake_server),
    ), _patch_httpx(_FakeResponse(200, body)):
        creds = await login_anthropic(callbacks)

    assert creds.access == "AT"
    assert manual_arrived.is_set()
    # Crucially, on_prompt MUST NOT have been called — the late manual
    # input wins per Pi parity anthropic.ts:294-307.
    assert on_prompt_called is False


# === P-98 / W4 M6 — CALLBACK_HOST env override wiring ===


async def test_login_anthropic_passes_callback_host_to_server() -> None:
    """W6 P-98: ``CALLBACK_HOST`` / ``CALLBACK_PORT`` / ``CALLBACK_PATH``
    are wired into ``start_callback_server`` so the
    ``PI_OAUTH_CALLBACK_HOST`` env override actually takes effect.
    Previously the env var was read at module load but never passed."""

    captured: dict[str, Any] = {}

    async def _fake_start(state: str, **kwargs: Any) -> Any:
        captured["state"] = state
        captured.update(kwargs)

        class _FS:
            redirect_uri = REDIRECT_URI

            def __init__(self_inner) -> None:
                self_inner._fut: asyncio.Future[Any] = (
                    asyncio.get_running_loop().create_future()
                )
                self_inner.shutdown_called = False

            def wait_for_code(self_inner) -> asyncio.Future[Any]:
                return self_inner._fut

            def cancel_wait(self_inner) -> None:
                if not self_inner._fut.done():
                    self_inner._fut.set_result(None)

            def shutdown(self_inner) -> None:
                self_inner.shutdown_called = True

        fs = _FS()
        fs._fut.set_result(("CODE", state))
        return fs

    from aelix_ai.oauth import anthropic as anth_mod

    def on_auth(_info: OAuthAuthInfo) -> None:
        return None

    def on_prompt(_p: OAuthPrompt) -> str:
        return ""

    callbacks = OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt)

    body = {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}

    with patch(
        "aelix_ai.oauth.anthropic.start_callback_server", side_effect=_fake_start
    ), patch.object(anth_mod, "CALLBACK_HOST", "0.0.0.0"), _patch_httpx(
        _FakeResponse(200, body)
    ):
        await login_anthropic(callbacks)

    assert captured.get("host") == "0.0.0.0"
    assert captured.get("port") == 53692
    assert captured.get("path") == "/callback"
