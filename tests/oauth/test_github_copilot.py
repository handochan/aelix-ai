"""Sprint 6e · Phase 4.5 — GitHub Copilot OAuth flow tests.

Uses stub HTTP client patches so we don't actually hit
``github.com`` or ``api.github.com``. Mirrors the test style of
``test_anthropic.py``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest
from aelix_ai.oauth.github_copilot import (
    CLIENT_ID,
    COPILOT_HEADERS,
    DEFAULT_DOMAIN,
    GITHUB_COPILOT_OAUTH_ID,
    GITHUB_COPILOT_OAUTH_PROVIDER,
    INITIAL_POLL_INTERVAL_MULTIPLIER,
    SLOW_DOWN_POLL_INTERVAL_MULTIPLIER,
    _get_base_url_from_token,
    _get_urls,
    _modify_copilot_models,
    _poll_for_github_access_token,
    _start_device_flow,
    enable_all_github_copilot_models,
    enable_github_copilot_model,
    get_github_copilot_base_url,
    login_github_copilot,
    normalize_domain,
    refresh_github_copilot_token,
)
from aelix_ai.oauth.types import (
    OAuthAuthInfo,
    OAuthCredentials,
    OAuthLoginCallbacks,
    OAuthPrompt,
)

# === Constants ===


def test_pi_constants_match_spec() -> None:
    """Pi parity: github-copilot.ts:13-24 constants verbatim."""

    # CLIENT_ID is base64-decoded from ``SXYxLmI1MDdhMDhjODdlY2ZlOTg=``.
    assert CLIENT_ID == "Iv1.b507a08c87ecfe98"
    assert GITHUB_COPILOT_OAUTH_ID == "github-copilot"
    assert COPILOT_HEADERS == {
        "User-Agent": "GitHubCopilotChat/0.35.0",
        "Editor-Version": "vscode/1.107.0",
        "Editor-Plugin-Version": "copilot-chat/0.35.0",
        "Copilot-Integration-Id": "vscode-chat",
    }
    assert INITIAL_POLL_INTERVAL_MULTIPLIER == 1.2
    assert SLOW_DOWN_POLL_INTERVAL_MULTIPLIER == 1.4
    assert DEFAULT_DOMAIN == "github.com"


def test_provider_object_shape() -> None:
    assert GITHUB_COPILOT_OAUTH_PROVIDER.id == "github-copilot"
    assert GITHUB_COPILOT_OAUTH_PROVIDER.name == "GitHub Copilot"
    assert GITHUB_COPILOT_OAUTH_PROVIDER.uses_callback_server is False


def test_get_api_key_returns_access_token() -> None:
    creds = OAuthCredentials(refresh="rt", access="copilot-bearer", expires=1)
    assert GITHUB_COPILOT_OAUTH_PROVIDER.get_api_key(creds) == "copilot-bearer"


# === normalize_domain ===


def test_normalize_domain_blank_returns_none() -> None:
    assert normalize_domain("") is None
    assert normalize_domain("   ") is None


def test_normalize_domain_with_scheme() -> None:
    assert normalize_domain("https://company.ghe.com") == "company.ghe.com"


def test_normalize_domain_without_scheme() -> None:
    """Pi parity: github-copilot.ts:50 — prefix ``https://`` if missing."""

    assert normalize_domain("company.ghe.com") == "company.ghe.com"


def test_normalize_domain_invalid_returns_none() -> None:
    # URL parse never raises in Python's urlparse for arbitrary strings,
    # but a totally empty hostname → None.
    assert normalize_domain("://") is None


# === _get_urls ===


def test_get_urls_default_domain() -> None:
    urls = _get_urls("github.com")
    assert urls["device_code_url"] == "https://github.com/login/device/code"
    assert (
        urls["access_token_url"] == "https://github.com/login/oauth/access_token"
    )
    assert (
        urls["copilot_token_url"]
        == "https://api.github.com/copilot_internal/v2/token"
    )


