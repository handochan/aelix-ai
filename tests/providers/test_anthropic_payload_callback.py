"""Sprint 6a (Phase 4.1, §B) — Anthropic adapter payload/response callback tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from aelix_ai.providers.anthropic import stream_anthropic
from aelix_ai.streaming import (
    Context,
    Model,
    SimpleStreamOptions,
)


@dataclass
class _MockFinalMessage:
    stop_reason: str = "end_turn"


@dataclass
class _MockResponse:
    status_code: int = 200
    headers: dict = field(default_factory=lambda: {"x-test": "1"})


class _MockStream:
    def __init__(self, response: Any = None) -> None:
        self.response = response

    async def __aenter__(self) -> _MockStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def __aiter__(self) -> AsyncIterator[Any]:
        return
        yield  # pragma: no cover

    async def get_final_message(self) -> _MockFinalMessage:
        return _MockFinalMessage()


class _MockMessages:
    def __init__(self, stream: _MockStream, captured: dict) -> None:
        self._stream = stream
        self._captured = captured

    def stream(self, **params: Any) -> _MockStream:
        self._captured["params"] = params
        return self._stream


class _MockAnthropicClient:
    def __init__(self, stream: _MockStream, captured: dict) -> None:
        self.messages = _MockMessages(stream, captured)


def _model() -> Model:
    return Model(api="anthropic-messages", id="claude-3-test", provider="anthropic")


async def test_on_payload_invoked_with_params() -> None:
    captured: dict = {}
    stream = _MockStream(response=_MockResponse())
    seen: list[dict] = []

    async def on_payload(params: dict, _m: Model) -> dict | None:
        seen.append(params)
        return None  # No replacement

    opts = SimpleStreamOptions(
        api_key="sk-test",
        client=_MockAnthropicClient(stream, captured),
        on_payload=on_payload,
    )
    async for _ in stream_anthropic(_model(), Context(), opts):
        pass
    assert seen
    assert seen[0].get("model") == "claude-3-test"


async def test_on_payload_replacement_propagated_to_sdk() -> None:
    captured: dict = {}
    stream = _MockStream(response=_MockResponse())

    async def on_payload(params: dict, _m: Model) -> dict:
        return {"model": "patched-model", "max_tokens": 4096, "messages": []}

    opts = SimpleStreamOptions(
        api_key="sk-test",
        client=_MockAnthropicClient(stream, captured),
        on_payload=on_payload,
    )
    async for _ in stream_anthropic(_model(), Context(), opts):
        pass
    assert captured["params"]["model"] == "patched-model"


async def test_on_response_receives_provider_response() -> None:
    captured: dict = {}
    seen: list[Any] = []
    stream = _MockStream(response=_MockResponse(status_code=204, headers={"a": "b"}))

    async def on_response(resp: Any, _m: Model) -> None:
        seen.append(resp)

    opts = SimpleStreamOptions(
        api_key="sk-test",
        client=_MockAnthropicClient(stream, captured),
        on_response=on_response,
    )
    async for _ in stream_anthropic(_model(), Context(), opts):
        pass
    assert len(seen) == 1
    assert seen[0].status == 204
    assert seen[0].headers.get("a") == "b"


async def test_no_callbacks_does_not_break_stream() -> None:
    """With both callbacks omitted the adapter still completes cleanly."""

    captured: dict = {}
    stream = _MockStream(response=_MockResponse())
    opts = SimpleStreamOptions(
        api_key="sk-test",
        client=_MockAnthropicClient(stream, captured),
    )
    events: list[Any] = []
    async for ev in stream_anthropic(_model(), Context(), opts):
        events.append(ev)
    # AssistantStart + AssistantDone at minimum.
    assert len(events) >= 2
