"""OpenAI **Codex** Responses adapter tests — #15 / Phase B §4.1 item #6.

Covers the pure helpers (:func:`resolve_codex_url`, :func:`build_codex_headers`,
:func:`build_request_body`, :func:`parse_sse_block`, :func:`map_codex_events`),
the SSE transport (chunk-boundary framing, retry-on-429, non-retryable error),
an end-to-end drive of the shared engine over a fake ``httpx`` client (text +
tool-call + thinking + usage), auth failure modes (missing token / missing
account-id claim), the sync ``stream_simple`` key-raise + reasoning clamp, and
registration (both the adapter's own ``register_all`` and the CLI
``register_providers`` making an ``openai-codex`` model runnable).
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from aelix_ai.api_registry import get_registered_providers
from aelix_ai.messages import TextContent, ThinkingContent, ToolCallContent
from aelix_ai.providers.openai_codex_responses import (
    DEFAULT_CODEX_BASE_URL,
    OPENAI_CODEX_RESPONSES_API,
    build_codex_headers,
    build_request_body,
    map_codex_events,
    parse_sse_block,
    register_all,
    resolve_codex_url,
    stream_openai_codex_responses,
    stream_simple_openai_codex_responses,
)
from aelix_ai.providers.openai_responses import OpenAIResponsesOptions
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)

# === Fixtures / factories ===


def _make_token(account_id: str = "acc-123", *, with_claim: bool = True) -> str:
    """Build a fake (unsigned) JWT carrying the chatgpt_account_id claim."""

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload: dict[str, Any] = {"exp": 9999999999}
    if with_claim:
        payload["https://api.openai.com/auth"] = {"chatgpt_account_id": account_id}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.sig"


def _model(**kw: Any) -> Model:
    base: dict[str, Any] = {
        "api": OPENAI_CODEX_RESPONSES_API,
        "id": "gpt-5.2",
        "provider": "openai-codex",
        "base_url": DEFAULT_CODEX_BASE_URL,
        "reasoning": True,
        "input": ["text", "image"],
        "thinking_level_map": {"xhigh": "xhigh", "minimal": "low"},
    }
    base.update(kw)
    return Model(**base)


class _FakeTool:
    name = "get_weather"
    description = "Get the weather"
    parameters = {"type": "object", "properties": {"city": {"type": "string"}}}


# === resolve_codex_url ===


def test_resolve_codex_url_default_host() -> None:
    assert (
        resolve_codex_url(DEFAULT_CODEX_BASE_URL)
        == "https://chatgpt.com/backend-api/codex/responses"
    )


def test_resolve_codex_url_none_falls_back_to_default() -> None:
    assert resolve_codex_url(None) == "https://chatgpt.com/backend-api/codex/responses"


def test_resolve_codex_url_trailing_slash_stripped() -> None:
    assert resolve_codex_url("https://h/api/") == "https://h/api/codex/responses"


def test_resolve_codex_url_idempotent_when_base_ends_in_codex() -> None:
    assert resolve_codex_url("https://h/api/codex") == "https://h/api/codex/responses"


# === build_codex_headers ===


def test_build_codex_headers_sets_required_fields() -> None:
    token = _make_token("acc-9")
    headers = build_codex_headers(_model(), token, "acc-9", None)
    assert headers["Authorization"] == f"Bearer {token}"
    assert headers["chatgpt-account-id"] == "acc-9"
    assert headers["OpenAI-Beta"] == "responses=experimental"
    assert headers["originator"] == "aelix"
    assert headers["accept"] == "text/event-stream"
    assert headers["content-type"] == "application/json"


def test_build_codex_headers_originator_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AELIX_CODEX_ORIGINATOR", "codex_cli_rs")
    headers = build_codex_headers(_model(), "tok", "acc", None)
    assert headers["originator"] == "codex_cli_rs"


def test_build_codex_headers_merges_model_and_option_headers() -> None:
    model = _model(headers={"x-model": "m"})
    headers = build_codex_headers(model, "tok", "acc", {"x-opt": "o"})
    assert headers["x-model"] == "m"
    assert headers["x-opt"] == "o"


def test_build_codex_headers_required_fields_win_over_stray_auth() -> None:
    # A stray upstream Authorization header must NOT clobber the codex bearer.
    headers = build_codex_headers(
        _model(), "realtok", "acc", {"Authorization": "Bearer stale"}
    )
    assert headers["Authorization"] == "Bearer realtok"


# === build_request_body ===


def test_build_request_body_fixed_fields() -> None:
    body = build_request_body(_model(), Context(system_prompt="SYS"), None)
    assert body["model"] == "gpt-5.2"
    assert body["store"] is False
    assert body["stream"] is True
    assert body["text"] == {"verbosity": "low"}
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["tool_choice"] == "auto"
    assert body["parallel_tool_calls"] is True
    assert body["instructions"] == "SYS"


def test_build_request_body_default_instructions_when_no_system_prompt() -> None:
    body = build_request_body(_model(), Context(), None)
    assert body["instructions"] == "You are a helpful assistant."


def test_build_request_body_system_prompt_not_duplicated_in_input() -> None:
    # include_system_prompt=False → the system prompt lives ONLY in instructions.
    body = build_request_body(
        _model(), Context(system_prompt="SYS", messages=[]), None
    )
    for item in body["input"]:
        if isinstance(item, dict):
            assert item.get("role") not in ("system", "developer")


def test_build_request_body_tools_carry_strict_null() -> None:
    body = build_request_body(_model(), Context(tools=[_FakeTool()]), None)
    assert body["tools"][0]["name"] == "get_weather"
    assert body["tools"][0]["strict"] is None
    assert body["tools"][0]["type"] == "function"


def test_build_request_body_prompt_cache_key_gated_on_session() -> None:
    body = build_request_body(
        _model(), Context(), OpenAIResponsesOptions(session_id="s-1")
    )
    assert body["prompt_cache_key"] == "s-1"


def test_build_request_body_no_cache_key_when_retention_none() -> None:
    body = build_request_body(
        _model(),
        Context(),
        OpenAIResponsesOptions(session_id="s-1", cache_retention="none"),
    )
    assert "prompt_cache_key" not in body


def test_build_request_body_reasoning_effort_block() -> None:
    body = build_request_body(
        _model(), Context(), OpenAIResponsesOptions(reasoning_effort="high")
    )
    assert body["reasoning"] == {"effort": "high", "summary": "auto"}


def test_build_request_body_reasoning_effort_uses_thinking_level_map() -> None:
    # gpt-5.2 maps minimal→low.
    body = build_request_body(
        _model(), Context(), OpenAIResponsesOptions(reasoning_effort="minimal")
    )
    assert body["reasoning"] == {"effort": "low", "summary": "auto"}


def test_build_request_body_reasoning_off_branch_default_none() -> None:
    # Reasoning model, no effort, no "off" mapping → effort "none".
    body = build_request_body(_model(), Context(), None)
    assert body["reasoning"] == {"effort": "none"}


def test_build_request_body_no_reasoning_for_non_reasoning_model() -> None:
    body = build_request_body(_model(reasoning=False), Context(), None)
    assert "reasoning" not in body
    # include is still unconditional for codex.
    assert body["include"] == ["reasoning.encrypted_content"]


def test_build_request_body_service_tier_forwarded() -> None:
    body = build_request_body(
        _model(), Context(), OpenAIResponsesOptions(service_tier="flex")
    )
    assert body["service_tier"] == "flex"


# === parse_sse_block ===


def test_parse_sse_block_extracts_data_json() -> None:
    assert parse_sse_block(b'event: x\ndata: {"type":"a"}') == {"type": "a"}


def test_parse_sse_block_skips_done_sentinel() -> None:
    assert parse_sse_block(b"data: [DONE]") is None


def test_parse_sse_block_skips_comment_and_keepalive() -> None:
    assert parse_sse_block(b": keep-alive") is None
    assert parse_sse_block(b"event: ping") is None


def test_parse_sse_block_multiline_data_concatenated() -> None:
    assert parse_sse_block(b'data: {"a":\ndata: 1}') == {"a": 1}


def test_parse_sse_block_invalid_json_returns_none() -> None:
    assert parse_sse_block(b"data: not json") is None


def test_parse_sse_block_non_object_json_returns_none() -> None:
    assert parse_sse_block(b"data: [1,2,3]") is None


# === map_codex_events ===


def test_map_codex_events_renames_response_done() -> None:
    out = map_codex_events({"type": "response.done", "response": {"id": "r"}})
    assert out["type"] == "response.completed"
    assert out["response"] == {"id": "r"}


def test_map_codex_events_passthrough_other_types() -> None:
    ev = {"type": "response.output_text.delta", "delta": "hi"}
    assert map_codex_events(ev) is ev


# === Fake httpx client for the SSE transport ===


def _sse(events: list[dict[str, Any]]) -> bytes:
    out = b""
    for ev in events:
        out += b"data: " + json.dumps(ev).encode() + b"\n\n"
    return out


class _FakeResponse:
    def __init__(
        self, status: int, chunks: list[bytes], headers: dict[str, str] | None = None
    ) -> None:
        self.status_code = status
        self._chunks = chunks
        self.headers = headers or {}

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aread(self) -> bytes:
        return b"".join(self._chunks)


class _FakeStreamCM:
    def __init__(self, response: _FakeResponse | BaseException) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        # An exception entry simulates a transport error at establishment.
        if isinstance(self._response, BaseException):
            raise self._response
        return self._response

    async def __aexit__(self, *_a: Any) -> bool:
        return False


class _FakeClient:
    def __init__(
        self, responses: Sequence[_FakeResponse | BaseException]
    ) -> None:
        self._responses: list[_FakeResponse | BaseException] = list(responses)
        self.calls = 0
        self.captured_url: str | None = None
        self.captured_json: Any = None
        self.captured_headers: dict[str, str] | None = None
        self.closed = False

    def stream(
        self,
        _method: str,
        url: str,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeStreamCM:
        self.calls += 1
        self.captured_url = url
        self.captured_json = json
        self.captured_headers = headers
        return _FakeStreamCM(self._responses.pop(0))

    async def aclose(self) -> None:
        self.closed = True


def _full_stream_events() -> list[dict[str, Any]]:
    return [
        {"type": "response.created", "response": {"id": "resp_1"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "reasoning"},
        },
        {
            "type": "response.reasoning_text.delta",
            "output_index": 0,
            "delta": "thinking",
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [{"type": "summary_text", "text": "I reasoned"}],
                "encrypted_content": "ENC",
            },
        },
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {"type": "message", "id": "msg_1"},
        },
        {"type": "response.output_text.delta", "output_index": 1, "delta": "Hello "},
        {"type": "response.output_text.delta", "output_index": 1, "delta": "world"},
        {
            "type": "response.output_item.done",
            "output_index": 1,
            "item": {
                "type": "message",
                "id": "msg_1",
                "content": [{"type": "output_text", "text": "Hello world"}],
            },
        },
        {
            "type": "response.output_item.added",
            "output_index": 2,
            "item": {
                "type": "function_call",
                "call_id": "call_1",
                "id": "fc_1",
                "name": "get_weather",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 2,
            "delta": '{"city":"NYC"}',
        },
        {
            "type": "response.function_call_arguments.done",
            "output_index": 2,
            "arguments": '{"city":"NYC"}',
        },
        {
            "type": "response.output_item.done",
            "output_index": 2,
            "item": {
                "type": "function_call",
                "call_id": "call_1",
                "id": "fc_1",
                "name": "get_weather",
                "arguments": '{"city":"NYC"}',
            },
        },
        # Codex terminal event name — normalized to response.completed.
        {
            "type": "response.done",
            "response": {
                "id": "resp_1",
                "status": "completed",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": 150,
                    "input_tokens_details": {"cached_tokens": 10},
                    "output_tokens_details": {"reasoning_tokens": 20},
                },
            },
        },
    ]


async def _collect(gen: AsyncIterator[Any]) -> list[Any]:
    return [ev async for ev in gen]


# === End-to-end stream ===


async def test_stream_end_to_end_text_tool_thinking_usage() -> None:
    raw = _sse(_full_stream_events())
    # Split across tiny chunk boundaries to exercise the SSE buffer.
    chunks = [raw[i : i + 7] for i in range(0, len(raw), 7)]
    client = _FakeClient([_FakeResponse(200, chunks)])
    opts = OpenAIResponsesOptions(
        api_key=_make_token("acc-9"), session_id="s1", client=client
    )
    events = await _collect(
        stream_openai_codex_responses(_model(), Context(system_prompt="SYS"), opts)
    )

    assert isinstance(events[0], AssistantStartEvent)
    assert isinstance(events[-1], AssistantDoneEvent)
    done = events[-1]
    assert isinstance(done, AssistantDoneEvent)
    assert done.reason == "toolUse"

    msg = done.message
    texts = [c for c in msg.content if isinstance(c, TextContent)]
    thinks = [c for c in msg.content if isinstance(c, ThinkingContent)]
    tools = [c for c in msg.content if isinstance(c, ToolCallContent)]
    assert any("Hello world" in c.text for c in texts)
    assert thinks and thinks[0].thinking_signature
    assert tools and tools[0].tool_name == "get_weather"
    assert tools[0].input == {"city": "NYC"}
    assert msg.usage is not None
    assert msg.usage["input_tokens"] == 90  # 100 - 10 cached
    assert msg.usage["output_tokens"] == 50
    assert msg.usage["reasoning"] == 20
    assert msg.response_id == "resp_1"

    # The bespoke transport hit the codex URL with the account-id header.
    assert client.captured_url == "https://chatgpt.com/backend-api/codex/responses"
    assert client.captured_headers is not None
    assert client.captured_headers["chatgpt-account-id"] == "acc-9"
    # A CALLER-provided client is owned by the caller — the adapter must NOT
    # close it (only clients the adapter creates itself are closed).
    assert not client.closed


async def test_stream_owned_client_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # When the adapter creates its OWN client (opts.client is None) it must
    # close it after the stream drains.
    import aelix_ai.providers.openai_codex_responses as cx

    events = [
        {"type": "response.created", "response": {"id": "r"}},
        {"type": "response.done", "response": {"id": "r", "status": "completed"}},
    ]
    created = _FakeClient([_FakeResponse(200, [_sse(events)])])
    monkeypatch.setattr(cx, "_create_codex_client", lambda _opts: created)
    opts = OpenAIResponsesOptions(api_key=_make_token())  # no client → adapter-owned
    await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    assert created.closed


async def test_stream_plain_text_done_reason_stop() -> None:
    events = [
        {"type": "response.created", "response": {"id": "r"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "message", "id": "m"},
        },
        {"type": "response.output_text.delta", "output_index": 0, "delta": "hi"},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "message",
                "id": "m",
                "content": [{"type": "output_text", "text": "hi"}],
            },
        },
        {"type": "response.done", "response": {"id": "r", "status": "completed"}},
    ]
    client = _FakeClient([_FakeResponse(200, [_sse(events)])])
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)
    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    done = out[-1]
    assert isinstance(done, AssistantDoneEvent)
    assert done.reason == "stop"


async def test_stream_incomplete_status_maps_to_length() -> None:
    events = [
        {"type": "response.created", "response": {"id": "r"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "message", "id": "m"},
        },
        {"type": "response.output_text.delta", "output_index": 0, "delta": "hi"},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "message",
                "id": "m",
                "content": [{"type": "output_text", "text": "hi"}],
            },
        },
        {"type": "response.done", "response": {"id": "r", "status": "incomplete"}},
    ]
    client = _FakeClient([_FakeResponse(200, [_sse(events)])])
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)
    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    done = out[-1]
    assert isinstance(done, AssistantDoneEvent)
    assert done.reason == "length"


async def test_stream_fires_on_payload_and_on_response() -> None:
    seen: dict[str, Any] = {}

    async def on_payload(payload: Any, _model: Model) -> Any:
        seen["payload"] = payload
        return None

    async def on_response(resp: Any, _model: Model) -> None:
        seen["status"] = resp.status

    events = [
        {"type": "response.created", "response": {"id": "r"}},
        {"type": "response.done", "response": {"id": "r", "status": "completed"}},
    ]
    client = _FakeClient([_FakeResponse(200, [_sse(events)], headers={"x": "y"})])
    opts = OpenAIResponsesOptions(
        api_key=_make_token(), client=client, on_payload=on_payload, on_response=on_response
    )
    await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    assert seen["payload"]["model"] == "gpt-5.2"
    assert seen["status"] == 200


# === Auth failure modes ===


async def test_stream_missing_token_emits_error_event() -> None:
    out = await _collect(
        stream_openai_codex_responses(
            _model(), Context(), OpenAIResponsesOptions(api_key=None)
        )
    )
    assert len(out) == 1
    assert isinstance(out[0], AssistantErrorEvent)
    assert "OAuth" in (out[0].error_message or "")


async def test_stream_token_without_account_claim_emits_error_event() -> None:
    token = _make_token(with_claim=False)
    out = await _collect(
        stream_openai_codex_responses(
            _model(), Context(), OpenAIResponsesOptions(api_key=token)
        )
    )
    assert len(out) == 1
    assert isinstance(out[0], AssistantErrorEvent)
    assert "chatgpt_account_id" in (out[0].error_message or "")


# === Retry / HTTP-error transport ===


async def test_stream_retries_on_429_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_ai.providers.openai_codex_responses as cx

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(cx.asyncio, "sleep", _no_sleep)

    ok_events = [
        {"type": "response.created", "response": {"id": "r"}},
        {"type": "response.done", "response": {"id": "r", "status": "completed"}},
    ]
    bad = _FakeResponse(429, [b'{"error":"rate"}'], headers={"retry-after": "0"})
    good = _FakeResponse(200, [_sse(ok_events)])
    client = _FakeClient([bad, good])
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)
    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    assert client.calls == 2
    assert isinstance(out[-1], AssistantDoneEvent)


async def test_stream_retries_exhausted_emits_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_ai.providers.openai_codex_responses as cx

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(cx.asyncio, "sleep", _no_sleep)

    # 1 initial + _MAX_RETRIES retries = 4 attempts, all 503.
    responses = [_FakeResponse(503, [b"busy"]) for _ in range(cx._MAX_RETRIES + 1)]
    client = _FakeClient(responses)
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)
    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    assert client.calls == cx._MAX_RETRIES + 1
    assert isinstance(out[-1], AssistantErrorEvent)
    assert "503" in (out[-1].error_message or "")


async def test_stream_non_retryable_400_single_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_ai.providers.openai_codex_responses as cx

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(cx.asyncio, "sleep", _no_sleep)

    client = _FakeClient([_FakeResponse(400, [b'{"error":"bad"}'])])
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)
    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    assert client.calls == 1
    assert isinstance(out[-1], AssistantErrorEvent)
    assert "400" in (out[-1].error_message or "")


async def test_stream_aborted_signal_reports_aborted() -> None:
    class _Signal:
        aborted = True

    events = [
        {"type": "response.created", "response": {"id": "r"}},
        {"type": "response.done", "response": {"id": "r", "status": "completed"}},
    ]
    client = _FakeClient([_FakeResponse(200, [_sse(events)])])
    opts = OpenAIResponsesOptions(
        api_key=_make_token(), client=client, signal=_Signal()
    )
    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    err = out[-1]
    assert isinstance(err, AssistantErrorEvent)
    assert err.reason == "aborted"


# === stream_simple (sync factory) ===


def test_stream_simple_raises_without_token() -> None:
    with pytest.raises(RuntimeError, match="No OAuth token for openai-codex"):
        stream_simple_openai_codex_responses(
            _model(), Context(), SimpleStreamOptions(api_key=None)
        )


def test_stream_simple_returns_async_iterator_with_token() -> None:
    it = stream_simple_openai_codex_responses(
        _model(), Context(), SimpleStreamOptions(api_key=_make_token())
    )
    assert hasattr(it, "__anext__")


# === Registration ===


def test_register_all_registers_under_codex_api() -> None:
    register_all()
    providers = get_registered_providers()
    assert OPENAI_CODEX_RESPONSES_API in providers
    assert providers[OPENAI_CODEX_RESPONSES_API].api == OPENAI_CODEX_RESPONSES_API


def test_runtime_bootstrap_makes_codex_model_runnable() -> None:
    from aelix_coding_agent.cli.runtime_bootstrap import register_providers
    from aelix_coding_agent.core.runnable_models import is_runnable, partition_runnable

    register_providers()
    model = _model()
    assert is_runnable(model)
    runnable, blocked = partition_runnable([model])
    assert runnable == [model]
    assert blocked == []


# === Transport-error retry (review: correctness-transport MEDIUM) ===


def _ok_terminal_response() -> _FakeResponse:
    events = [
        {"type": "response.created", "response": {"id": "r"}},
        {"type": "response.done", "response": {"id": "r", "status": "completed"}},
    ]
    return _FakeResponse(200, [_sse(events)])


async def test_stream_retries_on_transport_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_ai.providers.openai_codex_responses as cx
    import httpx

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(cx.asyncio, "sleep", _no_sleep)
    # First establishment raises a transport error; second succeeds.
    client = _FakeClient([httpx.ConnectError("reset"), _ok_terminal_response()])
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)
    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    assert client.calls == 2
    assert isinstance(out[-1], AssistantDoneEvent)


async def test_stream_transport_error_retries_exhausted_emits_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_ai.providers.openai_codex_responses as cx
    import httpx

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(cx.asyncio, "sleep", _no_sleep)
    errs: list[_FakeResponse | BaseException] = [
        httpx.ConnectTimeout("t") for _ in range(cx._MAX_RETRIES + 1)
    ]
    client = _FakeClient(errs)
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)
    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    assert client.calls == cx._MAX_RETRIES + 1
    assert isinstance(out[-1], AssistantErrorEvent)


# === Retry delay / retry-after (review: test-adequacy LOW) ===


class _HdrResponse:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_retry_delay_honors_retry_after_header() -> None:
    import aelix_ai.providers.openai_codex_responses as cx

    assert cx._retry_delay(_HdrResponse({"retry-after": "2"}), 0) == 2.0
    assert cx._retry_delay(_HdrResponse({"Retry-After": "3"}), 0) == 3.0


def test_retry_delay_caps_retry_after() -> None:
    import aelix_ai.providers.openai_codex_responses as cx

    assert cx._retry_delay(_HdrResponse({"retry-after": "9999"}), 0) == (
        cx._RETRY_MAX_DELAY_S
    )


def test_retry_delay_falls_back_to_exponential_on_garbage() -> None:
    import aelix_ai.providers.openai_codex_responses as cx

    # attempt 0 → base; attempt grows exponentially; capped.
    assert cx._retry_delay(_HdrResponse({"retry-after": "x"}), 0) == (
        cx._RETRY_BASE_DELAY_S
    )
    assert cx._retry_delay(_HdrResponse({}), 1) == cx._RETRY_BASE_DELAY_S * 2
    assert cx._retry_delay(_HdrResponse({}), 10) == cx._RETRY_MAX_DELAY_S


async def test_stream_retry_after_delay_is_actually_awaited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_ai.providers.openai_codex_responses as cx

    slept: list[float] = []

    async def _spy_sleep(d: float) -> None:
        slept.append(d)

    monkeypatch.setattr(cx.asyncio, "sleep", _spy_sleep)
    bad = _FakeResponse(429, [b"slow"], headers={"retry-after": "2"})
    client = _FakeClient([bad, _ok_terminal_response()])
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)
    await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    assert slept == [2.0]  # the retry-after value was honored, not the backoff


# === Robustness: SSE buffer cap + bounded error body (review: LOW) ===


async def test_stream_unbounded_frame_buffer_aborts() -> None:
    import aelix_ai.providers.openai_codex_responses as cx

    # A response that streams > _MAX_SSE_BUFFER_BYTES with NO frame boundary.
    big = b"x" * (cx._MAX_SSE_BUFFER_BYTES + 1024)
    client = _FakeClient([_FakeResponse(200, [big])])
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)
    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    err = out[-1]
    assert isinstance(err, AssistantErrorEvent)
    assert "buffer" in (err.error_message or "")


async def test_stream_large_error_body_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_ai.providers.openai_codex_responses as cx

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(cx.asyncio, "sleep", _no_sleep)
    # 1 MB error body streamed in 64 KB chunks; the adapter must not choke and
    # the surfaced message stays short (truncated).
    chunk = b"E" * (64 * 1024)
    client = _FakeClient([_FakeResponse(400, [chunk] * 16)])
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)
    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    err = out[-1]
    assert isinstance(err, AssistantErrorEvent)
    assert err.error_message is not None
    assert len(err.error_message) < 2000  # never the full 1 MB


def test_parse_sse_block_recursion_error_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_ai.providers.openai_codex_responses as cx

    def _boom(_s: str) -> Any:
        raise RecursionError("too deep")

    monkeypatch.setattr(cx.json, "loads", _boom)
    assert parse_sse_block(b'data: {"type":"x"}') is None


# === Multi-byte UTF-8 across chunk boundary (review: test-adequacy NIT) ===


async def test_stream_multibyte_utf8_split_across_chunks() -> None:
    events = [
        {"type": "response.created", "response": {"id": "r"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "message", "id": "m"},
        },
        {"type": "response.output_text.delta", "output_index": 0, "delta": "안녕😀"},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "message",
                "id": "m",
                "content": [{"type": "output_text", "text": "안녕😀"}],
            },
        },
        {"type": "response.done", "response": {"id": "r", "status": "completed"}},
    ]
    raw = _sse(events)
    # 1-byte chunks: every multi-byte codepoint is split across boundaries.
    chunks = [raw[i : i + 1] for i in range(len(raw))]
    client = _FakeClient([_FakeResponse(200, chunks)])
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)
    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    done = out[-1]
    assert isinstance(done, AssistantDoneEvent)
    texts = [c for c in done.message.content if isinstance(c, TextContent)]
    assert texts and texts[0].text == "안녕😀"


# === Owned-client close on the ERROR path (review: test-adequacy LOW) ===


async def test_stream_owned_client_closed_on_error_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_ai.providers.openai_codex_responses as cx

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(cx.asyncio, "sleep", _no_sleep)
    created = _FakeClient([_FakeResponse(400, [b"bad"])])
    monkeypatch.setattr(cx, "_create_codex_client", lambda _opts: created)
    opts = OpenAIResponsesOptions(api_key=_make_token())  # adapter-owned client
    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    assert isinstance(out[-1], AssistantErrorEvent)
    assert created.closed


# === on_payload replacement body (review: test-adequacy LOW) ===


async def test_stream_on_payload_replacement_body_used() -> None:
    async def on_payload(_payload: Any, _model: Model) -> Any:
        return {"model": "REPLACED", "input": [], "stream": True}

    client = _FakeClient([_ok_terminal_response()])
    opts = OpenAIResponsesOptions(
        api_key=_make_token(), client=client, on_payload=on_payload
    )
    await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    assert client.captured_json == {"model": "REPLACED", "input": [], "stream": True}


async def test_stream_on_payload_sync_callback_supported() -> None:
    def on_payload(_payload: Any, _model: Model) -> Any:
        return {"model": "SYNC-REPLACED"}

    client = _FakeClient([_ok_terminal_response()])
    opts = OpenAIResponsesOptions(
        api_key=_make_token(), client=client, on_payload=on_payload
    )
    await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    assert client.captured_json == {"model": "SYNC-REPLACED"}


# === Reasoning-off branches of build_request_body (review: test-adequacy LOW) ===


def test_build_request_body_reasoning_off_explicit_null_omits_reasoning() -> None:
    model = _model(thinking_level_map={"off": None})
    body = build_request_body(model, Context(), None)
    assert "reasoning" not in body


def test_build_request_body_reasoning_off_concrete_value() -> None:
    model = _model(thinking_level_map={"off": "low"})
    body = build_request_body(model, Context(), None)
    assert body["reasoning"] == {"effort": "low"}


def test_build_request_body_reasoning_summary_only() -> None:
    body = build_request_body(
        _model(), Context(), OpenAIResponsesOptions(reasoning_summary="detailed")
    )
    assert body["reasoning"] == {"effort": "medium", "summary": "detailed"}


def test_build_request_body_no_tools_key_when_empty() -> None:
    body = build_request_body(_model(), Context(), None)
    assert "tools" not in body


# === stream_simple reasoning clamp (review: test-adequacy MEDIUM) ===


async def test_stream_simple_clamps_off_to_off_branch() -> None:
    # reasoning="off" must clamp to None → the off-branch effort, NOT effort:"off".
    client = _FakeClient([_ok_terminal_response()])
    opts = SimpleStreamOptions(api_key=_make_token(), reasoning="off", client=client)
    gen = stream_simple_openai_codex_responses(_model(), Context(), opts)
    await _collect(gen)
    assert client.captured_json["reasoning"] == {"effort": "none"}


async def test_stream_simple_forwards_effort_level() -> None:
    client = _FakeClient([_ok_terminal_response()])
    opts = SimpleStreamOptions(api_key=_make_token(), reasoning="high", client=client)
    gen = stream_simple_openai_codex_responses(_model(), Context(), opts)
    await _collect(gen)
    assert client.captured_json["reasoning"] == {"effort": "high", "summary": "auto"}


# === Error terminal events end-to-end (review: test-adequacy LOW) ===


async def test_stream_response_failed_status_emits_error() -> None:
    events = [
        {"type": "response.created", "response": {"id": "r"}},
        {"type": "response.done", "response": {"id": "r", "status": "failed"}},
    ]
    client = _FakeClient([_FakeResponse(200, [_sse(events)])])
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)
    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    assert isinstance(out[-1], AssistantErrorEvent)


async def test_stream_mid_stream_error_event_emits_error() -> None:
    events = [
        {"type": "response.created", "response": {"id": "r"}},
        {"type": "error", "code": "server_error", "message": "boom"},
    ]
    client = _FakeClient([_FakeResponse(200, [_sse(events)])])
    opts = OpenAIResponsesOptions(api_key=_make_token(), client=client)
    out = await _collect(stream_openai_codex_responses(_model(), Context(), opts))
    err = out[-1]
    assert isinstance(err, AssistantErrorEvent)
    assert "boom" in (err.error_message or "")