def test_get_urls_enterprise() -> None:
    urls = _get_urls("company.ghe.com")
    assert urls["device_code_url"] == "https://company.ghe.com/login/device/code"
    assert (
        urls["copilot_token_url"]
        == "https://api.company.ghe.com/copilot_internal/v2/token"
    )


# === _get_base_url_from_token ===


def test_get_base_url_from_token_parses_proxy_ep() -> None:
    """Pi parity: github-copilot.ts:74-81."""

    token = "tid=abc;exp=999;proxy-ep=proxy.individual.githubcopilot.com;sku=free"
    assert (
        _get_base_url_from_token(token)
        == "https://api.individual.githubcopilot.com"
    )


def test_get_base_url_from_token_missing_returns_none() -> None:
    assert _get_base_url_from_token("tid=abc;exp=999") is None


def test_get_github_copilot_base_url_token_priority() -> None:
    """Token's proxy-ep takes priority over enterprise fallback."""

    token = "tid=x;proxy-ep=proxy.enterprise.example.com;exp=1"
    assert (
        get_github_copilot_base_url(token=token, enterprise_domain="other.com")
        == "https://api.enterprise.example.com"
    )


def test_get_github_copilot_base_url_enterprise_fallback() -> None:
    """No token → enterprise domain fallback."""

    assert (
        get_github_copilot_base_url(enterprise_domain="ghe.example.com")
        == "https://copilot-api.ghe.example.com"
    )


def test_get_github_copilot_base_url_default() -> None:
    assert (
        get_github_copilot_base_url() == "https://api.individual.githubcopilot.com"
    )


# === Mock HTTP infrastructure ===


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self.reason_phrase = "OK" if 200 <= status_code < 300 else "Error"
        self.text = json.dumps(body)
        self._body = body

    def json(self) -> Any:
        return self._body


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient that records calls."""

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
        json: Any = None,
    ) -> _FakeResponse:
        self.calls.append(
            {"url": url, "headers": headers, "data": data, "json": json}
        )
        return self._next_response  # type: ignore[attr-defined]

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.calls.append({"url": url, "headers": headers, "method": "GET"})
        return self._next_response  # type: ignore[attr-defined]


def _patch_httpx(response: _FakeResponse) -> Any:
    fake = _FakeAsyncClient()
    fake._next_response = response  # type: ignore[attr-defined]
    return patch(
        "aelix_ai.oauth.github_copilot.httpx.AsyncClient", return_value=fake
    )


# === _start_device_flow ===


async def test_start_device_flow_happy() -> None:
    body = {
        "device_code": "DC",
        "user_code": "UC-123",
        "verification_uri": "https://github.com/login/device",
        "interval": 5,
        "expires_in": 900,
    }
    with _patch_httpx(_FakeResponse(200, body)):
        result = await _start_device_flow("github.com")
    assert result["device_code"] == "DC"
    assert result["user_code"] == "UC-123"
    assert result["interval"] == 5
    assert result["expires_in"] == 900


async def test_start_device_flow_invalid_fields_raises() -> None:
    body = {"device_code": "DC"}  # missing user_code, etc.
    with _patch_httpx(_FakeResponse(200, body)), pytest.raises(RuntimeError):
        await _start_device_flow("github.com")


async def test_start_device_flow_http_error() -> None:
    with _patch_httpx(_FakeResponse(500, {"error": "x"})), pytest.raises(
        RuntimeError
    ):
        await _start_device_flow("github.com")


# === _poll_for_github_access_token ===


class _SequencedClient:
    """httpx.AsyncClient stand-in that returns a SEQUENCE of responses."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self._idx = 0
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *_a: Any, **_kw: Any) -> _SequencedClient:
        return self

    async def __aenter__(self) -> _SequencedClient:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: Any = None,
        json: Any = None,
    ) -> _FakeResponse:
        self.calls.append({"url": url, "data": data, "method": "POST"})
        resp = self._responses[self._idx]
        self._idx = min(self._idx + 1, len(self._responses) - 1)
        return resp

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.calls.append({"url": url, "method": "GET"})
        resp = self._responses[self._idx]
        self._idx = min(self._idx + 1, len(self._responses) - 1)
        return resp


