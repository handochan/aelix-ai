"""Gemini Developer API thin-adapter tests — #15 (Workflow A, dormant).

Covers :func:`build_params` (thinking family level/budget/disabled,
toolConfig gating, systemInstruction, temperature/maxOutputTokens), the sync
``stream_simple`` key-raise + family selection, an end-to-end drive of the
shared engine on a fake ``genai`` client (text deltas, response_id, usage,
tool-call thoughtSignature replay, toolUse done-reason, error event), and the
dormancy guard.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_ai.api_registry import get_registered_providers
from aelix_ai.messages import ThinkingContent, ToolCallContent
from aelix_ai.providers._google_shared import GoogleThinking
from aelix_ai.providers.google_generative_ai import (
    GOOGLE_GENERATIVE_AI_API,
    GoogleOptions,
    build_params,
    stream_google,
    stream_simple_google,
)
from aelix_ai.streaming import (
    AssistantDoneEvent,
    Context,
    Model,
    SimpleStreamOptions,
    TextDeltaEvent,
    ToolCallEndEvent,
)
from aelix_ai.tools import Tool


def _model(**kw: Any) -> Model:
    base: dict[str, Any] = {
        "id": "gemini-2.5-flash",
        "name": "gemini-2.5-flash",
        "api": GOOGLE_GENERATIVE_AI_API,
        "provider": "google",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "input": ["text", "image"],
        "reasoning": True,
    }
    base.update(kw)
    return Model(**base)


def _tool() -> Tool:
    return Tool(
        name="ls",
        description="list",
        parameters={"type": "object", "properties": {}},
    )


# === build_params ===


def test_build_params_returns_model_contents_config() -> None:
    params = build_params(_model(), Context(), None)
    assert params["model"] == "gemini-2.5-flash"
    assert params["contents"] == []
    assert params["config"] == {}


def test_build_params_temperature_and_max_tokens_flat() -> None:
    opts = GoogleOptions(temperature=0.4, max_tokens=256)
    config = build_params(_model(), Context(), opts)["config"]
    assert config["temperature"] == 0.4
    assert config["maxOutputTokens"] == 256


def test_build_params_system_instruction_from_context() -> None:
    ctx = Context(system_prompt="be terse")
    config = build_params(_model(), ctx, None)["config"]
    assert config["systemInstruction"] == "be terse"


def test_build_params_tool_config_only_with_tools_and_choice() -> None:
    ctx = Context(tools=[_tool()])
    # tools but no tool_choice → no toolConfig.
    config = build_params(_model(), ctx, GoogleOptions())["config"]
    assert "tools" in config
    assert "toolConfig" not in config
    # tools + tool_choice → toolConfig with mapped mode.
    config2 = build_params(_model(), ctx, GoogleOptions(tool_choice="any"))["config"]
    assert config2["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}


def test_build_params_tool_choice_without_tools_omits_tool_config() -> None:
    config = build_params(_model(), Context(), GoogleOptions(tool_choice="any"))[
        "config"
    ]
    assert "toolConfig" not in config


def test_build_params_thinking_level() -> None:
    opts = GoogleOptions(thinking=GoogleThinking(enabled=True, level="HIGH"))
    config = build_params(_model(), Context(), opts)["config"]
    assert config["thinkingConfig"] == {"includeThoughts": True, "thinkingLevel": "HIGH"}


def test_build_params_thinking_budget() -> None:
    opts = GoogleOptions(thinking=GoogleThinking(enabled=True, budget_tokens=8192))
    config = build_params(_model(), Context(), opts)["config"]
    assert config["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingBudget": 8192,
    }


def test_build_params_thinking_disabled_uses_disabled_config() -> None:
    # gemini-2.5-flash disables via thinkingBudget=0.
    opts = GoogleOptions(thinking=GoogleThinking(enabled=False))
    config = build_params(_model(), Context(), opts)["config"]
    assert config["thinkingConfig"] == {"thinkingBudget": 0}


def test_build_params_thinking_disabled_gemini3_pro_uses_low_level() -> None:
    opts = GoogleOptions(thinking=GoogleThinking(enabled=False))
    config = build_params(_model(id="gemini-3-pro-preview"), Context(), opts)["config"]
    assert config["thinkingConfig"] == {"thinkingLevel": "LOW"}


def test_build_params_no_thinking_config_for_non_reasoning_model() -> None:
    opts = GoogleOptions(thinking=GoogleThinking(enabled=True, level="HIGH"))
    config = build_params(_model(reasoning=False), Context(), opts)["config"]
    assert "thinkingConfig" not in config


# === stream_simple sync key-raise + family selection ===


def test_stream_simple_raises_synchronously_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No API key for provider: google"):
        stream_simple_google(_model(), Context(), SimpleStreamOptions())


def test_stream_simple_resolves_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    it = stream_simple_google(_model(), Context(), SimpleStreamOptions())
    assert hasattr(it, "__anext__")


async def test_stream_simple_gemini25_uses_budget() -> None:
    # gemini-2.5-flash (not Gemini 3 / Gemma 4) → thinkingBudget path. "high"
    # → 24576 from the 2.5-flash table.
    captured: dict[str, Any] = {}

    async def _payload(params: dict[str, Any], _m: Model) -> None:
        captured.update(params)

    client = _FakeGenAIClient(_text_chunks())
    g_opts = GoogleOptions(
        api_key="k", client=client, reasoning="high", on_payload=_payload
    )
    await _collect(stream_simple_google(_model(), Context(), g_opts))
    assert captured["config"]["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingBudget": 24576,
    }


async def test_stream_simple_gemini3_uses_level() -> None:
    # gemini-3-pro-preview → thinkingLevel path. "high" → "HIGH".
    captured: dict[str, Any] = {}

    async def _payload(params: dict[str, Any], _m: Model) -> None:
        captured.update(params)

    client = _FakeGenAIClient(_text_chunks())
    g_opts = GoogleOptions(
        api_key="k", client=client, reasoning="high", on_payload=_payload
    )
    await _collect(
        stream_simple_google(_model(id="gemini-3-pro-preview"), Context(), g_opts)
    )
    assert captured["config"]["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingLevel": "HIGH",
    }


# === Fake genai streaming client ===


class _FakeChunkIter:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> _FakeChunkIter:
        self._i = 0
        return self

    async def __anext__(self) -> dict[str, Any]:
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._i]
        self._i += 1
        if isinstance(c, Exception):
            raise c
        return c


class _FakeModels:
    def __init__(self, chunks: list[Any], captured: dict[str, Any]) -> None:
        self._chunks = chunks
        self._captured = captured

    async def generate_content_stream(self, **kwargs: Any) -> _FakeChunkIter:
        self._captured.update(kwargs)
        return _FakeChunkIter(self._chunks)


class _FakeAio:
    def __init__(self, chunks: list[Any], captured: dict[str, Any]) -> None:
        self.models = _FakeModels(chunks, captured)


class _FakeGenAIClient:
    def __init__(self, chunks: list[Any]) -> None:
        self.captured: dict[str, Any] = {}
        self.aio = _FakeAio(chunks, self.captured)


async def _collect(it: AsyncIterator[Any]) -> list[Any]:
    return [ev async for ev in it]


def _text_chunks() -> list[dict[str, Any]]:
    return [
        {
            "responseId": "resp_g1",
            "candidates": [
                {"content": {"parts": [{"text": "Hel"}]}},
            ],
        },
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": "lo"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "thoughtsTokenCount": 2,
                "cachedContentTokenCount": 3,
                "totalTokenCount": 17,
            },
        },
    ]


async def test_adapter_drives_engine_end_to_end_text() -> None:
    client = _FakeGenAIClient(_text_chunks())
    out = await _collect(
        stream_google(
            _model(), Context(), GoogleOptions(api_key="k", client=client)
        )
    )
    types = [ev.type for ev in out]
    assert types[0] == "start"
    assert "text_start" in types
    deltas = [ev.delta for ev in out if isinstance(ev, TextDeltaEvent)]
    assert deltas == ["Hel", "lo"]
    done = out[-1]
    assert isinstance(done, AssistantDoneEvent)
    assert done.reason == "stop"


async def test_adapter_captures_response_id_and_usage() -> None:
    client = _FakeGenAIClient(_text_chunks())
    out = await _collect(
        stream_google(
            _model(), Context(), GoogleOptions(api_key="k", client=client)
        )
    )
    done = out[-1]
    assert isinstance(done, AssistantDoneEvent)
    assert done.message.response_id == "resp_g1"
    # input = prompt - cached = 10 - 3 = 7; output = candidates + thoughts = 7.
    assert done.message.usage["input"] == 7
    assert done.message.usage["output"] == 7
    assert done.message.usage["reasoning"] == 2
    assert done.message.usage["cache_read"] == 3
    assert done.message.usage["total_tokens"] == 17


async def test_adapter_thought_signature_replay_on_tool_call() -> None:
    chunks = [
        {
            "responseId": "resp_t",
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {"name": "ls", "args": {"path": "."}},
                                "thoughtSignature": "YWJjZA==",
                            }
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
        },
    ]
    client = _FakeGenAIClient(chunks)
    out = await _collect(
        stream_google(
            _model(), Context(), GoogleOptions(api_key="k", client=client)
        )
    )
    tc_end = next(ev for ev in out if isinstance(ev, ToolCallEndEvent))
    assert tc_end.tool_call.thought_signature == "YWJjZA=="
    done = out[-1]
    assert isinstance(done, AssistantDoneEvent)
    # finishReason STOP but a tool call present → toolUse override.
    assert done.reason == "toolUse"
    tc = next(b for b in done.message.content if isinstance(b, ToolCallContent))
    assert tc.tool_name == "ls"
    assert tc.input == {"path": "."}


async def test_adapter_thinking_block_retains_signature() -> None:
    chunks = [
        {
            "responseId": "r",
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": "pondering",
                                "thought": True,
                                "thoughtSignature": "c2ln",
                            }
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
        }
    ]
    client = _FakeGenAIClient(chunks)
    out = await _collect(
        stream_google(
            _model(), Context(), GoogleOptions(api_key="k", client=client)
        )
    )
    done = out[-1]
    assert isinstance(done, AssistantDoneEvent)
    thinking = next(
        b for b in done.message.content if isinstance(b, ThinkingContent)
    )
    assert thinking.thinking == "pondering"
    assert thinking.thinking_signature == "c2ln"


async def test_adapter_passes_contents_and_config_to_sdk() -> None:
    client = _FakeGenAIClient(_text_chunks())
    ctx = Context(system_prompt="be terse")
    await _collect(
        stream_google(
            _model(), ctx, GoogleOptions(api_key="k", client=client, temperature=0.2)
        )
    )
    assert client.captured["model"] == "gemini-2.5-flash"
    assert client.captured["config"]["systemInstruction"] == "be terse"
    assert client.captured["config"]["temperature"] == 0.2


async def test_adapter_error_event_on_stream_failure() -> None:
    client = _FakeGenAIClient([RuntimeError("kaboom")])
    out = await _collect(
        stream_google(
            _model(), Context(), GoogleOptions(api_key="k", client=client)
        )
    )
    assert out[-1].type == "error"
    assert not any(isinstance(ev, AssistantDoneEvent) for ev in out)
    assert "kaboom" in (out[-1].error_message or "")


async def test_adapter_max_tokens_length_done_reason() -> None:
    chunks = [
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": "trunc"}]},
                    "finishReason": "MAX_TOKENS",
                }
            ]
        }
    ]
    client = _FakeGenAIClient(chunks)
    out = await _collect(
        stream_google(
            _model(), Context(), GoogleOptions(api_key="k", client=client)
        )
    )
    done = out[-1]
    assert isinstance(done, AssistantDoneEvent)
    assert done.reason == "length"


# === dormancy guard ===


def test_importing_adapter_does_not_register_it() -> None:
    assert GOOGLE_GENERATIVE_AI_API not in get_registered_providers()
