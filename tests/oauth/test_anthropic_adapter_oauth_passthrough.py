"""Sprint 6c · Phase 4.3 §I — Sprint 6a adapter accepts ``sk-ant-oat…`` tokens.

P-91: Sprint 6a's eager OAuth-token rejection is removed. The Anthropic
SDK accepts OAuth tokens directly (routes via Authorization: Bearer).

Sprint 6c W6 (P-94): The adapter ACTUALLY injects the OAuth bearer
header when the token starts with ``sk-ant-oat…`` (the SDK does NOT
auto-detect — it would otherwise send the token as ``x-api-key`` and
Anthropic would 401).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from aelix_ai.providers.anthropic import _AuthError, stream_anthropic
from aelix_ai.streaming import Context, Model, SimpleStreamOptions


class _StubMessages:
    """Minimal stub for the Anthropic SDK's ``client.messages.stream``."""

    def stream(self, **_kwargs: Any) -> Any:
        class _Mgr:
            async def __aenter__(self_inner) -> Any:
                class _Stream:
                    response = None

                    def __aiter__(self_inner2) -> Any:
                        return self_inner2

                    async def __anext__(self_inner2) -> Any:
                        raise StopAsyncIteration

                    async def get_final_message(self_inner2) -> Any:
                        class _M:
                            stop_reason = "end_turn"

                        return _M()

                return _Stream()

            async def __aexit__(self_inner, *_a: Any) -> None:
                return None

        return _Mgr()


class _StubClient:
    messages = _StubMessages()


def _model() -> Model:
    return Model(api="anthropic-messages", id="claude-3", provider="anthropic")


async def test_oauth_token_does_not_raise_auth_error() -> None:
    """A bare ``sk-ant-oat…`` token must NOT trigger ``_AuthError`` eagerly."""

    opts = SimpleStreamOptions(api_key="sk-ant-oat-fresh", client=_StubClient())
    events: list[Any] = []
    async for ev in stream_anthropic(_model(), Context(), opts):
        events.append(ev)
    # No _AuthError; stream completes.
    assert events


async def test_sdk_401_still_translates_to_auth_error() -> None:
    """Sprint 6c new trigger: SDK 401 surfaces as ``_AuthError``."""

    class _Stub401:
        messages = type(
            "_M",
            (),
            {
                "stream": lambda self, **_k: type(  # type: ignore[misc]
                    "_Mgr",
                    (),
                    {
                        "__aenter__": _make_aenter_raising(401),
                        "__aexit__": _aexit_noop,
                    },
                )()
            },
        )()

    opts = SimpleStreamOptions(api_key="sk-anything", client=_Stub401())
    with pytest.raises(_AuthError):
        async for _ in stream_anthropic(_model(), Context(), opts):
            pass


def _make_aenter_raising(status: int):
    async def _aenter(_self: Any) -> Any:
        err = RuntimeError(f"HTTP {status}")
        err.status_code = status  # type: ignore[attr-defined]
        raise err

    return _aenter


async def _aexit_noop(_self: Any, *_a: Any) -> None:
    return None


# === P-94 — OAuth Bearer header injection ===


async def test_oauth_token_sets_bearer_header() -> None:
    """W5 P-94: OAuth path constructs client with ``Authorization: Bearer ...``
    and empty ``api_key`` so the Anthropic SDK does NOT put the OAuth
    token in ``x-api-key`` (which would be rejected with 401).

    Captures the kwargs passed to :func:`create_async_client` and asserts:
      - ``api_key`` is blank
      - ``default_headers['Authorization']`` == ``"Bearer sk-ant-oat-…"``
      - ``default_headers['anthropic-beta']`` == ``"oauth-2025-04-20"``
    """

    captured: dict[str, Any] = {}

    def _spy(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _StubClient()

    opts = SimpleStreamOptions(api_key="sk-ant-oat-FRESH-AT")

    with patch(
        "aelix_ai.providers.anthropic.create_async_client", side_effect=_spy
    ):
        async for _ in stream_anthropic(_model(), Context(), opts):
            pass

    assert captured.get("api_key") == ""
    headers = captured.get("default_headers") or {}
    assert headers.get("Authorization") == "Bearer sk-ant-oat-FRESH-AT"
    assert headers.get("anthropic-beta") == "oauth-2025-04-20"


async def test_non_oauth_token_keeps_api_key_path() -> None:
    """Non-OAuth tokens MUST keep the legacy ``api_key=...`` codepath.

    Regression for P-94: a refactor that always used the bearer path
    would break every existing API-key user.
    """

    captured: dict[str, Any] = {}

    def _spy(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _StubClient()

    opts = SimpleStreamOptions(api_key="sk-ant-classic")

    with patch(
        "aelix_ai.providers.anthropic.create_async_client", side_effect=_spy
    ):
        async for _ in stream_anthropic(_model(), Context(), opts):
            pass

    assert captured.get("api_key") == "sk-ant-classic"
    headers = captured.get("default_headers")
    # Non-OAuth path doesn't inject Authorization.
    if headers is not None:
        assert "Authorization" not in headers


async def test_oauth_path_preserves_user_supplied_headers() -> None:
    """Caller-supplied headers must merge with the injected OAuth headers."""

    captured: dict[str, Any] = {}

    def _spy(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _StubClient()

    opts = SimpleStreamOptions(
        api_key="sk-ant-oat-XYZ",
        headers={"X-Trace-Id": "test-123"},
    )

    with patch(
        "aelix_ai.providers.anthropic.create_async_client", side_effect=_spy
    ):
        async for _ in stream_anthropic(_model(), Context(), opts):
            pass

    headers = captured.get("default_headers") or {}
    assert headers.get("X-Trace-Id") == "test-123"
    assert headers.get("Authorization") == "Bearer sk-ant-oat-XYZ"
    assert headers.get("anthropic-beta") == "oauth-2025-04-20"


async def test_oauth_path_caller_can_override_anthropic_beta() -> None:
    """``setdefault`` semantics: caller-supplied ``anthropic-beta`` wins."""

    captured: dict[str, Any] = {}

    def _spy(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _StubClient()

    opts = SimpleStreamOptions(
        api_key="sk-ant-oat-XYZ",
        headers={"anthropic-beta": "custom-beta-flag"},
    )

    with patch(
        "aelix_ai.providers.anthropic.create_async_client", side_effect=_spy
    ):
        async for _ in stream_anthropic(_model(), Context(), opts):
            pass

    headers = captured.get("default_headers") or {}
    assert headers.get("anthropic-beta") == "custom-beta-flag"