async def test_poll_handles_authorization_pending_then_success() -> None:
    """Pi parity: Sprint 6e W6 (W4 M1) — fetch fires FIRST, sleep AFTER.

    Pi: ``while (deadline) { fetch; check; sleep; continue }``. The
    Aelix port originally slept before the first fetch — adding ~6s of
    latency to every Copilot login. The ordering is now Pi-strict: the
    first fetch must happen with ZERO sleep calls beforehand.
    """

    client = _SequencedClient(
        [
            _FakeResponse(200, {"error": "authorization_pending"}),
            _FakeResponse(200, {"access_token": "GH_AT"}),
        ]
    )
    sleep_call_count = {"value": 0}
    first_fetch_at_sleep_count = {"value": -1}

    async def _track_sleep(_n: float) -> None:
        sleep_call_count["value"] += 1
        return None

    # Wrap the client.post to capture how many sleeps happened before
    # the first network call — must be 0 for Pi parity.
    orig_post = client.post

    async def _wrapped_post(*a: Any, **kw: Any) -> _FakeResponse:
        if first_fetch_at_sleep_count["value"] == -1:
            first_fetch_at_sleep_count["value"] = sleep_call_count["value"]
        return await orig_post(*a, **kw)

    client.post = _wrapped_post  # type: ignore[method-assign]

    with patch(
        "aelix_ai.oauth.github_copilot.httpx.AsyncClient", new=client
    ), patch(
        "aelix_ai.oauth.github_copilot.asyncio.sleep", side_effect=_track_sleep
    ):
        token = await _poll_for_github_access_token(
            "github.com", "DC", interval_seconds=1, expires_in=300
        )
    assert token == "GH_AT"
    assert len(client.calls) == 2
    # Pi parity: first fetch happens BEFORE any sleep call (W4 M1).
    assert first_fetch_at_sleep_count["value"] == 0
    # One sleep between the two fetches (after the pending response).
    assert sleep_call_count["value"] == 1


async def test_poll_handles_slow_down_backoff() -> None:
    """Pi parity: slow_down → SLOW_DOWN multiplier (1.4×).

    Sprint 6e W6 (W4 M1 + P-144): with the Pi-strict ``fetch → check →
    sleep`` ordering, one slow_down → 1 sleep at SLOW_DOWN multiplier
    → success. The previous shape (sleep-before-fetch) produced 2 sleeps;
    Pi-parity produces exactly 1.

    P-144 (``math.ceil``): the wait is computed by
    ``math.ceil(intervalMs * intervalMultiplier)``, matching Pi's
    ``Math.ceil``. With a 1s base interval + SLOW_DOWN multiplier of
    1.4, the wait is ``ceil(1000 * 1.4) = 1400`` ms → 1.4 seconds.
    """

    import math

    client = _SequencedClient(
        [
            _FakeResponse(200, {"error": "slow_down"}),
            _FakeResponse(200, {"access_token": "GH_AT"}),
        ]
    )
    sleep_calls: list[float] = []

    async def _track_sleep(n: float) -> None:
        sleep_calls.append(n)

    with patch(
        "aelix_ai.oauth.github_copilot.httpx.AsyncClient", new=client
    ), patch("aelix_ai.oauth.github_copilot.asyncio.sleep", side_effect=_track_sleep):
        token = await _poll_for_github_access_token(
            "github.com", "DC", interval_seconds=1, expires_in=300
        )
    assert token == "GH_AT"
    # Pi parity (W4 M1): one slow_down response → one sleep at 1.4x.
    assert len(sleep_calls) == 1
    # Pi parity (P-144): slow_down with no ``interval`` field bumps
    # ``interval_ms`` by 5000 (Pi github-copilot.ts:200-211 fallback);
    # base 1000 → 6000 → ceil(6000 * 1.4) = 8400 ms = 8.4 s.
    assert sleep_calls[0] == math.ceil(6000 * 1.4) / 1000.0


