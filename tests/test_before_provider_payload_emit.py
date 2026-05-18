"""Sprint 6a (Phase 4.1, §D) — ``before_provider_payload`` emit-site tests.

Wired via ``SimpleStreamOptions.on_payload`` callback in
``_make_stream_fn``. The adapter calls ``on_payload(params, model)``
before the actual SDK send; a handler may return a replacement payload.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    BeforeProviderPayloadHookEvent,
    BeforeProviderPayloadResult,
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


def _register_payload_capturing_provider(
    sample_payload: dict | None = None,
) -> dict[str, Any]:
    """Register a provider that fires ``on_payload`` and records the response."""

    state: dict[str, Any] = {"final_payload": None}
    payload = sample_payload if sample_payload is not None else {"messages": [], "max_tokens": 1024}

    async def fake(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        if options.on_payload is not None:
            patched = await options.on_payload(dict(payload), model)
            if patched is not None:
                state["final_payload"] = patched
            else:
                state["final_payload"] = payload
        else:
            state["final_payload"] = payload
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantDoneEvent(
            reason="stop", message=AssistantMessage(stop_reason="stop")
        )

    register_provider("anthropic-messages", fake)
    return state


async def test_before_provider_payload_emits() -> None:
    """The harness invokes ``on_payload`` per turn."""

    state = _register_payload_capturing_provider({"hi": "there"})
    seen: list[Any] = []

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )

    def handler(event: BeforeProviderPayloadHookEvent, _ctx: Any) -> None:
        seen.append(event.payload)
        return None

    h.hooks.on("before_provider_payload", handler)
    await h.prompt("hi")

    assert seen == [{"hi": "there"}]
    assert state["final_payload"] == {"hi": "there"}


async def test_before_provider_payload_chains_replacement() -> None:
    """Handler-returned payload replaces the original."""

    state = _register_payload_capturing_provider({"orig": True})

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )

    def patcher(
        event: BeforeProviderPayloadHookEvent, _ctx: Any
    ) -> BeforeProviderPayloadResult:
        return BeforeProviderPayloadResult(payload={"patched": True})

    h.hooks.on("before_provider_payload", patcher)
    await h.prompt("hi")
    assert state["final_payload"] == {"patched": True}


async def test_before_provider_payload_unchanged_when_no_handler() -> None:
    """Without handlers, ``on_payload`` returns the original payload."""

    state = _register_payload_capturing_provider({"raw": 1})
    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )
    await h.prompt("hi")
    assert state["final_payload"] == {"raw": 1}


async def test_before_provider_payload_carries_model() -> None:
    _register_payload_capturing_provider({"x": 1})
    seen: list[Any] = []

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages", id="m-1"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )

    def handler(event: BeforeProviderPayloadHookEvent, _ctx: Any) -> None:
        seen.append(event.model)
        return None

    h.hooks.on("before_provider_payload", handler)
    await h.prompt("hi")
    assert seen[0] is not None
    assert seen[0].id == "m-1"


async def test_before_provider_payload_multiple_handlers_chain() -> None:
    """Multiple handlers see the chained payload (last replacement wins)."""

    state = _register_payload_capturing_provider({"v": 0})

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )

    def h1(event: BeforeProviderPayloadHookEvent, _ctx: Any) -> BeforeProviderPayloadResult:
        return BeforeProviderPayloadResult(payload={"v": 1})

    def h2(event: BeforeProviderPayloadHookEvent, _ctx: Any) -> BeforeProviderPayloadResult:
        assert event.payload == {"v": 1}
        return BeforeProviderPayloadResult(payload={"v": 2})

    h.hooks.on("before_provider_payload", h1)
    h.hooks.on("before_provider_payload", h2)
    await h.prompt("hi")
    assert state["final_payload"] == {"v": 2}
