"""Sprint 6b (Phase 4.2, §G.2) — OpenAI adapter ↔ harness emit-site tests.

Verifies the OpenAI adapter wires Pi's three provider lifecycle events
through the harness's ``_make_stream_fn`` exactly the same way the
Anthropic adapter does in Sprint 6a:

- ``before_provider_request`` fires before the adapter starts.
- ``before_provider_payload`` receives the params dict and may patch it.
- ``after_provider_response`` receives status + headers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    AfterProviderResponseHookEvent,
    BeforeProviderPayloadHookEvent,
    BeforeProviderPayloadResult,
    BeforeProviderRequestHookEvent,
)
from aelix_ai import (
    Model,
    clear_providers,
)
from aelix_ai.providers.openai_completions import (
    OPENAI_COMPLETIONS_PROVIDER,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    clear_providers()
    yield
    clear_providers()


# === Fake SDK shapes (mirror tests/providers/test_openai_completions_streaming.py) ===


@dataclass
class _Delta:
    content: str | None = None
    tool_calls: list | None = None


@dataclass
class _Choice:
    delta: _Delta
    finish_reason: str | None = None


@dataclass
class _Chunk:
    id: str = "chatcmpl-x"
    model: str = "gpt-4"
    choices: list = field(default_factory=list)
    usage: Any = None


@dataclass
class _RawResponse:
    status_code: int = 201
    headers: dict = field(default_factory=lambda: {"x-rate-limit": "30"})


class _AsyncIter:
    def __init__(self, chunks: list, response: _RawResponse) -> None:
        self._chunks = chunks
        self.response = response

    def __aiter__(self) -> _AsyncIter:
        self._i = 0
        return self

    async def __anext__(self) -> _Chunk:
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._i]
        self._i += 1
        return c


class _RawWrapper:
    def __init__(self, iterator: _AsyncIter, response: _RawResponse) -> None:
        self._iterator = iterator
        self.http_response = response

    def parse(self) -> _AsyncIter:
        return self._iterator


class _WithRawResponse:
    def __init__(
        self, iterator: _AsyncIter, response: _RawResponse, captured: dict
    ) -> None:
        self._iterator = iterator
        self._response = response
        self.captured = captured

    async def create(self, **kwargs: Any) -> _RawWrapper:
        self.captured["params"] = kwargs
        return _RawWrapper(self._iterator, self._response)


class _Completions:
    def __init__(
        self, iterator: _AsyncIter, response: _RawResponse, captured: dict
    ) -> None:
        self.with_raw_response = _WithRawResponse(iterator, response, captured)


class _Chat:
    def __init__(
        self, iterator: _AsyncIter, response: _RawResponse, captured: dict
    ) -> None:
        self.completions = _Completions(iterator, response, captured)


class _FakeAsyncOpenAI:
    def __init__(self) -> None:
        self.captured: dict = {}
        chunks = [
            _Chunk(choices=[_Choice(delta=_Delta(content="hi"))]),
            _Chunk(choices=[_Choice(delta=_Delta(), finish_reason="stop")]),
        ]
        response = _RawResponse()
        self.chat = _Chat(_AsyncIter(chunks, response), response, self.captured)


def _register_with_injected_client(client: _FakeAsyncOpenAI) -> None:
    """Register a thin wrapper provider that injects ``options.client``.

    The harness's ``_make_stream_fn`` builds a fresh
    :class:`SimpleStreamOptions` with no ``client`` field, so we register
    a wrapper that swaps the client in before delegating to the real
    adapter.
    """

    from aelix_ai.api_registry import register_provider
    from aelix_ai.providers.openai_completions import stream_openai_completions
    from aelix_ai.streaming import (
        AssistantMessageEvent,
        Context,
        SimpleStreamOptions,
    )

    async def adapter(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        from dataclasses import replace as _replace

        patched = _replace(options, client=client, api_key=options.api_key or "k")
        async for ev in stream_openai_completions(model, context, patched):
            yield ev

    register_provider("openai-completions", adapter)


async def test_before_provider_request_fires() -> None:
    client = _FakeAsyncOpenAI()
    _register_with_injected_client(client)

    seen: list[BeforeProviderRequestHookEvent] = []
    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="openai-completions", id="gpt-4", provider="openai"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )

    def handler(event: BeforeProviderRequestHookEvent, _ctx: Any) -> None:
        seen.append(event)
        return None

    h.hooks.on("before_provider_request", handler)
    await h.prompt("hello")
    assert len(seen) == 1


async def test_before_provider_payload_receives_params_dict() -> None:
    client = _FakeAsyncOpenAI()
    _register_with_injected_client(client)

    seen: list[Any] = []
    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="openai-completions", id="gpt-4", provider="openai"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )

    def handler(event: BeforeProviderPayloadHookEvent, _ctx: Any) -> None:
        seen.append(event.payload)
        return None

    h.hooks.on("before_provider_payload", handler)
    await h.prompt("hello")
    assert len(seen) == 1
    payload = seen[0]
    # Pi parity: payload is the params dict the adapter built (model id,
    # messages, stream flag).
    assert isinstance(payload, dict)
    assert payload.get("model") == "gpt-4"
    assert payload.get("stream") is True


async def test_before_provider_payload_mutation_propagates_to_sdk() -> None:
    client = _FakeAsyncOpenAI()
    _register_with_injected_client(client)

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="openai-completions", id="gpt-4", provider="openai"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )

    def patcher(
        event: BeforeProviderPayloadHookEvent, _ctx: Any
    ) -> BeforeProviderPayloadResult:
        patched = dict(event.payload)
        patched["temperature"] = 0.42
        return BeforeProviderPayloadResult(payload=patched)

    h.hooks.on("before_provider_payload", patcher)
    await h.prompt("hello")

    sdk_params = client.captured.get("params") or {}
    assert sdk_params.get("temperature") == 0.42


async def test_after_provider_response_carries_status_and_headers() -> None:
    client = _FakeAsyncOpenAI()
    _register_with_injected_client(client)

    seen: list[AfterProviderResponseHookEvent] = []
    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="openai-completions", id="gpt-4", provider="openai"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )

    def handler(event: AfterProviderResponseHookEvent, _ctx: Any) -> None:
        seen.append(event)
        return None

    h.hooks.on("after_provider_response", handler)
    await h.prompt("hello")

    assert len(seen) == 1
    assert seen[0].status == 201
    assert seen[0].headers.get("x-rate-limit") == "30"


def test_provider_object_api_field_pi_parity() -> None:
    """Confirm the registered provider's ``api`` matches Pi's KnownApi."""

    assert OPENAI_COMPLETIONS_PROVIDER.api == "openai-completions"