async def test_poll_raises_on_unexpected_error() -> None:
    client = _SequencedClient(
        [_FakeResponse(200, {"error": "expired_token"})]
    )

    async def _instant(_n: float) -> None:
        return None

    with patch(
        "aelix_ai.oauth.github_copilot.httpx.AsyncClient", new=client
    ), patch(
        "aelix_ai.oauth.github_copilot.asyncio.sleep", side_effect=_instant
    ), pytest.raises(RuntimeError) as ei:
        await _poll_for_github_access_token(
            "github.com", "DC", interval_seconds=1, expires_in=300
        )
    assert "Device flow failed" in str(ei.value)
    assert "expired_token" in str(ei.value)


# === refresh_github_copilot_token ===


async def test_refresh_github_copilot_token_happy() -> None:
    body = {
        "token": "tid=t;exp=999;proxy-ep=proxy.individual.githubcopilot.com",
        "expires_at": 9_999_999,
    }
    with _patch_httpx(_FakeResponse(200, body)):
        creds = await refresh_github_copilot_token("GH_AT")
    assert creds.refresh == "GH_AT"
    assert "proxy-ep" in creds.access
    # expires = expires_at*1000 - safety_margin (5 min).
    assert creds.expires == 9_999_999 * 1000 - 5 * 60 * 1000


async def test_refresh_github_copilot_token_with_enterprise() -> None:
    """Sprint 6e W6 (P-147): persisted key is ``enterpriseUrl`` (Pi camelCase)."""

    body = {"token": "X", "expires_at": 9_999_999}
    with _patch_httpx(_FakeResponse(200, body)):
        creds = await refresh_github_copilot_token(
            "GH_AT", enterprise_url="ghe.example.com"
        )
    # Pi parity: stored as ``enterpriseUrl`` (camelCase), preserving the
    # raw user input verbatim — not the prior ``enterprise_domain``.
    assert creds.extra.get("enterpriseUrl") == "ghe.example.com"
    assert creds.extra.get("enterprise_domain") is None


async def test_refresh_github_copilot_token_preserves_raw_user_input() -> None:
    """Sprint 6e W6 (P-147): raw user input (with scheme) round-trips."""

    body = {"token": "X", "expires_at": 9_999_999}
    with _patch_httpx(_FakeResponse(200, body)):
        creds = await refresh_github_copilot_token(
            "GH_AT", enterprise_url="https://ghe.example.com"
        )
    # The raw input (with scheme) is preserved byte-for-byte; the
    # normalization to hostname happens only for URL construction.
    assert creds.extra.get("enterpriseUrl") == "https://ghe.example.com"


async def test_refresh_github_copilot_token_http_error() -> None:
    with _patch_httpx(_FakeResponse(401, {"error": "x"})), pytest.raises(
        RuntimeError
    ):
        await refresh_github_copilot_token("BAD")


# === login_github_copilot (full flow) ===


async def test_login_github_copilot_default_domain() -> None:
    """Empty enterprise input → defaults to github.com."""

    device_response = _FakeResponse(
        200,
        {
            "device_code": "DC",
            "user_code": "UC",
            "verification_uri": "https://github.com/login/device",
            "interval": 1,
            "expires_in": 300,
        },
    )
    poll_response = _FakeResponse(200, {"access_token": "GH_AT"})
    refresh_response = _FakeResponse(
        200, {"token": "proxy-ep=proxy.x.y;exp=1", "expires_at": 9_999_999}
    )

    client = _SequencedClient(
        [device_response, poll_response, refresh_response]
    )

    async def _instant(_n: float) -> None:
        return None

    auth_seen: list[OAuthAuthInfo] = []

    def on_auth(info: OAuthAuthInfo) -> None:
        auth_seen.append(info)

    async def on_prompt(_p: OAuthPrompt) -> str:
        return ""  # blank → defaults to github.com

    callbacks = OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt)

    # Isolate the login flow from the post-login model-enable step (its own
    # unit tests cover it); record that it is invoked with the access token.
    enable_calls: list[tuple[str, str | None]] = []

    async def _fake_enable_all(
        token: str, enterprise_domain: str | None = None
    ) -> None:
        enable_calls.append((token, enterprise_domain))

    with patch(
        "aelix_ai.oauth.github_copilot.httpx.AsyncClient", new=client
    ), patch(
        "aelix_ai.oauth.github_copilot.asyncio.sleep", side_effect=_instant
    ), patch(
        "aelix_ai.oauth.github_copilot.enable_all_github_copilot_models",
        side_effect=_fake_enable_all,
    ):
        creds = await login_github_copilot(callbacks)

    assert "proxy-ep" in creds.access
    assert auth_seen and auth_seen[0].url == "https://github.com/login/device"
    assert "Enter code: UC" in (auth_seen[0].instructions or "")
    # Pi parity: models are enabled after login, with the resolved access
    # token and the normalized enterprise domain (None for github.com).
    assert enable_calls == [(creds.access, None)]


