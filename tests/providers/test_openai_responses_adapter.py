"""OpenAI **Responses** thin-adapter tests — #15 (Workflow A, dormant).

Covers :func:`build_params` (store=False always, cache-key/retention gating,
github-copilot reasoning-off exclusion, reasoning block + include), the sync
``stream_simple`` key-raise, and an end-to-end drive of the shared engine on a
fake Responses client (response_id capture + event ordering). Also introspects
the installed ``openai`` SDK's ``responses.create`` signature to confirm the
kwargs the adapter sends are accepted (with ``prompt_cache_retention`` riding
in ``extra_body``).
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from aelix_ai.api_registry import get_registered_providers
from aelix_ai.messages import AssistantMessage, TextContent, ThinkingContent
from aelix_ai.providers.openai_responses import (
    OPENAI_RESPONSES_API,
    OpenAIResponsesOptions,
    _relocate_extra_body_params,
    build_params,
    stream_openai_responses,
    stream_simple_openai_responses,
)
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantStartEvent,
    Context,
    Model,
    ProviderResponse,
    SimpleStreamOptions,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingEndEvent,
)

# === Model factories ===


def _model(**kw: Any) -> Model:
    base: dict[str, Any] = {
        "api": OPENAI_RESPONSES_API,
        "id": "gpt-5",
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
    }
    base.update(kw)
    return Model(**base)


# === build_params ===


def test_build_params_store_is_always_false() -> None:
    params = build_params(_model(), Context(), None)
    assert params["store"] is False


def test_build_params_store_false_even_with_reasoning_and_tools() -> None:
    model = _model(reasoning=True)
    opts = OpenAIResponsesOptions(reasoning_effort="high", session_id="s")
    params = build_params(model, Context(), opts)
    assert params["store"] is False


def test_build_params_cache_key_set_when_retention_not_none() -> None:
    opts = OpenAIResponsesOptions(session_id="sess-123", cache_retention="short")
    params = build_params(_model(), Context(), opts)
    assert params["prompt_cache_key"] == "sess-123"


def test_build_params_cache_key_omitted_when_retention_none() -> None:
    opts = OpenAIResponsesOptions(session_id="sess-123", cache_retention="none")
    params = build_params(_model(), Context(), opts)
    assert "prompt_cache_key" not in params


def test_build_params_cache_key_clamped_to_64_codepoints() -> None:
    long_id = "x" * 100
    opts = OpenAIResponsesOptions(session_id=long_id, cache_retention="short")
    params = build_params(_model(), Context(), opts)
    assert params["prompt_cache_key"] == "x" * 64


def test_build_params_long_retention_sets_24h_when_compat_allows() -> None:
    opts = OpenAIResponsesOptions(session_id="s", cache_retention="long")
    params = build_params(_model(), Context(), opts)
    assert params["prompt_cache_retention"] == "24h"


def test_build_params_long_retention_omitted_when_compat_disallows() -> None:
    model = _model(compat={"supportsLongCacheRetention": False})
    opts = OpenAIResponsesOptions(session_id="s", cache_retention="long")
    params = build_params(model, Context(), opts)
    assert "prompt_cache_retention" not in params


def test_build_params_max_output_tokens_only_when_truthy() -> None:
    assert "max_output_tokens" not in build_params(_model(), Context(), None)
    opts = OpenAIResponsesOptions(max_tokens=512)
    assert build_params(_model(), Context(), opts)["max_output_tokens"] == 512
    opts0 = OpenAIResponsesOptions(max_tokens=0)
    assert "max_output_tokens" not in build_params(_model(), Context(), opts0)


def test_build_params_temperature_and_service_tier_forwarded() -> None:
    opts = OpenAIResponsesOptions(temperature=0.3, service_tier="flex")
    params = build_params(_model(), Context(), opts)
    assert params["temperature"] == 0.3
    assert params["service_tier"] == "flex"


# === reasoning block ===


def test_build_params_reasoning_effort_sets_block_and_include() -> None:
    model = _model(reasoning=True)
    opts = OpenAIResponsesOptions(reasoning_effort="high")
    params = build_params(model, Context(), opts)
    assert params["reasoning"] == {"effort": "high", "summary": "auto"}
    assert params["include"] == ["reasoning.encrypted_content"]


def test_build_params_reasoning_summary_defaults_auto_at_use_site() -> None:
    model = _model(reasoning=True)
    # summary alone (no effort) → effort defaults "medium", include present.
    opts = OpenAIResponsesOptions(reasoning_summary="detailed")
    params = build_params(model, Context(), opts)
    assert params["reasoning"] == {"effort": "medium", "summary": "detailed"}
    assert params["include"] == ["reasoning.encrypted_content"]


def test_build_params_reasoning_effort_uses_thinking_level_map() -> None:
    model = _model(reasoning=True, thinking_level_map={"high": "xtra-high"})
    opts = OpenAIResponsesOptions(reasoning_effort="high")
    params = build_params(model, Context(), opts)
    assert params["reasoning"]["effort"] == "xtra-high"


def test_build_params_reasoning_off_branch_for_non_copilot() -> None:
    # reasoning model, no effort requested, provider != copilot → off block.
    model = _model(reasoning=True)
    params = build_params(model, Context(), None)
    assert params["reasoning"] == {"effort": "none"}
    assert "include" not in params


def test_build_params_reasoning_off_uses_explicit_off_mapping() -> None:
    model = _model(reasoning=True, thinking_level_map={"off": "minimal"})
    params = build_params(model, Context(), None)
    assert params["reasoning"] == {"effort": "minimal"}


def test_build_params_reasoning_off_skipped_when_off_explicitly_null() -> None:
    model = _model(reasoning=True, thinking_level_map={"off": None})
    params = build_params(model, Context(), None)
    assert "reasoning" not in params


def test_build_params_github_copilot_excluded_from_reasoning_off_branch() -> None:
    # Copilot is the default Copilot model — high impact. A reasoning copilot
    # model with NO effort requested must NOT get a reasoning-off block.
    model = _model(reasoning=True, provider="github-copilot")
    params = build_params(model, Context(), None)
    assert "reasoning" not in params


def test_build_params_github_copilot_still_gets_reasoning_when_effort_set() -> None:
    # The exclusion is ONLY for the off-branch; an explicit effort still
    # produces the encrypted-reasoning block.
    model = _model(reasoning=True, provider="github-copilot")
    opts = OpenAIResponsesOptions(reasoning_effort="medium")
    params = build_params(model, Context(), opts)
    assert params["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert params["include"] == ["reasoning.encrypted_content"]


def test_build_params_no_reasoning_block_for_non_reasoning_model() -> None:
    params = build_params(_model(reasoning=False), Context(), None)
    assert "reasoning" not in params


# === extra_body relocation ===


def test_relocate_moves_prompt_cache_retention_to_extra_body() -> None:
    params = {"model": "gpt-5", "prompt_cache_retention": "24h", "store": False}
    out = _relocate_extra_body_params(params)
    assert "prompt_cache_retention" not in out
    assert out["extra_body"] == {"prompt_cache_retention": "24h"}


def test_relocate_no_op_when_no_extension_keys() -> None:
    params = {"model": "gpt-5", "store": False}
    out = _relocate_extra_body_params(params)
    assert "extra_body" not in out


# === stream_simple sync key-raise ===


def test_stream_simple_raises_synchronously_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No API key for provider: openai"):
        stream_simple_openai_responses(_model(), Context(), SimpleStreamOptions())


def test_stream_simple_accepts_auth_header_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # cf-aig-authorization header → "unused" path, no raise.
    opts = SimpleStreamOptions(headers={"cf-aig-authorization": "Bearer tok"})
    it = stream_simple_openai_responses(_model(provider="cloudflare-ai-gateway"), Context(), opts)
    assert hasattr(it, "__anext__")


def test_stream_simple_resolves_env_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    # Should not raise — env key resolves.
    it = stream_simple_openai_responses(_model(), Context(), SimpleStreamOptions())
    assert hasattr(it, "__anext__")


# === Fake Responses SDK client (events are plain dicts; engine uses _get) ===


@dataclass
class _RawResponse:
    status_code: int = 200
    headers: dict = field(default_factory=lambda: {"x-test": "ok"})


class _AsyncIter:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    def __aiter__(self) -> _AsyncIter:
        self._idx = 0
        return self

    async def __anext__(self) -> dict[str, Any]:
        if self._idx >= len(self._events):
            raise StopAsyncIteration
        ev = self._events[self._idx]
        self._idx += 1
        return ev


class _RawWrapper:
    def __init__(self, iterator: _AsyncIter, response: _RawResponse) -> None:
        self._iterator = iterator
        self.http_response = response

    def parse(self) -> _AsyncIter:
        return self._iterator


class _WithRawResponse:
    def __init__(self, iterator: _AsyncIter, captured: dict[str, Any]) -> None:
        self._iterator = iterator
        self._captured = captured

    def create(self, **kwargs: Any) -> _RawWrapper:
        self._captured["params"] = kwargs
        return _RawWrapper(self._iterator, _RawResponse())


class _Responses:
    def __init__(self, iterator: _AsyncIter, captured: dict[str, Any]) -> None:
        self.with_raw_response = _WithRawResponse(iterator, captured)


class _FakeAsyncOpenAI:
    def __init__(self, iterator: _AsyncIter) -> None:
        self.captured: dict[str, Any] = {}
        self.responses = _Responses(iterator, self.captured)


async def _collect(it: AsyncIterator[Any]) -> list[Any]:
    return [ev async for ev in it]


def _text_stream_events() -> list[dict[str, Any]]:
    return [
        {"type": "response.created", "response": {"id": "resp_abc"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "message"},
        },
        {"type": "response.output_text.delta", "output_index": 0, "delta": "Hel"},
        {"type": "response.output_text.delta", "output_index": 0, "delta": "lo"},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "message",
                "id": "msg_1",
                "content": [{"type": "output_text", "text": "Hello"}],
            },
        },
        {
            "type": "response.completed",
            "response": {
                "id": "resp_abc",
                "status": "completed",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                    "input_tokens_details": {"cached_tokens": 2},
                    "output_tokens_details": {"reasoning_tokens": 3},
                },
            },
        },
    ]


async def test_adapter_drives_engine_end_to_end_text() -> None:
    client = _FakeAsyncOpenAI(_AsyncIter(_text_stream_events()))
    out = await _collect(
        stream_openai_responses(
            _model(), Context(), OpenAIResponsesOptions(api_key="k", client=client)
        )
    )
    types = [ev.type for ev in out]
    assert types[0] == "start"
    assert "text_start" in types
    deltas = [ev.delta for ev in out if isinstance(ev, TextDeltaEvent)]
    assert deltas == ["Hel", "lo"]
    assert any(isinstance(ev, TextEndEvent) for ev in out)
    done = out[-1]
    assert isinstance(done, AssistantDoneEvent)
    assert done.reason == "stop"


async def test_adapter_captures_response_id_and_usage() -> None:
    client = _FakeAsyncOpenAI(_AsyncIter(_text_stream_events()))
    out = await _collect(
        stream_openai_responses(
            _model(), Context(), OpenAIResponsesOptions(api_key="k", client=client)
        )
    )
    done = out[-1]
    assert isinstance(done, AssistantDoneEvent)
    assert done.message.response_id == "resp_abc"
    # usage token-dict: reasoning subset captured; cached subtracted from input.
    assert done.message.usage["input"] == 8  # 10 - 2 cached
    assert done.message.usage["output"] == 5
    assert done.message.usage["reasoning"] == 3
    assert done.message.usage["cache_read"] == 2
    # The start partial also carries the response_id once response.created fired.
    assert isinstance(out[0], AssistantStartEvent)


async def test_adapter_fires_on_response_with_status_and_headers() -> None:
    client = _FakeAsyncOpenAI(_AsyncIter(_text_stream_events()))
    seen: list[ProviderResponse] = []

    async def _on_response(resp: ProviderResponse, _model: Model) -> None:
        seen.append(resp)

    await _collect(
        stream_openai_responses(
            _model(),
            Context(),
            OpenAIResponsesOptions(
                api_key="k", client=client, on_response=_on_response
            ),
        )
    )
    assert len(seen) == 1
    assert seen[0].status == 200
    assert seen[0].headers["x-test"] == "ok"


async def test_adapter_on_payload_sees_flat_params_before_relocation() -> None:
    client = _FakeAsyncOpenAI(_AsyncIter(_text_stream_events()))
    captured: dict[str, Any] = {}

    async def _on_payload(params: dict[str, Any], _model: Model) -> None:
        captured.update(params)
        return None

    await _collect(
        stream_openai_responses(
            _model(),
            Context(),
            OpenAIResponsesOptions(
                api_key="k",
                client=client,
                cache_retention="long",
                session_id="s",
                on_payload=_on_payload,
            ),
        )
    )
    # on_payload sees the Pi-shaped flat param (pre-relocation)...
    assert captured["prompt_cache_retention"] == "24h"
    # ...but the SDK only ever receives it inside extra_body.
    sdk_params = client.captured["params"]
    assert "prompt_cache_retention" not in sdk_params
    assert sdk_params["extra_body"]["prompt_cache_retention"] == "24h"
    assert sdk_params["store"] is False


async def test_adapter_reasoning_roundtrip_stores_full_item_signature() -> None:
    events = [
        {"type": "response.created", "response": {"id": "resp_r"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "reasoning"},
        },
        {
            "type": "response.reasoning_text.delta",
            "output_index": 0,
            "delta": "think",
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "reasoning",
                "id": "rs_1",
                "encrypted_content": "ENC",
                "summary": [{"type": "summary_text", "text": "think"}],
            },
        },
        {
            "type": "response.completed",
            "response": {"id": "resp_r", "status": "completed"},
        },
    ]
    client = _FakeAsyncOpenAI(_AsyncIter(events))
    out = await _collect(
        stream_openai_responses(
            _model(reasoning=True),
            Context(),
            OpenAIResponsesOptions(api_key="k", client=client),
        )
    )
    end = next(ev for ev in out if isinstance(ev, ThinkingEndEvent))
    assert end.content == "think"
    done = out[-1]
    assert isinstance(done, AssistantDoneEvent)
    thinking = next(
        b for b in done.message.content if isinstance(b, ThinkingContent)
    )
    # The full ResponseReasoningItem (incl. encrypted_content) is serialized
    # onto thinking_signature for cross-turn replay.
    assert "ENC" in thinking.thinking_signature
    assert "rs_1" in thinking.thinking_signature


async def test_adapter_tool_call_sets_tooluse_done_reason() -> None:
    events = [
        {"type": "response.created", "response": {"id": "resp_t"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "function_call", "call_id": "call_1", "id": "fc_1", "name": "ls"},
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": '{"path": "."}',
        },
        {
            "type": "response.function_call_arguments.done",
            "output_index": 0,
            "arguments": '{"path": "."}',
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "call_id": "call_1",
                "id": "fc_1",
                "name": "ls",
                "arguments": '{"path": "."}',
            },
        },
        {
            "type": "response.completed",
            "response": {"id": "resp_t", "status": "completed"},
        },
    ]
    client = _FakeAsyncOpenAI(_AsyncIter(events))
    out = await _collect(
        stream_openai_responses(
            _model(), Context(), OpenAIResponsesOptions(api_key="k", client=client)
        )
    )
    done = out[-1]
    assert isinstance(done, AssistantDoneEvent)
    # A tool call with completed status maps to toolUse (engine override).
    assert done.reason == "toolUse"


async def test_adapter_raises_error_event_on_stream_failure() -> None:
    events = [
        {"type": "response.created", "response": {"id": "resp_f"}},
        {"type": "error", "code": "boom", "message": "kaboom"},
    ]
    client = _FakeAsyncOpenAI(_AsyncIter(events))
    out = await _collect(
        stream_openai_responses(
            _model(), Context(), OpenAIResponsesOptions(api_key="k", client=client)
        )
    )
    # error event, no done event.
    assert out[-1].type == "error"
    assert not any(isinstance(ev, AssistantDoneEvent) for ev in out)
    assert "kaboom" in (out[-1].error_message or "")


async def test_adapter_passes_input_not_messages_to_sdk() -> None:
    ctx = Context(
        system_prompt="be terse",
        messages=[AssistantMessage(content=[TextContent(text="hi")])],
    )
    client = _FakeAsyncOpenAI(_AsyncIter(_text_stream_events()))
    await _collect(
        stream_openai_responses(
            _model(), ctx, OpenAIResponsesOptions(api_key="k", client=client)
        )
    )
    sdk_params = client.captured["params"]
    # Responses API uses ``input`` (NOT ``messages``).
    assert "input" in sdk_params
    assert "messages" not in sdk_params
    assert sdk_params["stream"] is True


# === SDK signature introspection (kwargs the adapter sends) ===


def test_installed_sdk_responses_create_accepts_adapter_kwargs() -> None:
    from openai.resources.responses import AsyncResponses

    sig = inspect.signature(AsyncResponses.create)
    params = set(sig.parameters)
    # Natively accepted top-level kwargs.
    for kw in (
        "store",
        "reasoning",
        "include",
        "prompt_cache_key",
        "max_output_tokens",
        "service_tier",
        "temperature",
        "tools",
        "input",
        "stream",
        "extra_body",
    ):
        assert kw in params, f"SDK responses.create missing kwarg: {kw}"
    # prompt_cache_retention is NOT native — must ride extra_body, which is
    # exactly why _EXTRA_BODY_PARAM_KEYS relocates it.
    assert "prompt_cache_retention" not in params


# === dormancy guard ===


def test_importing_adapter_does_not_register_it() -> None:
    assert OPENAI_RESPONSES_API not in get_registered_providers()
