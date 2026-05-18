"""Sprint 6c · Phase 4.3 — AuthStorage ↔ harness wire-up tests.

Verifies that ``AuthStorage.get_oauth_api_key`` can be wired into
:attr:`AgentHarnessOptions.get_api_key_and_headers` and the Anthropic
adapter receives the refreshed access token.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
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
from aelix_ai.oauth.auth_storage import AuthStorage
from aelix_ai.oauth.types import OAuthCredentials


@pytest.fixture(autouse=True)
def _reset_providers() -> None:
    clear_providers()
    yield
    clear_providers()


@pytest.fixture
def auth_path(tmp_path: Path) -> Path:
    return tmp_path / "auth.json"


async def test_auth_storage_wired_into_harness(auth_path: Path) -> None:
    """``get_oauth_api_key`` resolves OAuth → adapter sees fresh access token."""

    storage = AuthStorage(auth_path)
    creds = OAuthCredentials(
        refresh="rt-fresh",
        access="sk-ant-oat-fresh",
        expires=int(time.time() * 1000) + 3_600_000,
    )
    await storage.set_oauth("anthropic", creds)

    captured_keys: list[str | None] = []

    async def capturing_provider(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        captured_keys.append(options.api_key)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantDoneEvent(
            reason="stop", message=AssistantMessage(stop_reason="stop")
        )

    register_provider("anthropic-messages", capturing_provider)

    async def get_auth(_model: Model) -> dict:
        key = await storage.get_oauth_api_key("anthropic")
        return {"apiKey": key, "headers": {}}

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages", id="claude-3"),
            get_api_key_and_headers=get_auth,
        )
    )
    await harness.prompt("hi")

    assert captured_keys == ["sk-ant-oat-fresh"]


async def test_auth_storage_refresh_path_used_when_expired(
    auth_path: Path,
) -> None:
    """When stored token is expired, ``get_oauth_api_key`` triggers refresh."""

    storage = AuthStorage(auth_path)
    expired = OAuthCredentials(
        refresh="rt-old",
        access="sk-ant-oat-old",
        expires=int(time.time() * 1000) - 10_000,
    )
    await storage.set_oauth("anthropic", expired)

    refreshed = OAuthCredentials(
        refresh="rt-new",
        access="sk-ant-oat-refreshed",
        expires=int(time.time() * 1000) + 3_600_000,
    )

    class _StubProvider:
        id = "anthropic"

        async def refresh_token(self, c: OAuthCredentials) -> OAuthCredentials:
            return refreshed

        def get_api_key(self, c: OAuthCredentials) -> str:
            return c.access

    captured: list[str | None] = []

    async def capturing_provider(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        captured.append(options.api_key)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantDoneEvent(
            reason="stop", message=AssistantMessage(stop_reason="stop")
        )

    register_provider("anthropic-messages", capturing_provider)

    async def get_auth(_model: Model) -> dict:
        with patch(
            "aelix_ai.oauth._high_level.get_oauth_provider",
            return_value=_StubProvider(),
        ):
            key = await storage.get_oauth_api_key("anthropic")
        return {"apiKey": key, "headers": {}}

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages", id="claude-3"),
            get_api_key_and_headers=get_auth,
        )
    )
    await harness.prompt("hi")

    assert captured == ["sk-ant-oat-refreshed"]
