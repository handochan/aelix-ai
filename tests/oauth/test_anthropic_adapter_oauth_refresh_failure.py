"""Sprint 6c W6 (W5 P-103) — OAuth refresh failure E2E.

Scenario: the harness's ``get_api_key_and_headers`` resolved an OAuth
access token, the adapter sent it via ``Authorization: Bearer …``, but
Anthropic responds 401 (token was revoked / expired between refresh and
use). The adapter MUST surface ``_AuthError`` so ``_make_stream_fn``
can translate to ``AgentHarnessError("auth", …)``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
from aelix_ai import (
    AssistantMessageEvent,
    Context,
    Model,
    SimpleStreamOptions,
    clear_providers,
    register_provider,
)
from aelix_ai.providers.anthropic import _AuthError, stream_anthropic


@pytest.fixture(autouse=True)
def _reset_registry():
    clear_providers()
    yield
    clear_providers()


class _Stub401Stream:
    """A stub Anthropic SDK that always 401s on stream open."""

    class _Messages:
        def stream(self, **_kwargs: Any) -> Any:
            class _Mgr:
                async def __aenter__(self_inner) -> Any:
                    err = RuntimeError("oauth token revoked")
                    err.status_code = 401  # type: ignore[attr-defined]
                    raise err

                async def __aexit__(self_inner, *_a: Any) -> None:
                    return None

            return _Mgr()

    messages = _Messages()


async def test_oauth_refreshed_token_then_401_propagates_as_auth_error() -> None:
    """End-to-end: bearer-injected OAuth call returns 401 → AgentHarnessError("auth")."""

    def _stub_client(**_kwargs: Any) -> Any:
        return _Stub401Stream()

    async def adapter(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        # Patch the underlying SDK constructor; the real adapter still
        # decides on the bearer-header injection branch (P-94).
        with patch(
            "aelix_ai.providers.anthropic.create_async_client",
            side_effect=_stub_client,
        ):
            async for ev in stream_anthropic(model, context, options):
                yield ev

    register_provider("anthropic-messages", adapter)

    async def get_oauth_creds(_model: Model) -> dict[str, str]:
        # Simulates a successful AuthStorage.get_oauth_api_key refresh.
        return {"apiKey": "sk-ant-oat-FRESHLY-REFRESHED"}

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages", id="claude-3"),
            get_api_key_and_headers=get_oauth_creds,
        )
    )

    with pytest.raises(AgentHarnessError) as ei:
        await harness.prompt("hello")
    assert ei.value.code == "auth"


async def test_oauth_token_401_raises_auth_error_at_adapter_layer() -> None:
    """Direct adapter test: 401 mid-OAuth-flow surfaces as ``_AuthError``."""

    def _stub_client(**_kwargs: Any) -> Any:
        return _Stub401Stream()

    opts = SimpleStreamOptions(api_key="sk-ant-oat-AAA")
    with patch(
        "aelix_ai.providers.anthropic.create_async_client",
        side_effect=_stub_client,
    ), pytest.raises(_AuthError):
        async for _ in stream_anthropic(
            Model(api="anthropic-messages", id="claude-3"),
            Context(),
            opts,
        ):
            pass