async def test_login_github_copilot_invalid_enterprise_raises() -> None:
    async def on_prompt(_p: OAuthPrompt) -> str:
        return "://"  # invalid URL

    def on_auth(_info: OAuthAuthInfo) -> None:
        return None

    callbacks = OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt)

    with pytest.raises(RuntimeError) as ei:
        await login_github_copilot(callbacks)
    assert "Invalid GitHub Enterprise" in str(ei.value)


# === _modify_copilot_models ===


@dataclass
class _FakeModel:
    id: str
    provider: str
    base_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def test_modify_copilot_models_injects_base_url() -> None:
    """Pi parity: github-copilot.ts:390-395 ``{...m, baseUrl}``."""

    creds = OAuthCredentials(
        refresh="rt",
        access="tid=x;proxy-ep=proxy.individual.githubcopilot.com;exp=1",
        expires=1,
    )
    models = [
        _FakeModel(id="claude-3-5", provider="github-copilot"),
        _FakeModel(id="gpt-4", provider="openai"),
    ]
    result = _modify_copilot_models(models, creds)
    # Copilot-routed: base_url injected.
    assert result[0].provider == "github-copilot"
    assert result[0].base_url == "https://api.individual.githubcopilot.com"
    # Non-Copilot: untouched.
    assert result[1].provider == "openai"
    assert result[1].base_url is None
    # Original list NOT mutated (new list returned).
    assert models[0].base_url is None


def test_modify_copilot_models_enterprise_fallback() -> None:
    """Enterprise URL in ``extra`` is normalized into base_url.

    Sprint 6e W6 (P-147): persisted key is ``enterpriseUrl`` (camelCase).
    """

    creds = OAuthCredentials(
        refresh="rt",
        access="no-proxy-ep-here",
        expires=1,
        extra={"enterpriseUrl": "ghe.example.com"},
    )
    models = [_FakeModel(id="m1", provider="github-copilot")]
    result = _modify_copilot_models(models, creds)
    assert result[0].base_url == "https://copilot-api.ghe.example.com"


def test_modify_copilot_models_non_dataclass_raises() -> None:
    """Sprint 6e W6 (P-145 / P-146): non-dataclass Copilot model raises.

    Pi unconditionally spreads ``{...m, baseUrl}``. Aelix originally
    silently passed non-dataclass models through, hiding Model misuse.
    """

    class _NonDataclassModel:
        def __init__(self) -> None:
            self.id = "bad"
            self.provider = "github-copilot"
            self.base_url = None

    creds = OAuthCredentials(
        refresh="r",
        access="tid=x;proxy-ep=proxy.individual.githubcopilot.com",
        expires=1,
    )
    with pytest.raises(TypeError) as ei:
        _modify_copilot_models([_NonDataclassModel()], creds)
    assert "dataclass" in str(ei.value).lower()


def test_modify_copilot_models_provider_callback_attr() -> None:
    """Provider exposes ``modify_models`` as a callable (Pi P-132)."""

    assert callable(GITHUB_COPILOT_OAUTH_PROVIDER.modify_models)


# === Async helper assertion (no real sleeps anywhere) ===


def test_module_imports_clean() -> None:
    """Smoke: the module imports without bind-time side effects."""

    # Just touching CLIENT_ID forces module init; if anything in the
    # base64 / regex / urllib setup was broken, this would already have
    # failed at collection.
    assert isinstance(CLIENT_ID, str)
    assert asyncio.iscoroutinefunction(login_github_copilot)


