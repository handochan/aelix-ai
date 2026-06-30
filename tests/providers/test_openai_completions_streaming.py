"""Sprint 6b (Phase 4.2, §G.1) — OpenAI completions streaming tests.

Uses a fake ``AsyncOpenAI``-shaped client (via ``options.client``) to
drive the adapter through canned SSE chunks. Verifies the case-by-case
event translation matches Pi (text / thinking / tool-call families) and
the terminal events fire in the right order.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from aelix_ai.providers.openai_completions import stream_openai_completions
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
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

# === Fake SDK shapes ===


@dataclass
class _Function:
    name: str | None = None
    arguments: str | None = None


@dataclass
class _ToolCallDelta:
    index: int = 0
    id: str | None = None
    function: _Function | None = None


@dataclass
class _Delta:
    content: str | None = None
    reasoning_content: str | None = None
    reasoning: str | None = None
    tool_calls: list[_ToolCallDelta] | None = None


@dataclass
class _Choice:
    delta: _Delta
    finish_reason: str | None = None


@dataclass
class _Chunk:
    id: str = "chatcmpl-test"
    model: str = "gpt-4"
    choices: list[_Choice] = field(default_factory=list)
    usage: Any = None


@dataclass
class _RawResponse:
    status_code: int = 200
    headers: dict = field(default_factory=lambda: {"x-test": "ok"})


class _AsyncIter:
    """Async iterator over a scripted chunk list with an attached response."""

    def __init__(
        self,
        chunks: list[_Chunk],
        response: _RawResponse | None = None,
        raise_at: int | None = None,
    ) -> None:
        self._chunks = chunks
        self.response = response or _RawResponse()
        self._raise_at = raise_at

    def __aiter__(self) -> _AsyncIter:
        self._idx = 0
        return self

    async def __anext__(self) -> _Chunk:
        if self._raise_at is not None and self._idx == self._raise_at:
            raise RuntimeError("synthetic stream error")
        if self._idx >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk


class _RawWrapper:
    """Pi parity ``with_raw_response.create`` return shape.

    The wrapper exposes ``parse()`` (the AsyncStream) and
    ``http_response`` (the underlying httpx response) so the adapter
    can surface status + headers via ``on_response``.
    """

    def __init__(self, iterator: _AsyncIter, response: _RawResponse) -> None:
        self._iterator = iterator
        self.http_response = response

    def parse(self) -> _AsyncIter:
        return self._iterator


class _WithRawResponse:
    def __init__(self, iterator: _AsyncIter, captured: dict[str, Any]) -> None:
        self._iterator = iterator
        self._captured = captured

    async def create(self, **kwargs: Any) -> _RawWrapper:
        self._captured["kwargs"] = kwargs
        self._captured["params"] = kwargs
        return _RawWrapper(self._iterator, self._iterator.response)


class _Completions:
    def __init__(self, iterator: _AsyncIter, captured: dict[str, Any]) -> None:
        self.with_raw_response = _WithRawResponse(iterator, captured)


class _Chat:
    def __init__(self, iterator: _AsyncIter, captured: dict[str, Any]) -> None:
        self.completions = _Completions(iterator, captured)


class _FakeAsyncOpenAI:
    def __init__(self, iterator: _AsyncIter) -> None:
        self.captured: dict[str, Any] = {}
        self.chat = _Chat(iterator, self.captured)


def _model() -> Model:
    return Model(
        api="openai-completions",
        id="gpt-4",
        provider="openai",
        base_url="https://api.openai.com/v1",
    )


async def _collect(it: AsyncIterator[Any]) -> list[Any]:
    return [ev async for ev in it]


# === Tests ===


async def test_text_only_response_emits_full_event_sequence() -> None:
    chunks = [
        _Chunk(choices=[_Choice(delta=_Delta(content="Hello"))]),
        _Chunk(choices=[_Choice(delta=_Delta(content=" world"))]),
        _Chunk(choices=[_Choice(delta=_Delta(), finish_reason="stop")]),
    ]
    client = _FakeAsyncOpenAI(_AsyncIter(chunks))
    out = await _collect(
        stream_openai_completions(
            _model(), Context(), SimpleStreamOptions(api_key="k", client=client)
        )
    )
    types = [ev.type for ev in out]
    assert types[0] == "start"
    assert "text_start" in types
    text_deltas = [ev for ev in out if isinstance(ev, TextDeltaEvent)]
    assert [d.delta for d in text_deltas] == ["Hello", " world"]
    assert any(isinstance(ev, TextEndEvent) for ev in out)
    assert isinstance(out[-1], AssistantDoneEvent)
    assert out[-1].reason == "stop"


async def test_thinking_block_emits_thinking_events() -> None:
    chunks = [
        _Chunk(choices=[_Choice(delta=_Delta(reasoning_content="ponder"))]),
        _Chunk(choices=[_Choice(delta=_Delta(reasoning_content="ing"))]),
        _Chunk(choices=[_Choice(delta=_Delta(), finish_reason="stop")]),
    ]
    client = _FakeAsyncOpenAI(_AsyncIter(chunks))
    out = await _collect(
        stream_openai_completions(
            _model(), Context(), SimpleStreamOptions(api_key="k", client=client)
        )
    )
    types = [ev.type for ev in out]
    assert "thinking_start" in types
    deltas = [ev for ev in out if isinstance(ev, ThinkingDeltaEvent)]
    assert [d.delta for d in deltas] == ["ponder", "ing"]
    assert any(isinstance(ev, ThinkingEndEvent) for ev in out)


async def test_tool_call_response_emits_toolcall_events() -> None:
    chunks = [
        _Chunk(
            choices=[
                _Choice(
                    delta=_Delta(
                        tool_calls=[
                            _ToolCallDelta(
                                index=0,
                                id="call_abc",
                                function=_Function(name="echo", arguments=""),
                            )
                        ]
                    )
                )
            ]
        ),
        _Chunk(
            choices=[
                _Choice(
                    delta=_Delta(
                        tool_calls=[
                            _ToolCallDelta(
                                index=0,
                                function=_Function(arguments='{"x":'),
                            )
                        ]
                    )
                )
            ]
        ),
        _Chunk(
            choices=[
                _Choice(
                    delta=_Delta(
                        tool_calls=[
                            _ToolCallDelta(
                                index=0,
                                function=_Function(arguments="1}"),
                            )
                        ]
                    )
                )
            ]
        ),
        _Chunk(choices=[_Choice(delta=_Delta(), finish_reason="tool_calls")]),
    ]
    client = _FakeAsyncOpenAI(_AsyncIter(chunks))
    out = await _collect(
        stream_openai_completions(
            _model(), Context(), SimpleStreamOptions(api_key="k", client=client)
        )
    )
    starts = [ev for ev in out if isinstance(ev, ToolCallStartEvent)]
    deltas = [ev for ev in out if isinstance(ev, ToolCallDeltaEvent)]
    ends = [ev for ev in out if isinstance(ev, ToolCallEndEvent)]
    assert len(starts) == 1
    assert len(deltas) == 3
    assert len(ends) == 1
    assert ends[0].tool_call.tool_call_id == "call_abc"
    assert ends[0].tool_call.tool_name == "echo"
    assert ends[0].tool_call.input == {"x": 1}
    done = next(ev for ev in out if isinstance(ev, AssistantDoneEvent))
    assert done.reason == "toolUse"


async def test_mixed_text_and_tool_call_events_in_order() -> None:
    chunks = [
        _Chunk(choices=[_Choice(delta=_Delta(content="Calling tool:"))]),
        _Chunk(
            choices=[
                _Choice(
                    delta=_Delta(
                        tool_calls=[
                            _ToolCallDelta(
                                index=0,
                                id="t1",
                                function=_Function(name="f", arguments="{}"),
                            )
                        ]
                    )
                )
            ]
        ),
        _Chunk(choices=[_Choice(delta=_Delta(), finish_reason="tool_calls")]),
    ]
    client = _FakeAsyncOpenAI(_AsyncIter(chunks))
    out = await _collect(
        stream_openai_completions(
            _model(), Context(), SimpleStreamOptions(api_key="k", client=client)
        )
    )
    type_seq = [ev.type for ev in out]
    # Order: start, text_start, text_delta, toolcall_start, toolcall_delta, text_end, toolcall_end, done.
    assert type_seq[0] == "start"
    assert type_seq.index("text_start") < type_seq.index("text_delta")
    assert type_seq.index("toolcall_start") < type_seq.index("toolcall_delta")
    assert type_seq[-1] == "done"


async def test_error_mid_stream_yields_error_event_no_done() -> None:
    chunks = [
        _Chunk(choices=[_Choice(delta=_Delta(content="partial"))]),
    ]
    iterator = _AsyncIter(chunks, raise_at=1)
    client = _FakeAsyncOpenAI(iterator)
    out = await _collect(
        stream_openai_completions(
            _model(), Context(), SimpleStreamOptions(api_key="k", client=client)
        )
    )
    err = next(ev for ev in out if isinstance(ev, AssistantErrorEvent))
    assert err.reason == "error"
    assert "synthetic stream error" in (err.error_message or "")
    assert not any(isinstance(ev, AssistantDoneEvent) for ev in out)


async def test_aborted_signal_yields_aborted_reason() -> None:
    class _Signal:
        aborted = True

    chunks = [_Chunk(choices=[_Choice(delta=_Delta(content="x"))])]
    iterator = _AsyncIter(chunks, raise_at=1)
    client = _FakeAsyncOpenAI(iterator)
    out = await _collect(
        stream_openai_completions(
            _model(),
            Context(),
            SimpleStreamOptions(api_key="k", client=client, signal=_Signal()),
        )
    )
    err = next(ev for ev in out if isinstance(ev, AssistantErrorEvent))
    assert err.reason == "aborted"


async def test_assistant_start_event_first() -> None:
    chunks = [
        _Chunk(choices=[_Choice(delta=_Delta(), finish_reason="stop")]),
    ]
    client = _FakeAsyncOpenAI(_AsyncIter(chunks))
    out = await _collect(
        stream_openai_completions(
            _model(), Context(), SimpleStreamOptions(api_key="k", client=client)
        )
    )
    assert isinstance(out[0], AssistantStartEvent)


async def test_text_start_carries_content_index() -> None:
    chunks = [
        _Chunk(choices=[_Choice(delta=_Delta(content="a"))]),
        _Chunk(choices=[_Choice(delta=_Delta(), finish_reason="stop")]),
    ]
    client = _FakeAsyncOpenAI(_AsyncIter(chunks))
    out = await _collect(
        stream_openai_completions(
            _model(), Context(), SimpleStreamOptions(api_key="k", client=client)
        )
    )
    start = next(ev for ev in out if isinstance(ev, TextStartEvent))
    assert start.content_index == 0


async def test_thinking_start_carries_content_index() -> None:
    chunks = [
        _Chunk(choices=[_Choice(delta=_Delta(reasoning_content="x"))]),
        _Chunk(choices=[_Choice(delta=_Delta(), finish_reason="stop")]),
    ]
    client = _FakeAsyncOpenAI(_AsyncIter(chunks))
    out = await _collect(
        stream_openai_completions(
            _model(), Context(), SimpleStreamOptions(api_key="k", client=client)
        )
    )
    start = next(ev for ev in out if isinstance(ev, ThinkingStartEvent))
    # Thinking blocks don't occupy output_content (Aelix union limitation)
    # — content_index reflects the position they *would* take, which is 0.
    assert start.content_index == 0


async def test_done_message_carries_stop_reason() -> None:
    chunks = [
        _Chunk(choices=[_Choice(delta=_Delta(content="ok"))]),
        _Chunk(choices=[_Choice(delta=_Delta(), finish_reason="stop")]),
    ]
    client = _FakeAsyncOpenAI(_AsyncIter(chunks))
    out = await _collect(
        stream_openai_completions(
            _model(), Context(), SimpleStreamOptions(api_key="k", client=client)
        )
    )
    done = next(ev for ev in out if isinstance(ev, AssistantDoneEvent))
    assert done.message.stop_reason == "stop"


async def test_no_finish_reason_triggers_error() -> None:
    chunks = [_Chunk(choices=[_Choice(delta=_Delta(content="x"))])]
    client = _FakeAsyncOpenAI(_AsyncIter(chunks))
    out = await _collect(
        stream_openai_completions(
            _model(), Context(), SimpleStreamOptions(api_key="k", client=client)
        )
    )
    assert any(isinstance(ev, AssistantErrorEvent) for ev in out)


# === #36: explicit ``model.compat`` metadata drives headers / tool-fields ===
#
# A user-defined custom provider (provider name + base_url that match NO
# built-in substring heuristic) must still get the right session-affinity
# headers and tool-field handling purely from explicit ``model.compat``.
# Pi parity: ``createClient`` / ``convertTools`` (openai-completions.ts).


@dataclass
class _FakeTool:
    name: str = "search"
    description: str = "search the web"
    parameters: dict = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )


def _custom_model(
    compat: dict | None, headers: dict | None = None
) -> Model:
    """Provider name + base_url that match NO built-in detection heuristic."""
    return Model(
        api="openai-completions",
        id="custom-model",
        provider="my-private-llm",
        base_url="https://llm.internal.example/v1",
        compat=compat,
        headers=headers,
    )


def _patch_create_client(
    monkeypatch: Any, chunks: list[_Chunk], captured: dict[str, Any]
) -> None:
    """Capture the ``default_headers`` the adapter hands to the real client."""

    def _fake(**kwargs: Any) -> _FakeAsyncOpenAI:
        captured["default_headers"] = kwargs.get("default_headers")
        return _FakeAsyncOpenAI(_AsyncIter(chunks))

    monkeypatch.setattr(
        "aelix_ai.providers.openai_completions.create_async_client", _fake
    )


async def test_custom_compat_session_affinity_headers_injected(
    monkeypatch: Any,
) -> None:
    """Explicit ``sendSessionAffinityHeaders`` injects the trio for an unknown
    provider name. Pi parity: ``createClient`` (openai-completions.ts:521-524)."""
    chunks = [
        _Chunk(choices=[_Choice(delta=_Delta(content="hi"), finish_reason="stop")])
    ]
    captured: dict[str, Any] = {}
    _patch_create_client(monkeypatch, chunks, captured)
    out = await _collect(
        stream_openai_completions(
            _custom_model({"sendSessionAffinityHeaders": True}),
            Context(),
            SimpleStreamOptions(api_key="k", session_id="sess-123"),
        )
    )
    assert any(isinstance(ev, AssistantDoneEvent) for ev in out)
    headers = captured["default_headers"]
    assert headers["session_id"] == "sess-123"
    assert headers["x-client-request-id"] == "sess-123"
    assert headers["x-session-affinity"] == "sess-123"


async def test_no_session_affinity_headers_without_compat(
    monkeypatch: Any,
) -> None:
    """Default compat (no metadata, unknown provider) ships no affinity headers."""
    chunks = [
        _Chunk(choices=[_Choice(delta=_Delta(content="hi"), finish_reason="stop")])
    ]
    captured: dict[str, Any] = {}
    _patch_create_client(monkeypatch, chunks, captured)
    await _collect(
        stream_openai_completions(
            _custom_model(None),
            Context(),
            SimpleStreamOptions(api_key="k", session_id="sess-123"),
        )
    )
    headers = captured["default_headers"] or {}
    assert "session_id" not in headers
    assert "x-session-affinity" not in headers
    assert "x-client-request-id" not in headers


async def test_session_affinity_suppressed_when_cache_retention_none(
    monkeypatch: Any,
) -> None:
    """Pi gates affinity headers behind caching: retention ``"none"`` →
    ``cacheSessionId`` undefined (openai-completions.ts:181), so no headers
    even with the compat flag on."""
    chunks = [
        _Chunk(choices=[_Choice(delta=_Delta(content="hi"), finish_reason="stop")])
    ]
    captured: dict[str, Any] = {}
    _patch_create_client(monkeypatch, chunks, captured)
    await _collect(
        stream_openai_completions(
            _custom_model({"sendSessionAffinityHeaders": True}),
            Context(),
            SimpleStreamOptions(
                api_key="k", session_id="sess-123", cache_retention="none"
            ),
        )
    )
    headers = captured["default_headers"] or {}
    assert "session_id" not in headers


async def test_model_headers_flow_through_options_headers_override(
    monkeypatch: Any,
) -> None:
    """``model.headers`` seed client headers; ``options.headers`` win on
    collision. Pi parity: ``createClient`` header merge order."""
    chunks = [
        _Chunk(choices=[_Choice(delta=_Delta(content="hi"), finish_reason="stop")])
    ]
    captured: dict[str, Any] = {}
    _patch_create_client(monkeypatch, chunks, captured)
    model = _custom_model(None, headers={"x-org": "acme", "x-shared": "from-model"})
    await _collect(
        stream_openai_completions(
            model,
            Context(),
            SimpleStreamOptions(
                api_key="k", headers={"x-shared": "from-options", "x-extra": "1"}
            ),
        )
    )
    headers = captured["default_headers"]
    assert headers["x-org"] == "acme"
    assert headers["x-shared"] == "from-options"
    assert headers["x-extra"] == "1"


async def test_custom_compat_strict_mode_false_omits_tool_strict() -> None:
    """Explicit ``supportsStrictMode: false`` omits ``strict`` from tool defs
    for an unknown provider name (tool-field handling without name heuristics)."""
    chunks = [
        _Chunk(choices=[_Choice(delta=_Delta(content="ok"), finish_reason="stop")])
    ]
    client = _FakeAsyncOpenAI(_AsyncIter(chunks))
    await _collect(
        stream_openai_completions(
            _custom_model({"supportsStrictMode": False}),
            Context(tools=[_FakeTool()]),
            SimpleStreamOptions(api_key="k", client=client),
        )
    )
    tools = client.captured["params"]["tools"]
    assert tools and "strict" not in tools[0]["function"]


async def test_custom_compat_strict_mode_default_keeps_tool_strict() -> None:
    """Unknown provider with no compat keeps the default ``strict: false``."""
    chunks = [
        _Chunk(choices=[_Choice(delta=_Delta(content="ok"), finish_reason="stop")])
    ]
    client = _FakeAsyncOpenAI(_AsyncIter(chunks))
    await _collect(
        stream_openai_completions(
            _custom_model(None),
            Context(tools=[_FakeTool()]),
            SimpleStreamOptions(api_key="k", client=client),
        )
    )
    tools = client.captured["params"]["tools"]
    assert tools[0]["function"]["strict"] is False
