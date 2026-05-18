"""Sprint 6a (Phase 4.1, §F) — ``AgentHarnessError("auth", …)`` paths.

Sprint 6a adds the ``"auth"`` code (P-42b) raised by ``_make_stream_fn``
when ``get_api_key_and_headers`` fails OR returns a dict carrying
neither apiKey nor headers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
from aelix_ai import (
    AssistantDoneEvent,
    AssistantMessage,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
    clear_providers,
    register_provider,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    clear_providers()
    yield
    clear_providers()


def _register_dummy_provider() -> None:
    async def fake(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantDoneEvent(
            reason="stop", message=AssistantMessage(stop_reason="stop")
        )

    register_provider("anthropic-messages", fake)


async def test_adapter_auth_error_translates_to_harness_auth_error() -> None:
    """W6 Fix 1 regression — `_AuthError` raised by an adapter (e.g. OAuth
    token rejection in providers/anthropic.py) must be translated to
    `AgentHarnessError("auth", ...)` by `_make_stream_fn` per spec §D.2.
    """
    from aelix_ai.providers.anthropic import _AuthError

    async def auth_raising_provider(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        raise _AuthError("OAuth not supported in Sprint 6a")
        yield  # pragma: no cover - generator marker

    register_provider("anthropic-messages", auth_raising_provider)

    async def headers_only(model: Model) -> dict[str, str]:
        return {"apiKey": "sk-test", "headers": {}}

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages", id="claude-3"),
            get_api_key_and_headers=headers_only,
        )
    )

    with pytest.raises(AgentHarnessError) as ei:
        await harness.prompt("hello")
    assert ei.value.code == "auth"
    assert "OAuth" in str(ei.value)


async def test_auth_code_in_literal() -> None:
    """``"auth"`` is a recognized AgentHarnessError code (P-42 widening)."""

    err = AgentHarnessError("auth", "test")
    assert err.code == "auth"


async def test_get_api_key_and_headers_failure_raises_auth_error() -> None:
    """An exception in the auth callback wraps as ``AgentHarnessError("auth")``."""

    _register_dummy_provider()

    def bad_auth(_m: Model) -> dict:
        raise RuntimeError("kid not found")

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            get_api_key_and_headers=bad_auth,
        )
    )
    with pytest.raises(AgentHarnessError) as ei:
        await h.prompt("hi")
    assert ei.value.code == "auth"
    assert "get_api_key_and_headers failed" in str(ei.value)


async def test_get_api_key_and_headers_empty_dict_raises_auth_error() -> None:
    """A dict with neither apiKey nor headers is rejected."""

    _register_dummy_provider()

    def empty_auth(_m: Model) -> dict:
        return {}

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            get_api_key_and_headers=empty_auth,
        )
    )
    with pytest.raises(AgentHarnessError) as ei:
        await h.prompt("hi")
    assert ei.value.code == "auth"


async def test_no_callback_runs_without_auth() -> None:
    """When ``get_api_key_and_headers`` is omitted, no auth error fires."""

    _register_dummy_provider()
    h = AgentHarness(
        AgentHarnessOptions(model=Model(api="anthropic-messages")),
    )
    # No exception expected.
    await h.prompt("hi")


async def test_callback_returning_only_headers_succeeds() -> None:
    """Pi parity (types.ts:808-811): ``headers`` without ``apiKey`` is OK."""

    _register_dummy_provider()

    def headers_only(_m: Model) -> dict:
        return {"headers": {"Authorization": "Bearer abc"}}

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            get_api_key_and_headers=headers_only,
        )
    )
    # Should not raise — auth dict carries headers.
    await h.prompt("hi")


# === W4 M3 — SDK 401 → AgentHarnessError("auth") ===


async def test_sdk_401_translates_to_harness_auth_error() -> None:
    """W4 M3: a 401 raised by the SDK INSIDE the real Sprint 6c
    Anthropic adapter (not a stub provider raising ``_AuthError``
    manually) must end up as ``AgentHarnessError("auth", ...)``.

    This exercises the actual Sprint 6c trigger (``except Exception``
    + ``status_code in (401, 403)``) end-to-end, not the eager-raise
    fallback the original W6 Fix 1 test covers.
    """

    from unittest.mock import patch as _patch

    from aelix_ai.providers.anthropic import stream_anthropic

    class _Stub401:
        class _Messages:
            def stream(self, **_k: object) -> object:
                class _Mgr:
                    async def __aenter__(self_inner) -> object:
                        err = RuntimeError("Unauthorized")
                        err.status_code = 401  # type: ignore[attr-defined]
                        raise err

                    async def __aexit__(self_inner, *_a: object) -> None:
                        return None

                return _Mgr()

        messages = _Messages()

    async def adapter_with_stubbed_sdk(
        model: Model,
        context: object,
        options: SimpleStreamOptions,
    ):
        with _patch(
            "aelix_ai.providers.anthropic.create_async_client",
            return_value=_Stub401(),
        ):
            async for ev in stream_anthropic(model, context, options):
                yield ev

    register_provider("anthropic-messages", adapter_with_stubbed_sdk)

    async def auth_cb(_m: Model) -> dict[str, str]:
        return {"apiKey": "sk-something"}

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages", id="claude-3"),
            get_api_key_and_headers=auth_cb,
        )
    )

    with pytest.raises(AgentHarnessError) as ei:
        await harness.prompt("hello")
    assert ei.value.code == "auth"