# === enable_github_copilot_model / enable_all_github_copilot_models ===


_TOKEN_WITH_PROXY = "tid=abc;exp=1;proxy-ep=proxy.foo.example"


async def test_enable_model_posts_policy_and_returns_true() -> None:
    """POSTs ``/models/{id}/policy`` with the chat-policy headers + body."""

    fake = _FakeAsyncClient()
    fake._next_response = _FakeResponse(200, {})  # type: ignore[attr-defined]
    with patch(
        "aelix_ai.oauth.github_copilot.httpx.AsyncClient", return_value=fake
    ):
        ok = await enable_github_copilot_model(_TOKEN_WITH_PROXY, "gpt-4o")

    assert ok is True
    assert len(fake.calls) == 1
    call = fake.calls[0]
    # base_url derives from the token's proxy-ep (proxy. -> api.).
    assert call["url"] == "https://api.foo.example/models/gpt-4o/policy"
    assert call["json"] == {"state": "enabled"}
    headers = call["headers"] or {}
    assert headers["Authorization"] == f"Bearer {_TOKEN_WITH_PROXY}"
    assert headers["openai-intent"] == "chat-policy"
    assert headers["x-interaction-type"] == "chat-policy"
    # The static Copilot headers ride along (User-Agent, Editor-Version, …).
    for key, value in COPILOT_HEADERS.items():
        assert headers[key] == value


async def test_enable_model_returns_false_on_non_2xx() -> None:
    """A policy rejection (e.g. account without access) is best-effort False."""

    fake = _FakeAsyncClient()
    fake._next_response = _FakeResponse(400, {"error": "nope"})  # type: ignore[attr-defined]
    with patch(
        "aelix_ai.oauth.github_copilot.httpx.AsyncClient", return_value=fake
    ):
        ok = await enable_github_copilot_model(_TOKEN_WITH_PROXY, "grok-code")
    assert ok is False


async def test_enable_model_returns_false_on_exception() -> None:
    """A transport error never raises out of the enable call."""

    with patch(
        "aelix_ai.oauth.github_copilot.httpx.AsyncClient",
        side_effect=RuntimeError("boom"),
    ):
        ok = await enable_github_copilot_model(_TOKEN_WITH_PROXY, "gpt-4o")
    assert ok is False


async def test_enable_all_enumerates_catalog_and_reports_progress() -> None:
    """Enables one policy per catalog Copilot model; reports each result."""

    class _M:
        def __init__(self, mid: str) -> None:
            self.id = mid

    fake = _FakeAsyncClient()
    fake._next_response = _FakeResponse(200, {})  # type: ignore[attr-defined]
    seen: list[tuple[str, bool]] = []

    with patch(
        "aelix_ai.models.get_models",
        return_value=[_M("gpt-4o"), _M("claude-opus-4.6"), _M("grok-code-fast-1")],
    ), patch(
        "aelix_ai.oauth.github_copilot.httpx.AsyncClient", return_value=fake
    ):
        await enable_all_github_copilot_models(
            _TOKEN_WITH_PROXY, None, on_model=lambda mid, ok: seen.append((mid, ok))
        )

    assert len(fake.calls) == 3
    posted = {c["url"] for c in fake.calls}
    assert posted == {
        "https://api.foo.example/models/gpt-4o/policy",
        "https://api.foo.example/models/claude-opus-4.6/policy",
        "https://api.foo.example/models/grok-code-fast-1/policy",
    }
    assert sorted(seen) == [
        ("claude-opus-4.6", True),
        ("gpt-4o", True),
        ("grok-code-fast-1", True),
    ]


def test_catalog_has_github_copilot_models() -> None:
    """Regression guard: the catalog must carry Copilot models, else
    :func:`enable_all_github_copilot_models` silently no-ops."""

    from aelix_ai.models import get_models

    ids = {m.id for m in get_models(GITHUB_COPILOT_OAUTH_ID)}
    assert ids, "catalog lost all github-copilot models"
    assert "gpt-4o" in ids
