"""Sprint 6a (Phase 4.1, §B) — Anthropic adapter stream-event translation tests.

Uses a mock SDK client (via ``options.client``) to verify the case-by-case
event translation in ``_translate_event`` (Pi
``providers/anthropic.ts:506-660``).

The mock SDK exposes ``messages.stream(**params)`` returning an async
context manager whose body iterates over a scripted list of typed
Anthropic events. The adapter MUST project each event onto the matching
Aelix variant.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from aelix_ai.providers.anthropic import _AuthError, stream_anthropic
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

# === Mock SDK events (mirror anthropic SDK's typed events) ===


@dataclass
class _MockTextBlock:
    text: str = ""
    type: str = "text"


@dataclass
class _MockToolUseBlock:
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)
    type: str = "tool_use"


@dataclass
class _MockThinkingBlock:
    thinking: str = ""
    type: str = "thinking"


@dataclass
class _MockContentBlockStart:
    index: int = 0
    content_block: Any = None
    type: str = "content_block_start"


@dataclass
class _MockTextDelta:
    text: str = ""
    type: str = "text_delta"


@dataclass
class _MockInputJsonDelta:
    partial_json: str = ""
    type: str = "input_json_delta"


@dataclass
class _MockThinkingDelta:
    thinking: str = ""
    type: str = "thinking_delta"


@dataclass
class _MockContentBlockDelta:
    index: int = 0
    delta: Any = None
    type: str = "content_block_delta"


@dataclass
class _MockContentBlockStop:
    index: int = 0
    type: str = "content_block_stop"


@dataclass
class _MockFinalMessage:
    stop_reason: str = "end_turn"


class _MockStream:
    """Async context manager exposing a scripted event stream."""

    def __init__(
        self,
        events: list[Any],
        final_stop_reason: str = "end_turn",
        response: Any = None,
    ) -> None:
        self._events = events
        self._final_stop_reason = final_stop_reason
        self.response = response

    async def __aenter__(self) -> _MockStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def __aiter__(self) -> AsyncIterator[Any]:
        for ev in self._events:
            yield ev

    async def get_final_message(self) -> _MockFinalMessage:
        return _MockFinalMessage(stop_reason=self._final_stop_reason)


@dataclass
class _MockResponse:
    status_code: int = 200
    headers: dict = field(default_factory=lambda: {"x-test": "ok"})


class _MockMessages:
    def __init__(self, stream: _MockStream) -> None:
        self._stream = stream

    def stream(self, **_params: Any) -> _MockStream:
        return self._stream


class _MockAnthropicClient:
    def __init__(self, stream: _MockStream) -> None:
        self.messages = _MockMessages(stream)


def _make_options(stream: _MockStream, api_key: str = "sk-test") -> SimpleStreamOptions:
    return SimpleStreamOptions(
        api_key=api_key,
        client=_MockAnthropicClient(stream),
    )


def _model() -> Model:
    return Model(api="anthropic-messages", id="claude-3-test", provider="anthropic")


async def _collect(it: AsyncIterator[Any]) -> list[Any]:
    return [ev async for ev in it]


# === Tests ===


async def test_start_event_yielded_first() -> None:
    stream = _MockStream(events=[], response=_MockResponse())
    events = await _collect(
        stream_anthropic(_model(), Context(), _make_options(stream))
    )
    assert isinstance(events[0], AssistantStartEvent)


async def test_text_block_emits_start_delta_end() -> None:
    events = [
        _MockContentBlockStart(index=0, content_block=_MockTextBlock()),
        _MockContentBlockDelta(index=0, delta=_MockTextDelta(text="hi")),
        _MockContentBlockStop(index=0),
    ]
    stream = _MockStream(events=events, response=_MockResponse())
    out = await _collect(
        stream_anthropic(_model(), Context(), _make_options(stream))
    )
    types = [ev.type for ev in out]
    assert "text_start" in types
    assert "text_delta" in types
    assert "text_end" in types
    delta_ev = next(ev for ev in out if isinstance(ev, TextDeltaEvent))
    assert delta_ev.delta == "hi"
    end_ev = next(ev for ev in out if isinstance(ev, TextEndEvent))
    assert end_ev.content == "hi"


async def test_thinking_block_emits_thinking_events() -> None:
    events = [
        _MockContentBlockStart(index=0, content_block=_MockThinkingBlock()),
        _MockContentBlockDelta(index=0, delta=_MockThinkingDelta(thinking="ponder")),
        _MockContentBlockStop(index=0),
    ]
    stream = _MockStream(events=events, response=_MockResponse())
    out = await _collect(
        stream_anthropic(_model(), Context(), _make_options(stream))
    )
    types = [ev.type for ev in out]
    assert "thinking_start" in types
    assert "thinking_delta" in types
    assert "thinking_end" in types


async def test_tool_use_block_emits_toolcall_events() -> None:
    events = [
        _MockContentBlockStart(
            index=0,
            content_block=_MockToolUseBlock(id="t1", name="echo", input={}),
        ),
        _MockContentBlockDelta(
            index=0, delta=_MockInputJsonDelta(partial_json='{"x":1}')
        ),
        _MockContentBlockStop(index=0),
    ]
    stream = _MockStream(
        events=events, response=_MockResponse(), final_stop_reason="tool_use"
    )
    out = await _collect(
        stream_anthropic(_model(), Context(), _make_options(stream))
    )
    types = [ev.type for ev in out]
    assert "toolcall_start" in types
    assert "toolcall_delta" in types
    assert "toolcall_end" in types
    end_ev = next(ev for ev in out if isinstance(ev, ToolCallEndEvent))
    assert end_ev.tool_call.tool_name == "echo"
    assert end_ev.tool_call.input == {"x": 1}


async def test_done_event_terminates_stream_success() -> None:
    stream = _MockStream(events=[], response=_MockResponse(), final_stop_reason="end_turn")
    out = await _collect(
        stream_anthropic(_model(), Context(), _make_options(stream))
    )
    assert isinstance(out[-1], AssistantDoneEvent)
    assert out[-1].reason in ("stop", "length", "toolUse")


async def test_tool_use_stop_reason_maps_to_done_toolUse() -> None:
    """When the final stop_reason is ``tool_use``, ``done.reason == "toolUse"``."""

    events = [
        _MockContentBlockStart(
            index=0,
            content_block=_MockToolUseBlock(id="t", name="echo"),
        ),
        _MockContentBlockStop(index=0),
    ]
    stream = _MockStream(
        events=events, response=_MockResponse(), final_stop_reason="tool_use"
    )
    out = await _collect(
        stream_anthropic(_model(), Context(), _make_options(stream))
    )
    done = next(ev for ev in out if isinstance(ev, AssistantDoneEvent))
    assert done.reason == "toolUse"


async def test_max_tokens_maps_to_done_length() -> None:
    stream = _MockStream(events=[], response=_MockResponse(), final_stop_reason="max_tokens")
    out = await _collect(
        stream_anthropic(_model(), Context(), _make_options(stream))
    )
    done = next(ev for ev in out if isinstance(ev, AssistantDoneEvent))
    assert done.reason == "length"


async def test_oauth_token_raises_auth_error() -> None:
    """``sk-ant-oat`` tokens raise ``_AuthError`` (wrapped upstream)."""

    stream = _MockStream(events=[], response=_MockResponse())
    with pytest.raises(_AuthError):
        it = stream_anthropic(
            _model(),
            Context(),
            _make_options(stream, api_key="sk-ant-oat-stub"),
        )
        async for _ in it:
            pass


async def test_aborted_signal_yields_error_event() -> None:
    """Adapter handles abort via ``opts.signal.aborted``."""

    class _Signal:
        aborted = True

    # Use a stream that will raise during iteration so the except branch fires.
    class _BadStream(_MockStream):
        async def __aiter__(self) -> AsyncIterator[Any]:
            raise RuntimeError("stream broke")
            yield  # pragma: no cover

    stream = _BadStream(events=[], response=_MockResponse())
    options = SimpleStreamOptions(
        api_key="sk-test",
        client=_MockAnthropicClient(stream),
        signal=_Signal(),
    )
    out = await _collect(stream_anthropic(_model(), Context(), options))
    err = next(ev for ev in out if isinstance(ev, AssistantErrorEvent))
    assert err.reason == "aborted"


async def test_provider_response_callback_fires() -> None:
    """``on_response`` is invoked with status + headers."""

    seen: list[Any] = []

    async def on_response(resp: Any, _model: Model) -> None:
        seen.append(resp)

    stream = _MockStream(events=[], response=_MockResponse(status_code=200, headers={"a": "b"}))
    opts = SimpleStreamOptions(
        api_key="sk-test",
        client=_MockAnthropicClient(stream),
        on_response=on_response,
    )
    await _collect(stream_anthropic(_model(), Context(), opts))
    assert len(seen) == 1
    assert seen[0].status == 200
    assert seen[0].headers.get("a") == "b"


async def test_payload_callback_fires() -> None:
    """``on_payload`` receives the SDK params dict and may replace it."""

    captured: list[Any] = []

    async def on_payload(params: dict, _model: Model) -> dict:
        captured.append(params)
        params["max_tokens"] = 999
        return params

    stream = _MockStream(events=[], response=_MockResponse())
    opts = SimpleStreamOptions(
        api_key="sk-test",
        client=_MockAnthropicClient(stream),
        on_payload=on_payload,
    )
    await _collect(stream_anthropic(_model(), Context(), opts))
    assert captured and captured[0].get("model") == "claude-3-test"


async def test_error_during_stream_yields_error_event() -> None:
    """A non-abort exception surfaces as ``AssistantErrorEvent(reason="error")``."""

    class _BadStream(_MockStream):
        async def __aiter__(self) -> AsyncIterator[Any]:
            raise RuntimeError("network failure")
            yield  # pragma: no cover

    stream = _BadStream(events=[], response=_MockResponse())
    opts = SimpleStreamOptions(
        api_key="sk-test", client=_MockAnthropicClient(stream)
    )
    out = await _collect(stream_anthropic(_model(), Context(), opts))
    err = next(ev for ev in out if isinstance(ev, AssistantErrorEvent))
    assert err.reason == "error"
    assert "network failure" in (err.error_message or "")


async def test_multiple_text_blocks_share_content_index() -> None:
    """Two text blocks at indices 0 and 1 both project text events."""

    events = [
        _MockContentBlockStart(index=0, content_block=_MockTextBlock()),
        _MockContentBlockDelta(index=0, delta=_MockTextDelta(text="a")),
        _MockContentBlockStop(index=0),
        _MockContentBlockStart(index=1, content_block=_MockTextBlock()),
        _MockContentBlockDelta(index=1, delta=_MockTextDelta(text="b")),
        _MockContentBlockStop(index=1),
    ]
    stream = _MockStream(events=events, response=_MockResponse())
    out = await _collect(
        stream_anthropic(_model(), Context(), _make_options(stream))
    )
    starts = [ev for ev in out if isinstance(ev, TextStartEvent)]
    assert len(starts) == 2
    assert starts[0].content_index == 0
    assert starts[1].content_index == 1


async def test_tool_call_delta_uses_toolcall_delta_spelling() -> None:
    """P-39d: ``ToolCallDeltaEvent.type == "toolcall_delta"`` (no underscore)."""

    events = [
        _MockContentBlockStart(
            index=0, content_block=_MockToolUseBlock(id="t", name="echo")
        ),
        _MockContentBlockDelta(
            index=0, delta=_MockInputJsonDelta(partial_json='{"x":1}')
        ),
        _MockContentBlockStop(index=0),
    ]
    stream = _MockStream(events=events, response=_MockResponse(), final_stop_reason="tool_use")
    out = await _collect(
        stream_anthropic(_model(), Context(), _make_options(stream))
    )
    delta = next(ev for ev in out if isinstance(ev, ToolCallDeltaEvent))
    assert delta.type == "toolcall_delta"


async def test_text_start_carries_index() -> None:
    events = [
        _MockContentBlockStart(index=3, content_block=_MockTextBlock()),
        _MockContentBlockStop(index=3),
    ]
    stream = _MockStream(events=events, response=_MockResponse())
    out = await _collect(
        stream_anthropic(_model(), Context(), _make_options(stream))
    )
    starts = [ev for ev in out if isinstance(ev, TextStartEvent)]
    assert starts and starts[0].content_index == 3


async def test_toolcall_start_carries_index() -> None:
    events = [
        _MockContentBlockStart(
            index=2, content_block=_MockToolUseBlock(id="t", name="echo")
        ),
        _MockContentBlockStop(index=2),
    ]
    stream = _MockStream(events=events, response=_MockResponse(), final_stop_reason="tool_use")
    out = await _collect(
        stream_anthropic(_model(), Context(), _make_options(stream))
    )
    starts = [ev for ev in out if isinstance(ev, ToolCallStartEvent)]
    assert starts and starts[0].content_index == 2
